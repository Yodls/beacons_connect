"""Every database operation this project has, as functions rather than scripts.

`db/` grew to 36 separate scripts, each run by hand with its own flags and its
own idea of what was dangerous -- `--apply` to opt into danger in most of them,
`--dry-run` to opt into safety in one, and `--i-mean-live` in another. This
module holds the work those scripts did. `dbtool.py` is the command line over
it and the admin panel calls the same functions, so the two cannot drift.

Three rules hold everywhere:

  * Nothing writes unless `apply_` is true. A dry run executes every statement
    and then throws the transaction away, which proves the SQL runs rather
    than only that this module believes it would.
  * Nothing reaches live unless `live` is true, and the server is asked for
    `current_database()` rather than the URL being trusted -- a URL can name
    one database and resolve to another.
  * Nothing prints a credential. The only things said out loud about a
    connection are its host and its database name: never the URL, never the
    user, never the password. tests/test_dbtool.py searches every command's
    output for the password and the URL from the environment, because a tool
    that logs its own connection string into a browser page is the most
    plausible way this feature could leak one.

Deliberately different from the db/_target.py it replaces:

  * `finish()` returned by calling sys.exit(). That is fine for one script per
    process and fatal for a tool that runs several, so the work returns a
    Result and only dbtool.py turns one into an exit code.
  * A refusal raises Refuse instead of exiting, for the same reason. A
    refusal is not a crash: it is a migration's own domain assertion saying
    the database is not in the state it expected, and the transaction is
    rolled back before it is raised.
  * The connection header no longer prints `current_user`. It was harmless in
    a terminal and is a credential in a web page, and one format for both is
    worth more than the username was.
"""
import os
import sys
from urllib.parse import urlsplit

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql as _sql

_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_ROOT, ".env"))


class Refuse(Exception):
    """A safety check said no. The caller has already rolled back."""


class Say:
    """Collects output as well as printing it.

    The terminal wants it on stdout as it happens; the panel wants the whole
    thing afterwards to show in the output pane. Keeping both in one place is
    what lets the panel show exactly what the command line would have said.
    """

    def __init__(self, echo=True):
        self.lines = []
        self.echo = echo

    def __call__(self, line=""):
        self.lines.append(line)
        if self.echo:
            print(line)
            sys.stdout.flush()

    @property
    def text(self):
        return "\n".join(self.lines)


class Result:
    """What a command did. `ok` is false only for a refusal."""

    def __init__(self, ok, changed=0, kept=False, summary="", output=""):
        self.ok = ok
        self.changed = changed
        self.kept = kept          # True only if it was committed
        self.summary = summary
        self.output = output

    def __repr__(self):
        return (f"<Result ok={self.ok} changed={self.changed} "
                f"kept={self.kept} {self.summary!r}>")


# ---------------------------------------------------------------- connecting

def url_for(live):
    """The connection string for one side, straight from the environment.

    Never returned to a caller that prints it. `where()` is the safe thing to
    show; this exists so psycopg2 can be handed it and nothing else.
    """
    var = "LIVE_DATABASE_URL" if live else "DATABASE_URL"
    url = os.environ.get(var)
    if not url:
        raise Refuse(f"{var} is not set in .env.")
    return url


def where(live):
    """Host and database name from the URL, for showing. No user, no password.

    This reads the URL's own path rather than asking the server, so it works
    before a connection exists -- the panel shows it while deciding whether
    to offer a command at all. `connect` replaces it with what the server
    actually says, which is the one that gets trusted.
    """
    parts = urlsplit(url_for(live))
    return parts.path.lstrip("/") or "?", parts.hostname or "?"


def connect(live=False, apply_=False, say=None, readonly=False):
    """Open the chosen database, after proving it is the one asked for.

    Returns (conn, dbname). Not autocommit: the caller runs its whole
    operation in one transaction and decides at the end whether to keep it,
    which is what makes a dry run possible and a failure harmless.
    """
    url = url_for(live)
    parts = urlsplit(url)
    var = "LIVE_DATABASE_URL" if live else "DATABASE_URL"
    try:
        conn = psycopg2.connect(url, connect_timeout=8)
    except psycopg2.Error as exc:
        # The URL is deliberately not in this message.
        raise Refuse(f"cannot reach {parts.path.lstrip('/')} at "
                     f"{parts.hostname}: "
                     f"{str(exc).strip().splitlines()[0]}")

    # Before the first statement: psycopg2 refuses set_session once a
    # transaction is open, and the probe below opens one.
    if readonly:
        conn.set_session(readonly=True)

    cur = conn.cursor()
    cur.execute("SELECT current_database()")
    name = cur.fetchone()[0]

    # The flag and the reality have to agree in both directions.
    if live and name.endswith("_dev"):
        conn.close()
        raise Refuse(f"--live was passed but LIVE_DATABASE_URL resolves to "
                     f"{name!r}, which is a dev database. "
                     "Fix LIVE_DATABASE_URL in .env.")
    if not live and not name.endswith("_dev"):
        conn.close()
        raise Refuse(f"DATABASE_URL resolves to {name!r}, which is not a dev "
                     "database. Pass --live if you meant live, or fix "
                     "DATABASE_URL in .env.")

    if say:
        say(f"  -> {name} @ {parts.hostname}  "
            f"({'LIVE' if live else 'dev'})")
        if not apply_ and not readonly:
            say("  dry run: the work below is rolled back. "
                "Pass --apply to keep it.")
        say()
    return conn, name


def finish(conn, apply_, changed, say):
    """Commit or roll back, and say which. Returns a Result."""
    say()
    if not changed:
        conn.rollback()
        conn.close()
        say("  nothing to do: already applied.")
        return Result(True, 0, False, "already applied", say.text)

    if apply_:
        conn.commit()
        say(f"  committed {changed} change(s).")
        out = Result(True, changed, True, f"committed {changed}", say.text)
    else:
        conn.rollback()
        say(f"  rolled back {changed} change(s) -- nothing was kept.")
        say("  re-run with --apply to keep them.")
        out = Result(True, changed, False, f"would change {changed}", say.text)
    conn.close()
    return out


# ------------------------------------------------------------ what is there

def column_exists(cur, table, column):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, column))
    return cur.fetchone() is not None


def table_exists(cur, table):
    cur.execute("""SELECT 1 FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    return cur.fetchone() is not None


def index_exists(cur, name):
    cur.execute("SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                "AND indexname = %s", (name,))
    return cur.fetchone() is not None


def sequence_exists(cur, name):
    cur.execute("SELECT 1 FROM information_schema.sequences "
                "WHERE sequence_schema = 'public' AND sequence_name = %s",
                (name,))
    return cur.fetchone() is not None


def constraint_def(cur, name):
    """The CHECK body as Postgres stores it, or None if there is no such one.

    Migrations compare against this rather than assuming: a constraint that
    exists with the wrong body is the case that silently breaks a later
    insert, and it reads identically to a missing one unless you look.
    """
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = %s", (name,))
    row = cur.fetchone()
    return row[0] if row else None


def constraint_exists(cur, name):
    return constraint_def(cur, name) is not None


def column_decl(cur, table, column):
    """(type, nullable, default) for one column, or None."""
    cur.execute("""SELECT data_type, is_nullable, column_default
                   FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, column))
    return cur.fetchone()


def count(cur, table, clause="", params=()):
    """A row count, for the bracket every operation puts around its work."""
    sql = f'SELECT count(*) FROM "{table}"'
    if clause:
        sql += " WHERE " + clause
    cur.execute(sql, params)
    return cur.fetchone()[0]


def quoted(values):
    """A SQL list of string literals, for building a CHECK body.

    The values are this module's own constants, never anything a student
    typed, so there is nothing here to inject.
    """
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


def rejects(cur, sql, params, what):
    """True if the database refuses a statement it is supposed to refuse.

    A CHECK that exists proves nothing about what it allows. This tries the
    thing that must fail, inside a savepoint so the failure does not take the
    surrounding transaction with it.
    """
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(sql, params)
    except psycopg2.Error:
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        return True
    cur.execute("ROLLBACK TO SAVEPOINT probe")
    return False


# Note on CHECK constraints, because it caught me once while writing this:
# Postgres rewrites a CHECK body when it stores it, so the "category IN
# ('game', ...)" you send comes back as "(category)::text = ANY
# ((ARRAY['game'::character varying, ...])::text[])". Nothing here may decide
# whether a constraint is current by comparing bodies -- it never matches,
# and the constraint gets dropped and re-added on every run. `widen()` below
# compares the one value a widening introduces instead, which is what the
# original scripts did and why.


# ------------------------------------------------------------- migrations
#
# Each one is a pair of functions and a registry entry:
#
#   work(cur, dbname, say) -> number of changes made
#   applied(cur) -> True when every piece it adds is already present
#
# `work` keeps the original script's idempotence probes, its row-count
# bracket and its one domain assertion. Those assertions are the whole value
# of these scripts -- "refusing to continue: 3 existing account(s) came out
# unlisted" is the reason the migration is trustworthy -- so they are ported
# verbatim rather than summarised.
#
# `applied` is the one piece that is new. The old scripts never had to answer
# "is this pending?", because you ran one and read what it said. `migrate
# --list` has to answer it without touching anything, so each migration
# states it -- and it is derived from the same probes `work` uses, because a
# --list that disagrees with what a run would do is worse than no --list.
#
# A refusal raises Refuse. The runner rolls the transaction back, so no
# migration does its own rollback.


def _work_discoverable(cur, dbname, say):
    before = count(cur, "user")
    say(f"  {dbname}: {before} account(s)")

    changed = 0
    if column_exists(cur, "user", "discoverable"):
        say('  = "user".discoverable already exists')
    else:
        cur.execute('ALTER TABLE "user" ADD COLUMN discoverable BOOLEAN '
                    "NOT NULL DEFAULT true")
        say('  + added "user".discoverable')
        changed += 1

    # The assertion that matters. An account coming out of this unlisted is a
    # student who has silently vanished from every suggestion and search on
    # the site, and nobody would be told.
    hidden = count(cur, "user", "NOT discoverable")
    if hidden:
        raise Refuse(f"{hidden} existing account(s) came out unlisted.")
    say(f"  . all {before} existing account(s) stay listed")

    after = count(cur, "user")
    if after != before:
        raise Refuse(f'"user" changed {before} -> {after}')
    return changed


def _applied_discoverable(cur):
    return column_exists(cur, "user", "discoverable")


def _work_game_close(cur, dbname, say):
    before = count(cur, "games")
    say(f"  {dbname} has {before} game(s)")

    changed = 0
    for column in ("closed_a", "closed_b"):
        if column_exists(cur, "games", column):
            say(f"  = games.{column} already exists")
            continue
        cur.execute(f"ALTER TABLE games ADD COLUMN {column} "
                    f"BOOLEAN NOT NULL DEFAULT false")
        say(f"  + added games.{column}")
        changed += 1

    dismissed = count(cur, "games", "closed_a OR closed_b")
    after = count(cur, "games")
    if after != before:
        raise Refuse(f"game count changed {before} -> {after}")
    say(f"\n  {after} game(s), unchanged. "
        f"{dismissed} already dismissed by someone.")
    return changed


def _applied_game_close(cur):
    return all(column_exists(cur, "games", c)
               for c in ("closed_a", "closed_b"))


def _work_game_presence(cur, dbname, say):
    before = count(cur, "games")
    in_play = count(cur, "games", "status = 'active'")
    say(f"  {dbname}: {before} game(s), {in_play} of them in play")

    changed = 0
    for column in ("seen_a", "seen_b"):
        if column_exists(cur, "games", column):
            say(f"  = games.{column} already exists")
        else:
            cur.execute(f"ALTER TABLE games ADD COLUMN {column} "
                        "TIMESTAMP WITHOUT TIME ZONE")
            say(f"  + added games.{column}")
            changed += 1

    # Nullable on purpose, and this proves it: an existing game must not have
    # been given a presence it never had, or every paused game would look as
    # though somebody were sitting at it.
    stamped = count(cur, "games", "seen_a IS NOT NULL OR seen_b IS NOT NULL")
    if changed and stamped:
        raise Refuse(f"{stamped} existing game(s) came out with a presence "
                     "already set.")
    say(f"  . {before} existing game(s) read as nobody present, "
        "as they should")

    after = count(cur, "games")
    if after != before:
        raise Refuse(f"games changed {before} -> {after}")
    return changed


def _applied_game_presence(cur):
    return all(column_exists(cur, "games", c) for c in ("seen_a", "seen_b"))


def _work_game_here(cur, dbname, say):
    before = count(cur, "games")
    in_play = count(cur, "games", "status = 'active'")
    say(f"  {dbname}: {before} game(s), {in_play} of them in play")

    # The flag is only meaningful beside the stamp it qualifies.
    for column in ("seen_a", "seen_b"):
        if not column_exists(cur, "games", column):
            raise Refuse(f"games.{column} is missing. Run the "
                         "add_game_presence migration first.")

    changed = 0
    for column in ("here_a", "here_b"):
        if column_exists(cur, "games", column):
            say(f"  = games.{column} already exists")
        else:
            cur.execute(f"ALTER TABLE games ADD COLUMN {column} "
                        "BOOLEAN NOT NULL DEFAULT false")
            say(f"  + added games.{column}")
            changed += 1

    # And this is the assertion that matters: nobody may come out of this
    # already at a board. If an existing game did, every paused game would
    # look as though somebody were sitting at it -- which is the bug this is
    # fixing, reintroduced by the fix for it.
    #
    # Guarded on `changed`, the way the add_game_presence migration next
    # door already guards the same claim. Unguarded it refused every re-run
    # the moment anybody actually sat down at a board -- 103 games on dev --
    # which made a migration that is applied impossible to run again.
    seated = count(cur, "games", "here_a OR here_b")
    if changed and seated:
        raise Refuse(f"{seated} existing game(s) came out with somebody "
                     "already at the board.")
    if changed:
        say(f"  . {before} existing game(s) read as nobody at the board")
    else:
        say(f"  . {seated} game(s) have somebody at the board right now")

    after = count(cur, "games")
    if after != before:
        raise Refuse(f"games changed {before} -> {after}")
    return changed


def _applied_game_here(cur):
    return all(column_exists(cur, "games", c) for c in ("here_a", "here_b"))


def _work_post_starts_at(cur, dbname, say):
    if not table_exists(cur, "game_posts"):
        raise Refuse(f"{dbname} has no game_posts table.")

    before = count(cur, "game_posts")
    open_now = count(cur, "game_posts", "status = 'open'")
    say(f"  {dbname}: {before} meetup(s), {open_now} still open")

    changed = 0
    if column_exists(cur, "game_posts", "starts_at"):
        say("  = game_posts.starts_at already exists")
    else:
        cur.execute("ALTER TABLE game_posts ADD COLUMN starts_at TIMESTAMP")
        say("  + added game_posts.starts_at")
        changed += 1

    # The assertion that matters: this must not decide anything about a
    # meetup that already exists. Every one of them comes out with no stored
    # time, and the listing falls back to its date or its age.
    stamped = count(cur, "game_posts", "starts_at IS NOT NULL")
    if stamped:
        raise Refuse(f"{stamped} existing meetup(s) came out with a start "
                     "time nobody entered.")
    say(f"  . all {before} existing meetup(s) keep whatever date they had")

    after = count(cur, "game_posts")
    if after != before:
        raise Refuse(f"game_posts changed {before} -> {after}")
    return changed


def _applied_post_starts_at(cur):
    return column_exists(cur, "game_posts", "starts_at")


def create_tables(cur, names, say):
    """Create named tables from the models, if they are not there already.

    Three migrations used `db.create_all()` inside an app context for this.
    That worked but bound DATABASE_URL at import, which fixed the target for
    the whole process -- so those migrations could never be aimed at live at
    all. SQLAlchemy will compile the DDL for one table without a connection
    or an app, so the statement is built here and run on whichever database
    was actually chosen.

    models.py is importable on its own, deliberately -- it says so at the top
    -- so this does not drag app.py and its DATABASE_URL in.

    The shape created is the one models.py describes *now*, not the one it
    described when the migration was written. That is exactly what
    create_all did too, and it stays consistent: on a fresh database the
    table arrives complete and the later migrations that would have added
    its columns find them present and report "already applied".
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from models import db

    made = 0
    for name in names:
        if table_exists(cur, name):
            say(f"  = table {name} already exists")
            continue
        table = db.Model.metadata.tables[name]
        cur.execute(str(CreateTable(table).compile(
            dialect=postgresql.dialect())))
        say(f"  + created table {name}")
        made += 1
    return made


def _work_gender(cur, dbname, say):
    """Ported from a pre-_target script that committed after every step.

    It gains a dry run by being here, which it never had. The SQL and the
    backfill table are unchanged; what went is four separate commits, so a
    failure half way no longer leaves the column added and unpopulated.
    """
    before = count(cur, "user")
    say(f"  {dbname}: {before} account(s)")

    changed = 0
    decl = column_decl(cur, "user", "gender")
    if decl is None:
        cur.execute('ALTER TABLE "user" ADD COLUMN gender VARCHAR(12)')
        say("  + added column gender")
        changed += 1
    else:
        say("  = column gender already exists")

    cur.execute('SELECT id, name FROM "user" WHERE gender IS NULL')
    blank = cur.fetchall()
    for user_id, name in blank:
        cur.execute('UPDATE "user" SET gender = %s WHERE id = %s',
                    (_GENDER_FOR.get(name.split(" ")[0], "undisclosed"),
                     user_id))
    if blank:
        say(f"  + backfilled {len(blank)} row(s)")
        changed += 1
    else:
        say("  = nothing to backfill")

    if count(cur, "user", "gender IS NULL"):
        raise Refuse("some rows are still null")

    # Re-read: the column may have just been added above.
    decl = column_decl(cur, "user", "gender")
    if decl and decl[1] == "YES":
        cur.execute('ALTER TABLE "user" ALTER COLUMN gender SET NOT NULL')
        say("  + set NOT NULL")
        changed += 1
    else:
        say("  = already NOT NULL")

    if constraint_exists(cur, "ck_user_gender"):
        say("  = check constraint already present")
    else:
        cur.execute('ALTER TABLE "user" ADD CONSTRAINT ck_user_gender '
                    f"CHECK (gender IN ({quoted(_GENDERS)}))")
        say("  + added check constraint")
        changed += 1

    cur.execute('SELECT gender, count(*) FROM "user" '
                "GROUP BY gender ORDER BY 2 DESC")
    say("  distribution:")
    for gender, n in cur.fetchall():
        say(f"    {gender:12} {n:>3}")

    after = count(cur, "user")
    if after != before:
        raise Refuse(f'"user" changed {before} -> {after}')
    return changed


_GENDERS = ("man", "woman", "nonbinary", "undisclosed")

# The seeded demo names, so the backfill does not leave them all undisclosed.
# A real student's gender is never guessed from their name: anybody not in
# this table comes out "undisclosed".
_GENDER_FOR = {
    "Diego": "man", "Liam": "man", "Malik": "man", "Omar": "man",
    "Noah": "man", "Andre": "man", "Jonas": "man", "Kofi": "man",
    "Tomas": "man", "Ethan": "man", "Ravi": "man", "Marcus": "man",
    "Felix": "man", "Samir": "man", "Danny": "man",
    "Amara": "woman", "Priya": "woman", "Sofia": "woman", "Ava": "woman",
    "Fatima": "woman", "Leila": "woman", "Mei": "woman", "Rosa": "woman",
    "Hana": "woman", "Zara": "woman", "Nadia": "woman", "Elena": "woman",
    "Yara": "woman", "Aisha": "woman", "Ingrid": "woman", "Clara": "woman",
    # Commonly a surname and either gender as a given name - left undisclosed
    # rather than guessed.
    "Chen": "undisclosed",
}


def _applied_gender(cur):
    decl = column_decl(cur, "user", "gender")
    return (decl is not None and decl[1] == "NO"
            and constraint_exists(cur, "ck_user_gender"))


def _work_community(cur, dbname, say):
    """Ported from a pre-_target script. Also gains a dry run.

    The tables it used to make with create_all are made from the models
    metadata now, so this can be aimed at live like everything else.
    """
    new_check = "num_nonnulls(match_id, club_id, room) = 1"
    changed = create_tables(cur, ("game_posts", "game_post_rsvps"), say)

    if column_exists(cur, "messages", "room"):
        say("  = messages.room already exists")
    else:
        cur.execute("ALTER TABLE messages ADD COLUMN room VARCHAR(32)")
        say("  + added messages.room")
        changed += 1

    if column_exists(cur, "messages", "post_id"):
        say("  = messages.post_id already exists")
    else:
        cur.execute("ALTER TABLE messages ADD COLUMN post_id INTEGER "
                    "REFERENCES game_posts(id) ON DELETE CASCADE")
        say("  + added messages.post_id")
        changed += 1

    current = constraint_def(cur, "ck_message_one_parent")
    if current and "room" in current:
        say("  = one-parent check already covers room")
    else:
        # Every existing row must satisfy the new rule before it is enforced.
        bad = count(cur, "messages", f"NOT ({new_check})")
        if bad:
            raise Refuse(f"{bad} row(s) would violate the new check")
        if current:
            cur.execute("ALTER TABLE messages "
                        "DROP CONSTRAINT ck_message_one_parent")
        cur.execute("ALTER TABLE messages ADD CONSTRAINT "
                    f"ck_message_one_parent CHECK ({new_check})")
        say("  + one-parent check now allows a room")
        changed += 1

    if index_exists(cur, "ix_messages_room"):
        say("  = ix_messages_room already exists")
    else:
        cur.execute("CREATE INDEX ix_messages_room ON messages (room, id)")
        say("  + added ix_messages_room")
        changed += 1

    say(f"  check is now: {constraint_def(cur, 'ck_message_one_parent')}")
    say(f"  messages: {count(cur, 'messages')}")
    return changed


def _applied_community(cur):
    current = constraint_def(cur, "ck_message_one_parent") or ""
    return (table_exists(cur, "game_posts")
            and table_exists(cur, "game_post_rsvps")
            and column_exists(cur, "messages", "room")
            and column_exists(cur, "messages", "post_id")
            and "room" in current
            and index_exists(cur, "ix_messages_room"))


def _work_pairings(cur, dbname, say):
    """Ported from a pre-_target script. Also gains a dry run."""
    new_check = "num_nonnulls(match_id, club_id, room, pairing_id) = 1"
    changed = create_tables(cur, ("game_queue", "game_pairings"), say)

    if column_exists(cur, "messages", "pairing_id"):
        say("  = messages.pairing_id already exists")
    else:
        cur.execute("ALTER TABLE messages ADD COLUMN pairing_id INTEGER "
                    "REFERENCES game_pairings(id) ON DELETE CASCADE")
        say("  + added messages.pairing_id")
        changed += 1

    current = constraint_def(cur, "ck_message_one_parent")
    if current and "pairing_id" in current:
        say("  = one-parent check already covers pairings")
    else:
        bad = count(cur, "messages", f"NOT ({new_check})")
        if bad:
            raise Refuse(f"{bad} row(s) would violate the new check")
        if current:
            cur.execute("ALTER TABLE messages "
                        "DROP CONSTRAINT ck_message_one_parent")
        cur.execute("ALTER TABLE messages ADD CONSTRAINT "
                    f"ck_message_one_parent CHECK ({new_check})")
        say("  + one-parent check now allows a pairing")
        changed += 1

    if index_exists(cur, "ix_messages_pairing"):
        say("  = ix_messages_pairing already exists")
    else:
        cur.execute("CREATE INDEX ix_messages_pairing "
                    "ON messages (pairing_id, id)")
        say("  + added ix_messages_pairing")
        changed += 1

    say(f"  check is now: {constraint_def(cur, 'ck_message_one_parent')}")
    say(f"  messages: {count(cur, 'messages')}")
    return changed


def _applied_pairings(cur):
    current = constraint_def(cur, "ck_message_one_parent") or ""
    return (table_exists(cur, "game_queue")
            and table_exists(cur, "game_pairings")
            and column_exists(cur, "messages", "pairing_id")
            and "pairing_id" in current
            and index_exists(cur, "ix_messages_pairing"))


_MATCH_CHECKS = (
    # (constraint, body, the token that proves the body is the new one, label)
    ("ck_match_status",
     "status IN ('invited', 'active', 'ended', 'declined')",
     "invited", "status check"),
    ("ck_match_requester",
     "requested_by IS NULL OR requested_by IN (user_lo, user_hi)",
     "requested_by", "requester check"),
    ("ck_match_invite_has_asker",
     "status <> 'invited' OR requested_by IS NOT NULL",
     "requested_by", "asker check"),
)


def _work_match_requests(cur, dbname, say):
    """Ported from a pre-_target script. Also gains a dry run.

    `matches` already held rows, so this one never rode on create_all.

    It decided by looking for a token inside the existing constraint body,
    and keeps doing so. Comparing whole bodies would re-add all three
    constraints on a database that already has them, because Postgres
    rewrites a CHECK body when it stores it.
    """
    changed = 0
    if column_exists(cur, "matches", "requested_by"):
        say("  = matches.requested_by already exists")
    else:
        cur.execute('ALTER TABLE matches ADD COLUMN requested_by INTEGER '
                    'REFERENCES "user"(id) ON DELETE CASCADE')
        say("  + added matches.requested_by")
        changed += 1

    # Order matters: the status check has to allow 'invited' before the
    # asker check, which mentions it, can be evaluated.
    for name, body, token, label in _MATCH_CHECKS:
        current = constraint_def(cur, name)
        if current and token in current:
            say(f"  = {label}: already in place")
            continue
        bad = count(cur, "matches", f"NOT ({body})")
        if bad:
            raise Refuse(f"{bad} match row(s) would violate {name}")
        if current:
            cur.execute(f"ALTER TABLE matches DROP CONSTRAINT {name}")
        cur.execute(f"ALTER TABLE matches ADD CONSTRAINT {name} "
                    f"CHECK ({body})")
        say(f"  + {label}: {'replaced' if current else 'added'}")
        changed += 1

    say()
    for name, _, _, _ in _MATCH_CHECKS:
        say(f"  {name} = {constraint_def(cur, name)}")

    cur.execute("SELECT status, count(*) FROM matches "
                "GROUP BY status ORDER BY status")
    say()
    for status, n in cur.fetchall():
        say(f"  {status:9} {n}")
    return changed


def _applied_match_requests(cur):
    if not column_exists(cur, "matches", "requested_by"):
        return False
    for name, _, token, _ in _MATCH_CHECKS:
        current = constraint_def(cur, name)
        if not current or token not in current:
            return False
    return True


_PRONOUNS_VALID = ("she", "he", "they", "unspecified")
_PRONOUNS_DEFAULT = "unspecified"


def _work_pronouns(cur, dbname, say):
    """Same shape as the gender migration -- add, backfill, constrain.

    This one was written after _target, so it was already one transaction.
    """
    before = count(cur, "user")
    say(f"  {dbname} has {before} user row(s)")

    changed = 0
    state = column_decl(cur, "user", "pronouns")
    if state is None:
        cur.execute('ALTER TABLE "user" ADD COLUMN pronouns VARCHAR(16)')
        say("  + added column pronouns VARCHAR(16)")
        changed += 1
        state = column_decl(cur, "user", "pronouns")
    else:
        say("  = column pronouns already exists")

    # Backfill before NOT NULL, or the constraint cannot be satisfied.
    cur.execute('UPDATE "user" SET pronouns = %s WHERE pronouns IS NULL',
                (_PRONOUNS_DEFAULT,))
    if cur.rowcount:
        say(f"  + backfilled {cur.rowcount} row(s) "
            f"to {_PRONOUNS_DEFAULT!r}")
        changed += 1
    else:
        say("  = no rows needed backfilling")

    # models.py declares server_default="unspecified". Without this the live
    # schema would not match the model, and any insert that omits the column
    # -- which is most of them, including every test helper -- would fail.
    if state[2] is None:
        cur.execute('ALTER TABLE "user" ALTER COLUMN pronouns '
                    f"SET DEFAULT '{_PRONOUNS_DEFAULT}'")
        say(f"  + set default {_PRONOUNS_DEFAULT!r}")
        changed += 1
    else:
        say(f"  = default already set to {state[2]}")

    still_null = count(cur, "user", "pronouns IS NULL")
    if still_null:
        raise Refuse(f"{still_null} row(s) are still null")

    if state[1] == "YES":
        cur.execute('ALTER TABLE "user" ALTER COLUMN pronouns SET NOT NULL')
        say("  + set NOT NULL")
        changed += 1
    else:
        say("  = already NOT NULL")

    if constraint_exists(cur, "ck_user_pronouns"):
        say("  = check constraint ck_user_pronouns already present")
    else:
        cur.execute("ALTER TABLE \"user\" ADD CONSTRAINT ck_user_pronouns "
                    f"CHECK (pronouns IN ({quoted(_PRONOUNS_VALID)}))")
        say("  + added check constraint ck_user_pronouns")
        changed += 1

    after = count(cur, "user")
    if after != before:
        raise Refuse(f"row count changed {before} -> {after}")

    cur.execute('SELECT pronouns, count(*) FROM "user" '
                "GROUP BY pronouns ORDER BY 2 DESC")
    say(f"\n  {before} row(s), unchanged. distribution:")
    for value, n in cur.fetchall():
        say(f"    {value:12} {n:>4}")
    return changed


def _applied_pronouns(cur):
    state = column_decl(cur, "user", "pronouns")
    return (state is not None and state[1] == "NO" and state[2] is not None
            and constraint_exists(cur, "ck_user_pronouns"))


_CHAT_TABLES = {
    "chats": """
        CREATE TABLE chats (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            code       VARCHAR(16) NOT NULL UNIQUE,
            is_public  BOOLEAN NOT NULL DEFAULT false,
            owner_id   INTEGER NOT NULL
                       REFERENCES "user"(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT ck_chats_name CHECK (length(btrim(name)) > 0)
        )
    """,
    "chat_members": """
        CREATE TABLE chat_members (
            user_id   INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            chat_id   INTEGER NOT NULL REFERENCES chats(id)  ON DELETE CASCADE,
            joined_at TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, chat_id)
        )
    """,
    "chat_invites": """
        CREATE TABLE chat_invites (
            chat_id    INTEGER NOT NULL REFERENCES chats(id)  ON DELETE CASCADE,
            user_id    INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            invited_by INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            status     VARCHAR(12) NOT NULL DEFAULT 'invited',
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, user_id),
            CONSTRAINT ck_chat_invites_status
                CHECK (status IN ('invited', 'accepted', 'declined'))
        )
    """,
}

_CHAT_INDEXES = {
    "ix_chats_public": "CREATE INDEX ix_chats_public ON chats (is_public)",
    "ix_chat_invites_user":
        "CREATE INDEX ix_chat_invites_user ON chat_invites (user_id, status)",
    "ix_messages_chat":
        "CREATE INDEX ix_messages_chat ON messages (chat_id, id)",
}

_CHAT_PARENTS = "match_id, club_id, room, pairing_id, chat_id"


def _work_group_chats(cur, dbname, say):
    """The student-created chat tables, and teaching messages about them.

    Explicit DDL rather than the models metadata, as the original had it: the
    point was that the same script produces the same result on both
    databases regardless of what models.py says today.
    """
    changed = 0
    for name, ddl in _CHAT_TABLES.items():
        if table_exists(cur, name):
            say(f"  = table {name} already exists")
        else:
            cur.execute(ddl)
            say(f"  + created table {name}")
            changed += 1

    if column_exists(cur, "messages", "chat_id"):
        say("  = messages.chat_id already exists")
    else:
        cur.execute("ALTER TABLE messages ADD COLUMN chat_id INTEGER "
                    "REFERENCES chats(id) ON DELETE CASCADE")
        say("  + added messages.chat_id")
        changed += 1

    for name, ddl in _CHAT_INDEXES.items():
        if index_exists(cur, name):
            say(f"  = index {name} already exists")
        else:
            cur.execute(ddl)
            say(f"  + created index {name}")
            changed += 1

    # The one that bites: until chat_id is inside this constraint, a message
    # with only chat_id set has zero recognised parents and is rejected.
    current = constraint_def(cur, "ck_message_one_parent")
    wanted = f"CHECK (num_nonnulls({_CHAT_PARENTS}) = 1)"
    if current is None:
        cur.execute("ALTER TABLE messages ADD CONSTRAINT "
                    f"ck_message_one_parent {wanted}")
        say("  + added ck_message_one_parent")
        changed += 1
    elif "chat_id" in current:
        say("  = ck_message_one_parent already covers chat_id")
    else:
        cur.execute("ALTER TABLE messages "
                    "DROP CONSTRAINT ck_message_one_parent")
        cur.execute("ALTER TABLE messages ADD CONSTRAINT "
                    f"ck_message_one_parent {wanted}")
        say("  ~ replaced ck_message_one_parent to include chat_id")
        changed += 1

    # Existing rows all have exactly one of the original four parents, so the
    # widened constraint must validate without touching any of them.
    say(f"\n  {count(cur, 'messages')} message(s) present, all still valid")
    say("  ck_message_one_parent is now:")
    say(f"    {constraint_def(cur, 'ck_message_one_parent')}")
    return changed


def _applied_group_chats(cur):
    current = constraint_def(cur, "ck_message_one_parent") or ""
    return (all(table_exists(cur, t) for t in _CHAT_TABLES)
            and column_exists(cur, "messages", "chat_id")
            and all(index_exists(cur, i) for i in _CHAT_INDEXES)
            and "chat_id" in current)


# The kinds notices knew when the table was created. Two later migrations
# widen ck_notice_kind -- the hub ones and the fixture one -- so this list is
# deliberately not the current set, and _applied_notices does not look at it.
_NOTICE_KINDS = (
    "friend_request", "friend_accepted", "chat_invite",
    "game_invite", "game_answer", "paired", "message",
)

_NOTICE_INDEXES = (
    ("ix_notices_unread", "notices (user_id, read_at)"),
    ("ix_notices_recent", "notices (user_id, id)"),
    ("ix_notices_scope", "notices (user_id, kind, url)"),
)


def _work_notices(cur, dbname, say):
    # The tables notices points at have to be there first, or the FKs below
    # fail halfway through and say something much less helpful than this.
    for needed in ("user", "games", "chats"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")

    say(f"  {dbname}: {count(cur, 'user')} user(s)")

    changed = 0
    if table_exists(cur, "notices"):
        say("  = notices already exists")
    else:
        cur.execute(f"""
            CREATE TABLE notices (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL
                            REFERENCES "user"(id) ON DELETE CASCADE,
                actor_id    INTEGER
                            REFERENCES "user"(id) ON DELETE CASCADE,
                kind        VARCHAR(24) NOT NULL,
                body        TEXT NOT NULL,
                url         TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1,
                game_id     INTEGER REFERENCES games(id) ON DELETE CASCADE,
                chat_id     INTEGER REFERENCES chats(id) ON DELETE CASCADE,
                read_at     TIMESTAMP WITHOUT TIME ZONE,
                created_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                CONSTRAINT ck_notice_kind
                    CHECK (kind IN ({quoted(_NOTICE_KINDS)})),
                CONSTRAINT ck_notice_not_self
                    CHECK (actor_id IS NULL OR actor_id <> user_id),
                CONSTRAINT ck_notice_count
                    CHECK (count >= 1)
            )
        """)
        say("  + created notices")
        changed += 1

    for name, target in _NOTICE_INDEXES:
        if index_exists(cur, name):
            say(f"  = index {name} already exists")
        else:
            cur.execute(f"CREATE INDEX {name} ON {target}")
            say(f"  + created index {name}")
            changed += 1

    # Prove the constraints are real rather than assuming the DDL meant what
    # it said. Both of these run inside this operation's own transaction and
    # are undone immediately, so they leave nothing behind even with --apply.
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row and changed:
        uid = row[0]
        for clause, sql in (
            ("ck_notice_not_self",
             "INSERT INTO notices (user_id, actor_id, kind, body, url, "
             "created_at) VALUES (%s, %s, 'message', 'x', '/', now())"),
            ("ck_notice_kind",
             "INSERT INTO notices (user_id, kind, body, url, created_at) "
             "VALUES (%s, 'nonsense', 'x', '/', now())"),
        ):
            params = (uid, uid) if "actor_id" in sql else (uid,)
            if rejects(cur, sql, params, clause):
                say(f"  . {clause} rejects a bad row, as it should")
            else:
                raise Refuse(f"{clause} accepted a bad row.")

    say(f"\n  notices holds {count(cur, 'notices')} row(s).")
    return changed


def _applied_notices(cur):
    return (table_exists(cur, "notices")
            and all(index_exists(cur, n) for n, _ in _NOTICE_INDEXES))


_BUMP_SEQUENCE = "notices_bump_seq"


def _work_notice_bump(cur, dbname, say):
    rows = count(cur, "notices")
    say(f"  {dbname}: {rows} notice(s)")

    changed = 0
    if sequence_exists(cur, _BUMP_SEQUENCE):
        say(f"  = sequence {_BUMP_SEQUENCE} already exists")
    else:
        cur.execute(f"CREATE SEQUENCE {_BUMP_SEQUENCE}")
        say(f"  + created sequence {_BUMP_SEQUENCE}")
        changed += 1

    if column_exists(cur, "notices", "bump"):
        say("  = notices.bump already exists")
    else:
        # The default fills every existing row as the column is added, so no
        # separate backfill and no window in which the column is null.
        cur.execute("ALTER TABLE notices ADD COLUMN bump BIGINT NOT NULL "
                    f"DEFAULT nextval('{_BUMP_SEQUENCE}')")
        say("  + added notices.bump")
        changed += 1

    if index_exists(cur, "ix_notices_bump"):
        say("  = index ix_notices_bump already exists")
    else:
        cur.execute("CREATE INDEX ix_notices_bump ON notices (user_id, bump)")
        say("  + created index ix_notices_bump")
        changed += 1

    # Every row has to have one, and no two may share one, or "newer than
    # what I have seen" stops meaning anything.
    cur.execute("SELECT count(*), count(DISTINCT bump), count(bump) "
                "FROM notices")
    total, distinct, filled = cur.fetchone()
    if total != filled or total != distinct:
        raise Refuse(f"{total} row(s), {filled} filled, {distinct} distinct "
                     "-- bump must be unique and complete.")
    say(f"  . all {total} row(s) carry a distinct bump")

    cur.execute(f"SELECT nextval('{_BUMP_SEQUENCE}') > %s", (total,))
    say(f"  . the sequence is ahead of the rows: {cur.fetchone()[0]}")
    return changed


def _applied_notice_bump(cur):
    return (sequence_exists(cur, _BUMP_SEQUENCE)
            and column_exists(cur, "notices", "bump")
            and index_exists(cur, "ix_notices_bump"))


_EVENTS = ("invited", "accepted", "declined", "ended", "abandoned")
_OUTCOMES = ("win", "draw", "resigned", "abandoned")


def _work_game_events(cur, dbname, say):
    msgs = count(cur, "messages")
    say(f"  {dbname}: {msgs} message(s), {count(cur, 'games')} game(s)")

    changed = 0
    if column_exists(cur, "messages", "game_id"):
        say("  = messages.game_id already exists")
    else:
        cur.execute("ALTER TABLE messages ADD COLUMN game_id INTEGER "
                    "REFERENCES games(id) ON DELETE CASCADE")
        say("  + added messages.game_id")
        changed += 1

    if column_exists(cur, "messages", "event"):
        say("  = messages.event already exists")
    else:
        cur.execute("ALTER TABLE messages ADD COLUMN event VARCHAR(12)")
        say("  + added messages.event")
        changed += 1

    for name, clause in (
        ("ck_message_event",
         f"event IS NULL OR event IN ({quoted(_EVENTS)})"),
        ("ck_message_event_has_game", "(event IS NULL) = (game_id IS NULL)"),
    ):
        if constraint_exists(cur, name):
            say(f"  = {name} already present")
        else:
            cur.execute(f"ALTER TABLE messages ADD CONSTRAINT {name} "
                        f"CHECK ({clause})")
            say(f"  + added {name}")
            changed += 1

    if not index_exists(cur, "ix_messages_game"):
        cur.execute("CREATE INDEX ix_messages_game ON messages (game_id)")
        say("  + created index ix_messages_game")
        changed += 1
    else:
        say("  = index ix_messages_game already exists")

    # The one that has to be replaced rather than added.
    outcome = ("outcome IS NULL OR outcome IN "
               f"({quoted(_OUTCOMES)})")
    current = constraint_def(cur, "ck_game_outcome")
    if current is None:
        cur.execute("ALTER TABLE games ADD CONSTRAINT ck_game_outcome "
                    f"CHECK ({outcome})")
        say("  + added ck_game_outcome")
        changed += 1
    elif "abandoned" in current:
        say("  = ck_game_outcome already allows 'abandoned'")
    else:
        cur.execute("ALTER TABLE games DROP CONSTRAINT ck_game_outcome")
        cur.execute("ALTER TABLE games ADD CONSTRAINT ck_game_outcome "
                    f"CHECK ({outcome})")
        say("  ~ replaced ck_game_outcome to allow 'abandoned'")
        changed += 1

    after = count(cur, "messages")
    if after != msgs:
        raise Refuse(f"messages changed {msgs} -> {after}")

    say(f"\n  {after} message(s), unchanged.")
    say("  ck_game_outcome is now:\n    "
        f"{constraint_def(cur, 'ck_game_outcome')}")
    return changed


def _applied_game_events(cur):
    current = constraint_def(cur, "ck_game_outcome") or ""
    return (column_exists(cur, "messages", "game_id")
            and column_exists(cur, "messages", "event")
            and constraint_exists(cur, "ck_message_event")
            and constraint_exists(cur, "ck_message_event_has_game")
            and index_exists(cur, "ix_messages_game")
            and "abandoned" in current)


def _work_game_ended_by(cur, dbname, say):
    before = count(cur, "games")
    declined = count(cur, "games", "status = 'declined'")
    say(f"  {dbname}: {before} game(s), {declined} of them declined")

    changed = 0
    added_col = False
    if column_exists(cur, "games", "ended_by"):
        say("  = games.ended_by already exists")
    else:
        added_col = True
        # SET NULL rather than CASCADE: a game is still a game once one of
        # the players has deleted their account, and losing the whole row
        # would take the other player's history with it. The seats
        # themselves cascade, which is what removes the game in that case.
        cur.execute("ALTER TABLE games ADD COLUMN ended_by INTEGER "
                    'REFERENCES "user"(id) ON DELETE SET NULL')
        say("  + added games.ended_by")
        changed += 1

    if constraint_exists(cur, "ck_game_ended_by"):
        say("  = ck_game_ended_by already exists")
    else:
        cur.execute("ALTER TABLE games ADD CONSTRAINT ck_game_ended_by "
                    "CHECK (ended_by IS NULL OR ended_by IN "
                    "(player_a, player_b))")
        say("  + added ck_game_ended_by")
        changed += 1

    # No backfill, and this is the assertion that says so on purpose.
    # Guessing an actor for an existing declined row would reproduce the bug
    # being fixed -- naming somebody who may not have done it -- so every old
    # row must come out null and read as "not recorded".
    #
    # Guarded on the column having just been added: once the app has been
    # recording who ended a game, 44 rows on dev legitimately name somebody,
    # and the unguarded form refused every re-run.
    named = count(cur, "games", "ended_by IS NOT NULL")
    if added_col and named:
        raise Refuse(f"{named} existing game(s) came out with somebody named "
                     "as having ended them.")
    if added_col:
        say(f"  . all {before} existing game(s) read as not recorded")
    else:
        say(f"  . {named} game(s) already record who ended them")

    after = count(cur, "games")
    if after != before:
        raise Refuse(f"games changed {before} -> {after}")
    return changed


def _applied_game_ended_by(cur):
    return (column_exists(cur, "games", "ended_by")
            and constraint_exists(cur, "ck_game_ended_by"))


# Courts, fields, indoor, lawn -- and Chess, which is the one activity
# playable both online and in person, so dropping it from the pickers would
# take the only both-ways case with it.
_PRESETS = (
    "Basketball", "Soccer", "Volleyball", "Tennis",
    "Table tennis", "Board games", "Chess", "Running",
)


def _work_meetup_presets(cur, dbname, say):
    total = count(cur, "activities")
    say(f"  {dbname}: {total} activities")

    changed = 0
    if column_exists(cur, "activities", "preset"):
        say("  = activities.preset already exists")
    else:
        cur.execute("ALTER TABLE activities ADD COLUMN preset BOOLEAN "
                    "NOT NULL DEFAULT false")
        say("  + added activities.preset")
        changed += 1

    # Named rather than picked by sort_order: the order is presentation and
    # could be reshuffled, but which activities are offered is a decision.
    cur.execute("SELECT name FROM activities WHERE name = ANY(%s)",
                (list(_PRESETS),))
    found = {row[0] for row in cur.fetchall()}
    missing = [name for name in _PRESETS if name not in found]
    if missing:
        raise Refuse(f"{dbname} has no activity named "
                     + ", ".join(repr(m) for m in missing)
                     + ". Run the activities seeder first.")

    cur.execute("UPDATE activities SET preset = (name = ANY(%s))",
                (list(_PRESETS),))
    if cur.rowcount:
        say(f"  ~ set the flag across {cur.rowcount} row(s)")
        changed += 1

    # Exactly the eight, and nothing else. A typo in the list above would
    # otherwise leave a picker quietly short of an option.
    cur.execute("SELECT name FROM activities WHERE preset ORDER BY sort_order")
    marked = [row[0] for row in cur.fetchall()]
    if sorted(marked) != sorted(_PRESETS):
        raise Refuse(f"{len(marked)} row(s) carry the flag -- {marked}; "
                     f"expected the {len(_PRESETS)} presets.")

    say(f"\n  {len(marked)} activities are offered: " + ", ".join(marked))
    say(f"  {count(cur, 'activities', 'NOT preset')} stay in the catalogue, "
        "reachable as a write-in.")

    after = count(cur, "activities")
    if after != total:
        raise Refuse(f"activities changed {total} -> {after}")
    return changed


def _applied_meetup_presets(cur):
    if not column_exists(cur, "activities", "preset"):
        return False
    cur.execute("SELECT name FROM activities WHERE preset")
    return sorted(r[0] for r in cur.fetchall()) == sorted(_PRESETS)


def _work_post_audience(cur, dbname, say):
    before = count(cur, "game_posts")
    open_now = count(cur, "game_posts", "status = 'open'")
    say(f"  {dbname}: {before} meetup(s), {open_now} still open")

    changed = 0
    added_col = False
    if column_exists(cur, "game_posts", "audience"):
        say("  = game_posts.audience already exists")
    else:
        cur.execute("ALTER TABLE game_posts ADD COLUMN audience TEXT "
                    "NOT NULL DEFAULT 'campus'")
        say("  + added game_posts.audience")
        added_col = True
        changed += 1

    if constraint_exists(cur, "ck_post_audience"):
        say("  = ck_post_audience already exists")
    else:
        cur.execute("ALTER TABLE game_posts ADD CONSTRAINT ck_post_audience "
                    "CHECK (audience IN ('campus', 'chat'))")
        say("  + added ck_post_audience")
        changed += 1

    # The assertion that matters, and it is about privacy rather than about
    # the column. Anything that came out as 'chat' would be a meetup that has
    # just disappeared from /find without anybody asking it to.
    #
    # Guarded on the column having just been added: chat-only meetups are a
    # feature now, so 5 of them on dev are correct and the unguarded form
    # read them as privacy damage and refused.
    hidden = count(cur, "game_posts", "audience <> 'campus'")
    if added_col and hidden:
        raise Refuse(f"{hidden} existing meetup(s) came out as something "
                     "other than campus-wide.")
    if added_col:
        say(f"  . all {before} existing meetup(s) stay campus-wide")
    else:
        say(f"  . {hidden} meetup(s) are deliberately chat-only")

    after = count(cur, "game_posts")
    if after != before:
        raise Refuse(f"game_posts changed {before} -> {after}")
    return changed


def _applied_post_audience(cur):
    return (column_exists(cur, "game_posts", "audience")
            and constraint_exists(cur, "ck_post_audience"))


_MEETUP_CHAT_COLUMNS = (
    ("chats", "post_id",
     "INTEGER REFERENCES game_posts(id) ON DELETE CASCADE"),
    ("chats", "expires_at", "TIMESTAMP WITHOUT TIME ZONE"),
    ("game_posts", "when_on", "DATE"),
)


def _work_meetup_chats(cur, dbname, say):
    chats_before = count(cur, "chats")
    posts_before = count(cur, "game_posts")
    say(f"  {dbname}: {chats_before} chat(s), {posts_before} meetup(s)")

    changed = 0
    added_cols = set()
    for table, column, decl in _MEETUP_CHAT_COLUMNS:
        if column_exists(cur, table, column):
            say(f"  = {table}.{column} already exists")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            say(f"  + added {table}.{column}")
            added_cols.add(f"{table}.{column}")
            changed += 1

    # One chat per meetup. Added separately so a re-run over half-applied
    # columns still arrives at the same place.
    if constraint_exists(cur, "uq_chats_post"):
        say("  = uq_chats_post already exists")
    else:
        cur.execute("ALTER TABLE chats ADD CONSTRAINT uq_chats_post "
                    "UNIQUE (post_id)")
        say("  + added uq_chats_post")
        changed += 1

    # The assertion that matters: every chat that already existed has to come
    # out as an ordinary one. A chat that came out attached to a meetup would
    # start refusing its own join code and would close on somebody's date.
    #
    # Each half is guarded on its own columns having just been added. Meetup
    # chats are the feature this built, so 185 of them on dev are the thing
    # working -- the unguarded form read them as damage and refused.
    added_chat = {"chats.post_id", "chats.expires_at"} & added_cols
    touched = count(cur, "chats",
                    "post_id IS NOT NULL OR expires_at IS NOT NULL")
    if added_chat and touched:
        raise Refuse(f"{touched} existing chat(s) came out attached to a "
                     "meetup or carrying a deadline.")
    if added_chat:
        say(f"  . {chats_before} existing chat(s) read as ordinary, "
            "as they should")
    else:
        say(f"  . {touched} chat(s) belong to a meetup or carry a deadline")

    dated = count(cur, "game_posts", "when_on IS NOT NULL")
    if "game_posts.when_on" in added_cols and dated:
        raise Refuse(f"{dated} existing meetup(s) came out with a date "
                     "nobody gave them.")

    # The original ran a count over chats here and then threw the result
    # away, so only the meetup count was ever bracketed. Both are checked
    # now; neither may move, and nothing above touches a row.
    if count(cur, "chats") != chats_before:
        raise Refuse("the chat count moved")
    if count(cur, "game_posts") != posts_before:
        raise Refuse("the meetup count moved")
    return changed


def _applied_meetup_chats(cur):
    return (all(column_exists(cur, t, c)
                for t, c, _ in _MEETUP_CHAT_COLUMNS)
            and constraint_exists(cur, "uq_chats_post"))


_SEEK_KINDS = ("tictactoe", "connect4", "battleship", "chess")


def _work_online_matchmaking(cur, dbname, say):
    for needed in ("user", "game_pairings"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")

    pairings = count(cur, "game_pairings")
    say(f"  {dbname}: {pairings} existing pairing(s)")

    changed = 0
    if table_exists(cur, "game_seek"):
        say("  = game_seek already exists")
    else:
        cur.execute(f"""
            CREATE TABLE game_seek (
                user_id     INTEGER PRIMARY KEY
                            REFERENCES "user"(id) ON DELETE CASCADE,
                kind        TEXT NOT NULL,
                created_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                seen_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                CONSTRAINT ck_seek_kind
                    CHECK (kind IN ({quoted(_SEEK_KINDS)}))
            )
        """)
        say("  + created game_seek")
        changed += 1

    if index_exists(cur, "ix_seek_kind"):
        say("  = index ix_seek_kind already exists")
    else:
        cur.execute("CREATE INDEX ix_seek_kind "
                    "ON game_seek (kind, created_at)")
        say("  + created index ix_seek_kind")
        changed += 1

    if column_exists(cur, "game_pairings", "mode"):
        say("  = game_pairings.mode already exists")
    else:
        # The default fills every existing row as the column is added. Every
        # pairing made so far came from the in-person queue, so that is right.
        cur.execute("ALTER TABLE game_pairings ADD COLUMN mode TEXT NOT NULL "
                    "DEFAULT 'inperson'")
        say("  + added game_pairings.mode")
        changed += 1

    if constraint_exists(cur, "ck_pairing_mode"):
        say("  = ck_pairing_mode already present")
    else:
        cur.execute("ALTER TABLE game_pairings ADD CONSTRAINT "
                    "ck_pairing_mode CHECK (mode IN ('inperson', 'online'))")
        say("  + added ck_pairing_mode")
        changed += 1

    # Prove the constraints bite rather than assuming the DDL meant what it
    # said. Both run inside this operation's own transaction and are undone
    # at once, so they leave nothing behind even with --apply.
    #
    # Carried over as it was, including a soft spot: the ck_pairing_mode
    # probe is an UPDATE over every pairing, so on a database with no
    # pairings at all it changes nothing, raises nothing, and this would
    # refuse to continue saying the constraint accepted a bad row. dev has
    # four, live has rows too, so it has never fired -- but it is a probe
    # that reports on the table's emptiness rather than on the constraint.
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row and changed:
        uid = row[0]
        for label, sql, params in (
            ("ck_seek_kind",
             "INSERT INTO game_seek (user_id, kind, created_at, seen_at) "
             "VALUES (%s, 'hopscotch', now(), now())", (uid,)),
            ("ck_pairing_mode",
             "UPDATE game_pairings SET mode = 'sideways'", ()),
        ):
            if rejects(cur, sql, params, label):
                say(f"  . {label} rejects a bad row, as it should")
            else:
                raise Refuse(f"{label} accepted a bad row.")

    say(f"\n  game_seek holds {count(cur, 'game_seek')} row(s).")
    cur.execute("SELECT mode, count(*) FROM game_pairings GROUP BY mode")
    for mode, n in cur.fetchall():
        say(f"  {n} pairing(s) are {mode}")

    after = count(cur, "game_pairings")
    if after != pairings:
        raise Refuse(f"pairings changed {pairings} -> {after}")
    return changed


def _applied_online_matchmaking(cur):
    return (table_exists(cur, "game_seek")
            and index_exists(cur, "ix_seek_kind")
            and column_exists(cur, "game_pairings", "mode")
            and constraint_exists(cur, "ck_pairing_mode"))


_HUB_NOTICE_KINDS = (
    "friend_request", "friend_accepted", "chat_invite", "game_invite",
    "game_answer", "paired", "message", "hub_request", "hub_decision",
)
_HUB_STATUSES = ("pending", "approved", "declined")


def _work_admin_and_hubs(cur, dbname, say):
    # The tables this points at have to be there first, or the foreign keys
    # below fail halfway through and say something much less helpful.
    for needed in ("user", "clubs", "notices"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")

    before = count(cur, "user")
    notices_before = count(cur, "notices")
    say(f"  {dbname}: {before} account(s), {notices_before} notice(s)")

    changed = 0

    # ---- the flag ------------------------------------------------------
    added_flag = False
    if column_exists(cur, "user", "is_admin"):
        say('  = "user".is_admin already exists')
    else:
        cur.execute('ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN '
                    "NOT NULL DEFAULT false")
        say('  + added "user".is_admin')
        added_flag = True
        changed += 1

    # ---- the queue -----------------------------------------------------
    if table_exists(cur, "hub_requests"):
        say("  = hub_requests already exists")
    else:
        cur.execute(f"""
            CREATE TABLE hub_requests (
                id           SERIAL PRIMARY KEY,
                name         TEXT NOT NULL,
                pitch        TEXT NOT NULL,
                requested_by INTEGER NOT NULL
                             REFERENCES "user"(id) ON DELETE CASCADE,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TIMESTAMP NOT NULL
                             DEFAULT (now() AT TIME ZONE 'utc'),
                decided_by   INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                decided_at   TIMESTAMP,
                club_id      INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
                note         TEXT,
                CONSTRAINT ck_hub_request_status
                    CHECK (status IN ({quoted(_HUB_STATUSES)})),
                CONSTRAINT ck_hub_request_decided
                    CHECK ((status = 'pending')
                           = (decided_by IS NULL AND decided_at IS NULL)),
                CONSTRAINT ck_hub_request_name
                    CHECK (length(btrim(name)) > 0)
            )
        """)
        say("  + created hub_requests")
        changed += 1

    if index_exists(cur, "uq_hub_request_pending"):
        say("  = uq_hub_request_pending already exists")
    else:
        cur.execute("CREATE UNIQUE INDEX uq_hub_request_pending "
                    "ON hub_requests (lower(name)) WHERE status = 'pending'")
        say("  + added uq_hub_request_pending")
        changed += 1

    if index_exists(cur, "ix_hub_requests_status"):
        say("  = ix_hub_requests_status already exists")
    else:
        cur.execute("CREATE INDEX ix_hub_requests_status "
                    "ON hub_requests (status, id)")
        say("  + added ix_hub_requests_status")
        changed += 1

    # ---- the notice kinds ----------------------------------------------
    current = constraint_def(cur, "ck_notice_kind")
    wanted = f"CHECK (kind IN ({quoted(_HUB_NOTICE_KINDS)}))"
    if current is None:
        cur.execute(f"ALTER TABLE notices ADD CONSTRAINT ck_notice_kind "
                    f"{wanted}")
        say("  + added ck_notice_kind")
        changed += 1
    elif "hub_request" in current and "hub_decision" in current:
        say("  = ck_notice_kind already allows both hub kinds")
    else:
        cur.execute("ALTER TABLE notices DROP CONSTRAINT ck_notice_kind")
        cur.execute(f"ALTER TABLE notices ADD CONSTRAINT ck_notice_kind "
                    f"{wanted}")
        say("  ~ replaced ck_notice_kind to allow the two hub kinds")
        changed += 1

    # ---- prove the constraints are real --------------------------------
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row is None:
        say("  . no accounts, so the probes below are skipped")
    else:
        who = row[0]
        for sql, params, what, wrong in (
            ("INSERT INTO hub_requests (name, pitch, requested_by, status) "
             "VALUES ('Probe', 'Probe', %s, 'nonsense')", (who,),
             "ck_hub_request_status refuses an unknown status",
             "a bad status was accepted."),
            ("INSERT INTO hub_requests (name, pitch, requested_by, status) "
             "VALUES ('Probe', 'Probe', %s, 'approved')", (who,),
             "ck_hub_request_decided refuses a decision by nobody",
             "an undecided 'approved' row was accepted."),
            ("INSERT INTO notices (user_id, kind, body, url, created_at) "
             "VALUES (%s, 'not_a_kind', 'x', '/', now())", (who,),
             "ck_notice_kind still refuses an unknown kind",
             "the notice kinds are no longer constrained."),
        ):
            if rejects(cur, sql, params, what):
                say(f"  . {what}")
            else:
                raise Refuse(wrong)

    # ---- the assertion that matters ------------------------------------
    # Only meaningful when the column was just added. The original asserted
    # it unconditionally, which made the migration refuse to run a second
    # time the moment anybody was granted admin -- and somebody has to be,
    # or the queue it creates has no reviewer. Guarded the way every other
    # migration here guards a no-backfill claim, so the intent survives:
    # nobody is granted admin BY THIS, and a pre-existing admin is not this
    # migration's doing.
    admins = count(cur, "user", "is_admin")
    if added_flag and admins:
        raise Refuse(f"{admins} existing account(s) came out as admins. "
                     "Nobody was granted that here.")
    if added_flag:
        say(f"  . all {before} existing account(s) stay ordinary students")
    else:
        say(f"  . {admins} account(s) already hold admin, left as they are")

    after = count(cur, "user")
    notices_after = count(cur, "notices")
    if after != before or notices_after != notices_before:
        raise Refuse(f"rows moved (user {before} -> {after}, "
                     f"notices {notices_before} -> {notices_after})")
    return changed


def _applied_admin_and_hubs(cur):
    current = constraint_def(cur, "ck_notice_kind") or ""
    return (column_exists(cur, "user", "is_admin")
            and table_exists(cur, "hub_requests")
            and index_exists(cur, "uq_hub_request_pending")
            and index_exists(cur, "ix_hub_requests_status")
            and "hub_request" in current and "hub_decision" in current)


# The three CHECKs the map upgrade widens, each with the value that proves
# the widening has happened.
_MAP_WIDENED_CHECKS = {
    "ck_map_flag_category": "home",
    "ck_map_flag_visibility": "going",
    "ck_map_flag_audience": "going",
}


def _work_map_flags(cur, dbname, say):
    """The map schema that had no migration at all.

    app.py brings map_flags up to date from a before_request, which is fine
    for dev and useless for live: that upgrader only ever runs against the
    database the app is connected to. So live had no way to receive
    map_flags.post_id, map_flags.updated_at, the widened CHECKs,
    uq_map_flag_post or the map_flag_viewers table -- and add_fixtures
    refuses without post_id, which left live unable to catch up at all.

    Schema only, which is the part that was unreachable. The upgrader also
    drops an event flag for every open in-person meetup the first time
    post_id appears; that needs make_event_flag and the app, so it is not
    done here and the count is reported instead.

    The CHECK bodies come from models.py rather than being spelled out, so
    this lands the shape the application actually expects. That makes the
    category work in add_fixtures and drop_ufind no-ops afterwards, which is
    correct -- all three agree on where it ends up.
    """
    if not table_exists(cur, "map_flags"):
        say(f"  = {dbname} has no map_flags table; create_all builds it "
            "whole, so there is nothing to upgrade.")
        return 0

    from models import MapFlag

    before = count(cur, "map_flags")
    say(f"  {dbname}: {before} map flag(s)")

    changed = 0
    added_post_link = False
    if column_exists(cur, "map_flags", "post_id"):
        say("  = map_flags.post_id already exists")
    else:
        cur.execute("ALTER TABLE map_flags ADD COLUMN post_id INTEGER "
                    "REFERENCES game_posts (id) ON DELETE CASCADE")
        say("  + added map_flags.post_id")
        added_post_link = True
        changed += 1

    if column_exists(cur, "map_flags", "updated_at"):
        say("  = map_flags.updated_at already exists")
    else:
        cur.execute("ALTER TABLE map_flags ADD COLUMN updated_at "
                    "TIMESTAMP WITHOUT TIME ZONE")
        say("  + added map_flags.updated_at")
        changed += 1

    # Driven off the model's own constraints, the way the upgrader is.
    #
    # Compared by the one value each widening introduces, not by the whole
    # body: Postgres rewrites a CHECK when it stores it ("category IN (...)"
    # comes back as "(category)::text = ANY (ARRAY[...])"), so comparing
    # bodies would drop and re-add all three on every run.
    for constraint in MapFlag.__table__.constraints:
        name = getattr(constraint, "name", None)
        if name not in _MAP_WIDENED_CHECKS:
            continue
        changed += widen(cur, "map_flags", name, str(constraint.sqltext),
                         _MAP_WIDENED_CHECKS[name], say)

    if constraint_exists(cur, "uq_map_flag_post"):
        say("  = uq_map_flag_post already exists")
    else:
        cur.execute("ALTER TABLE map_flags ADD CONSTRAINT uq_map_flag_post "
                    "UNIQUE (post_id)")
        say("  + added uq_map_flag_post")
        changed += 1

    changed += create_tables(cur, ("map_flag_viewers",), say)

    # Prove the widened audience rule means something, since it is the one
    # with real logic in it rather than a list of allowed words.
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row and changed:
        what = "ck_map_flag_audience refuses a 'going' flag with no meetup"
        if rejects(
            cur,
            "INSERT INTO map_flags (public_id, owner_id, title, category, "
            "visibility, location_key, location_name, latitude, longitude, "
            "created_at, expires_at) VALUES ('probe-going', %s, 'x', "
            "'event', 'going', 'custom', 'x', 42.31, -71.03, now(), "
            "now() + interval '1 hour')", (row[0],), what,
        ):
            say(f"  . {what}")
        else:
            raise Refuse("a 'going' flag with no meetup was accepted.")

    # The data half this deliberately does not do.
    owed = count(cur, "game_posts",
                 "mode = 'inperson' AND status = 'open'")
    if added_post_link and owed:
        say(f"  ! {owed} open in-person meetup(s) have no flag yet. This "
            "does the schema only --")
        say("    run `flask --app app upgrade-map-flags` against this "
            "database to place them.")
    elif added_post_link:
        say("  . no open in-person meetups, so no event flags are owed")

    after = count(cur, "map_flags")
    if after != before:
        raise Refuse(f"map_flags changed {before} -> {after}")
    return changed


def _applied_map_flags(cur):
    if not table_exists(cur, "map_flags"):
        return True
    audience = constraint_def(cur, "ck_map_flag_audience") or ""
    return (column_exists(cur, "map_flags", "post_id")
            and column_exists(cur, "map_flags", "updated_at")
            and constraint_exists(cur, "uq_map_flag_post")
            and table_exists(cur, "map_flag_viewers")
            and "going" in audience)


_FIXTURE_NOTICE_KINDS = _HUB_NOTICE_KINDS + ("fixture",)
# "ufind" is here because it was a category when this ran. The drop_ufind
# migration below takes it out again; this list is history, not the truth.
_FIXTURE_CATEGORIES = ("game", "study", "social", "club", "event",
                       "ufind", "home")
_FIXTURE_STATUSES = ("scheduled", "cancelled")


def widen(cur, table, name, check, allows, say):
    """Drop and re-add a CHECK built from a tuple. Returns 1 if it changed.

    Decides by looking for one value inside the existing body, which is what
    these migrations need: Postgres rewrites a CHECK body when it stores it,
    so comparing whole bodies would re-add a constraint already correct.
    """
    current = constraint_def(cur, name)
    if current is None:
        cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                    f"CHECK ({check})")
        say(f"  + added {name}")
        return 1
    if allows in current:
        say(f"  = {name} already allows {allows!r}")
        return 0
    cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"CHECK ({check})")
    say(f"  ~ replaced {name} to allow {allows!r}")
    return 1


def _work_fixtures(cur, dbname, say):
    for needed in ("user", "map_flags", "notices"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")

    # The map's own columns arrive through the app, not through here. Adding
    # fixture_id to a half-built map_flags would leave a table that matches
    # neither models.py nor the upgrader.
    if not column_exists(cur, "map_flags", "post_id"):
        # The original said to serve a request with the app or run `flask
        # --app app upgrade-map-flags`. Both of those only ever reach
        # DATABASE_URL, so against live that advice could not work -- which
        # is why the add_map_flags migration exists. Point at it instead.
        raise Refuse("map_flags has no post_id, so this database has not had "
                     "the map schema upgrade yet. Run the add_map_flags "
                     "migration against this database first; `migrate --all` "
                     "does them in that order.")

    before = count(cur, "user")
    flags_before = count(cur, "map_flags")
    say(f"  {dbname}: {before} account(s), {flags_before} map flag(s)")

    changed = 0

    # ---- the privilege --------------------------------------------------
    added_flag = False
    if column_exists(cur, "user", "can_post_fixtures"):
        say('  = "user".can_post_fixtures already exists')
    else:
        cur.execute('ALTER TABLE "user" ADD COLUMN can_post_fixtures BOOLEAN '
                    "NOT NULL DEFAULT false")
        say('  + added "user".can_post_fixtures')
        added_flag = True
        changed += 1

    # ---- the table ------------------------------------------------------
    if table_exists(cur, "fixtures"):
        say("  = fixtures already exists")
    else:
        cur.execute(f"""
            CREATE TABLE fixtures (
                id         SERIAL PRIMARY KEY,
                sport      VARCHAR(60) NOT NULL,
                opponent   VARCHAR(80) NOT NULL,
                starts_at  TIMESTAMP NOT NULL,
                when_text  VARCHAR(60) NOT NULL,
                venue      VARCHAR(120) NOT NULL,
                latitude   NUMERIC(9, 6) NOT NULL,
                longitude  NUMERIC(9, 6) NOT NULL,
                note       VARCHAR(300),
                posted_by  INTEGER NOT NULL
                           REFERENCES "user"(id) ON DELETE CASCADE,
                status     VARCHAR(12) NOT NULL DEFAULT 'scheduled',
                created_at TIMESTAMP NOT NULL
                           DEFAULT (now() AT TIME ZONE 'utc'),
                CONSTRAINT ck_fixture_status
                    CHECK (status IN ({quoted(_FIXTURE_STATUSES)})),
                CONSTRAINT ck_fixture_sport
                    CHECK (length(btrim(sport)) > 0),
                CONSTRAINT ck_fixture_opponent
                    CHECK (length(btrim(opponent)) > 0)
            )
        """)
        say("  + created fixtures")
        changed += 1

    if index_exists(cur, "ix_fixtures_when"):
        say("  = ix_fixtures_when already exists")
    else:
        cur.execute("CREATE INDEX ix_fixtures_when "
                    "ON fixtures (status, starts_at)")
        say("  + added ix_fixtures_when")
        changed += 1

    # ---- the pin's link back --------------------------------------------
    if column_exists(cur, "map_flags", "fixture_id"):
        say("  = map_flags.fixture_id already exists")
    else:
        cur.execute("ALTER TABLE map_flags ADD COLUMN fixture_id INTEGER "
                    "REFERENCES fixtures(id) ON DELETE CASCADE")
        say("  + added map_flags.fixture_id")
        changed += 1

    if constraint_exists(cur, "uq_map_flag_fixture"):
        say("  = uq_map_flag_fixture already exists")
    else:
        cur.execute("ALTER TABLE map_flags ADD CONSTRAINT "
                    "uq_map_flag_fixture UNIQUE (fixture_id)")
        say("  + added uq_map_flag_fixture")
        changed += 1

    # ---- the two widened CHECKs -----------------------------------------
    changed += widen(cur, "map_flags", "ck_map_flag_category",
                     f"category IN ({quoted(_FIXTURE_CATEGORIES)})",
                     "home", say)
    changed += widen(cur, "notices", "ck_notice_kind",
                     f"kind IN ({quoted(_FIXTURE_NOTICE_KINDS)})",
                     "fixture", say)

    # ---- prove the constraints are real ---------------------------------
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row is None:
        say("  . no accounts, so the probes below are skipped")
    else:
        who = row[0]
        for sql, what, wrong in (
            ("INSERT INTO fixtures (sport, opponent, starts_at, when_text, "
             "venue, latitude, longitude, posted_by, status) VALUES "
             "('Probe', 'Probe', now(), 'x', 'x', 42.31, -71.03, %s, "
             "'nonsense')",
             "ck_fixture_status refuses an unknown status",
             "a bad fixture status was accepted."),
            ("INSERT INTO fixtures (sport, opponent, starts_at, when_text, "
             "venue, latitude, longitude, posted_by) VALUES "
             "('   ', 'Probe', now(), 'x', 'x', 42.31, -71.03, %s)",
             "ck_fixture_sport refuses a blank sport",
             "a blank sport was accepted."),
            ("INSERT INTO notices (user_id, kind, body, url, created_at) "
             "VALUES (%s, 'not_a_kind', 'x', '/', now())",
             "ck_notice_kind still refuses an unknown kind",
             "the notice kinds are no longer constrained."),
            ("INSERT INTO map_flags (public_id, owner_id, title, category, "
             "visibility, location_key, location_name, latitude, longitude, "
             "created_at, expires_at) VALUES ('probe-row', %s, 'x', "
             "'not_a_kind', 'public', 'custom', 'x', 42.31, -71.03, now(), "
             "now() + interval '1 hour')",
             "ck_map_flag_category still refuses an unknown category",
             "the flag categories are no longer constrained."),
        ):
            if rejects(cur, sql, (who,), what):
                say(f"  . {what}")
            else:
                raise Refuse(wrong)

    # ---- the assertion that matters -------------------------------------
    # Guarded the same way as the admin flag: unconditional, it would refuse
    # every re-run once the privilege had been handed to anybody, which is
    # the normal state of both databases.
    posters = count(cur, "user", "can_post_fixtures")
    if added_flag and posters:
        raise Refuse(f"{posters} existing account(s) came out able to post "
                     "fixtures. Nobody was granted that here.")
    if added_flag:
        say(f"  . all {before} existing account(s) stay without it")
    else:
        say(f"  . {posters} account(s) already post meetups, "
            "left as they are")

    after = count(cur, "user")
    flags_after = count(cur, "map_flags")
    if after != before or flags_after != flags_before:
        raise Refuse(f"rows moved (user {before} -> {after}, "
                     f"map_flags {flags_before} -> {flags_after})")
    return changed


def _applied_fixtures(cur):
    kinds = constraint_def(cur, "ck_notice_kind") or ""
    cats = constraint_def(cur, "ck_map_flag_category") or ""
    return (column_exists(cur, "user", "can_post_fixtures")
            and table_exists(cur, "fixtures")
            and index_exists(cur, "ix_fixtures_when")
            and column_exists(cur, "map_flags", "fixture_id")
            and constraint_exists(cur, "uq_map_flag_fixture")
            and "home" in cats and "fixture" in kinds)


def _soften(cur, name, col, say):
    """Let a column be absent, while still refusing a blank one."""
    wanted = f"{col} IS NULL OR length(btrim({col})) > 0"
    current = constraint_def(cur, name)
    if current is not None and "IS NULL" in current:
        say(f"  = {name} already allows an absent {col}")
        return 0
    if current is not None:
        cur.execute(f"ALTER TABLE fixtures DROP CONSTRAINT {name}")
    cur.execute(f"ALTER TABLE fixtures ADD CONSTRAINT {name} "
                f"CHECK ({wanted})")
    say(f"  ~ {name} now allows an absent {col}, still not a blank one")
    return 1


def _work_fixture_title(cur, dbname, say):
    if not table_exists(cur, "fixtures"):
        raise Refuse(f"{dbname} has no fixtures table. Run the fixtures "
                     "migration first.")

    before = count(cur, "fixtures")
    say(f"  {dbname}: {before} campus-wide meetup(s)")

    changed = 0

    # ---- the title ------------------------------------------------------
    if column_decl(cur, "fixtures", "title"):
        say("  = fixtures.title already exists")
    else:
        cur.execute("ALTER TABLE fixtures ADD COLUMN title VARCHAR(80)")
        say("  + added fixtures.title")
        changed += 1

    # Nothing already posted may lose its name.
    cur.execute("""
        UPDATE fixtures
           SET title = btrim(
                 coalesce(sport, '')
                 || CASE WHEN sport IS NOT NULL AND opponent IS NOT NULL
                         THEN ' vs ' ELSE '' END
                 || coalesce(opponent, ''))
         WHERE title IS NULL
    """)
    if cur.rowcount:
        say(f"  ~ named {cur.rowcount} existing meetup(s) from their details")
        changed += 1

    # Any row the backfill could not name would become NOT NULL-violating in
    # a moment, so it is caught here where the message can say why.
    nameless = count(cur, "fixtures", "coalesce(btrim(title), '') = ''")
    if nameless:
        raise Refuse(f"{nameless} meetup(s) have no sport or opponent to take "
                     "a name from. Give them titles by hand first.")

    if column_decl(cur, "fixtures", "title")[1] == "NO":
        say("  = fixtures.title is already required")
    else:
        cur.execute("ALTER TABLE fixtures ALTER COLUMN title SET NOT NULL")
        say("  + fixtures.title is now required")
        changed += 1

    if constraint_exists(cur, "ck_fixture_title"):
        say("  = ck_fixture_title already exists")
    else:
        cur.execute("ALTER TABLE fixtures ADD CONSTRAINT ck_fixture_title "
                    "CHECK (length(btrim(title)) > 0)")
        say("  + added ck_fixture_title")
        changed += 1

    # ---- the details become optional ------------------------------------
    for col in ("sport", "opponent"):
        if column_decl(cur, "fixtures", col)[1] == "YES":
            say(f"  = fixtures.{col} is already optional")
        else:
            cur.execute(f"ALTER TABLE fixtures ALTER COLUMN {col} "
                        "DROP NOT NULL")
            say(f"  + fixtures.{col} is now optional")
            changed += 1
        changed += _soften(cur, f"ck_fixture_{col}", col, say)

    # ---- the assertion that matters -------------------------------------
    after = count(cur, "fixtures")
    if after != before:
        raise Refuse(f"fixtures changed {before} -> {after}")
    if count(cur, "fixtures", "title IS NULL"):
        raise Refuse("something came out without a title.")
    say(f"  . all {before} meetup(s) still here, all of them named")
    return changed


def _applied_fixture_title(cur):
    title = column_decl(cur, "fixtures", "title")
    sport = column_decl(cur, "fixtures", "sport")
    if not title or not sport:
        return False
    return (title[1] == "NO" and sport[1] == "YES"
            and constraint_exists(cur, "ck_fixture_title")
            and "IS NULL" in (constraint_def(cur, "ck_fixture_sport") or ""))


_UFIND_CATEGORIES = ("game", "study", "social", "club", "event", "home")


def _work_drop_ufind(cur, dbname, say):
    if not table_exists(cur, "map_flags"):
        say(f"  = {dbname} has no map_flags table; nothing to do here.")
        return 0

    before = count(cur, "map_flags")
    say(f"  {dbname}: {before} map flag(s)")

    # The failure worth refusing. A listing somebody posted would be deleted
    # by the narrowing below, and nobody would be told.
    listings = count(cur, "map_flags", "category = 'ufind'")
    if listings:
        raise Refuse(f"{listings} U-Find listing(s) are on {dbname}. Decide "
                     "what happens to them first -- this will not delete "
                     "somebody's posting as a side effect.")
    say("  . no U-Find listings to lose")

    changed = 0

    # ---- the category ---------------------------------------------------
    current = constraint_def(cur, "ck_map_flag_category")
    if current is None:
        say("  = ck_map_flag_category is not there to narrow")
    elif "ufind" not in current:
        say("  = ck_map_flag_category already excludes 'ufind'")
    else:
        cur.execute("ALTER TABLE map_flags "
                    "DROP CONSTRAINT ck_map_flag_category")
        cur.execute("ALTER TABLE map_flags ADD CONSTRAINT "
                    "ck_map_flag_category CHECK (category IN "
                    f"({quoted(_UFIND_CATEGORIES)}))")
        say("  ~ ck_map_flag_category no longer allows 'ufind'")
        changed += 1

    # ---- the price ------------------------------------------------------
    if not constraint_exists(cur, "ck_map_flag_price"):
        say("  = ck_map_flag_price is already gone")
    else:
        cur.execute("ALTER TABLE map_flags DROP CONSTRAINT ck_map_flag_price")
        say("  - dropped ck_map_flag_price")
        changed += 1

    if not column_exists(cur, "map_flags", "price"):
        say("  = map_flags.price is already gone")
    else:
        priced = count(cur, "map_flags", "price IS NOT NULL")
        if priced:
            raise Refuse(f"{priced} flag(s) carry a price.")
        cur.execute("ALTER TABLE map_flags DROP COLUMN price")
        say("  - dropped map_flags.price")
        changed += 1

    # ---- prove the narrowing bites --------------------------------------
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row is None:
        say("  . no accounts, so the probe below is skipped")
    else:
        what = "a U-Find flag is now refused by the database"
        if rejects(
            cur,
            "INSERT INTO map_flags (public_id, owner_id, title, category, "
            "visibility, location_key, location_name, latitude, longitude, "
            "created_at, expires_at) VALUES ('probe-ufind', %s, 'x', "
            "'ufind', 'public', 'custom', 'x', 42.31, -71.03, now(), "
            "now() + interval '1 hour')", (row[0],), what,
        ):
            say(f"  . {what}")
        else:
            raise Refuse("a U-Find flag was still accepted.")

    after = count(cur, "map_flags")
    if after != before:
        raise Refuse(f"map_flags changed {before} -> {after}")
    say(f"  . all {before} flag(s) still here")
    return changed


def _applied_drop_ufind(cur):
    if not table_exists(cur, "map_flags"):
        return True
    return ("ufind" not in (constraint_def(cur, "ck_map_flag_category") or "")
            and not constraint_exists(cur, "ck_map_flag_price")
            and not column_exists(cur, "map_flags", "price"))


def _work_fix_hub_decided(cur, dbname, say):
    """Restate ck_hub_request_decided so a cascade cannot forbid a row.

    The constraint said:

        (status = 'pending') = (decided_by IS NULL AND decided_at IS NULL)

    and `decided_by` is ON DELETE SET NULL. Postgres does not re-check a
    CHECK when a cascade nulls one of its columns, so deleting the admin who
    approved a hub left that row as status='approved' with decided_by NULL --
    a state the constraint forbids. It read back fine and failed on the next
    UPDATE touching it, which is why nothing noticed.

    The fix is to say the same thing over `decided_at`, which is written in
    the same breath by hub_approve and hub_decline and which no cascade can
    reach. Every row the old rule forbade, the new one accepts, so there is
    no data to repair: an approved row with a timestamp and no decider is
    'not pending' = 'not null', which is true.

    ADD CONSTRAINT validates what is already there, so if that reasoning is
    wrong anywhere this refuses rather than passing quietly -- and the count
    below names the problem instead of leaving Postgres to quote it.
    """
    if not table_exists(cur, "hub_requests"):
        say(f"  = {dbname} has no hub_requests table; nothing to restate.")
        return 0

    rows = count(cur, "hub_requests")
    say(f"  {dbname}: {rows} hub request(s)")

    wanted = "(status = 'pending') = (decided_at IS NULL)"
    current = constraint_def(cur, "ck_hub_request_decided")

    # The one shape the new rule rejects and the old one allowed. Nothing
    # can produce it -- both routes write the timestamp -- but a refusal
    # that says so beats a constraint violation from the ALTER.
    stuck = count(cur, "hub_requests",
                  "status <> 'pending' AND decided_at IS NULL")
    if stuck:
        raise Refuse(f"{stuck} decided request(s) have no decided_at, which "
                     "the restated rule would reject. Give them a timestamp "
                     "or return them to pending first.")

    # How many rows the old rule was already forbidding, which is the thing
    # this migration exists to stop happening again.
    broken = count(cur, "hub_requests",
                   "status <> 'pending' AND decided_by IS NULL")
    if broken:
        say(f"  . {broken} row(s) are currently in a state the old rule "
            "forbids; the restated one accepts them")

    if current is not None and "decided_by" not in current:
        say("  = ck_hub_request_decided already leaves decided_by out")
        return 0

    changed = 0
    if current is not None:
        cur.execute("ALTER TABLE hub_requests "
                    "DROP CONSTRAINT ck_hub_request_decided")
        say("  - dropped the old ck_hub_request_decided")
        changed += 1
    cur.execute("ALTER TABLE hub_requests ADD CONSTRAINT "
                f"ck_hub_request_decided CHECK ({wanted})")
    say("  + added ck_hub_request_decided over decided_at")
    changed += 1

    # Prove the new rule bites both ways, and that it accepts the row the
    # old one forbade -- which is the whole behaviour change.
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row:
        who = row[0]
        probes = (
            ("INSERT INTO hub_requests (name, pitch, requested_by, status) "
             "VALUES ('Probe', 'Probe', %s, 'approved')", (who,), True,
             "a decided request with no timestamp is refused"),
            ("INSERT INTO hub_requests (name, pitch, requested_by, status, "
             "decided_at) VALUES ('Probe', 'Probe', %s, 'pending', now())",
             (who,), True,
             "a pending request with a timestamp is refused"),
            ("INSERT INTO hub_requests (name, pitch, requested_by, status, "
             "decided_at, decided_by) VALUES ('Probe', 'Probe', %s, "
             "'approved', now(), NULL)", (who,), False,
             "a decided request whose decider has gone is accepted"),
        )
        for sql, params, must_reject, what in probes:
            refused = rejects(cur, sql, params, what)
            if refused != must_reject:
                raise Refuse(f"{what}: it did not")
            say(f"  . {what}")

    after = count(cur, "hub_requests")
    if after != rows:
        raise Refuse(f"hub_requests changed {rows} -> {after}")
    return changed


def _applied_fix_hub_decided(cur):
    if not table_exists(cur, "hub_requests"):
        return True
    body = constraint_def(cur, "ck_hub_request_decided")
    # By what the body no longer names. Postgres rewrites a CHECK body, but
    # the column identifiers in it survive the rewrite. The `is not None`
    # half matters: "decided_by" not in "" is True, so a missing constraint
    # would otherwise report as applied.
    return body is not None and "decided_by" not in body


_REPORT_NOTICE_KINDS = _FIXTURE_NOTICE_KINDS + (
    "report_filed", "report_closed", "account_gone")

_REPORT_KINDS = ("abuse", "bug", "contact")
_REPORT_STATUSES = ("new", "actioned", "dismissed")
_REPORT_PIECES = "num_nonnulls(message_id, post_id, flag_id)"


def _work_reports(cur, dbname, say):
    """The reports queue, and the three notice kinds that go with it.

    Every link out of this table is ON DELETE SET NULL and no CHECK names a
    foreign key, which is the lesson of fix_hub_decided above applied to a
    new table rather than learned again later. models.py's Report docstring
    has the reasoning; this is the same shape in raw DDL.
    """
    for needed in ("user", "messages", "game_posts", "notices"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")
    if not table_exists(cur, "map_flags"):
        raise Refuse("map_flags is missing, so a report cannot point at a "
                     "map pin. Run the add_map_flags migration first; "
                     "`migrate --all` does them in that order.")

    before = count(cur, "user")
    say(f"  {dbname}: {before} account(s)")

    changed = 0
    if table_exists(cur, "reports"):
        say("  = reports already exists")
    else:
        cur.execute(f"""
            CREATE TABLE reports (
                id            SERIAL PRIMARY KEY,
                kind          VARCHAR(12) NOT NULL,
                filed_by      INTEGER
                              REFERENCES "user"(id) ON DELETE SET NULL,
                filed_by_name TEXT,
                about_user_id INTEGER
                              REFERENCES "user"(id) ON DELETE SET NULL,
                about_name    TEXT,
                message_id    INTEGER
                              REFERENCES messages(id) ON DELETE SET NULL,
                post_id       INTEGER
                              REFERENCES game_posts(id) ON DELETE SET NULL,
                flag_id       INTEGER
                              REFERENCES map_flags(id) ON DELETE SET NULL,
                quoted        TEXT,
                seen_in       TEXT,
                detail        TEXT NOT NULL,
                created_at    TIMESTAMP NOT NULL
                              DEFAULT (now() AT TIME ZONE 'utc'),
                status        VARCHAR(12) NOT NULL DEFAULT 'new',
                reviewed_by   INTEGER
                              REFERENCES "user"(id) ON DELETE SET NULL,
                reviewed_at   TIMESTAMP,
                note          TEXT,
                CONSTRAINT ck_report_kind
                    CHECK (kind IN ({quoted(_REPORT_KINDS)})),
                CONSTRAINT ck_report_status
                    CHECK (status IN ({quoted(_REPORT_STATUSES)})),
                CONSTRAINT ck_report_about
                    CHECK ((kind = 'abuse' AND about_name IS NOT NULL
                            AND {_REPORT_PIECES} <= 1)
                           OR (kind <> 'abuse' AND about_name IS NULL
                               AND {_REPORT_PIECES} = 0)),
                CONSTRAINT ck_report_reviewed
                    CHECK ((status = 'new') = (reviewed_at IS NULL)),
                CONSTRAINT ck_report_detail
                    CHECK (length(btrim(detail)) > 0)
            )
        """)
        say("  + created reports")
        changed += 1

    for name, target in (("ix_reports_queue", "reports (status, id)"),
                         ("ix_reports_about", "reports (about_user_id)")):
        if index_exists(cur, name):
            say(f"  = index {name} already exists")
        else:
            cur.execute(f"CREATE INDEX {name} ON {target}")
            say(f"  + created index {name}")
            changed += 1

    # Three calls with the same body and a different `allows` each time.
    # After the first succeeds the others find their value present and say
    # so; one call with one value would accept a half-widened body that
    # named only the first kind.
    for kind in ("report_filed", "report_closed", "account_gone"):
        changed += widen(cur, "notices", "ck_notice_kind",
                         f"kind IN ({quoted(_REPORT_NOTICE_KINDS)})",
                         kind, say)

    # Prove every rule means something, including the one positive probe
    # that is the reason the table is shaped this way.
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row is None:
        say("  . no accounts, so the probes below are skipped")
    else:
        who = row[0]
        probes = (
            ("INSERT INTO reports (kind, detail, about_name) "
             "VALUES ('bug', 'x', 'Somebody')", True,
             "ck_report_about refuses a bug report about a person"),
            ("INSERT INTO reports (kind, detail) VALUES ('abuse', 'x')", True,
             "ck_report_about refuses a complaint about nobody"),
            ("INSERT INTO reports (kind, detail, about_name, message_id, "
             "post_id) VALUES ('abuse', 'x', 'Somebody', 1, 1)", True,
             "ck_report_about refuses two pieces of content at once"),
            ("INSERT INTO reports (kind, detail, status) "
             "VALUES ('bug', 'x', 'dismissed')", True,
             "ck_report_reviewed refuses a decision at no time"),
            ("INSERT INTO reports (kind, detail, reviewed_at) "
             "VALUES ('bug', 'x', now())", True,
             "ck_report_reviewed refuses a new report with a timestamp"),
            ("INSERT INTO reports (kind, detail) VALUES ('bug', '   ')", True,
             "ck_report_detail refuses a blank report"),
            ("INSERT INTO reports (kind, detail) VALUES ('nonsense', 'x')",
             True, "ck_report_kind refuses an unknown kind"),
            ("INSERT INTO notices (user_id, kind, body, url, created_at) "
             f"VALUES ({who}, 'not_a_kind', 'x', '/', now())", True,
             "ck_notice_kind still refuses an unknown kind"),
            # The one that must be ACCEPTED: a complaint whose reporter,
            # subject and reviewer have all been deleted must still store.
            # If this is ever refused, the table has stopped being able to
            # outlive the people in it, which is its whole job.
            ("INSERT INTO reports (kind, detail, about_name, filed_by, "
             "about_user_id, status, reviewed_by, reviewed_at) "
             "VALUES ('abuse', 'x', 'Somebody', NULL, NULL, 'dismissed', "
             "NULL, now())", False,
             "a report outliving its reporter, subject and reviewer is kept"),
        )
        for sql, must_reject, what in probes:
            if rejects(cur, sql, (), what) != must_reject:
                raise Refuse(f"{what}: it did not")
            say(f"  . {what}")

    after = count(cur, "user")
    if after != before:
        raise Refuse(f'"user" changed {before} -> {after}')
    return changed


def _applied_reports(cur):
    kinds = constraint_def(cur, "ck_notice_kind") or ""
    return (table_exists(cur, "reports")
            and index_exists(cur, "ix_reports_queue")
            and index_exists(cur, "ix_reports_about")
            and "report_filed" in kinds and "report_closed" in kinds
            and "account_gone" in kinds)


_REACTION_NAMES = ("heart", "thumbsup", "thumbsdown", "haha", "exclaim",
                   "question")
_REPLY_COLUMNS = (
    # SET NULL: a reply must outlive the message it quotes, because deleting
    # an account deletes everything that person wrote. In no CHECK, which
    # test_schema_rules enforces for every SET NULL column.
    ("reply_to_id", "INTEGER REFERENCES messages(id) ON DELETE SET NULL"),
    ("reply_name", "TEXT"),
    ("reply_body", "TEXT"),
)


def _work_reactions(cur, dbname, say):
    """Replies and emoji reactions on chat messages.

    Three nullable columns on messages for the reply and its quoted copy,
    and a message_reactions table whose primary key is the one-per-person
    rule. Nothing existing is backfilled: every message comes out as a
    plain message with no reactions, which is what they are.
    """
    for needed in ("user", "messages"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")

    before = count(cur, "messages")
    say(f"  {dbname}: {before} message(s)")

    changed = 0
    for column, decl in _REPLY_COLUMNS:
        if column_exists(cur, "messages", column):
            say(f"  = messages.{column} already exists")
        else:
            cur.execute(f"ALTER TABLE messages ADD COLUMN {column} {decl}")
            say(f"  + added messages.{column}")
            changed += 1

    if table_exists(cur, "message_reactions"):
        say("  = message_reactions already exists")
    else:
        cur.execute(f"""
            CREATE TABLE message_reactions (
                message_id INTEGER NOT NULL
                           REFERENCES messages(id) ON DELETE CASCADE,
                user_id    INTEGER NOT NULL
                           REFERENCES "user"(id) ON DELETE CASCADE,
                emoji      VARCHAR(12) NOT NULL,
                created_at TIMESTAMP NOT NULL
                           DEFAULT (now() AT TIME ZONE 'utc'),
                PRIMARY KEY (message_id, user_id),
                CONSTRAINT ck_reaction_emoji
                    CHECK (emoji IN ({quoted(_REACTION_NAMES)}))
            )
        """)
        say("  + created message_reactions")
        changed += 1

    if index_exists(cur, "ix_reactions_message"):
        say("  = index ix_reactions_message already exists")
    else:
        cur.execute("CREATE INDEX ix_reactions_message "
                    "ON message_reactions (message_id)")
        say("  + created index ix_reactions_message")
        changed += 1

    # The rules mean something, proven by trying to break them.
    cur.execute("SELECT id, user_id FROM messages ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row is None:
        say("  . no messages, so the probes below are skipped")
    else:
        mid, uid = row
        if not rejects(cur,
                       "INSERT INTO message_reactions (message_id, user_id, "
                       "emoji) VALUES (%s, %s, 'shrug')", (mid, uid),
                       "an unknown reaction"):
            raise Refuse("ck_reaction_emoji accepted an unknown reaction.")
        say("  . ck_reaction_emoji refuses an unknown reaction")
        cur.execute("SAVEPOINT twice")
        try:
            cur.execute("INSERT INTO message_reactions (message_id, user_id, "
                        "emoji) VALUES (%s, %s, 'heart')", (mid, uid))
            twice = not rejects(
                cur, "INSERT INTO message_reactions (message_id, user_id, "
                     "emoji) VALUES (%s, %s, 'haha')", (mid, uid),
                "a second reaction by the same person")
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT twice")
        if twice:
            raise Refuse("one person could react to one message twice.")
        say("  . one reaction per person per message, enforced by the key")

    after = count(cur, "messages")
    if after != before:
        raise Refuse(f"messages changed {before} -> {after}")
    return changed


def _applied_reactions(cur):
    return (all(column_exists(cur, "messages", c) for c, _ in _REPLY_COLUMNS)
            and table_exists(cur, "message_reactions")
            and index_exists(cur, "ix_reactions_message"))


_EMAIL_NOTICE_KINDS = _REPORT_NOTICE_KINDS + ("meetup",)
_EMAIL_USER_COLUMNS = (
    # When a visible tab last spoke to /live. Null until they next visit.
    ("last_seen_at", "TIMESTAMP"),
    # Both kinds of email start on; anybody can turn either off.
    ("email_alerts", "BOOLEAN NOT NULL DEFAULT true"),
    ("email_digest", "BOOLEAN NOT NULL DEFAULT true"),
    ("digest_sent_at", "TIMESTAMP"),
)


def _work_email_alerts(cur, dbname, say):
    """Email alerts and the every-other-day summary.

    Four columns on "user", one on notices, and a new notice kind for a
    meetup posted in somebody's chat with you. Nothing is backfilled: every
    account starts with both emails on and nothing yet emailed about, which
    is what they are.
    """
    for needed in ("user", "notices"):
        if not table_exists(cur, needed):
            raise Refuse(f"{dbname} has no {needed!r} table.")

    users_before = count(cur, "user")
    notices_before = count(cur, "notices")
    say(f"  {dbname}: {users_before} account(s), {notices_before} notice(s)")

    changed = 0
    for column, decl in _EMAIL_USER_COLUMNS:
        if column_exists(cur, "user", column):
            say(f'  = "user".{column} already exists')
        else:
            cur.execute(f'ALTER TABLE "user" ADD COLUMN {column} {decl}')
            say(f'  + added "user".{column}')
            changed += 1

    if column_exists(cur, "notices", "emailed_at"):
        say("  = notices.emailed_at already exists")
    else:
        cur.execute("ALTER TABLE notices ADD COLUMN emailed_at TIMESTAMP")
        say("  + added notices.emailed_at")
        changed += 1

    changed += widen(cur, "notices", "ck_notice_kind",
                     f"kind IN ({quoted(_EMAIL_NOTICE_KINDS)})",
                     "meetup", say)

    # The widened rule still refuses what it should.
    cur.execute('SELECT id FROM "user" ORDER BY id LIMIT 1')
    row = cur.fetchone()
    if row is None:
        say("  . no accounts, so the probe below is skipped")
    else:
        if not rejects(cur,
                       "INSERT INTO notices (user_id, kind, body, url, "
                       "created_at) VALUES (%s, 'nonsense', 'x', '/', now())",
                       (row[0],), "an unknown notice kind"):
            raise Refuse("ck_notice_kind accepted an unknown kind.")
        say("  . ck_notice_kind still refuses an unknown kind")

    if count(cur, "user") != users_before:
        raise Refuse(f"accounts changed {users_before} -> {count(cur, 'user')}")
    if count(cur, "notices") != notices_before:
        raise Refuse(f"notices changed {notices_before} -> {count(cur, 'notices')}")
    return changed


def _applied_email_alerts(cur):
    current = constraint_def(cur, "ck_notice_kind") or ""
    return (all(column_exists(cur, "user", c) for c, _ in _EMAIL_USER_COLUMNS)
            and column_exists(cur, "notices", "emailed_at")
            and "meetup" in current)


def _work_signup_expiry(cur, dbname, say):
    """When each account was made, so an unverified one can expire.

    One column on "user", and nothing backfilled. The app only ever removes
    an unverified account that has a sign-up time -- one made under the
    rule -- so every account already here, the students stuck behind a
    failed email among them, is left for an admin to verify by hand rather
    than deleted the minute the new code starts.
    """
    if not table_exists(cur, "user"):
        raise Refuse(f"{dbname} has no 'user' table.")
    users_before = count(cur, "user")
    say(f"  {dbname}: {users_before} account(s)")

    changed = 0
    if column_exists(cur, "user", "created_at"):
        say('  = "user".created_at already exists')
    else:
        cur.execute('ALTER TABLE "user" ADD COLUMN created_at TIMESTAMP')
        say('  + added "user".created_at')
        changed += 1

    if count(cur, "user") != users_before:
        raise Refuse(f"accounts changed {users_before} -> {count(cur, 'user')}")
    return changed


def _applied_signup_expiry(cur):
    return column_exists(cur, "user", "created_at")


def _work_fixture_schedule(cur, dbname, say):
    """Campus-wide meetups that appear at a time their poster chooses.

    Two columns on fixtures: publish_at (when it should appear) and
    published_at (when it did). Every fixture already there went up the
    moment it was posted, so each is stamped published at its created_at --
    otherwise they would all vanish from the list until the app published
    them a second time.
    """
    if not table_exists(cur, "fixtures"):
        raise Refuse(f"{dbname} has no 'fixtures' table.")
    before = count(cur, "fixtures")
    say(f"  {dbname}: {before} fixture(s)")

    changed = 0
    for column in ("publish_at", "published_at"):
        if column_exists(cur, "fixtures", column):
            say(f"  = fixtures.{column} already exists")
        else:
            cur.execute(f"ALTER TABLE fixtures ADD COLUMN {column} TIMESTAMP")
            say(f"  + added fixtures.{column}")
            changed += 1

    cur.execute("UPDATE fixtures SET published_at = created_at "
                "WHERE published_at IS NULL AND publish_at IS NULL")
    say(f"  . marked {cur.rowcount} existing fixture(s) as already published")
    changed += cur.rowcount

    if count(cur, "fixtures") != before:
        raise Refuse(f"fixtures changed {before} -> {count(cur, 'fixtures')}")
    return changed


def _applied_fixture_schedule(cur):
    return (column_exists(cur, "fixtures", "publish_at")
            and column_exists(cur, "fixtures", "published_at"))


# ---------------------------------------------------------- the registry
#
# In the order they were written, which is the order they have to run in:
# add_game_here needs add_game_presence's columns, add_fixture_title needs
# add_fixtures' table, add_meetup_presets needs the activities seeder, and
# drop_ufind undoes part of add_fixtures.
#
# `db/` was never in version control, so this order came from the files'
# modification times, cross-checked against the four dependencies the
# scripts state out loud.

MIGRATIONS = [
    ("add_gender", "\"user\".gender, backfilled and constrained",
     _work_gender, _applied_gender),
    ("community", "game_posts, game_post_rsvps, messages.room/post_id",
     _work_community, _applied_community),
    ("pairings", "game_queue, game_pairings, messages.pairing_id",
     _work_pairings, _applied_pairings),
    ("match_requests", "matches.requested_by and its three CHECKs",
     _work_match_requests, _applied_match_requests),
    ("add_pronouns", "\"user\".pronouns, with a default and a CHECK",
     _work_pronouns, _applied_pronouns),
    ("add_group_chats", "chats, chat_members, chat_invites, messages.chat_id",
     _work_group_chats, _applied_group_chats),
    ("add_game_close", "games.closed_a/closed_b",
     _work_game_close, _applied_game_close),
    ("add_game_events", "messages.game_id/event, and the outcome CHECK",
     _work_game_events, _applied_game_events),
    ("add_notices", "the notices table and its indexes",
     _work_notices, _applied_notices),
    ("add_notice_bump", "notices.bump and its sequence",
     _work_notice_bump, _applied_notice_bump),
    ("add_meetup_presets", "activities.preset (needs the activities seeder)",
     _work_meetup_presets, _applied_meetup_presets),
    ("add_online_matchmaking", "game_seek, game_pairings.mode",
     _work_online_matchmaking, _applied_online_matchmaking),
    ("add_game_presence", "games.seen_a/seen_b",
     _work_game_presence, _applied_game_presence),
    ("add_game_here", "games.here_a/here_b (needs add_game_presence)",
     _work_game_here, _applied_game_here),
    ("add_meetup_chats", "chats.post_id/expires_at, game_posts.when_on",
     _work_meetup_chats, _applied_meetup_chats),
    ("add_game_ended_by", "games.ended_by",
     _work_game_ended_by, _applied_game_ended_by),
    ("add_post_audience", "game_posts.audience",
     _work_post_audience, _applied_post_audience),
    ("add_user_discoverable", "\"user\".discoverable",
     _work_discoverable, _applied_discoverable),
    ("add_admin_and_hubs", "\"user\".is_admin and the hub_requests queue",
     _work_admin_and_hubs, _applied_admin_and_hubs),
    # Not one of the original scripts: the map schema only ever arrived
    # through app.py's before_request, which cannot reach live. Placed here
    # because add_fixtures refuses without map_flags.post_id.
    ("add_map_flags", "map_flags.post_id/updated_at, map_flag_viewers",
     _work_map_flags, _applied_map_flags),
    ("add_fixtures", "the fixtures table and can_post_fixtures",
     _work_fixtures, _applied_fixtures),
    ("add_fixture_title", "fixtures.title (needs add_fixtures)",
     _work_fixture_title, _applied_fixture_title),
    ("add_post_starts_at", "game_posts.starts_at",
     _work_post_starts_at, _applied_post_starts_at),
    ("drop_ufind", "takes the U-Find category and price back out",
     _work_drop_ufind, _applied_drop_ufind),
    # A schema bug rather than a feature: ck_hub_request_decided named a
    # column a SET NULL cascade could null behind it.
    ("fix_hub_decided", "ck_hub_request_decided, restated over decided_at",
     _work_fix_hub_decided, _applied_fix_hub_decided),
    # After add_map_flags, because a report can point at a map pin.
    ("add_reports", "the reports queue and three new notice kinds",
     _work_reports, _applied_reports),
    ("add_reactions", "message replies and emoji reactions",
     _work_reactions, _applied_reactions),
    ("add_email_alerts", "email alerts, the summary, and a meetup notice kind",
     _work_email_alerts, _applied_email_alerts),
    ("add_signup_expiry", "\"user\".created_at, so unverified accounts expire",
     _work_signup_expiry, _applied_signup_expiry),
    ("add_fixture_schedule", "fixtures.publish_at and published_at",
     _work_fixture_schedule, _applied_fixture_schedule),
]

MIGRATION_NAMES = [m[0] for m in MIGRATIONS]


def migration(name):
    for entry in MIGRATIONS:
        if entry[0] == name:
            return entry
    raise Refuse(f"no migration called {name!r}. "
                 "Run `migrate --list` to see them.")


# ------------------------------------------------------------- the runners

def run_migration(name, live=False, apply_=False, echo=True):
    """One migration, in one transaction, committed only if apply_."""
    mname, summary, work, _ = migration(name)
    say = Say(echo)
    say(f"  {mname}: {summary}")
    conn, dbname = connect(live, apply_, say)
    try:
        changed = work(conn.cursor(), dbname, say)
    except Refuse as exc:
        conn.rollback()
        conn.close()
        say(f"\n  REFUSED: {exc}")
        return Result(False, 0, False, str(exc), say.text)
    except Exception as exc:
        conn.rollback()
        conn.close()
        say(f"\n  FAILED: {exc}")
        raise
    return finish(conn, apply_, changed, say)


def migration_status(live=False, echo=True):
    """Which migrations are applied, without changing anything.

    Read-only, and each probe is the migration's own. A list that disagreed
    with what a run would do would be worse than no list at all.
    """
    say = Say(echo)
    conn, dbname = connect(live, False, say, readonly=True)
    cur = conn.cursor()
    rows = []
    for name, summary, _, applied in MIGRATIONS:
        try:
            done = applied(cur)
        except psycopg2.Error:
            # A probe that cannot run means the tables it asks about are not
            # there, which is the same answer as "not applied".
            conn.rollback()
            cur = conn.cursor()
            done = False
        rows.append((name, summary, done))
        say(f"  {'applied' if done else 'PENDING'}  {name:24} {summary}")
    waiting = [r[0] for r in rows if not r[2]]
    say()
    if waiting:
        say(f"  {len(waiting)} pending on {dbname}: {', '.join(waiting)}")
    else:
        say(f"  all {len(rows)} applied on {dbname}.")
    conn.close()
    return rows, say.text


def _catalogs(live, say):
    """One database's structure: columns, constraints, indexes.

    Read-only, and structure only. Row data is deliberately ignored: the two
    databases diverge legitimately the moment the clone is made, because dev
    carries harness accounts and whatever the suites leave behind.
    """
    conn, name = connect(live, False, None, readonly=True)
    _, host = where(live)
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name, column_name, data_type,
               coalesce(character_maximum_length, 0),
               is_nullable, coalesce(column_default, '')
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """)
    columns = {(r[0], r[1]): tuple(r[2:]) for r in cur.fetchall()}

    cur.execute("""
        SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE connamespace = 'public'::regnamespace
    """)
    constraints = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    cur.execute("SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public'")
    indexes = dict(cur.fetchall())

    conn.close()
    say(f"  {name:14} @ {host:15} "
        f"{len(columns)} columns, {len(constraints)} constraints, "
        f"{len(indexes)} indexes")
    return name, columns, constraints, indexes


def _fmt(value):
    if isinstance(value, tuple):
        return " ".join(str(v) for v in value if str(v) != "")
    return str(value)


def _report(kind, live_name, live, dev_name, dev, say):
    """Print the differences for one catalog. True when they agree."""
    only_live = sorted(set(live) - set(dev))
    only_dev = sorted(set(dev) - set(live))
    differing = sorted(k for k in set(live) & set(dev) if live[k] != dev[k])

    if not (only_live or only_dev or differing):
        say(f"  {kind:12} {len(live):>4}  {len(dev):>4}  same")
        return True

    say(f"  {kind:12} {len(live):>4}  {len(dev):>4}  DIFFERENT")
    for key in only_dev:
        say(f"      missing in {live_name}: {_fmt(key)}  {_fmt(dev[key])}")
    for key in only_live:
        say(f"      missing in {dev_name}: {_fmt(key)}  {_fmt(live[key])}")
    for key in differing:
        say(f"      differs: {_fmt(key)}")
        say(f"        {live_name}: {_fmt(live[key])}")
        say(f"        {dev_name}: {_fmt(dev[key])}")
    return False


def status(echo=True):
    """Do live and dev have the same shape? Read-only in both directions.

    The one command that is not "pick a database": it opens both at once.
    """
    say = Say(echo)
    say()
    live_name, live_cols, live_cons, live_idx = _catalogs(True, say)
    dev_name, dev_cols, dev_cons, dev_idx = _catalogs(False, say)

    if live_name == dev_name:
        raise Refuse(f"both URLs resolve to {live_name!r}. "
                     "There is nothing to compare.")

    say()
    say(f"  {'catalog':12} {'live':>4}  {'dev':>4}")
    agree = all([
        _report("columns", live_name, live_cols, dev_name, dev_cols, say),
        _report("constraints", live_name, live_cons, dev_name, dev_cons, say),
        _report("indexes", live_name, live_idx, dev_name, dev_idx, say),
    ])

    say()
    say(f"  schemas agree: {'yes' if agree else 'NO'}")
    if not agree:
        rows, _ = migration_status(live=True, echo=False)
        waiting = [n for n, _, done in rows if not done]
        if waiting:
            say(f"  {live_name} is behind. Pending there: "
                f"{', '.join(waiting)}")
            say("  Run `python dbtool.py migrate --all --live --apply`.")
        else:
            say(f"  {live_name} differs but no migration is pending, so the "
                "difference is not one this tool can close.")
    return Result(agree, 0, False,
                  "schemas agree" if agree else "schemas differ", say.text)


def run_pending(live=False, apply_=False, echo=True):
    """Every pending migration, in order, each in its own transaction.

    Not one transaction over all of them: a migration that refuses should
    leave the ones before it in place, because they were correct. The run
    stops at the first refusal rather than carrying on past it.
    """
    say = Say(echo)
    rows, _ = migration_status(live, echo=False)
    waiting = [name for name, _, done in rows if not done]
    if not waiting:
        say("  nothing pending.")
        return Result(True, 0, False, "nothing pending", say.text)

    say(f"  {len(waiting)} pending: {', '.join(waiting)}")
    say()
    total = 0
    for name in waiting:
        out = run_migration(name, live, apply_, echo=False)
        say(out.output)
        if not out.ok:
            say(f"\n  stopped at {name}: {out.summary}")
            return Result(False, total, apply_, f"stopped at {name}",
                          say.text)
        total += out.changed
    return Result(True, total, apply_ and total > 0,
                  f"{len(waiting)} migration(s), {total} change(s)",
                  say.text)


# ------------------------------------------------------------ operations

def run_op(label, work, live=False, apply_=False, echo=True, **kw):
    """One operation, in one transaction, committed only if apply_.

    The same wrapper run_migration uses, so every command in this module
    gets the same two gates and the same dry-run report.
    """
    say = Say(echo)
    say(f"  {label}")
    conn, dbname = connect(live, apply_, say)
    try:
        changed = work(conn.cursor(), dbname, say, **kw)
    except Refuse as exc:
        conn.rollback()
        conn.close()
        say(f"\n  REFUSED: {exc}")
        return Result(False, 0, False, str(exc), say.text)
    except Exception as exc:
        conn.rollback()
        conn.close()
        say(f"\n  FAILED: {exc}")
        raise
    return finish(conn, apply_, changed, say)


# ---- the seeders -------------------------------------------------------
#
# All three used to go through the models inside an app context, which bound
# DATABASE_URL at import and so could never be aimed at live. They are
# psycopg2 now, and they add or update but never delete: a question, hub or
# activity that has left the data above is reported and kept, because posts
# and answers refer to them by name. `reset_catalogue` is the one that
# removes things, deliberately and loudly.


def seed_questions_work(cur, dbname, say):
    """The questionnaire. Adds what is missing, updates what has moved."""
    keys = [spec["key"] for spec in QUESTIONS]
    if len(keys) != len(set(keys)):
        raise Refuse("the question data names a key twice.")

    added = updated = options_added = 0
    for spec in QUESTIONS:
        cur.execute("SELECT id FROM questions WHERE key = %s", (spec["key"],))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO questions (key, prompt, allows_multiple,"
                        " weight) VALUES (%s, %s, %s, %s) RETURNING id",
                        (spec["key"], spec["prompt"], spec["allows_multiple"],
                         spec["weight"]))
            question_id = cur.fetchone()[0]
            added += 1
        else:
            question_id = row[0]
            cur.execute("UPDATE questions SET prompt = %s, "
                        "allows_multiple = %s, weight = %s WHERE id = %s",
                        (spec["prompt"], spec["allows_multiple"],
                         spec["weight"], question_id))
            updated += 1

        for order, label in enumerate(spec["options"]):
            cur.execute("SELECT id FROM question_options "
                        "WHERE question_id = %s AND label = %s",
                        (question_id, label))
            got = cur.fetchone()
            if got is None:
                cur.execute("INSERT INTO question_options (question_id, "
                            "label, sort_order) VALUES (%s, %s, %s)",
                            (question_id, label, order))
                options_added += 1
            else:
                cur.execute("UPDATE question_options SET sort_order = %s "
                            "WHERE id = %s", (order, got[0]))

        # Never removed here: an answer row points at an option, so dropping
        # one would take somebody's answer with it. reset-catalogue does
        # that on purpose and says so.
        cur.execute("""SELECT label FROM question_options
                        WHERE question_id = %s AND label <> ALL(%s)""",
                    (question_id, list(spec["options"])))
        for (stale,) in cur.fetchall():
            say(f"  kept {spec['key']}/{stale!r} -- no longer in this file")

    say(f"  {len(keys)} question(s): {added} added, {updated} updated, "
        f"{options_added} new option(s)")

    cur.execute("SELECT count(*) FROM questions WHERE key = ANY(%s)", (keys,))
    if cur.fetchone()[0] != len(keys):
        raise Refuse("not every question in the data is on the database.")
    return added + options_added


def seed_clubs_work(cur, dbname, say):
    """The hubs and their tags.

    A tag naming an option that does not exist is refused rather than
    skipped: recommend_clubs reaches clubs only through club_tags, so a hub
    whose tags all failed could never be suggested to anybody.
    """
    names = [name for name, _, _ in CLUBS]
    if len(names) != len(set(names)):
        raise Refuse("the hub data names a hub twice.")

    cur.execute("""SELECT q.key, o.label, o.question_id, o.id
                     FROM question_options o
                     JOIN questions q ON q.id = o.question_id""")
    options = {(key, label): (qid, oid)
               for key, label, qid, oid in cur.fetchall()}

    unknown = sorted({tag for _, _, tags in CLUBS for tag in tags
                      if tag not in options})
    if unknown:
        for key, label in unknown:
            say(f"      unknown option: {key}/{label!r}")
        raise Refuse(f"{len(unknown)} hub tag(s) name an option that does "
                     "not exist. Seed the questions first.")

    added = tags_added = 0
    for name, description, tags in CLUBS:
        cur.execute("SELECT id FROM clubs WHERE name = %s", (name,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO clubs (name, description) "
                        "VALUES (%s, %s) RETURNING id", (name, description))
            club_id = cur.fetchone()[0]
            added += 1
        else:
            club_id = row[0]
            cur.execute("UPDATE clubs SET description = %s WHERE id = %s",
                        (description, club_id))

        for tag in dict.fromkeys(tags):          # the same tag twice is once
            question_id, option_id = options[tag]
            cur.execute("""SELECT 1 FROM club_tags WHERE club_id = %s
                             AND question_id = %s AND option_id = %s""",
                        (club_id, question_id, option_id))
            if cur.fetchone() is None:
                cur.execute("INSERT INTO club_tags (club_id, question_id, "
                            "option_id) VALUES (%s, %s, %s)",
                            (club_id, question_id, option_id))
                tags_added += 1

    say(f"  {len(names)} hub(s): {added} added, {tags_added} new tag(s)")

    untagged = count(cur, "clubs", """NOT EXISTS (SELECT 1 FROM club_tags t
                                      WHERE t.club_id = clubs.id)""")
    if untagged:
        raise Refuse(f"{untagged} hub(s) have no tags and could never be "
                     "suggested to anybody.")
    return added + tags_added


def seed_activities_work(cur, dbname, say):
    """The activity catalogue and its venues."""
    unknown = set(ACTIVITY_PRESETS) - {n for n, _, _, _ in ACTIVITIES}
    if unknown:
        raise Refuse(f"the preset list names nothing in ACTIVITIES: "
                     f"{sorted(unknown)}")

    added = venues_added = 0
    for order, (name, online, in_person, venues) in enumerate(ACTIVITIES):
        cur.execute("SELECT id FROM activities WHERE name = %s", (name,))
        row = cur.fetchone()
        preset = name in set(ACTIVITY_PRESETS)
        if row is None:
            cur.execute("INSERT INTO activities (name, online, in_person, "
                        "preset, sort_order) VALUES (%s, %s, %s, %s, %s) "
                        "RETURNING id",
                        (name, online, in_person, preset, order))
            activity_id = cur.fetchone()[0]
            added += 1
        else:
            activity_id = row[0]
            cur.execute("UPDATE activities SET online = %s, in_person = %s, "
                        "preset = %s, sort_order = %s WHERE id = %s",
                        (online, in_person, preset, order, activity_id))

        cur.execute("SELECT name FROM activity_locations "
                    "WHERE activity_id = %s", (activity_id,))
        have = {r[0] for r in cur.fetchall()}
        for venue_order, venue in enumerate(venues):
            if venue in have:
                cur.execute("UPDATE activity_locations SET sort_order = %s "
                            "WHERE activity_id = %s AND name = %s",
                            (venue_order, activity_id, venue))
                have.discard(venue)
            else:
                cur.execute("INSERT INTO activity_locations (activity_id, "
                            "name, sort_order) VALUES (%s, %s, %s)",
                            (activity_id, venue, venue_order))
                venues_added += 1

        # Never delete a venue that has left the data: existing meetup posts
        # refer to these by name. Report and keep it.
        for stale in sorted(have):
            say(f"  kept {name}/{stale!r} -- no longer in this file")

    say(f"  {len(ACTIVITIES)} activity(s): {added} added, "
        f"{venues_added} new venue(s)")
    offered = count(cur, "activities", "preset")
    if offered != len(ACTIVITY_PRESETS):
        raise Refuse(f"{offered} activity(s) carry the preset flag, "
                     f"expected {len(ACTIVITY_PRESETS)}")
    say(f"  . {offered} offered in the pickers, "
        f"{count(cur, 'activities') - offered} write-in only")
    return added + venues_added


SEEDERS = {
    "questions": ("the questionnaire", seed_questions_work),
    "clubs": ("the hubs and their tags", seed_clubs_work),
    "activities": ("the activity catalogue", seed_activities_work),
}


# ---- replacing the catalogue -------------------------------------------

def reset_catalogue_work(cur, dbname, say):
    """Replace the questionnaire and the hubs, and make everybody re-answer.

    The seeders only ever add. That is right when a question is being
    appended and exactly wrong when one is retired: the old question stays
    on /onboarding and stays scored by matching.py. This is the other half.

    Every answer goes, deliberately. Onboarding's gate is "has at least one
    answer row", so a student whose major answer survived would stay through
    it with the new questions unanswered, match nobody, and never be asked.
    """
    keys = [spec["key"] for spec in QUESTIONS]
    names = [name for name, _, _ in CLUBS]
    say(f"  data: {len(keys)} questions, {len(names)} hubs")

    before = {
        "clubs": count(cur, "clubs"),
        "club_members": count(cur, "club_members"),
        "club messages": count(cur, "messages", "club_id IS NOT NULL"),
        "questions": count(cur, "questions"),
        "question_options": count(cur, "question_options"),
        "user_answers": count(cur, "user_answers"),
    }
    say(f"  {dbname} holds:")
    for what, n in before.items():
        say(f"      {n:>6}  {what}")
    say()

    changed = 0

    # ---- the hubs -------------------------------------------------------
    # Every one, not just the ones no longer listed: the list is being
    # replaced wholesale, and a hub kept by name would keep a membership
    # list built from a questionnaire that no longer exists.
    if before["clubs"]:
        cur.execute("DELETE FROM clubs")
        say(f"  - clubs: {before['clubs']} removed, with their tags, members "
            f"and {before['club messages']} club message(s)")
        changed += 1
    else:
        say("  = no clubs to remove")

    # ---- questions no longer in the data --------------------------------
    cur.execute("SELECT key FROM questions WHERE key <> ALL(%s)", (keys,))
    retired = [row[0] for row in cur.fetchall()]
    if retired:
        cur.execute("DELETE FROM questions WHERE key <> ALL(%s)", (keys,))
        say(f"  - questions: dropped {', '.join(sorted(retired))}")
        changed += 1
    else:
        say("  = every question on the database is still in the data")

    # ---- options no longer in the data ----------------------------------
    dropped = 0
    for spec in QUESTIONS:
        cur.execute("""DELETE FROM question_options o
                        USING questions q
                        WHERE q.id = o.question_id AND q.key = %s
                          AND o.label <> ALL(%s)""",
                    (spec["key"], list(spec["options"])))
        dropped += cur.rowcount
    if dropped:
        say(f"  - question_options: {dropped} stale option(s) removed")
        changed += 1
    else:
        say("  = no stale options")

    # ---- everybody answers again ----------------------------------------
    answers = count(cur, "user_answers")
    if answers:
        cur.execute("DELETE FROM user_answers")
        say(f"  - user_answers: {answers} row(s); every account will be "
            "asked the questionnaire again")
        changed += 1
    else:
        say("  = no answers to clear")

    # ---- then the seeds, in the same transaction ------------------------
    seed_questions_work(cur, dbname, say)
    seed_clubs_work(cur, dbname, say)
    changed += 2

    # ---- the assertions that matter --------------------------------------
    after_clubs = count(cur, "clubs")
    after_questions = count(cur, "questions")
    if after_clubs != len(names):
        raise Refuse(f"{after_clubs} clubs, expected {len(names)}")
    if after_questions != len(keys):
        raise Refuse(f"{after_questions} questions, expected {len(keys)}")
    if count(cur, "user_answers"):
        raise Refuse(f"{count(cur, 'user_answers')} answer(s) survived; "
                     "somebody would stay through the onboarding gate.")

    # Accounts are not touched by any of this, and that is worth proving.
    say(f"  . {after_clubs} hubs, {after_questions} questions, "
        f"{count(cur, 'club_tags')} tags, {count(cur, 'user')} accounts "
        "untouched")
    return changed


# ---- who may do what ---------------------------------------------------

def admin_list(live=False, echo=True):
    """Who holds admin. Read-only."""
    say = Say(echo)
    conn, dbname = connect(live, False, say, readonly=True)
    cur = conn.cursor()
    cur.execute('SELECT email, name FROM "user" WHERE is_admin ORDER BY email')
    had = cur.fetchall()
    for email, name in had:
        say(f"  . {email}  ({name})")
    if not had:
        say("  . nobody reviews requests on this database yet")
    conn.close()
    say()
    say(f"  {len(had)} admin(s) on {dbname}. Nothing was changed.")
    return Result(True, 0, False, f"{len(had)} admin(s)", say.text)


def admin_set_work(cur, dbname, say, email=None, revoke=False):
    """Grant or revoke admin on one account, by email.

    How the FIRST admin on a database is appointed. After that the admin
    page hands it out too -- but the page refuses to remove the last one,
    because the page is the only way back to itself. This will, because a
    command can be run again and a locked page cannot.

    An address matching nobody is refused rather than a quiet no-op: a typo
    would otherwise look exactly like success and leave a database with no
    reviewer at all, which nothing else would tell you about.
    """
    if not email:
        raise Refuse("pass an email address.")

    cur.execute('SELECT email, name FROM "user" WHERE is_admin ORDER BY email')
    for had_email, name in cur.fetchall():
        say(f"  . {had_email}  ({name})")

    want = not revoke
    cur.execute('SELECT id, name, is_admin FROM "user" '
                "WHERE lower(email) = %s", (email.strip().lower(),))
    row = cur.fetchone()
    if row is None:
        raise Refuse(f"no account on {dbname} has the address {email!r}. "
                     "Check the spelling, or list who is already an admin.")

    user_id, name, now = row
    changed = 0
    if now == want:
        say(f"  = {name} is already "
            f"{'an admin' if want else 'an ordinary student'}")
    else:
        cur.execute('UPDATE "user" SET is_admin = %s WHERE id = %s',
                    (want, user_id))
        say(f"  {'+' if want else '-'} {name} "
            f"{'now reviews' if want else 'no longer reviews'} hub requests")
        changed = 1

    # The failure worth refusing: revoking the last one leaves requests with
    # nobody to look at them, and the students who sent them are never told.
    left = count(cur, "user", "is_admin")
    if left == 0:
        waiting = count(cur, "hub_requests", "status = 'pending'")
        if waiting:
            raise Refuse(f"that would leave {dbname} with no admin and "
                         f"{waiting} request(s) waiting.")
        say("  . nobody is an admin now, and nothing is waiting")
    else:
        say(f"  . {left} admin(s) on {dbname}")
    return changed


# ---- the two clean-ups -------------------------------------------------

def clear_dead_invites_work(cur, dbname, say):
    """Remove game invites that nobody can ever answer.

    An invite is answered from two places and only those two: the buttons on
    its invite message in the chat, and the same pair on the bell notice. An
    invite with neither has no way to be answered, and is not harmless --
    LIVE_GAME = ("invited", "active"), so it counts as a game in progress:
    the two players cannot invite each other to anything else, and the lobby
    tells the invitee the game is "waiting on you" and links them to a chat
    with nothing in it to click.

    Deleted rather than marked declined because nothing refers to them, so
    there is no history to keep consistent -- and marking one declined would
    put a result card for a game that never happened into a conversation
    days later.

    Found by that rule rather than by a list of ids, so this cannot delete a
    game that has been answered since.
    """
    rule = """status = 'invited'
              AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.game_id = games.id)
              AND NOT EXISTS (SELECT 1 FROM notices n WHERE n.game_id = games.id)"""

    cur.execute(f"""
        SELECT g.id, g.kind, g.created_at, g.player_a, g.player_b
          FROM games g
         WHERE g.status = 'invited'
           AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.game_id = g.id)
           AND NOT EXISTS (SELECT 1 FROM notices n WHERE n.game_id = g.id)
         ORDER BY g.id
    """)
    rows = cur.fetchall()
    if not rows:
        say("  nothing to do: every pending invite can be answered.")
        return 0

    cur.execute('SELECT id, name FROM "user"')
    names = dict(cur.fetchall())
    say(f"  {len(rows)} invite(s) nobody can answer:")
    for game_id, kind, made, player_a, player_b in rows:
        say(f"   game {game_id:<5} {kind:<10} made {made:%Y-%m-%d %H:%M}"
            f"   {names.get(player_a, player_a)}"
            f"  ->  {names.get(player_b, player_b)}")

    ids = [r[0] for r in rows]
    # The same rule again in the WHERE clause, not just the id list: if one
    # of these were answered between the read above and this delete, it no
    # longer matches and is left alone.
    cur.execute(f"DELETE FROM games WHERE id = ANY(%s) AND {rule}", (ids,))
    removed = cur.rowcount
    if removed != len(ids):
        raise Refuse(f"would have deleted {removed} of {len(ids)} rows.")
    say(f"  - deleted {removed} row(s)")

    left = count(cur, "games", rule)
    if left:
        raise Refuse(f"{left} unanswerable invite(s) remain.")
    cur.execute("SELECT status, count(*) FROM games GROUP BY status "
                "ORDER BY 1")
    say(f"  . games by status now: {dict(cur.fetchall())}")
    return removed


def delete_account_work(cur, dbname, say, email=None):
    """Delete one account and everything attached to it, by email.

    One address at a time on purpose, so a typo cannot clear more than a
    single row. This is the strictest thing here: the script it replaces,
    run against live, once deleted the owner's real account along with 15
    answers, a club membership and 4 matches -- which is why `--live` is
    required separately from `--apply` and why the counts are printed before
    and after rather than assumed.
    """
    if not email:
        raise Refuse("pass an email address.")

    cur.execute('SELECT id, name, email_verified FROM "user" '
                "WHERE lower(email) = %s", (email.strip().lower(),))
    row = cur.fetchone()
    if row is None:
        raise Refuse(f"{email} is not registered on {dbname} -- "
                     "nothing to delete.")

    user_id, name, verified = row

    # The one thing a bare DELETE gets badly wrong. Chat.owner_id is
    # CASCADE, so deleting somebody who made a group chat destroys the chat,
    # its members, its invites and every message anybody ever wrote in it.
    # The app's own /account/delete hands such a chat to its longest-standing
    # member instead; that logic is Flask and SQLAlchemy and cannot be
    # reached from here, so rather than writing a second copy that would
    # drift, this refuses and says where to do it properly -- the same choice
    # take_backup makes when something is attached to the database.
    cur.execute("""
        SELECT c.name, count(m.user_id) - 1
          FROM chats c
          JOIN chat_members m ON m.chat_id = c.id
         WHERE c.owner_id = %s AND c.post_id IS NULL
         GROUP BY c.id, c.name
        HAVING count(m.user_id) > 1
         ORDER BY c.name
    """, (user_id,))
    shared = cur.fetchall()
    if shared:
        say(f"  {name} owns {len(shared)} group chat(s) with other people "
            "in them:")
        for chat_name, others in shared:
            say(f"    {chat_name}  ({others} other member(s))")
        raise Refuse(
            "deleting this account here would destroy those chats and every "
            "message in them. Have them delete their own account from the "
            "account page, which hands each chat to its longest-standing "
            "member -- or hand the chats over first.")

    before = {
        "answers": count(cur, "user_answers", "user_id = %s", (user_id,)),
        "clubs": count(cur, "club_members", "user_id = %s", (user_id,)),
        "matches": count(cur, "matches", "user_lo = %s OR user_hi = %s",
                         (user_id, user_id)),
    }
    say(f"  deleting id={user_id}  {name!r}  verified={verified}")
    say(f"  answers={before['answers']}  clubs={before['clubs']}  "
        f"matches={before['matches']}")

    cur.execute('DELETE FROM "user" WHERE id = %s', (user_id,))

    after = {
        "answers": count(cur, "user_answers", "user_id = %s", (user_id,)),
        "clubs": count(cur, "club_members", "user_id = %s", (user_id,)),
        "matches": count(cur, "matches", "user_lo = %s OR user_hi = %s",
                         (user_id, user_id)),
    }
    if any(after.values()):
        raise Refuse(f"rows survived the cascade: {after}")
    say("  . answers, memberships and matches went with it")

    orphans = {
        "answers": count(cur, "user_answers", """NOT EXISTS (SELECT 1 FROM
            "user" u WHERE u.id = user_answers.user_id)"""),
        "clubs": count(cur, "club_members", """NOT EXISTS (SELECT 1 FROM
            "user" u WHERE u.id = club_members.user_id)"""),
    }
    if any(orphans.values()):
        raise Refuse(f"orphaned rows elsewhere: {orphans}")

    if before["matches"]:
        say(f"  . {before['matches']} other student(s) lost a match; they "
            "get topped back up on their next visit")
    say(f"  . {email} deleted -- the address is free to register again")
    return 1


# ---- the demo population -----------------------------------------------

DEMO_PREFIX = "demo."
DEMO_PASSWORD = "demo-account-not-for-login"
DEMO_SEED = 20260929


def _option_ids(cur):
    cur.execute("""SELECT q.key, o.label, q.id, o.id
                     FROM question_options o
                     JOIN questions q ON q.id = o.question_id""")
    options, questions = {}, {}
    for key, label, question_id, option_id in cur.fetchall():
        options[(key, label)] = option_id
        questions[key] = question_id
    return options, questions


def seed_demo_work(cur, dbname, say):
    """Thirty-two students who look like each other in clusters.

    The names are paired and shuffled with a fixed seed, so the same
    accounts come out every run -- which is what lets the suites rely on
    them. The sequence of random calls below is therefore load-bearing:
    reordering it would quietly produce a different population.

    An option label that does not exist is refused rather than skipped,
    because a demo student with no answers matches nobody and the demo is
    then about nothing.
    """
    import random

    from werkzeug.security import generate_password_hash

    options, questions = _option_ids(cur)
    if not options:
        raise Refuse("no questions on this database. Seed the questions "
                     "first.")

    rng = random.Random(DEMO_SEED)
    names = [f"{f} {l}" for f, l in zip(DEMO_FIRST, DEMO_LAST)]
    rng.shuffle(names)

    created = updated = answers = 0
    index = 0
    for cluster in DEMO_CLUSTERS:
        for _ in range(cluster["size"]):
            name = names[index]
            index += 1
            email = (DEMO_PREFIX + name.lower().replace(" ", ".")
                     + "@umb.edu")
            gender = _GENDER_FOR.get(name.split(" ")[0], "undisclosed")

            cur.execute('SELECT id FROM "user" WHERE email = %s', (email,))
            row = cur.fetchone()
            if row is None:
                # verification_attempts is NOT NULL with no database
                # default: models.py fills it from Python, which a raw
                # INSERT does not get. Same for every timestamp below.
                cur.execute(
                    'INSERT INTO "user" (name, gender, email, password_hash,'
                    " email_verified, verification_attempts) "
                    "VALUES (%s, %s, %s, %s, true, 0) RETURNING id",
                    (name, gender, email,
                     generate_password_hash(DEMO_PASSWORD)))
                user_id = cur.fetchone()[0]
                created += 1
            else:
                user_id = row[0]
                cur.execute('UPDATE "user" SET name = %s, gender = %s, '
                            "email_verified = true WHERE id = %s",
                            (name, gender, user_id))
                updated += 1

            # Exactly the order the original made these calls in. Each one
            # advances the generator, so the sequence is the identity of the
            # population rather than an implementation detail.
            picks = {}
            picks["degree_type"] = [rng.choices(
                ["Bachelor's", "Master's"], weights=[85, 15])[0]]
            picks["major"] = [rng.choice(cluster["majors"])]
            picks["grad_year"] = [rng.choice(
                ["2026", "2027", "2028", "2029", "2030"])]
            picks["activity"] = rng.sample(
                cluster["activity"],
                rng.randint(1, len(cluster["activity"])))
            picks["boston"] = rng.sample(
                cluster["boston"],
                rng.randint(1, min(3, len(cluster["boston"]))))
            picks["media"] = rng.sample(
                cluster["media"], rng.randint(1, len(cluster["media"])))
            picks["rainy_day"] = [rng.choice(cluster["rainy"])]
            picks["friday_night"] = rng.sample(
                cluster["friday"],
                rng.randint(1, min(3, len(cluster["friday"]))))
            picks["study_style"] = [rng.choice(cluster["study"])]
            if rng.random() < 0.5:
                picks["minor"] = [rng.choice(cluster["minors"])]

            cur.execute("DELETE FROM user_answers WHERE user_id = %s",
                        (user_id,))
            for key, labels in picks.items():
                for label in labels:
                    option_id = options.get((key, label))
                    if option_id is None:
                        raise Refuse(f"unknown option {key}/{label!r}")
                    cur.execute("INSERT INTO user_answers (user_id, "
                                "question_id, option_id) VALUES (%s, %s, %s)",
                                (user_id, questions[key], option_id))
                    answers += 1

    total = count(cur, "user", "email LIKE %s", (DEMO_PREFIX + "%",))
    say(f"  {created} created, {updated} refreshed, {answers} answer(s); "
        f"{total} demo account(s) total")
    wanted = sum(c["size"] for c in DEMO_CLUSTERS)
    if total < wanted:
        raise Refuse(f"{total} demo accounts, expected at least {wanted}")
    return created + updated


def clear_demo_work(cur, dbname, say):
    """Remove the demo accounts, and everything that cascades with them."""
    cur.execute('SELECT id, email FROM "user" WHERE email LIKE %s',
                (DEMO_PREFIX + "%",))
    rows = cur.fetchall()
    if not rows:
        say("  nothing to do: no demo accounts here.")
        return 0
    cur.execute('DELETE FROM "user" WHERE email LIKE %s',
                (DEMO_PREFIX + "%",))
    say(f"  - removed {cur.rowcount} demo account(s)")
    left = count(cur, "user", "email LIKE %s", (DEMO_PREFIX + "%",))
    if left:
        raise Refuse(f"{left} demo account(s) survived.")
    return len(rows)


# ---- an account you can actually log into -------------------------------

QA_NAME = "QA Tester"
QA_ROOM = "QA Test Room"


def qa_defaults():
    """The address to befriend and the password, from the environment.

    Both used to be constants in db/seed_test_account.py: a real
    institutional address and a literal password. That folder was never in
    version control; this file is, and the repository is public -- so
    neither is written down here. QA_FRIEND and QA_PASSWORD in .env, or
    passed in, and the command refuses rather than guessing.
    """
    return (os.environ.get("QA_FRIEND", "").strip(),
            os.environ.get("QA_PASSWORD", ""))


def seed_test_account_work(cur, dbname, say, friend=None, password=None,
                           reset=False):
    """A second account you can log into, wired up to test chat and games.

    The demo accounts cannot be signed into on purpose, so there is no way
    to hold two real sessions and watch a conversation or a game from both
    sides. A bare user row would not help either: both features are gated
    on an active friendship -- the lobby offers active matches as the
    opponent list, and a 1:1 room resolves through an active matches row --
    so the friendship is as much the point as the account.

    The password is never printed back. In a terminal that was the point;
    in the admin panel it would be writing a credential into a web page,
    and whoever ran it supplied it anyway.
    """
    from werkzeug.security import generate_password_hash

    from models import new_code, pair

    env_friend, env_password = qa_defaults()
    friend = (friend or env_friend).strip()
    password = password or env_password
    if not friend:
        raise Refuse("no address to befriend. Pass one, or set QA_FRIEND "
                     "in .env.")
    if not password:
        raise Refuse("no password given. Pass one, or set QA_PASSWORD "
                     "in .env.")

    cur.execute('SELECT id, name FROM "user" WHERE lower(email) = %s',
                (friend.lower(),))
    row = cur.fetchone()
    if row is None:
        raise Refuse(f"no account {friend!r} to befriend on {dbname}.")
    friend_id, friend_name = row
    say(f"  befriending {friend_name} (id {friend_id})")

    cur.execute('SELECT id FROM "user" WHERE email = %s', (QA_LOGIN,))
    row = cur.fetchone()
    tester_id = row[0] if row else None
    changed = 0

    if reset and tester_id is not None:
        # The room is owned by the friend, so it outlives the tester and has
        # to go by hand; everything else cascades off the user row.
        cur.execute("DELETE FROM chats WHERE owner_id = %s AND name = %s",
                    (friend_id, QA_ROOM))
        if cur.rowcount:
            say(f"  - deleted the room {QA_ROOM!r}")
        cur.execute('DELETE FROM "user" WHERE id = %s', (tester_id,))
        say(f"  - deleted {QA_LOGIN}")
        tester_id = None
        changed += 1

    # --- the account -------------------------------------------------
    if tester_id is None:
        cur.execute('INSERT INTO "user" (name, gender, pronouns, email, '
                    "password_hash, email_verified, verification_attempts) "
                    "VALUES (%s, 'undisclosed', 'they', %s, %s, true, 0) "
                    "RETURNING id",
                    (QA_NAME, QA_LOGIN, generate_password_hash(password)))
        tester_id = cur.fetchone()[0]
        say(f"  + created {QA_LOGIN}")
        changed += 1
    else:
        say(f"  = {QA_LOGIN} already exists")
        # Always reset these two, so a forgotten password or a half-made
        # account from an earlier run cannot leave it unusable.
        cur.execute('UPDATE "user" SET password_hash = %s, '
                    "email_verified = true WHERE id = %s",
                    (generate_password_hash(password), tester_id))

    # --- onboarding ---------------------------------------------------
    if count(cur, "user_answers", "user_id = %s", (tester_id,)) == 0:
        # One answer per question, spread rather than all-first. Every
        # suite's probe accounts answer options[0]; an account doing the
        # same would be a maximum shared-answer match for all of them and
        # could outrank a suite's own probes in the ranking -- a failure
        # this project has already been bitten by once. A stride avoids
        # that while still satisfying the onboarding gate.
        cur.execute("SELECT id FROM questions ORDER BY id")
        questions = [r[0] for r in cur.fetchall()]
        written = 0
        for index, question_id in enumerate(questions):
            cur.execute("SELECT id FROM question_options "
                        "WHERE question_id = %s ORDER BY sort_order, id",
                        (question_id,))
            options = [r[0] for r in cur.fetchall()]
            if not options:
                continue
            option_id = options[(index * 3 + 1) % len(options)]
            cur.execute("INSERT INTO user_answers (user_id, question_id, "
                        "option_id) VALUES (%s, %s, %s)",
                        (tester_id, question_id, option_id))
            written += 1
        say(f"  + answered {written} question(s)")
        changed += 1
    else:
        say("  = already onboarded")

    # --- the friendship, which is what makes chat and games testable --
    lo, hi = pair(tester_id, friend_id)
    cur.execute("SELECT status FROM matches WHERE user_lo = %s AND user_hi = %s",
                (lo, hi))
    row = cur.fetchone()
    if row is None:
        # created_at is NOT NULL with no database default -- see the note in
        # the demo seeder. Naive UTC, which is what models.py stores.
        cur.execute("INSERT INTO matches (user_lo, user_hi, score, status, "
                    "requested_by, created_at) VALUES (%s, %s, 0, 'active', "
                    "%s, (now() AT TIME ZONE 'utc'))", (lo, hi, tester_id))
        say("  + friendship (active)")
        changed += 1
    elif row[0] != "active":
        cur.execute("UPDATE matches SET status = 'active', ended_at = NULL "
                    "WHERE user_lo = %s AND user_hi = %s", (lo, hi))
        say(f"  ~ friendship revived (was {row[0]!r})")
        changed += 1
    else:
        say("  = already friends")

    # --- a club in common ---------------------------------------------
    cur.execute("""SELECT c.id, c.name FROM clubs c
                     JOIN club_members m ON m.club_id = c.id
                    WHERE m.user_id = %s ORDER BY c.id LIMIT 1""",
                (friend_id,))
    row = cur.fetchone()
    if row is None:
        # They are in none, so put them both in the same one.
        cur.execute("SELECT id, name FROM clubs ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row is None:
            raise Refuse("there are no hubs on this database to join.")
        cur.execute("INSERT INTO club_members (user_id, club_id, "
                    "joined_at) VALUES (%s, %s, (now() AT TIME ZONE 'utc'))",
                    (friend_id, row[0]))
        say(f"  + added {friend_name} to {row[1]}")
        changed += 1
    club_id, club_name = row

    cur.execute("SELECT 1 FROM club_members WHERE user_id = %s "
                "AND club_id = %s", (tester_id, club_id))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO club_members (user_id, club_id, "
                    "joined_at) VALUES (%s, %s, (now() AT TIME ZONE 'utc'))",
                    (tester_id, club_id))
        say(f"  + joined {club_name}")
        changed += 1
    else:
        say(f"  = already in {club_name}")

    # --- a group chat in common ---------------------------------------
    cur.execute("SELECT id, code FROM chats WHERE owner_id = %s AND name = %s",
                (friend_id, QA_ROOM))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO chats (name, code, is_public, owner_id) "
                    "VALUES (%s, %s, false, %s) RETURNING id, code",
                    (QA_ROOM, new_code(), friend_id))
        row = cur.fetchone()
        cur.execute("INSERT INTO chat_members (user_id, chat_id) "
                    "VALUES (%s, %s)", (friend_id, row[0]))
        say(f"  + created the room {QA_ROOM!r}")
        changed += 1
    else:
        say(f"  = the room {QA_ROOM!r} already exists")
    room_id, room_code = row

    cur.execute("SELECT 1 FROM chat_members WHERE user_id = %s "
                "AND chat_id = %s", (tester_id, room_id))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO chat_members (user_id, chat_id) "
                    "VALUES (%s, %s)", (tester_id, room_id))
        say("  + joined the room")
        changed += 1
    else:
        say("  = already in the room")

    say(f"\n  log in as {QA_LOGIN} with the password you supplied, then try:")
    say(f"    /chats/match/{friend_id}      the 1:1 chat with {friend_name}")
    say(f"    /chats/club/{club_id}         {club_name}")
    say(f"    /chats/group/{room_id}        {QA_ROOM} (code {room_code})")
    say("    /games                     challenge them to a game")
    say(f"\n  and from {friend_name}'s session, /chats/match/{tester_id}")
    return changed


# ---- clearing a database for real students -----------------------------

QA_LOGIN = "qa.tester@umb.edu"

# Everything a student made. Children first, so this reads as a plan rather
# than relying on which foreign keys happen to cascade -- and so a table
# added later without ON DELETE CASCADE does not quietly survive the wipe.
CONTENT = (
    "notices",
    # Before clubs is read, and before accounts go: a request names the
    # student who asked and the hub it became, and both of those are rows
    # this removes.
    "hub_requests",
    # Before accounts go, and before map_flags: a fixture names who posted
    # it, and its pin points back at the fixture.
    "fixtures",
    "messages",
    "chat_invites",
    "chat_members",
    "chats",
    "game_post_rsvps",
    "game_posts",
    "games",
    "game_pairings",
    "game_queue",
    "game_seek",
    "matches",
    "club_members",
    "map_flags",
)

# The catalogue. Not touched, and checked rather than trusted: these are the
# rows the app cannot run without, and seeding them again is a separate job.
CATALOGUE = (
    "clubs",
    "club_tags",
    "activities",
    "activity_locations",
    "questions",
    "question_options",
)


def _maintenance_url(url):
    """The same server, connected to `postgres`.

    CREATE DATABASE ... TEMPLATE x fails while anything is connected to x,
    and that includes us -- so the backup is taken from another database.
    """
    from urllib.parse import urlunsplit
    return urlunsplit(urlsplit(url)._replace(path="/postgres"))


def sessions_on(cur, dbname):
    cur.execute("""
        SELECT pid, usename, state,
               date_trunc('second', now() - state_change) AS idle_for
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid()
        ORDER BY backend_start
    """, (dbname,))
    return cur.fetchall()


def connections_to(live):
    """Who else is attached to one database. Used by the panel.

    The panel needs this before it offers wipe or clone at all: both take a
    template copy, Postgres refuses one while anything is connected to the
    source, and the web app is itself a connection -- so a request handler
    cannot take a backup of the database it is running against.
    """
    url = url_for(live)
    dbname = urlsplit(url).path.lstrip("/")
    conn = psycopg2.connect(_maintenance_url(url), connect_timeout=8)
    conn.autocommit = True
    try:
        return dbname, sessions_on(conn.cursor(), dbname)
    finally:
        conn.close()


def take_backup(live, say, force_disconnect=False, include_active=False):
    """A template copy of the whole database. Returns its name, or None.

    Not pg_dump, which is not installed here, but `CREATE DATABASE <name>
    TEMPLATE <source>` -- a server-side file copy that carries data,
    indexes, constraints and sequence state.
    """
    import time
    from datetime import datetime, timezone

    url = url_for(live)
    dbname = urlsplit(url).path.lstrip("/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = f"{dbname}_backup_{stamp}"

    conn = psycopg2.connect(_maintenance_url(url), connect_timeout=15)
    conn.autocommit = True      # CREATE DATABASE cannot run in a transaction
    cur = conn.cursor()
    try:
        rows = sessions_on(cur, dbname)
        if rows and force_disconnect:
            for pid, usr, state, idle in rows:
                if include_active or state == "idle":
                    cur.execute("SELECT pg_terminate_backend(%s, 5000)",
                                (pid,))
                    say(f"    ended session pid {pid} ({usr}, {state})")
            for _ in range(20):
                if not sessions_on(cur, dbname):
                    break
                time.sleep(0.25)
            rows = sessions_on(cur, dbname)

        if rows:
            say(f"  {len(rows)} other session(s) are connected to {dbname}:")
            for pid, usr, state, idle in rows:
                say(f"    pid {pid:<8} {usr:<10} {state:<22} idle {idle}")
            say("  a template copy cannot be taken while they are attached.")
            say("  Stop the app, or pass --force-disconnect.")
            return None

        say(f"  taking a backup: {backup}")
        cur.execute(_sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            _sql.Identifier(backup), _sql.Identifier(dbname)))
        say(f"  + {backup} created -- this is the only undo, so keep it "
            "until you are sure")
        return backup
    finally:
        cur.close()
        conn.close()


def _table_counts(cur, tables):
    out = {}
    for table in tables:
        cur.execute(_sql.SQL("SELECT count(*) FROM {}").format(
            _sql.Identifier(table)))
        out[table] = cur.fetchone()[0]
    return out


def wipe(live=False, apply_=False, keep=None, hide=None, reset_answers=False,
         force_disconnect=False, include_active=False, echo=True):
    """Clear a database of everything students made, ready for real ones.

    What survives is the catalogue the app is useless without -- hubs and
    their tags, activities and their venues, the questionnaire -- and
    whichever accounts are named.

    Three things worth knowing:

    It backs up first, and will not write to live without one. Postgres
    refuses a template copy while anything is connected to the source, so
    this reports who is attached and stops rather than wiping something it
    could not copy.

    You say who survives. `keep` is a list of emails; one matching nobody is
    an error rather than a silent no-op, because a typo in it would
    otherwise delete the very account you meant to save.

    It is one transaction, and it checks the catalogue is untouched before
    committing. If a single hub, activity or question moved, it rolls back.
    """
    say = Say(echo)
    keep = list(keep) if keep else [QA_LOGIN]
    hide = list(hide) if hide is not None else [QA_LOGIN]

    conn, dbname = connect(live, apply_, say)
    cur = conn.cursor()

    # ---------------------------------------------------------- who survives
    cur.execute('SELECT id, name, email FROM "user" ORDER BY id')
    everyone = cur.fetchall()
    by_email = {row[2].lower(): row for row in everyone}

    def stop(message):
        conn.rollback()
        conn.close()
        say(f"\n  REFUSED: {message}")
        return Result(False, 0, False, message, say.text)

    missing = [e for e in keep if e.lower() not in by_email]
    if missing:
        return stop(f"--keep names no account here: {', '.join(missing)}. "
                    "Nothing was changed.")

    kept = [by_email[e.lower()] for e in keep]
    kept_ids = [row[0] for row in kept]
    going = [row for row in everyone if row[0] not in kept_ids]

    unknown_hide = [e for e in hide if e.lower() not in by_email]
    if unknown_hide:
        return stop("--hide names no account here: "
                    f"{', '.join(unknown_hide)}")
    not_kept = [e for e in hide if by_email[e.lower()][0] not in kept_ids]
    if not_kept:
        return stop("--hide names accounts that --keep does not spare: "
                    f"{', '.join(not_kept)}")
    hide_ids = [by_email[e.lower()][0] for e in hide]

    say(f"  keeping {len(kept)} account(s):")
    for uid, name, email in kept:
        mark = "  (unlisted)" if uid in hide_ids else ""
        say(f"    {uid:>6}  {name[:28]:28} {email}{mark}")
    say(f"  deleting {len(going)} account(s)"
        + (f", first few: {', '.join(r[2] for r in going[:3])}"
           if going else ""))

    # ------------------------------------------------------------- inventory
    before_content = _table_counts(cur, CONTENT)
    before_catalogue = _table_counts(cur, CATALOGUE)
    before_answers = count(cur, "user_answers")

    say("\n  to be cleared:")
    for table, n in before_content.items():
        say(f"    {table:<20} {n:>6}")
    say(f"    {'user_answers':<20} {before_answers:>6}"
        + ("" if reset_answers else "   (kept accounts' answers stay)"))
    say("\n  to be left alone:")
    for table, n in before_catalogue.items():
        say(f"    {table:<20} {n:>6}")

    # ---------------------------------------------------------------- backup
    backup = None
    if apply_ and live:
        say("")
        cur.close()
        conn.close()                   # our own connection blocks the copy
        backup = take_backup(live, say, force_disconnect, include_active)
        if backup is None:
            say("\n  REFUSED: no backup could be taken. Nothing was changed.")
            return Result(False, 0, False, "no backup", say.text)
        conn, dbname = connect(live, apply_, None)
        cur = conn.cursor()
    elif apply_:
        say("\n  no backup taken: this is not the live database, and dev is "
            "a disposable clone of it (the clone command refreshes it).")

    # ----------------------------------------------------------------- clear
    say("")
    if going:
        cur.execute('DELETE FROM "user" WHERE id <> ALL(%s)', (kept_ids,))
        say(f"  - {cur.rowcount} account(s) deleted, and everything that "
            "cascaded with them")

    for table in CONTENT:
        cur.execute(_sql.SQL("DELETE FROM {}").format(
            _sql.Identifier(table)))
        if cur.rowcount:
            say(f"  - {table}: {cur.rowcount} row(s) left by kept accounts")

    if reset_answers:
        cur.execute("DELETE FROM user_answers")
        say(f"  - user_answers: {cur.rowcount} row(s); kept accounts will "
            "be asked the questionnaire again")

    if hide_ids:
        cur.execute('UPDATE "user" SET discoverable = false '
                    "WHERE id = ANY(%s)", (hide_ids,))
        say(f"  . {cur.rowcount} kept account(s) marked unlisted")

    # ---------------------------------------------------------------- verify
    after_content = _table_counts(cur, CONTENT)
    left = {t: n for t, n in after_content.items() if n}
    if left:
        return stop(f"these still hold rows: {left}")

    after_catalogue = _table_counts(cur, CATALOGUE)
    moved = {t: (before_catalogue[t], after_catalogue[t])
             for t in CATALOGUE if before_catalogue[t] != after_catalogue[t]}
    if moved:
        return stop(f"the catalogue changed: {moved}")

    cur.execute('SELECT id, discoverable FROM "user" ORDER BY id')
    survivors = cur.fetchall()
    if sorted(r[0] for r in survivors) != sorted(kept_ids):
        return stop(f"expected {sorted(kept_ids)} to survive, found "
                    f"{sorted(r[0] for r in survivors)}")
    wrong = [uid for uid, seen in survivors if seen != (uid not in hide_ids)]
    if wrong:
        return stop(f"wrong listing for {wrong}")

    say("\n  . every content table is empty")
    say("  . the catalogue is untouched: "
        + ", ".join(f"{t} {after_catalogue[t]}" for t in CATALOGUE))
    say(f"  . {len(survivors)} account(s) remain, "
        f"{len(hide_ids)} of them unlisted")

    cleared = sum(before_content.values())
    if apply_:
        conn.commit()
        say("\n  committed.")
        if backup:
            say(f"  the backup is {backup}; drop it when you are satisfied:")
            say(f"    DROP DATABASE {backup};")
    else:
        conn.rollback()
        say("\n  rolled back -- nothing was kept.")
        say("  re-run with --apply to do it for real.")
    conn.close()
    return Result(True, cleared, apply_,
                  f"{len(going)} account(s), {cleared} content row(s)",
                  say.text)


# ---- the dev copy of live ----------------------------------------------

CLONE_STAMP = "beacons_connect dev clone of"


def _sessions_full(cur, dbname):
    cur.execute("""
        SELECT pid, usename, application_name, client_addr, state,
               date_trunc('second', now() - state_change) AS idle_for, query
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid()
        ORDER BY backend_start
    """, (dbname,))
    return cur.fetchall()


def _show_sessions(rows, say):
    for pid, usr, app_name, addr, state, idle, query in rows:
        snippet = " ".join((query or "").split())[:60]
        say(f"    pid {pid:<8} {usr:<8} {state:<22} idle {idle}  "
            f"{app_name or '-'} from {addr or 'local'}")
        if state != "idle" and snippet:
            say(f"      running: {snippet}")


def _clear_sessions(cur, dbname, include_active, dry_run, say):
    """Terminate blocking sessions. Idle ones are safe; others need asking."""
    import time

    rows = _sessions_full(cur, dbname)
    if not rows:
        say(f"  no other sessions on {dbname}")
        return True

    say(f"  {len(rows)} other session(s) on {dbname}:")
    _show_sessions(rows, say)

    doomed = [r[0] for r in rows if include_active or r[4] == "idle"]
    spared = [r for r in rows if r[0] not in doomed]
    if spared:
        say("  these are mid-transaction, so --include-active is needed "
            "for them:")
        _show_sessions(spared, say)

    if dry_run:
        say(f"  would terminate {len(doomed)} session(s)")
        return not spared

    for pid in doomed:
        cur.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))
        say(f"    terminated pid {pid}")

    # Termination is asynchronous even with the timeout argument.
    for _ in range(20):
        if not _sessions_full(cur, dbname):
            return True
        time.sleep(0.25)

    left = _sessions_full(cur, dbname)
    say(f"  {len(left)} session(s) still attached:")
    _show_sessions(left, say)
    return False


def fingerprint(url, label, say):
    """Per-table row count and content hash, plus constraints and sequences.

    The table list comes from the catalog so a table added later cannot be
    silently skipped, and count(*) is exact -- reltuples is a planner
    estimate and would differ between the two databases for legitimate
    reasons.
    """
    conn = psycopg2.connect(url, connect_timeout=8)
    cur = conn.cursor()
    out = {"tables": {}, "constraints": [], "indexes": [], "sequences": {}}

    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "ORDER BY tablename")
    names = [r[0] for r in cur.fetchall()]
    for name in names:
        cur.execute(_sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(t::text, '|' "
            "ORDER BY t::text), '')) FROM {} t"
        ).format(_sql.Identifier(name)))
        out["tables"][name] = cur.fetchone()

    cur.execute("""SELECT conrelid::regclass::text, conname,
                          pg_get_constraintdef(oid)
                     FROM pg_constraint
                    WHERE connamespace = 'public'::regnamespace
                    ORDER BY 1, 2""")
    out["constraints"] = cur.fetchall()

    cur.execute("SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' ORDER BY indexname")
    out["indexes"] = cur.fetchall()

    cur.execute("SELECT sequencename, last_value FROM pg_sequences "
                "WHERE schemaname = 'public' ORDER BY sequencename")
    out["sequences"] = dict(cur.fetchall())

    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
    out["size"] = cur.fetchone()[0]
    cur.close()
    conn.close()
    say(f"  {label}: {len(names)} tables, "
        f"{sum(v[0] for v in out['tables'].values())} rows, {out['size']}")
    return out


def _compare_fingerprints(live, dev, say):
    """Print a live-vs-dev table and return True when they agree."""
    agree = True
    say()
    say(f"    {'table':24} {'live':>7} {'dev':>7}  content")
    for name in sorted(set(live["tables"]) | set(dev["tables"])):
        lrows, ldig = live["tables"].get(name, (None, None))
        drows, ddig = dev["tables"].get(name, (None, None))
        same = lrows == drows and ldig == ddig
        agree = agree and same
        say(f"    {name:24} {str(lrows):>7} {str(drows):>7}  "
            f"{'same' if same else 'DIFFERENT'}")

    for key in ("constraints", "indexes"):
        same = live[key] == dev[key]
        agree = agree and same
        say(f"    {key:24} {len(live[key]):>7} {len(dev[key]):>7}  "
            f"{'same' if same else 'DIFFERENT'}")

    # The check that actually proves new inserts will not collide.
    behind = [s for s, v in dev["sequences"].items()
              if live["sequences"].get(s) != v]
    agree = agree and not behind
    say(f"    {'sequences':24} {len(live['sequences']):>7} "
        f"{len(dev['sequences']):>7}  {'same' if not behind else behind}")
    return agree


def clone(apply_=False, refresh=False, force_disconnect=False,
          include_active=False, echo=True):
    """Make (or refresh) a development copy of the live database.

    Runs `CREATE DATABASE <live>_dev TEMPLATE <live>`, a server-side file
    copy: data, indexes, every constraint, owners, grants, and -- the part a
    hand-rolled copy always gets wrong -- sequence state, so the first
    insert in the copy does not collide on a primary key.

    Reads LIVE_DATABASE_URL and never DATABASE_URL, because DATABASE_URL
    points at the dev copy; deriving live from it would make this treat the
    copy as its own template.

    This is one of the two commands that cannot run from the admin panel
    against the database the app is using: Postgres refuses a template copy
    while anything is connected to the source, and the web app is itself a
    connection. A request handler cannot close the pool it is running in.
    """
    from urllib.parse import urlunsplit
    from datetime import datetime, timezone

    say = Say(echo)
    url = url_for(True)
    live_db = urlsplit(url).path.lstrip("/")
    dev_db = live_db + "_dev"
    host = urlsplit(url).hostname

    conn = psycopg2.connect(_maintenance_url(url), connect_timeout=8)
    conn.autocommit = True      # CREATE DATABASE cannot run in a transaction
    cur = conn.cursor()

    cur.execute("SELECT current_database(), "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user),"
                "(SELECT rolcreatedb FROM pg_roles "
                " WHERE rolname = current_user)")
    here, is_super, can_create = cur.fetchone()

    say(f"  server      {host}")
    say(f"  working on  {here} (superuser={is_super}, createdb={can_create})")
    say(f"  copying     {live_db} -> {dev_db}")
    if not apply_:
        say("  dry run: nothing will be changed. Pass --apply to do it.")

    def stop(message):
        conn.close()
        say(f"\n  REFUSED: {message}")
        return Result(False, 0, False, message, say.text)

    if here == live_db:
        return stop(f"connected to {here!r}. This works from the maintenance "
                    "database so it is not its own blocker.")
    if not (is_super or can_create):
        return stop("this role cannot create databases")

    say()
    live_before = fingerprint(url, f"{live_db} (source)", say)

    cur.execute("SELECT shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = %s", (dev_db,))
    row = cur.fetchone()
    if row is not None:
        note = row[0] or ""
        if not refresh:
            say()
            say(f"  {dev_db} already exists; pass --refresh to replace it")
            say(f"  it says: {note or '(no stamp)'}")
            conn.close()
            return Result(True, 0, False, "already exists", say.text)
        if not note.startswith(CLONE_STAMP):
            return stop(f"{dev_db} exists but was not created by this tool "
                        f"(stamp: {note or 'none'}). Drop it by hand if you "
                        "are sure.")
        say()
        say(f"  replacing the existing {dev_db}")
        if apply_:
            # Sessions on the dev copy are ours by definition, so clearing
            # them needs no permission -- but still say what was closed.
            _clear_sessions(cur, dev_db, True, False, say)
            # The only DROP DATABASE here. Both conditions, so even a
            # misparsed live_db cannot get a non-_dev database dropped;
            # there is deliberately no argument for a database name.
            if dev_db == live_db or not dev_db.endswith("_dev"):
                return stop(f"{dev_db!r} is not a *_dev database")
            cur.execute(_sql.SQL("DROP DATABASE IF EXISTS {}").format(
                _sql.Identifier(dev_db)))
            say(f"    dropped {dev_db}")
        else:
            say(f"    would drop {dev_db}")

    say()
    if force_disconnect:
        clear = _clear_sessions(cur, live_db, include_active, not apply_, say)
    else:
        rows = _sessions_full(cur, live_db)
        clear = not rows
        if rows:
            say(f"  {len(rows)} other session(s) on {live_db}:")
            _show_sessions(rows, say)
            say("  the copy cannot run while anything else is attached.")
            say("  stop the app, disconnect pgAdmin, then re-run --")
            say("  or pass --force-disconnect to close the idle ones above.")
        else:
            say(f"  no other sessions on {live_db}")

    if not clear:
        return stop("something is still attached to the source.")

    if not apply_:
        say()
        say(f"  would run: CREATE DATABASE {dev_db} TEMPLATE {live_db}")
        conn.close()
        return Result(True, 1, False, "would clone", say.text)

    stamp = (f"{CLONE_STAMP} {live_db}; created "
             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    cur.execute(_sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
        _sql.Identifier(dev_db), _sql.Identifier(live_db)))
    cur.execute(_sql.SQL("COMMENT ON DATABASE {} IS %s").format(
        _sql.Identifier(dev_db)), (stamp,))
    say(f"  created {dev_db}")

    dev_url = urlunsplit(urlsplit(url)._replace(path="/" + dev_db))
    say()
    dev_after = fingerprint(dev_url, f"{dev_db} (copy)", say)
    live_after = fingerprint(url, f"{live_db} (source, re-read)", say)

    faithful = _compare_fingerprints(live_after, dev_after, say)
    untouched = live_before["tables"] == live_after["tables"]

    say()
    say(f"  copy matches the source : {'yes' if faithful else 'NO'}")
    say(f"  source left untouched   : {'yes' if untouched else 'NO'}")
    say()
    say("  point development at it with this in .env:")
    # From the '@' on: the user and password are deliberately not printed.
    say(f"    DATABASE_URL=...{dev_url[dev_url.index('@'):]}")

    cur.close()
    conn.close()
    ok = faithful and untouched
    return Result(ok, 1, True,
                  "cloned" if ok else "cloned but the copy does not match",
                  say.text)


# ------------------------------------------------------- the catalogue
#
# The questionnaire, the hubs and the activity list: what db/seed_questions.py,
# db/seed_clubs.py and db/seed_activities.py held. Data only, read out of
# those files rather than retyped, because a mistyped hub tag names an option
# that does not exist and the hub can then never be suggested to anybody.
#
# QUESTIONS   one dict per question; `options` is in the order shown.
# CLUBS       (name, description, [(question key, option label), ...])
# ACTIVITIES  (name, online, in_person, [venue, ...])
# PRESETS     the activities the pickers offer; the rest are write-ins.

QUESTIONS = [{'key': 'degree_type',
  'prompt': "What's your degree type?",
  'allows_multiple': False,
  'weight': 0.5,
  'options': ["Bachelor's",
              "Master's",
              'Doctorate',
              'Graduate Certificate',
              'Undergraduate Certificate']},
 {'key': 'major',
  'prompt': "What's your major?",
  'allows_multiple': False,
  'weight': 3,
  'options': ['Accounting',
              'Accounting with Data Analytics',
              'Africana Studies',
              'Aging Studies',
              'American Studies',
              'Anthropology',
              'Applied Behavior Analysis for Special Populations',
              'Applied Economics',
              'Applied Linguistics',
              'Applied Physics',
              'Applied Sociology',
              'Art',
              'Asian Studies',
              'Assistive Technology for Individuals with Visual '
              'Impairments',
              'Autism Endorsement',
              'Biochemistry',
              'Biology',
              'Biomedical Engineering and Biotechnology',
              'Biotechnology Professional Science',
              'Biotechnology and Biomedical Sciences',
              'Business Administration',
              'Business Analytics',
              'Chemistry',
              'Classical Languages',
              'Classical Studies',
              'Clean Energy and Sustainability',
              'Clinical Psychology',
              'Communications',
              'Community Development',
              'Computational Sciences',
              'Computer Engineering',
              'Computer Science',
              'Conflict Resolution',
              'Contemporary Marketing',
              'Cortical/Cerebral Visual Impairment',
              'Counseling Psychology',
              'Creative Writing',
              'Criminology and Criminal Justice',
              'Critical Ethnic and Community Studies',
              'Critical and Creative Thinking',
              'Cybersecurity',
              'Deafblind',
              'Developmental and Brain Sciences',
              'Doctor of Nursing Practice',
              'Dual Language',
              'Early Childhood Education and Care',
              'Early Education and Care in Inclusive Settings',
              'Economics',
              'Education - Early Education',
              'Education - Elementary',
              'Education - Middle and Secondary',
              'Educational Leadership for Social Justice',
              'Electrical Engineering',
              'Engineering Physics',
              'English',
              'English Language Development',
              'Entrepreneurship',
              'Environmental Biology',
              'Environmental Sciences',
              'Environmental Studies and Sustainability',
              'Exercise and Health Sciences',
              'Finance',
              'French',
              'Gender, Leadership, and Public Policy',
              'Gerontology',
              'Gerontology Research and Policy',
              'Gerontology: Frank J. Manning',
              'Gerontology: Management of Aging Services',
              'Global Governance and Human Security',
              'Global Inclusion and Social Development',
              'Higher Education',
              'Historical Archaeology',
              'History',
              'Human Rights',
              'Human Services',
              'Information Systems and Business Analytics',
              'Information Technology',
              'Instructional Design',
              'Instructional Technology Design',
              'Instructional and Learning Design',
              'Integrative Biosciences',
              'Interdisciplinary Business',
              'International Management',
              'International Relations',
              'Investment Management and Quantitative Finance',
              'Italian',
              'K-12 Instructional Technology',
              'Labor Studies',
              'Latin American and Iberian Studies',
              'Latin and Classical Humanities',
              'Learning, Teaching, and Educational Transformation',
              'Management and Leadership',
              'Marine Science and Technology',
              'Marketing',
              'Mathematics',
              'Mental Health Counseling',
              'Music',
              'Nurse Educator',
              'Nursing',
              'Philosophy',
              'Philosophy, Law, and Ethics',
              'Physics',
              'Political Science',
              'Psychology',
              'Public Administration',
              'Public History',
              'Public Policy',
              'Quantum Information',
              'Rehabilitation Counseling',
              'School Psychology',
              'Sociology',
              'Spanish-English Translation',
              'Special Education',
              'Sport Business',
              'Sport Leadership and Administration',
              'Supply Chain Management',
              'Sustainable Marine Aquaculture',
              'Theatre Arts',
              'Transition Leadership',
              'Urban Education Leadership and Policy Studies',
              'Urban Planning and Community Development',
              'Urban Public Health',
              'Vision Rehabilitation Therapy',
              'Vision Studies',
              'Vision Studies: Orientation and Mobility',
              'Women’s Gender and Sexuality Studies']},
 {'key': 'minor',
  'prompt': "What's your minor? (if applicable)",
  'allows_multiple': False,
  'weight': 1.5,
  'options': ['Accounting',
              'Africana Studies',
              'Aging Studies',
              'American Studies',
              'Anthropology',
              'Arabic',
              'Art History',
              'Asian American Studies',
              'Biology',
              'Business Analytics',
              'Chemistry',
              'Chinese',
              'Cinema Studies',
              'Classical Languages',
              'Classical Studies',
              'Clean Energy',
              'Cognitive Science',
              'Communication',
              'Community Development',
              'Computer Science',
              'Creative Writing',
              'Criminology and Criminal Justice',
              'Cross-Cultural East Asian Studies',
              'Dance',
              'East Asian Languages',
              'Economics',
              'Education Studies',
              'English',
              'Entrepreneurship',
              'Environmental Anthropology',
              'Environmental Biology',
              'Environmental Chemistry',
              'Environmental Science',
              'Finance',
              'Geospatial Analysis and Modeling',
              'German Studies',
              'History',
              'Human Rights',
              'Information Systems',
              'International Management',
              'International Relations',
              'Italian Studies',
              'Japanese',
              'Labor Studies',
              'Latin American Studies',
              'Latino Studies',
              'Literary History',
              'Management and Leadership',
              'Marketing',
              'Mathematics',
              'Music',
              'Native American and Indigenous Studies',
              'Philosophy',
              'Philosophy and Law',
              'Physics',
              'Political Science',
              'Portuguese Studies',
              'Pre-Medical and Allied Health Program',
              'Professional and New Media Writing',
              'Psychology',
              'Public Policy',
              'Queer and Trans Studies',
              'Race, Ethnicity, and Literature',
              'Religious Studies',
              'Science, Medicine, and Society: Past and Present',
              'Sociology',
              'South Asian Studies',
              'Spanish Language',
              'Sport Business',
              'Studio Art',
              'Supply Chain Management',
              'Theatre Arts',
              'Women’s, Gender, and Sexuality Studies']},
 {'key': 'grad_year',
  'prompt': 'When do you expect to graduate?',
  'allows_multiple': False,
  'weight': 2,
  'options': ['2026', '2027', '2028', '2029', '2030', '2031', 'Later']},
 {'key': 'activity',
  'prompt': "What's your favorite kind of activity?",
  'allows_multiple': True,
  'weight': 2.5,
  'options': ['Reading',
              'Running/Walking',
              'Movies',
              'Sports',
              'Board Games',
              'Video Games',
              'Museums/Galleries']},
 {'key': 'boston',
  'prompt': "What's your favorite Boston activity?",
  'allows_multiple': True,
  'weight': 2,
  'options': ['Museum',
              'Art gallery',
              'Coffee shop',
              'Sporting event',
              'Gardens/park',
              'Walk around the city',
              'Finding the best restaurant',
              'Shopping',
              'Movies',
              'Bowling']},
 {'key': 'media',
  'prompt': "What's your favorite type of media?",
  'allows_multiple': True,
  'weight': 1.5,
  'options': ['Books', 'Social media', 'Movies', 'TV shows', 'Manga']},
 {'key': 'house',
  'prompt': 'Which Hogwarts house are you?',
  'allows_multiple': False,
  'weight': 0.5,
  'options': ['Hufflepuff', 'Ravenclaw', 'Slytherin', 'Gryffindor']},
 {'key': 'rainy_day',
  'prompt': 'On a rainy day, what are you doing?',
  'allows_multiple': False,
  'weight': 1,
  'options': ['Catching up on schoolwork',
              "Going out to do something (can't stay cooped up)",
              'Watching movies or playing games',
              'This weather makes me want to stay at home curled up with '
              'a good book']},
 {'key': 'friday_night',
  'prompt': "What's your ideal Friday night?",
  'allows_multiple': True,
  'weight': 2,
  'options': ['Hanging out with my friends',
              'Walking around the city',
              'Trying the foods around Boston',
              'Staying in and reading/crafting/cooking/baking',
              'Watching my favorite movies/TV shows',
              'Finding a place to play sports',
              'Catching up on my workouts',
              'Gaming',
              'Finding a place to party!',
              'Learning a new skill (coding/language)']},
 {'key': 'study_style',
  'prompt': 'You have a big test coming up. How are you going to study '
            'for it?',
  'allows_multiple': False,
  'weight': 1,
  'options': ['I study alone',
              "I'm forming a study group with my classmates",
              'Asking for help from the prof/TA',
              "I don't study (I'm sure it will all turn out fine)"]}]

CLUBS = [('Basketball',
  'Pick-up games, league nights and anyone still learning to shoot.',
  [('activity', 'Sports'),
   ('boston', 'Sporting event'),
   ('friday_night', 'Finding a place to play sports')]),
 ('Tennis',
  'Courts, doubles partners and a ladder for anyone who wants one.',
  [('activity', 'Sports'),
   ('friday_night', 'Finding a place to play sports'),
   ('major', 'Exercise and Health Sciences')]),
 ('Pickle Ball',
  'The fastest way to make friends on campus. Paddles provided.',
  [('activity', 'Sports'),
   ('boston', 'Sporting event'),
   ('friday_night', 'Finding a place to play sports')]),
 ('Kickball',
  'Recess, except everyone is an adult and nobody has grown out of it.',
  [('activity', 'Sports'),
   ('friday_night', 'Hanging out with my friends'),
   ('boston', 'Gardens/park')]),
 ('Volleyball',
  'Indoor and sand, from first-timers to people who call the lines.',
  [('activity', 'Sports'),
   ('friday_night', 'Finding a place to play sports'),
   ('boston', 'Sporting event')]),
 ('Football',
  'Flag games on the quad and a group that watches Sundays together.',
  [('activity', 'Sports'),
   ('boston', 'Sporting event'),
   ('friday_night', 'Hanging out with my friends')]),
 ('Frisbee',
  'Ultimate, disc golf and throwing one around when the weather allows.',
  [('activity', 'Sports'),
   ('boston', 'Gardens/park'),
   ('activity', 'Running/Walking')]),
 ('Badminton',
  'Shuttlecocks, singles and doubles, every level welcome.',
  [('activity', 'Sports'),
   ('friday_night', 'Finding a place to play sports'),
   ('major', 'Exercise and Health Sciences')]),
 ('Ice Skating',
  'Rink trips, lessons for the unsteady, and hot chocolate afterwards.',
  [('activity', 'Sports'),
   ('boston', 'Walk around the city'),
   ('friday_night', 'Hanging out with my friends')]),
 ('Running Around',
  'Group runs along the harbour at a pace that suits whoever turns up.',
  [('activity', 'Running/Walking'),
   ('friday_night', 'Catching up on my workouts'),
   ('major', 'Exercise and Health Sciences')]),
 ('Walking the City',
  'Long walks through Boston with no particular destination.',
  [('activity', 'Running/Walking'),
   ('boston', 'Walk around the city'),
   ('friday_night', 'Walking around the city')]),
 ('Card Games',
  'Poker nights, trick-taking and whatever somebody brings along.',
  [('activity', 'Board Games'),
   ('friday_night', 'Hanging out with my friends'),
   ('rainy_day', 'Watching movies or playing games')]),
 ('Chess',
  'Casual boards, a ladder, and people happy to teach the openings.',
  [('activity', 'Board Games'),
   ('rainy_day', 'Watching movies or playing games'),
   ('major', 'Mathematics')]),
 ('Video Games',
  'Couch co-op, tournaments and a server that is always up.',
  [('activity', 'Video Games'),
   ('friday_night', 'Gaming'),
   ('rainy_day', 'Watching movies or playing games')]),
 ('Pokemon',
  'Trading, battling and the long walk to catch something rare.',
  [('activity', 'Video Games'),
   ('friday_night', 'Gaming'),
   ('media', 'Manga')]),
 ('Minecraft',
  'One shared world, built slowly by whoever is online.',
  [('activity', 'Video Games'),
   ('friday_night', 'Gaming'),
   ('rainy_day', 'Watching movies or playing games')]),
 ('Books',
  'One book a month, argued about properly over coffee.',
  [('activity', 'Reading'),
   ('media', 'Books'),
   ('rainy_day',
    'This weather makes me want to stay at home curled up with a good '
    'book'),
   ('major', 'English')]),
 ('Writing',
  'Workshops, prompts and honest feedback on whatever you are working '
  'on.',
  [('activity', 'Reading'),
   ('media', 'Books'),
   ('friday_night', 'Staying in and reading/crafting/cooking/baking'),
   ('major', 'English')]),
 ('Anime',
  'Weekly screenings, seasonal picks and a manga shelf.',
  [('media', 'Manga'),
   ('media', 'TV shows'),
   ('rainy_day', 'Watching movies or playing games')]),
 ('Crafting',
  'Knitting, printing, stickers, and whatever else is on the table.',
  [('friday_night', 'Staying in and reading/crafting/cooking/baking'),
   ('activity', 'Museums/Galleries'),
   ('major', 'Art')]),
 ('Fashion',
  'Thrift runs, fittings and swapping what no longer fits.',
  [('boston', 'Shopping'),
   ('media', 'Social media'),
   ('friday_night', 'Walking around the city')]),
 ('Music',
  'Players, singers, producers and people who just want to listen '
  'loudly.',
  [('friday_night', 'Hanging out with my friends'),
   ('activity', 'Museums/Galleries'),
   ('major', 'Music')]),
 ('Theatre',
  'Productions, readings and cheap seats to everything in town.',
  [('activity', 'Museums/Galleries'),
   ('boston', 'Art gallery'),
   ('major', 'Theatre Arts')]),
 ('Movies',
  'A film a week, picked by whoever shouts loudest, plus the argument '
  'after.',
  [('activity', 'Movies'),
   ('media', 'Movies'),
   ('boston', 'Movies'),
   ('friday_night', 'Watching my favorite movies/TV shows')]),
 ('TV Shows',
  'Watch-alongs, recaps and the slow communal suffering of a weekly '
  'release.',
  [('media', 'TV shows'),
   ('friday_night', 'Watching my favorite movies/TV shows'),
   ('rainy_day', 'Watching movies or playing games')]),
 ('History',
  'Talks, archives and walking tours of a city that is full of it.',
  [('activity', 'Museums/Galleries'),
   ('boston', 'Museum'),
   ('major', 'History')]),
 ('Cooking',
  'Shared meals, cheap recipes and cooking for more people than planned.',
  [('friday_night', 'Staying in and reading/crafting/cooking/baking'),
   ('boston', 'Finding the best restaurant'),
   ('major', 'Exercise and Health Sciences')]),
 ('Baking',
  'Bread, cakes and the arguments that come with both.',
  [('friday_night', 'Staying in and reading/crafting/cooking/baking'),
   ('boston', 'Coffee shop'),
   ('major', 'Exercise and Health Sciences')]),
 ('Coffee',
  'A different cafe every week and strong opinions about all of them.',
  [('boston', 'Coffee shop'),
   ('activity', 'Reading'),
   ('friday_night', 'Walking around the city')]),
 ('Food Trucks',
  'Chasing the good ones around the city and reporting back.',
  [('boston', 'Finding the best restaurant'),
   ('friday_night', 'Trying the foods around Boston'),
   ('activity', 'Running/Walking')]),
 ('Eating Around Boston',
  'Working through the city one neighbourhood at a time.',
  [('boston', 'Finding the best restaurant'),
   ('friday_night', 'Trying the foods around Boston'),
   ('boston', 'Walk around the city')]),
 ('Coding/Computers',
  'Project nights, interview prep and help with whatever will not '
  'compile.',
  [('friday_night', 'Learning a new skill (coding/language)'),
   ('activity', 'Video Games'),
   ('major', 'Computer Science'),
   ('major', 'Information Technology')]),
 ('Auto',
  'Maintenance in the lot, car meets, and keeping an old one running.',
  [('friday_night', 'Learning a new skill (coding/language)'),
   ('activity', 'Running/Walking'),
   ('major', 'Electrical Engineering')]),
 ('Accountability Groups',
  'Small groups that meet weekly so the work actually gets done.',
  [('study_style', "I'm forming a study group with my classmates"),
   ('rainy_day', 'Catching up on schoolwork'),
   ('friday_night', 'Learning a new skill (coding/language)')])]

ACTIVITIES = [('Basketball', False, True, ['Indoor Court', 'Outdoor Court']),
 ('Soccer', False, True, ['Soccer Field']),
 ('Softball', False, True, ['Softball Field']),
 ('Volleyball', False, True, ['Beach Court', 'Gym']),
 ('Tennis', False, True, ['Tennis Courts']),
 ('Table tennis', False, True, ['Game Room']),
 ('Pool', False, True, ['Dorm Pool Table', 'Game Room']),
 ('Dungeons & Dragons', False, True, ['Gaming Lounge']),
 ('Board games', False, True, ['Gaming Lounge', 'Campus Center']),
 ('Cards', False, True, ['Campus Center', 'Gaming Lounge']),
 ('Spikeball', False, True, ['Harborwalk Lawn']),
 ('Frisbee', False, True, ['Harborwalk Lawn', 'Soccer Field']),
 ('Running', False, True, ['Harborwalk']),
 ('Chess', True, True, ['Gaming Lounge', 'Campus Center']),
 ('Tic-Tac-Toe', True, False, []),
 ('Connect Four', True, False, []),
 ('Battleship', True, False, [])]

ACTIVITY_PRESETS = ['Basketball',
 'Board games',
 'Chess',
 'Running',
 'Soccer',
 'Table tennis',
 'Tennis',
 'Volleyball']


# ---- the demo population ------------------------------------------
#
# What db/seed_demo.py held. The names are paired and shuffled with a
# fixed seed, so the same 38 accounts come out every time -- which is
# what makes the suites able to rely on them.
#
# Each cluster is a group of students who should look like each other to
# the matcher: the option labels here must exist in QUESTIONS, and the
# seeder refuses rather than skips if one does not.

DEMO_FIRST = ['Amara',
 'Diego',
 'Priya',
 'Liam',
 'Sofia',
 'Malik',
 'Chen',
 'Ava',
 'Omar',
 'Fatima',
 'Noah',
 'Leila',
 'Andre',
 'Mei',
 'Jonas',
 'Rosa',
 'Kofi',
 'Hana',
 'Tomas',
 'Zara',
 'Ethan',
 'Nadia',
 'Ravi',
 'Elena',
 'Marcus',
 'Yara',
 'Felix',
 'Aisha',
 'Danny',
 'Ingrid',
 'Samir',
 'Clara']

DEMO_LAST = ['Okafor',
 'Ramirez',
 'Shah',
 'Byrne',
 'Costa',
 'Hassan',
 'Wu',
 'Sullivan',
 'Haddad',
 'Rahman',
 'Bergman',
 'Nasser',
 'Laurent',
 'Tanaka',
 'Weiss',
 'Delgado',
 'Mensah',
 'Kim',
 'Silva',
 'Ahmed',
 'Doyle',
 'Petrov',
 'Nair',
 'Moreau',
 'Osei',
 'Haddadi',
 'Lindqvist',
 'Diallo',
 'Walsh',
 'Larsen',
 'Aziz',
 'Novak']

DEMO_CLUSTERS = [{'name': 'CS / tech',
  'size': 8,
  'majors': ['Computer Science',
             'Information Technology',
             'Computer Engineering',
             'Information Systems and Business Analytics'],
  'minors': ['Mathematics', 'Business Analytics', 'Cognitive Science'],
  'activity': ['Video Games', 'Movies', 'Board Games'],
  'boston': ['Coffee shop', 'Movies', 'Bowling'],
  'media': ['Manga', 'TV shows', 'Social media'],
  'rainy': ['Watching movies or playing games',
            'Catching up on schoolwork'],
  'friday': ['Gaming',
             'Learning a new skill (coding/language)',
             'Staying in and reading/crafting/cooking/baking'],
  'study': ['I study alone',
            "I'm forming a study group with my classmates"]},
 {'name': 'arts / humanities',
  'size': 7,
  'majors': ['English',
             'Art',
             'Music',
             'Theatre Arts',
             'Communications',
             'History'],
  'minors': ['Creative Writing',
             'Art History',
             'Cinema Studies',
             'Philosophy'],
  'activity': ['Reading', 'Museums/Galleries', 'Movies'],
  'boston': ['Art gallery',
             'Museum',
             'Coffee shop',
             'Walk around the city'],
  'media': ['Books', 'Movies', 'Manga'],
  'rainy': ['Watching movies or playing games',
            'This weather makes me want to stay at home curled up with a '
            'good book'],
  'friday': ['Staying in and reading/crafting/cooking/baking',
             'Walking around the city'],
  'study': ['I study alone', 'Asking for help from the prof/TA']},
 {'name': 'sports / health',
  'size': 7,
  'majors': ['Exercise and Health Sciences',
             'Nursing',
             'Biology',
             'Urban Public Health'],
  'minors': ['Psychology', 'Sport Business', 'Biology'],
  'activity': ['Sports', 'Running/Walking'],
  'boston': ['Sporting event',
             'Gardens/park',
             'Finding the best restaurant'],
  'media': ['Social media', 'TV shows'],
  'rainy': ["Going out to do something (can't stay cooped up)",
            'Catching up on schoolwork'],
  'friday': ['Finding a place to play sports',
             'Catching up on my workouts',
             'Finding a place to party!'],
  'study': ["I'm forming a study group with my classmates",
            'I study alone']},
 {'name': 'business',
  'size': 5,
  'majors': ['Accounting',
             'Finance',
             'Marketing',
             'Management and Leadership',
             'Entrepreneurship'],
  'minors': ['Economics', 'Marketing', 'International Management'],
  'activity': ['Movies', 'Running/Walking', 'Board Games'],
  'boston': ['Shopping', 'Coffee shop', 'Finding the best restaurant'],
  'media': ['Social media', 'TV shows', 'Books'],
  'rainy': ['Catching up on schoolwork',
            'Watching movies or playing games'],
  'friday': ['Finding a place to party!',
             'Trying the foods around Boston',
             'Hanging out with my friends'],
  'study': ["I'm forming a study group with my classmates",
            'I study alone']},
 {'name': 'science / environment',
  'size': 5,
  'majors': ['Chemistry',
             'Biochemistry',
             'Environmental Studies and Sustainability',
             'Physics',
             'Environmental Sciences'],
  'minors': ['Environmental Biology', 'Mathematics', 'Chemistry'],
  'activity': ['Running/Walking', 'Board Games', 'Reading'],
  'boston': ['Gardens/park', 'Museum', 'Walk around the city'],
  'media': ['Books', 'TV shows'],
  'rainy': ['Catching up on schoolwork',
            'Watching movies or playing games'],
  'friday': ['Walking around the city',
             'Staying in and reading/crafting/cooking/baking'],
  'study': ['I study alone',
            "I'm forming a study group with my classmates"]}]
