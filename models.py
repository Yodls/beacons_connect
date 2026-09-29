"""Database models, deliberately kept out of app.py.

Running `python app.py` loads that file under the name __main__. Anything that
then does `from app import ...` loads it a *second* time, producing a second
Flask app and a second SQLAlchemy instance - and queries issued through one are
not bound to the other. Keeping `db` and the models here means matching.py and
the seed scripts never import the app, so there is only ever one of each.
"""
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    verification_code_hash = db.Column(db.String(255))
    verification_expires_at = db.Column(db.DateTime)
    verification_attempts = db.Column(db.Integer, nullable=False, default=0)


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
        # Redundant-looking, but the composite foreign key below needs a unique
        # constraint on exactly these two columns to point at.
        db.UniqueConstraint("question_id", "id", name="uq_option_question"),
    )


class UserAnswer(db.Model):
    __tablename__ = "user_answers"

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    question_id = db.Column(db.Integer, nullable=False)
    option_id = db.Column(db.Integer, primary_key=True)

    # The pair, not two separate keys: this is what makes it impossible to file
    # an option under the wrong question.
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
    tags = db.relationship(
        "ClubTag", cascade="all, delete-orphan", passive_deletes=True
    )


class ClubTag(db.Model):
    """What a club is about, in the same vocabulary students answer in.

    Tagging a club with question options means club recommendations fall out of
    the same weighted overlap as person-to-person matching.
    """

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
