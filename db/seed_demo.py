import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from app import app
from models import Question, QuestionOption, User, UserAnswer, db

EMAIL_PREFIX = "demo."
PASSWORD = "demo-account-not-for-login"

FIRST = [
    "Amara", "Diego", "Priya", "Liam", "Sofia", "Malik", "Chen", "Ava",
    "Omar", "Fatima", "Noah", "Leila", "Andre", "Mei", "Jonas", "Rosa",
    "Kofi", "Hana", "Tomas", "Zara", "Ethan", "Nadia", "Ravi", "Elena",
    "Marcus", "Yara", "Felix", "Aisha", "Danny", "Ingrid", "Samir", "Clara",
]
LAST = [
    "Okafor", "Ramirez", "Shah", "Byrne", "Costa", "Hassan", "Wu", "Sullivan",
    "Haddad", "Rahman", "Bergman", "Nasser", "Laurent", "Tanaka", "Weiss",
    "Delgado", "Mensah", "Kim", "Silva", "Ahmed", "Doyle", "Petrov", "Nair",
    "Moreau", "Osei", "Haddadi", "Lindqvist", "Diallo", "Walsh", "Larsen",
    "Aziz", "Novak",
]

CLUSTERS = [
    {
        "name": "CS / tech",
        "size": 8,
        "majors": ["Computer Science", "Information Technology",
                   "Computer Engineering",
                   "Information Systems and Business Analytics"],
        "minors": ["Mathematics", "Business Analytics", "Cognitive Science"],
        "hobbies": ["Coding Projects", "Robotics", "Anime & Manga", "Movies & TV",
                    "Astronomy", "Podcasts", "Collecting"],
        "games": ["Minecraft", "Valorant", "League of Legends", "Chess",
                  "Rocket League", "Super Smash Bros.", "Overwatch"],
        "rainy": ["Watching movies or playing games", "Catching up on schoolwork"],
        "friday": ["Staying inside to recuperate after a long week",
                   "Trying some new foods"],
        "study": ["I study alone", "I'm forming a study group with my classmates"],
    },
    {
        "name": "arts / humanities",
        "size": 7,
        "majors": ["English", "Art", "Music", "Theatre Arts", "Communications",
                   "History"],
        "minors": ["Creative Writing", "Art History", "Cinema Studies",
                   "Philosophy"],
        "hobbies": ["Creative Writing", "Drawing & Painting", "Photography",
                    "Playing an Instrument", "Reading", "Theater",
                    "Music Production", "Journaling", "Coffee & Cafes"],
        "games": ["Dungeons & Dragons", "Scrabble", "Chess", "Animal Crossing",
                  "Stardew Valley", "Uno"],
        "rainy": ["Watching movies or playing games",
                  "Going out to do something (can't stay cooped up)"],
        "friday": ["Night at the Museum of Fine Arts",
                   "Looking for an adventure around Boston"],
        "study": ["I study alone", "Asking for help from the prof/TA"],
    },
    {
        "name": "sports / health",
        "size": 7,
        "majors": ["Exercise and Health Sciences", "Nursing", "Biology",
                   "Urban Public Health"],
        "minors": ["Psychology", "Sport Business", "Biology"],
        "hobbies": ["Cooking", "Yoga & Meditation", "Hiking", "Traveling",
                    "Pets & Animals", "Movies & TV"],
        "games": ["Basketball", "Soccer", "Volleyball", "Running", "Swimming",
                  "Weightlifting", "NBA 2K"],
        "rainy": ["Going out to do something (can't stay cooped up)",
                  "Catching up on schoolwork"],
        "friday": ["Going out to party!", "Looking for an adventure around Boston"],
        "study": ["I'm forming a study group with my classmates",
                  "Asking for help from the prof/TA"],
    },
    {
        "name": "business",
        "size": 5,
        "majors": ["Accounting", "Finance", "Marketing",
                   "Management and Leadership", "Entrepreneurship"],
        "minors": ["Economics", "Marketing", "International Management"],
        "hobbies": ["Fashion & Thrifting", "Coffee & Cafes", "Traveling",
                    "Podcasts", "Collecting", "Cars"],
        "games": ["Poker", "EA Sports FC", "Basketball", "Monopoly", "NBA 2K"],
        "rainy": ["Catching up on schoolwork",
                  "Going out to do something (can't stay cooped up)"],
        "friday": ["Going out to party!", "Trying some new foods"],
        "study": ["I'm forming a study group with my classmates", "I study alone"],
    },
    {
        "name": "science / environment",
        "size": 5,
        "majors": ["Chemistry", "Biochemistry",
                   "Environmental Studies and Sustainability", "Physics",
                   "Environmental Sciences"],
        "minors": ["Environmental Biology", "Mathematics", "Chemistry"],
        "hobbies": ["Gardening", "Astronomy", "Hiking", "Camping",
                    "Volunteering", "Reading", "Fishing"],
        "games": ["Catan", "Chess", "Rock Climbing", "Running", "Scrabble"],
        "rainy": ["Catching up on schoolwork", "Watching movies or playing games"],
        "friday": ["Night at the Museum of Fine Arts",
                   "Staying inside to recuperate after a long week"],
        "study": ["I study alone", "I'm forming a study group with my classmates"],
    },
]


def option_ids():
    rows = db.session.execute(
        db.select(Question.key, QuestionOption.label, QuestionOption.id)
        .join(QuestionOption, QuestionOption.question_id == Question.id)
    )
    return {(key, label): oid for key, label, oid in rows}


def question_ids():
    return {q.key: q.id for q in db.session.scalars(db.select(Question))}


def clear():
    demo_users = db.session.scalars(
        db.select(User).where(User.email.like(f"{EMAIL_PREFIX}%"))
    ).all()
    for user in demo_users:
        db.session.delete(user)
    db.session.commit()
    print(f"  removed {len(demo_users)} demo accounts")


def seed():
    options = option_ids()
    questions = question_ids()
    if not options:
        raise SystemExit("No questions found. Run seed_questions.py first.")

    rng = random.Random(20260929)
    names = [f"{f} {l}" for f, l in zip(FIRST, LAST)]
    rng.shuffle(names)

    created = updated = 0
    index = 0

    for cluster in CLUSTERS:
        for _ in range(cluster["size"]):
            name = names[index]
            index += 1
            email = EMAIL_PREFIX + name.lower().replace(" ", ".") + "@umb.edu"

            user = db.session.scalar(db.select(User).filter_by(email=email))
            if user is None:
                user = User(
                    name=name,
                    email=email,
                    password_hash=generate_password_hash(PASSWORD),
                )
                db.session.add(user)
                created += 1
            else:
                updated += 1
            user.name = name
            user.email_verified = True
            db.session.flush()

            picks = {
                "degree_type": [rng.choices(
                    ["Bachelor's", "Master's"], weights=[85, 15]
                )[0]],
                "major": [rng.choice(cluster["majors"])],
                "grad_year": [rng.choice(["2026", "2027", "2028", "2029", "2030"])],
                "hobbies": rng.sample(
                    cluster["hobbies"], rng.randint(3, min(6, len(cluster["hobbies"])))
                ),
                "games": rng.sample(
                    cluster["games"], rng.randint(2, min(5, len(cluster["games"])))
                ),
                "rainy_day": [rng.choice(cluster["rainy"])],
                "friday_night": [rng.choice(cluster["friday"])],
                "study_style": [rng.choice(cluster["study"])],
            }
            if rng.random() < 0.5:
                picks["minor"] = [rng.choice(cluster["minors"])]

            db.session.execute(db.delete(UserAnswer).filter_by(user_id=user.id))
            for key, labels in picks.items():
                for label in labels:
                    option_id = options.get((key, label))
                    if option_id is None:
                        raise SystemExit(f"unknown option {key}/{label!r}")
                    db.session.add(
                        UserAnswer(
                            user_id=user.id,
                            question_id=questions[key],
                            option_id=option_id,
                        )
                    )

    db.session.commit()

    total = db.session.scalar(
        db.select(db.func.count()).select_from(User)
        .where(User.email.like(f"{EMAIL_PREFIX}%"))
    )
    print(f"  {created} created, {updated} refreshed, {total} demo accounts total")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if "--clear" in sys.argv:
            clear()
        else:
            seed()
