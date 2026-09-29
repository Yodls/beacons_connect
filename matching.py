"""Beacon Connect - weighted matching.

Students are matched to each other, and to clubs, by the same rule: every answer
two profiles share pays out that question's weight, and the payouts are summed.
Clubs are tagged with the same question options students answer in, so a club
behaves like another profile to match against.

Reads through the app's SQLAlchemy session, so it needs an application context:

    from app import app
    import matching

    with app.app_context():
        matching.find_matches(user_id)

Run it directly to see matches for whoever has answered so far:

    python matching.py
"""
from models import Question, db

# Each shared answer adds its question's weight once per shared option, so a
# multi-select question pays out once per overlapping pick. See README notes.
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
    """Students ranked by weighted score, highest first.

    Returns [{'user_id', 'email', 'score', 'shared'}], where `shared` lists the
    answers the two have in common.
    """
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
    """Clubs ranked by how much of their tagging the student's answers cover.

    Returns [{'club_id', 'name', 'description', 'score', 'shared'}].
    """
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


def get_answers(user_id):
    """One student's answers: {'major': ['Computer Science'], 'hobbies': [...]}"""
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
    """Change a question's weight. Takes effect on the next find_matches call."""
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
