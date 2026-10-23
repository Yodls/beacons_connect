"""One tool for every database job in this project.

`db/` held 36 separate scripts, each run by hand with its own flags and its
own idea of what was dangerous -- `--apply` to opt into danger in most,
`--dry-run` to opt into safety in one, `--i-mean-live` in another. This is
the command line over db_ops.py, and the admin panel calls the same
functions, so the two cannot drift.

Two flags, everywhere:

    --apply     keep the changes. Without it everything is rolled back.
    --live      target LIVE_DATABASE_URL instead of DATABASE_URL.

So writing to production takes two deliberate words, and the default of
every command is the harmless one. A dry run still executes the work and
then throws the transaction away, which proves the SQL runs rather than only
that the tool believes it would.

THE COMMANDS

    python dbtool.py status                     are dev and live the same?

    python dbtool.py migrate --list             every migration, with state
    python dbtool.py migrate --pending          just what is outstanding
    python dbtool.py migrate <name>             run one
    python dbtool.py migrate --all              run every pending one, in order

    python dbtool.py seed --list                the seeders
    python dbtool.py seed questions             the questionnaire
    python dbtool.py seed clubs                 the hubs and their tags
    python dbtool.py seed activities            the activity catalogue
    python dbtool.py seed demo                  32 students who cluster
    python dbtool.py seed test-account          an account you can log into
    python dbtool.py seed --clear demo          remove the demo accounts

    python dbtool.py admin --list               who holds admin
    python dbtool.py admin --email a@umb.edu    grant it
    python dbtool.py admin --email a@umb.edu --revoke

    python dbtool.py reset-catalogue            replace questions and hubs
    python dbtool.py clear-dead-invites         unanswerable game invites
    python dbtool.py delete-account --email a@umb.edu

    python dbtool.py wipe                       clear a database for real students
    python dbtool.py clone                      the dev copy of live

EXAMPLES

    python dbtool.py status
    python dbtool.py migrate --pending --live
    python dbtool.py migrate --all --live --apply
    python dbtool.py seed demo --apply
    python dbtool.py wipe --keep me@umb.edu --apply --live
    python dbtool.py clone --refresh --force-disconnect --apply

Nothing here prints a credential. The only things said out loud about a
connection are its host and its database name: never the URL, never the
user, never the password.
"""
import argparse
import sys

import db_ops


def build_parser():
    ap = argparse.ArgumentParser(
        prog="dbtool",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = ap.add_subparsers(dest="command", metavar="<command>")

    def with_gates(parser):
        parser.add_argument("--apply", action="store_true",
                            help="keep the changes (default: roll back)")
        parser.add_argument("--live", action="store_true",
                            help="target LIVE_DATABASE_URL")
        return parser

    subs.add_parser("status", help="are dev and live the same shape?",
                    description="Compares the structure of live and dev. "
                                "Read-only in both directions.")

    migrate = with_gates(subs.add_parser(
        "migrate", help="run or list the schema migrations"))
    migrate.add_argument("name", nargs="?", help="the migration to run")
    migrate.add_argument("--list", action="store_true",
                         help="every migration, applied or pending")
    migrate.add_argument("--pending", action="store_true",
                         help="just what is outstanding")
    migrate.add_argument("--all", action="store_true",
                         help="run every pending migration, in order")

    seed = with_gates(subs.add_parser("seed", help="fill a database"))
    seed.add_argument("what", nargs="?", help="what to seed")
    seed.add_argument("--list", action="store_true", help="the seeders")
    seed.add_argument("--clear", action="store_true",
                      help="remove instead of add (demo only)")
    seed.add_argument("--reset", action="store_true",
                      help="test-account: delete and rebuild it")
    seed.add_argument("--friend", help="test-account: who to befriend")
    seed.add_argument("--password", help="test-account: its password")

    admin = with_gates(subs.add_parser("admin", help="who may review hubs"))
    admin.add_argument("--list", action="store_true", help="who holds it now")
    admin.add_argument("--email", help="the account to grant or revoke")
    admin.add_argument("--revoke", action="store_true",
                       help="take it away instead of granting it")

    with_gates(subs.add_parser(
        "reset-catalogue",
        help="replace the questionnaire and the hubs",
        description="Removes what is no longer in the data and re-seeds. "
                    "Every answer goes, so everybody is asked again."))

    with_gates(subs.add_parser(
        "clear-dead-invites", help="remove unanswerable game invites"))

    delete = with_gates(subs.add_parser(
        "delete-account", help="delete one account and everything attached"))
    delete.add_argument("--email", help="the account to delete")

    wipe = with_gates(subs.add_parser(
        "wipe", help="clear a database of everything students made"))
    wipe.add_argument("--keep", action="append", metavar="EMAIL",
                      help="an account to spare (repeatable)")
    wipe.add_argument("--hide", action="append", metavar="EMAIL",
                      help="a kept account to mark unlisted (repeatable)")
    wipe.add_argument("--reset-answers", action="store_true",
                      help="also clear the kept accounts' answers")
    wipe.add_argument("--force-disconnect", action="store_true",
                      help="end idle sessions blocking the backup")
    wipe.add_argument("--include-active", action="store_true",
                      help="...and ones mid-transaction")

    # No --live: this is inherently live -> live_dev, and reads
    # LIVE_DATABASE_URL directly. DATABASE_URL points at the copy, so
    # deriving the source from it would make the copy its own template.
    clone = subs.add_parser("clone", help="make or refresh the dev copy")
    clone.add_argument("--apply", action="store_true",
                       help="actually do it (default: say what would happen)")
    clone.add_argument("--refresh", action="store_true",
                       help="replace an existing dev copy")
    clone.add_argument("--force-disconnect", action="store_true",
                       help="terminate idle sessions blocking the copy")
    clone.add_argument("--include-active", action="store_true",
                       help="...and ones mid-transaction")
    return ap


def do_migrate(args):
    if args.pending:
        rows, _ = db_ops.migration_status(args.live, echo=False)
        waiting = [(n, s) for n, s, done in rows if not done]
        print()
        for name, summary in waiting:
            print(f"  PENDING  {name:24} {summary}")
        print()
        print(f"  {len(waiting)} pending of {len(rows)}."
              if waiting else f"  nothing pending; all {len(rows)} applied.")
        print()
        return 0
    if args.list:
        db_ops.migration_status(args.live)
        return 0
    if args.all:
        return 0 if db_ops.run_pending(args.live, args.apply).ok else 1
    if not args.name:
        print("  Nothing to do: pass a migration name, --list or --all.")
        return 2
    return 0 if db_ops.run_migration(args.name, args.live, args.apply).ok else 1


def do_seed(args):
    if args.list:
        print()
        for name, (summary, _) in db_ops.SEEDERS.items():
            print(f"  {name:14} {summary}")
        print(f"  {'demo':14} 32 students who cluster, for the matcher")
        print(f"  {'test-account':14} an account you can log into, with a "
              "friend, a hub and a room")
        print()
        return 0

    what = (args.what or "").replace("_", "-")
    if not what:
        print("  Nothing to do: pass what to seed, or --list.")
        return 2

    if args.clear:
        if what != "demo":
            print(f"  --clear only applies to demo, not {what!r}.")
            return 2
        result = db_ops.run_op("clear demo", db_ops.clear_demo_work,
                               args.live, args.apply)
    elif what in db_ops.SEEDERS:
        summary, work = db_ops.SEEDERS[what]
        result = db_ops.run_op(f"seed {what}: {summary}", work,
                               args.live, args.apply)
    elif what == "demo":
        result = db_ops.run_op("seed demo", db_ops.seed_demo_work,
                               args.live, args.apply)
    elif what == "test-account":
        result = db_ops.run_op(
            "seed test-account", db_ops.seed_test_account_work,
            args.live, args.apply, friend=args.friend,
            password=args.password, reset=args.reset)
    else:
        print(f"  No seeder called {what!r}. Run `seed --list`.")
        return 2
    return 0 if result.ok else 1


def do_admin(args):
    if args.list or not args.email:
        if not args.email and not args.list:
            print("  Nothing to do: pass --email, or --list.")
            return 2
        return 0 if db_ops.admin_list(args.live).ok else 1
    result = db_ops.run_op("admin", db_ops.admin_set_work,
                           args.live, args.apply,
                           email=args.email, revoke=args.revoke)
    return 0 if result.ok else 1


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return 0

    try:
        if args.command == "status":
            return 0 if db_ops.status().ok else 1
        if args.command == "migrate":
            return do_migrate(args)
        if args.command == "seed":
            return do_seed(args)
        if args.command == "admin":
            return do_admin(args)
        if args.command == "reset-catalogue":
            return 0 if db_ops.run_op(
                "reset catalogue", db_ops.reset_catalogue_work,
                args.live, args.apply).ok else 1
        if args.command == "clear-dead-invites":
            return 0 if db_ops.run_op(
                "clear dead invites", db_ops.clear_dead_invites_work,
                args.live, args.apply).ok else 1
        if args.command == "delete-account":
            if not args.email:
                print("  Nothing to do: pass --email.")
                return 2
            return 0 if db_ops.run_op(
                "delete account", db_ops.delete_account_work,
                args.live, args.apply, email=args.email).ok else 1
        if args.command == "wipe":
            return 0 if db_ops.wipe(
                live=args.live, apply_=args.apply, keep=args.keep,
                hide=args.hide, reset_answers=args.reset_answers,
                force_disconnect=args.force_disconnect,
                include_active=args.include_active).ok else 1
        if args.command == "clone":
            return 0 if db_ops.clone(
                apply_=args.apply, refresh=args.refresh,
                force_disconnect=args.force_disconnect,
                include_active=args.include_active).ok else 1
    except db_ops.Refuse as exc:
        # A refusal is not a crash: it is a safety check saying the database
        # is not in the state the command expected. Nothing was kept.
        print(f"\n  REFUSED: {exc}\n")
        return 1
    except KeyboardInterrupt:
        print("\n  stopped. Nothing was committed.")
        return 130

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
