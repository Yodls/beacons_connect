"""Where each club sits on the Clubs page, and which icon it gets.

Clubs live in the database with just a name and a description, and new ones
are added there by hand, so nothing here needs a migration. A club's category
and icon are worked out from its name (or, failing that, its description) by
the keyword rules below. Get one wrong? Add the club to OVERRIDES.

The academic categories follow UMass Boston's own colleges, so a student can
find the computing clubs where they would look for computer science, and so
on. The rest follow the usual student-life groups.

    look(name, description)  -> Look(category, icon)
    grouped(clubs)           -> [(Category, [club, ...]), ...] in page order
"""
import re
from collections import namedtuple

from club_icons import ICONS

Category = namedtuple("Category", "key label home icon")
Look = namedtuple("Look", "category icon")

# Page order. `home` is the line under the category's title bar: the UMass
# Boston college a subject belongs to, or what the group is about.
CATEGORIES = [
    Category("tech", "Tech & Computing",
             "College of Science and Mathematics and College of Management", "laptop"),
    Category("engineering", "Engineering",
             "College of Science and Mathematics", "gear"),
    Category("science", "Science & Math",
             "College of Science and Mathematics", "flask"),
    Category("health", "Health & Medicine",
             "Manning College of Nursing and Health Sciences", "medcross"),
    Category("law", "Law, Policy & Government",
             "McCormack Graduate School of Policy and Global Studies", "scales"),
    Category("humanities", "Humanities & Social Sciences",
             "College of Liberal Arts", "column"),
    Category("business", "Business",
             "College of Management", "chart"),
    Category("education", "Education",
             "College of Education and Human Development", "apple"),
    Category("environment", "Environment",
             "School for the Environment", "leaf"),
    Category("arts", "Arts & Media",
             "Film, music, performance, art and campus media", "palette"),
    Category("sports", "Sports & Recreation",
             "Teams, fitness and the outdoors", "trophy"),
    Category("hobbies", "Games & Hobbies",
             "Games, food and pastimes", "dice"),
    Category("culture", "Culture & Identity",
             "Heritage, identity and community", "bunting"),
    Category("faith", "Faith & Spirituality",
             "Faith groups and spiritual life", "dove"),
    Category("service", "Service & Advocacy",
             "Volunteering, activism and causes", "hands"),
    Category("more", "More clubs",
             "Everything else", "flag"),
]
BY_KEY = {c.key: c for c in CATEGORIES}

# Checked top to bottom; the first rule with a keyword in the club's name
# wins (whole words, any case). Order matters where names overlap: "Marine
# Science" must reach marine before science, "Martial Arts" sports before
# art, "Muslim Student Association" faith before culture.
RULES = [
    # --- the specific before the general ---
    ("sports", "trophy", ["martial arts", "mixed martial arts"]),
    ("service", "megaphone", ["social justice", "advocacy", "activism", "activists"]),
    ("faith", "dove", ["muslim student", "muslim students", "hillel", "christian", "catholic",
                       "islamic", "jewish", "bible", "interfaith", "spiritual", "spirituality",
                       "religious", "faith", "buddhist", "hindu", "sikh", "chaplaincy"]),
    ("culture", "rainbow", ["lgbtq", "lgbtq+", "lgbt", "queer", "pride", "gsa", "trans"]),
    ("arts", "paper", ["mass media", "newspaper", "journalism", "student press", "magazine"]),
    ("arts", "mic", ["radio", "podcast", "broadcasting", "wumb"]),
    ("environment", "wave", ["marine", "ocean", "oceanography", "coastal", "aquatic"]),
    ("environment", "leaf", ["environment", "environmental", "sustainability", "sustainable",
                             "climate", "garden", "gardening", "conservation", "ecology",
                             "green", "earth", "urban planning"]),
    # --- tech, engineering, science, math ---
    ("tech", "laptop", ["computer science", "computing", "coding", "code", "programming",
                        "hackathon", "hack", "hackers", "software", "developers", "web",
                        "cyber", "cybersecurity", "data science", "data",
                        "artificial intelligence", "machine learning", "tech", "technology",
                        "information technology", "game design", "game development"]),
    ("tech", "laptop", ["IT", "AI"]),   # capitals only (see CASE_SENSITIVE)
    ("engineering", "robot", ["robotics", "robot", "robots"]),
    ("engineering", "gear", ["engineering", "engineers", "engineer", "mechanical", "electrical",
                             "maker", "makers", "makerspace"]),
    ("health", "stetho", ["pre-med", "premed", "pre-medical", "medicine", "medical", "pre-health",
                          "prehealth", "physicians", "doctors", "mcat", "pre-pa"]),
    ("health", "medcross", ["nursing", "nurse", "nurses", "public health", "health", "emt",
                            "first aid", "red cross", "pharmacy", "pre-pharmacy", "dental",
                            "pre-dental", "physical therapy", "exercise science", "kinesiology",
                            "gerontology", "aging", "nutrition"]),
    ("humanities", "brain", ["psychology", "psych", "neuroscience", "cognitive"]),
    ("science", "telescope", ["astronomy", "astro", "astrophysics", "space", "stargazing",
                              "planetarium", "rocketry", "telescope"]),
    ("science", "atom", ["physics"]),
    ("science", "dna", ["biology", "bio", "genetics", "biotech", "biotechnology", "microbiology"]),
    ("science", "flask", ["chemistry", "chem", "biochemistry", "biochem"]),
    ("science", "calc", ["math", "mathematics", "statistics", "stats", "actuarial"]),
    # --- law, policy, government ---
    ("law", "scales", ["pre-law", "prelaw", "law", "legal", "mock trial", "justice",
                       "criminal justice", "criminology"]),
    ("law", "globe", ["model un", "model united nations", "international relations",
                      "global", "diplomacy", "foreign affairs"]),
    ("law", "ballot", ["student government", "government", "politics", "political",
                       "political science", "policy", "democrats", "republicans", "civic",
                       "vote", "voting", "election", "elections"]),
    ("law", "mic", ["debate", "speech", "forensics", "toastmasters", "public speaking"]),
    # --- humanities and social sciences ---
    ("humanities", "column", ["philosophy", "history", "classics", "archaeology",
                              "anthropology", "ethics"]),
    ("humanities", "quill", ["writing", "writers", "poetry", "poets", "literary", "literature",
                             "english", "creative writing", "zine", "storytelling"]),
    ("humanities", "book", ["language", "languages", "linguistics", "spanish", "french",
                            "japanese language", "chinese language", "translation"]),
    ("humanities", "chart", ["economics", "econ", "sociology"]),
    # --- business ---
    ("business", "bulb", ["entrepreneur", "entrepreneurs", "entrepreneurship", "startup",
                          "startups", "innovation"]),
    ("business", "chart", ["business", "finance", "financial", "investment", "investing",
                           "accounting", "marketing", "management", "consulting", "sales",
                           "real estate", "supply chain"]),
    # --- education ---
    ("education", "apple", ["education", "teaching", "teachers", "future teachers",
                            "tutoring", "tutors", "mentoring", "early childhood"]),
    # --- science catch-all, after every "X science" above ---
    ("science", "flask", ["science", "sciences", "stem", "research"]),
    # --- arts and media ---
    ("arts", "film", ["film", "films", "cinema", "movie", "movies", "filmmaking",
                      "screenwriting", "video production"]),
    ("arts", "anime", ["anime", "manga", "cosplay", "animation"]),
    ("arts", "note", ["music", "musicians", "band", "choir", "chorus", "a cappella",
                      "acapella", "jazz", "orchestra", "dj", "hip hop", "rap", "singers"]),
    ("arts", "masks", ["theater", "theatre", "drama", "improv", "comedy", "acting",
                       "musical theater", "performing arts"]),
    ("arts", "dancer", ["dance", "dancing", "ballet", "salsa", "step team", "k-pop"]),
    ("arts", "camera", ["photography", "photo", "photographers"]),
    ("arts", "palette", ["art", "arts", "painting", "drawing", "design", "illustration",
                         "craft", "crafts", "knitting", "crochet", "pottery", "sculpture"]),
    # --- sports and recreation ---
    ("sports", "hoop", ["basketball", "hoops"]),
    ("sports", "soccer", ["soccer", "futsal"]),
    ("sports", "shoe", ["running", "run", "runners", "track", "cross country", "marathon"]),
    ("sports", "mountain", ["climbing", "bouldering", "climbers", "mountaineering"]),
    ("sports", "tent", ["outdoors", "outdoor", "hiking", "hikers", "camping", "backpacking",
                        "fishing", "kayak", "kayaking", "skiing", "snowboarding"]),
    ("sports", "lotus", ["yoga", "mindfulness", "meditation", "pilates", "wellness"]),
    ("sports", "dumbbell", ["fitness", "lifting", "weightlifting", "powerlifting", "gym",
                            "strength", "crossfit", "bodybuilding", "barbell"]),
    ("sports", "bike", ["cycling", "bike", "biking", "bicycle"]),
    ("sports", "wave", ["swim", "swimming", "sailing", "rowing", "crew", "surf", "surfing"]),
    ("sports", "racquet", ["tennis", "pickle ball", "pickleball", "badminton",
                            "squash", "racquet", "racquetball"]),
    ("sports", "skate", ["skating", "skate", "ice skating", "skateboard",
                         "rollerblading", "hockey"]),
    ("sports", "soccer", ["kickball", "dodgeball", "kick ball"]),
    ("sports", "trophy", ["sport", "sports", "intramural", "intramurals", "athletics",
                          "volleyball", "tennis", "baseball", "softball", "hockey", "lacrosse",
                          "rugby", "karate", "taekwondo", "jiu jitsu", "boxing", "fencing",
                          "ultimate", "frisbee", "badminton", "table tennis", "ping pong",
                          "golf", "football", "cricket", "wrestling", "club sports"]),
    # --- games and hobbies ---
    ("hobbies", "car", ["auto", "car", "cars", "automotive", "motorsport",
                        "driving", "mechanic"]),
    ("hobbies", "truck", ["food truck", "food trucks"]),
    ("hobbies", "knight", ["chess"]),
    ("hobbies", "pad", ["pokemon", "pokémon", "minecraft", "roblox", "fortnite"]),
    ("arts", "film", ["tv show", "tv shows", "television", "streaming"]),
    ("arts", "palette", ["fashion", "thrifting", "thrift", "style"]),
    ("hobbies", "chef", ["eating around", "restaurants", "dining"]),
    ("sports", "shoe", ["walking the city", "walking", "walks"]),
    ("education", "hands", ["accountability", "accountability groups",
                            "study group", "study groups"]),
    ("hobbies", "pad", ["esports", "e-sports", "gaming", "video game", "video games", "gamers",
                        "smash", "nintendo"]),
    ("hobbies", "dice", ["board game", "board games", "tabletop", "dungeons", "d&d", "dnd",
                         "card games", "puzzle", "puzzles", "trivia", "rpg", "games"]),
    ("hobbies", "coffee", ["coffee", "tea", "cafe", "café", "barista"]),
    ("hobbies", "chef", ["cooking", "baking", "culinary", "food", "foodies", "chef"]),
    ("hobbies", "book", ["book club", "books", "reading", "readers"]),
    # --- culture and identity ---
    ("culture", "bunting", ["cultural", "culture", "heritage", "multicultural",
                            "black student", "black students", "asian", "latinx", "latino",
                            "latina", "hispanic", "african", "caribbean", "haitian",
                            "cape verdean", "vietnamese", "korean", "chinese", "japanese",
                            "indian", "south asian", "pakistani", "arab", "irish",
                            "international students", "first generation", "first-gen",
                            "student union", "students association", "student association"]),
    # --- service and advocacy ---
    ("service", "paw", ["animal", "animals", "pets", "shelter", "rescue", "wildlife"]),
    ("service", "hands", ["volunteer", "volunteers", "volunteering", "service", "habitat",
                          "charity", "nonprofit", "community service", "mutual aid",
                          "food pantry", "outreach", "unicef"]),
    ("service", "megaphone", ["advocacy", "activism", "activists", "rights", "awareness",
                              "coalition", "social justice", "equity", "union"]),
    # --- academic societies with no subject in the name ---
    ("more", "cap", ["honor society", "honors", "honours", "scholars", "phi", "sigma", "alpha",
                     "academic"]),
]

# Matched only in capitals, so "IT Club" is tech and "make it" is not.
CASE_SENSITIVE = {"IT", "AI"}

# Read in a club's name but not in its description, where they are too
# common to mean anything ("make it", "art of", "run by", "space for").
NAME_ONLY = {"code", "data", "web", "hack", "run", "track", "crew", "green", "earth", "space",
             "games", "union", "rights", "art", "arts", "design", "tea", "bio", "psych", "chem",
             "econ", "astro", "stats", "trans", "pride", "service", "health", "sport", "sports",
             "law", "justice", "policy", "speech", "history", "english", "math", "craft",
             "photo", "band", "rap", "dj", "gym", "strength", "food", "reading", "books",
             "awareness", "equity", "outreach", "academic", "alpha", "phi", "sigma", "maker",
             "makers", "aging", "management", "sales", "marketing", "innovation", "comedy",
             "acting", "drama", "garden", "climate", "research", "science", "sciences",
             "stem", "culture", "cultural", "heritage", "faith", "spiritual", "animal",
             "animals", "shelter", "rescue", "volunteer", "dance", "music", "film", "movie",
             "movies", "medicine", "medical", "nurse", "global", "civic", "vote", "voting",
             "language", "languages", "writing", "poetry", "board games", "puzzle", "puzzles"}

# A club whose name the rules read wrongly: its exact name -> (category, icon).
OVERRIDES = {
    "Coffee Society": ("hobbies", "coffee"),
    "Yoga & Mindfulness": ("sports", "lotus"),
}


def _pattern(words, case_sensitive=False):
    # Whole words; "&", "+" and "-" count as part of a word.
    if not words:
        return None
    alts = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"(?<![\w&+-])(?:%s)(?![\w&+-])" % "|".join(alts),
                      0 if case_sensitive else re.I)


def _compile(for_description):
    out = []
    for cat, icon, words in RULES:
        exact = [w for w in words if w in CASE_SENSITIVE]
        loose = [w for w in words if w not in CASE_SENSITIVE]
        if for_description:
            exact = []
            loose = [w for w in loose if w not in NAME_ONLY and len(w) > 3]
        pattern = _pattern(exact, True) if exact else _pattern(loose)
        if pattern:
            out.append((cat, icon, pattern))
    return out


_FOR_NAME = _compile(False)
_FOR_DESCRIPTION = _compile(True)


def _check():
    for cat, icon, _ in RULES:
        assert cat in BY_KEY, cat
        assert icon in ICONS, icon
    for cat, icon in OVERRIDES.values():
        assert cat in BY_KEY and icon in ICONS, (cat, icon)
    for c in CATEGORIES:
        assert c.icon in ICONS, c.icon


_check()


def look(name, description=""):
    """The category and icon for one club."""
    if name in OVERRIDES:
        return Look(*OVERRIDES[name])
    for text, rules in ((name or "", _FOR_NAME), (description or "", _FOR_DESCRIPTION)):
        for cat, icon, pattern in rules:
            if pattern.search(text):
                return Look(cat, icon)
    return Look("more", "flag")


def grouped(clubs):
    """Clubs bucketed by category, in page order, empty categories left out.

    Each club keeps the order it came in, so whatever the page sorted first
    (suggestions, then the alphabet) stays first within its category.
    """
    buckets = {c.key: [] for c in CATEGORIES}
    for club in clubs:
        buckets[look(club.name, club.description).category].append(club)
    return [(c, buckets[c.key]) for c in CATEGORIES if buckets[c.key]]
