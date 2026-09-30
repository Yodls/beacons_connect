from datetime import datetime

from sqlalchemy.exc import IntegrityError

from models import Match, db, pair

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

_CANDIDATES_SQL = """
    WITH mine AS (
        SELECT question_id, option_id
        FROM user_answers
        WHERE user_id = :user_id
    ),
    mine_n AS (
        SELECT question_id, count(*) AS n
        FROM mine
        GROUP BY question_id
    ),
    overlap AS (
        SELECT theirs.user_id     AS other_id,
               theirs.question_id AS question_id,
               count(*)              AS n_theirs,
               count(mine.option_id) AS n_shared
        FROM user_answers theirs
        LEFT JOIN mine ON mine.option_id = theirs.option_id
        WHERE theirs.user_id <> :user_id
        GROUP BY theirs.user_id, theirs.question_id
    ),
    scored AS (
        SELECT o.other_id AS other_id,
               ROUND(SUM(
                   q.weight * o.n_shared
                   / (COALESCE(mn.n, 0) + o.n_theirs - o.n_shared)
               ), 2) AS shared_score
        FROM overlap o
        JOIN questions q   ON q.id = o.question_id
        LEFT JOIN mine_n mn ON mn.question_id = o.question_id
        GROUP BY o.other_id
        HAVING SUM(o.n_shared) > 0
    )
    SELECT s.other_id     AS user_id,
           s.shared_score AS shared_score,
           s.shared_score + CASE
               WHEN me.gender IN ('man', 'woman') AND me.gender = them.gender
               THEN :gender_bonus ELSE 0
           END AS rank_score
    FROM scored s
    JOIN "user" me   ON me.id = :user_id
    JOIN "user" them ON them.id = s.other_id
    WHERE NOT EXISTS (
        SELECT 1 FROM matches m
        WHERE m.user_lo = LEAST(:user_id, s.other_id)
          AND m.user_hi = GREATEST(:user_id, s.other_id)
    )
    ORDER BY rank_score DESC, (me.gender = them.gender) DESC, s.other_id
    LIMIT :limit
"""

_ACTIVE_SQL = """
    SELECT other.id    AS user_id,
           other.name  AS name,
           other.email AS email,
           m.score     AS score
    FROM matches m
    JOIN "user" other
      ON other.id = CASE WHEN m.user_lo = :user_id THEN m.user_hi ELSE m.user_lo END
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


def ensure_matches(user_id, target=3, gender_bonus=0):
    """Top the student up to `target` active matches. Safe to call on every view.

    `gender_bonus` steers who gets paired without inflating what is stored: the
    ranking uses it, the saved score is the shared-answer total the dashboard
    shows beside the list of answers in common.
    """
    active = db.session.execute(
        db.text(_ACTIVE_SQL), {"user_id": user_id}
    ).fetchall()
    missing = target - len(active)
    if missing <= 0:
        return 0

    candidates = db.session.execute(
        db.text(_CANDIDATES_SQL),
        {"user_id": user_id, "limit": missing, "gender_bonus": gender_bonus},
    ).mappings().all()

    created = 0
    for row in candidates:
        lo, hi = pair(user_id, row["user_id"])
        try:
            with db.session.begin_nested():
                db.session.add(
                    Match(
                        user_lo=lo,
                        user_hi=hi,
                        score=row["shared_score"],
                        status="active",
                    )
                )
            created += 1
        except IntegrityError:
            pass

    db.session.commit()
    return created


def active_matches(user_id):
    rows = db.session.execute(db.text(_ACTIVE_SQL), {"user_id": user_id}).mappings().all()

    out = []
    for row in rows:
        shared = db.session.execute(
            db.text(_SHARED_SQL), {"user_id": user_id, "other_id": row["user_id"]}
        ).scalar()
        out.append({
            "user_id": row["user_id"],
            "name": row["name"],
            "email": row["email"],
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
