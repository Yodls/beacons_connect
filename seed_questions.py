from app import app
from models import Question, QuestionOption, db

DEGREE_TYPES = [
    "Bachelor's",
    "Master's",
    "Doctorate",
    "Graduate Certificate",
    "Undergraduate Certificate",
]

GRAD_YEARS = ["2026", "2027", "2028", "2029", "2030", "2031", "Later"]

MAJORS = [
    "Accounting",
    "Accounting with Data Analytics",
    "Africana Studies",
    "Aging Studies",
    "American Studies",
    "Anthropology",
    "Applied Behavior Analysis for Special Populations",
    "Applied Economics",
    "Applied Linguistics",
    "Applied Physics",
    "Applied Sociology",
    "Art",
    "Asian Studies",
    "Assistive Technology for Individuals with Visual Impairments",
    "Autism Endorsement",
    "Biochemistry",
    "Biology",
    "Biomedical Engineering and Biotechnology",
    "Biotechnology Professional Science",
    "Biotechnology and Biomedical Sciences",
    "Business Administration",
    "Business Analytics",
    "Chemistry",
    "Classical Languages",
    "Classical Studies",
    "Clean Energy and Sustainability",
    "Clinical Psychology",
    "Communications",
    "Community Development",
    "Computational Sciences",
    "Computer Engineering",
    "Computer Science",
    "Conflict Resolution",
    "Contemporary Marketing",
    "Cortical/Cerebral Visual Impairment",
    "Counseling Psychology",
    "Creative Writing",
    "Criminology and Criminal Justice",
    "Critical Ethnic and Community Studies",
    "Critical and Creative Thinking",
    "Cybersecurity",
    "Deafblind",
    "Developmental and Brain Sciences",
    "Doctor of Nursing Practice",
    "Dual Language",
    "Early Childhood Education and Care",
    "Early Education and Care in Inclusive Settings",
    "Economics",
    "Education - Early Education",
    "Education - Elementary",
    "Education - Middle and Secondary",
    "Educational Leadership for Social Justice",
    "Electrical Engineering",
    "Engineering Physics",
    "English",
    "English Language Development",
    "Entrepreneurship",
    "Environmental Biology",
    "Environmental Sciences",
    "Environmental Studies and Sustainability",
    "Exercise and Health Sciences",
    "Finance",
    "French",
    "Gender, Leadership, and Public Policy",
    "Gerontology",
    "Gerontology Research and Policy",
    "Gerontology: Frank J. Manning",
    "Gerontology: Management of Aging Services",
    "Global Governance and Human Security",
    "Global Inclusion and Social Development",
    "Higher Education",
    "Historical Archaeology",
    "History",
    "Human Rights",
    "Human Services",
    "Information Systems and Business Analytics",
    "Information Technology",
    "Instructional Design",
    "Instructional Technology Design",
    "Instructional and Learning Design",
    "Integrative Biosciences",
    "Interdisciplinary Business",
    "International Management",
    "International Relations",
    "Investment Management and Quantitative Finance",
    "Italian",
    "K-12 Instructional Technology",
    "Labor Studies",
    "Latin American and Iberian Studies",
    "Latin and Classical Humanities",
    "Learning, Teaching, and Educational Transformation",
    "Management and Leadership",
    "Marine Science and Technology",
    "Marketing",
    "Mathematics",
    "Mental Health Counseling",
    "Music",
    "Nurse Educator",
    "Nursing",
    "Philosophy",
    "Philosophy, Law, and Ethics",
    "Physics",
    "Political Science",
    "Psychology",
    "Public Administration",
    "Public History",
    "Public Policy",
    "Quantum Information",
    "Rehabilitation Counseling",
    "School Psychology",
    "Sociology",
    "Spanish-English Translation",
    "Special Education",
    "Sport Business",
    "Sport Leadership and Administration",
    "Supply Chain Management",
    "Sustainable Marine Aquaculture",
    "Theatre Arts",
    "Transition Leadership",
    "Urban Education Leadership and Policy Studies",
    "Urban Planning and Community Development",
    "Urban Public Health",
    "Vision Rehabilitation Therapy",
    "Vision Studies",
    "Vision Studies: Orientation and Mobility",
    "Women’s Gender and Sexuality Studies",
]

MINORS = [
    "Accounting",
    "Africana Studies",
    "Aging Studies",
    "American Studies",
    "Anthropology",
    "Arabic",
    "Art History",
    "Asian American Studies",
    "Biology",
    "Business Analytics",
    "Chemistry",
    "Chinese",
    "Cinema Studies",
    "Classical Languages",
    "Classical Studies",
    "Clean Energy",
    "Cognitive Science",
    "Communication",
    "Community Development",
    "Computer Science",
    "Creative Writing",
    "Criminology and Criminal Justice",
    "Cross-Cultural East Asian Studies",
    "Dance",
    "East Asian Languages",
    "Economics",
    "Education Studies",
    "English",
    "Entrepreneurship",
    "Environmental Anthropology",
    "Environmental Biology",
    "Environmental Chemistry",
    "Environmental Science",
    "Finance",
    "Geospatial Analysis and Modeling",
    "German Studies",
    "History",
    "Human Rights",
    "Information Systems",
    "International Management",
    "International Relations",
    "Italian Studies",
    "Japanese",
    "Labor Studies",
    "Latin American Studies",
    "Latino Studies",
    "Literary History",
    "Management and Leadership",
    "Marketing",
    "Mathematics",
    "Music",
    "Native American and Indigenous Studies",
    "Philosophy",
    "Philosophy and Law",
    "Physics",
    "Political Science",
    "Portuguese Studies",
    "Pre-Medical and Allied Health Program",
    "Professional and New Media Writing",
    "Psychology",
    "Public Policy",
    "Queer and Trans Studies",
    "Race, Ethnicity, and Literature",
    "Religious Studies",
    "Science, Medicine, and Society: Past and Present",
    "Sociology",
    "South Asian Studies",
    "Spanish Language",
    "Sport Business",
    "Studio Art",
    "Supply Chain Management",
    "Theatre Arts",
    "Women’s, Gender, and Sexuality Studies",
]

HOBBIES = [
    "Reading", "Creative Writing", "Drawing & Painting", "Photography",
    "Graphic Design", "Playing an Instrument", "Singing", "Dancing",
    "Theater", "Movies & TV", "Anime & Manga", "Music Production",
    "Podcasts", "Cooking", "Baking", "Coffee & Cafes",
    "Fashion & Thrifting", "Hiking", "Camping", "Fishing",
    "Gardening", "Traveling", "Volunteering", "Learning Languages",
    "Astronomy", "Coding Projects", "Robotics", "Cars",
    "Pets & Animals", "Collecting", "Journaling", "Yoga & Meditation",
]

GAMES = [
    "Minecraft", "Fortnite", "Roblox", "Valorant",
    "League of Legends", "Overwatch", "Call of Duty", "Rocket League",
    "Grand Theft Auto", "Elden Ring", "The Legend of Zelda",
    "Super Smash Bros.", "Mario Kart", "Animal Crossing", "Stardew Valley",
    "EA Sports FC", "NBA 2K", "Baldur's Gate 3", "Among Us",
    "Chess", "Poker", "Uno", "Catan", "Monopoly", "Scrabble",
    "Dungeons & Dragons",
    "Basketball", "Soccer", "Volleyball", "Tennis", "Table Tennis",
    "Football", "Baseball", "Running", "Swimming", "Rock Climbing",
    "Weightlifting",
]

RAINY_DAY = [
    "Catching up on schoolwork",
    "Going out to do something (can't stay cooped up)",
    "Watching movies or playing games",
]

FRIDAY_NIGHT = [
    "Going out to party!",
    "Night at the Museum of Fine Arts",
    "Trying some new foods",
    "Staying inside to recuperate after a long week",
    "Looking for an adventure around Boston",
]

STUDY_STYLE = [
    "I study alone",
    "I'm forming a study group with my classmates",
    "Asking for help from the prof/TA",
    "I don't study (I'm sure it will all turn out fine)",
]

# Order matters: get_questionnaire() sorts by questions.id, so the rows have to be
# inserted in the order the form should show them.
QUESTIONS = [
    {
        "key": "degree_type",
        "prompt": "What's your degree type?",
        "allows_multiple": False,
        "weight": 1,
        "options": DEGREE_TYPES,
    },
    {
        "key": "major",
        "prompt": "What's your major?",
        "allows_multiple": False,
        "weight": 3,
        "options": MAJORS,
    },
    {
        "key": "minor",
        "prompt": "What's your minor? (if applicable)",
        "allows_multiple": False,
        "weight": 2,
        "options": MINORS,
    },
    {
        "key": "grad_year",
        "prompt": "When do you expect to graduate?",
        "allows_multiple": False,
        "weight": 2,
        "options": GRAD_YEARS,
    },
    {
        "key": "hobbies",
        "prompt": "Pick your hobbies",
        "allows_multiple": True,
        "weight": 1,
        "options": HOBBIES,
    },
    {
        "key": "games",
        "prompt": "Favorite games & sports",
        "allows_multiple": True,
        "weight": 1,
        "options": GAMES,
    },
    {
        "key": "rainy_day",
        "prompt": "On a rainy day, what are you doing?",
        "allows_multiple": False,
        "weight": 1,
        "options": RAINY_DAY,
    },
    {
        "key": "friday_night",
        "prompt": "What's your ideal Friday night?",
        "allows_multiple": False,
        "weight": 1,
        "options": FRIDAY_NIGHT,
    },
    {
        "key": "study_style",
        "prompt": "You have a big test coming up. How are you going to study for it?",
        "allows_multiple": False,
        "weight": 1,
        "options": STUDY_STYLE,
    },
]


def seed():
    added_questions = added_options = 0

    for spec in QUESTIONS:
        question = db.session.scalar(db.select(Question).filter_by(key=spec["key"]))
        if question is None:
            question = Question(key=spec["key"])
            db.session.add(question)
            added_questions += 1

        question.prompt = spec["prompt"]
        question.allows_multiple = spec["allows_multiple"]
        question.weight = spec["weight"]
        db.session.flush()

        existing = {option.label: option for option in question.options}
        for sort_order, label in enumerate(spec["options"]):
            option = existing.pop(label, None)
            if option is None:
                db.session.add(
                    QuestionOption(
                        question_id=question.id, label=label, sort_order=sort_order
                    )
                )
                added_options += 1
            else:
                option.sort_order = sort_order

        # Never delete a stale option: the answer rows point at it with an
        # ON DELETE CASCADE foreign key, so dropping one silently erases that
        # answer for every student who picked it. Report and leave it alone.
        for label in existing:
            print(f"  kept {spec['key']}/{label!r} - no longer in this file")

        print(f"  {spec['key']:<13} {len(spec['options']):>3} options")

    db.session.commit()
    print()
    print(f"added {added_questions} questions, {added_options} options")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed()
