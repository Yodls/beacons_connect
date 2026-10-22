# Beacons Connect

A communication platform that motivates students to connect with each other in person.

## Running it on your own machine

### Prerequisites

- **Python 3** installed ([python.org/downloads](https://www.python.org/downloads/))
- **Git** installed
- Access to a **PostgreSQL database**
- Access to a **mail server** (SMTP) for sending emails

### 1. Clone the repository

```bash
git clone https://github.com/Yodls/beacons_connect.git
```

### 2. Move into the project folder

```bash
cd beacons_connect
```

### 3. Install the required modules

```bash
pip install -r requirements.txt
```

> On Mac or Linux, use `pip3` instead of `pip`.

### 4. Set up your environment variables

The app reads its settings (database, mail server, secret key) from a file called `.env` in the project folder. You have two options:

**Option A — You were given a `.env` file:**
Just move it into the `beacons_connect` folder and skip to step 5.

**Option B — Create your own:**

1. Open `.env.example` and fill in your own values:

   ```
   SECRET_KEY=any-long-random-string
   DATABASE_URL=postgresql://username:password@host:5432/dbname
   MAIL_SERVER=smtp.example.com
   MAIL_PORT=587
   MAIL_USE_TLS=true
   MAIL_USE_SSL=false
   MAIL_USERNAME=you@example.com
   MAIL_PASSWORD=your-mail-password
   MAIL_DEFAULT_SENDER=you@example.com
   ```

   - `DATABASE_URL` should point to your PostgreSQL database.
   - `MAIL_PORT` must be a number (usually `587` with TLS or `465` with SSL).
   - `MAIL_USE_TLS` and `MAIL_USE_SSL` should be `true` or `false`. Normally only one of them is `true`.
   - Every value must be filled in, or the app will fail to start.

2. Rename the file from `.env.example` to `.env`.

> ⚠️ Never commit your `.env` file to GitHub. It contains passwords.

### 5. Start the server

```bash
python app.py
```

> On Mac or Linux, use `python3` instead of `python`.

The database tables are created automatically the first time the app runs.

### 6. Open the website

In your browser, go to:

**http://127.0.0.1:5000**

To stop the server, press `Ctrl + C` in the terminal.

## Design system: Beacon OS

The site's look is a retro desktop in the UMass Boston palette. It is all
CSS and markup; nothing in the backend depends on it.

### Where things live

| What | Where |
| --- | --- |
| Colours, fonts, windows, buttons, top bar, taskbar, friends panel, sign-in pages | `templates/base.html` (the one shared stylesheet, numbered sections 1 to 10) |
| Pixel icons | `templates/_icons.html` (one SVG sprite, used with `ui.icon("name")`) |
| Club categories and club icons | `club_catalog.py` (which club goes where) and `club_icons.py` (the drawings) |
| Sign-in brand panel and yellow tagline | `templates/_brandpanel.html` |
| Styles used by one page only | that page's `{% block appstyle %}` (or `{% block style %}`) |
| Community map styles | `templates/_community_map.css` (included by `find.html`) |
| Map pins, key and draft flag | `static/community-map.js` |

### Palette

| Name | Hex | Used for |
| --- | --- | --- |
| Beacon Blue | `#005A8B` | chats, primary buttons, your messages |
| Navy | `#132245` | text, borders, shadows, meet ups, the taskbar |
| Sky | `#78AFE9` | the desk background, friends |
| Orange | `#FF791F` | games, focus rings |
| Yellow | `#FEDE42` | clubs, the Post key, highlights |
| Alert red | `#D62828` | unread counts only |

Every window's title bar is coloured by what is inside it: chats Beacon
Blue, friends Sky, games Orange, meet ups Navy, clubs Yellow. A
`sec-chat`, `sec-friend`, `sec-game`, `sec-place` or `sec-club` class on a
wrapper picks the colour.

Type: **Jersey 15** (pixel titles, one weight) and **Atkinson Hyperlegible
Next** (everything you read), both from Google Fonts.

### Building blocks

- **Window**: a `<section class="sec-KIND">` whose first child is
  `ui.section("Title", "KIND", "icon")`. The section becomes a white window
  with a hard navy shadow, and the header becomes its title bar with grip
  stripes and an optional count.
- **Page heading**: wrap the page's `<h1>` and `.lede` in
  `<div class="pagehead">` for the frosted label over the grid.
- **Buttons**: every submit button is a raised Beacon Blue key; add
  `class="ghost"` for the white one. Panels that need a different button
  qualify their rule with `button` (see `.bell-tools` in `base.html`).
- **Icons**: `ui.icon("game")`. Small icons are drawn on a 12px grid and
  shown at 24px; keep sizes at multiples of 12 so pixels stay crisp.

### Rules the tests depend on (keep these when restyling)

- The `<style>` in `base.html` must contain no Jinja and must be identical
  on every page.
- No stylesheet may contain tag-like text such as `<h1>` or `class="card"`
  in its comments: several tests split or count the raw HTML.
- Home keeps one bare `<h1>`, first on the page, and the waiting banner
  starts with its digits.
- `<details class="dock" >` and the `class="pill">N<` badge are matched
  byte for byte; the top bar may not contain card, pill, score, shared,
  waiting, dock or "in common".
- `#log`, `.msg` and `.composer` rules stay in `chat.html`.
- Each club on `/clubs` is exactly one `class="card"` element.

## Campus map flags

The Meet up page's map (Leaflet with OpenStreetMap tiles) shows short-lived
flags inside the campus area.

**Two kinds of flag**

| | Hand-dropped flag | Event flag |
| --- | --- | --- |
| Made by | "Drop a flag" on the map | Posting an in-person meetup (Community Chat, Meet up, or Meet up in a friend chat) |
| How long | 30 minutes to 8 hours, picked when dropped | Until the end of the meetup's day |
| Who sees it | Everyone, picked friends, or one of your clubs | Only people who said they're in, until the author makes it public |
| Counts toward the 3-flag limit | Yes | No |
| Cancelling the meetup | n/a | Takes the flag down |

Flag types: Game, Study, Social, Club, Event and **U-Find** (selling
something on campus, with an optional price). Owners can drag their flags to
a new spot, edit them, or take them down; nobody else can. Meetup cards in
chats and on Meet up get a "See on map" link for anyone who can see the flag.

**API** (all JSON, signed in, writes need `X-Requested-With: XMLHttpRequest`):
`GET /api/map-flags`, `POST /api/map-flags`, `PATCH /api/map-flags/<id>`
(send only what changed, e.g. a new `latitude`/`longitude` after a drag) and
`DELETE /api/map-flags/<id>`.

**Database.** Event flags add three columns to `map_flags` (`price`,
`post_id`, `updated_at`), widen its CHECK constraints, and add a
`map_flag_viewers` table for "My friend(s)". An existing database is upgraded
automatically the first time the app serves a request, and flags are added for
meetups that are still to come. To run the upgrade by hand (safe to repeat):

```bash
flask --app app upgrade-map-flags
```

**Map tiles.** OpenStreetMap only serves its tiles to pages opened from a web
address, so the map is blank (with a note saying so) if a page is opened as a
file. Running the app (`python app.py`) and visiting it in the browser works.

## Clubs: categories and icons

The Clubs page groups clubs by subject, following UMass Boston's own
colleges, then the usual student-life groups:

| Category | Under |
| --- | --- |
| Tech & Computing | College of Science and Mathematics, College of Management |
| Engineering; Science & Math | College of Science and Mathematics |
| Health & Medicine | Manning College of Nursing and Health Sciences |
| Law, Policy & Government | McCormack Graduate School of Policy and Global Studies |
| Humanities & Social Sciences | College of Liberal Arts |
| Business | College of Management |
| Education | College of Education and Human Development |
| Environment | School for the Environment |
| Arts & Media, Sports & Recreation, Games & Hobbies, Culture & Identity, Faith & Spirituality, Service & Advocacy | student life |
| More clubs | anything the rules don't recognise |

Nothing is stored for this: `club_catalog.py` reads each club's name (then
its description) against a list of keywords and picks a category and a pixel
icon, so a club added to the database lands in the right place by itself.
If one lands wrongly, add its exact name to `OVERRIDES` in that file.

Each icon is a 16 x 16 drawing written as text in `club_icons.py`, one letter
per colour. The app serves the whole set, cached, at `/club-icons.svg`; in a
template, `ui.club_badge(club.name, club.description)` shows a club's icon
wherever a person would get initials (Clubs, Friends, Welcome, Home, the club
chat header).

## Games page

Pick a game from the four tiles, then **Find a player** (the queue) or pick a
friend and **Send invite**. Both buttons belong to one form: the friend list
joins it with `form="startgame"` and "Send invite" posts to `/games/new` with
`formaction`, so it works without JavaScript. `/games/seek` and `/games/new`
take the same fields as before.

## Chat: bubbles and cards

Messages are bubbles that fit their words, with the sender's name, the text
and the time inside, and the sender's avatar beside them (yours sit on the
right in Beacon Blue). Times show in the reader's own time zone, with
"Today", "Yesterday" or the date where each day starts (each message carries
its full UTC timestamp as `data-ts`).

Some messages are small windows instead of bubbles:

| Card | Title bar | Comes from |
| --- | --- | --- |
| Meetup | navy, "Meetup" | a message carrying a meetup (`_postcard.html`) |
| Game | orange, the game's name | an invite, accept, decline or result |
| Map flag | the flag's colour (U-Find shows its logo and price) | a link to a flag (`/find?flag=...`) in a message |

To share a flag, press **Copy link** on its card on the map and paste it into
any chat. The chat asks `/api/map-flags` which flags the reader may see, so a
flag shared with the wrong people still only shows to its own audience;
everyone else sees "That map flag has ended, or it isn't shared with you."

