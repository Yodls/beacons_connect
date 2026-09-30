import os
import secrets
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_mail import Mail, Message
from werkzeug.security import check_password_hash, generate_password_hash

import matching
from models import Club, ClubMember, Question, User, UserAnswer, db

load_dotenv()

CODE_TTL = timedelta(minutes=10)
MAX_VERIFICATION_ATTEMPTS = 5
OPTIONAL_QUESTIONS = {"minor"}
MATCH_TARGET = 3
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
        Message(
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


RETURN_TO = {"index", "clubs"}


def joined_clubs(user_id):
    return db.session.scalars(
        db.select(Club)
        .join(ClubMember, ClubMember.club_id == Club.id)
        .where(ClubMember.user_id == user_id)
        .order_by(Club.name)
    ).all()


def back_to():
    target = request.form.get("back", "index")
    return redirect(url_for(target if target in RETURN_TO else "index"))


@app.route("/")
@login_required
def index():
    matching.ensure_matches(
        current_user.id, target=MATCH_TARGET, gender_bonus=GENDER_BONUS
    )
    return render_template(
        "index.html",
        matches=matching.active_matches(current_user.id),
        joined=joined_clubs(current_user.id),
        clubs=matching.recommend_clubs(current_user.id, limit=5),
    )


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
    if matching.end_match(current_user.id, other_id):
        flash("Unmatched. We'll find you someone else.")
    else:
        flash("You are not matched with that person.")

    return redirect(url_for("index"))


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
