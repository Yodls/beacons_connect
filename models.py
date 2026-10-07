# Kept out of app.py: `python app.py` loads it as __main__, so importing app
# from here would build a second Flask app and a second db session.
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    gender = db.Column(db.String(12), nullable=False)
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
    # A message may carry a "looking for a game" post, rendered as a card.
    post_id = db.Column(db.Integer, db.ForeignKey("game_posts.id", ondelete="CASCADE"))
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # A message hangs off exactly one chat, never both and never neither.
        db.CheckConstraint(
            "num_nonnulls(match_id, club_id, room, pairing_id) = 1",
            name="ck_message_one_parent",
        ),
        db.Index("ix_messages_match", "match_id", "id"),
        db.Index("ix_messages_club", "club_id", "id"),
        db.Index("ix_messages_room", "room", "id"),
        db.Index("ix_messages_pairing", "pairing_id", "id"),
    )


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
