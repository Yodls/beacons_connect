"""Seed demo clubs and their tags.

Re-runnable: clubs are matched on name, and a club's tags are replaced each run.

    python seed_clubs.py

A club is tagged with the same question options students answer in, so club
recommendations come out of the same weighted overlap as person matching. Tags
are written as (question key, option label) pairs and resolved to option ids at
seed time; an unknown label is an error rather than a silently dropped tag.
"""
from app import app
from models import Club, ClubTag, Question, QuestionOption, db

# (name, description, [(question_key, option_label), ...])
CLUBS = [
    ("Computer Science Club",
     "Talks, project nights and interview prep for anyone who writes code.",
     [("major", "Computer Science"), ("major", "Information Technology"),
      ("hobbies", "Coding Projects"), ("hobbies", "Robotics")]),

    ("Robotics Team",
     "Designs and builds competition robots. No experience necessary.",
     [("hobbies", "Robotics"), ("hobbies", "Coding Projects"),
      ("major", "Computer Engineering"), ("major", "Electrical Engineering")]),

    ("Beacon Esports",
     "Competitive and casual teams across the big online titles.",
     [("games", "League of Legends"), ("games", "Valorant"),
      ("games", "Overwatch"), ("games", "Rocket League"),
      ("games", "Super Smash Bros."), ("games", "Call of Duty")]),

    ("Tabletop Guild",
     "Board games, card games and a long-running D&D campaign.",
     [("games", "Dungeons & Dragons"), ("games", "Catan"),
      ("games", "Monopoly"), ("games", "Scrabble"), ("games", "Uno")]),

    ("Chess Club",
     "Casual play, ladder games and coaching for all ratings.",
     [("games", "Chess"), ("games", "Poker")]),

    ("Intramural Basketball",
     "Pickup and league play through the season.",
     [("games", "Basketball")]),

    ("Soccer Club",
     "Weekly matches on the turf field, all skill levels.",
     [("games", "Soccer"), ("games", "EA Sports FC")]),

    ("Volleyball Club",
     "Indoor and sand volleyball, competitive and social teams.",
     [("games", "Volleyball")]),

    ("Racquet Sports",
     "Tennis and table tennis ladders, plus open play nights.",
     [("games", "Tennis"), ("games", "Table Tennis")]),

    ("Running Club",
     "Group runs along the harbour, from 5K to marathon training.",
     [("games", "Running"), ("hobbies", "Yoga & Meditation")]),

    ("Swim Club",
     "Lap swimming, technique clinics and the occasional meet.",
     [("games", "Swimming")]),

    ("Climbing Club",
     "Indoor bouldering trips and outdoor climbs in season.",
     [("games", "Rock Climbing"), ("hobbies", "Hiking")]),

    ("Barbell Club",
     "Strength training, form coaching and friendly competition.",
     [("games", "Weightlifting")]),

    ("Outdoors Club",
     "Hiking, camping and fishing trips around New England.",
     [("hobbies", "Hiking"), ("hobbies", "Camping"), ("hobbies", "Fishing")]),

    ("Photography Club",
     "Photo walks, critique sessions and darkroom access.",
     [("hobbies", "Photography"), ("hobbies", "Traveling")]),

    ("Art & Design Collective",
     "Studio time, sketch nights and a yearly student show.",
     [("hobbies", "Drawing & Painting"), ("hobbies", "Graphic Design"),
      ("major", "Art")]),

    ("Theatre Troupe",
     "Two productions a year, on stage and behind it.",
     [("hobbies", "Theater"), ("hobbies", "Dancing"),
      ("major", "Theatre Arts")]),

    ("Film Society",
     "Weekly screenings and a student short film festival.",
     [("hobbies", "Movies & TV")]),

    ("Anime Club",
     "Screenings, manga library and an annual convention trip.",
     [("hobbies", "Anime & Manga")]),

    ("Music Ensemble",
     "Open rehearsals for players and singers of every instrument.",
     [("hobbies", "Playing an Instrument"), ("hobbies", "Singing"),
      ("hobbies", "Music Production"), ("major", "Music")]),

    ("Creative Writing Workshop",
     "Weekly workshop for fiction, poetry and everything between.",
     [("hobbies", "Creative Writing"), ("hobbies", "Journaling"),
      ("hobbies", "Reading"), ("major", "English")]),

    ("Book Club",
     "One book a month, argued over coffee.",
     [("hobbies", "Reading"), ("hobbies", "Coffee & Cafes")]),

    ("Campus Radio & Podcast",
     "Student-run shows, live sessions and production training.",
     [("hobbies", "Podcasts"), ("hobbies", "Music Production"),
      ("major", "Communications")]),

    ("Culinary Club",
     "Cook-alongs, baking swaps and restaurant crawls.",
     [("hobbies", "Cooking"), ("hobbies", "Baking")]),

    ("Coffee Society",
     "Cafe crawls around Boston and home-brewing tastings.",
     [("hobbies", "Coffee & Cafes")]),

    ("Fashion Society",
     "Thrift runs, styling workshops and an end-of-year showcase.",
     [("hobbies", "Fashion & Thrifting")]),

    ("Astronomy Club",
     "Observing nights, telescope training and planetarium trips.",
     [("hobbies", "Astronomy"), ("major", "Physics"),
      ("major", "Engineering Physics")]),

    ("Garden & Sustainability",
     "Campus garden plots and sustainability projects.",
     [("hobbies", "Gardening"),
      ("major", "Environmental Studies and Sustainability")]),

    ("Volunteer Corps",
     "Weekly service projects with Boston community partners.",
     [("hobbies", "Volunteering"), ("major", "Human Services")]),

    ("Language Exchange",
     "Conversation tables in a dozen languages, all levels welcome.",
     [("hobbies", "Learning Languages"), ("major", "French"),
      ("major", "Italian")]),

    ("Auto Club",
     "Maintenance workshops, car meets and track days.",
     [("hobbies", "Cars")]),

    ("Animal Welfare Society",
     "Shelter volunteering, fostering support and adoption drives.",
     [("hobbies", "Pets & Animals"), ("hobbies", "Volunteering")]),

    ("Travel Club",
     "Weekend trips, trip planning nights and budget travel tips.",
     [("hobbies", "Traveling")]),

    ("Yoga & Mindfulness",
     "Drop-in yoga, meditation sessions and stress-week workshops.",
     [("hobbies", "Yoga & Meditation")]),

    ("Pre-Med Society",
     "MCAT prep, shadowing leads and health careers panels.",
     [("major", "Biology"), ("major", "Biochemistry"),
      ("major", "Chemistry"), ("major", "Urban Public Health")]),

    ("Business & Entrepreneurship",
     "Pitch nights, case competitions and alumni networking.",
     [("major", "Accounting"), ("major", "Finance"), ("major", "Marketing"),
      ("major", "Entrepreneurship"), ("major", "Management and Leadership")]),

    ("Nursing Students Association",
     "Clinical skills practice, NCLEX prep and mentorship.",
     [("major", "Nursing")]),

    ("Psychology Society",
     "Research talks, grad school panels and study groups.",
     [("major", "Psychology")]),

    ("Model UN",
     "Conference delegations and weekly debate practice.",
     [("major", "Political Science"), ("major", "International Relations")]),

    ("Study Group Network",
     "Matches students into subject study groups each term.",
     [("study_style", "I'm forming a study group with my classmates"),
      ("rainy_day", "Catching up on schoolwork")]),
]


def option_lookup():
    """{(question key, option label): option id} for validating tags."""
    rows = db.session.execute(
        db.select(Question.key, QuestionOption.label, QuestionOption.id)
        .join(QuestionOption, QuestionOption.question_id == Question.id)
    )
    return {(key, label): option_id for key, label, option_id in rows}


def seed():
    options = option_lookup()
    if not options:
        raise SystemExit("No questions found. Run seed_questions.py first.")

    unknown = [tag for _, _, tags in CLUBS for tag in tags if tag not in options]
    if unknown:
        for key, label in unknown:
            print(f"  unknown option: {key}/{label!r}")
        raise SystemExit(f"{len(unknown)} tag(s) do not match any option.")

    added = 0
    for name, description, tags in CLUBS:
        club = db.session.scalar(db.select(Club).filter_by(name=name))
        if club is None:
            club = Club(name=name)
            db.session.add(club)
            added += 1

        club.description = description
        db.session.flush()

        # Tags are ours to rebuild -- unlike question options, nothing points at
        # them, so replacing them wholesale is safe.
        db.session.execute(db.delete(ClubTag).filter_by(club_id=club.id))
        for key, label in tags:
            option_id = options[(key, label)]
            question_id = db.session.scalar(
                db.select(QuestionOption.question_id).filter_by(id=option_id)
            )
            db.session.add(
                ClubTag(
                    club_id=club.id, question_id=question_id, option_id=option_id
                )
            )

    db.session.commit()

    total_tags = db.session.scalar(db.select(db.func.count()).select_from(ClubTag))
    print(f"  {len(CLUBS)} clubs ({added} new), {total_tags} tags")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed()
