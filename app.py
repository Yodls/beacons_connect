import os
import secrets
from datetime import datetime, timedelta, timezone

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
from models import (
    Activity,
    ActivityLocation,
    Club,
    ClubMember,
    Game,
    GamePairing,
    GamePost,
    GamePostRsvp,
    GameQueue,
    Match,
    Message,
    Question,
    User,
    UserAnswer,
    db,
    pair,
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


@app.before_request
def require_onboarding():
    if request.endpoint is None or request.endpoint in ONBOARDING_EXEMPT:
        return None

    if not current_user.is_authenticated:
        return None

    if has_onboarded(current_user.id):
        return None

    return redirect(url_for("onboarding"))


@app.context_processor
def inject_pending():
    """How many friend requests are waiting on an answer from you.

    This runs for every template, the signed-out ones included, so the
    authentication check is what keeps the login page from blowing up.
    """
    if not current_user.is_authenticated:
        return {}

    return {"pending_count": db.session.scalar(
        db.select(db.func.count()).select_from(Match).where(
            Match.status == "invited",
            # Requests you sent are not waiting on you.
            Match.requested_by != current_user.id,
            db.or_(Match.user_lo == current_user.id,
                   Match.user_hi == current_user.id),
        )
    )}


RETURN_TO = {"matches", "clubs"}


def joined_clubs(user_id):
    return db.session.scalars(
        db.select(Club)
        .join(ClubMember, ClubMember.club_id == Club.id)
        .where(ClubMember.user_id == user_id)
        .order_by(Club.name)
    ).all()


def back_to():
    target = request.form.get("back", "matches")
    return redirect(url_for(target if target in RETURN_TO else "matches"))


def back_to_people():
    """Back to the search, carrying the term so the results are still there."""
    term = request.form.get("q", "").strip()
    return redirect(url_for("people", q=term) if term else url_for("people"))


MESSAGE_MAX = 2000


COMMUNITY = "community"
COMMUNITY_TITLE = "Community Chat"
POST_MODES = {"online": "Online", "inperson": "In person"}

# There are four kinds of chat. Naming them here rather than in the templates
# keeps the two places that label a chat from drifting apart, and stops a new
# kind rendering as whichever one the else-branch happened to name.
CHAT_SUBTITLE = {
    COMMUNITY: "Everyone on campus",
    "match": "Friend",
    "pair": "Game pairing",
    "club": "Club space",
}
CHAT_TAG = {
    COMMUNITY: "Campus",
    "match": "Friend",
    "pair": "Game",
    "club": "Club",
}

def activity_catalogue(in_person_only=False):
    """Every activity with its locations, ready for a form or for validation."""
    query = db.select(Activity).order_by(Activity.sort_order)
    if in_person_only:
        query = query.where(Activity.in_person.is_(True))
    return [
        {
            "name": a.name,
            "online": a.online,
            "in_person": a.in_person,
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

    if kind == "club":
        if db.session.get(ClubMember, (current_user.id, target_id)) is None:
            return None, None
        club = db.session.get(Club, target_id)
        if club is None:
            return None, None
        return club, club.name

    return None, None


def attach_parent(message, kind, parent):
    """Point a message at exactly one parent, as the check constraint demands."""
    if kind == COMMUNITY:
        message.room = COMMUNITY
    elif kind == "pair":
        message.pairing_id = parent.id
    else:
        setattr(message, f"{kind}_id", parent.id)


def message_filter(kind, parent):
    if kind == COMMUNITY:
        return Message.room == COMMUNITY
    if kind == "pair":
        return Message.pairing_id == parent.id
    column = Message.match_id if kind == "match" else Message.club_id
    return column == parent.id


def chat_rows(kind, parent, after=0):
    rows = db.session.execute(
        db.select(Message, User.name)
        .join(User, User.id == Message.user_id)
        .where(message_filter(kind, parent), Message.id > after)
        .order_by(Message.id)
    ).all()

    posts = {}
    post_ids = [m.post_id for m, _ in rows if m.post_id]
    if post_ids:
        posts = {p.id: p for p in db.session.scalars(
            db.select(GamePost).where(GamePost.id.in_(post_ids)))}

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
            entry["post"] = post_payload(post)
        out.append(entry)
    return out


def post_payload(post):
    going = db.session.scalars(
        db.select(User.name)
        .join(GamePostRsvp, GamePostRsvp.user_id == User.id)
        .where(GamePostRsvp.post_id == post.id)
        .order_by(User.name)
    ).all()
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
        "im_going": db.session.get(GamePostRsvp, (post.id, current_user.id))
        is not None,
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
        entries.append(chat_entry(
            "pair", pairing.id,
            f"{pairing.game} with {other.name if other else 'Partner'}",
        ))

    for club in joined_clubs(current_user.id):
        entries.append(chat_entry("club", club.id, club.name))

    last = {}
    newest = db.session.execute(
        db.select(Message.body, Message.created_at)
        .where(Message.room == COMMUNITY)
        .order_by(Message.id.desc()).limit(1)
    ).first()
    if newest:
        last[(COMMUNITY, 0)] = (newest[0], newest[1])

    for kind, column in (("match", Message.match_id), ("club", Message.club_id),
                         ("pair", Message.pairing_id)):
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
            db.session.commit()

        return redirect(url_for("chat", kind=kind, target_id=target_id))

    return render_template(
        "chat.html",
        kind=kind,
        target_id=target_id,
        title=title,
        subtitle=CHAT_SUBTITLE[kind],
        messages=chat_rows(kind, parent),
        game_kinds=[(k, rules.LABELS[k]) for k in rules.PLAYABLE],
        post_modes=POST_MODES,
        catalogue=activity_catalogue(),
    )


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
        rows.append({
            "game": game,
            "label": rules.LABELS.get(game.kind, game.kind),
            "opponent": other.name if other else "Opponent",
            "waiting_on_you": (
                (game.status == "invited" and game.player_b == current_user.id)
                or (game.status == "active"
                    and rules.can_move(game.kind, game.state, seat))
            ),
        })

    return render_template(
        "games.html",
        rows=rows,
        opponents=matching.active_matches(current_user.id),
        kinds=[(k, rules.LABELS[k]) for k in rules.PLAYABLE],
    )


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
    db.session.commit()

    flash(f"Invite sent for {rules.LABELS[kind]}.")
    return redirect(url_for("game_view", game_id=game.id))


@app.route("/games/<int:game_id>")
@login_required
def game_view(game_id):
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    other_id = game.user_in_seat(1 - seat)
    parent, _ = chat_target("match", other_id)

    return render_template(
        "game.html",
        game=game,
        seat=seat,
        payload=game_payload(game, seat),
        other_id=other_id,
        messages=chat_rows("match", parent) if parent else None,
    )


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
            if outcome is None:
                nxt = rules.turn_seat(game.kind, state)
                game.turn_id = None if nxt is None else game.user_in_seat(nxt)
            elif outcome[0] == "win":
                finish_game(game, "win", game.user_in_seat(outcome[1]))
            else:
                finish_game(game, "draw")

            db.session.commit()

    return redirect(url_for("game_view", game_id=game_id))


@app.route("/games/<int:game_id>/<any(accept, decline, resign):action>",
           methods=["POST"])
@login_required
def game_action(game_id, action):
    game, seat = game_for(game_id)

    if game is None:
        flash("That game is not available to you.")
        return redirect(url_for("game_lobby"))

    if action in ("accept", "decline"):
        if game.status != "invited":
            flash("That invite has already been answered.")
        elif game.player_b != current_user.id:
            flash("Only the person invited can answer.")
        elif action == "accept":
            game.status = "active"
            nxt = rules.turn_seat(game.kind, game.state)
            game.turn_id = None if nxt is None else game.user_in_seat(nxt)
            game.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Game on.")
        else:
            game.status = "declined"
            game.turn_id = None
            game.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Invite declined.")

    elif action == "resign":
        if game.status != "active":
            flash("That game isn't in play.")
        else:
            finish_game(game, "resigned", game.user_in_seat(1 - seat))
            game.updated_at = datetime.utcnow()
            db.session.commit()
            flash("You resigned.")

    return redirect(url_for("game_view", game_id=game_id))


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
               GamePairing.status == "open")
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
        games=[a["name"] for a in activity_catalogue(in_person_only=True)],
        catalogue=activity_catalogue(),
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

    activity = find_activity(game)
    allowed = [] if activity is None else [loc.name for loc in activity.locations]

    if not game:
        flash("Say which activity you're looking for.")
    elif activity is None:
        flash("Pick an activity from the list.")
    elif mode not in POST_MODES:
        flash("Pick online or in person.")
    elif mode == "online" and not activity.online:
        flash(f"{activity.name} can't be played online.")
    elif mode == "inperson" and not activity.in_person:
        flash(f"{activity.name} is online only.")
    elif mode == "inperson" and not location:
        flash("Pick where you'll be playing.")
    elif mode == "inperson" and location not in allowed:
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
    else:
        flash("That friend request is no longer waiting on you.")

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
                email=email,
                password_hash=generate_password_hash(password),
            )
            db.session.add(user)
            db.session.commit()
            issue_verification_code(user)
            session["pending_user_id"] = user.id
            return redirect(url_for("verify"))

    return render_template("register.html", genders=GENDERS)


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
            db.session.commit()
            return redirect(url_for("index"))

    return render_template(
        "onboarding.html",
        questions=questions,
        selected=selected,
        optional=OPTIONAL_QUESTIONS,
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
