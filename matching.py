from datetime import datetime

from sqlalchemy.exc import IntegrityError

from models import Match, User, UserAnswer, db, pair

_CLUBS_SQL = """
    WITH mine AS (
        SELECT option_id
        FROM user_answers
        WHERE user_id = :user_id
    ),
    hit AS (
        SELECT t.club_id,
               t.question_id,
               t.option_id,
               (mine.option_id IS NOT NULL) AS matched
        FROM club_tags t
        LEFT JOIN mine ON mine.option_id = t.option_id
    ),
    per_q AS (
        SELECT club_id,
               question_id,
               count(*)                        AS n_tags,
               count(*) FILTER (WHERE matched) AS n_matched
        FROM hit
        GROUP BY club_id, question_id
    ),
    scored AS (
        SELECT p.club_id AS club_id,
               ROUND(SUM(q.weight * CASE
                   WHEN q.allows_multiple
                   THEN p.n_matched::numeric / p.n_tags
                   ELSE LEAST(p.n_matched, 1)
               END), 2) AS score
        FROM per_q p
        JOIN questions q ON q.id = p.question_id
        GROUP BY p.club_id
        HAVING SUM(p.n_matched) > 0
    ),
    labels AS (
        SELECT h.club_id AS club_id,
               array_agg(o.label ORDER BY q.id, o.sort_order) AS shared
        FROM hit h
        JOIN question_options o ON o.id = h.option_id
        JOIN questions q        ON q.id = h.question_id
        WHERE h.matched
        GROUP BY h.club_id
    )
    SELECT c.id          AS club_id,
           c.name        AS name,
           c.description AS description,
           s.score       AS score,
           l.shared      AS shared
    FROM scored s
    JOIN clubs c  ON c.id = s.club_id
    JOIN labels l ON l.club_id = s.club_id
    WHERE NOT EXISTS (
        SELECT 1 FROM club_members m
        WHERE m.club_id = c.id AND m.user_id = :user_id
    )
    ORDER BY s.score DESC, c.name
    LIMIT :limit
"""

# Who to suggest. The NOT EXISTS below is status-agnostic on purpose, so one
# clause covers every pair that should stay off the list: people you are
# already friends with, a request either of you has sent, a decline, and
# anyone you have removed. Together with the HAVING (shares at least one
# answer, so they have answered at all) and email_verified, that makes every
# row here someone request_match will accept outright.
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
           them.name      AS name,
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
      -- Unverified accounts cannot log in, so they cannot answer anything
      -- either. Saying so here keeps recommendations and the name search
      -- honest about the same set of people.
      AND them.email_verified
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

# The same weighted Jaccard as _CANDIDATES_SQL, aimed at one named person
# instead of ranking the field, so a friendship you asked for carries the
# same kind of score as one that came off a recommendation.
_PAIR_SCORE_SQL = """
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
        SELECT theirs.question_id    AS question_id,
               count(*)              AS n_theirs,
               count(mine.option_id) AS n_shared
        FROM user_answers theirs
        LEFT JOIN mine ON mine.option_id = theirs.option_id
        WHERE theirs.user_id = :other_id
        GROUP BY theirs.question_id
    )
    SELECT COALESCE(ROUND(SUM(
               q.weight * o.n_shared
               / (COALESCE(mn.n, 0) + o.n_theirs - o.n_shared)
           ), 2), 0) AS score
    FROM overlap o
    JOIN questions q    ON q.id = o.question_id
    LEFT JOIN mine_n mn ON mn.question_id = o.question_id
"""

# Name search. Only students who finished onboarding can be scored on
# answers, which is the same bar recommendations apply, so they are the only
# ones worth offering. The left join carries whatever already stands between
# the two of you, so the page knows which button to draw.
_SEARCH_SQL = """
    SELECT u.id            AS user_id,
           u.name          AS name,
           m.status        AS status,
           m.requested_by  AS requested_by
    FROM "user" u
    LEFT JOIN matches m
      ON m.user_lo = LEAST(:user_id, u.id)
     AND m.user_hi = GREATEST(:user_id, u.id)
    WHERE u.id <> :user_id
      AND u.email_verified
      AND u.name ILIKE :term
      AND EXISTS (SELECT 1 FROM user_answers a WHERE a.user_id = u.id)
    ORDER BY u.name, u.id
    LIMIT :limit
"""

_REQUESTS_SQL = """
    SELECT other.id   AS user_id,
           other.name AS name
    FROM matches m
    JOIN "user" other
      ON other.id = CASE WHEN m.user_lo = :user_id THEN m.user_hi ELSE m.user_lo END
    WHERE m.status = 'invited'
      AND (m.user_lo = :user_id OR m.user_hi = :user_id)
      AND m.requested_by {direction} :user_id
    ORDER BY m.created_at DESC, m.id
"""

SEARCH_MIN = 2
SEARCH_LIMIT = 25


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


def recommend_people(user_id, limit=4, gender_bonus=0):
    """Students worth sending a friend request to. Reads only, writes nothing.

    `gender_bonus` steers the order only. The score returned is the
    shared-answer total, not the ranking total -- nothing shows it to anyone,
    but keeping the two distinct is what lets a test watch the ranking SQL
    behave.
    """
    rows = db.session.execute(
        db.text(_CANDIDATES_SQL),
        {"user_id": user_id, "limit": limit, "gender_bonus": gender_bonus},
    ).mappings().all()

    return [{
        "user_id": row["user_id"],
        "name": row["name"],
        "score": float(row["shared_score"]),
    } for row in rows]


def active_matches(user_id):
    """Your friends, best score first. One query -- the score comes along
    because _ACTIVE_SQL orders on it, not because anything displays it."""
    rows = db.session.execute(
        db.text(_ACTIVE_SQL), {"user_id": user_id}
    ).mappings().all()

    return [{
        "user_id": row["user_id"],
        "name": row["name"],
        "email": row["email"],
        "score": float(row["score"]),
    } for row in rows]


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


def pair_score(user_id, other_id):
    return float(db.session.execute(
        db.text(_PAIR_SCORE_SQL), {"user_id": user_id, "other_id": other_id}
    ).scalar() or 0)


def shared_labels(user_id, other_id):
    return list(db.session.execute(
        db.text(_SHARED_SQL), {"user_id": user_id, "other_id": other_id}
    ).scalar() or [])


def find_match(user_id, other_id):
    lo, hi = pair(user_id, other_id)
    return db.session.scalar(db.select(Match).filter_by(user_lo=lo, user_hi=hi))


def match_state(user_id, status, requested_by):
    """What stands between two students, as one word the page can switch on."""
    if status is None:
        return "none"
    if status == "active":
        return "matched"
    if status == "invited":
        return "you_asked" if requested_by == user_id else "they_asked"
    if status == "declined":
        return "declined"
    return "ended"


def _like(term):
    """A LIKE pattern for a literal search term, wildcards escaped.

    Without this, searching for "100%" would match anything.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_people(user_id, term, limit=SEARCH_LIMIT):
    """Students whose name contains `term`, and where you stand with each.

    One query. `state` is free: match_state is pure Python over the status and
    requested_by columns the LEFT JOIN already returns.
    """
    term = (term or "").strip()
    if len(term) < SEARCH_MIN:
        return []

    rows = db.session.execute(
        db.text(_SEARCH_SQL),
        {"user_id": user_id, "term": _like(term), "limit": limit},
    ).mappings().all()

    return [{
        "user_id": row["user_id"],
        "name": row["name"],
        "state": match_state(user_id, row["status"], row["requested_by"]),
    } for row in rows]


# The dock renders on every page, so these are the cheap shapes it asks for.
# They are now thin names over the fuller functions: once nothing displayed a
# score or a shared-answer list, the fuller ones stopped costing a query per
# row and the two sets collapsed into each other.

def friend_names(user_id):
    """Your friends, best score first. Caller trims for display."""
    return [{"user_id": f["user_id"], "name": f["name"]}
            for f in active_matches(user_id)]


def _requests(user_id, direction):
    rows = db.session.execute(
        db.text(_REQUESTS_SQL.format(direction=direction)), {"user_id": user_id}
    ).mappings().all()
    return [{"user_id": r["user_id"], "name": r["name"]} for r in rows]


def incoming_requests(user_id):
    """Friend requests waiting on you, newest first.

    Returns every one, so a caller gets the badge count and the rows it shows
    from a single query.
    """
    return _requests(user_id, "<>")


def outgoing_requests(user_id):
    return _requests(user_id, "=")


# The dock asks for fewer rows; otherwise these are the same questions.
incoming_names = incoming_requests


def search_names(user_id, term, limit=5):
    return search_people(user_id, term, limit)


def request_match(user_id, other_id):
    """Ask someone to match. Returns a code the caller turns into a message.

    One row per pair is a hard constraint, so asking someone you unmatched
    revives that row rather than inserting a second one. A pair that already
    said no is left alone: a decline should not be re-askable.
    """
    if user_id == other_id:
        return "self"

    other = db.session.get(User, other_id)
    if other is None or not other.email_verified:
        return "unknown"
    if not db.session.scalar(
        db.select(db.func.count()).select_from(UserAnswer)
        .where(UserAnswer.user_id == other_id)
    ):
        return "unknown"

    match = find_match(user_id, other_id)
    score = pair_score(user_id, other_id)

    if match is None:
        lo, hi = pair(user_id, other_id)
        try:
            with db.session.begin_nested():
                db.session.add(Match(user_lo=lo, user_hi=hi, score=score,
                                     status="invited", requested_by=user_id))
        except IntegrityError:
            # Someone asked in the gap between the lookup and the insert.
            return "already"
        db.session.commit()
        return "asked"

    if match.status == "active":
        return "already_matched"
    if match.status == "declined":
        return "declined"
    if match.status == "invited":
        if match.requested_by == user_id:
            return "already"
        # They asked first and now you have too, which is both of you saying
        # yes. No point making someone click Accept on their own idea.
        match.status = "active"
        match.score = score
        match.ended_at = None
        db.session.commit()
        return "matched"

    match.status = "invited"
    match.requested_by = user_id
    match.score = score
    match.ended_at = None
    db.session.commit()
    return "asked"


def respond_to_request(user_id, other_id, action):
    """Accept or decline a request aimed at you, or cancel one you sent."""
    match = find_match(user_id, other_id)
    if match is None or match.status != "invited":
        return False

    if action == "cancel":
        if match.requested_by != user_id:
            return False
        # Nobody has acted on it, so there is nothing worth keeping and the
        # pair goes back to being askable.
        db.session.delete(match)
        db.session.commit()
        return True

    if match.requested_by == user_id:
        return False

    if action == "accept":
        match.status = "active"
        match.score = pair_score(user_id, other_id)
        match.ended_at = None
    elif action == "decline":
        match.status = "declined"
        match.ended_at = datetime.utcnow()
    else:
        return False

    db.session.commit()
    return True
