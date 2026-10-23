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
    # Whether this account is listed to people who are not already friends
    # with it. False is for accounts that have to exist on a live database
    # without turning up in anybody's suggestions, searches or member lists --
    # a QA login on production, which is the case this was added for.
    #
    # A column rather than a special-cased email address: a second test
    # account then needs no code change, and nothing has to read an address
    # to decide whether somebody is a real student.
    #
    # Defaults to true, and server_default earns its place the way pronouns'
    # does -- it backfills every existing account as listed, which is what
    # they were, and lets the suites keep building User(...) without naming
    # this field.
    discoverable = db.Column(db.Boolean, nullable=False, default=True,
                             server_default="true")
    # Whether this account reviews what students ask for -- today that is hub
    # requests and nothing else. The app has no other notion of privilege, so
    # this is deliberately one boolean and not a role table: there is one
    # thing an admin can do, and inventing a permission system for it would be
    # more machinery than the feature.
    #
    # server_default for the same two reasons as discoverable above: it
    # backfills every existing account as an ordinary student, which is what
    # they are, and lets the suites keep building User(...) without naming it.
    is_admin = db.Column(db.Boolean, nullable=False, default=False,
                         server_default="false")
    # Whether this account may post home fixtures. Separate from is_admin on
    # purpose: somebody who posts the basketball schedule has no business
    # approving hub requests, and an admin should be able to hand this out
    # without handing out everything they have.
    #
    # Two booleans rather than a role table, for the reason is_admin gives:
    # there are two things to be allowed to do, and a permission system for
    # two things is more machinery than the things.
    can_post_fixtures = db.Column(db.Boolean, nullable=False, default=False,
                                  server_default="false")

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


HUB_REQUEST_STATUSES = ("pending", "approved", "declined")


class HubRequest(db.Model):
    """A hub a student asked for, and what was decided about it.

    Kept after it is decided rather than deleted, so a student who asks for
    the same thing twice can be told it was already turned down, and so the
    decision has a record. `club_id` is what an approved one became: it is the
    only link between the request and the hub, and it goes null rather than
    taking the request with it if that hub is ever deleted -- the request is
    still a true account of what was asked for.
    """

    __tablename__ = "hub_requests"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    pitch = db.Column(db.Text, nullable=False)
    requested_by = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    status = db.Column(db.Text, nullable=False, default="pending",
                       server_default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    decided_at = db.Column(db.DateTime)
    club_id = db.Column(db.Integer, db.ForeignKey("clubs.id", ondelete="SET NULL"))
    note = db.Column(db.Text)

    __table_args__ = (
        db.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in HUB_REQUEST_STATUSES) + ")",
            name="ck_hub_request_status",
        ),
        # A decided request names who decided it and when; a pending one names
        # neither. Written as a rule rather than left to the route, because
        # "approved by nobody at no time" is the shape a half-finished branch
        # would leave behind.
        db.CheckConstraint(
            "(status = 'pending') = (decided_by IS NULL AND decided_at IS NULL)",
            name="ck_hub_request_decided",
        ),
        db.CheckConstraint("length(btrim(name)) > 0", name="ck_hub_request_name"),
        # Two people asking for the same hub while it is still pending is one
        # request, not two. Decided ones are exempt, so a hub that was turned
        # down can be asked for again later.
        db.Index("uq_hub_request_pending", db.text("lower(name)"),
                 unique=True, postgresql_where=db.text("status = 'pending'")),
        db.Index("ix_hub_requests_status", "status", "id"),
    )


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
    # The meetup this chat belongs to, if it belongs to one. Null is an
    # ordinary chat made by a student, and everything about those is unchanged:
    # this column is the whole of how the two are told apart.
    #
    # A meetup chat's membership is the meetup's RSVP list, which is why the
    # join code and the invite controls are refused on one.
    post_id = db.Column(
        db.Integer, db.ForeignKey("game_posts.id", ondelete="CASCADE"),
        unique=True,
    )
    # When it stops taking messages. Null never expires, which is every chat
    # that existed before this and every one a student makes by hand.
    expires_at = db.Column(db.DateTime)
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


# The moments in a game worth recording in the conversation. A message with
# one of these set is a notice about a game rather than something somebody
# said, and renders that way.
GAME_EVENTS = ("invited", "accepted", "declined", "ended", "abandoned")

# The columns a message may hang off, in the order they appear inside the live
# CHECK constraint. Keep that order: the rendered constraint text is what
# `dbtool.py status` compares between databases, so reordering here would make a
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
    # ...or record something that happened to a game. Both of these say what a
    # message is *about*, not which chat it hangs off, so neither belongs in
    # ck_message_one_parent below.
    game_id = db.Column(db.Integer, db.ForeignKey("games.id", ondelete="CASCADE"))
    event = db.Column(db.String(12))
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # A message hangs off exactly one chat, never both and never neither.
        db.CheckConstraint(ONE_PARENT, name="ck_message_one_parent"),
        db.CheckConstraint(
            "event IS NULL OR event IN ("
            + ", ".join(f"'{e}'" for e in GAME_EVENTS) + ")",
            name="ck_message_event",
        ),
        # An event message is always about a game, and vice versa.
        db.CheckConstraint("(event IS NULL) = (game_id IS NULL)",
                           name="ck_message_event_has_game"),
        db.Index("ix_messages_match", "match_id", "id"),
        db.Index("ix_messages_club", "club_id", "id"),
        db.Index("ix_messages_room", "room", "id"),
        db.Index("ix_messages_pairing", "pairing_id", "id"),
        db.Index("ix_messages_chat", "chat_id", "id"),
        db.Index("ix_messages_game", "game_id"),
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
    # The one structured part of when a meetup is, and optional. when_text
    # stays free prose -- "Tonight", "Now", "Friday 6pm" -- because that is
    # what it has always held and a backfill would have to guess what those
    # meant. This is here so the meetup's chat has a date to end after; when
    # it is null the chat falls back to a fixed horizon.
    when_on = db.Column(db.Date)
    # When it actually begins. The form has always asked for an hour, a
    # minute and AM or PM and has always discarded them -- the time survived
    # only inside when_text's prose -- so nothing could tell whether a meetup
    # had already happened, and the board listed every one ever posted.
    #
    # Nullable, because every meetup posted before this has no stored time
    # and guessing one out of when_text would be a guess about prose written
    # by somebody else. Without it the board falls back to the date, and
    # failing that to the same 36-hour horizon the meetup's chat uses.
    starts_at = db.Column(db.DateTime)
    max_players = db.Column(db.Integer, nullable=False, default=2)
    status = db.Column(db.Text, nullable=False, default="open")
    # Who this meetup is for. 'campus' is the posting /find has always done:
    # anybody may see it and join it. 'chat' is an arrangement between the
    # people in one conversation, and /find must not list it.
    #
    # A column rather than "look at where its announcing message went", which
    # would answer the same question most of the time. A message can be
    # missing -- eight invites on live were committed without theirs -- and a
    # private meetup whose message had gone would quietly become a public one.
    # GamePairing.mode is here for the same kind of reason.
    audience = db.Column(db.Text, nullable=False, default="campus",
                         server_default="campus")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("mode IN ('online', 'inperson')", name="ck_post_mode"),
        db.CheckConstraint("status IN ('open', 'closed')", name="ck_post_status"),
        db.CheckConstraint("audience IN ('campus', 'chat')",
                           name="ck_post_audience"),
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
    # Whether the activity pickers offer this one. The catalogue is long, and a
    # form asking students to organise something in person has no business
    # listing Tic-Tac-Toe -- so the pickers show a focused handful and let
    # anything else be written in.
    #
    # A presentation shortlist and nothing more: post_new and find_queue still
    # accept any catalogue activity that passes their own checks, so narrowing
    # the list cannot invalidate a post somebody already made.
    preset = db.Column(db.Boolean, nullable=False, default=False,
                       server_default=db.text("false"))
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


class GameSeek(db.Model):
    """One waiting slot per student, for online matchmaking.

    Deliberately not a second use of GameQueue, which serves the in-person
    page. That one is keyed by user_id alone, so sharing it would mean joining
    an online queue kicked you out of the Basketball one -- and its `game`
    column holds activity names, which /find reads and would render as
    "Waiting for tictactoe".
    """

    __tablename__ = "game_seek"

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    kind = db.Column(db.Text, nullable=False)
    # Queue order, so the longest wait is served first.
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # The heartbeat. The waiting page holds a live request, and that request
    # touches this at its start -- the one moment the tab that asked for it
    # demonstrably still existed. A tab that has gone stops touching it and
    # ages out, so nobody is paired into a game the other player never opens.
    seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # The same four kinds ck_game_kind allows: a queue for a game that
        # cannot be played would pair people into nothing.
        db.CheckConstraint(
            "kind IN ('tictactoe', 'connect4', 'battleship', 'chess')",
            name="ck_seek_kind",
        ),
        db.Index("ix_seek_kind", "kind", "created_at"),
    )


class GamePairing(db.Model):
    """Two students the queue put together for one game.

    Originally in-person only; `mode` tells the two apart now that online
    matchmaking uses the same conversation to give two strangers somewhere to
    play. Without it /find would list online pairings on the in-person page.
    """

    __tablename__ = "game_pairings"

    id = db.Column(db.Integer, primary_key=True)
    user_lo = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    user_hi = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    game = db.Column(db.Text, nullable=False)
    # An in-person pairing's `game` is an activity name ("Basketball"); an
    # online one's is a game kind ("tictactoe"), which rules.LABELS turns into
    # something readable.
    mode = db.Column(db.Text, nullable=False, default="inperson",
                     server_default="inperson")
    status = db.Column(db.Text, nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # Ordered like matches, so a pair is stored one way round. Unlike
        # matches there is no unique constraint: the same two people may be
        # paired again for a different game.
        db.CheckConstraint("user_lo < user_hi", name="ck_pairing_order"),
        db.CheckConstraint("status IN ('open', 'closed')", name="ck_pairing_status"),
        db.CheckConstraint("mode IN ('inperson', 'online')", name="ck_pairing_mode"),
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
    # Who ended it, which is not always who lost and never "the other one".
    # The panel has to say "You turned this invite down" to one player and
    # "They turned it down" to the other, and a declined row alone cannot
    # tell them apart -- an invite is declined by the person invited and
    # withdrawn by the person who sent it, and both land here as 'declined'.
    #
    # Null on every row made before this, and on an invite nobody has
    # answered. The panel says something neutral rather than guessing.
    ended_by = db.Column(db.Integer,
                         db.ForeignKey("user.id", ondelete="SET NULL"))
    outcome = db.Column(db.Text)
    # db.JSON does not notice in-place edits: always reassign a fresh object.
    state = db.Column(db.JSON, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Whether each seat has dismissed the finished result from their chat.
    # Per seat, so putting your own chat back to full width does not take the
    # result away from the other player before they have seen it.
    closed_a = db.Column(db.Boolean, nullable=False, default=False,
                         server_default="false")
    closed_b = db.Column(db.Boolean, nullable=False, default=False,
                         server_default="false")
    # When each seat was last at this board. Stamped by the live request the
    # board already holds, so it answers "are they still here" rather than
    # "how long since anybody moved" -- somebody can sit and think for ten
    # minutes, and that is not the same as having gone.
    #
    # Null means not seen since this shipped, which reads as absent and is
    # true: nothing was recording it before.
    seen_a = db.Column(db.DateTime)
    seen_b = db.Column(db.DateTime)
    # And whether the board is on their screen *now*, as their tab last said.
    # Separate from the stamp above on purpose: this is what lets leaving be
    # noticed at once instead of waited out, while seen_a/seen_b stay the
    # honest last-seen that the "left 6 minutes ago" label and the claim clock
    # are both measured from.
    #
    # False for every row that existed before this, which reads as away and is
    # true -- nothing was telling us either way.
    here_a = db.Column(db.Boolean, nullable=False, default=False,
                       server_default="false")
    here_b = db.Column(db.Boolean, nullable=False, default=False,
                       server_default="false")

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
            "outcome IS NULL OR outcome IN "
            "('win', 'draw', 'resigned', 'abandoned')",
            name="ck_game_outcome",
        ),
        # Somebody outside the game cannot have ended it. Match states the
        # same rule about requested_by, for the same reason: the column is
        # read to decide what each player is told.
        db.CheckConstraint(
            "ended_by IS NULL OR ended_by IN (player_a, player_b)",
            name="ck_game_ended_by",
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

    def closed_for(self, seat):
        return self.closed_b if seat else self.closed_a

    def close_for(self, seat):
        if seat:
            self.closed_b = True
        else:
            self.closed_a = True

    def seen_for(self, seat):
        return self.seen_b if seat else self.seen_a

    def mark_seen(self, seat, when):
        if seat:
            self.seen_b = when
        else:
            self.seen_a = when

    def here_for(self, seat):
        return self.here_b if seat else self.here_a

    def mark_here(self, seat, flag):
        if seat:
            self.here_b = flag
        else:
            self.here_a = flag


def pair(a, b):
    """Matches are symmetric but stored once; always normalise through this."""
    return (a, b) if a < b else (b, a)


# Everything a notice can be about. A tuple, and the CHECK below is built from
# it, so adding a kind is one entry here plus one writer in notices.py.
NOTICE_KINDS = (
    "friend_request",    # somebody asked to be your friend
    "friend_accepted",   # the request you sent was accepted
    "chat_invite",       # you were invited to a chat somebody made
    "game_invite",       # you were challenged to a game
    "game_answer",       # the challenge you sent was accepted or declined
    "paired",            # you and somebody else picked the same game
    "message",           # new messages in a chat you were not reading
    "hub_request",       # a student asked for a hub; for whoever reviews them
    "hub_decision",      # the hub you asked for was created, or was not
    "fixture",           # a home game was announced to everybody on campus
)


class Notice(db.Model):
    """One thing worth telling one person about.

    `body` and `url` are deliberately denormalised -- the opposite of
    Message.event, which stores a code and lets event_wording() phrase it per
    viewer. That exists because both players read the same message and it has
    to say "you resigned" to one and "Ann resigned" to the other. A notice has
    exactly one recipient, so there is no per-viewer wording to do, and holding
    the finished sentence means the bell is one query with no joins however
    many rows it shows -- and that a notice still reads correctly after the
    thing it describes has been renamed or deleted.

    game_id and chat_id are here because the existing accept/decline routes are
    keyed by them, which is what makes a notice actionable in place. A friend
    request needs no column of its own: /matches/<other_id>/<action> is keyed
    by the other person, which is actor_id.
    """

    __tablename__ = "notices"

    id = db.Column(db.Integer, primary_key=True)
    # Who is being told.
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    # Who caused it. Null for anything the app itself decided.
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"))
    kind = db.Column(db.String(24), nullable=False)
    body = db.Column(db.Text, nullable=False)
    url = db.Column(db.Text, nullable=False)
    # How many things this one row stands for. Only the message kind ever goes
    # above 1: the writer bumps this rather than adding a second row, so a
    # chatty conversation is one line in the bell and not thirty.
    count = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    game_id = db.Column(db.Integer, db.ForeignKey("games.id", ondelete="CASCADE"))
    chat_id = db.Column(db.Integer, db.ForeignKey("chats.id", ondelete="CASCADE"))
    # When this row last changed, as a number rather than a clock. Separate
    # from the id because a coalesced row is *updated*: the id of "3 new
    # messages from Ann" is the id the first message made, so "newer than what
    # that tab has seen" cannot be answered with ids alone -- and a message
    # that neither toasts nor moves the badge arrives with no signal at all.
    # Drawn from its own sequence, rewritten on every change, and the id is
    # left alone so a bell already on screen keeps working buttons.
    bump = db.Column(db.BigInteger, nullable=False,
                     server_default=db.text("nextval('notices_bump_seq')"))
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in NOTICE_KINDS) + ")",
            name="ck_notice_kind",
        ),
        # Nobody is told about their own doing. This is the one rule the whole
        # feature would be most annoying to get wrong, and a rule in the
        # schema cannot be forgotten by a caller: a writer that gets this
        # wrong fails loudly on insert instead of quietly spamming somebody
        # with their own activity.
        db.CheckConstraint("actor_id IS NULL OR actor_id <> user_id",
                           name="ck_notice_not_self"),
        db.CheckConstraint("count >= 1", name="ck_notice_count"),
        # The badge and the unread filter.
        db.Index("ix_notices_unread", "user_id", "read_at"),
        # The newest-first list.
        db.Index("ix_notices_recent", "user_id", "id"),
        # Finding the open message notice for one conversation, to bump it.
        db.Index("ix_notices_scope", "user_id", "kind", "url"),
    )

    def seen(self):
        return self.read_at is not None


FIXTURE_STATUSES = ("scheduled", "cancelled")


class Fixture(db.Model):
    """What the app calls a campus-wide meetup: something the university is
    putting on here, announced to everybody.

    Called a Fixture in the code and a campus-wide meetup in the interface,
    which is deliberate in both directions.

    Not a "game", because this file already holds Game (an online board
    game), GamePost, GamePairing, GameQueue, GameSeek and GamePostRsvp, and
    MapFlag has a "game" category -- the bare word already means three
    things. Not a "meetup" either, because GamePost is what the interface
    has always called a meetup, and two models answering to the same word is
    exactly how the game* family became hard to read.

    `starts_at` is the first real instant in this schema. Everywhere else a
    time of day is either absent (GamePost.when_on is a bare date) or lives
    inside prose (GamePost.when_text). A fixture has to be sortable and
    alertable by its kick-off, so this is a true UTC datetime, converted from
    Boston time when it is posted. `when_text` is the sentence that was true
    on the day, stored beside it so a notice written last week still reads
    correctly -- the same split Notice.body makes, and for the same reason.
    """

    __tablename__ = "fixtures"

    id = db.Column(db.Integer, primary_key=True)
    # What it is called -- "Home Game", "Spring Carnival", anything. The
    # headline everywhere: the list, the notice, the pin on the map.
    title = db.Column(db.String(80), nullable=False)
    # The details, for when it is a game. Optional, because not every
    # campus-wide meetup has a sport or somebody to play.
    sport = db.Column(db.String(60))
    opponent = db.Column(db.String(80))
    # Naive UTC, like every other datetime here.
    starts_at = db.Column(db.DateTime, nullable=False)
    # How the kick-off read in Boston when it was posted.
    when_text = db.Column(db.String(60), nullable=False)
    venue = db.Column(db.String(120), nullable=False)
    latitude = db.Column(db.Numeric(9, 6), nullable=False)
    longitude = db.Column(db.Numeric(9, 6), nullable=False)
    note = db.Column(db.String(300))
    posted_by = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    status = db.Column(db.String(12), nullable=False, default="scheduled",
                       server_default="scheduled")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def billing(self):
        """The sport and the opponent as one phrase, or an empty string.

        Both are optional now, so there are four cases and every place that
        shows them would otherwise have to handle all four -- which is how
        "Men's Basketball vs None" reaches a notice that was phrased once and
        stored forever.
        """
        if self.sport and self.opponent:
            said = f"{self.sport} vs {self.opponent}"
        elif self.sport or self.opponent:
            said = self.sport or self.opponent
        else:
            return ""
        # Not twice. A title backfilled from these two reads exactly like
        # this, so without this every meetup that predates the title column
        # printed its own heading again on the line underneath it.
        if said.strip().casefold() == (self.title or "").strip().casefold():
            return ""
        return f"{said},"

    __table_args__ = (
        db.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in FIXTURE_STATUSES) + ")",
            name="ck_fixture_status",
        ),
        db.CheckConstraint("length(btrim(title)) > 0", name="ck_fixture_title"),
        # Absent is allowed; blank is not. A field somebody left empty and a
        # field they filled with spaces are different mistakes.
        db.CheckConstraint("sport IS NULL OR length(btrim(sport)) > 0",
                           name="ck_fixture_sport"),
        db.CheckConstraint("opponent IS NULL OR length(btrim(opponent)) > 0",
                           name="ck_fixture_opponent"),
        # What is coming up, which is every read this table has.
        db.Index("ix_fixtures_when", "status", "starts_at"),
    )


# What a map flag can be, and who may see it. Kept beside the CHECK
# constraints below so the values the database accepts and the ones the app
# offers cannot drift apart; app.py reads these for its labels and checks.
# "home" is a home fixture: a scheduled game the university is playing here,
# posted by somebody trusted rather than dropped by a student. It is its own
# kind so a fixture is not dressed as somebody's pick-up game.
MAP_FLAG_KINDS = ("game", "study", "social", "club", "event", "home")
MAP_FLAG_AUDIENCES = (
    "public",    # every signed-in student
    "club",      # members of one club (club_id)
    "friends",   # the friends the owner picked (map_flag_viewers)
    "going",     # people who said they're in for the meetup (post_id)
)


class MapFlag(db.Model):
    """A short-lived meetup beacon inside the approved campus boundary.

    Two kinds share this table:
      - a flag a student drops by hand on the map (post_id is null), which
        counts toward their limit and ends after a chosen duration;
      - an event flag (post_id set), dropped automatically when somebody posts
        an in-person meetup. It lives as long as the meetup, does not count
        toward the limit, and starts out visible only to the people who said
        they're in ("going") until its owner makes it public.
    Either kind can be moved and edited by its owner and nobody else.
    """

    __tablename__ = "map_flags"

    id = db.Column(db.Integer, primary_key=True)
    # This is the ID exposed to browsers. Database row IDs stay internal.
    public_id = db.Column(db.String(36), unique=True, nullable=False)
    owner_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    category = db.Column(db.String(20), nullable=False)
    visibility = db.Column(db.String(12), nullable=False, default="public")
    club_id = db.Column(
        db.Integer, db.ForeignKey("clubs.id", ondelete="CASCADE")
    )
    # The meetup this flag belongs to, for event flags. Deleting the meetup
    # deletes its flag; one meetup has at most one flag.
    post_id = db.Column(
        db.Integer, db.ForeignKey("game_posts.id", ondelete="CASCADE")
    )
    # The home fixture this flag belongs to, for fixture flags. Mirrors
    # post_id above, and exists for the same two reasons: cancelling the
    # fixture takes its pin with it, and a flag the app dropped must not
    # count against the poster's own handful. Without this a fixture flag is
    # indistinguishable from a hand-dropped one, and somebody posting a
    # season would be locked out of the map after three.
    fixture_id = db.Column(
        db.Integer, db.ForeignKey("fixtures.id", ondelete="CASCADE")
    )
    location_key = db.Column(db.String(40), nullable=False)
    location_name = db.Column(db.String(120), nullable=False)
    latitude = db.Column(db.Numeric(9, 6), nullable=False)
    longitude = db.Column(db.Numeric(9, 6), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # When the owner last moved or edited it. Null until they do.
    updated_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.CheckConstraint(
            "category IN (" + ", ".join(f"'{k}'" for k in MAP_FLAG_KINDS) + ")",
            name="ck_map_flag_category",
        ),
        db.CheckConstraint(
            "visibility IN (" + ", ".join(f"'{a}'" for a in MAP_FLAG_AUDIENCES) + ")",
            name="ck_map_flag_visibility",
        ),
        # Each audience carries exactly what it needs: a club for 'club', a
        # meetup for 'going', and nothing extra for the others. ('friends'
        # keeps its list in map_flag_viewers.)
        db.CheckConstraint(
            "(visibility = 'public' AND club_id IS NULL) OR "
            "(visibility = 'club' AND club_id IS NOT NULL) OR "
            "(visibility = 'friends' AND club_id IS NULL) OR "
            "(visibility = 'going' AND club_id IS NULL AND post_id IS NOT NULL)",
            name="ck_map_flag_audience",
        ),
        db.CheckConstraint(
            "expires_at > created_at", name="ck_map_flag_expiration"
        ),
        db.UniqueConstraint("post_id", name="uq_map_flag_post"),
        db.UniqueConstraint("fixture_id", name="uq_map_flag_fixture"),
        db.Index("ix_map_flags_expires", "expires_at"),
        db.Index("ix_map_flags_owner_expires", "owner_id", "expires_at"),
        db.Index("ix_map_flags_club_expires", "club_id", "expires_at"),
    )


class MapFlagViewer(db.Model):
    """Who may see a flag shared with "My friend(s)".

    One row per friend the owner picked. The owner always sees their own
    flag, so they are never listed here. Rows go when the flag goes.
    """

    __tablename__ = "map_flag_viewers"

    flag_id = db.Column(
        db.Integer, db.ForeignKey("map_flags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        # "Which flags were shared with me?" is the question the map asks.
        db.Index("ix_map_flag_viewers_user", "user_id"),
    )
