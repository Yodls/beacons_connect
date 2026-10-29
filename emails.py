"""The messages that leave the app, written once and in the app's voice.

Only one so far -- the verification code -- but it is the first thing anybody
sees of Beacons Connect, and it used to be two lines of plain text while the
rest of the site is a pixel harbour. This module builds both halves of it:
the text a client without HTML shows, and the HTML one for everybody else.

Email is not the web, and three of its rules shape everything below.

**No stylesheet.** Gmail strips <style> from the head, so every rule is an
inline style attribute. There is no cascade and nothing to share, which is
why the colours are a dict here rather than CSS custom properties.

**No flexbox, no grid.** Outlook renders through Word, which understands
tables and little else. The layout is nested tables with explicit widths.

**No web fonts and no SVG.** The site's pixel typeface cannot load and Gmail
drops <svg> entirely, so the lighthouse is drawn as a table of coloured
cells -- one <td> per run of a colour -- which is the one technique that
renders everywhere, and the headings fall back to a bold system stack.

The grid below is not a redrawing: it was extracted from the same path the
site's #icon-lighthouse uses, so the mark in the email and the mark on the
home page are the same sixteen-by-twenty-four picture.
"""

from html import escape
from itertools import groupby

# UMass Boston's palette, as base.html's :root holds it. Repeated rather than
# imported because an email cannot read a stylesheet, and a wrong colour here
# is better than a template that silently renders unstyled.
NAVY = "#132245"
BEACON = "#005a8b"
GOLD = "#fede42"
SKY = "#78afe9"
PAPER = "#eef5fc"
LINE = "#c9dcf1"
MUTED = "#4f5f7e"
ON_NAVY = "#cfe0f5"
NIGHT = "#0c1631"

PIXEL = {"N": NAVY, "B": BEACON, "Y": GOLD, "W": "#ffffff", ".": None}

# Who sent this and how to reach them, at the foot of every email. Mailbox
# providers trust mail that says so, and CAN-SPAM asks for a postal address.
CONTACT_EMAIL = "beaconsconnect@yodls.co"
POSTAL_ADDRESS = ("University of Massachusetts Boston, "
                  "100 William T. Morrissey Blvd, Boston, MA 02125")

LIGHTHOUSE = (
    "......NNNN......",
    ".....NBBBBN.....",
    "....NBBBBBBN....",
    "...NNNNNNNNNN...",
    "....NYNYYNYN....",
    "....NYYYYYYN....",
    "....NYNYYNYN....",
    "...NNNNNNNNNN...",
    "....NWWWWWWN....",
    "....NWWWWWWN....",
    "....NBBBBBBN....",
    "...NBBBBBBBBN...",
    "...NWWWWWWWWN...",
    "...NWWWNNWWWN...",
    "...NWWWNNWWWN...",
    "...NBBBBBBBBN...",
    "..NBBBBBBBBBBN..",
    "..NWWWWWWWWWWN..",
    "..NWWWWWWWWWWN..",
    "..NBBBBBBBBBBN..",
    ".NBBBBBBBBBBBBN.",
    ".NWWWWWNNWWWWWN.",
    ".NWWWWWNNWWWWWN.",
    "NNNNNNNNNNNNNNNN",
)

# A system stack. The site's pixel face is a web font and will not load, so
# the next best thing is something heavy and geometric that exists already.
HEAD_FONT = ("'Trebuchet MS', 'Segoe UI', Tahoma, Verdana, sans-serif")
BODY_FONT = ("-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif")
CODE_FONT = ("'Courier New', Consolas, Monaco, monospace")


def _lighthouse(scale=4):
    """The mark, as one table cell per run of a colour.

    It used to be one cell per pixel -- 384 of them, nearly all of every
    email's markup, and a page that is almost all markup and hardly any words
    is what spam filters look for. A row of the same colour is one cell now,
    and each pixel row is its own one-row table, so cells of different widths
    never have to agree on columns (colspan is where Outlook goes wrong).

    Empty cells collapse in Outlook, so every cell carries a zero-height
    spacer rather than nothing, and the tables collapse their borders so the
    pixels touch.
    """
    width = scale * len(LIGHTHOUSE[0])
    rows = []
    for line in LIGHTHOUSE:
        cells = []
        for ch, run in groupby(line):
            w = scale * len(list(run))
            colour = PIXEL[ch]
            bgcolor = f' bgcolor="{colour}"' if colour else ""
            fill = f"background-color:{colour};" if colour else ""
            cells.append(
                f'<td width="{w}" height="{scale}"{bgcolor} style="width:{w}px;'
                f"height:{scale}px;{fill}font-size:0;line-height:0;"
                'mso-line-height-rule:exactly;">&nbsp;</td>'
            )
        rows.append(
            f'<tr><td><table role="presentation" width="{width}" cellpadding="0" '
            'cellspacing="0" border="0" style="border-collapse:collapse;">'
            f'<tr>{"".join(cells)}</tr></table></td></tr>'
        )
    return (
        f'<table role="presentation" width="{width}" cellpadding="0" '
        'cellspacing="0" border="0" style="border-collapse:collapse;'
        f'width:{width}px;">{"".join(rows)}</table>'
    )


def _first(name):
    return (name or "").split(" ")[0] or "there"


def _frame(subject, preheader, rows):
    """The harbour every email arrives in, around `rows`.

    `rows` is the card's own content: table rows that go between the gold
    tagline and the foot of the card. Everything else -- the night scene with
    the lighthouse, the water, the tagline, the card and the line under it --
    is the same for every email, which is why it is written once.
    """
    return f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:{PAPER};">
  <!-- The line some clients show beside the subject. Hidden here so it is
       the sentence we chose rather than the first words of the markup. -->
  <div style="display:none;font-size:0;line-height:0;max-height:0;overflow:hidden;">
    {escape(preheader)}
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         border="0" style="background-color:{PAPER};">
    <tr>
      <td align="center" style="padding:24px 12px;">

        <!-- Fluid, not 560 wide. A width attribute beats max-width in most
             clients, which pushed the card off the side of a phone. -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               border="0" style="width:100%;max-width:560px;
               background-color:#ffffff;border:2px solid {NAVY};
               border-radius:12px;overflow:hidden;">

          <!-- the harbour at night, as the home page opens -->
          <tr>
            <td align="center" style="background-color:{NIGHT};padding:22px 20px 18px;">
              {_lighthouse()}
              <p style="margin:14px 0 0;font-family:{HEAD_FONT};font-size:26px;
                        line-height:1.1;font-weight:bold;color:#ffffff;
                        letter-spacing:.5px;">
                BeaconsConnect
              </p>
              <p style="margin:6px 0 0;font-family:{BODY_FONT};font-size:12px;
                        line-height:1.4;color:{ON_NAVY};">
                Connect on Campus @ UMass Boston
              </p>
            </td>
          </tr>

          <!-- the strip of water under the scene -->
          <tr>
            <td height="6" style="height:6px;background-color:{BEACON};
                       border-top:2px solid {NAVY};font-size:0;line-height:0;">&nbsp;</td>
          </tr>

          <!-- the gold tagline the sign-in pages carry, in the same words -->
          <tr>
            <td align="center" style="background-color:{GOLD};color:#000000;
                       border-bottom:2px solid {NAVY};padding:11px 20px;
                       font-family:{BODY_FONT};font-size:14px;line-height:1.35;
                       font-weight:bold;">
              Meet UMass Boston students in person, not just online.
            </td>
          </tr>

          {rows}
        </table>

        <p style="margin:14px 0 0;font-family:{BODY_FONT};font-size:11px;
                  line-height:1.5;color:{MUTED};">
          Beacons Connect &middot; {escape(POSTAL_ADDRESS)}<br />
          Questions or feedback?
          <a href="mailto:{CONTACT_EMAIL}" style="color:{BEACON};">{CONTACT_EMAIL}</a>
        </p>
      </td>
    </tr>
  </table>
</body>
</html>
"""


# ------------------------------------------------------------- the pieces
# Each returns table rows for _frame, already escaped where they carry text
# somebody typed: a name is user input, and in an email it lands in HTML.

def _hello(name, line):
    return f"""
          <tr>
            <td style="padding:26px 28px 8px;font-family:{BODY_FONT};
                       font-size:16px;line-height:1.5;color:{NAVY};">
              <p style="margin:0 0 6px;font-family:{HEAD_FONT};font-size:22px;
                        font-weight:bold;color:{NAVY};">Hi {escape(_first(name))}</p>
              <p style="margin:0;color:{MUTED};">
                {line}
              </p>
            </td>
          </tr>"""


def _divider():
    return f"""
          <tr>
            <td style="padding:0 28px;">
              <table role="presentation" width="100%" cellpadding="0"
                     cellspacing="0" border="0">
                <tr><td height="2" style="height:2px;background-color:{LINE};
                        font-size:0;line-height:0;">&nbsp;</td></tr>
              </table>
            </td>
          </tr>"""


def _note(html):
    return f"""
          <tr>
            <td style="padding:16px 28px 24px;font-family:{BODY_FONT};
                       font-size:12px;line-height:1.6;color:{MUTED};">
              {html}
            </td>
          </tr>"""


def _button(label, url):
    """A gold key, as the site's primary buttons are.

    A table cell with the colour on it, not a styled link alone: Outlook
    ignores padding on an <a>, and this is the one way a button renders as a
    button everywhere.
    """
    return f"""
          <tr>
            <td align="center" style="padding:18px 28px 24px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                     style="border-collapse:separate;">
                <tr>
                  <td align="center" bgcolor="{GOLD}" style="background-color:{GOLD};
                             border:2px solid {NAVY};border-radius:10px;">
                    <a href="{escape(url, quote=True)}" style="display:inline-block;
                       padding:13px 26px;font-family:{BODY_FONT};font-size:16px;
                       line-height:1;font-weight:bold;color:#000000;
                       text-decoration:none;">{escape(label)}</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""


def _box(html):
    """The paper-blue panel the code sits in, for whatever this email is about."""
    return f"""
          <tr>
            <td style="padding:16px 28px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                     border="0" style="border-collapse:separate;">
                <tr>
                  <td style="background-color:{PAPER};border:2px solid {NAVY};
                             border-radius:10px;padding:14px 16px;
                             font-family:{BODY_FONT};font-size:15px;
                             line-height:1.5;color:{NAVY};">
                    {html}
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""


def _why(reason, off_url, settings_url):
    """Why this arrived, and how to make it stop. On every email but the code."""
    return _note(
        f"{escape(reason)} "
        f'<a href="{escape(off_url, quote=True)}" style="color:{BEACON};">'
        "Turn these emails off</a> &middot; "
        f'<a href="{escape(settings_url, quote=True)}" style="color:{BEACON};">'
        "Email settings</a>")


def _text_sign_off():
    """The last lines of every text part: who sent it and how to reach them."""
    return ("Beacons Connect - Connect on Campus @ UMass Boston\n"
            f"{POSTAL_ADDRESS}\n"
            f"Questions or feedback: {CONTACT_EMAIL}\n")


def _text_footer(reason, off_url, settings_url):
    return (f"\n--\n{reason}\nTurn these emails off: {off_url}\n"
            f"Email settings: {settings_url}\n" + _text_sign_off())


# -------------------------------------------------------------- the emails

def verification(name, code, minutes):
    """Subject, plain text and HTML for one verification code.

    Returns all three together because they say the same thing and drift
    apart the moment they are written in different places -- the text part is
    what a client with HTML turned off shows, and it has to carry the code
    just as plainly.
    """
    first = _first(name)
    subject = f"{code} is your Beacons Connect code"

    text = (
        f"Hi {first},\n\n"
        f"Your Beacons Connect verification code is {code}\n\n"
        f"It expires in {minutes} minutes. Type it into the page you left "
        "open to finish signing up.\n\n"
        "If you did not create an account, you can ignore this message -- "
        "nothing happens until the code is used.\n\n"
        + _text_sign_off()
    )

    rows = (_hello(name, "Here is the code that finishes your sign-up.")
            + _code_rows(code, minutes) + _divider()
            + _note("Didn't sign up? You can ignore this message &mdash; "
                    "nothing happens until the code is used."))

    html = _frame(subject, f"Your code is {code}. It expires in {minutes} minutes.",
                  rows)
    return subject, text, html


def _code_rows(code, minutes):
    """The code itself, big, and how long it lasts: the one thing a code
    email exists to carry. Shared by the sign-up and password codes so the
    two cannot drift."""
    return f"""

          <!-- the code itself: the one thing this message exists to carry -->
          <tr>
            <td align="center" style="padding:18px 28px 6px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                     style="border-collapse:separate;">
                <tr>
                  <td align="center" style="background-color:{PAPER};
                             border:2px solid {NAVY};border-radius:10px;
                             padding:16px 28px;font-family:{CODE_FONT};
                             font-size:34px;line-height:1;font-weight:bold;
                             letter-spacing:8px;color:{NAVY};">
                    {escape(str(code))}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td align="center" style="padding:10px 28px 22px;font-family:{BODY_FONT};
                       font-size:13px;line-height:1.5;color:{MUTED};">
              It expires in <b style="color:{NAVY};">{minutes} minutes</b>.
              Type it into the page you left open.
            </td>
          </tr>"""


def password_code(name, code, minutes):
    """Subject, text and HTML for a password code: Forgot password on the
    sign-in page, and Change password on the profile, which send the same
    one. Like the sign-up code: no unsubscribe footer, since it is about
    their own account and arrives only when asked for.
    """
    first = _first(name)
    subject = f"{code} is your Beacons Connect password code"

    text = (
        f"Hi {first},\n\n"
        f"Your code to set a new Beacons Connect password is {code}\n\n"
        f"It expires in {minutes} minutes. Type it into the page you left "
        "open.\n\n"
        "Didn't ask to change your password? You can ignore this message -- "
        "nothing changes until the code is used.\n\n"
        + _text_sign_off()
    )

    rows = (_hello(name, "Here is the code to set a new password.")
            + _code_rows(code, minutes) + _divider()
            + _note("Didn't ask to change your password? You can ignore this "
                    "message &mdash; nothing changes until the code is used."))
    return subject, text, _frame(
        subject, f"Your code is {code}. It expires in {minutes} minutes.", rows)


def verified(name, login_url):
    """Subject, text and HTML for an account an admin verified by hand.

    For the students whose code never reached them: what happened, that
    nothing more is needed from them, and the way in. No unsubscribe footer
    -- it is a one-off about their own account, like the code it replaces.
    """
    subject = "You're in: your Beacons Connect account is ready"
    line = ("We had trouble getting a sign-up code to you, so we've verified "
            "your account ourselves.")
    text = (
        f"Hi {_first(name)},\n\n"
        f"{line} You can sign in now with the email and password you chose.\n\n"
        f"Sign in: {login_url}\n\n"
        + _text_sign_off()
    )
    rows = (_hello(name, escape(line))
            + _box("You can sign in now with the email and password you chose.")
            + _button("Sign in", login_url))
    return subject, text, _frame(subject, "Your account is verified. Sign in "
                                          "whenever you're ready.", rows)


# What each instant email says. {actor} is the other person's name; {what}
# is the game for a challenge and the plan for a meetup.
ALERTS = {
    "message": {
        "subject": "{actor} sent you a message",
        "line": "{actor} sent you a message on Beacons Connect while you were away.",
        "button": "Open the chat",
    },
    "meetup": {
        "subject": "{actor} asked to meet up",
        "line": "{actor} asked to meet up with you.",
        "button": "See the meetup",
    },
    "game": {
        "subject": "{actor} challenged you to {what}",
        "line": "{actor} challenged you to a game of {what}.",
        "button": "Answer the challenge",
    },
}
ALERT_REASON = ("You're getting this because somebody you're friends with "
                "reached you on Beacons Connect while you weren't on the site.")


def alert(kind, name, actor, url, off_url, settings_url, what=None):
    """Subject, text and HTML for one instant email.

    Says who and what, never the words of a message: an inbox is less private
    than the app, and the point is to bring them back to read it there.
    """
    words = ALERTS[kind]
    subject = words["subject"].format(actor=actor, what=what or "")
    line = words["line"].format(actor=actor, what=what or "")

    text = (f"Hi {_first(name)},\n\n{line}\n"
            + (f"\n{what}\n" if kind == "meetup" and what else "")
            + f"\n{words['button']}: {url}\n"
            + _text_footer(ALERT_REASON, off_url, settings_url))

    rows = _hello(name, escape(line))
    if kind == "meetup" and what:
        rows += _box(f"<b>{escape(what)}</b>")
    rows += _button(words["button"], url) + _divider() + _why(
        ALERT_REASON, off_url, settings_url)
    return subject, text, _frame(subject, line, rows)


DIGEST_SHOWN = 8
DIGEST_REASON = ("You're getting this because you have unread notifications "
                 "on Beacons Connect. It comes at most every other day.")


def digest(name, items, total, site_url, off_url, settings_url):
    """Subject, text and HTML for the every-other-day summary.

    `items` is [{"body": ..., "url": ...}], newest first, already trimmed to
    what should be shown; `total` is how many are unread altogether.
    """
    noun = "notification" if total == 1 else "notifications"
    subject = f"You have {total} unread {noun} on Beacons Connect"
    more = total - len(items)

    lines = "".join(f"- {i['body']}\n  {i['url']}\n" for i in items)
    text = (f"Hi {_first(name)},\n\nHere's what's waiting for you:\n\n{lines}"
            + (f"\n...and {more} more.\n" if more > 0 else "")
            + f"\nSee them all: {site_url}\n"
            + _text_footer(DIGEST_REASON, off_url, settings_url))

    list_html = "".join(
        f'<tr><td style="padding:7px 0;border-bottom:1px solid {LINE};'
        f'font-family:{BODY_FONT};font-size:15px;line-height:1.4;color:{NAVY};">'
        f'<a href="{escape(i["url"], quote=True)}" style="color:{NAVY};'
        f'text-decoration:none;">&#9679;&nbsp; {escape(i["body"])}</a></td></tr>'
        for i in items)
    if more > 0:
        list_html += (f'<tr><td style="padding:8px 0 0;font-family:{BODY_FONT};'
                      f'font-size:13px;color:{MUTED};">and {more} more</td></tr>')
    rows = (_hello(name, f"You have <b style=\"color:{NAVY};\">{total} unread "
                         f"{noun}</b>. Here's what's waiting:")
            + _box('<table role="presentation" width="100%" cellpadding="0" '
                   f'cellspacing="0" border="0">{list_html}</table>')
            + _button("See them all", site_url) + _divider()
            + _why(DIGEST_REASON, off_url, settings_url))
    return subject, text, _frame(subject, f"{total} unread {noun} are waiting for you.",
                                 rows)
