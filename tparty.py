"""The T Party's rules, for party rooms played online.

Pure functions only, like games.py: no Flask, no database, no imports from
the app. app.py keeps one room per row and runs the ride here.

This is a line-for-line port of section 1 of static/t-party.js (hash,
seatList, objectList, tremor, gateAt and LocalRide). The page draws the
dogs, scooters, seats and brake checks from the state it is sent, using its
own copy of those functions, so the two copies have to agree exactly or a
rider gets hit by something that is not on their screen. Change one, change
the other, then run

    python tparty.py

which drives whole rides through both (Node runs the browser's copy) and
compares the complete state after every step.

Practice rides with bots still run entirely in the browser. The bot is
ported here too, but only so the check above can drive complete rides;
online rooms are people only.
"""
import math

# The train. Also handed to the page (t_party.html renders it into
# tp-world-data), so the browser and the server always ride the same one.
WORLD = {
    "carWidth": 1120,
    "carCount": 8,
    "duration": 300,
    "gateInterval": 38.5,
    "speed": 134,
    "gravity": 640,
    "jumpVelocity": 300,
    "maxHealth": 6,
    "cinematicSeconds": 4.8,
    "capacity": 8,
    "carNames": ["All aboard", "Personal space", "Wheel trouble", "The quiet car",
                 "Moving furniture", "Seat scramble", "Last call", "The big green button"],
    "colors": ["#FF791F", "#78AFE9", "#FEDE42", "#B99DD8",
               "#51C6B1", "#F28CA8", "#8CD879", "#FFFFFF"],
    "seatOffsets": [150, 270, 390, 510, 630, 750, 870, 990],
}

# The two ride lengths the page offers.
DURATIONS = (90, 300)
# The browser steps practice rides in twentieths of a second; so does this.
STEP = 0.05
MIN_RIDERS = 2

PETS = ("dog", "cat", "turkey")
RIDES = ("scooter", "bike", "armchair", "cart")
HURTS = {
    "dog": "tangled in a dog leash",
    "cat": "swiped by a carrier cat",
    "turkey": "flogged by a wild turkey",
    "scooter": "e-scooter encounter",
    "bike": "e-bike encounter",
    "armchair": "flying furniture",
    "cart": "runaway luggage cart",
}


def _sign(v):
    return (v > 0) - (v < 0)


def hash32(n):
    """hash() in the script: 32-bit integer mixing, as Math.imul does it."""
    n &= 0xFFFFFFFF
    n = ((n ^ (n >> 16)) * 0x45D9F3B) & 0xFFFFFFFF
    n = ((n ^ (n >> 16)) * 0x45D9F3B) & 0xFFFFFFFF
    return (n ^ (n >> 16)) & 0xFFFFFFFF


def seat_list(s, car, w):
    out = []
    for deck in range(1 if car == 7 else 2):
        for i, x in enumerate(w["seatOffsets"]):
            sid = f"{car}:{deck}:{i}"
            rider = next((p for p in s["players"].values()
                          if p["hp"] > 0 and p["seat"] == sid), None)
            out.append({"id": sid, "x": car * w["carWidth"] + x, "deck": deck, "index": i,
                        "npc": (i + car + deck + s["seed"] % 3) % 3 != 0,
                        "owner": rider["id"] if rider else None})
    return out


def object_list(s, car, w, time=None):
    """The animals in the aisle and whatever is rolling back at you."""
    t = s["elapsed"] if time is None else time
    out, cw = [], w["carWidth"]
    for deck in range(1 if car == 7 else 2):
        for j, off in enumerate((438, 795)):
            kind = PETS[(hash32(s["seed"] + car * 7) + deck * 2 + j) % 3]
            out.append({"id": f"{kind}-{car}-{deck}-{j}", "kind": kind,
                        "x": car * cw + off + hash32(s["seed"] + car + deck + j) % 45,
                        "deck": deck, "w": 38, "h": 23, "vx": 0})
        cycle = 5.6 - min(car, 6) * .18
        phase = math.fmod(car * .77 + deck * 2.4, cycle)
        latest = math.floor((t - 7 - phase) / cycle)
        for n in range(max(0, latest - 2), latest + 1):
            age = t - (7 + phase + n * cycle)
            if age < 0:
                continue
            h = hash32(s["seed"] + car * 113 + deck * 47 + n * 29)
            kind = RIDES[h % 4]
            x = car * cw + cw - 145 - age * (170 + h % 65 + car * 5)
            if x >= car * cw + 24:
                out.append({"id": f"ride-{car}-{deck}-{n}", "kind": kind, "x": x, "deck": deck,
                            "w": 50 if kind in ("bike", "cart") else 36,
                            "h": 43 if kind == "armchair" else 33,
                            "vx": -(170 + h % 65 + car * 5)})
    return out


def tremor(s):
    """The next brake check or rough patch of track."""
    at, tid = 17, 0
    while at + 1 < s["elapsed"]:
        tid += 1
        at += 20 + hash32(s["seed"] + tid * 31) % 11
    left = at - s["elapsed"]
    return {"id": tid, "at": at, "in": left,
            "kind": "brake" if hash32(s["seed"] + tid) % 2 else "bounce",
            "warning": 0 <= left <= 4.5, "active": left <= 0 and left >= -1}


def gate_at(s, boundary, w):
    return boundary * w["gateInterval"] * s["duration"] / w["duration"]


def new_rider(pid, name, color, index, w=WORLD):
    """One rider at the platform end of car 08, where the practice ride puts them."""
    return {"id": pid, "name": name, "color": color, "x": 85 + index * 22,
            "deck": 1 if index >= 2 else 0, "y": 0, "vy": 0, "hp": w["maxHealth"],
            "seat": None, "dir": 0, "invuln": 0, "stairs": 0, "braced": -1,
            "note": "", "noteTime": 0}


class Ride:
    """LocalRide, working on a state dict in place. Callers copy first."""

    def __init__(self, state, w=WORLD):
        self.s = state
        self.w = w

    def log(self, kind, text, player):
        s = self.s
        s["eventId"] += 1
        s["events"].append({"id": s["eventId"], "kind": kind, "text": text,
                            "player": player, "at": s["elapsed"]})
        s["events"] = s["events"][-16:]

    def finish(self, winner, reason):
        self.s["phase"] = "finished"
        self.s["winner"] = winner
        self.s["reason"] = reason

    def hit(self, p, source):
        if p["hp"] <= 0 or p["seat"] is not None or p["invuln"] > 0:
            return
        s = self.s
        p["hp"] -= 1
        p["invuln"] = 1.6
        p["x"] = max(32, p["x"] - 28)
        p["note"] = source
        p["noteTime"] = 2
        if not p["hp"]:
            self.log("eject", p["name"] + " has left the atmosphere.", p["id"])
            victim = {"id": p["id"], "name": p["name"], "color": p["color"],
                      "x": p["x"], "deck": p["deck"]}
            if s["cinematic"]:
                s["cinematic"]["victims"].append(victim)
            else:
                s["cinematic"] = {"id": s["eventId"], "remaining": self.w["cinematicSeconds"],
                                  "victims": [victim]}
        else:
            self.log("hit", p["name"] + ": " + source, p["id"])

    def input(self, pid, d):
        s, w = self.s, self.w
        p = s["players"].get(pid)
        if not p or s["phase"] != "running" or p["hp"] <= 0 or s["cinematic"]:
            return
        cw = w["carWidth"]
        move = d.get("move")
        p["dir"] = move if move in (-1, 0, 1) else 0
        if d.get("jump") and p["y"] <= 0:
            p["seat"] = None
            p["vy"] = w["jumpVelocity"]
        car = min(7, math.floor(p["x"] / cw))
        off = math.fmod(p["x"], cw)
        if (d.get("stairs") and car < 7 and p["stairs"] <= 0 and p["y"] <= 0
                and (off < 105 or off > cw - 105)):
            deck = 1 if d["stairs"] == 1 else 0
            if p["deck"] != deck:
                p["deck"] = deck
                p["seat"] = None
                p["stairs"] = .65
        if d.get("act"):
            if p["seat"] is not None:
                p["seat"] = None
                return
            if p["x"] > cw * 8 - 130 and p["deck"] == 0 and p["y"] <= 0:
                self.finish(p["id"], p["name"] + " hit the green button!")
                self.log("win", s["reason"], p["id"])
                return
            options = [a for a in seat_list(s, car, w)
                       if a["deck"] == p["deck"] and not a["npc"] and not a["owner"]
                       and abs(a["x"] - p["x"]) < 61]
            options.sort(key=lambda a: abs(a["x"] - p["x"]))
            if options and p["y"] <= 0:
                a = options[0]
                p["seat"] = a["id"]
                p["x"] = a["x"]
                p["y"] = 0
                p["vy"] = 0
                p["dir"] = 0
            else:
                p["note"] = "Find an empty seat with a green marker."
                p["noteTime"] = 2.2

    def bot(self, p):
        """The practice bot. Used by the parity check, never by online rooms."""
        s, w = self.s, self.w
        cw = w["carWidth"]
        car = min(7, math.floor(p["x"] / cw))
        t, d = tremor(s), {"move": 1}
        if p["deck"] == 1 and car == 6 and math.fmod(p["x"], cw) > cw - 105:
            d["stairs"] = -1
        locked = car < 7 and s["elapsed"] < gate_at(s, car + 1, w)
        if p["seat"]:
            if t["warning"] or t["active"] or locked:
                return {"move": 0}
            return {"move": 1, "act": True}
        if t["warning"] or t["active"] or (locked and math.fmod(p["x"], cw) > cw * .45):
            available = [a for a in seat_list(s, car, w)
                         if a["deck"] == p["deck"] and not a["npc"] and not a["owner"]]
            if not t["warning"] and not t["active"]:
                forward = [a for a in available if a["x"] >= p["x"] - 55]
                if forward:
                    available = forward
            available.sort(key=lambda a: abs(a["x"] - p["x"]))
            if available:
                dx = available[0]["x"] - p["x"]
                d["move"] = 0 if abs(dx) < 35 else _sign(dx)
                d["act"] = abs(dx) < 55
            if d.get("act"):
                return d
        obstacles = [a for a in object_list(s, car, w) if a["deck"] == p["deck"]]
        walking = w["speed"] * (1.4 if s["duration"] < w["duration"] else 1)

        def coming(a):
            dx = a["x"] - p["x"]
            closing = d["move"] * walking - a["vx"]
            if not closing:
                return False
            contact = (dx - _sign(dx) * (a["w"] / 2 + 10)) / closing
            return dx * closing > 0 and 0 <= contact < .32

        if p["y"] <= 0 and any(coming(a) for a in obstacles):
            d["jump"] = True
        if car == 7 and p["x"] > cw * 8 - 125:
            d["act"] = True
        last = p["id"][-1:]
        if (last.isdigit() and (math.floor(s["elapsed"] * 2) + int(last)) % 23 == 0
                and not t["warning"]):
            d["move"] = 0
        return d

    def step(self, dt, bots=False):
        s, w = self.s, self.w
        cw = w["carWidth"]
        if s["phase"] == "countdown":
            s["countdown"] = max(0, s["countdown"] - dt)
            if not s["countdown"]:
                s["phase"] = "running"
            return
        if s["phase"] != "running":
            return
        if s["cinematic"]:
            s["cinematic"]["remaining"] -= dt
            if s["cinematic"]["remaining"] <= 0:
                s["cinematic"] = None
                for p in s["players"].values():
                    p["invuln"] = max(p["invuln"], 1.5)
                if not any(p["hp"] > 0 for p in s["players"].values()):
                    self.finish(None, "Every rider became a shooting star.")
            return
        s["elapsed"] += dt
        if s["elapsed"] >= s["duration"]:
            s["elapsed"] = s["duration"]
            self.finish(None, "Time's up. Nobody stopped the train.")
            return
        if bots:
            for p in list(s["players"].values()):
                if p["id"] != "you" and p["hp"] > 0:
                    self.input(p["id"], self.bot(p))
        event = tremor(s)
        objects = {}
        for p in s["players"].values():
            if p["hp"] <= 0:
                continue
            p["invuln"] = max(0, p["invuln"] - dt)
            p["stairs"] = max(0, p["stairs"] - dt)
            p["noteTime"] = max(0, p["noteTime"] - dt)
            if p["seat"] is None:
                unlocked = len([g for g in (gate_at(s, i + 1, w) for i in range(7))
                                if s["elapsed"] >= g])
                limit = min(cw * 8 - 55, (unlocked + 1) * cw - 23)
                if p["deck"] == 1:
                    limit = min(limit, cw * 7 - 35)
                # The quick ride walks faster; the full commute does not.
                quick = 1.4 if s["duration"] < w["duration"] else 1
                p["x"] = max(32, min(limit, p["x"] + p["dir"] * w["speed"] * quick * dt))
                if p["y"] > 0 or p["vy"] > 0:
                    p["y"] = max(0, p["y"] + p["vy"] * dt)
                    p["vy"] -= w["gravity"] * dt
                    if not p["y"]:
                        p["vy"] = 0
            car = min(7, math.floor(p["x"] / cw))
            if car not in objects:
                objects[car] = object_list(s, car, w)
            for obj in objects[car]:
                if (obj["deck"] == p["deck"] and abs(p["x"] - obj["x"]) < obj["w"] / 2 + 10
                        and p["y"] < obj["h"] - 3):
                    self.hit(p, HURTS[obj["kind"]])
            if event["active"] and p["braced"] != event["id"]:
                p["braced"] = event["id"]
                if p["seat"] is None:
                    brake = event["kind"] == "brake"
                    self.hit(p, "hard braking" if brake else "met the ceiling")
                    if p["hp"] > 0:
                        if brake:
                            p["x"] = max(32, p["x"] - 92)
                        else:
                            p["y"] = 91
                            p["vy"] = -90


# ---------------------------------------------------------------- the rooms
# app.py holds the row lock and the clock; these only change the state.
#
# Who is behind each rider lives under "_riders" ({rider id: {"uid", "seen"}}),
# beside the ride rather than in it, and view() leaves it out: the page has
# no use for user ids or anybody's last-seen time.

def new_state(code, seed, duration):
    """A room on the platform: nobody aboard yet, nothing moving."""
    return {"phase": "lobby", "seed": seed, "host": None, "code": code, "players": {},
            "elapsed": 0, "duration": duration, "events": [], "eventId": 0,
            "cinematic": None, "winner": None, "reason": "", "countdown": 3,
            "_riders": {}, "_next": 0}


def clean_input(d):
    """What a sync may ask for, and nothing else. True is not 1 here."""
    d = d if isinstance(d, dict) else {}

    def pick(v):
        return v if type(v) is int and v in (-1, 0, 1) else 0

    return {"move": pick(d.get("move")), "jump": d.get("jump") is True,
            "act": d.get("act") is True, "stairs": pick(d.get("stairs"))}


def rider_of(s, uid):
    """The rider id this student rides as in this room, or None."""
    return next((pid for pid, r in s["_riders"].items() if r["uid"] == uid), None)


def _line_up(s, w=WORLD):
    for i, p in enumerate(list(s["players"].values())):
        p["x"] = 85 + i * 22
        p["deck"] = 1 if i >= 2 else 0


def add_rider(s, uid, name, now, w=WORLD):
    """Board someone at the platform. Returns their rider id, or None when full."""
    pid = rider_of(s, uid)
    if pid:
        return pid
    if len(s["players"]) >= w["capacity"]:
        return None
    s["_next"] += 1
    pid = "p" + str(s["_next"])
    taken = {p["color"] for p in s["players"].values()}
    color = next(c for c in w["colors"] if c not in taken)
    s["players"][pid] = new_rider(pid, name, color, len(s["players"]), w)
    s["_riders"][pid] = {"uid": uid, "seen": now}
    if s["host"] is None:
        s["host"] = pid
    return pid


def remove_rider(s, pid):
    """Take someone off the train. The host's job passes to whoever boarded next."""
    s["players"].pop(pid, None)
    s["_riders"].pop(pid, None)
    if s["host"] == pid:
        s["host"] = next(iter(s["players"]), None)
    if s["phase"] == "lobby":
        _line_up(s)        # close the gap on the platform


def start(s, seed, w=WORLD):
    """Doors close: a fresh layout, everyone back to full hearts at car 08."""
    s.update({"phase": "countdown", "seed": seed, "elapsed": 0, "events": [], "eventId": 0,
              "cinematic": None, "winner": None, "reason": "", "countdown": 3})
    riders = list(s["players"].values())
    s["players"] = {p["id"]: new_rider(p["id"], p["name"], p["color"], i, w)
                    for i, p in enumerate(riders)}


def view(s, pid):
    """The room as one rider's page wants it: the ride, plus which rider they are."""
    out = {k: v for k, v in s.items() if not k.startswith("_")}
    out["you"] = pid
    return out


# ---------------------------------------------------------------- the check
# `python tparty.py`: whole practice rides, the bots driving, through the
# browser's rules (static/t-party.js, run by Node) and through these, compared
# after every twentieth of a second. Covers a win, the clock running out and
# every rider being knocked out.

_NODE = r"""
const T = require(process.argv[1]);
const w = JSON.parse(process.argv[2]), seed = Number(process.argv[3]), duration = Number(process.argv[4]);
const r = new T.LocalRide(w, "You", seed, duration);
const out = [JSON.stringify(r.s)];
for (let n = 0; r.s.phase !== "finished" && n < duration * 20 + 400; n++) {
  if (r.s.phase === "running") r.input("you", r.bot(r.s.players.you));
  r.step(.05);
  out.push(JSON.stringify(r.s));
}
process.stdout.write(out.join("\n"));
"""

_CHECK_RIDES = ((28341, 90), (4, 90), (771203, 90), (123457, 300), (899999, 300))


def _difference(a, b, path="state"):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k} is only on one side"
            found = _difference(a[k], b[k], f"{path}.{k}")
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} has {len(b)} items in the browser and {len(a)} here"
        for i, (x, y) in enumerate(zip(a, b)):
            found = _difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: browser {b!r}, server {a!r}"


def check_against_browser():
    """Returns a list of lines to print; raises AssertionError on a mismatch."""
    import json
    import os
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "t-party.js")
    said = []
    for seed, duration in _CHECK_RIDES:
        done = subprocess.run(["node", "-e", _NODE, script, json.dumps(WORLD),
                               str(seed), str(duration)],
                              capture_output=True, text=True, check=True)
        steps = [json.loads(line) for line in done.stdout.splitlines()]
        state = steps[0]
        ride = Ride(state)
        for n, want in enumerate(steps[1:], 1):
            if state["phase"] == "running":
                ride.input("you", ride.bot(state["players"]["you"]))
            ride.step(STEP, bots=True)
            diff = _difference(state, want)
            assert diff is None, f"seed {seed}, {duration}s ride, step {n}: {diff}"
        said.append(f"seed {seed:>6}, {duration:>3}s ride: {len(steps) - 1} steps match. "
                    f"{state['reason']}")
    return said


if __name__ == "__main__":
    import shutil
    import sys
    if not shutil.which("node"):
        sys.exit("Node is not installed, so the browser's copy of the rules cannot be run.")
    try:
        for line in check_against_browser():
            print(line)
    except AssertionError as e:
        sys.exit("MISMATCH: " + str(e))
    print("The server's rules match static/t-party.js.")
