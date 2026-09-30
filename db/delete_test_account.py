import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import ClubMember, Match, User, UserAnswer, db

TEST_ACCOUNT = "yusuf.waili001@umb.edu"


def _counts(user_id):
    total = lambda model, clause: db.session.scalar(
        db.select(db.func.count()).select_from(model).where(clause)
    )
    return {
        "answers": total(UserAnswer, UserAnswer.user_id == user_id),
        "clubs": total(ClubMember, ClubMember.user_id == user_id),
        "matches": total(
            Match, db.or_(Match.user_lo == user_id, Match.user_hi == user_id)
        ),
    }


def _orphans():
    return {
        "answers": db.session.scalar(db.text(
            'SELECT count(*) FROM user_answers a'
            ' LEFT JOIN "user" u ON u.id = a.user_id WHERE u.id IS NULL'
        )),
        "clubs": db.session.scalar(db.text(
            'SELECT count(*) FROM club_members m'
            ' LEFT JOIN "user" u ON u.id = m.user_id WHERE u.id IS NULL'
        )),
        "matches": db.session.scalar(db.text(
            'SELECT count(*) FROM matches m'
            ' LEFT JOIN "user" a ON a.id = m.user_lo'
            ' LEFT JOIN "user" b ON b.id = m.user_hi'
            ' WHERE a.id IS NULL OR b.id IS NULL'
        )),
    }


def delete_account(email):
    user = db.session.scalar(db.select(User).filter_by(email=email))
    if user is None:
        print(f"{email} is not registered - nothing to delete")
        return False

    before = _counts(user.id)
    print(f"deleting id={user.id}  {user.name!r}  verified={user.email_verified}")
    print(f"  answers={before['answers']}"
          f"  clubs={before['clubs']}"
          f"  matches={before['matches']}")

    db.session.delete(user)
    db.session.commit()

    after = _counts(user.id)
    if any(after.values()):
        print(f"  WARNING: rows survived the cascade: {after}")
    else:
        print("  answers, memberships and matches went with it")

    orphans = _orphans()
    if any(orphans.values()):
        print(f"  WARNING: orphaned rows elsewhere: {orphans}")

    if before["matches"]:
        print(f"  {before['matches']} other student(s) lost a match; they get topped"
              " back up on their next visit")

    print(f"{email} deleted - the address is free to register again")
    return True


# Uncomment to remove some other account instead. It takes one email at a time
# on purpose, so a typo cannot clear more than a single row.
#
#     python db/delete_test_account.py someone.else@umb.edu
#
# def delete_other():
#     if len(sys.argv) < 2:
#         print("usage: python db/delete_test_account.py <email>")
#         return
#     delete_account(sys.argv[1].strip().lower())


if __name__ == "__main__":
    with app.app_context():
        delete_account(TEST_ACCOUNT)
        # delete_other()
