"""Notices: one small, finished, already-worded fact addressed to one person.

Every notice in the app is written by one of the functions here, and all of
them go through notify(), which is the single place two rules live:

  * nobody is told about their own doing -- enforced here *and* by
    ck_notice_not_self, because this is the rule the feature would be most
    annoying to get wrong;
  * a notice is phrased once, at the moment it is written, and stored as a
    finished sentence. See the Notice docstring in models.py for why that is
    the opposite of how the chat's game events work.

app.py calls these from the routes where the event actually happens, so this
module stays the domain layer and app.py stays the pipeline -- the same split
matching.py already keeps.

Adding an event is a function here plus an entry in models.NOTICE_KINDS. The
two obvious ones still to come, recommended matches and game results, need
nothing else.
"""
from datetime import datetime

from models import Notice, User, db

# The sequence behind Notice.bump. Spelled through literal_column so it reaches
# Postgres as nextval('notices_bump_seq') and not as a bare identifier.
BUMP = db.func.nextval(db.literal_column("'notices_bump_seq'"))

# The newest N the bell draws. Everything older is still in the table and still
# counted; the panel just does not draw it.
PANEL_LIMIT = 30

# At most this many toasts from one reply, so a long absence does not bury the
# screen in them.
TOAST_LIMIT = 5


def chat_url(kind, target_id):
    """How a conversation is addressed.

    The chat route is /chats/<kind>/<target_id>, where a match's target is the
    *other student* and a group's is the chat itself. Built as a string rather
    than through url_for so it can be called with no request in hand.
    """
    return f"/chats/{kind}/{target_id}"


def notify(user_id, kind, body, url, actor_id=None, game_id=None, chat_id=None):
    """Write one notice, unless it would be about the recipient's own doing.

    Returns the Notice, or None if it was suppressed. Deliberately does not
    commit: it joins whatever transaction the route is already in, so a notice
    cannot outlive the thing it describes.
    """
    if actor_id is not None and actor_id == user_id:
        return None

    notice = Notice(
        user_id=user_id,
        actor_id=actor_id,
        kind=kind,
        body=body,
        url=url,
        game_id=game_id,
        chat_id=chat_id,
        bump=BUMP,
        created_at=datetime.utcnow(),
    )
    db.session.add(notice)
    return notice


# --------------------------------------------------------------- the writers

def friend_request(recipient_id, actor):
    return notify(
        recipient_id, "friend_request",
        f"{actor.name} wants to be friends.", "/matches",
        actor_id=actor.id,
    )


def friend_accepted(recipient_id, actor):
    return notify(
        recipient_id, "friend_accepted",
        f"{actor.name} accepted your friend request.",
        chat_url("match", actor.id),
        actor_id=actor.id,
    )


def campus(kind, body, url, exclude=()):
    """Tell every student the same finished sentence, in one statement.

    The only notice in the app addressed to everybody, and the only one that
    does not go through notify(). That is deliberate on both counts.

    Everywhere else the rule is the opposite -- chat_audience answers with
    nobody for the campus room, "because one post there would notify every
    student on the site at once" -- so this is the carve-out, and it is for
    announcements the university makes rather than anything a student does.

    One INSERT with many rows, not a loop. notify() writes a row at a time,
    and the only other fan-out here is `for admin_id in admin_ids()`, whose
    docstring says "usually one person". Several thousand students at one
    round trip each is a different thing entirely, and a loop would still
    look correct in every test that only counted the rows afterwards.

    `actor_id` stays null: the app is announcing, not a person. That also
    means whoever posted it is told too, rather than being quietly skipped by
    the no-self-notice rule -- which is right, because they are a student as
    well and the notice is not about them.

    `bump` is left out so notices_bump_seq fires per row as its server
    default; naming it here would give every row the same value and the live
    channel would carry one toast for all of them.

    Returns how many were told.
    """
    recipients = [
        user_id for (user_id,) in db.session.execute(
            db.select(User.id).where(User.email_verified.is_(True)))
        if user_id not in set(exclude)
    ]
    if not recipients:
        return 0

    now = datetime.utcnow()
    db.session.execute(db.insert(Notice), [
        {"user_id": user_id, "actor_id": None, "kind": kind, "body": body,
         "url": url, "count": 1, "created_at": now}
        for user_id in recipients
    ])
    return len(recipients)


def fixture_posted(title, billing, when_text):
    """A campus-wide meetup, told to everybody. Returns how many were told.

    `billing` is the sport and opponent as one phrase and may be empty, so
    the sentence is joined rather than formatted -- otherwise a meetup with
    no game in it reads "Spring Carnival:  Sat 2 May".
    """
    return campus(
        "fixture",
        " ".join(p for p in (f"{title}:", billing, f"{when_text}.") if p),
        "/fixtures",
    )


def hub_requested(admin_id, name):
    """Somebody asked for a hub. Addressed to whoever reviews them.

    No actor on purpose. The app is telling an admin that a queue has
    something in it, not that a particular student did something -- and a
    null actor also means an admin who asks for a hub themselves still gets
    told, instead of being silently skipped by the no-self-notice rule.
    """
    return notify(admin_id, "hub_request",
                  f"{name} was requested. It is waiting for review.",
                  "/admin")


def hub_decided(requester_id, name, approved, note=""):
    """What became of the hub somebody asked for.

    The sentence is finished here, as every notice's is, so the decision
    reads the same later as it did on the day -- including the reason, which
    is the whole point of telling somebody no.
    """
    if approved:
        return notify(requester_id, "hub_decision",
                      f"{name} is open. You can join it now.", "/clubs")
    said = f"{name} was not added."
    return notify(requester_id, "hub_decision",
                  f"{said} {note}".strip() if note else said, "/clubs")


def report_filed(admin_id, what):
    """Something was reported. Addressed to whoever reviews them.

    No actor, for the reason hub_requested gives: the app is telling an
    admin that a queue has something in it, and a null actor also means an
    admin who reports something themselves still gets told rather than being
    skipped by the no-self-notice rule.
    """
    return notify(admin_id, "report_filed",
                  f"{what} was reported. It is waiting for review.",
                  "/admin")


def report_closed(filer_id, said):
    """The thing somebody reported has been looked at.

    Only called when the report still has a filer: one filed by somebody who
    has since deleted their account has nobody to tell, and the route checks
    that rather than relying on notify() to shrug.

    Points at the home page. There is no per-report page to send anybody to,
    and adding Notice.report_id would be a second migration on notices for a
    link to something that does not exist.
    """
    return notify(filer_id, "report_closed", said, "/")


def account_gone(friend_id, name):
    """Somebody you were friends with has deleted their account.

    This exists because of what deletion takes with it: the conversation,
    the games you played together and the result cards in them all go, and
    without this the other person just finds a hole where a friendship was.
    One sentence is cheaper than preserving any of it, and more honest than
    leaving them to guess.

    actor_id is null deliberately, the way campus() does it -- the actor is
    about to not exist, and Notice.actor_id is CASCADE, so a notice naming
    them as the actor would be destroyed by the very deletion it describes.
    """
    return notify(friend_id, "account_gone",
                  f"{name} has left Beacons Connect. Your conversation and "
                  "any games you played together have gone with their "
                  "account.",
                  "/matches")


def chat_invite(recipient_id, actor, chat):
    return notify(
        recipient_id, "chat_invite",
        f"{actor.name} invited you to {chat.name}.", "/chats",
        actor_id=actor.id, chat_id=chat.id,
    )


def game_invite(recipient_id, actor, game, label):
    return notify(
        recipient_id, "game_invite",
        f"{actor.name} challenged you to {label}.",
        chat_url("match", actor.id),
        actor_id=actor.id, game_id=game.id,
    )


def game_answer(recipient_id, actor, game, label, action):
    said = "accepted" if action == "accept" else "declined"
    return notify(
        recipient_id, "game_answer",
        f"{actor.name} {said} your game of {label}.",
        chat_url("match", actor.id),
        actor_id=actor.id, game_id=game.id,
    )


def paired(recipient_id, actor, url, label):
    """Both halves of a pairing get one of these.

    The point of this one is the person who was *already waiting*. find_queue
    pairs instantly and flashes whoever arrived second, so until now the one
    who had been sitting on /find was told nothing at all.

    `url` rather than a pairing id, because online matchmaking reuses this and
    its games do not always land in a pair chat: paired with somebody who is
    already a friend, the game opens in the conversation they already share.
    Callers build it with chat_url().
    """
    return notify(
        recipient_id, "paired",
        f"{actor.name} is up for {label} too. You have been paired.",
        url,
        actor_id=actor.id,
    )


def message(recipient_id, actor, kind, target_id, chat_id=None, where=None):
    """New messages in a conversation the recipient was not reading.

    One row per conversation, bumped rather than repeated: thirty messages
    while somebody is away is one line in the bell and not thirty. The key is
    the url, which is already exactly one per conversation.

    `where` names the conversation, for the kinds where naming it helps. A
    one-to-one chat is named by the person, who is already in the sentence; a
    group or a club is not, and "posted in this chat" tells somebody nothing
    they can act on when five notices are stacked up.

    The caller decides whether to call this at all. The campus-wide room never
    does, because one post there would notify everybody on campus.
    """
    if actor.id == recipient_id:
        return None

    url = chat_url(kind, target_id)
    open_one = db.session.scalar(
        db.select(Notice).where(
            Notice.user_id == recipient_id,
            Notice.kind == "message",
            Notice.url == url,
            Notice.read_at.is_(None),
        )
    )

    if open_one is not None:
        open_one.count += 1
        open_one.actor_id = actor.id
        open_one.body = (
            f"{open_one.count} new messages from {actor.name}"
            if kind == "match" else
            f"{open_one.count} new messages in {where}" if where else
            f"{open_one.count} new messages in this chat")
        open_one.created_at = datetime.utcnow()
        # The whole reason bump exists. Without this the second message in a
        # conversation changes a row that every tab has already seen, and so
        # arrives with no toast and no change to the badge -- no signal at all.
        open_one.bump = BUMP
        return open_one

    return notify(
        recipient_id, "message",
        f"{actor.name} sent you a message." if kind == "match" else
        f"{actor.name} posted in {where}." if where else
        f"{actor.name} posted in this chat.",
        url, actor_id=actor.id, chat_id=chat_id,
    )


# ------------------------------------------------------------------- reading

def _unread(user_id, skip_url=None):
    """The unread set, optionally minus one conversation.

    `skip_url` is how a chat on somebody's screen is kept out of their badge
    without marking anything read. Doing it by exclusion rather than by writing
    is what stops an abandoned channel reading a conversation on their behalf:
    a ghost request that answers into the void changes nothing.
    """
    where = [Notice.user_id == user_id, Notice.read_at.is_(None)]
    if skip_url:
        where.append(Notice.url != skip_url)
    return where


def state(user_id, skip_url=None):
    """One query giving both things anybody wants about the unread set.

    Returns (token, unread count). The token is built exactly like game_token
    in app.py, and for the same reason: the waiting loop needs one cheap thing
    to compare. A plain max(id) would not do, because coalescing bumps an
    existing row without creating one and marking read deletes nothing -- both
    have to move it.
    """
    newest, rows, items = db.session.execute(
        db.select(
            db.func.coalesce(db.func.max(Notice.id), 0),
            db.func.count(Notice.id),
            db.func.coalesce(db.func.sum(Notice.count), 0),
        ).where(*_unread(user_id, skip_url))
    ).one()
    return f"{newest}:{rows}:{items}", rows


def token(user_id):
    """What the probe compares, every tick. One indexed aggregate.

    Deliberately counts the whole unread set, including a conversation being
    excluded from the badge: the probe's job is to notice that something
    happened, and the client stores this same value back, so the two have to be
    computed the same way or every round would answer immediately.
    """
    return state(user_id)[0]


def unread_count(user_id, skip_url=None):
    return state(user_id, skip_url)[1]


def recent(user_id, limit=PANEL_LIMIT):
    """What the bell draws: unread first, then newest first.

    One query and no joins -- no join is even possible, because body and url
    are stored whole -- which is what keeps the panel's cost flat in the number
    of rows, the same property tests/test_dock.py pins on the dock.
    """
    return db.session.scalars(
        db.select(Notice)
        .where(Notice.user_id == user_id)
        .order_by(Notice.read_at.is_(None).desc(), Notice.bump.desc())
        .limit(limit)
    ).all()


def since(user_id, after, skip_url=None):
    """Unread notices that have changed since this tab was last shown them.

    These become toasts. Anything the tab has seen stays out, so arriving at a
    page does not re-toast a backlog -- the bell is for that.

    Changed, not created: a coalesced row keeps the id of the first message
    that made it, so comparing ids would say nothing had happened the second
    time somebody wrote. Notice.bump is what moves on every change.

    Zero is a real watermark, not a missing one. A tab whose bell was empty
    when the page rendered sends 0, and the first notice to arrive after that
    is genuinely new to it -- treating 0 as "do not know, say nothing" meant
    anybody starting from an empty bell got no toast at all until they
    reloaded. TOAST_LIMIT is what actually keeps a long absence from burying
    the screen.
    """
    return db.session.scalars(
        db.select(Notice)
        .where(*_unread(user_id, skip_url), Notice.bump > after)
        .order_by(Notice.bump)
        .limit(TOAST_LIMIT)
    ).all()


def watermark(user_id):
    """The high-water mark a fresh page hands the channel to start from.

    Over every notice, read or not: a read notice that was bumped must still
    count as seen, or marking something read would make it toast again.
    """
    return db.session.scalar(
        db.select(db.func.coalesce(db.func.max(Notice.bump), 0))
        .where(Notice.user_id == user_id)
    ) or 0


# ------------------------------------------------------------------- writing

def mark_read(user_id, notice_id):
    notice = db.session.get(Notice, notice_id)
    if notice is None or notice.user_id != user_id:
        return None
    if notice.read_at is None:
        notice.read_at = datetime.utcnow()
    return notice


def mark_all_read(user_id):
    return db.session.execute(
        db.update(Notice)
        .where(Notice.user_id == user_id, Notice.read_at.is_(None))
        .values(read_at=datetime.utcnow())
    ).rowcount


def clear_read(user_id):
    return db.session.execute(
        db.delete(Notice).where(Notice.user_id == user_id,
                                Notice.read_at.is_not(None))
    ).rowcount


def close(user_id, notice_id):
    """Mark a notice read because what it asked about has now been answered.

    Called by the accept/decline routes, which carry the notice id as a hidden
    field. Silently does nothing for a missing, malformed or borrowed id: it is
    a tidy-up, never the point of the request.
    """
    if not notice_id:
        return None
    try:
        notice_id = int(notice_id)
    except (TypeError, ValueError):
        return None
    return mark_read(user_id, notice_id)


def close_invite(user_id, game_id):
    """Retire a game invite notice because the invite itself is gone.

    By game rather than by notice id, which is what close() takes: the person
    withdrawing an invite is not the person holding the notice about it, so
    there is no hidden field to read it from. Marked read rather than deleted,
    the same as every other answered notice.
    """
    return db.session.execute(
        db.update(Notice)
        .where(Notice.user_id == user_id, Notice.game_id == game_id,
               Notice.kind == "game_invite", Notice.read_at.is_(None))
        .values(read_at=datetime.utcnow())
    ).rowcount


def clear_chat(user_id, kind, target_id):
    """Reading a conversation retires its message notice.

    This is the whole of "do not tell somebody about what they are already
    looking at". The sender cannot know who is reading, so the notice is
    written unconditionally and retired here -- by the chat page itself, and by
    the live channel on behalf of whichever chat the tab has open. No presence
    table, no heartbeat.
    """
    return db.session.execute(
        db.update(Notice)
        .where(Notice.user_id == user_id,
               Notice.kind == "message",
               Notice.url == chat_url(kind, target_id),
               Notice.read_at.is_(None))
        .values(read_at=datetime.utcnow())
    ).rowcount
