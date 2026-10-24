import hashlib
import mimetypes
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal, InvalidOperation
from math import isfinite
from uuid import uuid4
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

import club_catalog
import club_icons
import emails
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
    ClubTag,
    Game,
    GamePairing,
    GameSeek,
    GamePost,
    GamePostRsvp,
    GameQueue,
    GAME_EVENTS,
    Fixture,
    HubRequest,
    Match,
    MAP_FLAG_KINDS,
    MapFlag,
    MapFlagViewer,
    MESSAGE_PARENTS,
    Message,
    PRONOUNS,
    Question,
    QuestionOption,
    REPORT_KINDS,
    Report,
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

# Answers that put somebody straight into a hub. Somebody who says they are
# forming a study group should not then have to go and find the hub for it.
#
# Keyed by the question and the answer rather than by option id: ids differ
# from one database to the next, and this has to mean the same thing on dev
# and on live. A dict rather than an `if`, so the second one of these is an
# entry rather than another branch buried in the route.
#
# Joining happens only when the answer is newly given, and nothing here ever
# removes anybody -- see the save block in onboarding() for why.
JOINS_HUB = {
    ("study_style", "I'm forming a study group with my classmates"):
        "Accountability Groups",
}
# How many people to suggest in the home rail. The friends page asks for more.
#
# Paired with HOME_CHATS below, and the pairing is the reason for the number.
# The two sit side by side on a wide screen and stretch to the taller of them,
# so three cards beside six conversation rows come out within about fifty
# pixels of each other and the grid covers the rest. Four left the chat
# column with a slab of white under it. Change one of these two and look at
# the home page, because nothing in the suite can see a layout.
RECOMMEND_LIMIT = 3
GENDER_BONUS = 2.0
GENDERS = {
    "man": "Man",
    "woman": "Woman",
    "nonbinary": "Non-binary",
    "undisclosed": "Prefer not to say",
}

# Windows' mimetypes has no entry for .webp, so the static route served the
# logo -- and the battleship banner before it -- as application/octet-stream.
# An <img> sniffs the bytes and shows it anyway, which is why this went
# unnoticed; a favicon does not, and browsers decline one whose type is not an
# image. Registered rather than relied upon, so it is the same on the host.
mimetypes.add_type("image/webp", ".webp")

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

# Cross-site request forgery, in three lines, for every form in the app.
#
# There is no CSRF token anywhere here: every form is raw HTML read through
# request.form. SameSite="Lax" means the browser does not send the session
# cookie on a cross-site POST at all, so a forged write arrives
# unauthenticated and @login_required turns it into a redirect. Nothing in
# the app wants a cross-site POST -- /live/away's sendBeacon is same-site --
# so this costs nothing and covers the thirty-odd POST routes that were
# written before anybody thought about it, not just the new ones.
#
# Secure is its own switch -- SESSION_COOKIE_SECURE in .env -- rather than
# being hard-coded: set it true on a site served over https, and leave it
# off for a development server on http, which would otherwise drop the
# cookie and make signing in impossible. (This comment used to claim it
# followed MAIL_USE_TLS. It never did.)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true")

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


# ---------- Club categories and icons ----------
# club_catalog.py decides a club's category and icon from its name;
# club_icons.py draws the icons. The icons are one SVG file, built once at
# start-up and served cached from /club-icons.svg, so pages carry only short
# references to it. The ?v= tag changes whenever a drawing does, so browsers
# never keep an old copy.
CLUB_SPRITE = club_icons.sprite_file()
CLUB_SPRITE_TAG = hashlib.sha1(CLUB_SPRITE.encode()).hexdigest()[:10]


@app.route("/club-icons.svg")
def club_icon_sprite():
    """The club icon set, for <use href="...#club-NAME">. Public and cached:
    it is pictures only, and the sign-in pages may show it too."""
    resp = app.response_class(CLUB_SPRITE, mimetype="image/svg+xml")
    resp.set_etag(CLUB_SPRITE_TAG)
    resp.cache_control.public = True
    resp.cache_control.max_age = 7 * 24 * 3600
    return resp.make_conditional(request)


def club_icon_href(name):
    """Where one club icon lives, for <use href>."""
    return url_for("club_icon_sprite", v=CLUB_SPRITE_TAG) + "#club-" + name


# Template globals rather than a context processor: plain functions, so no
# page pays a query for them.
#   club_look(name, description) -> (category, icon)   used by ui.club_badge
#   club_icon_href(icon)         -> the icon's address  used by ui.club_badge
app.jinja_env.globals.update(club_look=club_catalog.look, club_icon_href=club_icon_href)


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
    """Send the code, as text and as the harbour.

    Both parts, always: `body` is what a client with HTML turned off shows,
    and a message with only `html` reads as empty to it. The wording of both
    lives in emails.py, so they cannot drift apart.
    """
    subject, text, html = emails.verification(
        user.name, code, int(CODE_TTL.total_seconds() // 60)
    )
    mail.send(
        MailMessage(
            subject=subject,
            recipients=[user.email],
            body=text,
            html=html,
        )
    )


# What User.name holds. The two registration fields are joined with a space,
# so together they have to fit inside it.
NAME_MAX = 120

ONBOARDING_EXEMPT = {
    "static",
    "club_icon_sprite",
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


# There was a `same_origin` before_request here, comparing the Origin header
# against request.host_url and refusing a mismatch. It compared the SCHEME as
# well as the host, and this site is served over https through a proxy with
# gunicorn on plain port 80 behind it -- so every browser sent
# "https://host" while Flask computed "http://host/", and every POST in the
# app was answered 403 {"error":"unavailable"}. Register, log in, send a
# message, the map's writes: all of it, for everyone, until it was removed.
#
# SESSION_COOKIE_SAMESITE = "Lax" above is what actually stops a cross-site
# write, and it needs to know nothing about the scheme, so no proxy can
# confuse it. The Origin check was only ever a second layer, and it bought
# less than it cost.
#
# If it comes back it needs ProxyFix (or X-Forwarded-Proto) so Flask knows
# what scheme the browser used, and it needs tests/test_browser_posts.py --
# which sends what a browser behind a proxy really sends. The old check was
# untestable by construction: it returned early when Origin was absent, and
# the Flask test client never sends one.


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


# How many clubs on the browse page carry a "suggested" badge. A handful:
# past that it stops being a recommendation and becomes the list again.
CLUB_PICKS = 4

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


# Where a form is allowed to send you afterwards. "index" is here because the
# chat page can now leave a club or close a pairing, and the page you were on
# stops being readable the moment you do -- home, where the chat list is, is
# the only sensible place to land.
RETURN_TO = {"matches", "clubs", "chats", "welcome", "index", "find_game"}


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


def back_to(default="matches"):
    """Where a posted `back` says to go, or the caller's own fallback.

    The default is the caller's because the right fallback differs: a club
    left from /clubs belongs back on /clubs, a pairing closed from /find
    belongs back on /find. An unknown or missing value falls to the default
    rather than to a fixed page, and the default is checked too, so a typo
    here cannot build a url_for for a route that does not exist.
    """
    target = request.form.get("back") or default
    if target not in RETURN_TO:
        target = default if default in RETURN_TO else "matches"
    return redirect(url_for(target))


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

# What the time selects offer. Twelve-hour on purpose: the whole point is that
# a meetup's time cannot be read two ways, and "18:00" is only unambiguous to
# people who already think in 24-hour time.
WHEN_HOURS = tuple(str(h) for h in range(1, 13))
WHEN_MINUTES = ("00", "15", "30", "45")
WHEN_MERIDIEM = ("AM", "PM")


def when_label(on, hour, minute, ampm):
    """One line saying when a meetup is, from its date and a clock time.

    "Tue 3 Nov at 6:30 PM", or "Tue 3 Nov" when no time was picked.

    The date is required now and the typed day is gone, so this composes from
    a real date rather than from whatever somebody wrote. What it returns
    still goes into when_text, which stays free text and stays exactly as it
    is for everything posted before this -- 'Friday 6pm', '6 PM', 'Now' all
    keep displaying as they always did, with no backfill that would have to
    guess what 'Now' meant.
    """
    day = f"{on:%a} {on.day} {on:%b}" if on else ""
    clock = f"{int(hour)}:{minute} {ampm}" if hour else ""
    if day and clock:
        return f"{day} at {clock}"
    return day or clock or None

# The one value the activity select may carry that is not a catalogue activity.
# A sentinel rather than "anything we do not recognise" on purpose: writing an
# activity in is an explicit choice with its own two fields, so a stray or
# mistyped `game` is still refused rather than quietly becoming a meetup for
# whatever the sender felt like typing.
OTHER = "other"
CUSTOM_GAME_MAX = 60
CUSTOM_LOCATION_MAX = 120

# How long a meetup's chat outlives the meetup. Long enough to say how it went
# and sort out who left what behind; not so long that a chat for a thing that
# happened last week is still sitting in everybody's list.
MEETUP_CHAT_GRACE = timedelta(hours=12)
# And when no date was given -- "Tonight", "Now" -- measured from the posting
# instead. A meetup with no date still has to stop at some point, or its chat
# would be the one thing on the site that lives for ever.
MEETUP_CHAT_DEFAULT = timedelta(hours=36)

# How many meetups the board shows. It listed every open one -- thirty-six
# here, and an open meetup never closed itself, so the number only ever grew.
FIND_POSTS = 10

# How long a meetup stays on the board once it has started. Nobody says how
# long they will be there, so this is a guess, and it is deliberately on the
# generous side: dropping somebody's meetup while they are still at it is a
# worse mistake than leaving a finished one up for an afternoon.
MEETUP_OVER_AFTER = timedelta(hours=3)


def still_on(now=None):
    """A clause matching the meetups that have not been and gone.

    Three cases, because a meetup carries its "when" in up to three ways and
    older ones carry fewer:

      * it named a start time  -- over MEETUP_OVER_AFTER past it;
      * it named only a day    -- over at the end of that day in Boston;
      * it named neither       -- over MEETUP_CHAT_DEFAULT after it was
        posted, which is exactly the horizon its own chat already uses, so
        the board and the conversation stop at the same moment.

    A clause rather than a Python filter so the board can still ask the
    database for ten rows instead of reading every open meetup and throwing
    most of them away.
    """
    now = now or datetime.utcnow()
    # The Boston day a meetup's date is still "today" for. Computed here
    # rather than in SQL, which has no idea where the campus is.
    today = utc_to_boston(now).date()
    return db.or_(
        db.and_(GamePost.starts_at.is_not(None),
                GamePost.starts_at > now - MEETUP_OVER_AFTER),
        db.and_(GamePost.starts_at.is_(None),
                GamePost.when_on.is_not(None),
                GamePost.when_on >= today),
        db.and_(GamePost.starts_at.is_(None), GamePost.when_on.is_(None),
                GamePost.created_at > now - MEETUP_CHAT_DEFAULT),
    )


def meetup_chat_deadline(post):
    """When a meetup's chat stops taking messages.

    The end of its date plus a grace window, or a fixed horizon from the
    posting when the date was left out. Computed once, when the chat is made:
    a meetup cannot be edited, so there is nothing for this to drift from.
    """
    if post.when_on is not None:
        # datetime.max.time() rather than the `time` class: the name `time` is
        # taken here by the stdlib module the live loop uses for monotonic().
        end_of_day = datetime.combine(post.when_on, datetime.max.time())
        return end_of_day + MEETUP_CHAT_GRACE
    return (post.created_at or datetime.utcnow()) + MEETUP_CHAT_DEFAULT


def chat_ended(chat):
    """Whether a chat has stopped taking messages, and why it would have.

    A read rather than a stored flag, for the same reason away_for is one:
    nothing has to run for either fact to become true. A meetup chat ends when
    its deadline passes *or* when the meetup is cancelled, and deriving both
    means there is no second copy of the answer to go stale -- cancelling is
    felt by everyone in the same instant, with nothing to sweep.

    An ordinary chat has neither a deadline nor a meetup, so this is False for
    every chat a student made by hand.
    """
    if chat is None:
        return False
    if chat.expires_at is not None and datetime.utcnow() >= chat.expires_at:
        return True
    if chat.post_id is None:
        return False
    post = db.session.get(GamePost, chat.post_id)
    return post is None or post.status != "open"


def make_meetup_chat(post):
    """The chat that belongs to a meetup, with its author already in it.

    Insert and retry on the code, exactly as chat_new does and for the same
    reason: two students posting in the same moment could both pass a
    pre-check and then collide on it.

    Never public, and the code it carries will not let anybody in -- a meetup
    chat's membership is the RSVP list. It gets a code at all because the
    column is NOT NULL and shared with every other chat; nothing shows it.
    """
    for _ in range(CODE_TRIES):
        # A savepoint, where chat_new rolls the whole thing back. That one is
        # creating nothing but the chat, so a rollback costs it nothing; here
        # the meetup is already in this transaction and a rollback would throw
        # the post away to retry a join code.
        try:
            with db.session.begin_nested():
                room = Chat(name=post.game[:80], code=new_code(),
                            is_public=False, owner_id=post.author_id,
                            post_id=post.id,
                            expires_at=meetup_chat_deadline(post))
                db.session.add(room)
        except IntegrityError:
            continue
        db.session.add(ChatMember(user_id=post.author_id, chat_id=room.id))
        return room
    return None

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


def fold_name(text):
    """An activity name reduced to what two people have to agree on.

    Trimmed, inner runs of whitespace collapsed, lowercased. This is the whole
    reason the in-person queue can take a write-in at all: it used to match on
    the string as typed, so "basketball" and "Basketball" were two queues and
    the two students in them never met.

    Folded for comparison rather than rewritten on the way in, so what gets
    stored is still the spelling somebody chose -- "FIFA" would come back as
    "Fifa" from anything that normalised instead.
    """
    return " ".join((text or "").split()).lower()


def queue_name(text):
    """What to store for a typed activity.

    The catalogue's spelling when the text names an in-person activity, so a
    student who types "chess" joins the Chess queue that the picker fills
    rather than starting a second one next to it. Otherwise their own words,
    tidied of stray whitespace.

    Only in-person activities are resolved. Battleship is in the catalogue as
    online-only, and somebody typing it here means the board on a table --
    resolving it would hand back an activity this page then has to refuse.
    """
    tidy = " ".join((text or "").split())
    if not tidy:
        return ""
    known = db.session.scalar(
        db.select(Activity).where(db.func.lower(Activity.name) == tidy.lower(),
                                  Activity.in_person.is_(True))
    )
    return known.name if known else tidy


def queue_waiting(user_id):
    """Who else is waiting, keyed by folded name: counts and a spelling.

    One query, and the rows are grouped here rather than by the database
    because the page wants a name to show as well as a number -- and the name
    has to be somebody's actual spelling, picked the same way every time. The
    longest-waiting student's wins.
    """
    counts, display = {}, {}
    rows = db.session.scalars(
        db.select(GameQueue)
        .where(GameQueue.user_id != user_id)
        .order_by(GameQueue.created_at, GameQueue.user_id)
    )
    for row in rows:
        key = fold_name(row.game)
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, row.game)
    return counts, display




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
        # Closed as well as yours. This used to ask only whether you were one
        # of the two, so closing a pairing dropped it off the chat list and
        # left the page itself open -- a conversation both of you had agreed
        # was over, still reachable by anyone who had the link in their
        # history. Closed now means closed in both places.
        if (pairing is None or pairing.status != "open"
                or current_user.id not in (pairing.user_lo, pairing.user_hi)):
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


# The conversations a meetup can be arranged in: the one-on-one ones. A club
# or a group chat has a room full of people, which is what /find is for.
PRIVATE_MEETUP_CHATS = ("match", "pair")

# And the conversations a meetup card can appear in at all, which is those two
# plus the campus room. live_cards walks this.
CARD_CHATS = (COMMUNITY,) + PRIVATE_MEETUP_CHATS

# Where a friend button can be pressed: the three rooms that draw a list of
# people, and the pair chat, which has one person and a header to put it in.
ROSTER_CHATS = ("group", "club", "pair")


def chat_home(kinds):
    """The conversation a button was pressed in, or None.

    Resolved through chat_target rather than trusted, which is the same check
    the chat page itself passes. That is what stops a posted form reaching a
    conversation somebody is not in -- club and group ids are small integers,
    and a redirect into a room you cannot read is a bad answer to give.

    Returns (kind, target_id, parent); `kinds` says which are allowed here,
    because what a button means differs by surface.
    """
    kind = request.form.get("from_kind", "")
    target_id = request.form.get("from_id", type=int)
    if kind not in kinds or target_id is None:
        return None
    parent, _ = chat_target(kind, target_id)
    return None if parent is None else (kind, target_id, parent)


def card_home():
    """The one-on-one a meetup was posted from or acted on in, or None.

    The Meet up form and the card's own buttons carry the conversation in
    hidden fields. Resolved through chat_target rather than trusted, which is
    the same check the chat page itself passes: it is what stops a posted form
    putting a meetup into somebody else's conversation, and it keeps a
    redirect from landing anybody in a chat they cannot read.

    Returns (kind, target_id, parent), so the caller has both the id the chat
    URL needs and the row attach_parent needs.
    """
    return chat_home(PRIVATE_MEETUP_CHATS)


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
    rooms = {}
    pins = {}
    post_ids = [m.post_id for m, _ in rows if m.post_id]
    if post_ids:
        posts = {p.id: p for p in db.session.scalars(
            db.select(GamePost).where(GamePost.id.in_(post_ids)))}
        going, attending = rsvps_for(post_ids)
        rooms = chats_for(post_ids)
        pins = flags_for(post_ids)

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
            # The same moment in full (UTC), so the page can show it in the
            # reader's own time zone and mark where each day starts.
            "ts": m.created_at.isoformat() + "Z",
            "mine": m.user_id == current_user.id,
        }
        post = posts.get(m.post_id)
        if post is not None:
            entry["post"] = post_payload(
                post, going.get(post.id, []), post.id in attending, rooms, pins)

        if m.event:
            game = events.get(m.game_id)
            other = db.session.get(
                User,
                game.user_in_seat(1 - game.seat_of(current_user.id))
            ) if game and game.seat_of(current_user.id) is not None else None
            entry["event"] = m.event
            entry["game_id"] = m.game_id
            # The title on the game's card in the log ("Battleship").
            entry["game_label"] = (rules.LABELS.get(game.kind, "Game")
                                   if game is not None else "Game")
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


def chats_for(post_ids):
    """Each meetup's chat id, for a whole page in one query.

    rsvps_for's shape, for the same reason: these are built for lists, and a
    lookup per card is a query per card. Meetups posted before meetups had
    chats are simply absent from the result.
    """
    if not post_ids:
        return {}
    return dict(db.session.execute(
        db.select(Chat.post_id, Chat.id).where(Chat.post_id.in_(post_ids))
    ).all())


# What the card shows, in the order a reader reads it. Named here because
# post_card_token digests exactly this and nothing else: a field added to the
# card and not to this list would change the card without moving its token,
# and the card would then sit stale under somebody watching it.
CARD_SHOWS = ("id", "game", "mode_label", "location", "when_text", "note",
              "max_players", "status", "count", "full", "im_going", "mine",
              "chat_id", "flag")


def post_card_token(payload):
    """What one meetup card compares, so only a card that moved is replaced.

    Per card rather than per room, because a swap costs something: a reader
    halfway through the "are you sure" on their own card should not have it
    shut by somebody else's RSVP to the meetup underneath it.

    Who is going is folded in by name, not just by count -- one person leaving
    as another joins leaves the count where it was and the line of names
    completely different.
    """
    shown = [str(payload[key]) for key in CARD_SHOWS] + list(payload["going"])
    digest = hashlib.sha1("|".join(shown).encode()).hexdigest()[:12]
    return f"{payload['id']}:{digest}"


def flags_for(post_ids):
    """Each meetup's map flag, for a whole page in one query.

    chats_for's shape: {post_id: (public_id, visibility)}. Meetups with no
    flag (online ones, or ones whose owner took the flag down) are absent.
    """
    if not post_ids:
        return {}
    return {post_id: (public_id, visibility) for post_id, public_id, visibility
            in db.session.execute(
                db.select(MapFlag.post_id, MapFlag.public_id, MapFlag.visibility)
                .where(MapFlag.post_id.in_(post_ids),
                       MapFlag.expires_at > datetime.utcnow()))}


def post_payload(post, going=None, im_going=None, chats=None, flags=None):
    if going is None:
        going, mine = rsvps_for([post.id])
        going = going.get(post.id, [])
        im_going = post.id in mine
    if chats is None:
        chats = chats_for([post.id])
    if flags is None:
        flags = flags_for([post.id])
    # The meetup's flag, but only for someone allowed to see it: everyone
    # once its owner made it public, otherwise the people who are in.
    flag_id, flag_audience = flags.get(post.id, (None, None))
    can_see_flag = flag_id is not None and (
        flag_audience == "public" or im_going or post.author_id == current_user.id)
    data = {
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
        # The meetup's own chat, so the card can offer a way into it rather
        # than sending people round by the chats hub. None for meetups posted
        # before meetups had chats.
        "chat_id": chats.get(post.id),
        # Its flag on the campus map, when this reader may see it.
        "flag": flag_id if can_see_flag else None,
    }
    # Last, and from the finished dict: the card's own token is a digest of
    # what is in here, so it has to be built after the rest of it.
    data["card_token"] = post_card_token(data)
    return data


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

    # Which row each conversation's messages are filed under. A friend chat
    # is addressed by the other person's id but its messages carry the match's,
    # so that mapping is built once here -- it used to be a query per friend.
    match_for = {
        (hi if lo == current_user.id else lo): match_id
        for match_id, lo, hi in db.session.execute(
            db.select(Match.id, Match.user_lo, Match.user_hi)
            .where(db.or_(Match.user_lo == current_user.id,
                          Match.user_hi == current_user.id),
                   Match.status == "active")
        )
    }

    wanted = {kind: set() for kind in MESSAGE_PARENTS}
    for entry in entries:
        kind = entry["kind"]
        if kind == COMMUNITY:
            continue
        filed_under = (match_for.get(entry["target_id"]) if kind == "match"
                       else entry["target_id"])
        entry["_filed"] = filed_under
        if filed_under is not None:
            wanted[kind].add(filed_under)

    last = {}
    newest = db.session.execute(
        db.select(Message.body, Message.created_at)
        .where(Message.room == COMMUNITY)
        .order_by(Message.id.desc()).limit(1)
    ).first()
    if newest:
        last[(COMMUNITY, 0)] = (newest[0], newest[1])

    # The newest message in each of THIS student's conversations. It used to
    # read every message in the table, once per parent kind, and keep the
    # first of each -- four unbounded scans to answer a question about the
    # dozen or so rows in front of them. DISTINCT ON asks the database for
    # the newest per conversation instead, over the ids collected above.
    for kind, column in MESSAGE_PARENTS.items():
        ids = sorted(wanted[kind])
        if not ids:
            continue
        for parent_id, body, when in db.session.execute(
            db.select(column, Message.body, Message.created_at)
            .where(column.in_(ids))
            .distinct(column)
            .order_by(column, Message.id.desc())
        ):
            last[(kind, parent_id)] = (body, when)

    for entry in entries:
        kind = entry["kind"]
        key = ((COMMUNITY, 0) if kind == COMMUNITY
               else (kind, entry.pop("_filed", None)))
        preview, when = last.get(key, (None, None))
        entry["preview"] = preview
        entry["when"] = when

    # Sorted here and sliced by the caller, never the other way round: the
    # the most recent few is a different answer from the first few found.
    entries.sort(key=lambda e: (e["when"] is not None, e["when"]), reverse=True)
    return entries


# How many conversations the home page shows before it stops. Every chat a
# student has was burying everything below it -- 72 rows on one account here.
# Six rather than five to match the suggestion rail beside it: see
# RECOMMEND_LIMIT, which is the other half of that pairing.
HOME_CHATS = 6


@app.route("/chats/all")
@login_required
def all_chats():
    """Every conversation, for when the recent few are not the one you wanted."""
    return render_template("allchats.html", chats=chat_list())


@app.route("/")
@login_required
def index():
    everything = chat_list()
    return render_template(
        "home.html",
        chats=everything[:HOME_CHATS],
        # The real number, not the number drawn. A band that counts the rows
        # it drew tells a student with seventy conversations nothing.
        chat_total=len(everything),
        recs=matching.recommend_people(
            current_user.id, limit=RECOMMEND_LIMIT, gender_bonus=GENDER_BONUS
        ),
    )


# What the welcome page offers. Fewer than /matches shows: this is the first
# screen of the app and a wall of strangers is not a welcome.
WELCOME_PEOPLE = 4
WELCOME_CLUBS = 4


@app.route("/welcome")
@login_required
def welcome():
    """What the answers just given are actually worth, before the app proper.

    Onboarding asks eight questions and then dropped the student straight on
    the home page, where the connection between what they had just typed and
    what they were looking at was left for them to notice. This is that
    connection, said once: these people, because you like the same things;
    these clubs, because you picked these answers.

    Nothing here is new machinery. recommend_people and recommend_clubs are
    the same two calls /matches makes, and the cards are the same cards --
    which is the point: a suggestion must not be made one way on the first
    screen and another way everywhere else.

    Reachable afterwards at its own address, and a perfectly good page to
    come back to. What is one-time is the redirect, not the page.
    """
    return render_template(
        "welcome.html",
        recs=matching.recommend_people(
            current_user.id, limit=WELCOME_PEOPLE, gender_bonus=GENDER_BONUS
        ),
        clubs=matching.recommend_clubs(current_user.id, limit=WELCOME_CLUBS),
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
        # A meetup's chat stops taking messages once the meetup has been and
        # gone, or the moment it is cancelled. Refused here as well as hidden
        # in the template: the composer going away is not a rule.
        elif kind == "group" and chat_ended(parent):
            flash("That meetup has ended, so its chat is closed.")
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

    # Opening the board counts as being at it, the same as the live request
    # that follows a moment later. Has to come after the game is resolved --
    # it was above this and referred to a name that did not exist yet.
    if game is not None and mark_present(game, current_user.id):
        db.session.commit()

    group = parent if kind == "group" else None

    # Who is in the room, and what stands between me and each of them. A club
    # space had neither until now: chat() only built a list for a group chat,
    # so the room you would most want to meet people in showed nobody.
    roster = []
    if group is not None:
        roster = chat_members(group.id)
    elif kind == "club":
        roster = club_members(target_id)

    # The pair chat has no list, but it has the one person the queue put you
    # with -- and that is the other place with no way to become friends.
    people = [person.id for person in roster]
    if kind == "pair" and opponent_id:
        people.append(opponent_id)
    friend_states = friend_state_for(people) if people else {}
    # Drawn after the states, because who may be shown depends on them.
    roster = visible_people(roster, friend_states)

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
        members=roster,
        friend_states=friend_states,
        # The club this page is, when it is one: the roster's buttons carry it
        # so pressing one comes back here.
        club_id=target_id,
        is_owner=group is not None and group.owner_id == current_user.id,
        # A meetup's chat: its membership is the RSVP list, so the controls
        # that add and remove people do not belong on it, and the join code
        # it carries would not work for anybody who tried it.
        meetup=group is not None and group.post_id is not None,
        ended=chat_ended(group),
        invitable=invitable,
        subtitle=CHAT_SUBTITLE[kind],
        game=game,
        seat=game_seat,
        payload=game_payload(game, game_seat) if game is not None else None,
        game_token=game_token(game, game_seat),
        # Asked of live_game_with rather than the pane: an invite is a message
        # now, so the pane does not see it, and offering to start a second
        # game during one would be wrong.
        opponent_id=opponent_id,
        # Still friends-only: game_new requires a friendship, so a pair chat
        # has no rematch button. Queue again from the lobby instead.
        can_start=(kind == "match" and live_game_with(target_id) is None),
        other_id=target_id,
        # The other person, which is not target_id in a pair chat: there the
        # url carries the pairing's id, not theirs.
        partner_id=opponent_id,
        messages=chat_rows(kind, parent),
        game_kinds=[(k, rules.LABELS[k]) for k in rules.PLAYABLE],
        post_modes=POST_MODES,
        when_hours=WHEN_HOURS,
        when_minutes=WHEN_MINUTES,
        when_meridiem=WHEN_MERIDIEM,
        # Only the community room renders the meetup form, so only it needs
        # the catalogue. Building it unconditionally cost every other chat a
        # pile of queries for a form that was never on the page.
        # The campus room's form, and now the Meet up button in a
        # one-on-one. Still not built for a club or a group chat: the comment
        # this replaces was about not paying for a form that is not there,
        # and for those two it still is not.
        catalogue=(activity_catalogue(presets_only=True)
                   if kind in CARD_CHATS else []),
        # What the cards on this page add up to, handed over the way the game
        # pane's and the roster's tokens are: on the markup. Without it the
        # first live round of every visit to the campus room would come back
        # at once with every card in it.
        card_token=(live_cards_token() if kind == COMMUNITY else "none"),
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
        "token": game_token(game, seat),
        "live": busy,
        "html": render_template("_gamepane.html", game=game, seat=seat,
                                payload=game_payload(game, seat),
                                other_id=target_id),
    }


def live_group_token(chat_id):
    """What the poller compares to decide whether to swap the roster.

    Membership and nothing else: the count, and the newest joined_at. Both
    move when somebody joins and when somebody leaves, and neither moves when
    somebody talks -- which is the property that matters. This is recomputed
    on every probe of every held request in a group chat, so a token that
    moved per message would wake every tab in the chat on every message, and
    the channel would stop being a channel.

    One indexed aggregate, for the same reason.
    """
    if chat_id is None:
        return "none"
    count, newest = db.session.execute(
        db.select(db.func.count(), db.func.max(ChatMember.joined_at))
        .where(ChatMember.chat_id == chat_id)
    ).one()
    return f"{chat_id}:{count}:{newest.isoformat() if newest else '-'}"


def live_group(chat):
    """The roster half of a live answer, for a group chat.

    live_pane's shape, deliberately: the client already knows how to swap a
    {token, html} pair without re-rendering markup that has not changed.
    """
    if chat is None:
        return None
    token = live_group_token(chat.id)
    # Resolved once and used three times below: who is in the room, what
    # stands between the viewer and each of them, and which of those rows the
    # viewer may actually be shown.
    members = chat_members(chat.id)
    states = friend_state_for([m.id for m in members])
    return {
        "token": token,
        "html": render_template(
            "_grouppane.html", group=chat, group_token=token,
            # Resolved once: the roster, who may see whom, and the rows
            # that survive it. The live copy draws the same list the page
            # does, so an unlisted member left in here would be revealed by
            # the next swap even though the page itself hid them.
            members=visible_people(members, states),
            friend_states=states,
            is_owner=chat.owner_id == current_user.id,
            meetup=chat.post_id is not None,
            # The invite picker is rebuilt with the rest of the bar. It is
            # only ever offered on an ordinary chat.
            invitable=[] if chat.post_id is not None else [
                f for f in matching.friend_names(current_user.id)
                if f["user_id"] not in {m.id for m in members}],
        ),
    }


def live_cards_token():
    """What the poller compares to decide whether the meetup cards have moved.

    Every RSVP on the site in one aggregate, with no join to the room. A
    Message carries a post_id in exactly one place -- post_new, into the
    campus-wide room -- so every meetup card there is, is in that room; the
    whole table *is* this room's cards, and scoping the probe to it would buy
    nothing and cost a join.

    Three parts, for the three ways a card changes under a reader who is not
    touching it. The count moves when somebody joins or leaves. The newest
    RSVP moves when a join and a leave land between the same two probes,
    which the count alone would miss. And the closed count moves when an
    author cancels a meetup -- that writes no message at all, so nothing else
    in this loop would notice it.

    One round trip, because this runs five times a second for every tab with
    the campus room open.
    """
    count, newest, shut = db.session.execute(db.select(
        db.select(db.func.count()).select_from(GamePostRsvp).scalar_subquery(),
        db.select(db.func.max(GamePostRsvp.created_at)).scalar_subquery(),
        db.select(db.func.count()).select_from(GamePost)
        .where(GamePost.status != "open").scalar_subquery(),
    )).one()
    return f"{count}:{newest.isoformat() if newest else '-'}:{shut}"


def live_cards(kind, token, parent=None, target_id=None):
    """The meetup cards half of a live answer, for the campus-wide room.

    live_pane's and live_group's shape, and for the same reason: the client
    already knows how to be handed markup with a token on it.

    Every card in the room is rendered, which is more than has changed. That
    is deliberate: the alternative is the client telling the server which
    cards it is holding, and a page that has a card the server left out would
    disagree for ever and spin the channel. Rendering them all is paid when
    somebody RSVPs rather than on every probe, and each card carries its own
    token -- so the client replaces only the one that moved and the rest of
    the room is not touched.
    """
    if kind not in CARD_CHATS or (kind != COMMUNITY and parent is None):
        return None

    rows = db.session.execute(
        db.select(Message.id, GamePost)
        .join(GamePost, GamePost.id == Message.post_id)
        # Whichever conversation is being watched. This used to say
        # Message.room == COMMUNITY, on the stated grounds that a post_id
        # only ever reached a message there -- a meetup arranged inside a
        # one-on-one is the case that stopped being true. message_filter is
        # the same helper chat_rows uses, so there is one answer to "which
        # messages are in this conversation".
        .where(message_filter(kind, parent))
        .order_by(Message.id)
    ).all()
    ids = [post.id for _, post in rows]
    # Batched once for the room, not once per card: the whole point of
    # rsvps_for and chats_for taking lists.
    going, mine = rsvps_for(ids)
    rooms = chats_for(ids)
    pins = flags_for(ids)

    # Straight off the environment, not through render_template. That runs
    # every context processor, and inject_dock is six queries -- the right
    # price for a page, and an unaffordable one per card: rendering the 113
    # cards in a real campus room measured at 2.9 SECONDS, 678 queries for a
    # partial that reads none of what they fetched. This keeps the loader and
    # url_for and skips the page context, which nothing in the card looks at.
    card_markup = app.jinja_env.get_template("_postcard.html")

    cards = []
    for message_id, post in rows:
        card = post_payload(post, going.get(post.id, []), post.id in mine,
                            rooms, pins)
        cards.append({
            # The message the card lives inside, so a card can be put back
            # where it belongs -- including into a message that arrived live
            # a moment ago and has no card in it yet.
            "id": message_id,
            "post": post.id,
            "token": card["card_token"],
            # The card's buttons carry the conversation they are in, so
            # pressing one comes back here. Rendered standalone, this has no
            # page context to read it from, so it is handed over.
            "html": card_markup.render(
                post=card,
                home_kind=(kind if kind in PRIVATE_MEETUP_CHATS else None),
                home_id=target_id),
        })
    return {"token": token, "cards": cards}


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
    # Whether the page asking is actually on screen. Only presence cares: a
    # backgrounded tab should still collect notices.
    hidden = request.args.get("hidden") == "1"

    watching = kind is not None
    parent = None
    if watching:
        parent, _ = chat_target(kind, target_id)
        if parent is None:
            return jsonify({"error": "unavailable"}), 403

    after = request.args.get("after", type=int, default=0)
    token = request.args.get("token", "")
    # The roster the page already has, so it is only re-rendered once
    # membership has actually moved.
    group_token = request.args.get("crowd", "")
    # And the meetup cards it is holding. RSVPing writes no message into the
    # campus room, so without this the "2 of 4 going" on a card sat stale
    # under whoever posted it until they reloaded.
    card_token = request.args.get("cards", "")

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
        touched = notices.clear_chat(me, kind, target_id)

        # And for the same reason, this is where a board records that somebody
        # is sitting at it. A hidden tab still polls -- notices and the bell
        # should keep arriving for a window behind another one -- but it says
        # so, and a board nobody is looking at is not a player who is there.
        if watch_other is not None:
            if mark_present(pane_game(watch_other), me, here=not hidden):
                touched = True

        if touched:
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
        # Defaulted to what the page already has, exactly as `fresh` is: a
        # channel with no chat open -- which is every page that is not a chat
        # -- never reaches the branch below, and comparing an unset name is a
        # 500 on the site's busiest request.
        crowd = group_token
        # Defaulted the same way, and for a second reason as well: a page
        # that does not know about this field at all sends nothing, and
        # comparing it against a freshly computed token would return on every
        # round for ever.
        deck = card_token
        if watching:
            newest = db.session.scalar(
                db.select(db.func.max(Message.id)).where(where)
            ) or 0
            # pane_game rather than a hand-rolled probe: it owns the rules
            # about which game the panel shows, and it is where an abandoned
            # game gets settled. Reproducing that here would let the two drift.
            game = pane_game(watch_other) if watch_other else None
            fresh = game_token(game, game.seat_of(me) if game else None)
            # Membership, for a group chat only. Same shape as the game
            # token: one cheap aggregate, compared rather than rendered.
            crowd = (live_group_token(target_id)
                     if kind == "group" else "none")
            # Only a conversation that can hold a meetup card pays for
            # this probe.
            if kind in CARD_CHATS:
                deck = live_cards_token()

        if (newest > after or fresh != token or bell_now != bell
                or crowd != group_token or deck != card_token):
            rows = []
            if watching:
                # Resolved again, because this wait has been held for up to
                # LIVE_WAIT seconds and the session was released between
                # probes. In that window the conversation can go: a pairing
                # closed or deleted, a friendship ended, somebody removed
                # from a group. It then comes back None, and passing that to
                # chat_rows raised AttributeError on parent.id -- a 500 on
                # the one endpoint every open tab calls, several times a
                # minute. The same answer the check at the top gives, which
                # the client already knows how to handle.
                parent, _ = chat_target(kind, target_id)
                if parent is None:
                    return jsonify({"error": "unavailable"}), 403
                rows = chat_rows(kind, parent, after=after)
            return jsonify({
                "after": max([r["id"] for r in rows], default=after),
                "messages": rows,
                "game": (live_pane(watch_other, game)
                         if watch_other else None),
                "group": (live_group(db.session.get(Chat, target_id))
                          if kind == "group" else None),
                "cards": live_cards(kind, deck, parent, target_id),
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


@app.route("/live/away", methods=["POST"])
@login_required
def live_away():
    """A tab saying it is leaving a board. The mirror of /live.

    Absence used to be inferred from silence, and silence takes PRESENT_WITHIN
    to notice -- or never arrives at all, because a tab behind another window
    keeps polling. Closing a tab, walking to another page and locking a phone
    are all knowable the instant they happen, so the page says so instead,
    with sendBeacon.

    It takes the same `here=match:12` the channel already has, so the page
    needs to know nothing about which game is on it. And it speaks only for
    whoever sent it: taking a game id and clearing "the other seat" would let
    anybody push their opponent out of their chair, and being away is what
    makes somebody claimable against.
    """
    here = request.values.get("here", "")
    kind, _, raw = here.partition(":")
    if kind not in MESSAGE_PARENTS or not raw.isdigit():
        return "", 204          # nothing to let go of; a beacon cannot retry

    parent, _ = chat_target(kind, int(raw))
    other = chat_other_id(kind, parent) if parent is not None else None
    if other is not None and mark_present(pane_game(other), current_user.id,
                                          here=False):
        db.session.commit()
    return "", 204


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
        "token": game_token(game, seat),
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


def ago_label(gone):
    """How long ago, in words a sentence can hold: "4 minutes", "2 hours"."""
    if gone is None:
        return None
    minutes = int(gone.total_seconds() // 60)
    if minutes < 1:
        return "a moment"
    if minutes < 60:
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour" + ("" if hours == 1 else "s")
    days = hours // 24
    return f"{days} day" + ("" if days == 1 else "s")


def game_payload(game, seat):
    other_id = game.user_in_seat(1 - seat)
    other = db.session.get(User, other_id)
    gone = away_for(game, 1 - seat) if game.status == "active" else None
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
        # Who ended it, as the reader needs it. The same choice event_wording
        # makes for a message and for the same reason: one row, two people,
        # two different sentences, and this is where the reader is known.
        # None where it was never recorded -- every row made before the
        # column existed -- and the panel then says something neutral rather
        # than naming somebody who may not have done it.
        "ended_by_me": (None if game.ended_by is None
                        else game.ended_by == current_user.id),
        # An invite is declined by whoever was invited and withdrawn by
        # whoever sent it. Same status, so this is the only thing that tells
        # the two apart.
        "withdrawn": (game.status == "declined"
                      and game.ended_by is not None
                      and game.ended_by == game.player_a),
        "you_won": game.winner_id == current_user.id,
        # Rendered into data-token by the pane. Handed over rather than
        # rebuilt in the template, so the two cannot drift apart.
        "token": game_token(game, seat),
        # A flag and a phrase, never the timedelta itself: this payload is
        # handed to jsonify by /games/<id>/state, which cannot serialise one.
        "opponent_away": gone is not None,
        "away_label": ago_label(gone),
        # "Left a moment ago" is false for somebody who has not arrived yet,
        # which is every game until the other player first opens the board.
        "opponent_arrived": game.seen_for(1 - seat) is not None,
        "can_claim": claimable_by(game, seat),
    }


# A game in play. One at a time per pair, so "the game with them" is never
# ambiguous and the chat pane never has to choose.
LIVE_GAME = ("invited", "active")

# How many finished games the lobby shows. It listed every one ever played --
# 141 on one account here -- and each row costs a lookup to find the chat it
# lives in, so the page got slower the more somebody played. Games still being
# played are not capped: there are never many, and losing one would lose the
# way back to the board.
GAMES_DONE = 5
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
    """The game in play with someone, or None.

    An invite past INVITE_TTL does not count. "One game at a time" is a rule
    about a game two people are actually having, and an invite nobody has
    touched for a week is not one -- enforcing it from a fossil is how a pair
    ends up unable to play each other with nothing to press.
    """
    return db.session.scalar(
        db.select(Game)
        .where(shared_with(other_id), db.or_(
            Game.status == "active",
            db.and_(Game.status == "invited",
                    Game.created_at > datetime.utcnow() - INVITE_TTL)))
        .order_by(Game.updated_at.desc(), Game.id.desc())
    )


def pending_invite_with(other_id):
    """The unanswered invite between us, fresh or stale, or None."""
    return db.session.scalar(
        db.select(Game)
        .where(shared_with(other_id), Game.status == "invited")
        .order_by(Game.id.desc())
    )


def pane_game(other_id):
    """What the chat's game pane should show: the most recent game, or nothing.

    The *most recent*, and never anything older. This used to ask for "the
    newest finished game I have not dismissed", which meant closing the
    current result simply uncovered the one before it -- every result you had
    never dismissed was queued up behind it. Looking only at the latest game
    makes that impossible rather than tidying up after it.

    An invite is not what this shows when there is anything else to show: it
    arrives as a message with its own buttons, and letting it become the
    newest game would blank a result the other player is still reading. So it
    is skipped when picking the latest game, exactly as before -- and offered
    only where this would otherwise return nothing at all. That costs no
    result its place and gives an invite a second way to be answered, one
    that needs no message and no notice.

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

    # Each of these used to return None outright. They now fall through to the
    # invite instead, which is the same thing whenever there is no invite --
    # and the only change in what a panel showing something shows.
    if game is not None:
        if game.status == "active":
            return game
        if game.status in OVER_GAME:
            # The result has ten minutes, and only for somebody who has not
            # put it away.
            seat = game.seat_of(current_user.id)
            if (game.updated_at > datetime.utcnow() - CLOSE_AFTER
                    and seat is not None and not game.closed_for(seat)):
                return game

    return pending_invite_with(other_id)


# How long a finished game's result stays in the chat before the panel puts
# itself away.
CLOSE_AFTER = timedelta(minutes=10)

# How recently a player must have been at a board to count as still there.
# The channel re-issues at least every LIVE_WAIT, so this allows two chances
# to say hello before anybody is called absent.
PRESENT_WITHIN = timedelta(seconds=50)

# How long they have to be gone before the other player may take the win.
# Nothing happens on its own when this passes: it only unlocks the offer.
CLAIM_AFTER = timedelta(minutes=5)

# How long an unanswered invite goes on counting as a game in progress.
#
# The backstop, not the fix: an invite is answerable from the lobby and from
# the chat panel, both built from the games row alone, so one should never
# need this. It is here because the fault it guards against was not one
# anybody predicted -- eight invites on live were committed with no message
# and no notice, which left four pairs unable to play each other at all, with
# nothing in the interface to press. Whatever does that next, this means it
# stops mattering after a week.
#
# CLAIM_AFTER's rule holds here too: nothing happens on its own when this
# passes. The stale invite simply stops blocking, and is cleared when one of
# them acts -- see game_new.
INVITE_TTL = timedelta(days=7)


def mark_present(game, user_id, here=True):
    """Record whether this player has this board on screen, now.

    Called once at the start of a request, never from the probe loop: a held
    request outlives the tab that made it, so the only honest moment to say
    "they are here" is when the request arrives. A ghost stamps once and then
    ages out, which is exactly what should happen.

    `here=False` is a tab saying the board is no longer on screen -- hidden
    behind another window, navigated away from, or being unloaded. It lowers
    the flag and deliberately leaves the stamp alone: how long they have been
    gone is measured from when they were last really there, not from when they
    told us they were going.
    """
    if game is None or game.status != "active":
        return False
    seat = game.seat_of(user_id)
    if seat is None:
        return False

    if not here:
        if not game.here_for(seat):
            return False
        game.mark_here(seat, False)
        return True

    was = game.seen_for(seat)
    now = datetime.utcnow()
    # Only when it would actually change the answer. Writing on every request
    # would be a write every twenty-five seconds per open board for no gain.
    # The flag being down is such a change, though: somebody has just come
    # back, and that must not wait for the stamp to age out first.
    if (game.here_for(seat) and was is not None
            and now - was < PRESENT_WITHIN / 2):
        return False
    game.mark_seen(seat, now)
    game.mark_here(seat, True)
    return True


def away_for(game, seat):
    """How long that seat has been away from this board, or None if present.

    A read, deliberately. The thing this replaced wrote to the database during
    a page render, because a forfeit had to be noticed by somebody looking at
    it. Nothing is decided here any more -- the waiting player decides -- so
    this only has to answer a question.

    The flag first and the clock second. Presence used to be inferred from
    silence alone, and silence turned out to mean nothing: the live channel
    polls just as happily from a tab nobody is looking at, so "is here" stuck
    for anybody who walked away without closing it. A tab now says when the
    board leaves the screen, and PRESENT_WITHIN is left as what it should
    always have been -- the backstop for the cases that cannot say anything,
    like a crash, a dead battery or lost wifi.
    """
    seen = game.seen_for(seat)
    if seen is None:
        # Nothing has ever been recorded: either they have not opened the
        # board since this shipped, or they never had it open. Both are away.
        return datetime.utcnow() - game.updated_at
    gone = datetime.utcnow() - seen
    if not game.here_for(seat):
        return gone
    return gone if gone > PRESENT_WITHIN else None


def claimable_by(game, seat):
    """Whether this seat may take the win because the other one left.

    Three things have to hold at once, and the third is belt and braces: a
    claim must not land in the gap between somebody making a move and their
    presence being recorded.
    """
    if game is None or game.status != "active":
        return False

    owing = owes_action(game)
    if owing is None or owing == seat:
        return False          # nothing is owed, or it is owed by the claimer

    gone = away_for(game, owing)
    if gone is None or gone < CLAIM_AFTER:
        return False
    return datetime.utcnow() - game.updated_at >= CLAIM_AFTER


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
        # Says so rather than swallowing it. For an invite this line IS the
        # Accept button, and a game committed without one locks both players
        # out of playing each other at all -- which is what happened on live
        # in October. The caller decides what that is worth; game_new treats
        # it as fatal.
        return False
    db.session.add(Message(user_id=actor_id, match_id=match.id,
                           game_id=game.id, event=event, body=body))
    return True


def award_walkover(game, seat):
    """End a game because the other player left, and say so in the chat.

    What the automatic forfeit used to do, minus the automatic: the outcome,
    the constraint it satisfies and the message are all unchanged, so a game
    that ends this way reads exactly as one that ended that way. The
    difference is that somebody chose it.
    """
    winner_id = game.user_in_seat(seat)
    loser = db.session.get(User, game.user_in_seat(1 - seat))
    winner = db.session.get(User, winner_id)
    label = rules.LABELS.get(game.kind, game.kind)

    # The player who claimed it, not the one who left: this records who
    # acted, and walking away is not an act.
    finish_game(game, "abandoned", winner_id, ended_by=winner_id)
    game.updated_at = datetime.utcnow()
    game_event(game, "abandoned", winner_id,
               f"{loser.name if loser else 'They'} did not come back, so "
               f"{winner.name if winner else 'the other player'} won {label}.")
    db.session.commit()
    return True


def game_token(game, seat=None):
    """What the poller compares to decide whether to swap the pane.

    Anything that changes what the pane should look like has to move this, and
    nothing else may: re-swapping identical markup would wipe a half-made
    chess move, which is a two-click interaction.

    Hence the fourth part being a flag rather than a time. Whether the other
    player is here changes the pane, so it belongs in here; *when* they were
    last seen changes every twenty-five seconds, and putting that in would
    make the waiting request return constantly instead of waiting.
    """
    if game is None:
        return "none"
    base = f"{game.id}:{game.version}:{game.status}"
    if seat is None or game.status != "active":
        # Only a game in play has anybody to be present at it, so a finished
        # result keeps the three-part token it always had.
        return base
    return base + (":1" if away_for(game, 1 - seat) is not None else ":0")


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


def finish_game(game, outcome, winner_id=None, ended_by=None):
    game.status = "finished"
    game.outcome = outcome
    game.winner_id = winner_id
    game.ended_by = ended_by
    game.turn_id = None


@app.route("/games")
@login_required
def game_lobby():
    # Two queries rather than one, so the cut happens in the database. Reading
    # every finished game and then showing five is the same cost with a
    # tidier page, and the per-row lookup below is what makes it hurt.
    playing = db.or_(Game.player_a == current_user.id,
                     Game.player_b == current_user.id)
    live = db.session.scalars(
        db.select(Game)
        .where(playing, Game.status.in_(LIVE_GAME))
        .order_by(Game.updated_at.desc())
    ).all()
    over = db.session.scalars(
        db.select(Game)
        .where(playing, Game.status.not_in(LIVE_GAME))
        .order_by(Game.updated_at.desc())
        .limit(GAMES_DONE)
    ).all()
    mine = live + over

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
            # An unfinished game is one you can go back to; a finished one is
            # a result. Listing them together made a game look resumable when
            # it was over.
            "resumable": game.status in LIVE_GAME,
            # Past INVITE_TTL, so it no longer stands between these two. Said
            # on the card rather than left looking live, because the row is
            # still there and the two buttons beside it still work on it.
            "expired": (game.status == "invited"
                        and game.created_at
                        <= datetime.utcnow() - INVITE_TTL),
            "opponent_away": (away_for(game, 1 - seat)
                              if game.status == "active" else None),
        })

    waiting = seek_row()
    return render_template(
        "games.html",
        rows=rows,
        resumable=[r for r in rows if r["resumable"]],
        done=[r for r in rows if not r["resumable"]],
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

    # Nothing live, but there may still be an invite old enough that
    # live_game_with no longer counts it. Cleared here rather than on a read
    # path: a stale invite stops blocking on its own, but it is only written
    # off when somebody does something about it. Doing it here also keeps the
    # invariant pane_game relies on -- one invited-or-active game per pair.
    fossil = pending_invite_with(opponent_id)
    if fossil is not None:
        fossil.status = "declined"
        fossil.turn_id = None
        fossil.ended_by = current_user.id
        fossil.updated_at = datetime.utcnow()
        game_event(fossil, "declined", current_user.id,
                   f"{current_user.name} let the "
                   f"{rules.LABELS.get(fossil.kind, fossil.kind)} invite "
                   "expire and started a new game.")

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
    # This line is the Accept button. An invite committed without it is one
    # nobody can answer, and because an unanswered invite counts as a game in
    # progress, it locks both players out of playing each other at all -- the
    # fault that reached live in October. So a failure here throws the invite
    # away rather than committing half of it.
    said = game_event(game, "invited", current_user.id,
                      f"{current_user.name} invited "
                      f"{other.name if other else 'them'} "
                      f"to {rules.LABELS[kind]}.")
    if not said:
        db.session.rollback()
        flash("That invite could not be sent. Nothing was started.")
        return redirect(url_for("chat", kind="match", target_id=opponent_id))

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


@app.route(
    "/games/<int:game_id>/<any(accept, decline, withdraw, resign):action>",
    methods=["POST"])
@login_required
def game_action(game_id, action):
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    if action == "withdraw":
        # The way back out for whoever sent it. Until now only the invitee
        # could resolve an invite, so a sender who changed their mind -- or
        # whose invite the other person never answered -- had no way to stop
        # it standing between them.
        if game.status != "invited":
            flash("That invite has already been answered.")
        elif game.player_a != current_user.id:
            flash("Only the person who sent it can take it back.")
        else:
            game.status = "declined"
            game.turn_id = None
            # The sender. Which is what tells a withdrawal apart from a
            # decline later: the same status, the actors the other way round.
            game.ended_by = current_user.id
            game.updated_at = datetime.utcnow()
            label = rules.LABELS.get(game.kind, game.kind)
            game_event(game, "declined", current_user.id,
                       f"{current_user.name} took the {label} invite back.")
            # The invitee's bell stops offering a game that is no longer on
            # the table. By game, not by notice id: the notice belongs to
            # them, so this request has no hidden field carrying it.
            notices.close_invite(game.player_b, game.id)
            db.session.commit()

    elif action in ("accept", "decline"):
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
            game.ended_by = current_user.id
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
            finish_game(game, "resigned", winner_id,
                        ended_by=current_user.id)
            game.updated_at = datetime.utcnow()
            game_event(game, "ended", current_user.id,
                       f"{current_user.name} resigned, so "
                       f"{winner.name if winner else 'the other player'} "
                       f"won {label}.")
            db.session.commit()

    # The lobby answers invites too now, and sending somebody to the chat from
    # there would be answering one question with a different page.
    if request.form.get("back") == "games":
        return redirect(url_for("game_lobby"))
    return game_chat(game, seat)


@app.route("/games/<int:game_id>/claim", methods=["POST"])
@login_required
def game_claim(game_id):
    """Take the win because the other player left.

    Offered, never taken: nothing in the app ends a game on a timer any more,
    so a game can be paused for as long as the two of them like. This is the
    way out for somebody who would otherwise be waiting for a move that is
    not coming.
    """
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    if not claimable_by(game, seat):
        # Re-checked here and not merely hidden in the template: the button is
        # only shown when it applies, and the rule has to hold anyway.
        flash("They are still in the game. Give them a moment.")
    else:
        award_walkover(game, seat)

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
        # Campus-wide only. A meetup arranged inside a conversation belongs to
        # the two people in it and has no business on a public board -- this
        # is the filter that makes "private" mean anything.
        .where(GamePost.mode == "inperson", GamePost.status == "open",
               GamePost.audience == "campus", still_on())
        # Most recently posted first, and only a screenful: the board is for
        # finding something to do now, not an archive of everything anybody
        # ever suggested.
        .order_by(GamePost.created_at.desc())
        .limit(FIND_POSTS)
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

    # How many others are waiting per activity, so the page is honest about
    # odds. Keyed by the folded name, because "Chess" and "chess" are one
    # queue now and counting them apart would say otherwise.
    counts, spelled = queue_waiting(current_user.id)

    presets = [a["name"] for a in activity_catalogue(in_person_only=True,
                                                     presets_only=True)]

    # What people have typed in, so a write-in is something you can join
    # rather than a word you have to guess. The presets are left out: they are
    # in the picker already, with their own count beside them.
    picked = {fold_name(name) for name in presets}
    elsewhere = sorted(
        ({"name": spelled[key], "count": counts[key]}
         for key in counts if key not in picked),
        key=lambda row: (-row["count"], row["name"].lower()),
    )

    # Once for the whole list, not once per card.
    rooms = chats_for([p.id for p in posts])
    pins = flags_for([p.id for p in posts])

    return render_template(
        "find.html",
        # The picker is the focused set, plus a write-in. It used to have none
        # -- pairing matched two people on the string as typed, so one typing
        # "basketball" and another "Basketball" would have waited for ever
        # without meeting. fold_name is what made it safe to offer.
        games=presets,
        catalogue=activity_catalogue(presets_only=True),
        when_hours=WHEN_HOURS,
        when_minutes=WHEN_MINUTES,
        when_meridiem=WHEN_MERIDIEM,
        waiting=waiting,
        counts=counts,
        elsewhere=elsewhere,
        other=OTHER,
        custom_max=CUSTOM_GAME_MAX,
        posts=[post_payload(p, chats=rooms, flags=pins) for p in posts],
        pairings=pairings,
    )


@app.route("/find/queue", methods=["POST"])
@login_required
def find_queue():
    chosen = request.form.get("game", "")

    # A write-in, the same way the meetup form on this page takes one: the
    # sentinel in the picker, the words in a field beside it. Typing a name
    # nobody recognises only counts when it was typed on purpose -- sending an
    # unknown name as the picker's value is still refused, because that is a
    # form being posted by hand rather than a student saying what they want.
    if chosen == OTHER:
        typed = request.form.get("custom_game", "").strip()
        if not typed:
            flash("Type what you want to play.")
            return redirect(url_for("find_game"))
        if len(typed) > CUSTOM_GAME_MAX:
            flash(f"Keep the activity under {CUSTOM_GAME_MAX} characters.")
            return redirect(url_for("find_game"))
        game = queue_name(typed)
    else:
        activity = find_activity(chosen)
        if activity is None or not activity.in_person:
            flash("Pick an activity from the list.")
            return redirect(url_for("find_game"))
        game = activity.name

    # Folded, so the two students only have to mean the same activity rather
    # than type it identically. The stored spelling is left alone.
    folded = fold_name(game)

    partner = db.session.scalars(
        db.select(GameQueue)
        .where(db.func.lower(GameQueue.game) == folded,
               GameQueue.user_id != current_user.id)
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
        return back_to("find_game")

    other_id = (pairing.user_hi if pairing.user_lo == current_user.id
                else pairing.user_lo)

    # Not while you are still playing. The lobby links a game to its chat --
    # the board lives nowhere else -- and game_home only resolves an *open*
    # pairing, falling back to a friend chat two strangers do not have. So
    # closing here would leave both of you with a board you cannot open and a
    # "one game at a time" rule you cannot clear, which is exactly the frozen
    # state a pair of students got stuck in before. Resigning is the way out,
    # and it is a button on the board.
    playing = live_game_with(other_id)
    if playing is not None:
        flash("Finish or resign your game first, then close the chat.")
        return back_to("find_game")

    pairing.status = "closed"
    db.session.commit()
    flash("Chat closed.")
    return back_to("find_game")


@app.route("/posts/new", methods=["POST"])
@login_required
def post_new():
    # Where this was posted from, if it was posted from a conversation. The
    # Meet up button in a one-on-one sends these; /find and the campus room
    # send neither and behave exactly as they always have.
    #
    # Resolved through chat_target, which is the same check the chat page
    # itself passes -- so this cannot be used to put a meetup into somebody
    # else's conversation.
    home = card_home()
    here, from_id, here_parent = home if home else (None, None, None)

    # Named a conversation and did not get one. Refused rather than quietly
    # posted to the campus room: the difference between those two outcomes is
    # the difference between a private arrangement and a public announcement.
    if request.form.get("from_kind") and here is None:
        flash("That conversation isn't available to you.")
        return redirect(url_for("find_game"))

    game = request.form.get("game", "").strip()
    mode = request.form.get("mode", "online")
    note = request.form.get("note", "").strip()
    location = request.form.get("location", "").strip()
    # The day in words, and the time as three separate choices. Splitting
    # them is what lets the time be strict without making the field itself
    # mandatory: "Tonight" with no hour is still a perfectly good answer.
    # The date, and it is required: a meetup whose "when" is a sentence has no
    # day its chat can end after, and nothing can be sorted by it. The typed
    # day this replaced is gone from the forms.
    #
    # Parsed rather than trusted, so a malformed one is refused below instead
    # of quietly becoming no date at all.
    on_text = request.form.get("when_on", "").strip()
    on, on_bad = None, False
    if on_text:
        try:
            on = datetime.strptime(on_text, "%Y-%m-%d").date()
        except ValueError:
            on_bad = True
    hour = request.form.get("when_hour", "").strip()
    minute = request.form.get("when_minute", "").strip() or WHEN_MINUTES[0]
    ampm = request.form.get("when_ampm", "").strip()
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

    if here is not None:
        # In person by definition -- the button exists to arrange meeting --
        # and between the two people already in the conversation, so neither
        # is a question the form has to ask. Set before the checks below, so
        # the location rules are the in-person ones.
        mode = "inperson"
        players = 2

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
    # Only once an hour has been picked. Asking for AM or PM when nobody has
    # named a time would turn an optional field into a required one.
    elif hour and not ampm:
        flash("Please select AM or PM.")
    elif ampm and not hour:
        flash("Pick an hour to go with the AM or PM.")
    elif hour and (hour not in WHEN_HOURS or minute not in WHEN_MINUTES
                   or ampm not in WHEN_MERIDIEM):
        # Only reachable by posting the form by hand.
        flash("That isn't a time we offer.")
    elif on_bad:
        flash("That isn't a date we can read.")
    elif on is None:
        # Required, so its chat has a day to end after and so two meetups can
        # be told apart by when they are.
        flash("Pick the date of the meetup.")
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
            when_text=when_label(on, hour, minute, ampm),
            when_on=on,
            # Kept now, rather than only rendered into when_text. Without it
            # nothing can tell a meetup that has happened from one that has
            # not. Null when no hour was picked, which the form allows.
            starts_at=(boston_to_utc(on, hour, minute, ampm)
                       if on and hour and ampm else None),
            max_players=players,
            audience="chat" if here else "campus",
        )
        db.session.add(post)
        db.session.flush()

        # Its own chat, so the people going have somewhere to talk that is not
        # the campus-wide room. Membership is this post's RSVP list, and the
        # author is already on it.
        #
        # Not for one arranged in a conversation: make_meetup_chat exists to
        # give the people going somewhere to talk, and these two are already
        # doing exactly that in the chat they posted it from.
        if here is None:
            make_meetup_chat(post)

        # The post lives in the conversation as a message, so one timeline and
        # one poller cover both chatter and posts. attach_parent puts it in
        # whichever conversation that is.
        summary = f"Looking for {post.game}"
        if post.mode == "inperson":
            summary += " in person"
        said = Message(user_id=current_user.id, post_id=post.id, body=summary)
        attach_parent(said, here or COMMUNITY, here_parent)
        db.session.add(said)
        # The author is the first one going.
        db.session.add(GamePostRsvp(post_id=post.id, user_id=current_user.id))
        # An in-person meetup goes on the campus map too, seen only by the
        # people who are in until its author makes it public.
        if post.mode == "inperson":
            make_event_flag(post)
        db.session.commit()
        flash("Asked them to meet up." if here
              else "Posted to Community Chat.")
        if here is not None:
            return redirect(url_for("chat", kind=here, target_id=from_id))

    # A refused post from a conversation goes back to it, so the flash saying
    # what was wrong lands on the page the form is on.
    if here is not None:
        return redirect(url_for("chat", kind=here, target_id=from_id))
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
        # The meetup's chat, if it has one. Posts made before this feature do
        # not, and nothing below needs them to.
        room = db.session.scalar(db.select(Chat).filter_by(post_id=post_id))

        rsvp = db.session.get(GamePostRsvp, (post_id, current_user.id))
        if rsvp is not None:
            db.session.delete(rsvp)
            # Out of the meetup is out of its chat. chat_target already
            # refuses a group chat to a non-member, so removing the row is the
            # whole of closing it for them.
            if room is not None:
                seat = db.session.get(ChatMember, (current_user.id, room.id))
                if seat is not None:
                    db.session.delete(seat)
                    # A plain message rather than an event, the same choice
                    # game_new makes and for the same reason: Message.event is
                    # a CHECK constraint tied to a game, and widening it for a
                    # sentence would be a migration for a sentence.
                    said = Message(user_id=current_user.id,
                                   chat_id=room.id,
                                   body=f"{current_user.name} left the meetup.")
                    db.session.add(said)
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
                # And into its chat. Not while it has ended, or cancelling a
                # meetup and then RSVPing would put somebody into a closed
                # conversation.
                if room is not None and not chat_ended(room):
                    db.session.add(ChatMember(user_id=current_user.id,
                                              chat_id=room.id))
                    # So the people already in it see somebody arrive without
                    # refreshing: the live channel carries this like any other
                    # message.
                    db.session.add(Message(
                        user_id=current_user.id, chat_id=room.id,
                        body=f"{current_user.name} joined the meetup."))
                db.session.commit()
                flash(f"You're in for {post.game}.")

    # Pressed on a card inside a conversation: back to it, rather than to the
    # campus room they may never have been in.
    home = card_home()
    if home is not None:
        return redirect(url_for("chat", kind=home[0], target_id=home[1]))
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
        # Its map flag comes down with it.
        db.session.execute(db.delete(MapFlag).where(MapFlag.post_id == post.id))
        db.session.commit()
        flash(f"{post.game} cancelled.")

    # Pressed on a card inside a conversation: back to it, rather than to the
    # campus room they may never have been in.
    home = card_home()
    if home is not None:
        return redirect(url_for("chat", kind=home[0], target_id=home[1]))
    if request.form.get("back") == "find":
        return redirect(url_for("find_game"))
    return redirect(url_for("chat", kind=COMMUNITY, target_id=0))


@app.route("/clubs")
@login_required
def clubs():
    """Every club, with the ones the student's answers point at in a band
    above them.

    This used to argue the opposite -- that a band would put the same clubs
    on the page twice and make the one-card-per-club count meaningless -- and
    both halves of that are still true. The band was asked for anyway, so the
    clubs are drawn twice on purpose and the two suites that count cards
    expect clubs + recommended rather than clubs. The reason and the badge
    stay on the card either way, so a club carries its "because you picked"
    wherever it is shown.
    """
    joined = joined_clubs(current_user.id)
    # Keyed by club, because the template asks "is this one suggested, and
    # why" per card rather than walking a second list.
    picks = {row["club_id"]: row for row in
             matching.recommend_clubs(current_user.id, limit=CLUB_PICKS)}
    everything = db.session.scalars(db.select(Club).order_by(Club.name)).all()
    # Suggested first, best first; everything else keeps the alphabet. Both
    # halves come out of the one query above, so this is a sort, not a second
    # trip to the database.
    ordered = (
        sorted((c for c in everything if c.id in picks),
               key=lambda c: (-picks[c.id]["score"], c.name))
        + [c for c in everything if c.id not in picks]
    )
    return render_template(
        "clubs.html",
        clubs=ordered,
        # The same clubs, bucketed into the categories in club_catalog.py
        # (each keeps the order above: suggestions first, then A to Z).
        groups=club_catalog.grouped(ordered),
        # The band at the top. Best first, which is the order `ordered`
        # already puts them in.
        recommended=[c for c in ordered if c.id in picks],
        picks=picks,
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


# ----------------------------------------------------------- hub requests
# The catalogue is seeded by hand, so until now the only way to get a hub was
# to know somebody who could edit the hub list in db_ops.py. These three routes are the
# way in for everybody else: ask, and one person decides.

HUB_NAME_MAX = 60
HUB_PITCH_MAX = 300
# How many interests an admin may tag a new hub with. Enough to place it,
# few enough that a hub cannot be tagged with everything and out-rank the
# seeded ones for every student at once.
HUB_TAGS_MAX = 6


def admin_ids():
    """Everyone who reviews requests. Usually one person."""
    return db.session.scalars(
        db.select(User.id).filter_by(is_admin=True)).all()


def not_admin():
    """The redirect to send an ordinary student away with, or None.

    A flash and a redirect rather than a 403: no page in this app aborts, and
    somebody who followed a link they should not have is better told what
    happened than shown a bare error. The sentence names who may do this, the
    way the chat routes' do.
    """
    if current_user.is_admin:
        return None
    flash("That page is for whoever looks after the site.")
    return redirect(url_for("clubs"))


@app.route("/hubs/request", methods=["POST"])
@login_required
def hub_request():
    name = request.form.get("name", "").strip()
    pitch = request.form.get("pitch", "").strip()

    if not name:
        flash("Give the hub a name.")
        return redirect(url_for("clubs"))
    if len(name) > HUB_NAME_MAX:
        flash(f"Hub names are limited to {HUB_NAME_MAX} characters.")
        return redirect(url_for("clubs"))
    if not pitch:
        flash("Say what the hub is for, so there is something to decide on.")
        return redirect(url_for("clubs"))
    if len(pitch) > HUB_PITCH_MAX:
        flash(f"Keep it under {HUB_PITCH_MAX} characters.")
        return redirect(url_for("clubs"))

    # Already a hub. Checked case-insensitively, because "chess" and "Chess"
    # are the same request and only one of them collides with the unique name.
    taken = db.session.scalar(
        db.select(Club).where(db.func.lower(Club.name) == name.lower()))
    if taken is not None:
        flash(f"{taken.name} already exists. You can join it from here.")
        return redirect(url_for("clubs"))

    # Insert and catch, rather than looking first: two students asking for the
    # same hub in the same moment would both pass a pre-check. The partial
    # unique index is what actually decides, and it only covers pending rows,
    # so something turned down last term can be asked for again.
    db.session.add(HubRequest(name=name, pitch=pitch,
                              requested_by=current_user.id))
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash(f"{name} has already been requested and is waiting for review.")
        return redirect(url_for("clubs"))

    for admin_id in admin_ids():
        notices.hub_requested(admin_id, name)
    db.session.commit()
    flash(f"Thanks. {name} has been sent for review.")
    return redirect(url_for("clubs"))


@app.route("/admin")
@login_required
def admin():
    """Everything one person looks after, on one page.

    Hub requests and who may post were two pages, which meant two links in
    the account menu and two places to look for "the admin bit". They are
    two sections now.
    """
    away = not_admin()
    if away:
        return away

    term = request.args.get("q", "").strip()
    who = db.select(User).order_by(User.name)
    if term:
        who = who.where(User.name.ilike(f"%{term}%")
                        | User.email.ilike(f"%{term}%"))
    else:
        # Without a search, only the people who already hold something: a
        # list of every account on campus is not a useful page.
        who = who.where(User.is_admin | User.can_post_fixtures)

    rows = db.session.scalars(
        db.select(HubRequest).order_by(HubRequest.id.desc())).all()
    people = {u.id: u for u in db.session.scalars(
        db.select(User).where(User.id.in_(
            [r.requested_by for r in rows] or [0])))}
    return render_template(
        "admin.html",
        pending=[r for r in rows if r.status == "pending"],
        decided=[r for r in rows if r.status != "pending"],
        asked_by=people,
        people=db.session.scalars(who).all(),
        term=term,
        # What a new hub may be tagged with. The question a hub is placed by
        # is the one whose answers people are shown -- so the admin is
        # choosing from the same words a student saw.
        tags=db.session.scalars(
            db.select(QuestionOption)
            .join(Question, Question.id == QuestionOption.question_id)
            .where(Question.key == matching.SHOWN_QUESTION)
            .order_by(QuestionOption.sort_order)).all(),
        tag_max=HUB_TAGS_MAX,
        **admin_reports(),
        **admin_db_context(),
    )


def admin_reports():
    """The report queue, split the way admin() splits hub requests.

    `seen_before` answers the first question anybody asks about a complaint
    -- has this person been reported before? -- counted per account rather
    than left to be eyeballed down the page.
    """
    rows = db.session.scalars(
        db.select(Report).order_by(Report.id.desc())).all()
    seen_before = {}
    for row in rows:
        if row.about_user_id is not None:
            seen_before[row.about_user_id] = (
                seen_before.get(row.about_user_id, 0) + 1)
    return {
        "reports_new": [r for r in rows if r.status == "new"],
        "reports_done": [r for r in rows if r.status != "new"][:20],
        "seen_before": seen_before,
    }


def admin_db_context():
    """The Database section's facts: where we are, and what it can run.

    Host and database name only -- never the URL, never the user, never the
    password. Nothing here opens a connection: the name comes from the URL
    the app already holds, so a database that is down does not stop the
    admin page rendering. Whether dev and live agree is a command you run,
    not something computed on every page load.
    """
    import db_ops

    try:
        here, host = db_ops.where(False)
        live_name, live_host = db_ops.where(True)
    except db_ops.Refuse as exc:
        return {"db_here": "?", "db_host": "?", "db_live": None,
                "db_why": str(exc), "db_commands": [], "db_job": DB_JOB}
    return {
        "db_here": here,
        "db_host": host,
        "db_live": live_name,
        "db_live_host": live_host,
        "db_why": None,
        "db_commands": db_commands(),
        "db_job": DB_JOB,
    }


@app.route("/admin/hubs/<int:request_id>/approve", methods=["POST"])
@login_required
def hub_approve(request_id):
    away = not_admin()
    if away:
        return away

    asked = db.session.get(HubRequest, request_id)
    if asked is None or asked.status != "pending":
        flash("That request has already been decided.")
        return redirect(url_for("admin"))

    picked = [int(v) for v in request.form.getlist("tag") if v.isdigit()]
    # Refused rather than allowed through, because an untagged hub is not
    # merely ranked low: recommend_clubs reaches clubs only through club_tags,
    # so a hub with none can never be suggested to anybody, ever. It would sit
    # in the browse list looking fine and quietly never find its people.
    if not picked:
        flash("Pick at least one interest, or the hub will never be "
              "suggested to anyone.")
        return redirect(url_for("admin"))
    if len(picked) > HUB_TAGS_MAX:
        flash(f"Pick at most {HUB_TAGS_MAX} interests.")
        return redirect(url_for("admin"))

    # Checked against the offered set rather than trusted: these are small
    # integers in a posted form, and a tag naming another question's option
    # would be accepted by the composite foreign key only if the question id
    # agreed -- so the question id comes from the row, never from the form.
    options = db.session.scalars(
        db.select(QuestionOption)
        .join(Question, Question.id == QuestionOption.question_id)
        .where(Question.key == matching.SHOWN_QUESTION,
               QuestionOption.id.in_(picked))).all()
    if len(options) != len(set(picked)):
        flash("That is not one of the interests on offer.")
        return redirect(url_for("admin"))

    club = Club(name=asked.name, description=asked.pitch)
    db.session.add(club)
    try:
        db.session.flush()
    except IntegrityError:
        # Somebody seeded a hub with this name between the request and now.
        db.session.rollback()
        flash(f"{asked.name} already exists as a hub.")
        return redirect(url_for("admin"))

    for option in options:
        db.session.add(ClubTag(club_id=club.id,
                               question_id=option.question_id,
                               option_id=option.id))

    asked.status = "approved"
    asked.decided_by = current_user.id
    asked.decided_at = datetime.utcnow()
    asked.club_id = club.id
    notices.hub_decided(asked.requested_by, club.name, True)
    db.session.commit()
    flash(f"{club.name} is open, tagged with "
          f"{', '.join(o.label for o in options)}.")
    return redirect(url_for("admin"))


@app.route("/admin/hubs/<int:request_id>/decline", methods=["POST"])
@login_required
def hub_decline(request_id):
    away = not_admin()
    if away:
        return away

    asked = db.session.get(HubRequest, request_id)
    if asked is None or asked.status != "pending":
        flash("That request has already been decided.")
        return redirect(url_for("admin"))

    asked.status = "declined"
    asked.decided_by = current_user.id
    asked.decided_at = datetime.utcnow()
    asked.note = request.form.get("note", "").strip()[:HUB_PITCH_MAX]
    notices.hub_decided(asked.requested_by, asked.name, False, asked.note)
    db.session.commit()
    flash(f"{asked.name} was turned down.")
    return redirect(url_for("admin"))


# ------------------------------------------------------------- reporting
# Three things a student might need to tell us -- somebody behaving badly,
# something broken, or just a message -- and one queue for all three,
# because whoever looks after the site wants one list and not three pages.
#
# The rule that matters in here is that nothing is quoted back to anybody
# who could not already read it. Every target is re-fetched and
# access-checked on the way in AND on the way out, and the copy stored for
# the admin is built from the row the server fetched, never from the form.
# Without that, /report?message=<guess> is a way to read other people's
# private messages on a page whose whole purpose is safety.

REPORT_DETAIL_MAX = 600


def message_home(message):
    """The conversation a message hangs off, as (kind, target_id).

    Read off models.MESSAGE_PARENTS rather than an if/elif chain, so a new
    chat kind does not mean remembering this function separately. A match's
    target is the OTHER student, because that is what the chat route is
    keyed by.
    """
    for kind, column in MESSAGE_PARENTS.items():
        value = getattr(message, column.key)
        if value is None:
            continue
        if kind == "match":
            match = db.session.get(Match, value)
            if match is None:
                return None, None
            other = (match.user_hi if match.user_lo == current_user.id
                     else match.user_lo)
            return kind, other
        return kind, value
    if message.room:
        return COMMUNITY, 0
    return None, None


def report_subject():
    """Resolve what is being reported, or (None, ...) if it may not be seen.

    Returns (column, row, about_user, quoted, seen_in). `column` is one of
    REPORT_CONTENT_COLUMNS or None.

    Every branch checks readability through the check that already governs
    the thing itself -- chat_target for a message, the meetup's audience,
    map_flag_visible_to for a pin -- so this cannot become a second,
    weaker door to the same content.
    """
    raw_message = request.values.get("message", "")
    raw_post = request.values.get("post", "")
    raw_flag = request.values.get("flag", "")

    if raw_message.isdigit():
        message = db.session.get(Message, int(raw_message))
        if message is None:
            return None, None, None, None, None
        kind, target_id = message_home(message)
        if kind is None:
            return None, None, None, None, None
        parent, title = chat_target(kind, target_id)
        if parent is None:
            return None, None, None, None, None
        author = db.session.get(User, message.user_id)
        return ("message_id", message, author, message.body, title)

    if raw_post.isdigit():
        post = db.session.get(GamePost, int(raw_post))
        if post is None:
            return None, None, None, None, None
        # A chat-only meetup is readable by the people in its chat, which is
        # its RSVP list. Anything else is campus-wide.
        if post.audience != "campus":
            rsvp = db.session.get(GamePostRsvp, (post.id, current_user.id))
            if rsvp is None and post.author_id != current_user.id:
                return None, None, None, None, None
        author = db.session.get(User, post.author_id)
        said = " ".join(p for p in (post.game, post.note) if p)
        return ("post_id", post, author, said, "a meetup")

    if raw_flag:
        flag = db.session.scalar(
            db.select(MapFlag).where(MapFlag.public_id == raw_flag,
                                     map_flag_visible_to(current_user.id)))
        if flag is None:
            return None, None, None, None, None
        owner = db.session.get(User, flag.owner_id)
        said = " ".join(p for p in (flag.title, flag.description) if p)
        return ("flag_id", flag, owner, said, flag.location_name)

    return None, None, None, None, None


@app.route("/report", methods=["GET", "POST"])
@login_required
def report_new():
    """Report somebody, report a bug, or send us a message.

    One page for all three. The kind comes from the form on a POST and from
    the query on a GET, so the same template can arrive pre-aimed at a
    person or a message from a Report link, or empty from the account menu.
    """
    column, row, about_user, quoted, seen_in = report_subject()

    kind = request.values.get("kind", "")
    if kind not in REPORT_KINDS:
        # Anything named makes this a complaint. Inferring from `about`
        # alone sent somebody who clicked Report beside a message to the
        # "get in touch" form with the message silently dropped, which is
        # the kind of quiet wrong thing that only shows up in the queue a
        # week later as a report about nothing.
        named = (request.values.get("about") or column is not None)
        kind = "abuse" if named else "contact"

    # A person can be named directly, or inferred from the thing reported.
    raw_about = request.values.get("about", "")
    if about_user is None and raw_about.isdigit():
        about_user = db.session.get(User, int(raw_about))

    # Reached from the menu with nobody named: offer the people this student
    # already knows, rather than a search over every account. The Report
    # link beside a message or a meetup is the way to name anybody else, and
    # it carries the thing itself along with the person -- which is what an
    # admin actually needs. Deliberately not a name search: a report form
    # that will look anybody up is an account enumerator.
    choices = []
    if kind == "abuse" and about_user is None:
        choices = [(f["user_id"], f["name"])
                   for f in matching.active_matches(current_user.id)]

    if kind == "abuse" and about_user is not None \
            and about_user.id == current_user.id:
        flash("You cannot report yourself.")
        return redirect(url_for("index"))

    if request.method == "POST":
        detail = request.form.get("detail", "").strip()
        if kind == "abuse" and about_user is None:
            flash("Choose who you are reporting.")
        elif not detail:
            flash("Please say what happened.")
        elif len(detail) > REPORT_DETAIL_MAX:
            flash(f"Please keep it under {REPORT_DETAIL_MAX} characters.")
        else:
            report = Report(
                kind=kind,
                detail=detail[:REPORT_DETAIL_MAX],
                filed_by=current_user.id,
                filed_by_name=current_user.name,
                # Both built from what the server fetched, never from the
                # form: otherwise the person reporting chooses what the
                # admin reads.
                about_name=about_user.name if kind == "abuse" else None,
                about_user_id=about_user.id if kind == "abuse" else None,
                quoted=quoted if kind == "abuse" else None,
                seen_in=seen_in if kind == "abuse" else None,
            )
            if kind == "abuse" and column is not None:
                setattr(report, column, row.id)
            db.session.add(report)

            what = {"abuse": about_user.name if about_user else "Somebody",
                    "bug": "A problem",
                    "contact": "A message"}[kind]
            for admin_id in admin_ids():
                notices.report_filed(admin_id, what)
            db.session.commit()
            # The sentence the student sees a click after the form, so it
            # keeps the form's register rather than going back to
            # "whoever looks after the site has been told". The second half
            # is a promise the app keeps: report_closed notifies them.
            flash("Thanks for telling us. Someone will take a look, and "
                  "you'll hear back when they have.")
            return dock_back() or redirect(url_for("index"))

    return render_template(
        "report.html",
        kind=kind,
        about_user=about_user,
        quoted=quoted,
        seen_in=seen_in,
        choices=choices,
        detail_max=REPORT_DETAIL_MAX,
        carry={k: request.values.get(k, "") for k in
               ("about", "message", "post", "flag", "back")},
    )


@app.route("/admin/reports/<int:report_id>/<any(actioned, dismissed):action>",
           methods=["POST"])
@login_required
def report_decide(report_id, action):
    """Mark a report dealt with, or looked at and left.

    Deciding does not itself remove anything: there is no hide-this-content
    mechanism in the app, and inventing one is a larger feature than a
    report queue. "Actioned" records that somebody dealt with it; the tools
    to act are the ones that already exist.
    """
    away = not_admin()
    if away:
        return away

    report = db.session.get(Report, report_id)
    if report is None:
        flash("That report no longer exists.")
        return redirect(url_for("admin"))
    if report.status != "new":
        # The idempotence hub_approve has, against a double-submitted form.
        flash("That report has already been dealt with.")
        return redirect(url_for("admin"))

    report.status = action
    report.reviewed_by = current_user.id
    report.reviewed_at = datetime.utcnow()
    report.note = request.form.get("note", "").strip()[:REPORT_DETAIL_MAX]

    # Only when there is still somebody to tell: a report filed by an
    # account that has since been deleted has no reader.
    if report.filed_by is not None:
        said = ("What you reported has been dealt with."
                if action == "actioned"
                else "What you reported has been looked at.")
        notices.report_closed(report.filed_by,
                              f"{said} {report.note}".strip()
                              if report.note else said)
    db.session.commit()
    flash("Thank you. The report is closed.")
    return redirect(url_for("admin"))


# ------------------------------------------------- campus-wide meetups
# Something the university is putting on here, posted by somebody trusted.
# Everybody is told, the campus room carries it, and a pin sits on the map
# until the end of its day.
#
# Called a fixture in the code and a campus-wide meetup in the interface. Not
# a "game", because this file already has Game, GamePost, GamePairing,
# GameQueue, GameSeek and a "game" map category; not a "meetup" either,
# because GamePost is what the interface has always called one of those.

FIXTURE_TITLE_MAX = 80
FIXTURE_SPORT_MAX = 60
FIXTURE_OPPONENT_MAX = 80
FIXTURE_VENUE_MAX = 120
FIXTURE_NOTE_MAX = 300

# Where the university is. Every stored datetime in this app is naive UTC, so
# a kick-off typed as "7:00 PM" has to be converted from here -- and it is a
# real zone rather than a fixed offset because Boston is UTC-5 for half the
# year and UTC-4 for the other half. An hour out on a 7pm fixture is the
# difference between arriving and missing it.
#
# zoneinfo reads the operating system's database, and Windows has none: there
# the `tzdata` package supplies it, which is why it is in requirements.txt.
# The fallback below exists because this is resolved at import -- without it,
# a server missing that package would not start at all, and a whole site
# being down is a much worse failure than an hour's drift on one page.
class _Eastern(tzinfo):
    """US Eastern, by the rule in force since 2007.

    Only a fallback. The rule is law rather than physics -- Congress has
    debated ending the change more than once -- so the packaged database is
    what should normally answer this, and does wherever it is installed.
    """

    @staticmethod
    def _second_sunday(year, month):
        first = datetime(year, month, 1)
        return 1 + (6 - first.weekday()) % 7

    def _summer(self, when):
        # Second Sunday in March to the first Sunday in November, 2am local.
        starts = datetime(when.year, 3, self._second_sunday(when.year, 3) + 7, 2)
        ends = datetime(when.year, 11, self._second_sunday(when.year, 11), 2)
        return starts <= when.replace(tzinfo=None) < ends

    def utcoffset(self, when):
        return timedelta(hours=-4 if when and self._summer(when) else -5)

    def dst(self, when):
        return timedelta(hours=1 if when and self._summer(when) else 0)

    def tzname(self, when):
        return "EDT" if when and self._summer(when) else "EST"


try:
    CAMPUS_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:          # pragma: no cover - depends on the host
    CAMPUS_TZ = _Eastern()


def boston_to_utc(on, hour, minute, ampm):
    """A date and a 12-hour clock time in Boston, as a naive UTC datetime."""
    hour = int(hour) % 12
    if ampm == "PM":
        hour += 12
    local = datetime(on.year, on.month, on.day, hour, int(minute),
                     tzinfo=CAMPUS_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_boston(moment):
    """A naive UTC datetime, back in Boston time. For reading one out."""
    return moment.replace(tzinfo=timezone.utc).astimezone(CAMPUS_TZ)


def fixture_flag_deadline(starts_at):
    """When a fixture's pin comes down: the end of its day in Boston."""
    local = utc_to_boston(starts_at)
    midnight = datetime(local.year, local.month, local.day,
                        23, 59, 59, tzinfo=CAMPUS_TZ) + timedelta(seconds=1)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def not_poster():
    """The redirect to send somebody without the privilege away with, or None.

    not_admin()'s shape exactly: a flash naming who may do this and a
    redirect, never an abort, because no page in this app aborts. Admin is
    deliberately not enough on its own -- the two privileges are separate so
    one can be handed out without the other.
    """
    if current_user.can_post_fixtures:
        return None
    flash("Posting campus-wide meetups is for whoever looks after them.")
    return redirect(url_for("fixtures"))


def make_fixture_flag(fixture):
    """Drop the map pin for a fixture, inside the caller's transaction.

    make_event_flag's shape, with three differences: the point comes from
    whoever posted it rather than from a landmark lookup, it is public from
    the start because the whole campus is being told anyway, and it carries
    fixture_id -- which is what keeps it out of the poster's own flag count.
    """
    now = datetime.utcnow()
    expires = fixture_flag_deadline(fixture.starts_at)
    if expires <= now:
        return None
    flag = MapFlag(
        public_id=str(uuid4()),
        owner_id=fixture.posted_by,
        title=fixture.title[:100],
        description=(f"{fixture.billing()} {fixture.when_text}. "
                     f"{fixture.note or ''}").strip()[:300],
        category="home",
        visibility="public",
        fixture_id=fixture.id,
        location_key="custom",
        location_name=fixture.venue[:120],
        latitude=fixture.latitude,
        longitude=fixture.longitude,
        created_at=now,
        expires_at=expires,
    )
    db.session.add(flag)
    return flag


@app.route("/fixtures")
@login_required
def fixtures():
    """What is coming up, and what has just been. Everybody may read this."""
    now = datetime.utcnow()
    rows = db.session.scalars(
        db.select(Fixture)
        .where(Fixture.starts_at > now - timedelta(days=1))
        .order_by(Fixture.starts_at)
    ).all()
    flags = {
        fixture_id: public_id
        for fixture_id, public_id in db.session.execute(
            db.select(MapFlag.fixture_id, MapFlag.public_id)
            .where(MapFlag.fixture_id.in_([f.id for f in rows] or [0]),
                   MapFlag.expires_at > now))
    }
    return render_template(
        "fixtures.html",
        fixtures=[f for f in rows if f.status == "scheduled"],
        called_off=[f for f in rows if f.status != "scheduled"],
        flags=flags,
        can_post=current_user.can_post_fixtures,
    )


@app.route("/fixtures/new", methods=["GET", "POST"])
@login_required
def fixture_new():
    away = not_poster()
    if away:
        return away

    if request.method == "GET":
        return render_template("fixture_new.html", bounds=MAP_FLAG_BOUNDS,
                               title_max=FIXTURE_TITLE_MAX,
                               places=APPROVED_MAP_LOCATIONS,
                               hours=WHEN_HOURS, minutes=WHEN_MINUTES,
                               meridiem=WHEN_MERIDIEM)

    title = request.form.get("title", "").strip()
    sport = request.form.get("sport", "").strip()
    opponent = request.form.get("opponent", "").strip()
    venue = request.form.get("venue", "").strip()
    note = request.form.get("note", "").strip()
    on_text = request.form.get("when_on", "").strip()
    hour = request.form.get("when_hour", "").strip()
    minute = request.form.get("when_minute", "").strip() or WHEN_MINUTES[0]
    ampm = request.form.get("when_ampm", "").strip()

    on = None
    if on_text:
        try:
            on = datetime.strptime(on_text, "%Y-%m-%d").date()
        except ValueError:
            on = None

    # Read before the checks, so a refused post can hand the point back to
    # the form rather than making somebody place the pin twice.
    try:
        latitude = float(request.form.get("latitude", ""))
        longitude = float(request.form.get("longitude", ""))
    except (TypeError, ValueError):
        latitude = longitude = None

    if not title:
        flash("Give it a title, so people know what it is.")
    elif len(title) > FIXTURE_TITLE_MAX:
        flash(f"Keep the title under {FIXTURE_TITLE_MAX} characters.")
    elif len(sport) > FIXTURE_SPORT_MAX:
        flash(f"Keep the sport under {FIXTURE_SPORT_MAX} characters.")
    elif len(opponent) > FIXTURE_OPPONENT_MAX:
        flash(f"Keep the opponent under {FIXTURE_OPPONENT_MAX} characters.")
    elif on is None:
        flash("Pick the date of the game.")
    elif hour not in WHEN_HOURS or ampm not in WHEN_MERIDIEM \
            or minute not in WHEN_MINUTES:
        flash("Pick what time it starts.")
    elif not venue:
        flash("Name the venue.")
    elif len(venue) > FIXTURE_VENUE_MAX:
        flash(f"Keep the venue under {FIXTURE_VENUE_MAX} characters.")
    elif len(note) > FIXTURE_NOTE_MAX:
        flash(f"Keep the note under {FIXTURE_NOTE_MAX} characters.")
    elif latitude is None or longitude is None:
        flash("Put the pin on the map so people can find it.")
    # The same rule a student's flag obeys, checked with the same function.
    elif not map_flag_point_is_allowed(latitude, longitude):
        flash("Put the pin inside the campus boundary.")
    else:
        starts_at = boston_to_utc(on, hour, minute, ampm)
        fixture = Fixture(
            title=title, sport=sport or None, opponent=opponent or None,
            starts_at=starts_at,
            when_text=when_label(on, hour, minute, ampm) or "",
            venue=venue, latitude=latitude, longitude=longitude,
            note=note or None, posted_by=current_user.id,
        )
        db.session.add(fixture)
        db.session.flush()
        flag = make_fixture_flag(fixture)

        # The campus room carries it, with the flag's own link in the
        # sentence: chat.html already unfurls a /find?flag=... link into a
        # card, so this needs no markup of its own and cannot drift from what
        # the map says.
        said = " ".join(p for p in (f"{fixture.title}:", fixture.billing(),
                                    f"{fixture.when_text}.") if p)
        if flag is not None:
            said += " " + url_for("find_game", flag=flag.public_id,
                                  _external=False)
        announcement = Message(user_id=current_user.id, body=said)
        attach_parent(announcement, COMMUNITY, None)
        db.session.add(announcement)

        told = notices.fixture_posted(fixture.title, fixture.billing(),
                                      fixture.when_text)
        db.session.commit()
        flash(f"Posted. {told} student{'' if told == 1 else 's'} told.")
        return redirect(url_for("fixtures"))

    return redirect(url_for("fixture_new"))


@app.route("/fixtures/<int:fixture_id>/cancel", methods=["POST"])
@login_required
def fixture_cancel(fixture_id):
    away = not_poster()
    if away:
        return away

    fixture = db.session.get(Fixture, fixture_id)
    if fixture is None or fixture.status != "scheduled":
        flash("That game is not on the list.")
        return redirect(url_for("fixtures"))

    fixture.status = "cancelled"
    # Its pin comes down with it, the way post_close takes a meetup's.
    db.session.execute(db.delete(MapFlag).where(MapFlag.fixture_id == fixture.id))
    db.session.commit()
    flash(f"{fixture.title} called off.")
    return redirect(url_for("fixtures"))


# ----------------------------------------------------------- who may do what

# The two addresses these pages used to live at. Kept, because a notice
# stores its url as a finished string: every "waiting for review" already in
# somebody's bell points at /admin/hubs, and dropping it would turn those
# into dead ends years before they are read.
@app.route("/admin/hubs")
@app.route("/admin/people")
@login_required
def admin_moved():
    # Only when there is one: url_for with an empty q appends "?q=", which
    # makes a bookmarked address look like it carries a search it does not.
    term = request.args.get("q", "").strip()
    return redirect(url_for("admin", q=term) if term else url_for("admin"))


@app.route("/admin/people/<int:user_id>/admin", methods=["POST"])
@login_required
def admin_set_admin(user_id):
    """Make somebody an admin, or stop them being one.

    `python dbtool.py admin --email a@umb.edu --apply` is still the way to
    appoint the first one on a fresh database -- there has to be a way in
    before anybody can reach this page.
    """
    away = not_admin()
    if away:
        return away

    person = db.session.get(User, user_id)
    if person is None:
        flash("That account no longer exists.")
        return redirect(url_for("admin"))

    grant = request.form.get("grant") == "yes"
    # The rail. A script that leaves a database with no admin can be run
    # again; a page that does it has shut the only door back in. Counted
    # rather than assumed, because "surely there is another one" is exactly
    # the thought that ends with nobody being able to review anything.
    if not grant and person.is_admin:
        others = db.session.scalar(
            db.select(db.func.count()).select_from(User)
            .where(User.is_admin, User.id != person.id))
        if not others:
            flash("That is the only admin. Make somebody else one first, "
                  "or run `python dbtool.py admin --email ... --apply`.")
            return redirect(url_for("admin", q=request.args.get("q", "")))

    person.is_admin = grant
    db.session.commit()
    flash(f"{person.name} "
          f"{'is now an admin' if grant else 'is no longer an admin'}.")
    return redirect(url_for("admin", q=request.args.get("q", "")))


@app.route("/admin/people/<int:user_id>/fixtures", methods=["POST"])
@login_required
def admin_set_poster(user_id):
    away = not_admin()
    if away:
        return away

    person = db.session.get(User, user_id)
    if person is None:
        flash("That account no longer exists.")
        return redirect(url_for("admin"))

    person.can_post_fixtures = request.form.get("grant") == "yes"
    db.session.commit()
    flash(f"{person.name} "
          f"{'can now post' if person.can_post_fixtures else 'can no longer post'}"
          " home games.")
    return redirect(url_for("admin", q=request.args.get("q", "")))


# ------------------------------------------------------------ the database
# The same functions dbtool.py runs, from the admin page, so the terminal is
# optional. db_ops is imported lazily: it reads .env and opens psycopg2
# connections of its own, and nothing else in the app needs it.
#
# Two commands cannot run from here against the database the app is using,
# and no amount of confirmation changes that. `wipe` and `clone` both take a
# backup with CREATE DATABASE ... TEMPLATE, which Postgres refuses while
# anything is connected to the source -- and this request IS a connection. A
# handler cannot close the pool it is running inside. For those two, against
# the current database, the panel shows the command to paste and says why.

# One job at a time, in a thread, polled by the page. The live channel has
# the same shape. A second job is refused rather than queued: these are
# database-wide operations and two at once is never what was meant.
DB_JOB = {"running": False, "command": "", "output": "", "summary": "",
          "ok": None, "started": None, "who": ""}
DB_JOB_LOCK = threading.Lock()


def db_commands():
    """What the panel offers, and what each one needs before it runs.

    `writes` means it needs the typed confirmation; a read-only command runs
    on one click. `local_only` marks the two that cannot act on the database
    this process is connected to.
    """
    return [
        {"id": "status", "label": "Compare dev and live",
         "note": "Structure only, read-only in both directions.",
         "writes": False, "live": False, "local_only": False},
        {"id": "migrate-list", "label": "List migrations",
         "note": "Every migration, applied or pending.",
         "writes": False, "live": True, "local_only": False},
        {"id": "migrate-all", "label": "Run every pending migration",
         "note": "In order, each in its own transaction.",
         "writes": True, "live": True, "local_only": False},
        {"id": "admin-list", "label": "List admins",
         "note": "Who reviews hub requests.",
         "writes": False, "live": True, "local_only": False},
        {"id": "seed-questions", "label": "Seed the questionnaire",
         "note": "Adds or updates; removes nothing.",
         "writes": True, "live": True, "local_only": False},
        {"id": "seed-clubs", "label": "Seed the hubs",
         "note": "Adds or updates; removes nothing.",
         "writes": True, "live": True, "local_only": False},
        {"id": "seed-activities", "label": "Seed the activities",
         "note": "Adds or updates; removes nothing.",
         "writes": True, "live": True, "local_only": False},
        {"id": "seed-demo", "label": "Seed the demo students",
         "note": "32 accounts that cluster, for the matcher. Slow: it "
                 "hashes a password each.",
         "writes": True, "live": True, "local_only": False},
        {"id": "clear-demo", "label": "Remove the demo students",
         "note": "Deletes them and everything they made.",
         "writes": True, "live": True, "local_only": False},
        {"id": "clear-dead-invites", "label": "Clear unanswerable invites",
         "note": "Game invites with no message and no notice.",
         "writes": True, "live": True, "local_only": False},
        {"id": "reset-catalogue", "label": "Replace questions and hubs",
         "note": "Clears every answer, so everybody is asked again.",
         "writes": True, "live": True, "local_only": False},
        {"id": "delete-account", "label": "Delete one account",
         "note": "By email. Takes everything attached with it.",
         "writes": True, "live": True, "local_only": False,
         "needs": "email"},
        {"id": "wipe", "label": "Clear it for real students",
         "note": "Keeps the catalogue and whichever accounts you name. "
                 "Backs up first.",
         "writes": True, "live": True, "local_only": True},
        {"id": "clone", "label": "Refresh the dev copy of live",
         "note": "CREATE DATABASE ... TEMPLATE. Replaces dev entirely.",
         "writes": True, "live": False, "local_only": True},
    ]


def run_db_command(command, live, apply_, email=None):
    """Call into db_ops. Returns a Result. Runs on the job thread."""
    import db_ops

    if command == "status":
        return db_ops.status(echo=False)
    if command == "migrate-list":
        rows, text = db_ops.migration_status(live, echo=False)
        waiting = [n for n, _, done in rows if not done]
        return db_ops.Result(True, 0, False,
                             f"{len(waiting)} pending" if waiting
                             else "all applied", text)
    if command == "migrate-all":
        return db_ops.run_pending(live, apply_, echo=False)
    if command == "admin-list":
        return db_ops.admin_list(live, echo=False)
    if command in ("seed-questions", "seed-clubs", "seed-activities"):
        what = command.split("-", 1)[1]
        summary, work = db_ops.SEEDERS[what]
        return db_ops.run_op(f"seed {what}: {summary}", work, live, apply_,
                             echo=False)
    if command == "seed-demo":
        return db_ops.run_op("seed demo", db_ops.seed_demo_work, live,
                             apply_, echo=False)
    if command == "clear-demo":
        return db_ops.run_op("clear demo", db_ops.clear_demo_work, live,
                             apply_, echo=False)
    if command == "clear-dead-invites":
        return db_ops.run_op("clear dead invites",
                             db_ops.clear_dead_invites_work, live, apply_,
                             echo=False)
    if command == "reset-catalogue":
        return db_ops.run_op("reset catalogue", db_ops.reset_catalogue_work,
                             live, apply_, echo=False)
    if command == "delete-account":
        return db_ops.run_op("delete account", db_ops.delete_account_work,
                             live, apply_, echo=False, email=email)
    if command == "wipe":
        return db_ops.wipe(live=live, apply_=apply_, echo=False)
    if command == "clone":
        return db_ops.clone(apply_=apply_, echo=False)
    raise ValueError(f"no such command {command!r}")


def db_job_thread(command, live, apply_, email):
    """The worker. Writes its result into DB_JOB and clears `running`."""
    import db_ops

    try:
        result = run_db_command(command, live, apply_, email)
        output, summary, ok = result.output, result.summary, result.ok
    except db_ops.Refuse as exc:
        output, summary, ok = f"  REFUSED: {exc}", str(exc), False
    except Exception as exc:                        # noqa: BLE001
        # Never leave the panel stuck on "running" because something threw.
        app.logger.exception("database command %r failed", command)
        output, summary, ok = f"  FAILED: {exc}", str(exc), False
    with DB_JOB_LOCK:
        DB_JOB.update(running=False, output=output, summary=summary, ok=ok)


@app.route("/admin/db/run", methods=["POST"])
@login_required
def admin_db_run():
    """Start one database command, after the confirmation is typed right."""
    away = not_admin()
    if away:
        return away
    import db_ops

    command = request.form.get("command", "")
    known = {c["id"]: c for c in db_commands()}
    spec = known.get(command)
    if spec is None:
        return jsonify({"error": "no such command"}), 400

    live = spec["live"] and request.form.get("live") == "yes"
    apply_ = request.form.get("apply") == "yes"
    typed = request.form.get("confirm", "").strip()
    email = request.form.get("email", "").strip() or None

    # The typed confirmation guards anything that would write. A dry run
    # changes nothing, so it does not need one -- which is what makes "look
    # first, then apply" the easy path rather than a discipline.
    if apply_ and spec["writes"]:
        name, _ = db_ops.where(live)
        if typed != name:
            return jsonify({
                "error": f"type {name} exactly to confirm",
            }), 400
        if live and request.form.get("live_sure") != "yes":
            return jsonify({"error": "tick the live box as well"}), 400

    # The two that cannot act on the database this process is holding open.
    if spec["local_only"] and apply_:
        target, _ = db_ops.where(live)
        here, _ = db_ops.where(False)
        if command == "clone" or target == here:
            flags = []
            if command == "wipe" and live:
                flags.append("--live")
            flags.append("--apply")
            return jsonify({
                "blocked": True,
                "command": f"python dbtool.py {command} "
                           + " ".join(flags),
                "why": "This takes a backup with CREATE DATABASE ... "
                       "TEMPLATE, which Postgres refuses while anything is "
                       "connected to the source. This page is itself a "
                       "connection, so it cannot be run from here. Paste "
                       "the command above into a terminal with the app "
                       "stopped.",
            }), 409

    with DB_JOB_LOCK:
        if DB_JOB["running"]:
            return jsonify({"error": f"{DB_JOB['command']} is still "
                                     "running"}), 409
        DB_JOB.update(running=True, command=command, output="", summary="",
                      ok=None, started=datetime.utcnow(),
                      who=current_user.email)

    worker = threading.Thread(
        target=db_job_thread, args=(command, live, apply_, email),
        name=f"db-{command}", daemon=True)
    worker.start()
    return jsonify({"started": command, "apply": apply_, "live": live})


@app.route("/admin/db/output")
@login_required
def admin_db_output():
    """What the running (or last) command has said. Polled by the page."""
    away = not_admin()
    if away:
        return away
    with DB_JOB_LOCK:
        return jsonify({
            "running": DB_JOB["running"],
            "command": DB_JOB["command"],
            "output": DB_JOB["output"],
            "summary": DB_JOB["summary"],
            "ok": DB_JOB["ok"],
        })


# ---------------------------------------------------------------- group chats
# A chat a student made. Membership is the only key: a join code buys you a
# ChatMember row and nothing else, so every route below re-checks membership
# or ownership rather than trusting how you got here.

CODE_TRIES = 8


def is_member(chat_id, user_id=None):
    user_id = current_user.id if user_id is None else user_id
    return db.session.get(ChatMember, (user_id, chat_id)) is not None


# What stands between you and somebody else, as one word. The same four
# matching.match_state returns, because a second vocabulary for one fact is
# two places to disagree about it.
FRIEND_STATES = ("none", "you_asked", "they_asked", "matched", "you")


def visible_people(people, states):
    """The ones this viewer may be shown on a list.

    An unlisted account is in the room but not on the roster, unless the
    viewer is already friends with them -- and they can always see
    themselves. Every list this filters is counted by its own length, so the
    count follows the rows: "5 members" above four of them is the same leak
    said a different way.
    """
    return [person for person in people
            if person.discoverable
            or states.get(person.id) in ("matched", "you")]


def friend_state_for(user_ids):
    """What stands between me and each of these students, one word each.

    Three queries for the whole list rather than one per row. match_state
    answers about a single pair, which is right for a profile and wrong for a
    club space with two dozen people in it -- that would be two dozen round
    trips to draw one list.

    "you" is in here rather than left to the template: every surface that
    draws a roster would otherwise have to remember not to offer you yourself.
    """
    mine = {f["user_id"] for f in matching.friend_names(current_user.id)}
    asked = {r["user_id"] for r in matching.outgoing_requests(current_user.id)}
    asking = {r["user_id"] for r in matching.incoming_requests(current_user.id)}

    def state(uid):
        if uid == current_user.id:
            return "you"
        if uid in mine:
            return "matched"
        if uid in asked:
            return "you_asked"
        if uid in asking:
            return "they_asked"
        return "none"

    return {uid: state(uid) for uid in user_ids}


def club_members(club_id):
    """Everyone in a club, by name. chat_members' shape, for the other table."""
    return db.session.scalars(
        db.select(User)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club_id)
        .order_by(User.name)
    ).all()


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
        # `ended` rather than leaving them out: a meetup's chat is still worth
        # opening after the meetup, to read what was said in it. Marked, not
        # hidden.
        mine=[{"chat": c, "members": counts.get(c.id, 0),
               "owner": c.owner_id == current_user.id,
               "meetup": c.post_id is not None,
               "ended": chat_ended(c)} for c in mine],
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
    # A meetup's chat is its RSVP list. A code that let somebody in would put
    # a person in the conversation for a thing they are not going to, and no
    # amount of leaving the chat would keep them out while they were on it.
    elif chat.post_id is not None:
        flash("That chat belongs to a meetup. RSVP to the meetup to join it.")
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
    elif chat.post_id is not None:
        # Who is in a meetup's chat is who is going to the meetup. An invite
        # would put somebody in it who is not on that list.
        flash("People join this chat by RSVPing to the meetup.")
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


def hand_over_chat(chat, successor):
    """Point a chat at a new owner, or close it when there is nobody.

    `successor` is a member already checked not to be the person leaving, or
    None meaning there is nobody left -- in which case the chat goes, because
    an empty chat is just clutter.

    Never commits, and never touches the leaver's own ChatMember row:
    leaving deletes exactly one of those and deleting an account has the
    cascade do it, so the two callers own that part themselves.

    Shared with /account/delete so the two cannot drift. Without a handover
    there, Chat.owner_id's CASCADE destroys the chat, its members, its
    invites and every message anyone ever wrote in it.
    """
    if successor is None:
        db.session.delete(chat)
        return None
    chat.owner_id = successor.id
    return successor


def longest_standing_member(chat_id, excluding):
    """Who has been in a chat longest, not counting one person.

    This is who an owner's chat passes to when their account is deleted:
    there is nobody to ask, so the rule has to be in the code, and "whoever
    has been here longest" is the one rule that needs no judgement.

    Ordered by joined_at and then by id, so the answer is the same every
    time. Two people added inside one request share a timestamp, and a tie
    broken by whatever the planner happened to return first would make this
    untestable.
    """
    return db.session.scalars(
        db.select(User)
        .join(ChatMember, ChatMember.user_id == User.id)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id != excluding)
        .order_by(ChatMember.joined_at, User.id)
        .limit(1)
    ).first()


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

    if chat.post_id is not None:
        # There is no being in the meetup but out of its chat: the RSVP list
        # is the membership. Leaving the meetup is the action that does this,
        # and it is the one offered on the page.
        flash("Leave the meetup itself and this chat closes with it.")
        return redirect(url_for("chat", kind="group", target_id=chat.id))

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
        hand_over_chat(chat, None)
        db.session.commit()
        flash(f"You left and closed {name}.")
        return redirect(url_for("chats"))

    raw = request.form.get("successor", "")
    successor = next((m for m in others if str(m.id) == raw), None)

    if successor is None:
        flash(f"Choose who should take over {chat.name} before you leave.")
        return redirect(url_for("chat", kind="group", target_id=chat_id))

    hand_over_chat(chat, successor)
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
    elif chat.post_id is not None:
        flash("People leave this chat by leaving the meetup.")
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

    if chat.post_id is not None:
        # The meetup owns this chat's life. Cancelling the meetup is what
        # ends it, and it ends for everyone at once when that happens.
        flash("Cancel the meetup to close its chat.")
        return redirect(url_for("chat", kind="group", target_id=chat.id))

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
    # Somebody working down a list of suggestions should still be looking at
    # the list afterwards, not at the friends page with their place lost.
    if request.form.get("back") == "welcome":
        return redirect(url_for("welcome"))
    # The same courtesy for a roster row or a pair chat's header.
    if (home := chat_home(ROSTER_CHATS)) is not None:
        return redirect(url_for("chat", kind=home[0], target_id=home[1]))
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
    # Pressed on a roster row or a chat header: back to that conversation,
    # rather than to the friends page they were not looking at.
    if (home := chat_home(ROSTER_CHATS)) is not None:
        return redirect(url_for("chat", kind=home[0], target_id=home[1]))
    if action == "cancel":
        return back_to_people()
    return redirect(url_for("matches"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        # Two fields rather than one box. The surname is now required, and a
        # form that asks for it by name is a form you can fill in correctly
        # the first time -- one "Your name" box that rejects "Ada" afterwards
        # is the same rule taught the hard way.
        #
        # They are still stored as the one `name` the rest of the app reads
        # and displays. Nothing else needs to know this changed, and no
        # existing account has to be taken apart into pieces nobody recorded.
        first = request.form.get("first_name", "").strip()
        last = request.form.get("last_name", "").strip()
        name = " ".join(f"{first} {last}".split())
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

        if not first:
            flash("Please enter your first name.")
        elif not last:
            flash("Please enter your last name.")
        elif len(name) > NAME_MAX:
            # Said rather than silently truncated: the column is 120 and a
            # name cut in half is worse than a name refused.
            flash(f"Please keep your name under {NAME_MAX} characters.")
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


def read_answers(questions):
    """Read a questionnaire submission. Returns (picks, selected, complaint).

    `complaint` is a finished sentence to flash, or None when the submission
    is good. Shared by /onboarding and /account because the two forms post
    the same fields -- `_questions.html` is the partial both render -- and a
    second copy of these three checks is a second place for the one that
    matters to go missing.

    That one is `stray`: it refuses option ids that do not belong to the
    question they were posted under, which is what stops a crafted form
    answering one question with another's options.
    """
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
        complaint = "Please answer: " + "; ".join(q.prompt for q in missing)
    elif doubled:
        complaint = "Please pick just one answer for each single-choice question."
    elif stray:
        complaint = "Please choose from the listed options."
    else:
        complaint = None
    return picks, selected, complaint


def save_answers(questions, picks, selected):
    """Replace the current user's answers. Returns the hubs they joined.

    Does not commit: the caller owns the transaction, so the answers, the
    pronouns and the hub memberships all land together or not at all.
    """
    # What they had before the delete below, which is the only place
    # "newly picked" is answerable from. Without it, a hub somebody left by
    # hand would be rejoined every time they touched their answers, quietly
    # undoing a decision they had already made.
    had = set(db.session.scalars(
        db.select(UserAnswer.option_id)
        .filter_by(user_id=current_user.id)))
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

    # An answer can put somebody into a hub. Only when it is newly given,
    # and nothing here ever takes anybody out: dropping the answer must not
    # pull somebody out of a conversation they are in the middle of, and
    # leaving a hub by hand has to stick.
    # (question key, answer) -> option id, walked in Python rather than
    # queried: `stray` in read_answers already reads every question's
    # options, so they are in the session and this costs nothing.
    ids = {(q.key, o.label): o.id for q in questions for o in q.options}
    joined = []
    for (key, label), hub in JOINS_HUB.items():
        option_id = ids.get((key, label))
        if (option_id is None or option_id in had
                or option_id not in selected):
            continue
        club = db.session.scalar(db.select(Club).filter_by(name=hub))
        # A hub that has been renamed or removed is a quiet no-op. This runs
        # on the one page nobody can skip, so a missing row here must not be
        # the thing that stops somebody signing up.
        if club is None:
            app.logger.warning("JOINS_HUB names %r, which is not a hub", hub)
            continue
        # Already in it, by hand or from an earlier answer. The primary key
        # is (user_id, club_id), so a second row would raise.
        if db.session.get(ClubMember, (current_user.id, club.id)):
            continue
        db.session.add(ClubMember(user_id=current_user.id, club_id=club.id))
        joined.append(club.name)
    return joined


def set_pronouns():
    """Write pronouns from the form, if the form actually sent them.

    Only when the field came back, and only then if it is one we offer: a
    submit that leaves it out must not reset somebody's pronouns to
    "unspecified" as a side effect.
    """
    choice = request.form.get("pronouns")
    if choice in PRONOUNS:
        current_user.pronouns = choice


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
        picks, selected, complaint = read_answers(questions)
        if complaint:
            flash(complaint)
        else:
            # Asked before save_answers, which is what makes it an answer
            # about the student rather than about this submit.
            #
            # First time only: somebody editing answers they gave in
            # September is not being welcomed, and sending them to a page
            # that says so would be odd. They go where they always went.
            first_time = not has_onboarded(current_user.id)
            joined = save_answers(questions, picks, selected)
            set_pronouns()
            db.session.commit()
            # Said out loud. Being put into a conversation nobody asked to
            # join is a surprise worth one sentence.
            if joined:
                flash(f"You have been added to {', '.join(joined)}, "
                      "because of what you said about studying.")
            return redirect(url_for("welcome" if first_time else "index"))

    return render_template(
        "onboarding.html",
        questions=questions,
        selected=selected,
        optional=OPTIONAL_QUESTIONS,
        pronoun_options=PRONOUNS,
        pronouns_now=current_user.pronouns,
    )


# =====================================================================
# Your account
# ---------------------------------------------------------------------
# One page for everything about you: your name, your pronouns, the answers
# the matcher reads, and the way out. /onboarding is still the first-time
# flow -- it asks the same questions through the same partial, and it is the
# page nobody can skip -- but once you are in, this is where you change
# anything, including leaving.


def account_counts():
    """What deleting this account would take with it, counted not guessed.

    Shown before anybody confirms, because the cascade is wide and some of
    it reaches other people: the games go from the other player's history
    too, which is the part nobody would expect.
    """
    me = current_user.id
    owned = db.session.scalars(
        db.select(Chat).where(Chat.owner_id == me, Chat.post_id.is_(None))
    ).all()
    passing, closing = [], []
    for chat in owned:
        if longest_standing_member(chat.id, excluding=me) is None:
            closing.append(chat.name)
        else:
            passing.append(chat.name)
    count = lambda model, clause: db.session.scalar(
        db.select(db.func.count()).select_from(model).where(clause))
    return {
        "friends": len(matching.active_matches(me)),
        "messages": count(Message, Message.user_id == me),
        "games": count(Game, db.or_(Game.player_a == me, Game.player_b == me)),
        "meetups": count(GamePost, GamePost.author_id == me),
        "pins": count(MapFlag, MapFlag.owner_id == me),
        "passing": passing,
        "closing": closing,
    }


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """Your name, your pronouns and your answers, in three separate forms.

    Three forms rather than one, so saving your name cannot silently rewrite
    answers a half-loaded page had wrong -- and so each section can be
    submitted without the others being present.

    Not in ONBOARDING_EXEMPT, and that is not a style choice: inject_dock
    returns {} for exempt endpoints, _noticelist.html iterates notice_rows,
    and iterating a Jinja Undefined raises -- so any page extending
    shell.html that is added to that set fails to render. The consequence is
    that a verified-but-not-onboarded account answers the questions before it
    can reach this page, which is one page away and worth saying out loud.
    """
    questions = db.session.scalars(db.select(Question).order_by(Question.id)).all()
    selected = set(
        db.session.scalars(
            db.select(UserAnswer.option_id).filter_by(user_id=current_user.id)
        )
    )

    if request.method == "POST":
        section = request.form.get("section", "")

        if section == "who":
            name = " ".join(request.form.get("name", "").split())
            if not name:
                flash("Your name cannot be blank.")
            elif len(name) > NAME_MAX:
                flash(f"Please keep your name under {NAME_MAX} characters.")
            else:
                current_user.name = name
                set_pronouns()
                db.session.commit()
                flash("Saved.")
                return redirect(url_for("account"))

        elif section == "answers":
            picks, selected, complaint = read_answers(questions)
            if complaint:
                flash(complaint)
            else:
                joined = save_answers(questions, picks, selected)
                db.session.commit()
                if joined:
                    flash(f"Saved. You have been added to "
                          f"{', '.join(joined)}, because of what you said "
                          "about studying.")
                else:
                    flash("Saved.")
                return redirect(url_for("account"))

        else:
            flash("That form did not say what it was for.")

    return render_template(
        "account.html",
        questions=questions,
        selected=selected,
        optional=OPTIONAL_QUESTIONS,
        pronoun_options=PRONOUNS,
        pronouns_now=current_user.pronouns,
        counts=account_counts(),
        name_max=NAME_MAX,
    )


@app.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    """Delete your own account, permanently.

    The password is required, and it is doing two jobs. It is the "are you
    sure" an irreversible action needs, and it is the thing an attacker who
    forged this POST cannot supply -- the email would not do, because
    shell.html prints it on every page and the addresses are formulaic.

    The order below matters more than it looks. Everything that has to be
    read is read before anything is written, because the cascade makes it
    unreadable; the reports scrub runs before the delete, because afterwards
    `filed_by` is already NULL and the WHERE finds nothing; and the flush
    before the delete is explicit so that handing a chat over does not
    depend on the unit of work happening to emit the UPDATE before the
    DELETE. If it ever did not, Chat.owner_id's CASCADE would destroy a
    handed-over chat for everybody in it.
    """
    me = current_user.id
    # Plain values, not attributes: current_user is unusable after the row
    # goes and the password hash is needed before that.
    my_name = current_user.name
    my_email = current_user.email
    my_hash = current_user.password_hash

    if not check_password_hash(my_hash, request.form.get("password", "")):
        flash("That password is not right. Nothing has been deleted.")
        return redirect(url_for("account"))

    # The same door admin_set_admin refuses to shut. Deleting the last
    # admin's account locks the admin page, and the admin page is the only
    # way to appoint another one.
    if current_user.is_admin:
        others = db.session.scalar(
            db.select(db.func.count()).select_from(User)
            .where(User.is_admin, User.id != me))
        if not others:
            flash("You are the only admin. Make somebody else one before "
                  "you delete your account.")
            return redirect(url_for("account"))

    # Read now; the cascade makes both unreadable in a moment.
    friends = [f["user_id"] for f in matching.active_matches(me)]
    owned = db.session.scalars(
        db.select(Chat).where(Chat.owner_id == me, Chat.post_id.is_(None))
    ).all()

    # Told before they are unreachable. A meetup's chat is deliberately not
    # in `owned`: its membership IS the RSVP list, so it goes with the
    # meetup rather than being handed to somebody who only said they would
    # come.
    for friend_id in friends:
        notices.account_gone(friend_id, my_name)

    for chat in owned:
        hand_over_chat(chat, longest_standing_member(chat.id, excluding=me))

    # Their name comes off the reports they filed; the reports stay. Reports
    # ABOUT them keep `about_name`, which is the asymmetry the Report
    # docstring argues for.
    db.session.execute(
        db.update(Report).where(Report.filed_by == me)
        .values(filed_by_name=None))

    db.session.flush()
    db.session.delete(db.session.get(User, me))
    db.session.commit()

    # After the commit and before anything renders: Flask-Login caches the
    # resolved user on `g`, so a deleted row with a still-"authenticated"
    # request would be bounced into /onboarding by require_onboarding.
    logout_user()
    session.pop("pending_user_id", None)
    app.logger.info("account deleted: %s", my_email)
    flash("Your account and everything on it has been deleted. "
          "That address is free to register again.")
    return redirect(url_for("login"))


# =====================================================================
# Campus map
# ---------------------------------------------------------------------
# Flags on the Meet up page's map. Two kinds share one table (MapFlag):
#   - flags a student drops by hand: up to MAP_FLAG_LIMIT at a time, each
#     ending after a chosen duration;
#   - event flags, dropped automatically when an in-person meetup is posted
#     (from Community Chat, Meet up, or a friend chat). They last as long as
#     the meetup, don't count toward the limit, and are seen only by the
#     people who said they're in until the author makes them public.
# Owners can move (drag) and edit their own flags; nobody else can.
#
# Endpoints, all JSON and all for signed-in students:
#   GET    /api/map-flags            every flag you may see, plus picker data
#   POST   /api/map-flags            drop a new flag
#   PATCH  /api/map-flags/<id>       move or edit one of your flags
#   DELETE /api/map-flags/<id>       take one of your flags down
#
# Existing databases are brought up to date by upgrade_map_flags_schema()
# (also `flask --app app upgrade-map-flags`), which runs on its own the first
# time a request reaches a process that finds the old table.
# =====================================================================

# Hand-dropped flags per student at once. Event flags are not counted.
MAP_FLAG_LIMIT = 3

# Every kind of flag, in the order the map offers them. models.MAP_FLAG_KINDS
# is the list the database accepts; the two must match.
MAP_FLAG_CATEGORIES = {
    "game": "Game",
    "study": "Study",
    "social": "Social",
    "club": "Club",
    "event": "Event",
    "home": "Home game",
}
assert tuple(MAP_FLAG_CATEGORIES) == MAP_FLAG_KINDS

# Minutes a hand-dropped flag can stay up.
MAP_FLAG_DURATIONS = {30, 60, 120, 240, 480}

# Who can see a flag, as its owner can set it. An event flag starts at
# "going" (people who are in) and can be made "public".
HAND_FLAG_AUDIENCES = ("public", "friends", "club")
EVENT_FLAG_AUDIENCES = ("going", "public")
# The friend picker's ceiling, so a request cannot list the whole school.
MAP_FLAG_FRIENDS_MAX = 50

# An event flag stays up until the end of its meetup's day in Boston. Times
# here are naive UTC like the rest of the app; Boston's midnight is 04:00 or
# 05:00 UTC depending on daylight saving, so the later one is used.
EVENT_FLAG_DAY_END = timedelta(hours=5)
# The landmark an event flag starts on when its location names none. The
# author can drag it anywhere inside the campus area.
EVENT_FLAG_DEFAULT_PLACE = "campus-center"


MAP_FLAG_BOUNDS = {
    "south": 42.3097,
    "west": -71.0436,
    "north": 42.3174,
    "east": -71.0317,
}


APPROVED_MAP_LOCATIONS = {
    "campus-center": {
        "name": "Campus Center",
        "latitude": 42.312960,
        "longitude": -71.036660,
    },
    "healey-library": {
        "name": "Healey Library",
        "latitude": 42.313620,
        "longitude": -71.039720,
    },
    "university-hall": {
        "name": "University Hall",
        "latitude": 42.313320,
        "longitude": -71.035550,
    },
    "clark-athletic-center": {
        "name": "Clark Athletic Center",
        "latitude": 42.315080,
        "longitude": -71.039570,
    },
    "wheatley-hall": {
        "name": "Wheatley Hall",
        "latitude": 42.312080,
        "longitude": -71.038290,
    },
    "mccormack-hall": {
        "name": "McCormack Hall",
        "latitude": 42.312750,
        "longitude": -71.039350,
    },
}


def purge_expired_map_flags(now=None):
    """Delete elapsed flags and return the number removed.

    Every read also filters by expiration, so a flag disappears on time even
    if this cleanup has not run yet. This opportunistic deletion keeps a small
    installation tidy; production can also schedule the CLI command below.
    """
    now = now or datetime.utcnow()
    result = db.session.execute(
        db.delete(MapFlag).where(MapFlag.expires_at <= now)
    )
    return result.rowcount or 0


def map_flag_point_is_allowed(latitude, longitude):
    """Return whether a coordinate is finite and inside the campus boundary."""
    return (
        isfinite(latitude)
        and isfinite(longitude)
        and MAP_FLAG_BOUNDS["south"] <= latitude <= MAP_FLAG_BOUNDS["north"]
        and MAP_FLAG_BOUNDS["west"] <= longitude <= MAP_FLAG_BOUNDS["east"]
    )


# ---------- event flags: one per in-person meetup ----------

def place_for_location(text):
    """The approved landmark a meetup's location names, if it names one.

    The activity catalogue's locations read like "Campus Center, 2nd floor"
    or "Clark Athletic Center", so a landmark's name inside the text is
    enough. Returns (key, place) or (None, None).
    """
    folded = (text or "").lower()
    for key, place in APPROVED_MAP_LOCATIONS.items():
        if place["name"].lower() in folded:
            return key, place
    return None, None


def event_flag_deadline(post):
    """When a meetup's flag comes down: the end of its day, or, with no date,
    the same horizon its chat has."""
    if post.when_on is not None:
        return datetime.combine(post.when_on, datetime.max.time()) + EVENT_FLAG_DAY_END
    return (post.created_at or datetime.utcnow()) + MEETUP_CHAT_DEFAULT


def make_event_flag(post):
    """Drop the map flag for an in-person meetup. Called inside the
    transaction that creates the meetup; the caller commits.

    It starts on the landmark the meetup's location names (or the Campus
    Center), seen only by the people who are in. Returns None for a meetup
    whose day has already passed.
    """
    now = datetime.utcnow()
    expires = event_flag_deadline(post)
    if expires <= now:
        return None
    key, place = place_for_location(post.location)
    if place is None:
        place = APPROVED_MAP_LOCATIONS[EVENT_FLAG_DEFAULT_PLACE]
    flag = MapFlag(
        public_id=str(uuid4()),
        owner_id=post.author_id,
        title=post.game[:100],
        description=(post.note or "")[:300] or None,
        category="event",
        visibility="going",
        post_id=post.id,
        location_key=key or "custom",
        location_name=(post.location or "Campus meetup point")[:120],
        latitude=place["latitude"],
        longitude=place["longitude"],
        created_at=now,
        expires_at=expires,
    )
    db.session.add(flag)
    return flag


# ---------- what a flag looks like to the browser ----------

def map_flag_payloads(rows):
    """Payloads for a list of (flag, owner name, club name) rows.

    Batched for the whole list: one query for the meetups behind event flags,
    two for who is going, one for meetup chats and one for the friends the
    reader shared their own flags with.
    """
    flags = [flag for flag, _, _ in rows]
    post_ids = [f.post_id for f in flags if f.post_id]
    posts = {}
    going, mine = {}, set()
    rooms = {}
    if post_ids:
        posts = {p.id: p for p in db.session.scalars(
            db.select(GamePost).where(GamePost.id.in_(post_ids)))}
        going, mine = rsvps_for(post_ids)
        rooms = chats_for(post_ids)

    # The friends each of *my* "My friend(s)" flags is shared with, so the
    # editor can show who is picked. Never sent for anybody else's flag.
    viewers = {}
    shared = [f.id for f in flags
              if f.visibility == "friends" and f.owner_id == current_user.id]
    if shared:
        for flag_id, user_id, name in db.session.execute(
            db.select(MapFlagViewer.flag_id, User.id, User.name)
            .join(User, User.id == MapFlagViewer.user_id)
            .where(MapFlagViewer.flag_id.in_(shared))
            .order_by(User.name)
        ):
            viewers.setdefault(flag_id, []).append({"id": user_id, "name": name})

    out = []
    for flag, owner_name, club_name in rows:
        is_mine = flag.owner_id == current_user.id
        event = None
        post = posts.get(flag.post_id)
        if post is not None:
            names = going.get(post.id, [])
            im_going = post.id in mine
            event = {
                "post_id": post.id,
                "when": post.when_text,
                "going": len(names),
                "max": post.max_players,
                "full": len(names) >= post.max_players,
                "im_going": im_going,
                "open": post.status == "open",
                # The meetup's own chat, for the people in it.
                "chat_id": rooms.get(post.id) if (im_going or is_mine) else None,
            }
        out.append({
            "id": flag.public_id,
            "title": flag.title,
            "description": flag.description or "",
            "category": flag.category,
            "category_label": MAP_FLAG_CATEGORIES[flag.category],
            "visibility": flag.visibility,
            "club_name": club_name,
            "club_id": flag.club_id if is_mine else None,
            "friends": viewers.get(flag.id, []) if is_mine else None,
            "location_key": flag.location_key,
            "location_name": flag.location_name,
            "latitude": float(flag.latitude),
            "longitude": float(flag.longitude),
            "owner_name": owner_name,
            "mine": is_mine,
            "event": event,
            "created_at": flag.created_at.isoformat() + "Z",
            "updated_at": flag.updated_at.isoformat() + "Z" if flag.updated_at else None,
            "expires_at": flag.expires_at.isoformat() + "Z",
        })
    return out


def map_flag_payload(flag, owner_name, club_name=None):
    """One flag's payload (see map_flag_payloads)."""
    return map_flag_payloads([(flag, owner_name, club_name)])[0]


def flag_with_names(flag):
    """A flag's payload, looking up its owner's and club's names."""
    owner = db.session.get(User, flag.owner_id)
    club = db.session.get(Club, flag.club_id) if flag.club_id else None
    return map_flag_payload(flag, owner.name if owner else "Someone",
                            club.name if club else None)


# ---------- reading a new flag or an edit from the browser ----------

class FlagError(Exception):
    """A message for the student, and the HTTP status to send it with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def read_flag_fields(data, flag=None):
    """Check the editable parts of a flag from a JSON body.

    For a new flag (flag is None) every field is required, as it always was.
    For an edit only the fields sent are read, so dragging a pin can send its
    new position alone. Returns a dict of changes; raises FlagError.
    """
    creating = flag is None
    event = flag is not None and flag.post_id is not None
    changes = {}

    def sent(key):
        return creating or key in data

    if sent("title"):
        title = str(data.get("title") or "").strip()
        if not title:
            raise FlagError("Give the meetup a title.")
        if len(title) > 100:
            raise FlagError("Keep the title under 100 characters.")
        changes["title"] = title
    if sent("description"):
        description = str(data.get("description") or "").strip()
        if len(description) > 300:
            raise FlagError("Keep the note under 300 characters.")
        changes["description"] = description or None
    if sent("category"):
        category = str(data.get("category") or "")
        if category not in MAP_FLAG_CATEGORIES:
            raise FlagError("Choose a valid flag category.")
        changes["category"] = category

    if sent("location_name"):
        location_name = str(data.get("location_name") or "").strip()
        if not location_name:
            raise FlagError("Add a short label for the meetup location.")
        if len(location_name) > 120:
            raise FlagError("Keep the location label under 120 characters.")
        changes["location_name"] = location_name
    if sent("latitude") or sent("longitude"):
        try:
            latitude = float(data.get("latitude"))
            longitude = float(data.get("longitude"))
        except (TypeError, ValueError):
            latitude = longitude = float("nan")
        if not map_flag_point_is_allowed(latitude, longitude):
            raise FlagError("Place the flag inside the shaded UMass Boston campus area.")
        changes["latitude"] = round(latitude, 6)
        changes["longitude"] = round(longitude, 6)
    # Event flags end with their meetup, so only hand-dropped ones take a
    # duration. On an edit a new duration counts from now.
    if creating or ("duration" in data and not event):
        try:
            duration = int(data.get("duration", 0))
        except (TypeError, ValueError):
            duration = 0
        if duration not in MAP_FLAG_DURATIONS:
            raise FlagError("Choose a valid expiration time.")
        changes["duration"] = duration

    if sent("visibility"):
        visibility = str(data.get("visibility") or "public")
        if visibility not in (EVENT_FLAG_AUDIENCES if event else HAND_FLAG_AUDIENCES):
            raise FlagError("Choose who can see this flag.")
        changes["visibility"] = visibility
        changes["club_id"] = None
        changes["friend_ids"] = []
        if visibility == "club":
            try:
                club_id = int(data.get("club_id"))
            except (TypeError, ValueError):
                raise FlagError("Choose one of your clubs.")
            if db.session.get(ClubMember, (current_user.id, club_id)) is None:
                raise FlagError("You can only post to a club you joined.", 403)
            if db.session.get(Club, club_id) is None:
                raise FlagError("That club is no longer available.")
            changes["club_id"] = club_id
        elif visibility == "friends":
            picked = data.get("friend_ids")
            if not isinstance(picked, list):
                picked = []
            try:
                picked = sorted({int(x) for x in picked})
            except (TypeError, ValueError):
                raise FlagError("Pick friends from your list.")
            if not picked:
                raise FlagError("Pick at least one friend to share it with.")
            if len(picked) > MAP_FLAG_FRIENDS_MAX:
                raise FlagError(f"Share it with up to {MAP_FLAG_FRIENDS_MAX} friends.")
            friends = {f["user_id"] for f in matching.active_matches(current_user.id)}
            if not set(picked) <= friends:
                raise FlagError("You can only share a flag with your friends.", 403)
            changes["friend_ids"] = picked
    return changes


def apply_flag_changes(flag, changes, now):
    """Write checked changes onto a flag. The flag must already have an id
    when its friends list changes (flush a new one first)."""
    for key in ("title", "description", "category", "location_name",
                "latitude", "longitude"):
        if key in changes:
            setattr(flag, key, changes[key])
    if "latitude" in changes:
        # Moved by hand, so it is no longer pinned to a landmark.
        flag.location_key = "custom"
    if "duration" in changes:
        flag.expires_at = now + timedelta(minutes=changes["duration"])
    if "visibility" in changes:
        flag.visibility = changes["visibility"]
        flag.club_id = changes["club_id"]
        db.session.execute(
            db.delete(MapFlagViewer).where(MapFlagViewer.flag_id == flag.id))
        for user_id in changes["friend_ids"]:
            db.session.add(MapFlagViewer(flag_id=flag.id, user_id=user_id))


def from_the_map():
    """Writes must come from the map's own script, as they always have."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


# ---------- the endpoints ----------

def map_flag_visible_to(me):
    """The clause that says which flags one student may see.

    Public ones, their own, their clubs', ones a friend shared with them, and
    the flags of meetups they said they were in for.

    Extracted because /report has to ask the same question before it will
    quote a pin back to somebody: a second copy of this rule would drift,
    and the direction it would drift in is showing a private pin to a
    stranger on a page whose whole purpose is safety.
    """
    joined_club_ids = db.select(ClubMember.club_id).where(ClubMember.user_id == me)
    shared_with_me = db.select(MapFlagViewer.flag_id).where(MapFlagViewer.user_id == me)
    my_meetups = db.select(GamePostRsvp.post_id).where(GamePostRsvp.user_id == me)
    return db.or_(
        MapFlag.visibility == "public",
        MapFlag.owner_id == me,
        MapFlag.club_id.in_(joined_club_ids),
        db.and_(MapFlag.visibility == "friends",
                MapFlag.id.in_(shared_with_me)),
        db.and_(MapFlag.visibility == "going",
                MapFlag.post_id.in_(my_meetups)),
    )


@app.route("/api/map-flags")
@login_required
def map_flags_list():
    """Return every active flag this student is allowed to see.

    That is: public flags, your own, your clubs' flags, flags a friend shared
    with you, and the flags of meetups you said you're in for.
    """
    now = datetime.utcnow()
    if purge_expired_map_flags(now):
        db.session.commit()

    me = current_user.id
    rows = db.session.execute(
        db.select(MapFlag, User.name, Club.name)
        .join(User, User.id == MapFlag.owner_id)
        .outerjoin(Club, Club.id == MapFlag.club_id)
        .outerjoin(GamePost, GamePost.id == MapFlag.post_id)
        .where(
            MapFlag.expires_at > now,
            # an event flag goes with a cancelled meetup
            db.or_(MapFlag.post_id.is_(None), GamePost.status == "open"),
            map_flag_visible_to(me),
        )
        .order_by(MapFlag.expires_at, MapFlag.id)
    ).all()
    mine = db.session.scalar(
        db.select(db.func.count())
        .select_from(MapFlag)
        .where(MapFlag.owner_id == me, MapFlag.expires_at > now,
               MapFlag.post_id.is_(None), MapFlag.fixture_id.is_(None))
    )
    clubs = db.session.execute(
        db.select(Club.id, Club.name)
        .join(ClubMember, ClubMember.club_id == Club.id)
        .where(ClubMember.user_id == me)
        .order_by(Club.name)
    ).all()
    friends = [{"id": f["user_id"], "name": f["name"]}
               for f in matching.active_matches(me)]
    friends.sort(key=lambda f: f["name"].lower())

    return jsonify({
        "flags": map_flag_payloads(rows),
        "mine": mine,
        "limit": MAP_FLAG_LIMIT,
        "categories": MAP_FLAG_CATEGORIES,
        # JSON objects come out sorted, so the map's order travels separately.
        "category_order": list(MAP_FLAG_CATEGORIES),
        "durations": sorted(MAP_FLAG_DURATIONS),
        "places": [
            {"key": key, **place}
            for key, place in APPROVED_MAP_LOCATIONS.items()
        ],
        "bounds": MAP_FLAG_BOUNDS,
        "clubs": [{"id": club_id, "name": name} for club_id, name in clubs],
        # For the "My friend(s)" picker.
        "friends": friends,
    })


@app.route("/api/map-flags", methods=["POST"])
@login_required
def map_flag_create():
    """Create one expiring flag inside the reviewed campus boundary."""
    if not from_the_map():
        return jsonify({"error": "This request must come from the campus map."}), 400

    data = request.get_json(silent=True) or {}
    try:
        changes = read_flag_fields(data)
    except FlagError as error:
        return jsonify({"error": error.message}), error.status

    now = datetime.utcnow()
    if purge_expired_map_flags(now):
        db.session.commit()

    # Lock this student's row before counting. Two simultaneous create
    # requests for the same person therefore cannot both claim the last slot.
    db.session.execute(
        db.select(User).where(User.id == current_user.id).with_for_update()
    ).scalar_one()
    active_count = db.session.scalar(
        db.select(db.func.count())
        .select_from(MapFlag)
        .where(MapFlag.owner_id == current_user.id, MapFlag.expires_at > now,
               MapFlag.post_id.is_(None), MapFlag.fixture_id.is_(None))
    )
    if active_count >= MAP_FLAG_LIMIT:
        db.session.rollback()
        return jsonify({
            "error": f"You already have {MAP_FLAG_LIMIT} active flags. "
                     "Delete one or wait for it to expire."
        }), 409

    flag = MapFlag(
        public_id=str(uuid4()),
        owner_id=current_user.id,
        title=changes["title"],
        category=changes["category"],
        visibility=changes["visibility"],
        location_key="custom",
        location_name=changes["location_name"],
        latitude=changes["latitude"],
        longitude=changes["longitude"],
        created_at=now,
        expires_at=now + timedelta(minutes=changes["duration"]),
    )
    db.session.add(flag)
    db.session.flush()   # the friends list needs the flag's id
    apply_flag_changes(flag, changes, now)
    db.session.commit()

    return jsonify({
        "flag": flag_with_names(flag),
        "mine": active_count + 1,
        "limit": MAP_FLAG_LIMIT,
    }), 201


def owned_flag(public_id, verb):
    """One of the current student's live flags, or (error response)."""
    flag = db.session.scalar(
        db.select(MapFlag).filter_by(public_id=str(public_id))
    )
    if flag is None or flag.expires_at <= datetime.utcnow():
        return None, (jsonify({"error": "That flag has already expired or was removed."}), 404)
    if flag.owner_id != current_user.id:
        return None, (jsonify({"error": f"You can only {verb} your own flags."}), 403)
    return flag, None


@app.route("/api/map-flags/<uuid:public_id>", methods=["PATCH"])
@login_required
def map_flag_update(public_id):
    """Move or edit one of your flags. Send only what changed: a dragged pin
    sends its new latitude and longitude; the editor sends the whole form."""
    if not from_the_map():
        return jsonify({"error": "This request must come from the campus map."}), 400

    flag, refused = owned_flag(public_id, "change")
    if refused:
        return refused

    data = request.get_json(silent=True) or {}
    try:
        changes = read_flag_fields(data, flag)
    except FlagError as error:
        return jsonify({"error": error.message}), error.status
    if not changes:
        return jsonify({"error": "There was nothing to change."}), 400

    now = datetime.utcnow()
    apply_flag_changes(flag, changes, now)
    flag.updated_at = now
    db.session.commit()
    return jsonify({"flag": flag_with_names(flag)})


@app.route("/api/map-flags/<uuid:public_id>", methods=["DELETE"])
@login_required
def map_flag_delete(public_id):
    """Take one of your flags down. For an event flag the meetup stays."""
    if not from_the_map():
        return jsonify({"error": "This request must come from the campus map."}), 400

    flag, refused = owned_flag(public_id, "remove")
    if refused:
        return refused

    db.session.delete(flag)
    db.session.commit()
    return "", 204


@app.cli.command("purge-expired-flags")
def purge_expired_flags_command():
    """Physically remove elapsed map flags (safe to schedule every minute)."""
    removed = purge_expired_map_flags()
    db.session.commit()
    print(f"Removed {removed} expired map flag(s).")


# ---------- bringing an existing database up to date ----------

# The CHECK constraints this version widened. On an older table each is
# replaced with the model's own text, so the two cannot disagree.
MAP_FLAG_WIDENED_CHECKS = ("ck_map_flag_category", "ck_map_flag_visibility",
                           "ck_map_flag_audience")
# Any number, as long as nothing else in the app uses it: serialises the
# upgrade when several workers start at once.
MAP_SCHEMA_LOCK = 4207311

_map_schema_checked = False


def map_flags_need_upgrade(inspector):
    """True when map_flags exists but predates event flags."""
    if not inspector.has_table("map_flags"):
        return False
    columns = {c["name"] for c in inspector.get_columns("map_flags")}
    return (not {"post_id", "updated_at"} <= columns
            or not inspector.has_table("map_flag_viewers"))


def upgrade_map_flags_schema(force=False):
    """Bring an older map_flags table up to this version. Safe to repeat.

    Adds the post_id and updated_at columns, widens the CHECK
    constraints (friends and going audiences), adds the one-flag-per-
    meetup rule and the map_flag_viewers table, then drops a flag for every
    open in-person meetup that is still to come. Leaves every existing row as
    it was. Returns a short report, or None when nothing needed doing.
    """
    from sqlalchemy import inspect, text

    with db.engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MAP_SCHEMA_LOCK})
        inspector = inspect(conn)
        if not inspector.has_table("map_flags"):
            return None   # a fresh database: create_all builds it whole
        if not (force or map_flags_need_upgrade(inspector)):
            return None

        columns = {c["name"] for c in inspector.get_columns("map_flags")}
        added_post_link = "post_id" not in columns
        if added_post_link:
            conn.execute(text(
                "ALTER TABLE map_flags ADD COLUMN post_id INTEGER "
                "REFERENCES game_posts (id) ON DELETE CASCADE"))
        if "updated_at" not in columns:
            conn.execute(text("ALTER TABLE map_flags ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE"))

        for constraint in MapFlag.__table__.constraints:
            if getattr(constraint, "name", None) in MAP_FLAG_WIDENED_CHECKS:
                conn.execute(text(f"ALTER TABLE map_flags DROP CONSTRAINT IF EXISTS {constraint.name}"))
                conn.execute(text(f"ALTER TABLE map_flags ADD CONSTRAINT {constraint.name} "
                                  f"CHECK ({constraint.sqltext})"))
        uniques = {u["name"] for u in inspector.get_unique_constraints("map_flags")}
        if "uq_map_flag_post" not in uniques:
            conn.execute(text("ALTER TABLE map_flags ADD CONSTRAINT uq_map_flag_post UNIQUE (post_id)"))
        MapFlagViewer.__table__.create(bind=conn, checkfirst=True)

    # Meetups posted before this version get their flags now -- once, when
    # the link column first appears, so a flag somebody later took down is
    # never put back.
    added = 0
    if added_post_link:
        waiting = db.session.scalars(
            db.select(GamePost).where(GamePost.mode == "inperson",
                                      GamePost.status == "open")
        ).all()
        for post in waiting:
            if make_event_flag(post) is not None:
                added += 1
        db.session.commit()
    return f"map_flags upgraded; {added} event flag(s) added for upcoming meetups."


@app.before_request
def map_schema_ready():
    """Upgrade an older map table the first time this process serves a
    request, so deploying this version needs no manual step. Runs its checks
    once per process; a fresh database is left to create_all."""
    global _map_schema_checked
    if _map_schema_checked:
        return None
    _map_schema_checked = True
    try:
        report = upgrade_map_flags_schema()
        if report:
            app.logger.info(report)
    except Exception:   # never take the site down over the map
        db.session.rollback()
        app.logger.exception("Could not upgrade map_flags; run `flask upgrade-map-flags`.")
    return None


@app.cli.command("upgrade-map-flags")
def upgrade_map_flags_command():
    """Bring map_flags up to date (event flags, friends). Safe to repeat."""
    report = upgrade_map_flags_schema(force=True)
    print(report or "Nothing to upgrade: map_flags is missing (run the app once) "
                    "or already up to date.")


@app.cli.command("init-map-table")
def init_map_table_command():
    """Create only map_flags (and map_flag_viewers); preserve every existing table and row."""
    import click
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    missing = [name for name in ("user", "clubs", "game_posts")
               if not inspector.has_table(name)]
    if missing:
        raise click.ClickException(
            "Map setup needs the original project database first. "
            "Missing tables: " + ", ".join(missing) +
            ". No tables or data were changed."
        )
    MapFlag.__table__.create(bind=db.engine, checkfirst=True)
    MapFlagViewer.__table__.create(bind=db.engine, checkfirst=True)
    upgrade_map_flags_schema()
    click.echo("Map tables ready. Existing tables and rows were left intact.")



# Profile segment begins here

# This segment of the code consists of functions that are set up to create
# an easy interface for communication between the existing back end
# and the back end for the profile page as the profile page
# is heavily dependent on them, as well as congregating other 
# functions from across the site into one easy accessible spot.

# This is made as a pipeline for all of the logic from the
# back to the profile, so if a change is made to 
# a variable or function elsewhere in the codebase, then
# we do not need to go through all 700-800 lines of backend for the
# profile figuring out what needs to be fitted to newer changes.
# Instead, we can change these references here in an easily
# accessible spot. 

def beacon_account_page_values(user):
    """The values the account route gives account.html, under the same names,
    so the profile can show the same three forms and include _questions.html."""
    return {
        # The question list: the same list read_answers() and save_answers() check.
        "questions": db.session.scalars(db.select(Question).order_by(Question.id)).all(),
        # The options this student picked: the query save_answers() uses for `had`.
        "selected": set(db.session.scalars(
            db.select(UserAnswer.option_id).filter_by(user_id=user.id))),
        # Questions that may be left blank: what read_answers() checks against.
        "optional": OPTIONAL_QUESTIONS,
        # The pronoun choices (slug -> label) and this student's slug.
        "pronoun_options": PRONOUNS,
        "pronouns_now": user.pronouns,
        # The longest name your account route accepts.
        "name_max": NAME_MAX,
        # What deleting the account takes with it: account_counts(),
        # which reads current_user (the profile only ever shows the signed-in
        # student's own page, so that is this student).
        "counts": account_counts(),
    }


def beacon_pronoun_label(user):
    """What to show beside the student's name, or None: pronoun_label(), so
    "prefer not to say" shows nothing here, exactly as everywhere else."""
    return pronoun_label(user.pronouns)


def beacon_joined_clubs(user):
    """The student's clubs and hubs (each with .id, .name, .description): every
    clubs row they have a ClubMember row for."""
    return db.session.scalars(
        db.select(Club)
        .join(ClubMember, ClubMember.club_id == Club.id)
        .where(ClubMember.user_id == user.id)
        .order_by(Club.name)
    ).all()


def beacon_leave_club(user, club_id):
    """Takes the student out of one club: leave_club route, without the
    commit, flash and redirect."""
    membership = db.session.get(ClubMember, (user.id, club_id))
    if membership is not None:
        db.session.delete(membership)


def beacon_end_friendship(user, other):
    """When `user` blocks `other`: the same call unmatch route makes.
    end_match may commit by itself; that is safe here, because the Block route
    only adds the block row before this call and commits straight after it."""
    matching.end_match(user.id, other.id)


def beacon_send_email(to, subject, body):
    """One plain-text email, sent the way send_verification_email() sends the
    sign-up code: your `mail` and `MailMessage`, from MAIL_DEFAULT_SENDER."""
    mail.send(MailMessage(subject=subject, recipients=[to], body=body))


def beacon_set_password(user, new_password):
    """Stores a new password the way your register route does:
    password_hash=generate_password_hash(password)."""
    user.password_hash = generate_password_hash(new_password)



# Every name that starts with "beacon_", "Beacon" or "_bc_", is made to
# avoid replacing or tampering with anything already in app.py, 
# and app will refuse to start (saying which) if one of its route names or 
# addresses is already in use. It never changes your existing tables: 
# it adds three new ones, all starting "beacon_",
# created automatically the first time the server answers a request.
# The tables created are specifically for the needs of profile.html.



# WHAT EACH PART OF profile.html DOES HERE
#   Page                 GET  /profile                profile           shows everything
#   Your profile (a,d-i) POST /profile/details        profile_details   picture, bio, major,
#                                                                       minor, year, looking for,
#                                                                       GitHub / LinkedIn / Instagram
#   Clubs (j)            POST /profile/clubs          profile_clubs     leave clubs
#   Account and safety   POST /profile/account        profile_account   unblock, new password
#     (l, o)             POST /profile/password-code  password_code     6-digit code by email,
#                                                                       10 minutes, hashed,
#                                                                       one a minute, 5 tries
#   Block button         POST /block/<id>             block_user        for a future Block button
#
# WHEN YOUR account_delete DELETES A STUDENT
#   It deletes the user with db.session.delete(...). Just before that row goes,
#   this part removes the student's beacon rows (profile, password code, blocks
#   either way), and once the deletion is committed it deletes their picture file.
#   If the deletion is rolled back, the picture is kept.
#
# These imports are specific to this part of the code, and it will
# not start without these references.
import datetime as _bc_dt
import os as _bc_os
import re as _bc_re
import secrets as _bc_secrets
import urllib.parse as _bc_url

import click as _bc_click
import flask as _bc_flask
import flask_login as _bc_login
import sqlalchemy as _bc_sa
import sqlalchemy.orm as _bc_orm
from werkzeug import security as _bc_security


# ----------------------------------------------------------------------------- lists

# UMass Boston undergraduate majors. Sources: the university's "College and Major
# Listing" (January 2024) and the College of Management's five new majors.
# Check against the current catalog before launch.
BEACON_MAJORS = ["Undeclared"] + sorted([
    "Accounting", "Africana Studies", "Aging Studies", "American Studies", "Anthropology", "Art",
    "Asian Studies", "Biochemistry", "Biology", "Chemistry", "Classical Languages", "Classical Studies",
    "Communication", "Community Development", "Computer Engineering", "Computer Science",
    "Criminology and Criminal Justice", "Early Childhood Education and Care",
    "Early Education and Care in Inclusive Settings", "Economics", "Electrical Engineering",
    "Engineering Physics", "English", "Entrepreneurship", "Environmental Sciences",
    "Exercise and Health Sciences", "Finance", "French", "Global Affairs", "History", "Human Services",
    "Information Systems and Business Analytics", "Information Technology", "International Relations",
    "Italian", "Labor Studies", "Latin American and Iberian Studies", "Management", "Marketing",
    "Mathematics", "Music", "Nursing", "Philosophy", "Philosophy and Public Policy",
    "Philosophy, Law and Ethics", "Physics", "Political Science", "Psychology",
    "Sociology", "Sport Leadership and Administration", "Theatre Arts", "Urban Public Health",
])

# UMass Boston undergraduate minors. Source: Office of Institutional Research,
# Table 32 (fall 2017 to fall 2024). "None" is added by the template.
BEACON_MINORS = sorted([
    "Africana Studies", "Aging Studies", "American Studies", "Anthropology", "Arabic", "Art History",
    "Biology", "Chemistry", "Chinese", "Cinema Studies", "Classical Languages", "Classical Studies",
    "Clean Energy", "Cognitive Science", "Communication Studies", "Community Development",
    "Computer Science", "Creative Writing", "Criminal Justice", "Cross Cultural East Asian Studies",
    "Dance", "East Asian Languages", "Economics", "English", "Environmental Anthropology",
    "Environmental Biology", "Environmental Chemistry", "French", "Geospatial Analysis and Modeling",
    "German Studies", "History", "Human Rights", "International Relations", "Irish Studies", "Italian",
    "Japanese", "Labor Studies", "Latin American Studies", "Latino Studies", "Management", "Mathematics",
    "Music", "Native American and Indigenous Studies", "Philosophy", "Philosophy and Law", "Physics",
    "Political Science", "Portuguese", "Professional Writing and New Media", "Psychology", "Public Policy",
    "Religious Studies", "Science, Medicine, and Society", "Secondary Education", "Sexuality Studies",
    "Sociology", "South Asian Studies", "Spanish", "Theatre Arts", "Wealth, Poverty, and Opportunity",
    "Women's, Gender, and Sexuality Studies",
])

BEACON_YEARS = ["First-year", "Sophomore", "Junior", "Senior", "Graduate student"]

# One box per platform, username only. max = that platform's own username limit.
# Patterns are valid in both the browser (pattern="...") and Python re.fullmatch.
BEACON_SOCIALS = [
    {"key": "github", "label": "GitHub", "prefix": "github.com/", "max": 39,
     "pattern": r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}",
     "rule": "Up to 39 letters, numbers or single hyphens."},
    {"key": "linkedin", "label": "LinkedIn", "prefix": "linkedin.com/in/", "max": 100,
     "pattern": r"[A-Za-z0-9\-]{3,100}",
     "rule": "3 to 100 letters, numbers or hyphens."},
    {"key": "instagram", "label": "Instagram", "prefix": "instagram.com/", "max": 30,
     "pattern": r"[A-Za-z0-9._]{1,30}",
     "rule": "Up to 30 letters, numbers, periods or underscores."},
]

BEACON_PICTURE_LIMIT = 2 * 1024 * 1024     # 2 MB, the same limit the browser checks
BEACON_REQUEST_LIMIT = 3 * 1024 * 1024     # the whole "Your profile" form, picture included
BEACON_TEXT_MAX = 200                      # bio and "looking for"
BEACON_CODE_MINUTES = 10                   # a password code lasts this long
BEACON_CODE_RESEND_SECONDS = 60            # at most one code a minute
BEACON_CODE_MAX_TRIES = 5                  # wrong codes before it stops working
BEACON_PASSWORD_MIN = 8                    # the same minimum as your register route


def _bc_now():
    """The time now in UTC, without a time zone (how the tables store it)."""
    return _bc_dt.datetime.now(_bc_dt.timezone.utc).replace(tzinfo=None)


# ----------------------------------------------------------------------------- tables

_bc_user_pk = User.__mapper__.primary_key
if len(_bc_user_pk) != 1 or not isinstance(_bc_user_pk[0].type, _bc_sa.Integer):
    raise RuntimeError("Beacon profile: User's primary key must be one whole-number (Integer) column; "
                       "the new tables store user ids as whole numbers.")
_bc_user_id_column = "%s.%s" % (User.__table__.name, _bc_user_pk[0].name)


class BeaconProfile(db.Model):
    """The profile fields for one student. Made on their first visit to /profile."""
    __tablename__ = "beacon_profile"
    user_id = db.Column(db.Integer, db.ForeignKey(_bc_user_id_column, ondelete="CASCADE"), primary_key=True)
    picture = db.Column(db.String(80))                    # file name in the upload folder, or None
    bio = db.Column(db.String(200), nullable=False, default="")
    major = db.Column(db.String(80), nullable=False, default="Undeclared")
    minor = db.Column(db.String(80), nullable=False, default="")
    year = db.Column(db.String(30), nullable=False, default="")
    looking_for = db.Column(db.String(200), nullable=False, default="")
    github = db.Column(db.String(39), nullable=False, default="")
    linkedin = db.Column(db.String(100), nullable=False, default="")
    instagram = db.Column(db.String(30), nullable=False, default="")


class BeaconBlock(db.Model):
    """blocker_id blocked blocked_id at created_at. Nobody can block themselves."""
    __tablename__ = "beacon_block"
    blocker_id = db.Column(db.Integer, db.ForeignKey(_bc_user_id_column, ondelete="CASCADE"), primary_key=True)
    blocked_id = db.Column(db.Integer, db.ForeignKey(_bc_user_id_column, ondelete="CASCADE"), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_bc_now)
    __table_args__ = (db.CheckConstraint("blocker_id <> blocked_id", name="beacon_block_not_self"),)


class BeaconPasswordCode(db.Model):
    """The current 6-digit code for changing a password. Only its hash is stored."""
    __tablename__ = "beacon_password_code"
    user_id = db.Column(db.Integer, db.ForeignKey(_bc_user_id_column, ondelete="CASCADE"), primary_key=True)
    code_hash = db.Column(db.String(255), nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    tries = db.Column(db.Integer, nullable=False, default=0)


# ----------------------------------------------------------------------------- checking form values

_bc_control_chars = _bc_re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _bc_text(name, multiline=False):
    """A form value as clean text: no control characters (PostgreSQL refuses \\x00),
    Windows line ends made plain, spaces trimmed, and on one line unless multiline."""
    value = _bc_flask.request.form.get(name, "")
    if not isinstance(value, str):
        return ""
    value = _bc_control_chars.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    if not multiline:
        value = " ".join(value.split())
    return value.strip()


def _bc_id(value):
    """A positive whole number from a form or address, or None. Never raises."""
    if isinstance(value, int):
        return value if 0 < value < 2 ** 31 else None
    if isinstance(value, str) and value.isdigit() and len(value) < 10:
        number = int(value)
        return number if number > 0 else None
    return None


def _bc_same_site():
    """Refuses a POST sent by another website (the browser sends Origin on form posts)."""
    origin = _bc_flask.request.headers.get("Origin")
    if origin and origin != "null" and _bc_url.urlsplit(origin).netloc != _bc_flask.request.host:
        _bc_flask.abort(400)


def _bc_too_big():
    """True if this request's body is over BEACON_REQUEST_LIMIT. Checked before the
    form is read, on the one route that takes a picture only."""
    length = _bc_flask.request.content_length
    return length is not None and length > BEACON_REQUEST_LIMIT


def _bc_back(section, messages, saved=False):
    """Back to one section of the profile, with what happened shown inside it.
    On a problem nothing was saved, so anything half-written is rolled back."""
    if not saved:
        db.session.rollback()
    _bc_flask.session["pf_note"] = {"section": section, "saved": saved, "messages": list(messages)}
    return _bc_flask.redirect(_bc_flask.url_for("profile") + "#" + section)


# RELOCATION: the two helpers below are new.

def _bc_hand_over(endpoint, section, anchor):
    """Runs one of YOUR routes (account or account_delete) on this same request,
    unchanged. The profile's forms send the same fields account.html sends
    (section, name, pronouns, q<id>, password), so your route reads, checks,
    saves and flashes exactly as it does for Your account.

    Only where it sends the student next is changed. Your route answers in one
    of three ways (checked against your account route, Oct 24):
      - it saved, flashed "Saved." and redirected to /account: the student goes
        back to this window of the profile, with your message inside it;
      - it found a problem (a blank or too-long name, a missing answer), flashed
        it and drew Your account again: the same, back to this window with your
        message, so the student never leaves the profile;
      - anything else (signing out after a delete, say) is passed on untouched."""
    response = _bc_flask.make_response(app.view_functions[endpoint]())
    sent_to_account = (response.status_code in (301, 302, 303, 307, 308)
                       and _bc_url.urlsplit(response.location or "").path == _bc_flask.url_for("account"))
    messages = []
    if sent_to_account or (response.status_code == 200 and _bc_login.current_user.is_authenticated):
        # What your route flashed: still queued after a redirect, or already
        # read by the Your account page it drew (Flask keeps them for the request).
        messages = [str(m) for m in _bc_flask.get_flashed_messages()]
    if sent_to_account or messages:
        if messages:
            # saved=None: these are your route's own words, shown as they are
            # (it decides what counts as saved, not this part).
            _bc_flask.session["pf_note"] = {"section": section, "saved": None, "messages": messages}
        return _bc_flask.redirect(_bc_flask.url_for("profile") + "#" + anchor)
    return response


def _bc_safe_next(value):
    """Where the account menu's photo form was sent from: a path on this site
    only (like /people). Anything else, such as another website's address, goes
    to the profile instead."""
    if isinstance(value, str) and _bc_re.fullmatch(r"/(?![/\\])[^\s\\\x00-\x1f\x7f]*", value):
        return value
    return _bc_flask.url_for("profile")


# ----------------------------------------------------------------------------- pictures

def _bc_upload_folder():
    folder = app.config.get("BEACON_UPLOAD_FOLDER") or _bc_os.path.join(app.static_folder, "uploads")
    _bc_os.makedirs(folder, exist_ok=True)
    return folder


def _bc_is_image(head):
    """True for PNG, JPEG, GIF or WebP, judged by the file's first bytes (not its name)."""
    return (head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff")
            or head[:6] in (b"GIF87a", b"GIF89a") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def _bc_picture_path(name):
    """The file's path, only for names this code made (u<id>-<hex>.<ext>)."""
    if not name or not _bc_re.fullmatch(r"u\d+-[0-9a-f]{12}\.(png|jpg|gif|webp)", name):
        return None
    return _bc_os.path.join(_bc_upload_folder(), name)


def _bc_remove_file(name):
    path = _bc_picture_path(name)
    if path:
        try:
            _bc_os.remove(path)
        except OSError:
            pass


def _bc_store_picture(row, upload):
    """Saves a new picture for this profile row. Returns (old file name, error)."""
    data = upload.read(BEACON_PICTURE_LIMIT + 1)
    if len(data) > BEACON_PICTURE_LIMIT:
        return None, "The picture must be under 2 MB."
    if not _bc_is_image(data[:12]):
        return None, "The picture must be a JPG, PNG, GIF or WebP image."
    ext = {b"\x89P": "png", b"\xff\xd8": "jpg", b"GI": "gif", b"RI": "webp"}[data[:2]]
    name = "u%d-%s.%s" % (row.user_id, _bc_secrets.token_hex(6), ext)
    with open(_bc_picture_path(name), "wb") as out:
        out.write(data)
    old, row.picture = row.picture, name
    _bc_flask.g.pop("_bc_pictures", None)          # forget the old address for this request
    return old, None


def beacon_picture_url(user_id):
    """The picture's address, or None (the page then shows your initials avatar).
    Looked up once per request per student.

    RELOCATION: the account menu now shows the photo on every page, so this runs
    on every page with your top bar: one small query, by primary key. It makes
    sure the beacon_ tables exist first, and if they could not be made it shows
    initials rather than breaking the page."""
    cache = _bc_flask.g.setdefault("_bc_pictures", {}) if _bc_flask.has_request_context() else {}
    if user_id not in cache:
        _bc_ensure_tables()
        if not _bc_tables_ready:
            return None
        with db.session.no_autoflush:          # a lookup only: never writes pending changes
            row = db.session.get(BeaconProfile, user_id)
        name = row.picture if row else None
        path = _bc_picture_path(name)
        # The file itself must be there too: if it is gone (a server whose disk is
        # wiped on each redeploy, say), initials show instead of a broken image.
        cache[user_id] = name if path and _bc_os.path.isfile(path) else None
    name = cache[user_id]
    if not name:
        return None
    if app.config.get("BEACON_UPLOAD_FOLDER"):
        return _bc_flask.url_for("beacon_upload", name=name)
    return _bc_flask.url_for("static", filename="uploads/" + name)


# ----------------------------------------------------------------------------- helpers for your own routes

def beacon_profile_row(user_id):
    """The student's profile row, made on first use (not committed)."""
    row = db.session.get(BeaconProfile, user_id)
    if row is None:
        row = BeaconProfile(user_id=user_id)
        db.session.add(row)
        db.session.flush()
    return row


def beacon_blocked_ids(user_id):
    """Everyone on either side of a block with this student. For later: leave them
    out of people search, suggestions, chats and friend requests in your own routes."""
    rows = BeaconBlock.query.filter((BeaconBlock.blocker_id == user_id) | (BeaconBlock.blocked_id == user_id)).all()
    return {r.blocked_id if r.blocker_id == user_id else r.blocker_id for r in rows}


def beacon_is_blocked(a_id, b_id):
    """True if either student blocked the other."""
    return BeaconBlock.query.filter(
        ((BeaconBlock.blocker_id == a_id) & (BeaconBlock.blocked_id == b_id))
        | ((BeaconBlock.blocker_id == b_id) & (BeaconBlock.blocked_id == a_id))).first() is not None


# ----------------------------------------------------------------------------- when your account_delete removes a student

@_bc_sa.event.listens_for(User, "before_delete")
def _bc_user_leaving(mapper, connection, target):
    """Runs inside your account_delete's own transaction, just before the users row
    is deleted: removes this student's beacon_ rows (so it works the same on any
    database) and remembers the picture file to delete once the deletion commits."""
    profile = BeaconProfile.__table__
    picture = connection.execute(_bc_sa.select(profile.c.picture).where(profile.c.user_id == target.id)).scalar()
    connection.execute(profile.delete().where(profile.c.user_id == target.id))
    code = BeaconPasswordCode.__table__
    connection.execute(code.delete().where(code.c.user_id == target.id))
    block = BeaconBlock.__table__
    connection.execute(block.delete().where((block.c.blocker_id == target.id) | (block.c.blocked_id == target.id)))
    session = _bc_orm.object_session(target)
    if picture and session is not None:
        session.info.setdefault("_bc_files_to_remove", []).append(picture)


@_bc_sa.event.listens_for(_bc_orm.Session, "after_commit")
def _bc_remove_files_after_commit(session):
    for name in session.info.pop("_bc_files_to_remove", []):
        _bc_remove_file(name)


@_bc_sa.event.listens_for(_bc_orm.Session, "after_rollback")
def _bc_keep_files_after_rollback(session):
    session.info.pop("_bc_files_to_remove", None)


# ----------------------------------------------------------------------------- the routes

@_bc_login.login_required
def _bc_profile_page():
    me = _bc_login.current_user
    row = beacon_profile_row(me.id)
    db.session.commit()
    code = db.session.get(BeaconPasswordCode, me.id)
    code_sent = None
    if code and code.expires_at > _bc_now():
        code_sent = {"to": me.email, "preview_code": _bc_flask.session.get("beacon_preview_code")}
    blocked = []
    for block in BeaconBlock.query.filter_by(blocker_id=me.id).order_by(BeaconBlock.created_at.desc()).all():
        other = db.session.get(User, block.blocked_id)
        if other is not None:
            blocked.append({"user_id": other.id, "name": other.name, "pronouns": beacon_pronoun_label(other),
                            "picture_url": beacon_picture_url(other.id),
                            "blocked_on": block.created_at.strftime("%b %d, %Y").replace(" 0", " ")})
    return _bc_flask.render_template(
        "profile.html",
        profile=row, pronoun_label=beacon_pronoun_label(me),
        majors=BEACON_MAJORS, minors=BEACON_MINORS, years=BEACON_YEARS, socials=BEACON_SOCIALS,
        text_max=BEACON_TEXT_MAX, code_minutes=BEACON_CODE_MINUTES, password_min=BEACON_PASSWORD_MIN,
        joined=beacon_joined_clubs(me), blocked=blocked, code_sent=code_sent,
        note=_bc_flask.session.pop("pf_note", None),
        # RELOCATION: what account.html is given (questions, selected, optional,
        # pronoun_options, pronouns_now, name_max, counts), from part 1.
        **beacon_account_page_values(me),
    )


@_bc_login.login_required
def _bc_profile_details():
    """Picture, bio, major, minor, year, looking for, usernames. Checks everything
    first; saves nothing if any part is wrong."""
    _bc_same_site()
    if _bc_too_big():
        return _bc_back("profile", ["The picture must be under 2 MB."])
    problems = []
    bio = _bc_text("bio", multiline=True)
    looking = _bc_text("looking_for", multiline=True)
    if len(bio) > BEACON_TEXT_MAX:
        problems.append("Your bio can be up to %d characters." % BEACON_TEXT_MAX)
    if len(looking) > BEACON_TEXT_MAX:
        problems.append("What you're looking for can be up to %d characters." % BEACON_TEXT_MAX)
    major, minor, year = _bc_text("major"), _bc_text("minor"), _bc_text("year")
    if major not in BEACON_MAJORS:
        problems.append("Pick a major from the list.")
    if minor and minor not in BEACON_MINORS:
        problems.append("Pick a minor from the list, or None.")
    if year not in BEACON_YEARS:
        problems.append("Pick your class year.")
    handles = {}
    for social in BEACON_SOCIALS:
        value = _bc_text(social["key"]).lstrip("@")
        if value and not _bc_re.fullmatch(social["pattern"], value):
            problems.append("%s username: %s" % (social["label"], social["rule"]))
        handles[social["key"]] = value
    if problems:
        return _bc_back("profile", problems)

    row = beacon_profile_row(_bc_login.current_user.id)
    old_picture = None
    upload = _bc_flask.request.files.get("picture")
    if _bc_flask.request.form.get("remove_picture") == "1":
        old_picture, row.picture = row.picture, None
    elif upload and upload.filename:
        old_picture, problem = _bc_store_picture(row, upload)
        if problem:
            return _bc_back("profile", [problem])
    row.bio, row.looking_for, row.major, row.minor, row.year = bio, looking, major, minor, year
    for key, value in handles.items():
        setattr(row, key, value)
    db.session.commit()
    if old_picture and old_picture != row.picture:
        _bc_remove_file(old_picture)              # only once the new value is saved
    return _bc_back("profile", ["Saved."], saved=True)


@_bc_login.login_required
def _bc_profile_clubs():
    """Leave clubs. Only clubs the student is in can be left."""
    _bc_same_site()
    me = _bc_login.current_user
    leave = {_bc_id(v) for v in _bc_flask.request.form.getlist("leave")} - {None}
    if not leave:
        return _bc_back("clubs", ["Tick Remove next to a club first."])
    mine = {club.id: club.name for club in beacon_joined_clubs(me)}
    if leave - set(mine):
        return _bc_back("clubs", ["One of those clubs isn't one of yours. Reload the page and try again."])
    for club_id in sorted(leave):
        beacon_leave_club(me, club_id)
    db.session.commit()
    return _bc_back("clubs", ["You left %s." % ", ".join(sorted(mine[c] for c in leave))], saved=True)


@_bc_login.login_required
def _bc_profile_account():
    """Unblock people, and set a new password with the emailed code."""
    _bc_same_site()
    me = _bc_login.current_user
    form = _bc_flask.request.form
    code = _bc_text("code")
    new, again = form.get("new_password", ""), form.get("confirm_password", "")
    unblock = {_bc_id(v) for v in form.getlist("unblock")} - {None}
    if not (code or new or again or unblock):
        return _bc_back("safety", ["Nothing to save yet."])
    done = []
    if code or new or again:
        sent = db.session.get(BeaconPasswordCode, me.id)
        if not sent or sent.expires_at < _bc_now():
            return _bc_back("safety", ["Send yourself a code first. Codes last %d minutes." % BEACON_CODE_MINUTES])
        if not _bc_re.fullmatch(r"\d{6}", code) or not _bc_security.check_password_hash(sent.code_hash, code):
            sent.tries += 1
            message = "That code isn't right. Check the email and try again."
            if sent.tries >= BEACON_CODE_MAX_TRIES:
                db.session.delete(sent)
                message = "Too many wrong codes. Send yourself a new one."
            db.session.commit()                      # keep the count of wrong tries
            return _bc_back("safety", [message])
        if not BEACON_PASSWORD_MIN <= len(new) <= 200:
            return _bc_back("safety", ["The new password must be %d to 200 characters." % BEACON_PASSWORD_MIN])
        if new != again:
            return _bc_back("safety", ["The two new passwords do not match."])
        beacon_set_password(me, new)
        db.session.delete(sent)
        _bc_flask.session.pop("beacon_preview_code", None)
        done.append("Your password is changed.")
    if unblock:
        n = BeaconBlock.query.filter(BeaconBlock.blocker_id == me.id,
                                     BeaconBlock.blocked_id.in_(unblock)).delete(synchronize_session=False)
        if n:
            done.append("Unblocked %d %s." % (n, "person" if n == 1 else "people"))
    db.session.commit()
    return _bc_back("safety", done or ["Saved."], saved=True)


@_bc_login.login_required
def _bc_password_code():
    """Emails a 6-digit code, like your sign-up code. Stored hashed; one a minute."""
    _bc_same_site()
    me = _bc_login.current_user
    now = _bc_now()
    row = db.session.get(BeaconPasswordCode, me.id)
    if row and (now - row.sent_at).total_seconds() < BEACON_CODE_RESEND_SECONDS:
        return _bc_back("safety", ["A code was sent less than a minute ago. Check your email, "
                                   "or ask for a new one in a minute."])
    code = "%06d" % _bc_secrets.randbelow(1000000)
    if row is None:
        row = BeaconPasswordCode(user_id=me.id)
        db.session.add(row)
    row.code_hash = _bc_security.generate_password_hash(code)
    row.sent_at, row.expires_at, row.tries = now, now + _bc_dt.timedelta(minutes=BEACON_CODE_MINUTES), 0
    db.session.flush()
    try:
        beacon_send_email(me.email, "Your BeaconsConnect password code",
                          "Your code for changing your BeaconsConnect password is %s.\n"
                          "It expires in %d minutes. If you did not ask for it, you can ignore this email."
                          % (code, BEACON_CODE_MINUTES))
    except Exception:
        app.logger.exception("Beacon profile: the password code email was not sent")
        return _bc_back("safety", ["We couldn't send the email just now. Try again in a few minutes."])
    db.session.commit()
    if app.config.get("BEACON_SHOW_CODES"):          # local testing only: shows the code on the page
        _bc_flask.session["beacon_preview_code"] = code
    return _bc_back("safety", ["Code sent to %s. It expires in %d minutes." % (me.email, BEACON_CODE_MINUTES)],
                    saved=True)


@_bc_login.login_required
def _bc_block_user(other_id):
    """For a Block button (a form posting here). Ends the friendship and hides both ways."""
    _bc_same_site()
    me = _bc_login.current_user
    other = db.session.get(User, other_id)
    if other is None or other.id == me.id:
        _bc_flask.abort(404)
    if db.session.get(BeaconBlock, (me.id, other.id)) is None:
        db.session.add(BeaconBlock(blocker_id=me.id, blocked_id=other.id))
    beacon_end_friendship(me, other)
    db.session.commit()
    return _bc_back("safety", ["You blocked %s." % other.name], saved=True)


# the three routes below are new.

@_bc_login.login_required
def _bc_profile_name_interests():
    """The profile's "Name and pronouns" and "Your interests" forms. They carry
    the same hidden `section` as account.html's forms ("who" or "answers"), and
    your account route does the rest."""
    _bc_same_site()
    section = _bc_flask.request.form.get("section")
    if section == "who":
        return _bc_hand_over("account", "who", "profile")
    if section == "answers":
        return _bc_hand_over("account", "interests", "pf-interests")
    _bc_flask.abort(400)


@_bc_login.login_required
def _bc_profile_delete():
    """The profile's "Delete your account" form: your account_delete route checks
    the password and deletes, exactly as from Your account. A wrong password comes
    back to this window with your route's message."""
    _bc_same_site()
    return _bc_hand_over("account_delete", "leaving", "pf-delete")


@_bc_login.login_required
def _bc_profile_picture():
    """The account menu's photo control (shell.html), on every page: a new photo,
    or remove it. Saves only the photo, then goes back to the page it was sent
    from, with what happened as one of your usual flashed messages."""
    _bc_same_site()
    # `next` is in the address, not the form, so it can be read without
    # reading a body that may be too big.
    back = _bc_safe_next(_bc_flask.request.args.get("next"))
    if _bc_too_big():
        _bc_flask.flash("Your photo was not changed: the picture must be under 2 MB.")
        return _bc_flask.redirect(back)
    row = beacon_profile_row(_bc_login.current_user.id)
    if _bc_flask.request.form.get("remove") == "1":
        if not row.picture:
            db.session.rollback()
            _bc_flask.flash("You don't have a photo to remove.")
            return _bc_flask.redirect(back)
        old_picture, row.picture = row.picture, None
        message = "Your photo is removed. Your initials show instead."
    else:
        upload = _bc_flask.request.files.get("picture")
        if not upload or not upload.filename:
            db.session.rollback()
            _bc_flask.flash("Your photo was not changed: pick a picture first.")
            return _bc_flask.redirect(back)
        old_picture, problem = _bc_store_picture(row, upload)
        if problem:
            db.session.rollback()
            _bc_flask.flash("Your photo was not changed: " + problem[0].lower() + problem[1:])
            return _bc_flask.redirect(back)
        message = "Your photo is saved."
    db.session.commit()
    if old_picture and old_picture != row.picture:
        _bc_remove_file(old_picture)              # only once the new value is saved
    _bc_flask.flash(message)
    return _bc_flask.redirect(back)


def _bc_serve_upload(name):
    """Pictures, when BEACON_UPLOAD_FOLDER is outside the static folder."""
    path = _bc_picture_path(name)
    if not path or not _bc_os.path.exists(path):
        _bc_flask.abort(404)
    return _bc_flask.send_from_directory(_bc_upload_folder(), name)


BEACON_ROUTES = [
    ("/profile", "profile", _bc_profile_page, ["GET"]),
    ("/profile/details", "profile_details", _bc_profile_details, ["POST"]),
    ("/profile/clubs", "profile_clubs", _bc_profile_clubs, ["POST"]),
    ("/profile/account", "profile_account", _bc_profile_account, ["POST"]),
    ("/profile/password-code", "password_code", _bc_password_code, ["POST"]),
    ("/block/<int:other_id>", "block_user", _bc_block_user, ["POST"]),
    # RELOCATION: new
    ("/profile/name-and-interests", "profile_name_interests", _bc_profile_name_interests, ["POST"]),
    ("/profile/delete", "profile_delete", _bc_profile_delete, ["POST"]),
    ("/profile/picture", "profile_picture", _bc_profile_picture, ["POST"]),
]


# ----------------------------------------------------------------------------- set-up check

# Your routes that profile.html links to, or hands its forms to.
# account_delete, people and chats added.
BEACON_ROUTES_NEEDED = ["account", "account_delete", "report_new", "clubs", "chat", "people", "chats"]
BEACON_PART_1 = ["beacon_account_page_values",                                  # RELOCATION: new
                 "beacon_pronoun_label", "beacon_joined_clubs", "beacon_leave_club",
                 "beacon_end_friendship", "beacon_send_email", "beacon_set_password"]


def beacon_setup_problems():
    """Everything that is not connected yet, as plain sentences. [] means ready."""
    found = []
    here = globals()
    for name in BEACON_PART_1:
        if not callable(here.get(name)):
            found.append("Part 1 is missing %s(). Paste 1_profile_connect.py above this part." % name)
    for column in ("name", "email", "pronouns", "password_hash"):
        if not hasattr(User, column):
            found.append("User has no %r; the profile reads current_user.%s." % (column, column))
    if getattr(app, "login_manager", None) is None:
        found.append("Flask-Login's LoginManager is not set up on app.")
    for endpoint in BEACON_ROUTES_NEEDED:
        if endpoint not in app.view_functions:
            found.append("Your app has no route named %r; profile.html links to it." % endpoint)
    with app.test_request_context():
        for endpoint, args in (("chat", {"kind": "club", "target_id": 1}),
                               ("report_new", {"kind": "abuse", "about": 1})):
            if endpoint in app.view_functions:
                try:
                    _bc_flask.url_for(endpoint, **args)
                except Exception:
                    found.append("profile.html links to %s(%s), and your route can't be called that way."
                                 % (endpoint, ", ".join("%s=..." % k for k in args)))
    for template in ("profile.html", "_questions.html"):     # RELOCATION: profile.html includes _questions.html
        try:
            app.jinja_env.get_template(template)
        except Exception as error:                     # missing, or a syntax error in it
            found.append("Template %s: %s" % (template, error))
    try:
        shell = app.jinja_env.loader.get_source(app.jinja_env, "shell.html")[0]
        # RELOCATION: the account menu links to the profile and holds the photo control.
        for endpoint, ends in (("profile", ")"), ("profile_picture", "")):
            if ("url_for('%s'%s" % (endpoint, ends)) not in shell and ('url_for("%s"%s' % (endpoint, ends)) not in shell:
                found.append("shell.html's account menu has no url_for('%s') yet. Use the account menu "
                             "from this kit's shell.html (HOW_TO_PUSH.md, step 3)." % endpoint)
    except Exception as error:
        found.append("Could not read shell.html: %s" % error)
    if "mail" not in app.extensions:
        found.append("Flask-Mail is not set up on app, so \"Email me a code\" cannot send.")
    elif not app.config.get("MAIL_DEFAULT_SENDER"):
        found.append("MAIL_DEFAULT_SENDER is not set, so \"Email me a code\" cannot send.")
    folder = app.config.get("BEACON_UPLOAD_FOLDER") or _bc_os.path.join(app.static_folder or "", "uploads")
    where = folder if _bc_os.path.isdir(folder) else _bc_os.path.dirname(folder)
    if not _bc_os.access(where, _bc_os.W_OK):
        found.append("The server cannot write to %s, so profile pictures cannot be saved." % where)
    return found


def _bc_tables():
    """The three tables this code adds (and the only ones it ever creates)."""
    return [m.__table__ for m in (BeaconProfile, BeaconBlock, BeaconPasswordCode)]


_bc_tables_checked = []
_bc_tables_ready = []        #  [True] once the three tables are known to exist


def _bc_ensure_tables():
    """Before this server process's first request: creates any of the three beacon_
    tables that do not exist yet (checkfirst=True: only these, never yours)."""
    if _bc_tables_checked:
        return None
    _bc_tables_checked.append(True)
    try:
        db.metadata.create_all(bind=db.engine, tables=_bc_tables(), checkfirst=True)
        _bc_tables_ready.append(True)
    except Exception:                       # never take the site down over this
        db.session.rollback()
        app.logger.exception("Beacon profile: could not create the beacon_ tables; "
                             "run `flask --app app beacon-create-tables`.")
    return None


def _bc_register():
    taken = [name for _, name, _, _ in BEACON_ROUTES if name in app.view_functions]
    if taken:
        raise RuntimeError("Beacon profile: app.py already has route(s) named %s. Rename one of them, "
                           "then paste again." % ", ".join(taken))

    def shape(rule):
        return _bc_re.sub(r"<[^>]+>", "<>", rule)
    clashes = ["%s (your route %r)" % (rule, existing.endpoint)
               for rule, _, _, methods in BEACON_ROUTES for existing in app.url_map.iter_rules()
               if shape(existing.rule) == shape(rule) and set(methods) & set(existing.methods or ())]
    if clashes:
        raise RuntimeError("Beacon profile: these addresses are already used in app.py: %s. Give one of "
                           "them a different address, then paste again." % ", ".join(clashes))
    for rule, endpoint, view, methods in BEACON_ROUTES:
        app.add_url_rule(rule, endpoint, view, methods=methods)
    if app.config.get("BEACON_UPLOAD_FOLDER"):
        app.add_url_rule("/uploads/<name>", "beacon_upload", _bc_serve_upload)
    if not hasattr(User, "picture_url"):
        # profile.html and the top bar in shell.html read current_user.picture_url.
        User.picture_url = property(lambda user: beacon_picture_url(user.id))
    app.before_request(_bc_ensure_tables)


@app.cli.command("beacon-check")
def _bc_cli_check():
    """Says whether the profile backend is fully connected."""
    problems = beacon_setup_problems()
    with app.app_context():
        inspector = _bc_sa.inspect(db.engine)
        missing = [t.name for t in _bc_tables() if not inspector.has_table(t.name)]
    if missing:
        problems.append("Tables not created yet: %s. They are created on the first page load, or run: "
                        "flask --app app beacon-create-tables" % ", ".join(missing))
    with app.app_context():
        user = User.query.order_by(_bc_user_pk[0]).first()
        if user is not None:
            for name, call in [("beacon_pronoun_label", lambda: beacon_pronoun_label(user)),
                               ("beacon_joined_clubs",
                                lambda: [(c.id, c.name, c.description) for c in beacon_joined_clubs(user)])]:
                try:
                    call()
                except Exception as error:
                    problems.append("%s() failed on the student with id %s: %s: %s"
                                    % (name, user.id, type(error).__name__, error))
            #  the values copied from your account route, read as that
            # student (account_counts() reads current_user).
            with app.test_request_context():
                _bc_login.login_user(user)
                try:
                    values = beacon_account_page_values(user)
                    wanted = {"questions", "selected", "optional", "pronoun_options", "pronouns_now",
                              "name_max", "counts"}
                    if not wanted <= set(values):
                        problems.append("beacon_account_page_values() is missing: %s"
                                        % ", ".join(sorted(wanted - set(values))))
                except Exception as error:
                    problems.append("beacon_account_page_values() failed on the student with id %s: %s: %s"
                                    % (user.id, type(error).__name__, error))
                _bc_login.logout_user()
        db.session.rollback()
    if problems:
        _bc_click.echo("Beacon profile: %d thing(s) to fix:" % len(problems))
        for p in problems:
            _bc_click.echo("  - " + p)
    else:
        _bc_click.echo("Beacon profile: everything is connected.")


@app.cli.command("beacon-create-tables")
def _bc_cli_create():
    """Creates the three beacon_ tables. Existing tables are not touched."""
    tables = _bc_tables()
    db.metadata.create_all(bind=db.engine, tables=tables, checkfirst=True)
    _bc_click.echo("Beacon profile: tables ready: " + ", ".join(t.name for t in tables))


_bc_register()





if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # create_all adds missing tables but never changes existing ones.
        upgrade_map_flags_schema()
    app.run()
