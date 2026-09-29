from datetime import datetime

from sqlalchemy.exc import IntegrityError

from models import Match, Question, User, db, pair

_PEOPLE_SQL = """
    SELECT theirs.user_id AS user_id,
           u.email        AS email,
           SUM(q.weight)  AS score,
           array_agg(o.label ORDER BY q.id, o.sort_order) AS shared
    FROM user_answers mine
    JOIN user_answers theirs
      ON theirs.option_id = mine.option_id
     AND theirs.user_id <> mine.user_id
    JOIN "user" u           ON u.id = theirs.user_id
    JOIN questions q        ON q.id = mine.question_id
    JOIN question_options o ON o.id = mine.option_id
    WHERE mine.user_id = :user_id
    GROUP BY theirs.user_id, u.email
    ORDER BY score DESC, theirs.user_id
    LIMIT :limit
"""

_CLUBS_SQL = """
    SELECT c.id            AS club_id,
           c.name          AS name,
           c.description   AS description,
           SUM(q.weight)   AS score,
           array_agg(o.label ORDER BY q.id, o.sort_order) AS shared
    FROM user_answers mine
    JOIN club_tags t        ON t.option_id = mine.option_id
    JOIN clubs c            ON c.id = t.club_id
    JOIN questions q        ON q.id = mine.question_id
    JOIN question_options o ON o.id = mine.option_id
    WHERE mine.user_id = :user_id
      AND NOT EXISTS (
          SELECT 1 FROM club_members m
          WHERE m.club_id = c.id AND m.user_id = :user_id
      )
    GROUP BY c.id, c.name, c.description
    ORDER BY score DESC, c.name
    LIMIT :limit
"""

_ANSWERS_SQL = """
    SELECT q.key AS key, o.label AS label
    FROM user_answers a
    JOIN questions q        ON q.id = a.question_id
    JOIN question_options o ON o.id = a.option_id
    WHERE a.user_id = :user_id
    ORDER BY q.id, o.sort_order
"""


def find_matches(user_id, limit=10):
    rows = db.session.execute(
        db.text(_PEOPLE_SQL), {"user_id": user_id, "limit": limit}
    ).mappings()
    return [
        {
            "user_id": r["user_id"],
            "email": r["email"],
            "score": float(r["score"]),
            "shared": list(r["shared"]),
        }
        for r in rows
    ]


def recommend_clubs(user_id, limit=10):
    rows = db.session.execute(
        db.text(_CLUBS_SQL), {"user_id": user_id, "limit": limit}
    ).mappings()
    return [
        {
            "club_id": r["club_id"],
            "name": r["name"],
            "description": r["description"],
            "score": float(r["score"]),
            "shared": list(r["shared"]),
        }
        for r in rows
    ]


_CANDIDATES_SQL = """
    SELECT theirs.user_id AS user_id,
           SUM(q.weight)  AS score
    FROM user_answers mine
    JOIN user_answers theirs
      ON theirs.option_id = mine.option_id
     AND theirs.user_id <> mine.user_id
    JOIN questions q ON q.id = mine.question_id
    WHERE mine.user_id = :user_id
      AND NOT EXISTS (
          SELECT 1 FROM matches m
          WHERE (m.user_lo = LEAST(:user_id, theirs.user_id)
             AND m.user_hi = GREATEST(:user_id, theirs.user_id))
      )
    GROUP BY theirs.user_id
    ORDER BY score DESC, theirs.user_id
    LIMIT :limit
"""

_ACTIVE_SQL = """
    SELECT CASE WHEN m.user_lo = :user_id THEN m.user_hi ELSE m.user_lo END AS user_id,
           m.score AS score,
           m.created_at AS created_at
    FROM matches m
    WHERE m.status = 'active'
      AND (m.user_lo = :user_id OR m.user_hi = :user_id)
    ORDER BY m.score DESC, m.id
"""

_SHARED_SQL = """
    SELECT array_agg(o.label ORDER BY q.id, o.sort_order) AS shared
    FROM user_answers mine
    JOIN user_answers theirs
      ON theirs.option_id = mine.option_id
     AND theirs.user_id = :other_id
    JOIN questions q        ON q.id = mine.question_id
    JOIN question_options o ON o.id = mine.option_id
    WHERE mine.user_id = :user_id
"""


def ensure_matches(user_id, target=3):
    """Top the student up to `target` active matches. Safe to call on every view."""
    active = db.session.execute(
        db.text(_ACTIVE_SQL), {"user_id": user_id}
    ).fetchall()
    missing = target - len(active)
    if missing <= 0:
        return 0

    candidates = db.session.execute(
        db.text(_CANDIDATES_SQL), {"user_id": user_id, "limit": missing}
    ).mappings().all()

    created = 0
    for row in candidates:
        lo, hi = pair(user_id, row["user_id"])
        try:
            with db.session.begin_nested():
                db.session.add(
                    Match(user_lo=lo, user_hi=hi, score=row["score"], status="active")
                )
            created += 1
        except IntegrityError:
            # Another request created the same pair first; the unique constraint
            # is the guard, so just move on.
            pass

    db.session.commit()
    return created


def active_matches(user_id):
    rows = db.session.execute(db.text(_ACTIVE_SQL), {"user_id": user_id}).mappings().all()
    if not rows:
        return []

    people = {
        u.id: u for u in db.session.scalars(
            db.select(User).where(User.id.in_([r["user_id"] for r in rows]))
        )
    }

    out = []
    for row in rows:
        person = people.get(row["user_id"])
        if person is None:
            continue
        shared = db.session.execute(
            db.text(_SHARED_SQL), {"user_id": user_id, "other_id": row["user_id"]}
        ).scalar()
        out.append({
            "user_id": person.id,
            "name": person.name,
            "email": person.email,
            "score": float(row["score"]),
            "shared": list(shared or []),
        })
    return out


def end_match(user_id, other_id):
    lo, hi = pair(user_id, other_id)
    match = db.session.scalar(
        db.select(Match).filter_by(user_lo=lo, user_hi=hi, status="active")
    )
    if match is None:
        return False

    match.status = "ended"
    match.ended_at = datetime.utcnow()
    db.session.commit()
    return True


def get_answers(user_id):
    answers = {}
    rows = db.session.execute(db.text(_ANSWERS_SQL), {"user_id": user_id}).mappings()
    for row in rows:
        answers.setdefault(row["key"], []).append(row["label"])
    return answers


def get_weights():
    return {
        question.key: float(question.weight)
        for question in db.session.scalars(db.select(Question))
    }


def set_weight(question_key, weight):
    question = db.session.scalar(db.select(Question).filter_by(key=question_key))
    if question is None:
        raise ValueError(f"no question with key {question_key!r}")

    question.weight = weight
    db.session.commit()


if __name__ == "__main__":
    from app import app
    from models import User, UserAnswer

    with app.app_context():
        print("Weights:", get_weights())

        answered = db.session.scalars(
            db.select(User.id)
            .join(UserAnswer, UserAnswer.user_id == User.id)
            .distinct()
            .order_by(User.id)
        ).all()

        for user_id in answered[:3]:
            print(f"\nStudent {user_id} answered: {get_answers(user_id)}")

            print("  top matches:")
            for match in find_matches(user_id, limit=3):
                print(f"    {match['email']:<30} {match['score']:>5}  "
                      f"{', '.join(match['shared'][:6])}")

            print("  recommended clubs:")
            for club in recommend_clubs(user_id, limit=3):
                print(f"    {club['name']:<30} {club['score']:>5}  "
                      f"{', '.join(club['shared'][:6])}")
