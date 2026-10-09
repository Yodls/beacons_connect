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
