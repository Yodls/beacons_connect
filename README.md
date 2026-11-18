# Beacons Connect

A communication platform that motivates students to connect with each other in person.

## Running it on your own machine

### Prerequisites

- **Python 3** installed ([python.org/downloads](https://www.python.org/downloads/))
- **Git** installed
- Access to a **PostgreSQL database**
- A **[Resend](https://resend.com)** account with a verified sending domain, for sending emails

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

**Option A (you were given a `.env` file):**
Just move it into the `beacons_connect` folder and skip to step 5.

**Option B (create your own):**

1. Open `.env.example` and fill in your own values:

   ```
   SECRET_KEY=any-long-random-string
   DATABASE_URL=postgresql://username:password@host:5432/dbname
   RESEND_API_KEY=re_your-api-key
   MAIL_DEFAULT_SENDER=Beacons Connect <alerts@yourdomain.com>
   ```

   - `DATABASE_URL` should point to your PostgreSQL database.
   - `RESEND_API_KEY` is an API key from the Resend dashboard (API Keys).
   - `MAIL_DEFAULT_SENDER` must be an address on a domain you have verified in Resend (Domains), and on that exact domain: if you verified a subdomain such as `mail.yourdomain.com`, the address must be `…@mail.yourdomain.com`, not `…@yourdomain.com`.
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
| The Harborwalk behind every page, and the day/night switches | `templates/_harborwalk.html` (styles in `base.html`, section 2) |
| Styles used by one page only | that page's `{% block appstyle %}` (or `{% block style %}`) |
| Community map styles | `templates/_community_map.css` (included by `find.html`) |
| Map pins, their cards and the key | `static/community-map.js` |
| The Post a meetup form on the map | `templates/_meetup_form.html` (its script is at the foot of `find.html`) |
| What the two meetup forms share (section captions, the people counter) | `templates/_meet_parts.css`, included by `_community_map.css` and `_pin_picker.css` |
| The key to the map's flag colours | the strip under the map in `templates/_community_map.html`; each kind's colour is a `.kind-*` rule in `_community_map.css` |

### Palette

| Name | Hex | Used for |
| --- | --- | --- |
| Beacon Blue | `#005A8B` | chats, primary buttons, your messages |
| Navy | `#132245` | text, borders, shadows, meet ups, the taskbar |
| Sky | `#78AFE9` | friends |
| Orange | `#FF791F` | games, focus rings |
| Yellow | `#FEDE42` | clubs, the Post key, highlights |
| Alert red | `#D62828` | unread counts only |

Every window's title bar is coloured by what is inside it: chats Beacon
Blue, friends Sky, games Orange, meet ups Navy, clubs Yellow. A
`sec-chat`, `sec-friend`, `sec-game`, `sec-place` or `sec-club` class on a
wrapper picks the colour.

Type: **Jersey 15** (pixel titles, one weight, `var(--title)`) and
**Atkinson Hyperlegible Next** (everything you read), both from Google
Fonts.

### Building blocks

- **Window**: a `<section class="sec-KIND">` whose first child is
  `ui.section("Title", "KIND", "icon")`. The section becomes a white window
  with a hard navy shadow, and the header becomes its title bar with grip
  stripes and an optional count.
- **Page heading**: wrap the page's `<h1>` and `.lede` in
  `<div class="pagehead">` for the frosted label over the Harborwalk.
- **Buttons**: every submit button is a raised Beacon Blue key; add
  `class="ghost"` for the white one. Panels that need a different button
  qualify their rule with `button` (see `.bell-tools` in `base.html`).
- **Icons**: `ui.icon("game")`. Small icons are Material Symbols
  (Rounded, Apache License 2.0) in `_icons.html`; the five taskbar icons
  fill in on the tab you are on. The big illustrations, the lighthouse and
  the game icons stay pixel art.
- **Folding windows** (phones): every window folds to its title bar,
  tap to open (`_fold.html`). Mark a window `data-fold="open"` to keep it
  open, or put `<i hidden data-fold-none></i>` in a page to turn folding
  off there. Club cards on /clubs fold to their name row.- **The T Party** (`/games/t-party`): a party race game, linked from a
  tile on /games. Its page is `templates/t_party.html` and its script
  `static/t-party.js`. Practice with bots works now; party rooms need a
  room server at `/api/t-party` (not built yet).
- **Scoreboard** (/games): the ten students with the most finished games
  won (`SCOREBOARD_SIZE` in app.py). Unlisted accounts appear only to
  their friends and themselves, as on every other list of people.

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

The Meet up page's map (Leaflet with OpenStreetMap tiles) shows meetups as
flags inside the campus area. It opens on the middle of that area at zoom 16,
the whole campus in view on a computer.

**One way to put a flag on the map: post a meetup.** The form sits in the
map window (`templates/_meetup_form.html`) and opens from the "Post a
meetup" key or from the Post tab in the bottom bar. The key stands on the
map's bottom right corner on a screen wider than 700px and across under the
map on a phone; it steps aside while the form or a flag's card is open.
Posting is two steps. Pressing the key keeps the page on the map, which
asks for the flag to be placed (a tap on the map, a drag, or a tap on the
flag to keep it where it starts); placing it scrolls the page on to the
form. Once a meetup is posted, the
page scrolls back up to the map, which returns to its opening view with the
new meetup's card open on its flag. A flag's card never moves the map: it
opens above its flag, below it or beside it, wherever it fits whole (on a
phone, in a window under the map). The form asks three things:

| It asks | Choices | What follows |
| --- | --- | --- |
| What | An activity from the list, or "Other" with a type | The flag's look. A listed activity gets the meetup flag, navy with a star; one written in is Game, Study, Social or Club and takes that colour |
| When | Now (for 30 minutes to 8 hours) or Later (a date, optional time) | A meetup for now ends when its author said it would (`game_posts.ends_at`); a dated one lasts to the end of its day |
| Who can see it | Everyone, My friends (picked from a list), or One of my clubs | Who sees the flag and can join, as below |

| Posted for | Its card appears | Its flag is seen by |
| --- | --- | --- |
| Everyone | in Community Chat and under "Open meetups" | every student |
| One of my clubs | in that club's chat | the club's members |
| My friends | under "Open meetups" for the author and the picked friends, who each get a notification | the picked friends (`map_flag_viewers`) |
| The other person in a friend or pair chat (the chat's own Meet up key) | in that chat | the two of them |

Every flag a student posts is a meetup, so each has "I'm in", a limit on how
many can come and its own chat. The author of a meetup for everyone or for
friends can lock its flag, which hides it from everybody who is not already
in; unlocking gives it back to the people it was posted for, never to more.

Flag kinds: Event (a meetup: navy with a star), Game, Study, Social and Club
(a meetup written in and given that type), and **Campus-Wide** (a
campus-wide meetup from /fixtures; the kind is still called `home` in the
code and the database). Each is drawn
as a small flag in its own colour. An author can drag their flag to a new
spot, which rewrites the meetup's place to match, and takes it down by
cancelling the meetup from its card; nobody else can do either. Meetup cards
in chats and on Meet up get a "See on map" link for anyone who can see the
flag. Those cards keep their keys on one line: "I'm in" (or "Open chat" once
you are in), "See on map", and a "..." menu that holds "Leave Meetup" and
"Cancel meetup". Each of those two asks for a second tap before it acts
("Tap again to leave"), and goes back by itself after four seconds.
Unfriending or blocking somebody takes them off the flags they were
picked for.

Students used to be able to drop a flag by hand as well, with no meetup
behind it. Posting a meetup for "Now" does that job. A hand-dropped flag
lasted eight hours at most, so there was nothing to migrate.

**API** (all JSON, signed in, writes need `X-Requested-With: XMLHttpRequest`):
`GET /api/map-flags`, `PATCH /api/map-flags/<id>` (a new
`latitude`/`longitude` after a drag, or `visibility` to lock or unlock a
meetup's flag) and `DELETE /api/map-flags/<id>` (for a flag that is not a
meetup's; a meetup's comes down when the meetup is cancelled).

**Database.** A meetup's flag uses two columns on `map_flags` (`post_id`,
`updated_at`) and the `map_flag_viewers` table, which names the friends a
flag is for. An existing database gets those automatically the first time
the app serves a request, and flags are added for meetups that are still to
come. To run that upgrade by hand (safe to repeat):

```bash
flask --app app upgrade-map-flags
```

Meetups for "Now" and for picked friends need one more migration, which adds
`game_posts.ends_at` and lets a meetup's audience be `friends`. It only adds,
and `beacon update` runs it with the rest. By hand (a dry run first, then
`--apply` to keep it):

```bash
python dbtool.py migrate add_meetup_now
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
| Map flag | the flag's colour, titled with its kind ("Study meetup") | a link to a flag (`/find?flag=...`) in a message |

To share a flag, press **Copy link** on its card on the map and paste it into
any chat. The chat asks `/api/map-flags` which flags the reader may see, so a
flag shared with the wrong people still only shows to its own audience;
everyone else sees "That map flag has ended, or it isn't shared with you."

