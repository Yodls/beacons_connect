# Kept out of app.py: `python app.py` loads it as __main__, so importing app
# from here would build a second Flask app and a second db session.
import secrets
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# The pronouns we offer and how they are written out. Kept next to the CHECK
# constraint below so the values the database accepts and the labels the site
# shows cannot drift apart.
#
# This lives here rather than beside GENDERS in app.py because matching.py
# needs it too, and matching.py cannot import app -- that import runs the
# other way.
PRONOUNS = {
    "she": "she/her",
    "he": "he/him",
    "they": "they/them",
    "unspecified": "Prefer not to say",
}


def pronoun_label(slug):
    """What to show beside a name, or None when there is nothing to show.

    Choosing not to say is not something to announce next to every mention of
    someone, so it renders as nothing at all. Every display surface goes
    through here, which is what keeps that decision in one place.
    """
    return PRONOUNS.get(slug) if slug in ("she", "he", "they") else None


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    gender = db.Column(db.String(12), nullable=False)
    # server_default earns its place twice: it backfills existing rows when the
    # column is added to a live database, and it lets the test suites keep
    # building User(...) without naming this field.
    pronouns = db.Column(db.String(16), nullable=False,
                         default="unspecified", server_default="unspecified")
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    verification_code_hash = db.Column(db.String(255))
    verification_expires_at = db.Column(db.DateTime)
    verification_attempts = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.CheckConstraint(
            "gender IN ('man', 'woman', 'nonbinary', 'undisclosed')",
            name="ck_user_gender",
        ),
        db.CheckConstraint(
            "pronouns IN ('she', 'he', 'they', 'unspecified')",
            name="ck_user_pronouns",
        ),
    )


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.Text, unique=True, nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    allows_multiple = db.Column(db.Boolean, nullable=False, default=False)
    weight = db.Column(db.Numeric(4, 2), nullable=False, default=1)
    options = db.relationship(
        "QuestionOption",
        order_by="QuestionOption.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (db.CheckConstraint("weight >= 0", name="ck_questions_weight"),)


class QuestionOption(db.Model):
    __tablename__ = "question_options"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(
        db.Integer, db.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    label = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("question_id", "label", name="uq_option_label"),
        # Looks redundant; the composite FKs below need this exact pair to exist.
        db.UniqueConstraint("question_id", "id", name="uq_option_question"),
    )


class UserAnswer(db.Model):
    __tablename__ = "user_answers"

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    question_id = db.Column(db.Integer, nullable=False)
    option_id = db.Column(db.Integer, primary_key=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["question_id", "option_id"],
            ["question_options.question_id", "question_options.id"],
            ondelete="CASCADE",
            name="fk_answer_option",
        ),
        db.Index("ix_user_answers_option_id", "option_id"),
    )


class Club(db.Model):
    __tablename__ = "clubs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False)


class ClubTag(db.Model):

    __tablename__ = "club_tags"

    club_id = db.Column(
        db.Integer, db.ForeignKey("clubs.id", ondelete="CASCADE"), primary_key=True
    )
    question_id = db.Column(db.Integer, nullable=False)
    option_id = db.Column(db.Integer, primary_key=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["question_id", "option_id"],
            ["question_options.question_id", "question_options.id"],
            ondelete="CASCADE",
            name="fk_club_tag_option",
        ),
        db.Index("ix_club_tags_option_id", "option_id"),
    )


class ClubMember(db.Model):
    __tablename__ = "club_members"

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    club_id = db.Column(
        db.Integer, db.ForeignKey("clubs.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# Join codes are read aloud and typed by hand, so the alphabet leaves out every
# glyph that gets mistaken for another: no O or 0, no I, 1 or L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def new_code():
    """One candidate join code. Uniqueness is the database's job, not this
    function's -- callers insert and retry, because checking first and then
    inserting is a race."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class Chat(db.Model):
    """A chat a student made, as opposed to one the app handed them.

    Public only means listed in the directory. Every chat's code works either
    way, so "private" is unlisted rather than sealed.
    """
    __tablename__ = "chats"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    code = db.Column(db.String(16), unique=True, nullable=False)
    is_public = db.Column(db.Boolean, nullable=False, default=False,
                          server_default="false")
    # Follows the author_id idiom already on GamePost.
    owner_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("length(btrim(name)) > 0", name="ck_chats_name"),
        db.Index("ix_chats_public", "is_public"),
    )


class ChatMember(db.Model):
    """Who is in a chat. ClubMember's shape, for the same reason."""
    __tablename__ = "chat_members"

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id = db.Column(
        db.Integer, db.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ChatInvite(db.Model):
    """An invitation waiting on someone, modelled on Match's status column.

    Unlike a declined friend request, a declined invite is not final: it can be
    sent again, and joining by code never consults this table at all, so
    declining never locks anyone out of a chat they later want.
    """
    __tablename__ = "chat_invites"

    chat_id = db.Column(
        db.Integer, db.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    invited_by = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    status = db.Column(db.String(12), nullable=False, default="invited",
                       server_default="invited")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("status IN ('invited', 'accepted', 'declined')",
                           name="ck_chat_invites_status"),
        db.Index("ix_chat_invites_user", "user_id", "status"),
    )


class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)
    user_lo = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    user_hi = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    score = db.Column(db.Numeric(6, 2), nullable=False, default=0)
    status = db.Column(db.Text, nullable=False, default="active")
    # Null only on legacy rows, from back when a matcher paired people without
    # asking. Every new row records which of the two did the asking, so the
    # other one is the one who gets the accept button.
    requested_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)

    __table_args__ = (
        # One row per pair, ever. An ended row is what keeps someone you
        # removed from turning up in your recommendations again.
        db.UniqueConstraint("user_lo", "user_hi", name="uq_match_pair"),
        # Forces the canonical ordering, which rules out both a reversed
        # duplicate and a self-match.
        db.CheckConstraint("user_lo < user_hi", name="ck_match_order"),
        db.CheckConstraint(
            "status IN ('invited', 'active', 'ended', 'declined')",
            name="ck_match_status",
        ),
        # A requester who isn't in the pair is nonsense.
        db.CheckConstraint(
            "requested_by IS NULL OR requested_by IN (user_lo, user_hi)",
            name="ck_match_requester",
        ),
        # So "who do I show Accept to" always has an answer.
        db.CheckConstraint(
            "status <> 'invited' OR requested_by IS NOT NULL",
            name="ck_match_invite_has_asker",
        ),
        db.Index("ix_matches_lo", "user_lo"),
        db.Index("ix_matches_hi", "user_hi"),
    )


# The columns a message may hang off, in the order they appear inside the live
# CHECK constraint. Keep that order: the rendered constraint text is what
# db/schema_diff.py compares between databases, so reordering here would make a
# freshly created database look like it had drifted from a migrated one.
MESSAGE_PARENT_COLUMNS = ("match_id", "club_id", "room", "pairing_id", "chat_id")

ONE_PARENT = "num_nonnulls(" + ", ".join(MESSAGE_PARENT_COLUMNS) + ") = 1"


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id", ondelete="CASCADE"))
    club_id = db.Column(db.Integer, db.ForeignKey("clubs.id", ondelete="CASCADE"))
    # Singleton rooms everyone belongs to, e.g. the campus-wide "community".
    room = db.Column(db.String(32))
    pairing_id = db.Column(
        db.Integer, db.ForeignKey("game_pairings.id", ondelete="CASCADE")
    )
    # A chat a student made, as opposed to the singleton `room` above.
    chat_id = db.Column(db.Integer, db.ForeignKey("chats.id", ondelete="CASCADE"))
    # A message may carry a "looking for a game" post, rendered as a card.
    post_id = db.Column(db.Integer, db.ForeignKey("game_posts.id", ondelete="CASCADE"))
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # A message hangs off exactly one chat, never both and never neither.
        db.CheckConstraint(ONE_PARENT, name="ck_message_one_parent"),
        db.Index("ix_messages_match", "match_id", "id"),
        db.Index("ix_messages_club", "club_id", "id"),
        db.Index("ix_messages_room", "room", "id"),
        db.Index("ix_messages_pairing", "pairing_id", "id"),
        db.Index("ix_messages_chat", "chat_id", "id"),
    )


# Every chat kind that has a table of its own, and the messages column that
# points at it. Adding a kind means adding one entry here: attach_parent,
# message_filter and the chat list all read this rather than each keeping their
# own if/elif chain.
#
# The "room" column is deliberately absent. It holds a name, not an id, so the
# singleton community room stays an explicit special case in the three callers.
MESSAGE_PARENTS = {
    "match": Message.match_id,
    "club": Message.club_id,
    "pair": Message.pairing_id,
    "group": Message.chat_id,
}


class GamePost(db.Model):
    """A "looking for a game" post in the campus-wide room."""

    __tablename__ = "game_posts"

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    game = db.Column(db.Text, nullable=False)
    mode = db.Column(db.Text, nullable=False, default="online")
    note = db.Column(db.Text)
    # Filled in for in-person meetups; unused by online posts.
    location = db.Column(db.Text)
    when_text = db.Column(db.Text)
    max_players = db.Column(db.Integer, nullable=False, default=2)
    status = db.Column(db.Text, nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("mode IN ('online', 'inperson')", name="ck_post_mode"),
        db.CheckConstraint("status IN ('open', 'closed')", name="ck_post_status"),
        db.CheckConstraint("max_players >= 2", name="ck_post_players"),
        db.Index("ix_game_posts_created", "created_at"),
    )


class GamePostRsvp(db.Model):
    __tablename__ = "game_post_rsvps"

    post_id = db.Column(
        db.Integer, db.ForeignKey("game_posts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Activity(db.Model):
    """Something students can play, and how it can be played."""

    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, unique=True, nullable=False)
    online = db.Column(db.Boolean, nullable=False, default=False)
    in_person = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    locations = db.relationship(
        "ActivityLocation",
        order_by="ActivityLocation.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # An activity nobody can play either way is nonsense.
        db.CheckConstraint("online OR in_person", name="ck_activity_playable"),
    )


class ActivityLocation(db.Model):
    __tablename__ = "activity_locations"

    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(
        db.Integer, db.ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("activity_id", "name", name="uq_activity_location"),
    )


class GameQueue(db.Model):
    """One waiting slot per student, for in-person matchmaking."""

    __tablename__ = "game_queue"

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    game = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (db.Index("ix_queue_game", "game", "created_at"),)


class GamePairing(db.Model):
    """Two students the queue put together for one in-person game."""

    __tablename__ = "game_pairings"

    id = db.Column(db.Integer, primary_key=True)
    user_lo = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    user_hi = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    game = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # Ordered like matches, so a pair is stored one way round. Unlike
        # matches there is no unique constraint: the same two people may be
        # paired again for a different game.
        db.CheckConstraint("user_lo < user_hi", name="ck_pairing_order"),
        db.CheckConstraint("status IN ('open', 'closed')", name="ck_pairing_status"),
        db.Index("ix_pairings_lo", "user_lo"),
        db.Index("ix_pairings_hi", "user_hi"),
    )


class Game(db.Model):
    __tablename__ = "games"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False, default="invited")
    player_a = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    player_b = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    turn_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"))
    winner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"))
    outcome = db.Column(db.Text)
    # db.JSON does not notice in-place edits: always reassign a fresh object.
    state = db.Column(db.JSON, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("player_a <> player_b", name="ck_game_two_players"),
        db.CheckConstraint(
            "status IN ('invited', 'active', 'finished', 'declined')",
            name="ck_game_status",
        ),
        # All four kinds listed now so adding their rules needs no migration.
        db.CheckConstraint(
            "kind IN ('tictactoe', 'connect4', 'battleship', 'chess')",
            name="ck_game_kind",
        ),
        db.CheckConstraint(
            "outcome IS NULL OR outcome IN ('win', 'draw', 'resigned')",
            name="ck_game_outcome",
        ),
        db.Index("ix_games_player_a", "player_a"),
        db.Index("ix_games_player_b", "player_b"),
    )

    def seat_of(self, user_id):
        if user_id == self.player_a:
            return 0
        if user_id == self.player_b:
            return 1
        return None

    def user_in_seat(self, seat):
        return self.player_a if seat == 0 else self.player_b


def pair(a, b):
    """Matches are symmetric but stored once; always normalise through this."""
    return (a, b) if a < b else (b, a)
