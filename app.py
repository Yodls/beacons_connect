import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_mail import Mail
from jinja2 import ChoiceLoader, FileSystemLoader
from flask_mail import Message as MailMessage
from werkzeug.security import check_password_hash, generate_password_hash

import games as rules
import matching
import notices
from sqlalchemy.exc import IntegrityError

from models import (
    Activity,
    ActivityLocation,
    Chat,
    ChatInvite,
    ChatMember,
    Club,
    ClubMember,
    Game,
    GamePairing,
    GameSeek,
    GamePost,
    GamePostRsvp,
    GameQueue,
    GAME_EVENTS,
    Match,
    MESSAGE_PARENTS,
    Message,
    PRONOUNS,
    Question,
    User,
    UserAnswer,
    db,
    new_code,
    pair,
    pronoun_label,
)

load_dotenv()

CODE_TTL = timedelta(minutes=10)
MAX_VERIFICATION_ATTEMPTS = 5
OPTIONAL_QUESTIONS = {"minor"}
# How many people to suggest in the home rail. The friends page asks for more.
RECOMMEND_LIMIT = 4
GENDER_BONUS = 2.0
GENDERS = {
    "man": "Man",
    "woman": "Woman",
    "nonbinary": "Non-binary",
    "undisclosed": "Prefer not to say",
}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["MAIL_SERVER"] = os.environ["MAIL_SERVER"]
app.config["MAIL_PORT"] = int(os.environ["MAIL_PORT"])
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "").lower() == "true"
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "").lower() == "true"
app.config["MAIL_USERNAME"] = os.environ["MAIL_USERNAME"]
app.config["MAIL_PASSWORD"] = os.environ["MAIL_PASSWORD"]
app.config["MAIL_DEFAULT_SENDER"] = os.environ["MAIL_DEFAULT_SENDER"]

# A development database is one whose name ends in _dev, and the UI says so
# when it is on one. Asked this way round, the same code drops onto a live
# server with no configuration and no flag to remember: whatever the production
# database is called, DB_BANNER is None and the banner cannot render.
#
# Asking the opposite -- "is this NOT the live database?" -- needs the live name
# written down here, which is the one thing this file cannot know. It was
# briefly `LIVE_DB = "capstone"`, which would have put a development bar on any
# server whose database had a different name.
#
# tests/_env.py refuses to run against anything that is not a _dev database, so
# this is one shared definition rather than two.
DB_NAME = urlsplit(app.config["SQLALCHEMY_DATABASE_URI"]).path.lstrip("/")
DEV_DB = DB_NAME.endswith("_dev")
DB_BANNER = DB_NAME if DEV_DB else None


@app.context_processor
def inject_db_banner():
    return {"db_banner": DB_BANNER}


# A gitignored templates_local/ overrides templates/ when it exists, so local
# UI experiments stay off the repository. No folder, no change in behaviour.
_local_ui = os.path.join(app.root_path, "templates_local")
if os.path.isdir(_local_ui):
    app.jinja_loader = ChoiceLoader([FileSystemLoader(_local_ui), app.jinja_loader])

db.init_app(app)
mail = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def has_onboarded(user_id):
    return db.session.scalar(
        db.select(UserAnswer.user_id).filter_by(user_id=user_id).limit(1)
    ) is not None


def issue_verification_code(user):
    code = f"{secrets.randbelow(1_000_000):06d}"
    user.verification_code_hash = generate_password_hash(code)
    user.verification_expires_at = datetime.now(timezone.utc) + CODE_TTL
    user.verification_attempts = 0
    db.session.commit()
    send_verification_email(user, code)


def send_verification_email(user, code):
    mail.send(
        MailMessage(
            subject="Verify your Beacon account",
            recipients=[user.email],
            body=(
                f"Your Beacon verification code is {code}\n\n"
                f"It expires in {int(CODE_TTL.total_seconds() // 60)} minutes."
            ),
        )
    )


ONBOARDING_EXEMPT = {
    "static",
    "onboarding",
    "login",
    "logout",
    "register",
    "verify",
    "resend_code",
}


# Pages that only make sense before you are signed in. Guarding the endpoint
# rather than the page body covers POST as well as GET, which is the part that
# matters: login() accepts POST, and login_user() would otherwise let someone
# already signed in submit the form and land on whichever account it named.
GUEST_ONLY = {"login", "register", "verify", "resend_code"}


@app.before_request
def guests_only():
    if request.endpoint in GUEST_ONLY and current_user.is_authenticated:
        flash("You're already signed in.")
        return redirect(url_for("index"))

    return None


@app.before_request
def require_onboarding():
    if request.endpoint is None or request.endpoint in ONBOARDING_EXEMPT:
        return None

    if not current_user.is_authenticated:
        return None

    if has_onboarded(current_user.id):
        return None

    return redirect(url_for("onboarding"))


DOCK_FRIENDS = 8
DOCK_REQUESTS = 3
DOCK_RESULTS = 5
# The dock's own search term. Namespaced so it cannot collide with /people?q=.
DOCK_PARAM = "fq"


@app.context_processor
def inject_dock():
    """Everything the friends dock needs, on every page that renders the shell.

    A context processor rather than a before_request on purpose: Flask only
    calls these from its render paths, so the chat and game pollers -- which
    return jsonify several times a minute -- pay nothing for this.

    Three queries per authenticated page render, via the deliberately slim
    helpers in matching.py. Reaching for active_matches or incoming_requests
    here instead would make it one query plus one per row.

    The third is the pending chat invites. It is a constant cost whatever the
    numbers involved, which is what the query-count test actually pins.

    Six now, not three: the bell adds the unread summary, the rows it draws and
    the high-water mark the channel starts from. All three are flat in the
    number of notices, which is the property that test actually protects.
    """
    if not current_user.is_authenticated:
        return {}

    # The auth pages and onboarding render base.html, never the shell, so the
    # dock cannot appear on them. Bail before paying for data nobody shows.
    if request.endpoint in ONBOARDING_EXEMPT or request.endpoint == "onboarding":
        return {}

    # Both come back whole, so the counts and the rows shown cost one query
    # each. Requests you sent are not waiting on you, and incoming_names
    # already filters those out.
    waiting = matching.incoming_names(current_user.id)
    friends = matching.friend_names(current_user.id)

    # Deliberately separate from `waiting`. A test reads the badge out of the
    # dock and asserts it equals len(incoming_names) exactly, so folding chat
    # invites into pending_count would make that badge lie about what it counts.
    invites = db.session.execute(
        db.select(Chat, User.name)
        .join(ChatInvite, ChatInvite.chat_id == Chat.id)
        .join(User, User.id == ChatInvite.invited_by)
        .where(ChatInvite.user_id == current_user.id,
               ChatInvite.status == "invited")
        .order_by(ChatInvite.created_at.desc())
    ).all()

    # The bell. Three queries, and flat in the number of notices: a notice
    # carries its own finished wording, so no row it draws costs a join.
    bell_token, bell_unread = notices.state(current_user.id)
    term = request.args.get(DOCK_PARAM, "").strip()
    return {
        "pending_count": len(waiting),
        "notice_rows": notices.recent(current_user.id),
        "notice_count": bell_unread,
        # Handed to the live channel so its first wait is a real wait, and so a
        # notice already on the page is never toasted a second time.
        "notice_token": bell_token,
        "notice_seen": notices.watermark(current_user.id),
        "chat_invites": [{"chat_id": chat.id, "name": chat.name, "from": who}
                         for chat, who in invites[:DOCK_REQUESTS]],
        "invite_count": len(invites),
        # What the panel badge shows. Separate from pending_count on purpose:
        # that one is also the home banner, which says "friend request" in
        # words and is read back by a test, so widening it would make the
        # banner miscount rather than mislabel -- a much harder bug to spot.
        "waiting_total": len(waiting) + len(invites),
        "dock_requests": waiting[:DOCK_REQUESTS],
        "dock_friends": friends[:DOCK_FRIENDS],
        "dock_friends_more": len(friends) > DOCK_FRIENDS,
        "dock_term": term,
        "dock_short": bool(term) and len(term) < matching.SEARCH_MIN,
        "dock_results": (matching.search_names(current_user.id, term,
                                               limit=DOCK_RESULTS)
                         if term else []),
        # The templates need both so the name of the param and the minimum
        # length cannot drift from what this function enforces.
        "dock_param": DOCK_PARAM,
        "search_min": matching.SEARCH_MIN,
    }


RETURN_TO = {"matches", "clubs", "chats"}


def joined_clubs(user_id):
    return db.session.scalars(
        db.select(Club)
        .join(ClubMember, ClubMember.club_id == Club.id)
        .where(ClubMember.user_id == user_id)
        .order_by(Club.name)
    ).all()


def joined_chats(user_id):
    """The student-made chats someone is in. joined_clubs' shape."""
    return db.session.scalars(
        db.select(Chat)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(ChatMember.user_id == user_id)
        .order_by(Chat.name)
    ).all()


def back_to():
    target = request.form.get("back", "matches")
    return redirect(url_for(target if target in RETURN_TO else "matches"))


def back_to_people():
    """Back to the search, carrying the term so the results are still there."""
    term = request.form.get("q", "").strip()
    return redirect(url_for("people", q=term) if term else url_for("people"))


def dock_back():
    """Where a dock action came from, or None if it did not come from the dock.

    The dock rides on every page, so unlike back_to() it cannot work off a
    whitelist of endpoints -- it has to send you back to an arbitrary path.
    That is the whole risk here, so anything that could leave this site is
    refused and the caller falls back to its usual destination.
    """
    raw = request.form.get("dock_back", "")
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None

    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return None

    return redirect(raw)


MESSAGE_MAX = 2000


COMMUNITY = "community"
COMMUNITY_TITLE = "Community Chat"
POST_MODES = {"online": "Online", "inperson": "In person"}

# The one value the activity select may carry that is not a catalogue activity.
# A sentinel rather than "anything we do not recognise" on purpose: writing an
# activity in is an explicit choice with its own two fields, so a stray or
# mistyped `game` is still refused rather than quietly becoming a meetup for
# whatever the sender felt like typing.
OTHER = "other"
CUSTOM_GAME_MAX = 60
CUSTOM_LOCATION_MAX = 120

# There are four kinds of chat. Naming them here rather than in the templates
# keeps the two places that label a chat from drifting apart, and stops a new
# kind rendering as whichever one the else-branch happened to name.
CHAT_SUBTITLE = {
    COMMUNITY: "Everyone on campus",
    "match": "Friend",
    "pair": "Game pairing",
    "club": "Club space",
    "group": "Group chat",
}
CHAT_TAG = {
    COMMUNITY: "Campus",
    "match": "Friend",
    "pair": "Game",
    "club": "Club",
    "group": "Group",
}

def activity_catalogue(in_person_only=False, presets_only=False):
    """Every activity with its locations, ready for a form or for validation.

    `presets_only` is what the pickers ask for: the focused handful rather than
    the whole catalogue. Validation deliberately does not pass it -- a post
    somebody made last week names an activity that may not be offered today,
    and it has to go on being valid.

    selectinload matters here: the locations relationship is lazy, so without
    it this was one query per activity -- 17 of them on every page that built
    a catalogue, measured.
    """
    query = (db.select(Activity)
             .options(db.selectinload(Activity.locations))
             .order_by(Activity.sort_order))
    if in_person_only:
        query = query.where(Activity.in_person.is_(True))
    if presets_only:
        query = query.where(Activity.preset.is_(True))
    return [
        {
            "name": a.name,
            "online": a.online,
            "in_person": a.in_person,
            "preset": a.preset,
            "locations": [loc.name for loc in a.locations],
        }
        for a in db.session.scalars(query)
    ]


def find_activity(name):
    return db.session.scalar(db.select(Activity).filter_by(name=name))




class _Room:
    """Stand-in parent for a singleton room, which has no table of its own."""

    id = 0


def chat_target(kind, target_id):
    """Resolve a chat and check the student may see it.

    Access is derived from data that already exists: a friendship, or a club
    membership. Returns (parent, title) or (None, None).
    """
    if kind == "match":
        lo, hi = pair(current_user.id, target_id)
        match = db.session.scalar(
            db.select(Match).filter_by(user_lo=lo, user_hi=hi, status="active")
        )
        if match is None:
            return None, None
        other = db.session.get(User, target_id)
        return match, (other.name if other else "Chat")

    if kind == COMMUNITY:
        # Everyone who has finished onboarding is in this one.
        return _Room(), COMMUNITY_TITLE

    if kind == "pair":
        pairing = db.session.get(GamePairing, target_id)
        if pairing is None or current_user.id not in (pairing.user_lo,
                                                      pairing.user_hi):
            return None, None
        other_id = (pairing.user_hi if pairing.user_lo == current_user.id
                    else pairing.user_lo)
        other = db.session.get(User, other_id)
        name = other.name if other else "Partner"
        return pairing, f"{pairing.game} with {name}"

    if kind == "group":
        # Membership is the only key. A join code gets you a ChatMember row;
        # it is never itself permission to read a chat.
        if db.session.get(ChatMember, (current_user.id, target_id)) is None:
            return None, None
        chat = db.session.get(Chat, target_id)
        if chat is None:
            return None, None
        return chat, chat.name

    if kind == "club":
        if db.session.get(ClubMember, (current_user.id, target_id)) is None:
            return None, None
        club = db.session.get(Club, target_id)
        if club is None:
            return None, None
        return club, club.name

    return None, None


def attach_parent(message, kind, parent):
    """Point a message at exactly one parent, as the check constraint demands.

    The kind-to-column mapping lives in models.MESSAGE_PARENTS, so adding a
    chat kind does not mean remembering this function, message_filter and the
    chat list separately.
    """
    if kind == COMMUNITY:
        # The one kind with no table: its column holds a name, not an id.
        message.room = COMMUNITY
        return
    setattr(message, MESSAGE_PARENTS[kind].key, parent.id)


def message_filter(kind, parent):
    if kind == COMMUNITY:
        return Message.room == COMMUNITY
    return MESSAGE_PARENTS[kind] == parent.id


def event_wording(message, game, actor_name, other_name):
    """How a game event reads to the person looking at it.

    The stored body is neutral prose, because a Message row is written once
    and read by two people who need different sentences: the sender wants
    "You invited Ben", the recipient "Ann invited you". This is the only
    place current_user is known, so this is where that choice belongs.
    """
    label = rules.LABELS.get(game.kind, game.kind) if game else "a game"
    mine = message.user_id == current_user.id
    me, them = ("You", other_name) if mine else (actor_name, "you")

    if message.event == "invited":
        return f"{me} invited {them} to {label}."
    if message.event == "accepted":
        return f"{me} accepted the {label} invite."
    if message.event == "declined":
        return f"{me} declined the {label} invite."
    # A result reads the same to both: the stored prose already names people.
    return message.body


def chat_other_id(kind, parent):
    """The one other person in this conversation, or None.

    A one-to-one chat and a pair chat each have exactly one, which is what
    lets a game pane appear in either. chat_target already works this out for
    a pairing and throws it away, so this is the same answer kept.
    """
    if kind == "match":
        return (parent.user_hi if parent.user_lo == current_user.id
                else parent.user_lo)
    if kind == "pair":
        return (parent.user_hi if parent.user_lo == current_user.id
                else parent.user_lo)
    return None


def chat_rows(kind, parent, after=0):
    rows = db.session.execute(
        db.select(Message, User.name)
        .join(User, User.id == Message.user_id)
        .where(message_filter(kind, parent), Message.id > after)
        .order_by(Message.id)
    ).all()

    posts = {}
    going = {}
    attending = set()
    post_ids = [m.post_id for m, _ in rows if m.post_id]
    if post_ids:
        posts = {p.id: p for p in db.session.scalars(
            db.select(GamePost).where(GamePost.id.in_(post_ids)))}
        going, attending = rsvps_for(post_ids)

    # One query for every game mentioned, rather than one per message.
    events = {}
    game_ids = [m.game_id for m, _ in rows if m.game_id]
    if game_ids:
        events = {g.id: g for g in db.session.scalars(
            db.select(Game).where(Game.id.in_(game_ids)))}

    out = []
    for m, name in rows:
        entry = {
            "id": m.id,
            "user_id": m.user_id,
            "name": name,
            "body": m.body,
            "at": m.created_at.strftime("%H:%M"),
            "mine": m.user_id == current_user.id,
        }
        post = posts.get(m.post_id)
        if post is not None:
            entry["post"] = post_payload(
                post, going.get(post.id, []), post.id in attending)

        if m.event:
            game = events.get(m.game_id)
            other = db.session.get(
                User,
                game.user_in_seat(1 - game.seat_of(current_user.id))
            ) if game and game.seat_of(current_user.id) is not None else None
            entry["event"] = m.event
            entry["game_id"] = m.game_id
            entry["body"] = event_wording(
                m, game, name, other.name if other else "them")
            # Only the invitee, and only while it is still unanswered, gets
            # buttons. The poller reads this rather than deciding for itself.
            entry["can_answer"] = bool(
                game is not None
                and game.status == "invited"
                and m.event == "invited"
                and game.player_b == current_user.id
            )
            entry["accept_url"] = url_for("game_action", game_id=m.game_id,
                                          action="accept")
            entry["decline_url"] = url_for("game_action", game_id=m.game_id,
                                           action="decline")
        out.append(entry)
    return out


def rsvps_for(post_ids):
    """Who is going to each of several posts, and which of them I am in.

    Two queries for the whole page rather than two per post. post_payload
    takes the result so a single post still works without a batch.
    """
    if not post_ids:
        return {}, set()

    names = {}
    for post_id, name in db.session.execute(
        db.select(GamePostRsvp.post_id, User.name)
        .join(User, User.id == GamePostRsvp.user_id)
        .where(GamePostRsvp.post_id.in_(post_ids))
        .order_by(User.name)
    ):
        names.setdefault(post_id, []).append(name)

    mine = set(db.session.scalars(
        db.select(GamePostRsvp.post_id)
        .where(GamePostRsvp.post_id.in_(post_ids),
               GamePostRsvp.user_id == current_user.id)
    ))
    return names, mine


def post_payload(post, going=None, im_going=None):
    if going is None:
        going, mine = rsvps_for([post.id])
        going = going.get(post.id, [])
        im_going = post.id in mine
    return {
        "id": post.id,
        "game": post.game,
        "mode": post.mode,
        "mode_label": POST_MODES.get(post.mode, post.mode),
        "note": post.note,
        "location": post.location,
        "when_text": post.when_text,
        "max_players": post.max_players,
        "status": post.status,
        "going": going,
        "count": len(going),
        "full": len(going) >= post.max_players,
        "im_going": bool(im_going),
        "mine": post.author_id == current_user.id,
    }


def chat_entry(kind, target_id, title):
    """One row for the chat list. The wording for a kind lives in one place."""
    return {
        "kind": kind,
        "target_id": target_id,
        "title": title,
        "subtitle": CHAT_SUBTITLE[kind],
        "tag": CHAT_TAG[kind],
    }


def chat_list():
    """Every chat the student can open, most recently active first."""
    entries = [chat_entry(COMMUNITY, 0, COMMUNITY_TITLE)]
    for friend in matching.active_matches(current_user.id):
        entries.append(chat_entry("match", friend["user_id"], friend["name"]))
    for pairing in db.session.scalars(
        db.select(GamePairing).where(
            db.or_(GamePairing.user_lo == current_user.id,
                   GamePairing.user_hi == current_user.id),
            GamePairing.status == "open",
        )
    ):
        other_id = (pairing.user_hi if pairing.user_lo == current_user.id
                    else pairing.user_lo)
        other = db.session.get(User, other_id)
        # An in-person pairing's game is an activity name; an online one's is a
        # game kind, which would otherwise read "tictactoe with Ana".
        label = (rules.LABELS.get(pairing.game, pairing.game)
                 if pairing.mode == "online" else pairing.game)
        entries.append(chat_entry(
            "pair", pairing.id,
            f"{label} with {other.name if other else 'Partner'}",
        ))

    for club in joined_clubs(current_user.id):
        entries.append(chat_entry("club", club.id, club.name))

    for chat in joined_chats(current_user.id):
        entries.append(chat_entry("group", chat.id, chat.name))

    last = {}
    newest = db.session.execute(
        db.select(Message.body, Message.created_at)
        .where(Message.room == COMMUNITY)
        .order_by(Message.id.desc()).limit(1)
    ).first()
    if newest:
        last[(COMMUNITY, 0)] = (newest[0], newest[1])

    for kind, column in MESSAGE_PARENTS.items():
        for parent_id, body, when in db.session.execute(
            db.select(column, Message.body, Message.created_at)
            .where(column.is_not(None))
            .order_by(Message.id.desc())
        ).all():
            last.setdefault((kind, parent_id), (body, when))

    for entry in entries:
        key = (entry["kind"], entry["target_id"])
        if entry["kind"] == "match":
            lo, hi = pair(current_user.id, entry["target_id"])
            match = db.session.scalar(
                db.select(Match).filter_by(user_lo=lo, user_hi=hi, status="active")
            )
            key = ("match", match.id if match else 0)
        preview, when = last.get(key, (None, None))
        entry["preview"] = preview
        entry["when"] = when

    entries.sort(key=lambda e: (e["when"] is not None, e["when"]), reverse=True)
    return entries


@app.route("/")
@login_required
def index():
    return render_template(
        "home.html",
        chats=chat_list(),
        recs=matching.recommend_people(
            current_user.id, limit=RECOMMEND_LIMIT, gender_bonus=GENDER_BONUS
        ),
    )


@app.route("/matches")
@login_required
def matches():
    return render_template(
        "matches.html",
        matches=matching.active_matches(current_user.id),
        requests=matching.incoming_requests(current_user.id),
        recs=matching.recommend_people(
            current_user.id, limit=10, gender_bonus=GENDER_BONUS
        ),
        joined=joined_clubs(current_user.id),
        clubs=matching.recommend_clubs(current_user.id, limit=5),
    )


def chat_audience(kind, parent, target_id):
    """Everybody a new message should be announced to, bar whoever sent it.

    The campus-wide room answers with nobody, deliberately: one post there
    would notify every student on the site at once.
    """
    if kind == COMMUNITY:
        return []
    if kind == "match":
        return [target_id]
    if kind == "pair":
        return [parent.user_hi if parent.user_lo == current_user.id
                else parent.user_lo]
    if kind == "group":
        return list(db.session.scalars(
            db.select(ChatMember.user_id)
            .where(ChatMember.chat_id == parent.id,
                   ChatMember.user_id != current_user.id)))
    if kind == "club":
        return list(db.session.scalars(
            db.select(ClubMember.user_id)
            .where(ClubMember.club_id == parent.id,
                   ClubMember.user_id != current_user.id)))
    return []


def notify_message(kind, parent, target_id, title=None):
    """Tell the rest of a conversation that something was said in it.

    Note which target_id each notice gets. A one-to-one chat is addressed by
    the *other* person, so the link in Ben's notice has to name Ann, not Ben --
    the sender, not the recipient. Every other kind is addressed by the chat
    itself and is the same url for everyone in it.

    `title` is what chat_target already worked out, passed on so a notice about
    a group or a club can name it. A one-to-one chat's title is the other
    person's name, which the sentence already carries, so it is left out there.
    """
    where = None if kind == "match" else title
    for user_id in chat_audience(kind, parent, target_id):
        notices.message(
            user_id, current_user, kind,
            current_user.id if kind == "match" else target_id,
            chat_id=parent.id if kind == "group" else None,
            where=where,
        )


@app.route("/chats/<kind>/<int:target_id>", methods=["GET", "POST"])
@login_required
def chat(kind, target_id):
    parent, title = chat_target(kind, target_id)

    if parent is None:
        flash("That chat is not available to you.")
        return redirect(url_for("index"))

    if request.method == "POST":
        body = request.form.get("body", "").strip()

        if not body:
            flash("Write something first.")
        elif len(body) > MESSAGE_MAX:
            flash(f"Messages are limited to {MESSAGE_MAX} characters.")
        else:
            message = Message(user_id=current_user.id, body=body)
            attach_parent(message, kind, parent)
            db.session.add(message)
            notify_message(kind, parent, target_id, title)
            db.session.commit()

        return redirect(url_for("chat", kind=kind, target_id=target_id))

    # Reading a conversation retires its "new messages" notice, which is the
    # whole of "do not tell somebody about what they are already looking at".
    # The live channel does the same for whichever chat a tab has open, so this
    # covers arriving and that covers staying.
    if notices.clear_chat(current_user.id, kind, target_id):
        db.session.commit()

    # Only a one-to-one chat has a single person to name. resolve_room above
    # already fetched this user for that kind, so the identity map answers
    # this without a second query.
    other = db.session.get(User, target_id) if kind == "match" else None

    # Only a group chat has a code, a member list and an owner. The invite
    # picker offers your friends, since inviting a stranger by name is what
    # the code is for.
    # The game pane. Only a one-on-one chat has one: there is no "the game
    # between us" in a room with twelve people in it.
    # A pair chat has an opponent too, so it gets a board. pane_game is keyed
    # on the two players, so it needed no change for this.
    opponent_id = chat_other_id(kind, parent)
    game = pane_game(opponent_id) if opponent_id else None
    game_seat = game.seat_of(current_user.id) if game is not None else None

    group = parent if kind == "group" else None
    invitable = []
    if group is not None:
        inside = {m.id for m in chat_members(group.id)}
        invitable = [f for f in matching.friend_names(current_user.id)
                     if f["user_id"] not in inside]

    return render_template(
        "chat.html",
        kind=kind,
        target_id=target_id,
        title=title,
        pronouns=pronoun_label(other.pronouns) if other else None,
        group=group,
        members=chat_members(group.id) if group is not None else [],
        is_owner=group is not None and group.owner_id == current_user.id,
        invitable=invitable,
        subtitle=CHAT_SUBTITLE[kind],
        game=game,
        seat=game_seat,
        payload=game_payload(game, game_seat) if game is not None else None,
        game_token=game_token(game),
        # Asked of live_game_with rather than the pane: an invite is a message
        # now, so the pane does not see it, and offering to start a second
        # game during one would be wrong.
        opponent_id=opponent_id,
        # Still friends-only: game_new requires a friendship, so a pair chat
        # has no rematch button. Queue again from the lobby instead.
        can_start=(kind == "match" and live_game_with(target_id) is None),
        other_id=target_id,
        messages=chat_rows(kind, parent),
        game_kinds=[(k, rules.LABELS[k]) for k in rules.PLAYABLE],
        post_modes=POST_MODES,
        # Only the community room renders the meetup form, so only it needs
        # the catalogue. Building it unconditionally cost every other chat a
        # pile of queries for a form that was never on the page.
        catalogue=(activity_catalogue(presets_only=True)
                   if kind == COMMUNITY else []),
    )


# How long one waiting request holds before answering empty-handed, and how
# often it looks while it waits. The client re-issues immediately either way,
# so the ceiling only exists to keep proxies and browsers comfortable.
LIVE_WAIT = 25.0
LIVE_TICK = 0.2


def live_pane(target_id, game):
    """The game half of a live answer, or None for a chat that cannot have one."""
    busy = live_game_with(target_id) is not None
    if game is None:
        return {"token": "none", "html": "", "live": busy}

    seat = game.seat_of(current_user.id)
    return {
        "token": game_token(game),
        "live": busy,
        "html": render_template("_gamepane.html", game=game, seat=seat,
                                payload=game_payload(game, seat),
                                other_id=target_id),
    }


def notice_slice(user_id, seen, skip_url=None):
    """The notices half of a live answer: the badge, the panel and the toasts.

    Only built when something has actually changed, so the cost here is paid
    per event rather than per probe.

    `skip_url` is the conversation this tab has open. It is left out of the
    count and the toasts, so delivering a message to a chat on screen does not
    also badge it -- by exclusion, not by marking it read, because a held
    request outlives the tab that made it and a ghost must not be able to read
    a conversation on somebody's behalf. The tab's *next* request does the
    marking, at its start, and a ghost has no next request.

    The rows carry little forms that come back to the page they were used on,
    and the page is whatever the tab is showing -- not this request, which is
    /live. Hence `at`: the channel says where it is, and a value that is not
    plainly a local path is thrown away rather than trusted.
    """
    # The token covers everything, the count does not: see notices.token.
    token, _ = notices.state(user_id)
    unread = notices.unread_count(user_id, skip_url)
    at = request.args.get("at", "/")
    if not at.startswith("/") or at.startswith("//"):
        at = "/"
    return {
        "token": token,
        "count": unread,
        "seen": notices.watermark(user_id),
        "html": render_template("_noticelist.html",
                                notice_rows=notices.recent(user_id),
                                notice_back=at),
        "toasts": [{"id": n.id, "body": n.body}
                   for n in notices.since(user_id, seen, skip_url)],
    }


def seek_answer(user_id):
    """Whether a waiting player has been matched, and where their board is.

    Only asked for on the answering branch, which the notice written by
    start_random_game has already woken: there is nothing extra to probe for.
    """
    if seek_row(user_id) is not None:
        return {"waiting": True, "url": None}

    # No longer queued, so either they were matched or they cancelled. A live
    # game is the difference.
    game = db.session.scalar(
        db.select(Game)
        .where(db.or_(Game.player_a == user_id, Game.player_b == user_id),
               Game.status == "active")
        .order_by(Game.updated_at.desc(), Game.id.desc())
    )
    if game is None:
        return {"waiting": False, "url": None}

    kind, target_id = game_home(game, game.seat_of(user_id))
    return {"waiting": False,
            "url": url_for("chat", kind=kind, target_id=target_id)}


def live_reply(kind, target_id):
    """Hold the request until something changes, then answer at once.

    The delay in a chat was never the server -- a poll answered in about 25ms
    and the client then sat on its hands for up to four seconds. Waiting here
    instead makes delivery one round trip, and costs less: a cheap indexed
    probe every fifth of a second rather than a full request several times a
    second.

    One channel per tab, not one per thing being watched. Notices are always
    watched; a chat is watched as well when the tab has one open. That keeps
    one held thread per tab however much is going on in it, and -- far more
    importantly -- keeps a single copy of the rule below.

    The session is released between probes, and that is the part that matters.
    A Session holds its connection until commit, rollback or close, and the
    pool is five plus ten overflow -- so fifteen of these waiting with a
    session open would starve every other request on the site.
    """
    me = current_user.id
    seen = request.args.get("seen", type=int, default=0)
    bell = request.args.get("notices", "")
    # The lobby's waiting state asks for this. It doubles as the heartbeat that
    # keeps the student in the queue.
    seeking = request.args.get("seeking") == "1"

    watching = kind is not None
    parent = None
    if watching:
        parent, _ = chat_target(kind, target_id)
        if parent is None:
            return jsonify({"error": "unavailable"}), 403

    after = request.args.get("after", type=int, default=0)
    token = request.args.get("token", "")

    # Built from primitives, because the ORM object behind `parent` is
    # released along with the session between probes.
    where = None
    # Resolved once, here: the ORM object behind `parent` is released between
    # probes, so the loop below needs the id rather than the row.
    watch_other = chat_other_id(kind, parent) if watching else None
    if watching:
        if kind == COMMUNITY:
            where = Message.room == COMMUNITY
        else:
            where = MESSAGE_PARENTS[kind] == parent.id

        # Whatever is on screen is already read. Once, here, at the start of
        # the request -- the one moment the tab that asked for it demonstrably
        # still existed. Anything that arrives later is kept out of the badge
        # by exclusion instead, so an abandoned request cannot swallow it.
        if notices.clear_chat(me, kind, target_id):
            db.session.commit()

    if seeking:
        # Touched once, at the start of the request, the one moment the tab
        # that asked for it demonstrably still existed. A held request outlives
        # its tab, so a ghost refreshes this once and then ages out of the
        # queue rather than keeping a vanished player pairable for ever.
        mine = seek_row(me)
        if mine is not None:
            mine.seen_at = datetime.utcnow()
            db.session.commit()

    deadline = time.monotonic() + LIVE_WAIT
    while True:
        # One indexed aggregate. A plain max(id) would miss a notice that was
        # coalesced into an existing row, or one that was marked read.
        bell_now = notices.token(me)

        newest = after
        game = None
        fresh = token
        if watching:
            newest = db.session.scalar(
                db.select(db.func.max(Message.id)).where(where)
            ) or 0
            # pane_game rather than a hand-rolled probe: it owns the rules
            # about which game the panel shows, and it is where an abandoned
            # game gets settled. Reproducing that here would let the two drift.
            game = pane_game(watch_other) if watch_other else None
            fresh = game_token(game)

        if newest > after or fresh != token or bell_now != bell:
            rows = []
            if watching:
                parent, _ = chat_target(kind, target_id)
                rows = chat_rows(kind, parent, after=after)
            return jsonify({
                "after": max([r["id"] for r in rows], default=after),
                "messages": rows,
                "game": (live_pane(watch_other, game)
                         if watch_other else None),
                "notices": notice_slice(
                    me, seen,
                    skip_url=notices.chat_url(kind, target_id) if watching
                    else None),
                "seek": seek_answer(me) if seeking else None,
            })

        if time.monotonic() >= deadline:
            return jsonify({"after": after, "messages": [], "game": None,
                            "notices": None, "quiet": True})

        # Hand the connection back before sleeping. Without this the pool is
        # gone after fifteen simultaneous waits.
        db.session.remove()
        time.sleep(LIVE_TICK)


@app.route("/live")
@login_required
def live():
    """The app-wide channel. `here=match:12` adds a chat to what it watches."""
    here = request.args.get("here", "")
    if not here:
        return live_reply(None, None)

    kind, _, raw = here.partition(":")
    if kind == COMMUNITY:
        return live_reply(kind, 0)
    if kind not in MESSAGE_PARENTS or not raw.isdigit():
        return jsonify({"error": "unavailable"}), 403
    return live_reply(kind, int(raw))


@app.route("/chats/<kind>/<int:target_id>/live")
@login_required
def chat_live(kind, target_id):
    """The old per-chat address, kept so a page served before a deploy works."""
    if kind != COMMUNITY and kind not in MESSAGE_PARENTS:
        return jsonify({"error": "unavailable"}), 403
    return live_reply(kind, target_id)


def bell_back():
    """Back to the page the bell was opened on, if it is plainly a local path."""
    target = request.form.get("back", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for("index"))


@app.route("/notices/<int:notice_id>/open", methods=["POST"])
@login_required
def notice_open(notice_id):
    """Open what a notice is about, and mark it read on the way.

    A POST rather than a link so reading something is never a side effect of a
    GET. The enhancer swaps the redirect in place, so it still behaves like
    following a link.
    """
    notice = notices.mark_read(current_user.id, notice_id)
    if notice is None:
        flash("That notification is no longer there.")
        return bell_back()

    where = notice.url
    db.session.commit()
    if where.startswith("/") and not where.startswith("//"):
        return redirect(where)
    return redirect(url_for("index"))


@app.route("/notices/<int:notice_id>/read", methods=["POST"])
@login_required
def notice_read(notice_id):
    """Mark one notice read without going anywhere."""
    notices.mark_read(current_user.id, notice_id)
    db.session.commit()
    return bell_back()


@app.route("/notices/read", methods=["POST"])
@login_required
def notices_read_all():
    notices.mark_all_read(current_user.id)
    db.session.commit()
    return bell_back()


@app.route("/notices/clear", methods=["POST"])
@login_required
def notices_clear():
    """Throw away what has been read. Unread notices are left alone."""
    notices.clear_read(current_user.id)
    db.session.commit()
    return bell_back()


@app.route("/chats/match/<int:other_id>/game")
@login_required
def chat_game(other_id):
    """The game pane, for the poller.

    Returns the rendered pane rather than a state object, so _board.html stays
    the single source of the board markup -- rebuilding four games' boards in
    JavaScript would be the alternative.

    The token is what the client compares: it only re-swaps when that changes,
    which is what stops a poll wiping a half-made chess move.
    """
    parent, _ = chat_target("match", other_id)
    if parent is None:
        return jsonify({"error": "unavailable"}), 403

    game = pane_game(other_id)
    # Whether a game is in play at all, which is not the same question as
    # whether the panel has something to show: an invite is in play but lives
    # in the log. This is what decides if the launcher stays hidden.
    busy = live_game_with(other_id) is not None

    if game is None:
        return jsonify({"token": "none", "html": "", "live": busy})

    seat = game.seat_of(current_user.id)
    return jsonify({
        "token": game_token(game),
        "live": busy,
        "html": render_template("_gamepane.html", game=game, seat=seat,
                                payload=game_payload(game, seat),
                                other_id=other_id),
    })


@app.route("/chats/<kind>/<int:target_id>/messages")
@login_required
def chat_messages(kind, target_id):
    parent, _ = chat_target(kind, target_id)

    if parent is None:
        return jsonify({"error": "unavailable"}), 403

    after = request.args.get("after", type=int, default=0)
    return jsonify(chat_rows(kind, parent, after=after))


def game_for(game_id):
    """Resolve a game and check the student is one of its two players.

    Returns (game, seat) or (None, None). Every game route goes through this,
    including the JSON one.
    """
    game = db.session.get(Game, game_id)
    if game is None:
        return None, None

    seat = game.seat_of(current_user.id)
    if seat is None:
        return None, None

    return game, seat


def game_payload(game, seat):
    other_id = game.user_in_seat(1 - seat)
    other = db.session.get(User, other_id)
    return {
        "version": game.version,
        "status": game.status,
        "kind": game.kind,
        "label": rules.LABELS.get(game.kind, game.kind),
        "state": rules.view(game.kind, game.state, seat),
        "seat": seat,
        "your_turn": (
            game.status == "active"
            and rules.can_move(game.kind, game.state, seat)
        ),
        "opponent": other.name if other else "Opponent",
        "opponent_id": other_id,
        "outcome": game.outcome,
        "winner_id": game.winner_id,
        "you_won": game.winner_id == current_user.id,
    }


# A game in play. One at a time per pair, so "the game with them" is never
# ambiguous and the chat pane never has to choose.
LIVE_GAME = ("invited", "active")
OVER_GAME = ("finished", "declined")


def shared_with(other_id):
    """Clause matching games between me and someone, either orientation.

    player_a is the inviter rather than the lower id, so this cannot use
    pair() the way matches do.
    """
    return db.or_(
        db.and_(Game.player_a == current_user.id, Game.player_b == other_id),
        db.and_(Game.player_a == other_id, Game.player_b == current_user.id),
    )


def live_game_with(other_id):
    """The game in play with someone, or None."""
    return db.session.scalar(
        db.select(Game)
        .where(shared_with(other_id), Game.status.in_(LIVE_GAME))
        .order_by(Game.updated_at.desc(), Game.id.desc())
    )


def pane_game(other_id):
    """What the chat's game pane should show: the most recent game, or nothing.

    The *most recent*, and never anything older. This used to ask for "the
    newest finished game I have not dismissed", which meant closing the
    current result simply uncovered the one before it -- every result you had
    never dismissed was queued up behind it. Looking only at the latest game
    makes that impossible rather than tidying up after it.

    An invite is not shown here: it arrives as a message with its own buttons.
    It is also skipped when picking the latest game, so sending one does not
    blank a result the other player is still looking at.

    Also where an abandoned game gets settled, because this is the one place
    both the page render and the poller pass through.
    """
    # The most recent game that is not a pending invite. Invites are skipped
    # rather than shown-then-rejected, because an invite becoming the newest
    # game would otherwise hide the result somebody is still reading.
    #
    # Safe to skip them: only one game per pair can be invited or active at a
    # time, so an invite and a live board cannot coexist and this can never
    # step over a game in play.
    game = db.session.scalar(
        db.select(Game)
        .where(shared_with(other_id), Game.status != "invited")
        .order_by(Game.updated_at.desc(), Game.id.desc())
    )
    if game is None:
        return None

    # Safe to ask of the latest game: a live one is always the most recent,
    # because only one game per pair can be in play and starting it bumps
    # updated_at past every finished game.
    settle_abandoned(game)

    if game.status == "active":
        return game
    if game.status not in OVER_GAME:
        return None                      # nothing a panel can render
    if game.updated_at <= datetime.utcnow() - CLOSE_AFTER:
        return None                      # the result has had its ten minutes

    seat = game.seat_of(current_user.id)
    if seat is None or game.closed_for(seat):
        return None
    return game


# How long a finished game's result stays in the chat before the panel puts
# itself away, and how long a player has to act before the game is forfeited.
# The same ten minutes, so a whole game lifecycle can be watched in one go.
CLOSE_AFTER = timedelta(minutes=10)
ABANDON_AFTER = timedelta(minutes=10)


def owes_action(game):
    """Which seat the game is waiting on, or None if nobody can be blamed.

    Normally whoever's turn it is. Battleship's placement phase is the
    exception: both players act at once there, so turn_seat returns None and
    the ready flags are what identify a laggard. With neither side ready there
    is no single person at fault, and nothing should be forfeited.
    """
    if game.status != "active":
        return None

    if game.turn_id is not None:
        return game.seat_of(game.turn_id)

    state = game.state or {}
    ready = state.get("ready")
    if isinstance(ready, list) and len(ready) == 2 and ready.count(False) == 1:
        return ready.index(False)
    return None


def game_event(game, event, actor_id, body):
    """Record a moment in the game as a message in the players' chat.

    A real Message row, hanging off the match like any other, so it arrives
    through the message poller the two players already have running. The
    stored body is neutral prose -- chat_rows rewords it per viewer, which is
    the only place current_user is known.
    """
    lo, hi = pair(game.player_a, game.player_b)
    match = db.session.scalar(
        db.select(Match).filter_by(user_lo=lo, user_hi=hi)
    )
    if match is None:
        return
    db.session.add(Message(user_id=actor_id, match_id=match.id,
                           game_id=game.id, event=event, body=body))


def settle_abandoned(game):
    """Forfeit a game nobody has moved in. Returns True if it just happened.

    A write during a GET, which is deliberate: there is no scheduler, so the
    only moment this can be noticed is when somebody looks. Guarded on status
    and on there being exactly one player at fault, and it re-checks the
    status after the clock test, so two players polling at the same instant
    cannot both finish the game or write two messages.
    """
    if game is None or game.status != "active":
        return False
    if datetime.utcnow() - game.updated_at < ABANDON_AFTER:
        return False

    seat = owes_action(game)
    if seat is None:
        return False

    winner_id = game.user_in_seat(1 - seat)
    loser = db.session.get(User, game.user_in_seat(seat))
    winner = db.session.get(User, winner_id)
    label = rules.LABELS.get(game.kind, game.kind)

    finish_game(game, "abandoned", winner_id)
    game.updated_at = datetime.utcnow()
    game_event(game, "abandoned", winner_id,
               f"{loser.name if loser else 'They'} did not come back, so "
               f"{winner.name if winner else 'the other player'} won {label}.")
    db.session.commit()
    return True


def game_token(game):
    """What the poller compares to decide whether to swap the pane.

    Anything that changes what the pane should look like has to move this, and
    nothing else may: re-swapping identical markup would wipe a half-made
    chess move, which is a two-click interaction.
    """
    if game is None:
        return "none"
    return f"{game.id}:{game.version}:{game.status}"


# How long a waiting player stays pairable without their tab saying hello.
# The waiting page holds a live request that touches seen_at, so an open tab
# stays in the queue and a closed one falls out of it.
SEEK_TTL = timedelta(minutes=2)


def game_home(game, seat):
    """The conversation a game belongs to, as (kind, target_id).

    Friends play in the chat they already share -- the game pane is keyed on
    the two players, so putting it anywhere else would show the same game in
    two conversations. Two strangers paired by matchmaking have no such chat,
    so they get a pair conversation, the same kind the in-person queue makes.

    Falls back to the match chat, which is where every game lived before
    matchmaking existed, so an old game with a since-unfriended player behaves
    exactly as it did.
    """
    other_id = game.user_in_seat(1 - seat)

    # Active, not merely existing: matching.find_match would also answer for a
    # pending or declined request, and neither of those opens a chat. This is
    # the same test chat_target("match") makes.
    lo, hi = pair(current_user.id, other_id)
    friends = db.session.scalar(
        db.select(Match).filter_by(user_lo=lo, user_hi=hi, status="active"))
    if friends is not None:
        return "match", other_id

    pairing = db.session.scalar(
        db.select(GamePairing)
        .where(GamePairing.mode == "online",
               GamePairing.status == "open",
               db.or_(
                   db.and_(GamePairing.user_lo == current_user.id,
                           GamePairing.user_hi == other_id),
                   db.and_(GamePairing.user_lo == other_id,
                           GamePairing.user_hi == current_user.id),
               ))
        .order_by(GamePairing.created_at.desc())
    )
    if pairing is not None:
        return "pair", pairing.id

    return "match", other_id


def game_chat(game, seat):
    """Back to the conversation this game belongs to."""
    kind, target_id = game_home(game, seat)
    return redirect(url_for("chat", kind=kind, target_id=target_id))


def finish_game(game, outcome, winner_id=None):
    game.status = "finished"
    game.outcome = outcome
    game.winner_id = winner_id
    game.turn_id = None


@app.route("/games")
@login_required
def game_lobby():
    mine = db.session.scalars(
        db.select(Game)
        .where(db.or_(Game.player_a == current_user.id,
                      Game.player_b == current_user.id))
        .order_by(Game.updated_at.desc())
    ).all()

    rows = []
    for game in mine:
        seat = game.seat_of(current_user.id)
        other = db.session.get(User, game.user_in_seat(1 - seat))
        home_kind, home_id = game_home(game, seat)
        rows.append({
            "game": game,
            "label": rules.LABELS.get(game.kind, game.kind),
            "opponent": other.name if other else "Opponent",
            # The chat is where the board lives, so the lobby links there
            # directly rather than bouncing through game_view's redirect.
            # Asked of game_home rather than assumed to be a friend chat: a
            # game against a stranger lives in a pair conversation.
            "home_kind": home_kind,
            "home_id": home_id,
            "opponent_id": game.user_in_seat(1 - seat),
            "waiting_on_you": (
                (game.status == "invited" and game.player_b == current_user.id)
                or (game.status == "active"
                    and rules.can_move(game.kind, game.state, seat))
            ),
        })

    waiting = seek_row()
    return render_template(
        "games.html",
        rows=rows,
        seeking=waiting,
        seeking_label=(rules.LABELS.get(waiting.kind, waiting.kind)
                       if waiting else None),
        opponents=matching.active_matches(current_user.id),
        kinds=[(k, rules.LABELS[k]) for k in rules.PLAYABLE],
    )


def seek_row(user_id=None):
    """This student's place in the online queue, if they are in it."""
    return db.session.get(GameSeek, user_id or current_user.id)


def claim_partner(kind):
    """Take somebody out of the queue for `kind`, or return None.

    The claim is a delete whose rowcount decides it, exactly as find_queue
    does: if two players reach for the same partner at once, only one delete
    affects a row, and the loser falls through to waiting instead of both
    walking off with the same person.

    Stale rows are dropped as they are passed over -- a tab that has gone
    stops refreshing seen_at, and pairing somebody into a game they will never
    open is worse than making the next player wait a little longer.
    """
    fresh_since = datetime.utcnow() - SEEK_TTL

    for candidate in db.session.scalars(
        db.select(GameSeek)
        .where(GameSeek.kind == kind, GameSeek.user_id != current_user.id)
        .order_by(GameSeek.created_at)
    ):
        if candidate.seen_at < fresh_since:
            db.session.delete(candidate)
            continue

        # One game at a time per pair, which is the rule game_new enforces
        # with "Finish that one first". Matchmaking has to respect it or the
        # chat pane would have two games to choose between.
        if live_game_with(candidate.user_id) is not None:
            continue

        taken = db.session.execute(
            db.delete(GameSeek).where(GameSeek.user_id == candidate.user_id)
        ).rowcount
        if taken == 1:
            return candidate.user_id

    return None


def start_random_game(kind, partner_id):
    """Pair two players, and put the board where they can both reach it.

    Active rather than invited: both of them asked for a game, so there is
    nothing left to accept.
    """
    partner = db.session.get(User, partner_id)

    # Strangers have no conversation, so give them one -- the same kind the
    # in-person queue makes. Friends already have theirs, and the pane is keyed
    # on the two players, so a second chat would show the game twice.
    lo, hi = pair(current_user.id, partner_id)
    friends = db.session.scalar(
        db.select(Match).filter_by(user_lo=lo, user_hi=hi, status="active"))

    pairing = None
    if friends is None:
        pairing = GamePairing(user_lo=lo, user_hi=hi, game=kind, mode="online")
        db.session.add(pairing)
        db.session.flush()

    game = Game(
        kind=kind,
        status="active",
        player_a=partner_id,          # the one who was already waiting
        player_b=current_user.id,
        state=rules.new_state(kind),
        version=0,
    )
    nxt = rules.turn_seat(kind, game.state)
    game.turn_id = None if nxt is None else game.user_in_seat(nxt)
    db.session.add(game)
    db.session.flush()

    label = rules.LABELS.get(kind, kind)
    parent = pairing if pairing is not None else friends
    where = ("pair", pairing.id) if pairing is not None else ("match", partner_id)

    # A plain message rather than a game_event: GAME_EVENTS is a CHECK
    # constraint, and widening it for one opening line would be a migration
    # for a sentence.
    opening = Message(user_id=current_user.id,
                      body=f"Matched for {label}. Good luck.")
    attach_parent(opening, where[0], parent)
    db.session.add(opening)

    # Only the one who was already waiting is told; whoever arrived second is
    # being sent straight to the board. A match chat is addressed by the *other*
    # person, so their link names whoever just joined rather than themselves.
    their_url = notices.chat_url(
        "match", current_user.id) if where[0] == "match" else notices.chat_url(
        "pair", pairing.id)
    notices.paired(partner_id, current_user, their_url, label)
    db.session.commit()
    return game, where, partner


@app.route("/games/seek", methods=["POST"])
@login_required
def game_seek():
    """Join the queue for a game, and pair at once if somebody is waiting."""
    kind = request.form.get("kind", "")

    if kind not in rules.PLAYABLE:
        flash("Pick a game to play.")
        return redirect(url_for("game_lobby"))

    partner_id = claim_partner(kind)

    if partner_id is not None:
        db.session.execute(
            db.delete(GameSeek).where(GameSeek.user_id == current_user.id))
        game, where, partner = start_random_game(kind, partner_id)
        flash(f"Matched with {partner.name if partner else 'another student'}. "
              "Your move is in the chat.")
        return redirect(url_for("chat", kind=where[0], target_id=where[1]))

    # Nobody about: wait. Re-picking a different game replaces the slot rather
    # than adding a second, because the table holds one row per student.
    mine = seek_row()
    if mine is None:
        db.session.add(GameSeek(user_id=current_user.id, kind=kind))
    else:
        mine.kind = kind
        mine.created_at = datetime.utcnow()
        mine.seen_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("game_lobby"))


@app.route("/games/seek/cancel", methods=["POST"])
@login_required
def game_seek_cancel():
    db.session.execute(
        db.delete(GameSeek).where(GameSeek.user_id == current_user.id))
    db.session.commit()
    return redirect(url_for("game_lobby"))


@app.route("/games/new", methods=["POST"])
@login_required
def game_new():
    kind = request.form.get("kind", "")
    opponent_id = request.form.get("opponent_id", type=int)

    if kind not in rules.PLAYABLE:
        flash("Pick a game to play.")
        return redirect(url_for("game_lobby"))

    # You can only challenge someone on your friends list.
    lo, hi = pair(current_user.id, opponent_id or 0)
    match = db.session.scalar(
        db.select(Match).filter_by(user_lo=lo, user_hi=hi, status="active")
    )
    if match is None:
        flash("You can only start a game with one of your friends.")
        return redirect(url_for("game_lobby"))

    # One at a time, so the chat pane always has a single game to show.
    busy = live_game_with(opponent_id)
    if busy is not None:
        flash(f"You already have {rules.LABELS[busy.kind]} going with them. "
              "Finish that one first.")
        return redirect(url_for("chat", kind="match", target_id=opponent_id))

    game = Game(
        kind=kind,
        status="invited",
        player_a=current_user.id,
        player_b=opponent_id,
        turn_id=current_user.id,
        state=rules.new_state(kind),
        version=0,
    )
    db.session.add(game)
    db.session.flush()

    other = db.session.get(User, opponent_id)
    game_event(game, "invited", current_user.id,
               f"{current_user.name} invited {other.name if other else 'them'} "
               f"to {rules.LABELS[kind]}.")
    notices.game_invite(opponent_id, current_user, game, rules.LABELS[kind])
    db.session.commit()

    # No flash: the chat now carries this, and a flash would sit on screen
    # until the next full page swap -- the panel poller only replaces the
    # panel, so it has no way to clear one.
    # Back to the chat, not off to a separate board: the enhancer swaps this
    # response in place, so the inviter sees their own invite appear without
    # leaving the conversation.
    return redirect(url_for("chat", kind="match", target_id=opponent_id))


@app.route("/games/<int:game_id>")
@login_required
def game_view(game_id):
    """Kept so old links and the lobby still work; the chat is where a game
    lives now, so this just points there."""
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    return game_chat(game, seat)


@app.route("/games/<int:game_id>/state")
@login_required
def game_state(game_id):
    game, seat = game_for(game_id)

    if game is None:
        return jsonify({"error": "unavailable"}), 403

    after = request.args.get("after", type=int, default=-1)
    if after == game.version and game.status == "active":
        return jsonify({"version": game.version, "unchanged": True})

    return jsonify(game_payload(game, seat))


@app.route("/games/<int:game_id>/move", methods=["POST"])
@login_required
def game_move(game_id):
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    if game.status != "active":
        flash("That game isn't in play.")
    elif not rules.can_move(game.kind, game.state, seat):
        flash("It isn't your turn.")
    else:
        state, error = rules.apply_move(
            game.kind, game.state, seat, request.form.get("move")
        )
        if error:
            flash(error)
        else:
            # Reassign, never mutate: db.JSON does not track in-place edits.
            game.state = state
            game.version += 1
            game.updated_at = datetime.utcnow()

            outcome = rules.result(game.kind, state)
            label = rules.LABELS.get(game.kind, game.kind)
            if outcome is None:
                nxt = rules.turn_seat(game.kind, state)
                game.turn_id = None if nxt is None else game.user_in_seat(nxt)
            elif outcome[0] == "win":
                winner_id = game.user_in_seat(outcome[1])
                winner = db.session.get(User, winner_id)
                finish_game(game, "win", winner_id)
                game_event(game, "ended", current_user.id,
                           f"{winner.name if winner else 'Someone'} "
                           f"won {label}.")
            else:
                finish_game(game, "draw")
                game_event(game, "ended", current_user.id,
                           f"{label} ended in a draw.")

            db.session.commit()

    return game_chat(game, seat)


@app.route("/games/<int:game_id>/<any(accept, decline, resign):action>",
           methods=["POST"])
@login_required
def game_action(game_id, action):
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    if action in ("accept", "decline"):
        # Answered either way, so the notice that offered the game stops
        # offering it -- including when the answer came from the chat instead.
        notices.close(current_user.id, request.form.get("notice"))
        db.session.commit()

        if game.status != "invited":
            flash("That invite has already been answered.")
        elif game.player_b != current_user.id:
            flash("Only the person invited can answer.")
        elif action == "accept":
            game.status = "active"
            nxt = rules.turn_seat(game.kind, game.state)
            game.turn_id = None if nxt is None else game.user_in_seat(nxt)
            game.updated_at = datetime.utcnow()
            label = rules.LABELS.get(game.kind, game.kind)
            game_event(game, "accepted", current_user.id,
                       f"{current_user.name} accepted the {label} invite.")
            notices.game_answer(game.player_a, current_user, game, label,
                                "accept")
            db.session.commit()
        else:
            game.status = "declined"
            game.turn_id = None
            game.updated_at = datetime.utcnow()
            label = rules.LABELS.get(game.kind, game.kind)
            game_event(game, "declined", current_user.id,
                       f"{current_user.name} declined the {label} invite.")
            notices.game_answer(game.player_a, current_user, game, label,
                                "decline")
            db.session.commit()

    elif action == "resign":
        if game.status != "active":
            flash("That game isn't in play.")
        else:
            winner_id = game.user_in_seat(1 - seat)
            winner = db.session.get(User, winner_id)
            label = rules.LABELS.get(game.kind, game.kind)
            finish_game(game, "resigned", winner_id)
            game.updated_at = datetime.utcnow()
            game_event(game, "ended", current_user.id,
                       f"{current_user.name} resigned, so "
                       f"{winner.name if winner else 'the other player'} "
                       f"won {label}.")
            db.session.commit()

    return game_chat(game, seat)


@app.route("/games/<int:game_id>/close", methods=["POST"])
@login_required
def game_close(game_id):
    """Put the chat back to full width once a game is over.

    Per seat: dismissing your own pane must not take the result off the other
    player's screen before they have looked at it.
    """
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    if game.status in LIVE_GAME:
        flash("That game is still in play.")
    else:
        game.close_for(seat)
        db.session.commit()

    return game_chat(game, seat)


POST_NOTE_MAX = 300


@app.route("/find")
@login_required
def find_game():
    waiting = db.session.get(GameQueue, current_user.id)

    posts = db.session.scalars(
        db.select(GamePost)
        .where(GamePost.mode == "inperson", GamePost.status == "open")
        .order_by(GamePost.created_at.desc())
    ).all()

    pairings = []
    for pairing in db.session.scalars(
        db.select(GamePairing)
        .where(db.or_(GamePairing.user_lo == current_user.id,
                      GamePairing.user_hi == current_user.id),
               GamePairing.status == "open",
               # /find is the in-person page. An online pairing is a board in
               # a chat, not somebody to go and meet.
               GamePairing.mode == "inperson")
        .order_by(GamePairing.created_at.desc())
    ):
        other_id = (pairing.user_hi if pairing.user_lo == current_user.id
                    else pairing.user_lo)
        other = db.session.get(User, other_id)
        pairings.append({
            "id": pairing.id,
            "game": pairing.game,
            "other": other.name if other else "Partner",
        })

    # How many others are waiting per game, so the page is honest about odds.
    counts = dict(db.session.execute(
        db.select(GameQueue.game, db.func.count())
        .where(GameQueue.user_id != current_user.id)
        .group_by(GameQueue.game)
    ).all())

    return render_template(
        "find.html",
        # Both lists are the focused set. "Get paired" gets no write-in:
        # pairing matches two people on a string, so one typing "basketball"
        # and another "Basketball" would wait forever without ever meeting.
        games=[a["name"] for a in activity_catalogue(in_person_only=True,
                                                     presets_only=True)],
        catalogue=activity_catalogue(presets_only=True),
        waiting=waiting,
        counts=counts,
        posts=[post_payload(p) for p in posts],
        pairings=pairings,
    )


@app.route("/find/queue", methods=["POST"])
@login_required
def find_queue():
    game = request.form.get("game", "")

    activity = find_activity(game)
    if activity is None or not activity.in_person:
        flash("Pick an activity from the list.")
        return redirect(url_for("find_game"))

    partner = db.session.scalars(
        db.select(GameQueue)
        .where(GameQueue.game == game, GameQueue.user_id != current_user.id)
        .order_by(GameQueue.created_at)
    ).first()

    if partner is not None:
        # Claim them by deleting their row: if another request got there first
        # the delete affects nothing and we fall through to waiting instead.
        claimed = db.session.execute(
            db.delete(GameQueue).where(GameQueue.user_id == partner.user_id)
        ).rowcount

        if claimed == 1:
            db.session.execute(
                db.delete(GameQueue).where(GameQueue.user_id == current_user.id)
            )
            lo, hi = pair(current_user.id, partner.user_id)
            pairing = GamePairing(user_lo=lo, user_hi=hi, game=game)
            db.session.add(pairing)
            db.session.flush()
            # Only the person who was already waiting gets told. Whoever
            # arrived second is being redirected into the chat a line below, so
            # a notice would be about the page they are looking at.
            notices.paired(partner.user_id, current_user,
                           notices.chat_url("pair", pairing.id), game)
            db.session.commit()
            flash(f"Paired for {game}. Say hello.")
            return redirect(url_for("chat", kind="pair", target_id=pairing.id))

    existing = db.session.get(GameQueue, current_user.id)
    if existing is None:
        db.session.add(GameQueue(user_id=current_user.id, game=game))
    else:
        existing.game = game
        existing.created_at = datetime.utcnow()
    db.session.commit()
    flash(f"Waiting for someone who wants {game}.")
    return redirect(url_for("find_game"))


@app.route("/find/leave", methods=["POST"])
@login_required
def find_leave():
    db.session.execute(
        db.delete(GameQueue).where(GameQueue.user_id == current_user.id)
    )
    db.session.commit()
    flash("You left the queue.")
    return redirect(url_for("find_game"))


@app.route("/pairings/<int:pairing_id>/close", methods=["POST"])
@login_required
def pairing_close(pairing_id):
    pairing = db.session.get(GamePairing, pairing_id)

    if pairing is None or current_user.id not in (pairing.user_lo, pairing.user_hi):
        flash("That isn't your pairing.")
    else:
        pairing.status = "closed"
        db.session.commit()
        flash("Pairing closed.")

    return redirect(url_for("find_game"))


@app.route("/posts/new", methods=["POST"])
@login_required
def post_new():
    game = request.form.get("game", "").strip()
    mode = request.form.get("mode", "online")
    note = request.form.get("note", "").strip()
    location = request.form.get("location", "").strip()
    when_text = request.form.get("when_text", "").strip()
    players = request.form.get("max_players", type=int) or 2

    # A write-in: the activity and the place both come from the student, so
    # neither can be checked against the catalogue. It is in person by
    # definition -- they are telling us where to turn up -- which is why the
    # mode is taken rather than trusted, and the location select ignored.
    wrote_in = game == OTHER
    if wrote_in:
        game = request.form.get("custom_game", "").strip()
        location = request.form.get("custom_location", "").strip()
        mode = "inperson"

    activity = None if wrote_in else find_activity(game)
    allowed = [] if activity is None else [loc.name for loc in activity.locations]

    if not game:
        flash("Say which activity you're looking for."
              if not wrote_in else "Type what you want to play.")
    elif wrote_in and len(game) > CUSTOM_GAME_MAX:
        flash(f"Keep the activity under {CUSTOM_GAME_MAX} characters.")
    elif wrote_in and not location:
        flash("Say where you'll be meeting up.")
    elif wrote_in and len(location) > CUSTOM_LOCATION_MAX:
        flash(f"Keep the location under {CUSTOM_LOCATION_MAX} characters.")
    elif not wrote_in and activity is None:
        flash("Pick an activity from the list.")
    elif mode not in POST_MODES:
        flash("Pick online or in person.")
    elif not wrote_in and mode == "online" and not activity.online:
        flash(f"{activity.name} can't be played online.")
    elif not wrote_in and mode == "inperson" and not activity.in_person:
        flash(f"{activity.name} is online only.")
    elif not wrote_in and mode == "inperson" and not location:
        flash("Pick where you'll be playing.")
    elif not wrote_in and mode == "inperson" and location not in allowed:
        flash(f"You can't play {activity.name} at that spot.")
    elif len(note) > POST_NOTE_MAX:
        flash(f"Keep the note under {POST_NOTE_MAX} characters.")
    elif not 2 <= players <= 50:
        flash("Pick between 2 and 50 players.")
    else:
        post = GamePost(
            author_id=current_user.id,
            game=game[:120],
            mode=mode,
            note=note or None,
            location=(location if mode == "inperson" else None),
            when_text=when_text[:120] or None,
            max_players=players,
        )
        db.session.add(post)
        db.session.flush()

        # The post lives in the room as a message, so one timeline and one
        # poller cover both chatter and posts.
        summary = f"Looking for {post.game}"
        if post.mode == "inperson":
            summary += " in person"
        db.session.add(Message(
            user_id=current_user.id, room=COMMUNITY, post_id=post.id, body=summary
        ))
        # The author is the first one going.
        db.session.add(GamePostRsvp(post_id=post.id, user_id=current_user.id))
        db.session.commit()
        flash("Posted to Community Chat.")

    if request.form.get("back") == "find":
        return redirect(url_for("find_game"))
    return redirect(url_for("chat", kind=COMMUNITY, target_id=0))


@app.route("/posts/<int:post_id>/rsvp", methods=["POST"])
@login_required
def post_rsvp(post_id):
    post = db.session.get(GamePost, post_id)

    if post is None:
        flash("That post is gone.")
    elif post.status != "open":
        flash("That post is closed.")
    else:
        rsvp = db.session.get(GamePostRsvp, (post_id, current_user.id))
        if rsvp is not None:
            db.session.delete(rsvp)
            db.session.commit()
            flash(f"You're no longer down for {post.game}.")
        else:
            going = db.session.scalar(
                db.select(db.func.count()).select_from(GamePostRsvp)
                .where(GamePostRsvp.post_id == post_id)
            )
            if going >= post.max_players:
                flash("That one is full.")
            else:
                db.session.add(
                    GamePostRsvp(post_id=post_id, user_id=current_user.id)
                )
                db.session.commit()
                flash(f"You're in for {post.game}.")

    if request.form.get("back") == "find":
        return redirect(url_for("find_game"))
    return redirect(url_for("chat", kind=COMMUNITY, target_id=0))


@app.route("/posts/<int:post_id>/close", methods=["POST"])
@login_required
def post_close(post_id):
    post = db.session.get(GamePost, post_id)

    if post is None or post.author_id != current_user.id:
        flash("That isn't your post.")
    else:
        post.status = "closed"
        db.session.commit()
        flash(f"{post.game} cancelled.")

    if request.form.get("back") == "find":
        return redirect(url_for("find_game"))
    return redirect(url_for("chat", kind=COMMUNITY, target_id=0))


@app.route("/clubs")
@login_required
def clubs():
    joined = joined_clubs(current_user.id)
    return render_template(
        "clubs.html",
        clubs=db.session.scalars(db.select(Club).order_by(Club.name)).all(),
        joined_ids={club.id for club in joined},
    )


@app.route("/clubs/<int:club_id>/join", methods=["POST"])
@login_required
def join_club(club_id):
    club = db.session.get(Club, club_id)

    if club is None:
        flash("That club no longer exists.")
    elif db.session.get(ClubMember, (current_user.id, club_id)):
        flash(f"You are already in {club.name}.")
    else:
        db.session.add(ClubMember(user_id=current_user.id, club_id=club_id))
        db.session.commit()
        flash(f"You joined {club.name}.")

    return back_to()


@app.route("/clubs/<int:club_id>/leave", methods=["POST"])
@login_required
def leave_club(club_id):
    membership = db.session.get(ClubMember, (current_user.id, club_id))

    if membership is None:
        flash("You are not in that club.")
    else:
        club = db.session.get(Club, club_id)
        db.session.delete(membership)
        db.session.commit()
        flash(f"You left {club.name}.")

    return back_to()


# ---------------------------------------------------------------- group chats
# A chat a student made. Membership is the only key: a join code buys you a
# ChatMember row and nothing else, so every route below re-checks membership
# or ownership rather than trusting how you got here.

CODE_TRIES = 8


def is_member(chat_id, user_id=None):
    user_id = current_user.id if user_id is None else user_id
    return db.session.get(ChatMember, (user_id, chat_id)) is not None


def chat_members(chat_id):
    """Everyone in a chat, by name."""
    return db.session.scalars(
        db.select(User)
        .join(ChatMember, ChatMember.user_id == User.id)
        .where(ChatMember.chat_id == chat_id)
        .order_by(User.name)
    ).all()


@app.route("/chats")
@login_required
def chats():
    """The hub: make one, join one by code, or browse the public ones."""
    mine = joined_chats(current_user.id)
    mine_ids = {c.id for c in mine}

    public = db.session.scalars(
        db.select(Chat).where(Chat.is_public.is_(True)).order_by(Chat.name)
    ).all()

    counts = dict(db.session.execute(
        db.select(ChatMember.chat_id, db.func.count())
        .group_by(ChatMember.chat_id)
    ).all())

    return render_template(
        "chats.html",
        mine=[{"chat": c, "members": counts.get(c.id, 0),
               "owner": c.owner_id == current_user.id} for c in mine],
        public=[{"chat": c, "members": counts.get(c.id, 0),
                 "joined": c.id in mine_ids} for c in public],
    )


@app.route("/chats/new", methods=["POST"])
@login_required
def chat_new():
    name = request.form.get("name", "").strip()
    is_public = request.form.get("visibility") == "public"

    if not name:
        flash("Give the chat a name.")
        return redirect(url_for("chats"))
    if len(name) > 80:
        flash("Chat names are limited to 80 characters.")
        return redirect(url_for("chats"))

    # Insert and retry rather than checking first: two people creating a chat
    # in the same moment could both pass a pre-check and then collide.
    for _ in range(CODE_TRIES):
        chat = Chat(name=name, code=new_code(), is_public=is_public,
                    owner_id=current_user.id)
        db.session.add(chat)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            continue
        db.session.add(ChatMember(user_id=current_user.id, chat_id=chat.id))
        db.session.commit()
        flash(f"{chat.name} is ready. Share the code {chat.code} to let people in.")
        return redirect(url_for("chat", kind="group", target_id=chat.id))

    flash("Could not generate a join code. Please try again.")
    return redirect(url_for("chats"))


@app.route("/chats/join", methods=["POST"])
@login_required
def chat_join():
    # Codes get typed off a screenshot or read down a phone, so case and
    # stray spaces are not the student's problem to get right.
    code = request.form.get("code", "").strip().upper()

    if not code:
        flash("Enter a join code.")
        return redirect(url_for("chats"))

    chat = db.session.scalar(db.select(Chat).filter_by(code=code))
    if chat is None:
        flash("No chat has that code.")
    elif is_member(chat.id):
        flash(f"You are already in {chat.name}.")
        return redirect(url_for("chat", kind="group", target_id=chat.id))
    else:
        db.session.add(ChatMember(user_id=current_user.id, chat_id=chat.id))
        # An outstanding invite to a chat you joined by code anyway is spent.
        invite = db.session.get(ChatInvite, (chat.id, current_user.id))
        if invite is not None and invite.status == "invited":
            invite.status = "accepted"
        db.session.commit()
        flash(f"You joined {chat.name}.")
        return redirect(url_for("chat", kind="group", target_id=chat.id))

    return redirect(url_for("chats"))


@app.route("/chats/<int:chat_id>/invite", methods=["POST"])
@login_required
def chat_invite(chat_id):
    """Any member may invite. Only the invitee can turn it into membership."""
    chat = db.session.get(Chat, chat_id)
    other_id = request.form.get("other_id", "")
    other = db.session.get(User, int(other_id)) if other_id.isdigit() else None

    if chat is None or not is_member(chat_id):
        flash("That chat is not yours to invite to.")
    elif other is None:
        flash("We couldn't find that student.")
    elif other.id == current_user.id:
        flash("You are already in it.")
    elif is_member(chat_id, other.id):
        flash(f"{other.name} is already in {chat.name}.")
    else:
        invite = db.session.get(ChatInvite, (chat_id, other.id))
        if invite is not None and invite.status == "invited":
            flash(f"{other.name} already has an invite to {chat.name}.")
        else:
            if invite is None:
                db.session.add(ChatInvite(chat_id=chat_id, user_id=other.id,
                                          invited_by=current_user.id,
                                          status="invited"))
            else:
                # A declined invite is not final, unlike a declined friend
                # request: people change their minds about a group.
                invite.status = "invited"
                invite.invited_by = current_user.id
            notices.chat_invite(other.id, current_user, chat)
            db.session.commit()
            flash(f"Invited {other.name} to {chat.name}.")

    return redirect(url_for("chat", kind="group", target_id=chat_id))


@app.route("/chats/<int:chat_id>/invite/<action>", methods=["POST"])
@login_required
def chat_invite_respond(chat_id, action):
    invite = db.session.get(ChatInvite, (chat_id, current_user.id))
    chat = db.session.get(Chat, chat_id)

    # Up here rather than per branch: however this request turns out, the
    # notice that asked the question is no longer asking it.
    notices.close(current_user.id, request.form.get("notice"))
    db.session.commit()

    if invite is None or invite.status != "invited" or chat is None:
        flash("That invite is no longer waiting.")
    elif action == "accept":
        invite.status = "accepted"
        if not is_member(chat_id):
            db.session.add(ChatMember(user_id=current_user.id, chat_id=chat_id))
        db.session.commit()
        flash(f"You joined {chat.name}.")
        return dock_back() or redirect(
            url_for("chat", kind="group", target_id=chat_id))
    elif action == "decline":
        invite.status = "declined"
        db.session.commit()
        flash(f"You turned down {chat.name}.")
    else:
        flash("Unknown action.")

    return dock_back() or redirect(url_for("chats"))


@app.route("/chats/<int:chat_id>/leave", methods=["POST"])
@login_required
def chat_leave(chat_id):
    """Leaving, including the owner handing the chat on.

    One route for every case, so there is a single place that decides what
    leaving means. An owner used to be refused outright, which left whoever
    made a chat stuck in it unless they deleted it for everyone.
    """
    chat = db.session.get(Chat, chat_id)
    membership = db.session.get(ChatMember, (current_user.id, chat_id))

    if chat is None or membership is None:
        flash("You are not in that chat.")
        return redirect(url_for("chats"))

    if chat.owner_id != current_user.id:
        db.session.delete(membership)
        db.session.commit()
        flash(f"You left {chat.name}.")
        return redirect(url_for("chats"))

    # Everyone else still in it. Also the whitelist the successor is checked
    # against, so a posted id cannot name a non-member or the owner.
    others = [m for m in chat_members(chat_id) if m.id != current_user.id]

    if not others:
        # Nobody to hand it to, and an empty chat is just clutter.
        name = chat.name
        db.session.delete(chat)
        db.session.commit()
        flash(f"You left and closed {name}.")
        return redirect(url_for("chats"))

    raw = request.form.get("successor", "")
    successor = next((m for m in others if str(m.id) == raw), None)

    if successor is None:
        flash(f"Choose who should take over {chat.name} before you leave.")
        return redirect(url_for("chat", kind="group", target_id=chat_id))

    chat.owner_id = successor.id
    db.session.delete(membership)
    db.session.commit()
    flash(f"{successor.name} now manages {chat.name}. You have left.")
    return redirect(url_for("chats"))


@app.route("/chats/<int:chat_id>/remove/<int:user_id>", methods=["POST"])
@login_required
def chat_remove(chat_id, user_id):
    chat = db.session.get(Chat, chat_id)

    if chat is None or chat.owner_id != current_user.id:
        flash("Only the person who made the chat can remove people.")
    elif user_id == current_user.id:
        flash("You cannot remove yourself. Delete the chat instead.")
    else:
        membership = db.session.get(ChatMember, (user_id, chat_id))
        other = db.session.get(User, user_id)
        if membership is None:
            flash("They are not in that chat.")
        else:
            db.session.delete(membership)
            db.session.commit()
            flash(f"Removed {other.name if other else 'that student'} "
                  f"from {chat.name}.")

    return redirect(url_for("chat", kind="group", target_id=chat_id))


@app.route("/chats/<int:chat_id>/delete", methods=["POST"])
@login_required
def chat_delete(chat_id):
    chat = db.session.get(Chat, chat_id)

    if chat is None or chat.owner_id != current_user.id:
        flash("Only the person who made the chat can delete it.")
        return redirect(url_for("chats"))

    name = chat.name
    # Messages, memberships and invites go with it: each of those tables
    # declares ON DELETE CASCADE against chats.id.
    db.session.delete(chat)
    db.session.commit()
    flash(f"Deleted {name}.")
    return redirect(url_for("chats"))


@app.route("/matches/<int:other_id>/unmatch", methods=["POST"])
@login_required
def unmatch(other_id):
    other = db.session.get(User, other_id)
    name = other.name if other else "That student"

    if matching.end_match(current_user.id, other_id):
        flash(f"Removed {name} from your friends.")
    else:
        flash(f"{name} is not on your friends list.")

    return redirect(url_for("matches"))


@app.route("/people")
@login_required
def people():
    term = request.args.get("q", "").strip()
    searched = bool(term)

    if searched and len(term) < matching.SEARCH_MIN:
        flash(f"Search for at least {matching.SEARCH_MIN} characters.")
        searched = False

    return render_template(
        "people.html",
        term=term,
        searched=searched,
        results=matching.search_people(current_user.id, term) if searched else [],
        sent=matching.outgoing_requests(current_user.id),
    )


def dock_row(person):
    """One search hit, with the URL and label its state calls for.

    Built here rather than in the browser so the dock's script never has to
    assemble a path, and so the choice of action stays in one place.
    """
    state = person["state"]
    row = {"user_id": person["user_id"], "name": person["name"],
           "pronouns": person["pronouns"], "state": state}

    if state == "matched":
        row["link"] = url_for("chat", kind="match", target_id=person["user_id"])
        row["label"] = "Chat"
    elif state == "they_asked":
        row["post"] = url_for("match_respond", other_id=person["user_id"],
                              action="accept")
        row["label"] = "Accept"
    elif state in ("none", "ended"):
        row["post"] = url_for("match_ask", other_id=person["user_id"])
        row["label"] = "Add"
    else:
        # you_asked / declined: nothing to do from here.
        row["label"] = "Pending" if state == "you_asked" else "Declined"

    return row


@app.route("/dock/search")
@login_required
def dock_search():
    """The dock's typeahead. The dock works without this: its form is a plain
    GET that the context processor answers on whichever page it lands on."""
    term = request.args.get(DOCK_PARAM, "").strip()
    hits = (matching.search_names(current_user.id, term, limit=DOCK_RESULTS)
            if term else [])
    return jsonify({
        "term": term,
        "short": bool(term) and len(term) < matching.SEARCH_MIN,
        "results": [dock_row(p) for p in hits],
        "all": url_for("people", q=term) if term else None,
    })


ASK_MESSAGES = {
    "asked": "Friend request sent to {name}.",
    "matched": "You and {name} are friends now — they had already asked you.",
    "already": "You've already sent {name} a friend request.",
    "already_matched": "You're already friends with {name}.",
    "declined": "{name} turned down a friend request. Leave it there.",
    "self": "You can't send yourself a friend request.",
    "unknown": "We couldn't find that student.",
}


@app.route("/people/<int:other_id>/ask", methods=["POST"])
@login_required
def match_ask(other_id):
    other = db.session.get(User, other_id)
    outcome = matching.request_match(current_user.id, other_id)
    flash(ASK_MESSAGES[outcome].format(name=other.name if other else "that student"))

    # "matched" means they had already asked, so this ask answered theirs.
    if outcome == "asked":
        notices.friend_request(other_id, current_user)
        db.session.commit()
    elif outcome == "matched":
        notices.friend_accepted(other_id, current_user)
        db.session.commit()

    # Checked first: an action taken in the dock returns to the page it was
    # taken on. Everything below keeps the destination it always had.
    if (back := dock_back()) is not None:
        return back
    if request.form.get("back") == "home":
        return redirect(url_for("index"))
    if outcome in ("asked", "matched"):
        return redirect(url_for("matches"))
    return back_to_people()


@app.route("/matches/<int:other_id>/<any(accept, decline, cancel):action>",
           methods=["POST"])
@login_required
def match_respond(other_id, action):
    other = db.session.get(User, other_id)
    name = other.name if other else "that student"

    if matching.respond_to_request(current_user.id, other_id, action):
        flash({
            "accept": f"You and {name} are friends now.",
            "decline": f"Turned down {name}'s friend request.",
            "cancel": f"Took back your friend request to {name}.",
        }[action])
        if action == "accept":
            notices.friend_accepted(other_id, current_user)
    else:
        flash("That friend request is no longer waiting on you.")

    # Answered, so the notice that asked has nothing left to ask. Harmless when
    # the answer came from the matches page and there is no notice id to close.
    notices.close(current_user.id, request.form.get("notice"))
    db.session.commit()

    if (back := dock_back()) is not None:
        return back
    if action == "cancel":
        return back_to_people()
    return redirect(url_for("matches"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        gender = request.form.get("gender", "")
        # Not required, unlike gender. One of the four options is "prefer not
        # to say", so insisting on a choice only insists on clicking the
        # opt-out -- and anything unrecognised means the same thing. It can be
        # changed later on the questionnaire page.
        pronouns = request.form.get("pronouns", "")
        if pronouns not in PRONOUNS:
            pronouns = "unspecified"

        if not name:
            flash("Please enter your name.")
        elif gender not in GENDERS:
            flash("Please choose an option for gender.")
        elif not email or not password:
            flash("Email and password are required.")
        elif not email.endswith("@umb.edu"):
            flash("You must register with a @umb.edu email address.")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif password != request.form["confirm_password"]:
            flash("Passwords do not match.")
        elif db.session.scalar(db.select(User).filter_by(email=email)):
            flash("That email is already registered.")
        else:
            user = User(
                name=name,
                gender=gender,
                pronouns=pronouns,
                email=email,
                password_hash=generate_password_hash(password),
            )
            db.session.add(user)
            db.session.commit()
            issue_verification_code(user)
            session["pending_user_id"] = user.id
            return redirect(url_for("verify"))

    # Named pronoun_options, not pronouns: the local above holds the submitted
    # value, and shadowing it here would send the wrong thing to the form.
    return render_template("register.html", genders=GENDERS,
                           pronoun_options=PRONOUNS)


@app.route("/verify", methods=["GET", "POST"])
def verify():
    user = db.session.get(User, session.get("pending_user_id", 0))

    if user is None:
        return redirect(url_for("register"))

    if user.email_verified:
        session.pop("pending_user_id", None)
        return redirect(url_for("login"))

    if request.method == "POST":
        expires_at = user.verification_expires_at.replace(tzinfo=timezone.utc)

        if user.verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
            flash("Too many incorrect attempts. Request a new code.")
        elif datetime.now(timezone.utc) > expires_at:
            flash("That code has expired. Request a new one.")
        elif check_password_hash(
            user.verification_code_hash, request.form["code"].strip()
        ):
            user.email_verified = True
            user.verification_code_hash = None
            user.verification_expires_at = None
            user.verification_attempts = 0
            db.session.commit()
            session.pop("pending_user_id", None)
            login_user(user)
            return redirect(url_for("index"))
        else:
            user.verification_attempts += 1
            db.session.commit()
            flash("Incorrect code.")

    return render_template("verify.html", email=user.email)


@app.route("/verify/resend", methods=["POST"])
def resend_code():
    user = db.session.get(User, session.get("pending_user_id", 0))

    if user is None:
        return redirect(url_for("register"))

    if not user.email_verified:
        issue_verification_code(user)
        flash("A new code is on its way.")

    return redirect(url_for("verify"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = db.session.scalar(db.select(User).filter_by(email=email))

        if user and check_password_hash(user.password_hash, request.form["password"]):
            if not user.email_verified:
                issue_verification_code(user)
                session["pending_user_id"] = user.id
                flash("Please verify your email first. We sent you a new code.")
                return redirect(url_for("verify"))

            login_user(user)
            return redirect(url_for("index"))

        flash("Incorrect email or password.")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    questions = db.session.scalars(db.select(Question).order_by(Question.id)).all()
    selected = set(
        db.session.scalars(
            db.select(UserAnswer.option_id).filter_by(user_id=current_user.id)
        )
    )

    if request.method == "POST":
        picks = {
            question.id: [
                int(value)
                for value in request.form.getlist(f"q{question.id}")
                if value.isdigit()
            ]
            for question in questions
        }
        selected = {option_id for ids in picks.values() for option_id in ids}

        missing = [
            q for q in questions
            if q.key not in OPTIONAL_QUESTIONS and not picks[q.id]
        ]
        doubled = [q for q in questions if not q.allows_multiple and len(picks[q.id]) > 1]
        stray = [
            q for q in questions
            if not set(picks[q.id]) <= {option.id for option in q.options}
        ]

        if missing:
            flash("Please answer: " + "; ".join(q.prompt for q in missing))
        elif doubled:
            flash("Please pick just one answer for each single-choice question.")
        elif stray:
            flash("Please choose from the listed options.")
        else:
            db.session.execute(db.delete(UserAnswer).filter_by(user_id=current_user.id))
            for question in questions:
                for option_id in picks[question.id]:
                    db.session.add(
                        UserAnswer(
                            user_id=current_user.id,
                            question_id=question.id,
                            option_id=option_id,
                        )
                    )
            # Only written when the field actually came back, and only then if
            # it is one we offer. A submit that leaves it out must not reset
            # someone's pronouns to "unspecified" as a side effect.
            choice = request.form.get("pronouns")
            if choice in PRONOUNS:
                current_user.pronouns = choice
            db.session.commit()
            return redirect(url_for("index"))

    return render_template(
        "onboarding.html",
        questions=questions,
        selected=selected,
        optional=OPTIONAL_QUESTIONS,
        pronoun_options=PRONOUNS,
        pronouns_now=current_user.pronouns,
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run()
