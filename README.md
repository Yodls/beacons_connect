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
