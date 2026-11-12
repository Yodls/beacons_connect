/* T Party: no third-party renderer or game framework. Pixel-art canvas + controls.
 *   1. the rules   2. the pixel kit   3. the page: controls, camera, the send-off,
 *   painting, HUD.
 * Online games trust the Flask simulator; LocalRide is explicitly practice.
 * Exported practice rules also let the Node checks exercise complete rides.
 */
(function () {
  "use strict";
  /* ======================================================================
     1. THE RULES
     The Flask simulator mirrors these, and the Node checks load them
     through module.exports below. Since the prototype: animals took over
     the garbage piles, and the full commute is w.duration (five minutes).
     ====================================================================== */
  function hash(n) {
    n = n >>> 0;
    n = Math.imul(n ^ (n >>> 16), 0x45d9f3b) >>> 0;
    n = Math.imul(n ^ (n >>> 16), 0x45d9f3b) >>> 0;
    return (n ^ (n >>> 16)) >>> 0;
  }
  function seatList(s, car, w) {
    const out = [];
    for (let deck = 0; deck < (car === 7 ? 1 : 2); deck++) {
      w.seatOffsets.forEach((x, i) => {
        const id = car + ":" + deck + ":" + i;
        const rider = Object.values(s.players).find(p => p.hp > 0 && p.seat === id);
        out.push({id, x: car * w.carWidth + x, deck, index: i,
          npc: (i + car + deck + s.seed % 3) % 3 !== 0, owner: rider ? rider.id : null});
      });
    }
    return out;
  }
  /* The aisle animals took over the old garbage piles: same spots, same
     footprint, one animal per spot. The kinds rotate, so every car has a
     leashed dog, a cat in a carrier and a wild turkey somewhere in it. */
  const PETS = ["dog", "cat", "turkey"];
  function objectList(s, car, w, time) {
    const t = time === undefined ? s.elapsed : time, out = [], cw = w.carWidth;
    for (let deck = 0; deck < (car === 7 ? 1 : 2); deck++) {
      [438, 795].forEach((off, j) => {
        const kind = PETS[(hash(s.seed + car * 7) + deck * 2 + j) % 3];
        out.push({id: kind + "-" + car + "-" + deck + "-" + j, kind,
          x: car * cw + off + hash(s.seed + car + deck + j) % 45, deck, w: 38, h: 23, vx: 0});
      });
      const cycle = 5.6 - Math.min(car, 6) * .18;
      const phase = (car * .77 + deck * 2.4) % cycle;
      const latest = Math.floor((t - 7 - phase) / cycle);
      for (let n = Math.max(0, latest - 2); n <= latest; n++) {
        const age = t - (7 + phase + n * cycle);
        if (age < 0) continue;
        const h = hash(s.seed + car * 113 + deck * 47 + n * 29);
        const kind = ["scooter", "bike", "armchair", "cart"][h % 4];
        const x = car * cw + cw - 145 - age * (170 + h % 65 + car * 5);
        if (x >= car * cw + 24) out.push({id: "ride-" + car + "-" + deck + "-" + n,
          kind, x, deck, w: kind === "bike" || kind === "cart" ? 50 : 36, h: kind === "armchair" ? 43 : 33, vx: -(170 + h % 65 + car * 5)});
      }
    }
    return out;
  }
  function tremor(s) {
    let at = 17, id = 0;
    while (at + 1 < s.elapsed) { id++; at += 20 + hash(s.seed + id * 31) % 11; }
    const left = at - s.elapsed;
    return {id, at, in: left, kind: hash(s.seed + id) % 2 ? "brake" : "bounce",
      warning: left >= 0 && left <= 4.5, active: left <= 0 && left >= -1};
  }
  function gateAt(s, boundary, w) { return boundary * w.gateInterval * s.duration / w.duration; }
  class LocalRide {
    constructor(w, name, seed, duration) {
      this.w = w;
      this.s = {phase: "countdown", seed: seed === undefined ? 28341 : seed, you: "you", host: "you", code: "PRACTICE",
        players: {}, elapsed: 0, duration: duration || 90, events: [], eventId: 0,
        cinematic: null, winner: null, reason: "", countdown: 3};
      [name || "You", "Casey [bot]", "Morgan [bot]", "Riley [bot]"].forEach((name, i) => {
        const id = i ? "bot" + i : "you";
        this.s.players[id] = {id, name, color: w.colors[i], x: 85 + i * 22, deck: i >= 2 ? 1 : 0, y: 0, vy: 0,
          hp: w.maxHealth, seat: null, dir: 0, invuln: 0, stairs: 0, braced: -1, note: "", noteTime: 0};
      });
    }
    log(kind, text, player) {
      this.s.events.push({id: ++this.s.eventId, kind, text, player, at: this.s.elapsed});
      this.s.events = this.s.events.slice(-16);
    }
    finish(winner, reason) { this.s.phase = "finished"; this.s.winner = winner; this.s.reason = reason; }
    hit(p, source) {
      if (p.hp <= 0 || p.seat !== null || p.invuln > 0) return;
      p.hp--; p.invuln = 1.6; p.x = Math.max(32, p.x - 28); p.note = source; p.noteTime = 2;
      if (!p.hp) {
        this.log("eject", p.name + " has left the atmosphere.", p.id);
        const victim = {id: p.id, name: p.name, color: p.color, x: p.x, deck: p.deck};
        if (this.s.cinematic) this.s.cinematic.victims.push(victim);
        else this.s.cinematic = {id: this.s.eventId, remaining: this.w.cinematicSeconds, victims: [victim]};
      } else this.log("hit", p.name + ": " + source, p.id);
    }
    input(id, d) {
      const s = this.s, p = s.players[id], w = this.w;
      if (!p || s.phase !== "running" || p.hp <= 0 || s.cinematic) return;
      p.dir = [-1, 0, 1].includes(d.move) ? d.move : 0;
      if (d.jump && p.y <= 0) { p.seat = null; p.vy = w.jumpVelocity; }
      const car = Math.min(7, Math.floor(p.x / w.carWidth)), off = p.x % w.carWidth;
      if (d.stairs && car < 7 && p.stairs <= 0 && p.y <= 0 && (off < 105 || off > w.carWidth - 105)) {
        const deck = d.stairs === 1 ? 1 : 0;
        if (p.deck !== deck) { p.deck = deck; p.seat = null; p.stairs = .65; }
      }
      if (d.act) {
        if (p.seat !== null) { p.seat = null; return; }
        if (p.x > w.carWidth * 8 - 130 && p.deck === 0 && p.y <= 0) {
          this.finish(p.id, p.name + " hit the green button!"); this.log("win", s.reason, p.id); return;
        }
        const options = seatList(s, car, w).filter(a => a.deck === p.deck && !a.npc && !a.owner && Math.abs(a.x - p.x) < 61);
        options.sort((a, b) => Math.abs(a.x - p.x) - Math.abs(b.x - p.x));
        if (options.length && p.y <= 0) { const a = options[0]; p.seat = a.id; p.x = a.x; p.y = 0; p.vy = 0; p.dir = 0; }
        else { p.note = "Find an empty seat with a green marker."; p.noteTime = 2.2; }
      }
    }
    bot(p) {
      const s = this.s, w = this.w, car = Math.min(7, Math.floor(p.x / w.carWidth));
      const t = tremor(s), d = {move: 1};
      if (p.deck === 1 && car === 6 && p.x % w.carWidth > w.carWidth - 105) d.stairs = -1;
      const locked = car < 7 && s.elapsed < gateAt(s, car + 1, w);
      if (p.seat) {
        if (t.warning || t.active || locked) return {move: 0};
        return {move: 1, act: true};
      }
      if (t.warning || t.active || (locked && p.x % w.carWidth > w.carWidth * .45)) {
        if (p.seat) return {move: 0};
        let available = seatList(s, car, w).filter(a => a.deck === p.deck && !a.npc && !a.owner);
        if (!t.warning && !t.active) {
          const forward = available.filter(a => a.x >= p.x - 55);
          if (forward.length) available = forward;
        }
        available.sort((a, b) => Math.abs(a.x - p.x) - Math.abs(b.x - p.x));
        if (available.length) {
          const dx = available[0].x - p.x;
          d.move = Math.abs(dx) < 35 ? 0 : Math.sign(dx);
          d.act = Math.abs(dx) < 55;
        }
        if (d.act) return d;
      }
      const obstacles = objectList(s, car, w).filter(a => a.deck === p.deck);
      const walkingSpeed = w.speed * (s.duration < w.duration ? 1.4 : 1);
      if (p.y <= 0 && obstacles.some(a => {
        const dx = a.x - p.x, closing = d.move * walkingSpeed - a.vx;
        const timeToContact = (dx - Math.sign(dx) * (a.w / 2 + 10)) / closing;
        return dx * closing > 0 && timeToContact >= 0 && timeToContact < .32;
      })) d.jump = true;
      if (car === 7 && p.x > w.carWidth * 8 - 125) d.act = true;
      // A short hesitation gives the human an opening at every gate.
      if ((Math.floor(s.elapsed * 2) + Number(p.id.slice(-1))) % 23 === 0 && !t.warning) d.move = 0;
      return d;
    }
    step(dt) {
      const s = this.s, w = this.w;
      if (s.phase === "countdown") { s.countdown = Math.max(0, s.countdown - dt); if (!s.countdown) s.phase = "running"; return; }
      if (s.phase !== "running") return;
      if (s.cinematic) {
        s.cinematic.remaining -= dt;
        if (s.cinematic.remaining <= 0) {
          s.cinematic = null;
          Object.values(s.players).forEach(p => p.invuln = Math.max(p.invuln, 1.5));
          if (!Object.values(s.players).some(p => p.hp > 0)) this.finish(null, "Every rider became a shooting star.");
        }
        return;
      }
      s.elapsed += dt;
      if (s.elapsed >= s.duration) { s.elapsed = s.duration; this.finish(null, "Time's up. Nobody stopped the train."); return; }
      Object.values(s.players).filter(p => p.id !== "you" && p.hp > 0).forEach(p => this.input(p.id, this.bot(p)));
      const event = tremor(s);
      const objects = {};
      for (const p of Object.values(s.players)) {
        if (p.hp <= 0) continue;
        p.invuln = Math.max(0, p.invuln - dt); p.stairs = Math.max(0, p.stairs - dt); p.noteTime = Math.max(0, p.noteTime - dt);
        if (p.seat === null) {
          const unlocked = Array.from({length: 7}, (_, i) => gateAt(s, i + 1, w)).filter(t => s.elapsed >= t).length;
          let limit = Math.min(w.carWidth * 8 - 55, (unlocked + 1) * w.carWidth - 23);
          if (p.deck === 1) limit = Math.min(limit, w.carWidth * 7 - 35);
          /* the quick ride walks faster; the full commute (w.duration) does not */
          p.x = Math.max(32, Math.min(limit, p.x + p.dir * w.speed * (s.duration < w.duration ? 1.4 : 1) * dt));
          if (p.y > 0 || p.vy > 0) { p.y = Math.max(0, p.y + p.vy * dt); p.vy -= w.gravity * dt; if (!p.y) p.vy = 0; }
        }
        const car = Math.min(7, Math.floor(p.x / w.carWidth));
        if (!objects[car]) objects[car] = objectList(s, car, w);
        for (const obj of objects[car]) {
          if (obj.deck === p.deck && Math.abs(p.x - obj.x) < obj.w / 2 + 10 && p.y < obj.h - 3) {
            this.hit(p, {dog: "tangled in a dog leash", cat: "swiped by a carrier cat", turkey: "flogged by a wild turkey", scooter: "e-scooter encounter", bike: "e-bike encounter", armchair: "flying furniture", cart: "runaway luggage cart"}[obj.kind]);
          }
        }
        if (event.active && p.braced !== event.id) {
          p.braced = event.id;
          if (p.seat === null) {
            this.hit(p, event.kind === "brake" ? "hard braking" : "met the ceiling");
            if (p.hp > 0) { if (event.kind === "brake") p.x = Math.max(32, p.x - 92); else { p.y = 91; p.vy = -90; } }
          }
        }
      }
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {hash, seatList, objectList, tremor, gateAt, LocalRide};
  if (typeof document === "undefined") return;
  /* ---- sprite sheets, drawn by hand one pixel per character ----
     Riders face right. Keys: o outline, h hair, j hair shade, s skin,
     t skin shade, e eye, m mouth, c shirt, d shirt shade, l shirt light,
     p trousers, q trouser shade, k shoe, K sole, b bag, n bag shade.
     Heads are stamped onto bodies at each frame's head anchor. */
  const HEADS = {
    short: {y: 0, rows: [
      "..oooo..",
      ".ohhhhho",
      "ohhjhhho",
      "ohhhssso",
      "ohhsseso",
      "ohtsssso",
      ".osssmo.",
      "..oooo.."]},
    long: {y: 0, rows: [
      "..oooo..",
      ".ohhhhho",
      "ohjhhhho",
      "ohhhssso",
      "ohhsseso",
      "ohhsssso",
      "ohhssmo.",
      "ohjooo..",
      "ohho....",
      ".oo....."]},
    cap: {y: 0, rows: [
      "..oooo...",
      ".occccoo.",
      "occlcccco",
      "ohhoooooo",
      "ohhsseso.",
      "ohtsssso.",
      ".osssmo..",
      "..oooo..."]},
    bun: {y: -2, rows: [
      "..ooo...",
      ".ohhjo..",
      ".ooooo..",
      ".ohhhhho",
      "ohhjhhho",
      "ohhhssso",
      "ohhsseso",
      "ohtsssso",
      ".osssmo.",
      "..oooo.."]}
  };
  const BODIES = {
    stand: {head: [1, 0], rows: [
      "..........", "..........", "..........", "..........",
      "..........", "..........", "..........", "..........",
      ".ooocccco.",
      "obbocccdo.",
      "obbocldco.",
      "obbocldco.",
      "onnoclsco.",
      ".oooppppo.",
      "..oppoppo.",
      "..oppoppo.",
      "..oqpoqpo.",
      "..okkookko"]},
    walk1: {head: [1, 1], rows: [
      "..........", "..........", "..........", "..........",
      "..........", "..........", "..........", "..........",
      "..........",
      ".ooocccco.",
      "obbocccdo.",
      "obbocdlco.",
      "obbocdlso.",
      "onnooppppo",
      ".oppooqppo",
      "opqo..oppo",
      "okko..oqpo",
      "oo.....kko"]},
    walk2: {head: [1, 1], rows: [
      "..........", "..........", "..........", "..........",
      "..........", "..........", "..........", "..........",
      "..........",
      ".ooocccco.",
      "obbocccdo.",
      "obboclcdo.",
      "obbosclco.",
      "onnoppppo.",
      "..opqopo..",
      "..oppopo..",
      "..oqpoppo.",
      "..okkookko"]},
    jump: {head: [1, 0], rows: [
      "..........", "..........", "..........", "..........",
      "..........", "..........", "..........", "......os..",
      ".ooocccdso",
      "obbocccdo.",
      "obboccclo.",
      "obboccclo.",
      "onnoooooo.",
      ".oppppppo.",
      ".oppqoqppo",
      ".okkooopqo",
      "..oo..okko",
      "........o."]},
    hurt: {head: [0, 1], rows: [
      "..........", "..........", "..........", "..........",
      "..........", "..........", "..........", "..........",
      "........os",
      "ooocccccdo",
      "bbocccdco.",
      "bbocccdo..",
      "nnoclcco..",
      ".oooppppo.",
      "..oppoppo.",
      "..oppoppo.",
      ".oqpo.oqpo",
      ".okko.okko"]},
    sit: {head: [1, 0], rows: [
      "...........", "...........", "...........", "...........",
      "...........", "...........", "...........", "...........",
      "..ooccco...",
      "..occccdo..",
      "..occldco..",
      "..occlddso.",
      "..oqpppppo.",
      "..oppppppo.",
      "......oqpo.",
      "......oppo.",
      "......okko."]},
    tumble: {head: [3, 0], rows: [
      "..............", "..............", "..............", "..............",
      "..............", "..............", "..............", "..............",
      "os..occcco..so",
      "oooocccccdoooo",
      "...occclddo...",
      "...occccdco...",
      "....oppppo....",
      "...oppooppo...",
      "..oppo..oppo..",
      ".okko....okko.",
      ".oo........oo."]}
  };
  /* far away, a rider is a handful of pixels */
  const MINI = {
    half: [
      ".ohho.",
      "ohssso",
      "ocscco",
      ".occo.",
      ".oppo.",
      ".o..o."],
    dot: [
      "hs.",
      "cc.",
      "pp."]
  };
  /* seats face right: backrest on the left, cushion, steel pedestal */
  const SEAT = [
    "ooo........",
    "oxro.......",
    "oxro.......",
    "oxro.......",
    "oxro.......",
    "oxro.......",
    "oxroooooooo",
    "oxrxxxxxxxo",
    "orrrrrrrrRo",
    ".oooooooooo",
    "...ff...f..",
    "...ff...f..",
    "..ffff.fff."];
  const SEAT_PAL = {o: "#3a2234", x: "#b5688a", r: "#873c61", R: "#5e2a45", f: "#56636d"};
  const OBST = {
    scooter: {pal: {o: "#132245", e: "#132245", t: "#51c6b1", T: "#2e8f7f", w: "#c9dcf1", h: "#ff791f", H: "#c75a12", s: "#d9a27a", c: "#005a8b", C: "#003e61", p: "#2b3550", k: "#f3f1ea"}, rows: [
      "......ooo.......",
      ".....ohhho......",
      "....ohhhHHo.....",
      "....oseshho.....",
      ".oo.ossssoo.....",
      ".oto.ooccCCo....",
      "..oto.occcCCo...",
      "..oto.ocsoCCo...",
      "...oto.ooccCo...",
      "...oto..ooppo...",
      "....oto.opppppo.",
      "....oto..okkoko.",
      "....ottoooooooo.",
      "...ooTTTTTTTTTTo",
      "..oowwo....owwo.",
      "...oo.......oo.."]},
    bike: {pal: {o: "#132245", e: "#132245", f: "#ff791f", F: "#c75a12", w: "#c9dcf1", g: "#4f5f7e", h: "#fede42", H: "#c9a53a", s: "#b67a55", c: "#b99dd8", C: "#8a6db0", p: "#2b3550", k: "#f3f1ea"}, rows: [
      ".......ooo........",
      "......ohhho.......",
      ".....ohhhHHo......",
      ".....oseshho......",
      "..o..ossssoo......",
      ".ogo..occCCo......",
      ".oggoocccCCCo.....",
      "..oogsocccCCo.....",
      "....oo.occcCo.....",
      ".......opppppo....",
      "...oo..oppoppo....",
      "..offoooffoppo....",
      ".ooffffffffoko....",
      "owwoo.offo.oowwo..",
      "owgwo.of...owgwo..",
      "owwwo.o....owwwo..",
      ".ooo........ooo..."]},
    armchair: {pal: {o: "#132245", a: "#c88957", A: "#ac6c47", B: "#7d4a33", l: "#e3a874", m: "#563750"}, rows: [
      ".ooooooooo...",
      "oallllllaAo..",
      "oaaaaaaaaAo..",
      "oaaaaaaaaAo..",
      "oaaaaaaaaAo..",
      "oaaaaaaaaAo..",
      "oooooooooooo.",
      "oAlllllllllAo",
      "oAaaaaaaaaaAo",
      "oBAAAAAAAAABo",
      ".ooooooooooo.",
      "..oo.....oo..",
      "..oo.....oo.."]},
    cart: {pal: {o: "#132245", y: "#fede42", Y: "#c9a53a", t: "#51c6b1", T: "#2e8f7f", r: "#d6453d", R: "#9e2f2a", g: "#9aa6af", G: "#56636d", w: "#c9dcf1"}, rows: [
      "o..............",
      "og...oooooo....",
      "og..oyyyyYYo...",
      "og..oyooooYo...",
      "og..oyyyyyYo...",
      "og.ooooooooooo.",
      "og.orrrrrrRRRo.",
      "og.orooooooRRo.",
      "og.orrrrrrRRRo.",
      "oggggggggggggGo",
      "oooooooooooooo.",
      "..owo.....owo..",
      "...o.......o..."]}
  };
  /* ---- the aisle animals, which took over from the garbage piles ----
     Drawn WITHOUT their outer outline: outlined() in the pixel kit wraps
     them in ink, so the shapes stay easy to read and edit here. Each one
     faces left, toward riders coming up the train, and is mirrored to
     face right. A '.' is empty, an 'o' is an inside line. */
  const DOG = {
    /* b fur, B shade and ear, w white, n nose, e eye, c collar, y tag,
       m open mouth, t tongue */
    idle: [
      "..bbb........b",
      ".beBB.......b.",
      "nbbBB.......b.",
      ".wbBBcbbbbbbb.",
      "..wBBcbbbbbbB.",
      "...wbybbbbbBB.",
      "...wbbbbbbbBB.",
      "...bb.....bb..",
      "...ww.....ww.."],
    wag: [
      "..bbb.........",
      ".beBB......bb.",
      "nbbBB.......b.",
      ".wbBBcbbbbbbb.",
      "..wBBcbbbbbbB.",
      "...wbybbbbbBB.",
      "...wbbbbbbbBB.",
      "...bb.....bb..",
      "...ww.....ww.."],
    bark: [
      ".bbb.........b",
      "beBB........b.",
      "bbBB........b.",
      "mmBBcbbbbbbbb.",
      "twBBcbbbbbbbB.",
      "..wbybbbbbbBB.",
      "..wbbbbbbbbBB.",
      "..bb......bb..",
      "..ww......ww.."],
    /* where the leash clips on and where the barks come out, after outlining */
    collar: {idle: [6, 5], wag: [6, 5], bark: [5, 5]}, mouth: 4,
    coats: [
      {b: "#c8874a", B: "#7d4a2a", w: "#f4efe4"},
      {b: "#e2ad57", B: "#b47c2c", w: "#f8e6bd"},
      {b: "#4b4146", B: "#2b2528", w: "#8a7f84"},
      {b: "#9aa4b2", B: "#5b6576", w: "#f4f4f4"},
      {b: "#e08a3c", B: "#a85c22", w: "#fff4e0"}],
    leashes: ["#d6453d", "#2f6fb0", "#237b55", "#8b4381"]
  };
  /* a pet carrier with its grated door toward you; the cat lives behind
     the bars (two eyes) until a paw comes out swinging */
  const CARRIER = {
    rows: [
      ".....hhh.....",
      "....h...h....",
      ".TTTTTTTTTTT.",
      "SSkkkkkkkkkSS",
      "SSkdkdkdkdkSS",
      "SSkdkgkgkdkSS",
      "SSkdkdkdkdkSS",
      "SSkkkkkkkkkSS",
      ".UUUUUUUUUUU."],
    eyes: [[6, 6], [8, 6]], door: [3, 10], pawY: 7,
    shells: [
      {T: "#9fc9f1", S: "#78afe9", U: "#4f6f9a"},
      {T: "#f8b4c6", S: "#f28ca8", U: "#a85a73"},
      {T: "#b5e8a8", S: "#8cd879", U: "#4f8a47"},
      {T: "#efe7d4", S: "#d8ccae", U: "#8e8166"}],
    cats: [
      {c: "#e0883c", g: "#d6f04f"}, {c: "#8a8f99", g: "#ffb000"},
      {c: "#2e2a33", g: "#9fe86a"}, {c: "#efe9dc", g: "#7fd0ff"}]
  };
  /* the paw, reaching left out of the door: straight out, then the swipe */
  const PAW = {
    reach: [
      "w.......",
      "cccccccc",
      "w......."],
    up: [
      "w.w.....",
      "cc......",
      ".ccc....",
      "...ccccc"],
    down: [
      "...ccccc",
      ".ccc....",
      "cc......",
      "w.w....."],
    /* where the arm meets the door, after outlining */
    roots: {reach: [8, 2], up: [8, 4], down: [8, 1]}
  };
  /* a wild turkey, the kind that runs Boston's sidewalks: f bare head,
     r wattle and snood, k beak, b bronze, B belly, i sheen, W wing,
     w wing bars, t/T tail bands, u tail tips, l legs */
  const TURKEY = {
    idle: [
      "................",
      "................",
      "................",
      ".ff.............",
      "kfef............",
      ".rfr....uuuuu...",
      ".r.f...uTTTTTu..",
      "...ff.uTTtttTTu.",
      "...fbbbbbtttTTu.",
      "..bbbbbbbbbttTu.",
      ".bbibbbWWWbbtTu.",
      ".bbbbWwWwWbbbTu.",
      "..bbbbbbbbbbb...",
      "...BBBBBBBB.....",
      ".....l..l.......",
      "....ll.ll......."],
    peck: [
      "................",
      "................",
      "................",
      "................",
      "................",
      "........uuuuu...",
      ".......uTTTTTu..",
      "......uTTtttTTu.",
      "....bbbbbtttTTu.",
      "..fbbbbbbbbttTu.",
      ".fbibbbWWWbbtTu.",
      ".fbbbWwWwWbbbTu.",
      "ff.bbbbbbbbbb...",
      "kef.BBBBBBBB....",
      ".r...l..l.......",
      "....ll.ll......."],
    up: [
      "..........WWw...",
      ".........WWwWw..",
      "........WWwWw...",
      ".ff....WWwWw....",
      "kfef..WWwWw.....",
      ".rfr.WWwWwuuu...",
      ".r.f.WWWWTTTTu..",
      "...ffWWWTtttTTu.",
      "...fbbbbbtttTTu.",
      "..bbbbbbbbbttTu.",
      ".bbibbbbbbbbtTu.",
      ".bbbbbbbbbbbbTu.",
      "..bbbbbbbbbbb...",
      "...BBBBBBBB.....",
      ".....l..l.......",
      "....ll.ll......."],
    down: [
      "................",
      "................",
      "................",
      ".ff.............",
      "kfef............",
      ".rfr....uuuuu...",
      ".r.f...uTTTTTu..",
      "...ff.uTTtttTTu.",
      "...fbbbbbtttTTu.",
      "..bbbbbbbbbttTu.",
      ".bbibbWWWbbbtTu.",
      ".bbbbWWwWWbbbTu.",
      "..bbbbWwWwWW....",
      "...BBBBWwWwWW...",
      ".....l.WWwWwWW..",
      "....ll.l..WWWW.."],
    mid: [
      "................",
      "................",
      "................",
      ".ff.............",
      "kfef............",
      ".rfr....uuuuu...",
      ".r.f...uTTTTTu..",
      "...ff.uTTtttTTu.",
      "WWWfbbbbbWWWWWWW",
      "WwWWbbbbWWwWwWwW",
      ".WwWWbbWWwWbtTu.",
      "..WWbbbbbbbbbTu.",
      "..bbbbbbbbbbb...",
      "...BBBBBBBB.....",
      ".....l..l.......",
      "....ll.ll......."],
    pal: {f: "#cfe0f0", r: "#d6453d", k: "#e8c67a", e: "#132245", b: "#6b4a2e", B: "#3a281b", i: "#b07a42",
      W: "#7a5a3a", w: "#efe6d2", t: "#c69a62", T: "#3e2a1c", u: "#ead3a4", l: "#d29a8a"}
  };
  /* the twinkle: a star that pops, flares and settles */
  const TWINKLE = [
    [".........", ".........", ".........", ".........", "....W....", ".........", ".........", ".........", "........."],
    [".........", ".........", ".........", "....y....", "...yWy...", "....y....", ".........", ".........", "........."],
    [".........", ".........", "....y....", "....w....", "..ywWwy..", "....w....", "....y....", ".........", "........."],
    ["....y....", "....y....", "..y.w.y..", "....w....", "yywwWwwyy", "....w....", "..y.w.y..", "....y....", "....y...."],
    [".........", "....y....", "....y....", "...ywy...", ".yywWwyy.", "...ywy...", "....y....", "....y....", "........."],
    [".........", ".........", "....y....", "....w....", "..ywWwy..", "....w....", "....y....", ".........", "........."],
    [".........", ".........", ".........", "....y....", "...yWy...", "....y....", ".........", ".........", "........."]
  ];
  /* what seated passengers do with their hands, stamped over the sit frame */
  const PROPS = {
    phone: {at: [7, 8], pal: {o: "#132245", g: "#9fd8ff", s: "#000"}, rows: ["oo", "go", "oo"]},
    paper: {at: [4, 3], pal: {w: "#f4f1e6", W: "#d9d3c0", g: "#9aa6af", o: "#132245"}, rows: [
      "ooooooo",
      "owwwwwo",
      "owgggwo",
      "owwwwwo",
      "owggwWo",
      "owwwwWo",
      "owggwWo",
      "ooooooo"]},
    cup: {at: [8, 8], pal: {o: "#132245", w: "#f4f1e6", b: "#7d4a33"}, rows: ["ob", "ow", "ow"]},
    phones: {at: [1, 0], pal: {o: "#132245", d: "#2b3550", r: "#d6453d"}, rows: [
      ".oooo...",
      "o....o..",
      "o.....o.",
      "........",
      "........",
      ".dd.....",
      ".dr.....",
      ".dd....."]}
  };

  /* ======================================================================
     2. PIXEL KIT
     Everything from here down only runs in a browser. The scene is painted
     at one art pixel per canvas pixel, and the canvas is then scaled up by
     a whole number of device pixels (fitCanvas, in boot), which is how the
     Harborwalk behind the page is drawn too: every edge stays on the grid.
     ====================================================================== */
  const INK = "#132245";
  const CARPX = 320;                     // one car, in art pixels
  /* World art coordinates: x runs from the back of car 08 (0) to the front
     of car 01 (2560). y is 0 at the top of the rail and negative going up. */
  const LAY = {shellTop: -108, roof: -103, upCeil: -97, upFloor: -57, lowCeil: -52,
    lowFloor: -12, horizon: -66, viewBottom: 16};
  const floorY = deck => deck ? LAY.upFloor : LAY.lowFloor;
  const SPEED = 150;                     // how fast the outside goes by, art px per ride second
  const PAL = {
    steelHi: "#e4e9ed", steel: "#c3ccd3", steelLo: "#9aa6af", steelDk: "#6e7b86",
    purple: "#8b4381", purpleDk: "#5e2a58",
    wall: "#ede4cc", wallHi: "#f7f2e3", wallLo: "#ddd1b2", wallDk: "#c4b694",
    wains: "#d2c6a5", wainsDk: "#b3a682", trim: "#8c7f61",
    ceil: "#f6f1e3", lamp: "#fffbeb", lampWarm: "#ffe9a8",
    floorHi: "#9aa6aa", floor: "#5c6a70", floorDk: "#3e4a50",
    frame: "#7d8a8b", sill: "#c9d0cf", sillDk: "#6f7b7c",
    rack: "#a7b1b8", rackDk: "#6e7b86", pole: "#e2e8e6", poleLo: "#9aa6af",
    stair: "#6b777d", stairLo: "#4e5a60", well: "#c8bb98", wellDk: "#a99b77",
    gold: "#fede42", orange: "#ff791f", sky: "#78afe9", beacon: "#005a8b",
    green: "#51d67c", greenDk: "#237b55", greenHi: "#c0ffb7", red: "#d6453d", teal: "#51c6b1",
    led: "#0c1631", amber: "#ffb000",
    rail: "#c6d5e2", railMid: "#6f7b85", railDk: "#3e4a50", tie: "#5c4a3e", tieHi: "#7d6553",
    ballast: "#7e8d8e", ballastLo: "#66787a", ballastHi: "#a3b0b0",
    granite: "#b4aea4", graniteHi: "#d8d3ca", graniteLo: "#8e887e",
    bogie: "#2a3240", bogieHi: "#4f5f7e", spring: "#c9a53a",
    lampPole: "#558a72", lampPoleLo: "#376251", lampPoleDk: "#244539",
    lampHead: "#b2a872", lampHeadHi: "#e6dfaf", lampHeadLo: "#766e45"
  };

  /* ---------- small helpers ---------- */
  function canvasOf(wd, ht) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.ceil(wd)); c.height = Math.max(1, Math.ceil(ht));
    return c;
  }
  function pen(c) { const g = c.getContext("2d"); g.imageSmoothingEnabled = false; return g; }
  function paint(rows, pal) {
    const c = canvasOf(rows[0].length, rows.length), g = pen(c);
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const col = pal[row[x]];
        if (col) { g.fillStyle = col; g.fillRect(x, y, 1, 1); }
      }
    });
    return c;
  }
  /* paint a sprite drawn without its outer outline, wrapped in ink */
  function outlined(rows, pal) {
    const W = rows[0].length, H = rows.length, out = [];
    const filled = (x, y) => y >= 0 && y < H && x >= 0 && x < W && rows[y][x] !== ".";
    for (let y = -1; y <= H; y++) {
      let line = "";
      for (let x = -1; x <= W; x++) line += filled(x, y) ? rows[y][x] : (filled(x - 1, y) || filled(x + 1, y) || filled(x, y - 1) || filled(x, y + 1)) ? "o" : ".";
      out.push(line);
    }
    return paint(out, Object.assign({o: INK}, pal));
  }
  function mirror(src) {
    const c = canvasOf(src.width, src.height), g = pen(c);
    g.translate(src.width, 0); g.scale(-1, 1); g.drawImage(src, 0, 0);
    return c;
  }
  /* Quarter turns land every pixel exactly on the grid, so a tumbling
     rider stays crisp. Anything in between would smear. */
  function turn(src, q) {
    q = ((q % 4) + 4) % 4;
    const odd = q % 2, c = canvasOf(odd ? src.height : src.width, odd ? src.width : src.height), g = pen(c);
    g.translate(c.width / 2, c.height / 2); g.rotate(q * Math.PI / 2);
    g.drawImage(src, -src.width / 2, -src.height / 2);
    return c;
  }
  /* a solid silhouette of a sprite, for the hit flash */
  function silhouette(src, color) {
    const c = canvasOf(src.width, src.height), g = pen(c);
    g.drawImage(src, 0, 0); g.globalCompositeOperation = "source-in";
    g.fillStyle = color; g.fillRect(0, 0, c.width, c.height);
    return c;
  }
  function rgb(h) { const n = parseInt(String(h).slice(1, 7), 16) || 0; return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  function hex(r, g, b) { return "#" + ((1 << 24) | (Math.round(r) << 16) | (Math.round(g) << 8) | Math.round(b)).toString(16).slice(1); }
  function mix(a, b, t) { const x = rgb(a), y = rgb(b); return hex(x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, x[2] + (y[2] - x[2]) * t); }
  const lighten = (c, t) => mix(c, "#ffffff", t);
  function strHash(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = t => { t = clamp(t, 0, 1); return t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; };
  const smooth = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
  const wrap = (v, span) => ((v % span) + span) % span;

  /* ---------- the bitmap font: 3 x 5, so names stay on the grid ---------- */
  const FONT = {
    A: "010/101/111/101/101", B: "110/101/110/101/110", C: "011/100/100/100/011", D: "110/101/101/101/110",
    E: "111/100/110/100/111", F: "111/100/110/100/100", G: "011/100/101/101/011", H: "101/101/111/101/101",
    I: "111/010/010/010/111", J: "001/001/001/101/010", K: "101/101/110/101/101", L: "100/100/100/100/111",
    M: "10001/11011/10101/10001/10001", N: "1001/1101/1011/1001/1001", O: "010/101/101/101/010",
    P: "110/101/110/100/100", Q: "010/101/101/110/011", R: "110/101/110/101/101", S: "011/100/010/001/110",
    T: "111/010/010/010/010", U: "101/101/101/101/011", V: "101/101/101/101/010",
    W: "10001/10001/10101/11011/10001", X: "101/101/010/101/101", Y: "101/101/010/010/010",
    Z: "111/001/010/100/111", 0: "111/101/101/101/111", 1: "010/110/010/010/111", 2: "111/001/111/100/111",
    3: "111/001/111/001/111", 4: "101/101/111/001/001", 5: "111/100/111/001/111", 6: "111/100/111/101/111",
    7: "111/001/001/010/010", 8: "111/101/111/101/111", 9: "111/101/111/001/111",
    " ": "00/00/00/00/00", ".": "0/0/0/0/1", ",": "00/00/00/01/10", ":": "0/1/0/1/0", "!": "1/1/1/0/1",
    "?": "110/001/010/000/010", "-": "000/000/111/000/000", "+": "000/010/111/010/000",
    "/": "001/001/010/100/100", "'": "1/1/0/0/0", "(": "01/10/10/10/01", ")": "10/01/01/01/10",
    "[": "11/10/10/10/11", "]": "11/01/01/01/11", "#": "101/111/101/111/101", "%": "101/001/010/100/101",
    "&": "010/101/010/101/011", "·": "0/0/1/0/0", "×": "000/101/010/101/000",
    "→": "0010/0001/1111/0001/0010", "←": "0100/1000/1111/1000/0100", "↑": "010/111/010/010/010",
    "↓": "010/010/010/111/010", "✦": "00100/01110/11111/01110/00100", "♥": "01010/11111/11111/01110/00100"
  };
  const glyph = ch => (FONT[ch] || FONT["?"]).split("/");
  const textCache = new Map();
  function textArt(str, color, outline, scale) {
    const key = str + "\u0001" + color + "\u0001" + (outline || "") + "\u0001" + scale;
    let c = textCache.get(key);
    if (c) return c;
    const chars = Array.from(String(str).toUpperCase()), pad = outline ? 1 : 0;
    const width = textWidth(str, 1);
    c = canvasOf((width + pad * 2) * scale, (5 + pad * 2) * scale);
    const g = pen(c);
    const stamp = (ox, oy, col) => {
      g.fillStyle = col;
      let x = 0;
      chars.forEach(ch => {
        const rows = glyph(ch);
        rows.forEach((r, y) => { for (let i = 0; i < r.length; i++) if (r[i] === "1") g.fillRect((pad + x + i + ox) * scale, (pad + y + oy) * scale, scale, scale); });
        x += rows[0].length + 1;
      });
    };
    if (outline) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) if (dx || dy) stamp(dx, dy, outline);
    stamp(0, 0, color);
    if (textCache.size > 600) textCache.clear();
    textCache.set(key, c);
    return c;
  }
  function textWidth(str, scale) {
    let wd = 0;
    Array.from(String(str).toUpperCase()).forEach((ch, i) => { wd += glyph(ch)[0].length + (i ? 1 : 0); });
    return wd * (scale || 1);
  }
  /* (x, y) is the top-left of the letters; an outline hangs one pixel outside */
  function write(g, str, x, y, color, o) {
    o = o || {};
    const sc = o.scale || 1, img = textArt(str, color, o.outline, sc), pad = o.outline ? sc : 0;
    let left = x - pad;
    if (o.align === "center") left = x - Math.floor(img.width / 2);
    else if (o.align === "right") left = x - img.width + pad;
    g.drawImage(img, Math.round(left), Math.round(y - pad));
    return img.width - pad * 2;
  }

  /* ---------- riders: one look per player, built once and cached ---------- */
  const SKINS = [["#f1c7a5", "#d9a27a"], ["#e3b08a", "#c48c66"], ["#c08a62", "#9c6a46"], ["#8d5b3c", "#6c432b"], ["#5f3b27", "#47291b"]];
  const HAIRS = [["#2b1d14", "#160e09"], ["#5a3825", "#3a2418"], ["#a0602b", "#7a4720"], ["#e0b34a", "#b8902f"],
    ["#1c1c28", "#0d0d16"], ["#8a3a2e", "#62271f"], ["#c9c9d6", "#9a9aa8"]];
  const PANTS = [["#3e5f8a", "#2b4466"], ["#2b3550", "#1d2438"], ["#5c6a70", "#414c51"], ["#9c8b6a", "#7a6c50"], ["#253559", "#182340"]];
  const BAGS = [["#2f6fb0", "#1f4f82"], ["#3a4660", "#252f44"], ["#d6453d", "#9e2f2a"], ["#237b55", "#175239"],
    ["#8b4381", "#5e2a58"], ["#c9a53a", "#94781f"]];
  const STYLES = ["short", "long", "cap", "bun"];
  const NPC_SHIRTS = ["#4f5f7e", "#6f7560", "#ae6f48", "#4a8594", "#7d5a8c", "#9a6b3d", "#5b7f5a", "#a04f5a", "#c9c3b4", "#3a4660"];
  const FEET = {stand: 5, walk1: 5, walk2: 5, jump: 5, hurt: 5, sit: 5, tumble: 7};

  function lookOf(key, color) {
    const h = hash(strHash(String(key)));
    return {key: key + "|" + color, color: color || "#ff791f", skin: SKINS[h % SKINS.length],
      hair: HAIRS[(h >>> 4) % HAIRS.length], pants: PANTS[(h >>> 8) % PANTS.length],
      bag: BAGS[(h >>> 12) % BAGS.length], style: STYLES[(h >>> 16) % STYLES.length]};
  }
  function lookPalette(look, closedEyes) {
    return {o: INK, e: closedEyes ? look.skin[1] : INK, h: look.hair[0], j: look.hair[1], s: look.skin[0], t: look.skin[1],
      m: mix(look.skin[1], "#7a2f2a", .35), c: look.color, d: mix(look.color, INK, .32), l: lighten(look.color, .4),
      p: look.pants[0], q: look.pants[1], k: "#f3f1ea", K: "#9aa6af", b: look.bag[0], n: look.bag[1]};
  }
  function composeFrame(body, look, pal) {
    const head = HEADS[look.style];
    const headArt = paint(head.rows, Object.assign({}, pal, {c: look.bag[0], l: lighten(look.bag[0], .35)}));
    const bodyArt = paint(body.rows, pal);
    const hy = body.head[1] + head.y, shift = Math.max(0, -hy);
    const c = canvasOf(Math.max(bodyArt.width, body.head[0] + headArt.width), bodyArt.height + shift), g = pen(c);
    g.drawImage(bodyArt, 0, shift);
    g.drawImage(headArt, body.head[0], hy + shift);
    return {c, shift};
  }
  const riderCache = new Map();
  function riderArt(look) {
    let art = riderCache.get(look.key);
    if (art) return art;
    const pal = lookPalette(look);
    art = {};
    for (const [name, body] of Object.entries(BODIES)) {
      const {c} = composeFrame(body, look, pal);
      art[name] = {r: c, l: mirror(c), ax: FEET[name]};
    }
    art.flash = silhouette(art.hurt.r, "#ffffff");
    art.spin = [0, 1, 2, 3].map(q => turn(art.tumble.r, q));
    const half = paint(MINI.half, pal), dot = paint(MINI.dot, pal);
    art.halfSpin = [0, 1, 2, 3].map(q => turn(half, q));
    art.dotSpin = [0, 1, 2, 3].map(q => turn(dot, q));
    art.avatar = art.stand.r;
    riderCache.set(look.key, art);
    return art;
  }
  /* seated passengers: 24 faces, each doing one thing with their hands */
  const npcCache = new Map();
  function npcArt(seed) {
    const h = hash(seed), variant = h % 24, prop = ["phone", "paper", "sleep", "phones", "cup", "none"][(h >>> 5) % 6];
    const key = variant + ":" + prop;
    let art = npcCache.get(key);
    if (art) return art;
    const v = hash(variant * 7919 + 13);
    const look = {key, color: NPC_SHIRTS[v % NPC_SHIRTS.length], skin: SKINS[(v >>> 4) % SKINS.length],
      hair: HAIRS[(v >>> 8) % HAIRS.length], pants: PANTS[(v >>> 12) % PANTS.length],
      bag: BAGS[(v >>> 16) % BAGS.length], style: ["short", "long", "bun", "short"][(v >>> 20) % 4]};
    const {c, shift} = composeFrame(BODIES.sit, look, lookPalette(look, prop === "sleep"));
    if (PROPS[prop]) {
      const p = PROPS[prop];
      pen(c).drawImage(paint(p.rows, p.pal), p.at[0], p.at[1] + shift);
    }
    art = {img: c, prop};
    npcCache.set(key, art);
    return art;
  }

  /* ---------- the aisle animals, one look per spot ---------- */
  const petCache = new Map();
  function cached(key, build) { let art = petCache.get(key); if (!art) { art = build(); petCache.set(key, art); } return art; }
  const both = c => ({l: c, r: mirror(c)});
  function dogArt(h) {
    const coat = h % DOG.coats.length, lead = (h >>> 4) % DOG.leashes.length;
    return cached("dog" + coat + ":" + lead, () => {
      const pal = Object.assign({n: INK, e: INK, c: DOG.leashes[lead], y: "#fede42", m: "#7a2f2a", t: "#f28ca8"}, DOG.coats[coat]);
      return {leash: DOG.leashes[lead], idle: both(outlined(DOG.idle, pal)), wag: both(outlined(DOG.wag, pal)), bark: both(outlined(DOG.bark, pal))};
    });
  }
  function catArt(h) {
    const shell = h % CARRIER.shells.length, kitty = (h >>> 4) % CARRIER.cats.length;
    return cached("cat" + shell + ":" + kitty, () => {
      const cat = CARRIER.cats[kitty], art = {eye: cat.g, paw: {},
        carrier: outlined(CARRIER.rows, Object.assign({h: "#9aa6af", k: "#9aa6af", d: "#1d2338", g: cat.g}, CARRIER.shells[shell]))};
      ["reach", "up", "down"].forEach(k => { art.paw[k] = both(outlined(PAW[k], {c: cat.c, w: "#ffffff"})); });
      return art;
    });
  }
  function turkeyArt() {
    return cached("turkey", () => {
      const art = {};
      ["idle", "peck", "up", "down", "mid"].forEach(f => { art[f] = both(outlined(TURKEY[f], TURKEY.pal)); });
      return art;
    });
  }

  /* ---------- fixed sprites ---------- */
  function fixedArt() {
    const out = {seat: paint(SEAT, SEAT_PAL), twinkle: TWINKLE.map(f => paint(f, {W: "#ffffff", w: "#fff6c8", y: "#fede42"}))};
    for (const [k, v] of Object.entries(OBST)) { out[k] = paint(v.rows, v.pal); }
    out.seatDim = silhouette(out.seat, "#000000");
    return out;
  }

  /* ---------- the carriage shell, painted once per car type ----------
     A cutaway: the far wall with its windows punched out (the harbour is
     painted behind it each frame and shows through), seats left out
     (they are drawn live, because who sits where changes). */
  function buildShell(seatX, cab) {
    const top = LAY.shellTop, c = canvasOf(CARPX + (cab ? 36 : 0), -top), g = pen(c);
    const R = (x, y, wd, ht, col) => { g.fillStyle = col; g.fillRect(x, y - top, wd, ht); };
    const clear = (x, y, wd, ht) => g.clearRect(x, y - top, wd, ht);
    const decks = cab ? [[LAY.lowCeil, LAY.lowFloor]] : [[LAY.upCeil, LAY.upFloor], [LAY.lowCeil, LAY.lowFloor]];
    decks.forEach(([ceil, fl]) => {
      R(0, ceil, CARPX, fl - ceil, PAL.wall);
      R(0, ceil, CARPX, 1, PAL.ceil);
      for (let x = 6; x < CARPX - 6; x += 18) R(x, ceil + 1, 14, 1, PAL.lamp);
      R(0, ceil + 2, CARPX, 1, PAL.wallLo);
      /* luggage rack */
      R(30, ceil + 3, CARPX - 60, 1, PAL.rack);
      R(30, ceil + 4, CARPX - 60, 1, PAL.wallLo);
      for (let x = 32; x < CARPX - 30; x += 34) R(x, ceil + 3, 1, 3, PAL.rackDk);
      /* windows, one per seat bay; the glass is a hole in the wall */
      seatX.slice(0, 7).forEach(sx => {
        const wx = sx - 9, wy = fl - 33, ww = 20, wh = 18;
        R(wx - 1, wy - 1, ww + 2, wh + 2, PAL.frame);
        clear(wx, wy, ww, wh);
        [[wx, wy], [wx + ww - 1, wy], [wx, wy + wh - 1], [wx + ww - 1, wy + wh - 1]].forEach(([x, y]) => R(x, y, 1, 1, PAL.frame));
        R(wx - 1, wy + wh + 1, ww + 2, 1, PAL.sill); R(wx - 1, wy + wh + 2, ww + 2, 1, PAL.sillDk);
        g.fillStyle = "rgba(214,236,255,.10)"; g.fillRect(wx, wy - top, ww, wh);
        g.fillStyle = "rgba(255,255,255,.26)";
        for (let k = 0; k < 7; k++) { g.fillRect(wx + 3 + k, wy + 10 - k - top, 1, 1); g.fillRect(wx + 6 + k, wy + 13 - k - top, 1, 1); }
      });
      /* wainscot and heater grilles under the windows */
      R(0, fl - 12, CARPX, 1, PAL.trim);
      R(0, fl - 11, CARPX, 11, PAL.wains);
      for (const sx of seatX.slice(0, 7)) { for (let k = 0; k < 4; k++) R(sx - 6 + k * 4, fl - 6, 2, 3, PAL.wainsDk); }
      /* grab poles by the stairs */
      [33, CARPX - 34].forEach(x => { R(x, ceil + 3, 1, fl - ceil - 3, PAL.pole); R(x + 1, ceil + 3, 1, fl - ceil - 3, PAL.poleLo); });
    });
    /* posters between windows: a Beacons Connect ad, a station map, a lost umbrella */
    const poster = (x, fl, kind) => {
      const y = fl - 30;
      R(x - 1, y - 1, 10, 13, PAL.trim);
      if (kind === 0) { R(x, y, 8, 11, INK); R(x + 3, y + 2, 2, 5, PAL.gold); R(x + 2, y + 7, 4, 2, "#ffffff"); R(x + 1, y + 1, 1, 1, PAL.gold); R(x + 6, y + 3, 1, 1, PAL.gold); }
      else if (kind === 1) { R(x, y, 8, 11, "#ffffff"); R(x + 1, y + 5, 6, 1, PAL.purple); R(x + 1, y + 2, 1, 7, PAL.sky); R(x + 4, y + 2, 1, 3, PAL.orange); R(x + 6, y + 5, 1, 4, PAL.greenDk); }
      else { R(x, y, 8, 11, PAL.orange); R(x + 2, y + 2, 4, 2, "#ffffff"); R(x + 3, y + 4, 1, 5, "#ffffff"); R(x + 3, y + 8, 2, 1, "#ffffff"); }
    };
    if (!cab) {
      poster(Math.round((seatX[1] + seatX[2]) / 2) - 3, LAY.upFloor, 0);
      poster(Math.round((seatX[4] + seatX[5]) / 2) - 3, LAY.upFloor, 2);
    }
    poster(Math.round((seatX[3] + seatX[4]) / 2) - 3, LAY.lowFloor, 1);
    /* stairs at both ends of a double-deck car: steps rise toward the car end */
    if (!cab) {
      const flight = (x0, dir) => {
        R(dir > 0 ? x0 : x0 - 29, LAY.upFloor, 30, LAY.lowFloor - LAY.upFloor, PAL.well);
        for (let k = 0; k < 9; k++) {
          const ty = LAY.lowFloor - 5 * (k + 1), sx = dir > 0 ? x0 + 3 * k : x0 - 3 * (k + 1) + 1;
          R(sx, ty, 3, LAY.lowFloor - ty, PAL.stair);
          R(sx, ty, 3, 1, PAL.gold);
          R(dir > 0 ? sx : sx + 2, ty + 1, 1, LAY.lowFloor - ty - 1, PAL.stairLo);
        }
        for (let k = 0; k < 27; k++) {
          const x = dir > 0 ? x0 + k : x0 - k, y = LAY.lowFloor - 9 - Math.round(k * 5 / 3);
          R(x, y, 1, 1, PAL.pole);
        }
      };
      flight(291, 1);
      flight(29, -1);
    }
    /* the floor between decks, cut open so you can see it is a floor */
    if (!cab) {
      R(0, LAY.upFloor, CARPX, 1, PAL.floorHi); R(0, LAY.upFloor + 1, CARPX, 1, PAL.floor);
    } else {
      /* the cab car's upper level is crew-only machinery */
      R(0, LAY.upCeil, CARPX, LAY.upFloor - LAY.upCeil, "#4a5672");
      for (let x = 6; x < CARPX - 6; x += 26) {
        R(x, LAY.upCeil + 6, 22, 30, "#5b6986"); R(x, LAY.upCeil + 6, 22, 1, "#7b89a6");
        for (let k = 0; k < 6; k++) R(x + 3, LAY.upCeil + 11 + k * 4, 16, 1, "#3a4660");
        R(x + 18, LAY.upCeil + 9, 2, 2, "#2a3240");
      }
      R(0, LAY.upFloor, CARPX, 2, PAL.floorDk);
    }
    R(0, LAY.upFloor + 2, CARPX, 1, INK);
    R(0, LAY.upFloor + 3, CARPX, 1, PAL.steel);
    R(0, LAY.upFloor + 4, CARPX, 1, PAL.purple);
    /* roof, with a vent or two */
    R(0, LAY.roof, CARPX, 1, INK);
    R(0, LAY.roof + 1, CARPX, 2, PAL.steelHi);
    R(0, LAY.roof + 3, CARPX, 2, PAL.steel);
    R(0, LAY.roof + 5, CARPX, 1, PAL.steelLo);
    [70, 170, 250].forEach(x => { R(x, LAY.roof - 2, 14, 2, PAL.steelLo); R(x, LAY.roof - 3, 14, 1, INK); R(x + 1, LAY.roof - 2, 12, 1, PAL.steel); });
    clear(0, LAY.roof, 2, 1); clear(CARPX - 2, LAY.roof, 2, 1); clear(0, LAY.roof + 1, 1, 1); clear(CARPX - 1, LAY.roof + 1, 1, 1);
    /* lower floor and underframe */
    R(0, LAY.lowFloor, CARPX, 1, PAL.floorHi);
    R(0, LAY.lowFloor + 1, CARPX, 1, PAL.floor);
    R(0, LAY.lowFloor + 2, CARPX, 1, INK);
    R(0, LAY.lowFloor + 3, CARPX, 1, PAL.steel);
    R(0, LAY.lowFloor + 4, CARPX, 1, PAL.purple);
    R(0, LAY.lowFloor + 5, CARPX, 1, PAL.steelLo);
    R(0, LAY.lowFloor + 6, CARPX, 1, INK);
    [[78, 46], [140, 38], [196, 46]].forEach(([x, wd]) => { R(x, -5, wd, 4, "#3a4660"); R(x, -5, wd, 1, "#56637d"); R(x + 3, -3, wd - 6, 1, "#2a3240"); });
    /* the car ends */
    R(0, LAY.roof + 1, 1, LAY.lowFloor + 6 - LAY.roof, INK);
    R(CARPX - 1, LAY.roof + 1, 1, LAY.lowFloor + 6 - LAY.roof, INK);
    if (cab) {
      /* the nose: windscreen up top, headlight, then the stripe */
      const nx = CARPX;
      for (let y = LAY.roof; y < LAY.lowFloor + 7; y++) {
        const depth = y < LAY.upFloor ? Math.round((y - LAY.roof) * 0.42) : 23 + Math.round((y - LAY.upFloor) * 0.12);
        const wd = Math.min(34, depth);
        if (wd > 0) R(nx, y, wd, 1, PAL.steel);
        if (wd > 0) R(nx + wd - 1, y, 1, 1, INK);
      }
      for (let y = LAY.roof + 6; y < LAY.upFloor - 6; y++) {
        const wd = Math.round((y - LAY.roof) * 0.42) - 3;
        if (wd > 2) { R(nx + 1, y, wd - 1, 1, "#1d2c4f"); if ((y + wd) % 9 === 0) R(nx + wd - 4, y, 2, 1, "#8fb8e0"); }
      }
      R(nx, LAY.upFloor + 4, 27, 1, PAL.purple);
      R(nx, LAY.lowFloor + 4, 34, 1, PAL.purple);
      R(nx + 22, LAY.lowFloor - 14, 6, 4, PAL.gold); R(nx + 23, LAY.lowFloor - 13, 3, 2, "#ffffff");
      R(nx + 18, LAY.lowFloor - 30, 9, 5, "#1d2c4f"); write(g, "01", nx + 19, LAY.lowFloor - 30 - top, PAL.amber);
      R(nx + 30, LAY.lowFloor + 2, 4, 3, INK);
    }
    return c;
  }

  /* ---------- the Harborwalk, borrowed from the page itself ----------
     The page ground (templates/_harborwalk.html) already has the sky's
     clouds, the far shore, the ripples and the boats as SVG. Serialising
     them into small canvases means the train runs past exactly the
     harbour behind the page, pixel for pixel. If the markup is missing,
     the scene falls back to plain shapes. */
  function harborArt(onReady) {
    const out = {};
    if (!document.querySelector(".harborwalk .hw-defs")) return out;
    const ser = new XMLSerializer(), jobs = [];
    const bake = (name, ids, wd, ht, body) => {
      const els = ids.map(id => document.getElementById(id));
      if (els.some(el => !el)) return;
      const xml = '<svg xmlns="http://www.w3.org/2000/svg" width="' + wd + '" height="' + ht +
        '" shape-rendering="crispEdges"><defs>' + els.map(el => ser.serializeToString(el)).join("") + "</defs>" + body + "</svg>";
      const img = new Image();
      jobs.push(new Promise(done => {
        img.onload = () => { const c = canvasOf(wd, ht); pen(c).drawImage(img, 0, 0); out[name] = c; done(); };
        img.onerror = done;
      }));
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
    };
    const tile = (id, wd, ht) => bake(id, [id], wd, ht, '<rect width="' + wd + '" height="' + ht + '" fill="url(#' + id + ')"/>');
    tile("hw-shore", 640, 20); tile("hw-lit-a", 640, 20); tile("hw-lit-b", 640, 20); tile("hw-lit-c", 640, 20);
    tile("hw-rip-far", 320, 56); tile("hw-rip-near", 260, 28); tile("hw-reflect", 640, 6);
    [["hw-cloud-a", 76, 28], ["hw-cloud-b", 54, 22], ["hw-cloud-c", 48, 21], ["hw-cloud-d", 30, 13], ["hw-cloud-e", 70, 9], ["hw-cloud-f", 42, 7],
      ["hw-boat-far", 5, 4], ["hw-boat-fardark", 5, 3], ["hw-boat-motor", 7, 3], ["hw-boat-mid", 9, 9], ["hw-boat-near", 13, 14]]
      .forEach(([id, wd, ht]) => bake(id, [id], wd, ht, '<use href="#' + id + '" width="' + wd + '" height="' + ht + '"/>'));
    Promise.all(jobs).then(() => {
      /* night copies of the clouds, dimmed the way the Harborwalk dims its own */
      Object.keys(out).filter(k => k.startsWith("hw-cloud")).forEach(k => {
        const src = out[k], c = canvasOf(src.width, src.height), g = pen(c);
        g.drawImage(src, 0, 0); g.globalCompositeOperation = "source-atop";
        g.fillStyle = "rgba(16,26,58,.62)"; g.fillRect(0, 0, c.width, c.height);
        out[k + ":night"] = c;
      });
      /* the far shore at night: the Harborwalk's multiply tint, baked in */
      if (out["hw-shore"]) {
        const src = out["hw-shore"], c = canvasOf(src.width, src.height), g = pen(c);
        g.drawImage(src, 0, 0); g.globalCompositeOperation = "multiply";
        g.fillStyle = "#3e5079"; g.fillRect(0, 0, c.width, c.height);
        g.globalCompositeOperation = "destination-in"; g.drawImage(src, 0, 0);
        out["hw-shore:night"] = c;
      }
      out.ready = true;
      if (onReady) onReady();
    });
    return out;
  }

  /* ======================================================================
     3. THE PAGE
     ====================================================================== */
  const root = document.getElementById("t-party");
  if (!root) return;
  if (root.partyCleanup) root.partyCleanup();
  const $ = id => root.querySelector("#" + id);
  const embedded = document.getElementById("tp-world-data");
  const worldReady = embedded ? Promise.resolve(JSON.parse(embedded.textContent)) : fetch(root.dataset.world).then(r => {if (!r.ok) throw new Error("Train layout could not load."); return r.json();});
  worldReady.then(w => {
    if (!root.isConnected) return;
    const cleanup = boot(w);
    root.partyCleanup = cleanup;
    if (window.Beacon && Beacon.live) Beacon.live(() => cleanup);
  }).catch(e => { $("tp-error").hidden = false; $("tp-error").textContent = e.message; });

  function boot(w) {
    const canvas = $("tp-canvas"), ctx = canvas.getContext("2d", {alpha: false});
    const stage = $("tp-stage"), viewport = $("tp-viewport"), dpad = $("tp-dpad");
    const controller = new AbortController(), signal = controller.signal;
    const standalone = root.dataset.standalone === "true";
    const WPP = w.carWidth / CARPX, WORLD = w.carCount * CARPX, LAST = w.carCount - 1;
    const seatX = w.seatOffsets.map(o => Math.round(o / WPP));
    const shells = {bi: buildShell(seatX, false), cab: buildShell(seatX, true)};
    const fixed = fixedArt();
    const harbor = harborArt();
    const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = reducedQuery.matches;
    let touchMode = window.matchMedia("(pointer: coarse)").matches;

    let stopped = false, s = null, local = null, demo = null, networkTimer = null, raf = null;
    let view = {w: 240, h: 156, scale: 3};
    const cam = {x: 0, top: LAY.viewBottom - 156, set: false, shake: 0};
    let visualTime = 0, nightness = 0, watchId = null, show = null;
    let pets = {}, petClock = 0, petSoundAt = -9;
    let riders = {}, particles = [], floaters = [];
    let rosterKey = "", overlayKey = "", routeKey = "", lastPhase = "", warningId = -1, lastMySeat = null, lastGateOpen = -1;
    let busy = false, inflight = null, networkFailures = 0, sound = false, audio = null;
    let immersive = false, immersiveDismissed = false, visible = true;
    const idle = new LocalRide(w, root.dataset.name || "You", 28341, w.duration).s;
    const clockText = sec => String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0");
    const rideName = sec => sec >= 120 ? (["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"][Math.round(sec / 60)] || Math.round(sec / 60)) + "-minute ride" : sec + "-second ride";
    idle.phase = "idle"; idle.elapsed = 9;

    const on = (el, event, fn, extra) => el && el.addEventListener(event, fn, Object.assign({signal}, extra || {}));
    const setText = (id, text) => { const el = $(id); if (el && el.textContent !== text) el.textContent = text; };
    const error = message => { $("tp-error").hidden = !message; $("tp-error").textContent = message || ""; };
    const live = () => s && (s.phase === "countdown" || s.phase === "running");

    /* ---------- sound: a few square-wave chirps, off until asked for ---------- */
    function tone(freq, dur, o) {
      if (!sound || !audio || !s) return;
      o = o || {};
      const t0 = audio.currentTime + (o.delay || 0), osc = audio.createOscillator(), gain = audio.createGain();
      osc.type = o.type || "square";
      osc.frequency.setValueAtTime(freq, t0);
      if (o.to) osc.frequency.exponentialRampToValueAtTime(o.to, t0 + dur);
      gain.gain.setValueAtTime(o.vol || .025, t0);
      gain.gain.exponentialRampToValueAtTime(.0001, t0 + dur);
      osc.connect(gain).connect(audio.destination); osc.start(t0); osc.stop(t0 + dur + .02);
    }
    /* a burst of filtered noise, for hisses and the rough edge of a bark */
    let noiseBuffer = null;
    function noise(dur, o) {
      if (!sound || !audio || !s) return;
      o = o || {};
      if (!noiseBuffer) {
        noiseBuffer = audio.createBuffer(1, Math.floor(audio.sampleRate * .5), audio.sampleRate);
        const d = noiseBuffer.getChannelData(0);
        for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      }
      const t0 = audio.currentTime + (o.delay || 0), src = audio.createBufferSource(), filter = audio.createBiquadFilter(), gain = audio.createGain();
      src.buffer = noiseBuffer; filter.type = "bandpass"; filter.frequency.value = o.freq || 3000; filter.Q.value = o.q || 1;
      gain.gain.setValueAtTime(o.vol || .02, t0);
      gain.gain.exponentialRampToValueAtTime(.0001, t0 + dur);
      src.connect(filter).connect(gain).connect(audio.destination); src.start(t0); src.stop(t0 + dur + .02);
    }
    const sfx = {
      jump: () => tone(320, .08, {to: 520, vol: .02}),
      hit: () => tone(150, .2, {type: "sawtooth", to: 70, vol: .035}),
      seat: () => tone(520, .06, {vol: .018}),
      warn: () => { tone(660, .12); tone(660, .12, {delay: .18}); },
      door: () => { tone(784, .18, {type: "sine", vol: .045}); tone(587, .3, {type: "sine", vol: .045, delay: .18}); },
      launch: () => tone(170, .6, {type: "triangle", to: 1500, vol: .045}),
      ting: () => [1568, 2093, 2637].forEach((f, i) => tone(f, .24, {type: "sine", vol: .035, delay: i * .07})),
      swoop: () => tone(950, .55, {type: "triangle", to: 150, vol: .025}),
      win: () => [523, 659, 784, 1047].forEach((f, i) => tone(f, .18, {vol: .03, delay: i * .11})),
      bark: () => { tone(340, .08, {type: "sawtooth", to: 180, vol: .03}); noise(.07, {freq: 900, q: .8, vol: .025}); },
      hiss: () => noise(.34, {freq: 5200, q: .6, vol: .024}),
      gobble: () => { for (let i = 0; i < 10; i++) tone(i % 2 ? 390 - i * 9 : 590 - i * 14, .045, {delay: i * .036, vol: .022}); }
    };
    function buzz(pattern) { if (touchMode && s && navigator.vibrate) { try { navigator.vibrate(pattern); } catch (e) {} } }

    /* ---------- controls: the keyboard, and the thumb pad on phones ---------- */
    const keys = new Set(), press = {jump: false, act: false, stairs: 0};
    let padMove = 0, padStairs = 0, dpadId = null;
    const GAME_KEYS = new Set(["KeyA", "KeyD", "ArrowLeft", "ArrowRight", "Space", "KeyW", "KeyS", "ArrowUp", "ArrowDown", "KeyE"]);
    const heldMove = () => padMove || ((keys.has("KeyD") || keys.has("ArrowRight") ? 1 : 0) - (keys.has("KeyA") || keys.has("ArrowLeft") ? 1 : 0));
    /* Up or down held while walking into a stairwell takes the stairs on
       arrival, so a thumb never has to time a tap at the car end. */
    const heldStairs = () => padStairs || ((keys.has("KeyW") || keys.has("ArrowUp") ? 1 : 0) - (keys.has("KeyS") || keys.has("ArrowDown") ? 1 : 0));
    function takeInput() {
      const d = {move: heldMove(), jump: press.jump, act: press.act, stairs: press.stairs || heldStairs()};
      press.jump = false; press.act = false; press.stairs = 0;
      return d;
    }
    function setPadDir(mx, my) {
      padMove = mx; padStairs = -my;
      const name = (my < 0 ? "up" : my > 0 ? "down" : "") + (mx < 0 ? "left" : mx > 0 ? "right" : "");
      if (dpad.dataset.dir !== name) dpad.dataset.dir = name;
    }
    function clearInput() {
      keys.clear(); press.jump = press.act = false; press.stairs = 0; dpadId = null; setPadDir(0, 0);
      root.querySelectorAll(".is-down").forEach(b => b.classList.remove("is-down"));
    }
    function setTouchMode(yes) {
      if (touchMode === yes) return;
      touchMode = yes; root.classList.toggle("tp-touchmode", yes); overlayKey = ""; fitCanvas(); updateUI();
    }
    root.classList.toggle("tp-touchmode", touchMode);
    on(window, "touchstart", () => setTouchMode(true), {passive: true});
    const typing = t => t && (["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName) || t.isContentEditable);
    on(window, "keydown", e => {
      if (!s || typing(e.target) || e.ctrlKey || e.metaKey || e.altKey || !GAME_KEYS.has(e.code)) return;
      if (e.code === "Space" && e.target !== canvas && e.target.closest && e.target.closest("button, a, summary")) return;
      e.preventDefault(); keys.add(e.code);
      if (!e.repeat) {
        if (touchMode) setTouchMode(false);
        if (e.code === "Space") { press.jump = true; sfx.jump(); }
        if (e.code === "KeyE") press.act = true;
        if (e.code === "KeyW" || e.code === "ArrowUp") press.stairs = 1;
        if (e.code === "KeyS" || e.code === "ArrowDown") press.stairs = -1;
      }
    });
    on(window, "keyup", e => keys.delete(e.code));
    on(window, "blur", clearInput);
    on(document, "visibilitychange", () => { if (document.hidden) clearInput(); });
    /* The d-pad reads where the thumb is, not which arm it landed on, so
       you can roll from right to up-right to climb without lifting. */
    function dpadAt(e) {
      const r = dpad.getBoundingClientRect(), dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
      const dead = r.width * .13, ax = Math.abs(dx), ay = Math.abs(dy);
      const mx = ax > dead && ay < ax * 2.2 ? Math.sign(dx) : 0;
      const my = ay > dead && ax < ay * 1.15 ? Math.sign(dy) : 0;
      if (my && -my !== padStairs) press.stairs = -my;
      if ((mx || my) && (mx !== padMove || -my !== padStairs)) buzz(6);
      setPadDir(mx, my);
    }
    on(dpad, "pointerdown", e => { e.preventDefault(); setTouchMode(true); dpadId = e.pointerId; try { dpad.setPointerCapture(e.pointerId); } catch (x) {} dpadAt(e); });
    on(dpad, "pointermove", e => { if (e.pointerId === dpadId) dpadAt(e); });
    const dpadEnd = e => { if (e.pointerId === dpadId) { dpadId = null; setPadDir(0, 0); } };
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(ev => on(dpad, ev, dpadEnd));
    root.querySelectorAll("[data-btn]").forEach(b => {
      on(b, "pointerdown", e => {
        e.preventDefault(); setTouchMode(true);
        press[b.dataset.btn] = true; b.classList.add("is-down"); buzz(8);
        if (b.dataset.btn === "jump") sfx.jump();
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach(ev => on(b, ev, () => b.classList.remove("is-down")));
      on(b, "click", e => { if (e.detail === 0) press[b.dataset.btn] = true; });
    });
    on($("tp-pad"), "contextmenu", e => e.preventDefault());
    on(canvas, "contextmenu", e => { if (touchMode) e.preventDefault(); });

    /* ---------- rides: practice runs here, multiplayer trusts Flask ---------- */
    function resetScene() {
      riders = {}; particles = []; floaters = []; show = null; watchId = null; cam.set = false; pets = {};
      rosterKey = ""; overlayKey = ""; routeKey = ""; visualTime = s ? s.elapsed : 0; lastMySeat = null; lastGateOpen = -1;
    }
    function focusGame() {
      canvas.focus({preventScroll: true});
      if (!immersive && window.innerWidth < 850) viewport.scrollIntoView({block: "center", behavior: reduced ? "auto" : "smooth"});
    }
    function practice() {
      if (s && !local) return error("Leave your multiplayer room before starting practice.");
      error(""); clearInput();
      local = new LocalRide(w, root.dataset.name || "You", Math.floor(Math.random() * 900000), 90);
      s = local.s; immersiveDismissed = false; resetScene();
      if (touchMode) setImmersive(true, true);
      focusGame(); updateUI();
    }
    async function api(path, data) {
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), 8000);
      inflight = abort;
      try {
        const r = await fetch((root.dataset.api || "/api/t-party") + path, {method: data === undefined ? "GET" : "POST",
          credentials: "same-origin", cache: "no-store", signal: abort.signal,
          headers: {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
          body: data === undefined ? undefined : JSON.stringify(data)});
        let result;
        try { result = await r.json(); } catch (e) { throw new Error("The game could not connect. Check your sign-in and try again."); }
        if (!r.ok) { const e = new Error(result.error || "The train could not connect."); e.status = r.status; throw e; }
        return result;
      } finally { clearTimeout(timer); if (inflight === abort) inflight = null; }
    }
    async function perform(fn) {
      if (busy || stopped) return;
      busy = true; error("");
      root.querySelectorAll("#tp-create, #tp-practice, #tp-join-form button, #tp-start").forEach(b => b.disabled = true);
      try { await fn(); } catch (e) { if (!stopped) error(e.message); }
      finally { busy = false; if (!stopped) { updateUI(); configureAvailability(); } }
    }
    function configureAvailability() {
      $("tp-create").disabled = standalone || busy;
      $("tp-join-form").querySelector("button").disabled = standalone || busy;
      $("tp-code").disabled = standalone || busy;
      $("tp-practice").disabled = busy;
    }
    function accept(state) {
      const fresh = !s || s.code !== state.code || !!local;
      s = state; local = null; error(""); networkFailures = 0;
      if (fresh) { immersiveDismissed = false; resetScene(); }
      updateUI();
    }
    function startSync() {
      clearTimeout(networkTimer);
      async function tickNet() {
        if (stopped || !s || local) return;
        const code = s.code;
        if (busy) { networkTimer = setTimeout(tickNet, 100); return; }
        const sent = takeInput();
        try { const next = await api("/rooms/" + code + "/sync", sent); if (!stopped && s && s.code === code && !local) accept(next); }
        catch (e) {
          if (stopped) return;
          networkFailures++;
          error(networkFailures > 1 ? "Connection interrupted. Movement is stopped; reconnecting…" : e.message);
          if ([401, 403, 404].includes(e.status)) { s = null; clearInput(); setImmersive(false); updateUI(); return; }
        }
        if (!stopped && s && !local) networkTimer = setTimeout(tickNet, networkFailures ? Math.min(2000, networkFailures * 300) : 100);
      }
      networkTimer = setTimeout(tickNet, 100);
    }
    async function leave() {
      if (s && !local) await api("/rooms/" + s.code + "/leave", {});
      clearTimeout(networkTimer); local = null; s = null; clearInput(); setImmersive(false); resetScene(); updateUI();
    }
    on($("tp-practice"), "click", practice);
    on($("tp-create"), "click", () => perform(async () => { accept(await api("/rooms", {duration: Number($("tp-duration").value)})); startSync(); focusGame(); }));
    on($("tp-join-form"), "submit", e => { e.preventDefault(); e.stopImmediatePropagation(); perform(async () => {
      const code = $("tp-code").value.trim().toUpperCase();
      if (!/^[A-Z0-9]{6}$/.test(code)) throw new Error("Enter the six-character room code.");
      accept(await api("/rooms/" + code + "/join", {})); startSync(); focusGame();
    }); });
    on($("tp-start"), "click", () => { if (touchMode) setImmersive(true, true); perform(async () => { accept(await api("/rooms/" + s.code + "/start", {})); focusGame(); }); });
    on($("tp-leave"), "click", () => perform(leave));
    on($("tp-copy"), "click", async () => { try { await navigator.clipboard.writeText(s.code); setText("tp-copy", "Copied"); } catch (e) { error("Your room code is " + s.code + ". Select it to copy."); } });
    on($("tp-next-rider"), "click", () => {
      const alive = Object.values((s || idle).players).filter(p => p.hp > 0);
      if (!alive.length) return;
      watchId = alive[(alive.findIndex(p => p.id === watchId) + 1) % alive.length].id;
      canvas.focus({preventScroll: true});
    });
    on($("tp-sound"), "click", () => {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return error("This browser does not support game sound.");
      if (!audio) audio = new Ctx();
      audio.resume(); sound = !sound;
      $("tp-sound").setAttribute("aria-pressed", String(sound));
      $("tp-sound").setAttribute("aria-label", sound ? "Mute game sound" : "Turn game sound on");
      $("tp-sound").classList.toggle("is-on", sound);
      if (sound && s) tone(440, .1);
    });
    on($("tp-fullscreen"), "click", () => setImmersive(true, true));
    on($("tp-exit"), "click", () => { immersiveDismissed = true; setImmersive(false); });
    on(canvas, "pointerdown", () => canvas.focus({preventScroll: true}));

    /* ---------- the screen: whole device pixels per art pixel ---------- */
    function fitCanvas() {
      const dpr = window.devicePixelRatio || 1;
      const availW = Math.max(150, viewport.clientWidth);
      let target, artH, scale;
      if (immersive) {
        const availH = Math.max(120, viewport.clientHeight);
        const minW = availW >= 900 ? 300 : 150;
        target = Math.max(1, Math.min(Math.floor(availW / minW), Math.floor(availH / 140)));
        scale = Math.max(1, Math.round(target * dpr)) / dpr;
        artH = clamp(Math.floor(availH / scale), 120, 236);
      } else {
        target = availW >= 560 ? 3 : 2;
        scale = Math.max(1, Math.round(target * dpr)) / dpr;
        artH = availW >= 560 ? 156 : 168;
      }
      const artW = Math.max(120, Math.floor(availW / scale));
      if (canvas.width !== artW || canvas.height !== artH) { canvas.width = artW; canvas.height = artH; }
      ctx.imageSmoothingEnabled = false;
      canvas.style.width = artW * scale + "px"; canvas.style.height = artH * scale + "px";
      view = {w: artW, h: artH, scale};
    }
    /* On a phone the ride takes the whole screen, over the top bar and the
       taskbar, with the thumb pad where thumbs are. The Fullscreen API is
       asked for when there is a tap to ask with; iPhones say no, and the
       fixed layer below does the job on its own. */
    function setImmersive(yes, gesture) {
      if (immersive === yes) { if (yes && gesture) requestFull(); return; }
      immersive = yes;
      stage.classList.toggle("is-immersive", yes);
      document.documentElement.classList.toggle("tp-locked", yes);
      $("tp-exit").hidden = !yes; $("tp-fullscreen").hidden = yes;
      if (yes && gesture) requestFull();
      if (!yes && document.fullscreenElement === stage) document.exitFullscreen().catch(() => {});
      requestAnimationFrame(fitCanvas);
    }
    function requestFull() {
      if (!document.fullscreenElement && stage.requestFullscreen) stage.requestFullscreen({navigationUI: "hide"}).catch(() => {});
    }
    on(document, "fullscreenchange", () => {
      if (!document.fullscreenElement && immersive && !touchMode) setImmersive(false);
      requestAnimationFrame(fitCanvas);
    });
    const resize = new ResizeObserver(() => fitCanvas());
    resize.observe(viewport);
    on(window, "orientationchange", () => setTimeout(fitCanvas, 120));
    const seen = window.IntersectionObserver ? new IntersectionObserver(list => { visible = list[0].isIntersecting; }) : null;
    if (seen) seen.observe(canvas);
    if (reducedQuery.addEventListener) on(reducedQuery, "change", () => { reduced = reducedQuery.matches; });
    if (standalone) { configureAvailability(); setText("tp-network-note", "Party rooms with other riders are coming soon. Ride solo with three bots for now."); }
    else api("/current").then(r => { if (!stopped && r.room) { accept(r.room); startSync(); } }).catch(e => { if (!stopped) error(e.message); });

    /* ---------- who the camera watches ---------- */
    const normTop = () => LAY.viewBottom - view.h;
    const clampX = x => clamp(x, -16, WORLD + 40 - view.w);
    const firstName = n => (String(n || "Rider").split(/\s+/)[0] || "Rider").slice(0, 9);
    function focusRider(st) {
      const me = st.players[st.you];
      if (s && me && me.hp > 0) return me;
      const alive = Object.values(st.players).filter(p => p.hp > 0);
      if (!alive.length) return me || null;
      if (watchId && st.players[watchId] && st.players[watchId].hp > 0) return st.players[watchId];
      return alive.reduce((a, b) => b.x > a.x ? b : a);
    }
    function followTarget(st) {
      const p = focusRider(st);
      if (!p) return {x: cam.x, top: normTop()};
      return {x: clampX(riderState(p).x - view.w * .4), top: normTop()};
    }
    function followCamera(st, dt) {
      const t = followTarget(st);
      if (!cam.set) { cam.x = t.x; cam.top = t.top; cam.set = true; }
      cam.x += (t.x - cam.x) * Math.min(1, dt * 7);
      cam.top += (t.top - cam.top) * Math.min(1, dt * 6);
      if (Math.abs(t.x - cam.x) > view.w * 1.6) cam.x = t.x;
    }

    /* ---------- every rider's on-screen self, smoothed between snapshots ---------- */
    function riderState(p) {
      let r = riders[p.id];
      if (!r) r = riders[p.id] = {x: p.x / WPP, y: p.y / WPP, deck: p.deck, floor: floorY(p.deck), from: floorY(p.deck),
        stairT: 1, face: 1, dist: 0, hp: p.hp, air: false, moving: false, look: lookOf(p.id, p.color)};
      return r;
    }
    function updateRiders(st, dt) {
      Object.values(st.players).forEach(p => {
        const r = riderState(p), tx = p.x / WPP, k = Math.min(1, dt * 18);
        if (r.look.color !== p.color) r.look = lookOf(p.id, p.color);
        const step = Math.abs(tx - r.x) > 70 ? tx - r.x : (tx - r.x) * k;
        r.x += step; r.dist += Math.abs(step);
        r.moving = Math.abs(step) > .012 && p.seat == null && p.y <= 0;
        if (Math.abs(step) > .012) r.face = step > 0 ? 1 : -1; else if (p.dir) r.face = p.dir;
        r.y += (p.y / WPP - r.y) * Math.min(1, dt * 22);
        if (p.deck !== r.deck) { r.from = r.floor; r.deck = p.deck; r.stairT = reduced ? 1 : 0; }
        r.stairT = Math.min(1, r.stairT + dt / .3);
        r.floor = lerp(r.from, floorY(p.deck), ease(r.stairT));
        if (p.hp < r.hp) { hitFx(p, r, st); petHit(p, st); }
        r.hp = p.hp;
        const air = p.y > 2;
        if (r.air && !air && p.hp > 0) dust(r.x, r.floor);
        r.air = air;
      });
    }

    /* ---------- little bits: sparks, dust, debris ---------- */
    function particle(x, y, vx, vy, col, life, grav, size) { particles.push({x, y, vx, vy, col, life, max: life, g: grav || 0, size: size || 1}); }
    function hitFx(p, r, st) {
      const x = r.x, y = r.floor - r.y - 11;
      for (let k = 0; k < 8; k++) { const a = k / 8 * Math.PI * 2; particle(x, y, Math.cos(a) * 34, Math.sin(a) * 34 - 10, k % 2 ? PAL.gold : "#ffffff", .32, 0); }
      if (p.hp > 0) floaters.push({text: "-1", x, y: y - 8, life: .9, col: "#ff5a4a"});
      if (s && p.id === s.you) { cam.shake = reduced ? 0 : 3; buzz([25, 30, 25]); }
      sfx.hit();
    }
    function dust(x, fl) { for (let k = 0; k < 4; k++) particle(x + (k - 1.5) * 2, fl - 1, (k - 1.5) * 9, -6, "#c9cbc3", .3, 30); }
    function burst(x, y) {
      for (let k = 0; k < 16; k++) {
        const a = -Math.PI * (k / 15), sp = 40 + (hash(k * 31 + Math.round(x)) % 50);
        particle(x + (k % 5) - 2, y, Math.cos(a) * sp * .8, Math.sin(a) * sp - 20, [PAL.steelHi, PAL.steel, PAL.steelLo, PAL.purple, INK][k % 5], .9, 140, k % 3 ? 1 : 2);
      }
      cam.shake = reduced ? 0 : Math.max(cam.shake, 2);
    }
    function sparkle(x, y) { particle(x + (Math.random() * 6 - 3), y + (Math.random() * 6 - 3), 0, 6, ["#ffffff", PAL.gold, PAL.sky][Math.floor(Math.random() * 3)], .5, 0); }
    function updateParticles(dt) {
      particles.forEach(q => { q.vy += q.g * dt; q.x += q.vx * dt; q.y += q.vy * dt; q.life -= dt; });
      particles = particles.filter(q => q.life > 0);
      floaters.forEach(f => { f.y -= 14 * dt; f.life -= dt; });
      floaters = floaters.filter(f => f.life > 0);
    }

    /* ======================================================================
       THE SEND-OFF
       When a rider loses their last heart the ride pauses for everyone (the
       rules freeze the whole simulation for cinematicSeconds). Every client
       plays the same shot from the same timeline:
         a  freeze: the train greys out, bars slide in, the camera finds them
         b  launch: up through the ceiling and out of the roof
         c  arc:    up and back, shrinking, the camera chasing a beat behind
         d  twinkle
         e  return: the camera drops back to the riders still aboard
       Times are fractions of cinematicSeconds, so a server that sends a
       different length still gets the whole sequence.
       ====================================================================== */
    const PH = {b: .14, c: .24, d: .66, e: .78};
    function startShow(c) {
      const total = Math.max(1.6, Number(w.cinematicSeconds) || 3.2);
      show = {id: c.id, total, t: clamp(total - c.remaining, 0, total - .001), victims: [], fromX: cam.x, fromTop: cam.top,
        holdX: cam.x, holdTop: cam.top, vx: 0, vy: 0, fired: {}};
      c.victims.forEach(addVictim);
      clearInput();
      sfx.hit();
    }
    function addVictim(v) {
      const i = show.victims.length, r = riders[v.id], x0 = r ? r.x : v.x / WPP;
      const baseY = (r ? Math.round(r.floor - r.y) : floorY(v.deck)) - 9;
      show.victims.push({id: v.id, name: firstName(v.name), color: v.color, deck: v.deck, x0, x: x0, y: baseY, baseY,
        art: riderArt(lookOf(v.id, v.color)), dx: -(118 + 40 * i), rise: 255 + 28 * i, lastY: null, tier: 0, trail: 0, holes: []});
    }
    function fire(name, when, fn) { if (when && !show.fired[name]) { show.fired[name] = true; fn(); } }
    function updateShow(st, dt) {
      const c = st.cinematic;
      if (c && (!show || show.id !== c.id)) startShow(c);
      if (!show) return;
      if (c && c.id === show.id) {
        c.victims.forEach(v => { if (!show.victims.some(x => x.id === v.id)) addVictim(v); });
        show.t = clamp(Math.max(show.t + dt, show.total - c.remaining - .2), 0, show.total - .001);
      } else {
        /* the rules already resumed: finish the sequence quickly */
        show.t += dt * (show.t < show.total * PH.e ? 3 : 1.8);
        if (show.t >= show.total) { show = null; return; }
      }
      const u = show.t / show.total;
      fire("launch", u >= PH.b, () => { sfx.launch(); if (s && show.victims.some(v => v.id === s.you)) buzz([30, 40, 80]); });
      fire("ting", u >= PH.d, sfx.ting);
      fire("swoop", u >= PH.e, sfx.swoop);
      show.victims.forEach(v => placeVictim(v, u, dt));
      showCamera(st, u, dt);
    }
    /* where a rider is at time u: straight up through the roof, then an arc
       up and back (the train keeps going without them), shrinking with
       distance in steps so every size is still drawn on the pixel grid */
    function victimAt(v, u) {
      const startY = v.baseY, roofY = LAY.roof - 10;
      if (u < PH.b) return {x: v.x0, y: startY, tier: 0};
      if (u < PH.c) { const b = (u - PH.b) / (PH.c - PH.b); return {x: v.x0, y: lerp(startY, roofY, b * b), tier: 0}; }
      const k = clamp((u - PH.c) / (PH.d - PH.c), 0, 1);
      return {x: v.x0 + v.dx * Math.pow(k, 1.6), y: roofY - v.rise * (1 - (1 - k) * (1 - k)), tier: k < .42 ? 0 : k < .68 ? 1 : k < .9 ? 2 : 3};
    }
    function placeVictim(v, u, dt) {
      const at = victimAt(v, u), x = at.x, y = at.y, tier = at.tier;
      if (v.lastY !== null) (v.deck ? [LAY.roof] : [LAY.lowCeil, LAY.roof]).forEach(cy => {
        if (v.lastY > cy && y <= cy) { burst(x, cy); v.holes.push(cy); }
      });
      if (u >= PH.b && u < PH.d && !reduced) { v.trail -= dt; if (v.trail <= 0) { v.trail = .03; sparkle(x, y); } }
      v.lastY = y; v.x = x; v.y = y; v.tier = tier;
    }
    function showCamera(st, u, dt) {
      const vs = show.victims, nt = normTop();
      const cx = vs.reduce((a, v) => a + v.x, 0) / vs.length, cy = vs.reduce((a, v) => a + v.y, 0) / vs.length;
      const loose = x => clamp(x, -view.w, WORLD + view.w);
      if (u < PH.b) {
        const a = reduced ? 1 : ease(u / PH.b);
        cam.x = lerp(show.fromX, clampX(cx - view.w / 2), a); cam.top = lerp(show.fromTop, nt, a);
        show.holdX = cam.x; show.holdTop = cam.top; show.vx = show.vy = 0;
      } else if (u < PH.e) {
        if (reduced) {
          /* a still shot of the top of the arc, cut to rather than flown to */
          const v = vs[0];
          cam.x = loose(v.x0 + v.dx * .8 - view.w / 2);
          cam.top = Math.min(nt, LAY.roof - 10 - v.rise * .82 - view.h * .5);
        } else {
          /* a critically damped spring: the camera chases the rider a beat
             behind, which is what lets you see the arc on screen */
          /* aim a little ahead along the path, so the rider stays in frame */
          const ahead = vs.map(v => victimAt(v, Math.min(PH.d, u + .045)));
          const ax = (cx + ahead.reduce((a, p) => a + p.x, 0) / vs.length) / 2, ay = (cy + ahead.reduce((a, p) => a + p.y, 0) / vs.length) / 2;
          const tx = loose(ax - view.w / 2), ty = Math.min(nt, ay - view.h * .5), k = 40, damp = 2 * Math.sqrt(k);
          let left = Math.min(dt, .1);
          while (left > 0) {
            const h = Math.min(left, 1 / 120);
            show.vx += (k * (tx - cam.x) - damp * show.vx) * h; show.vy += (k * (ty - cam.top) - damp * show.vy) * h;
            cam.x += show.vx * h; cam.top += show.vy * h; left -= h;
          }
          cam.top = Math.min(cam.top, nt);
        }
        show.holdX = cam.x; show.holdTop = cam.top;
      } else {
        const back = followTarget(st), e = reduced ? 1 : ease((u - PH.e) / (1 - PH.e));
        cam.x = lerp(show.holdX, back.x, e); cam.top = lerp(show.holdTop, back.top, e);
      }
    }
    function showAmount() {
      if (!show) return {freeze: 0, bars: 0, u: 1};
      const u = show.t / show.total;
      return {u, freeze: smooth(u / .06) * (1 - smooth((u - .93) / .07)), bars: smooth(u / .07) * (1 - smooth((u - .9) / .1))};
    }

    /* ======================================================================
       PAINTING
       Back to front: sky, harbour, track, (night tint), lamps, carriages,
       seats and passengers, the mess in the aisle, riders, sparks, the
       send-off, prompts, letterbox.
       ====================================================================== */
    const SKY = (() => {
      const day = [[0, "#d3e6f8"], [3, "#c3ddf6"], [7, "#b1d3f4"], [12, "#9fc9f1"], [18, "#93c2ef"], [70, "#69a5e6"], [150, "#4a8ede"], [330, "#3a78cc"], [720, "#2a5fae"]];
      const night = [[0, "#3a4f80"], [3, "#33477a"], [7, "#2c4072"], [12, "#26396a"], [18, "#26396a"], [80, "#13234c"], [160, "#0a1430"], [400, "#070e24"], [720, "#050a1a"]];
      const build = stops => {
        const out = [];
        for (let d = 0; d <= 720; d++) {
          let i = 0;
          while (i < stops.length - 2 && d >= stops[i + 1][0]) i++;
          const [d0, c0] = stops[i], [d1, c1] = stops[i + 1];
          out.push(rgb(mix(c0, c1, d < 18 ? 0 : Math.floor((d - d0) / (d1 - d0) * 7) / 7)));
        }
        return out;
      };
      return {day: build(day), night: build(night)};
    })();
    function skyAt(d, N) {
      d = clamp(Math.floor(d), 0, 720);
      const a = SKY.day[d], b = SKY.night[d], n = Math.round(N * 24) / 24;
      return hex(a[0] + (b[0] - a[0]) * n, a[1] + (b[1] - a[1]) * n, a[2] + (b[2] - a[2]) * n);
    }
    const STARS = Array.from({length: 80}, (_, i) => { const h = hash(i * 977 + 5); return {x: h % 960, d: 24 + (h >>> 10) % 620, kind: (h >>> 20) % 10, tw: ((h >>> 24) % 10) / 3}; });
    const CLOUDS = [["a", 40, 70, .05, .5], ["b", 300, 48, .045, .45], ["d", 520, 96, .04, .55], ["c", 690, 60, .05, .5], ["e", 160, 126, .03, .62],
      ["f", 820, 116, .03, .6], ["b", 110, 176, .06, .7], ["a", 610, 214, .055, .74], ["c", 380, 262, .05, .78], ["d", 860, 300, .04, .8],
      ["a", 230, 352, .06, .84], ["e", 520, 398, .035, .86], ["b", 760, 446, .05, .88], ["c", 60, 492, .045, .9], ["d", 420, 540, .04, .9]];
    const BOATS = [["hw-boat-far", 60, 2, .08], ["hw-boat-fardark", 260, 3, .08], ["hw-boat-far", 520, 1, .08], ["hw-boat-motor", 380, 4, .09],
      ["hw-boat-mid", 120, 24, .14], ["hw-boat-near", 330, 29, .2], ["hw-boat-mid", 600, 26, .15], ["hw-boat-motor", 470, 33, .17],
      ["hw-boat-near", 820, 31, .21], ["hw-boat-mid", 900, 36, .19]];
    const tiles = {ballast: null, granite: null, glow: null, wheel: null};
    (function bakeTiles() {
      let c = canvasOf(64, 6), g = pen(c);
      g.fillStyle = PAL.ballast; g.fillRect(0, 0, 64, 6);
      for (let i = 0; i < 90; i++) { const h = hash(i * 131 + 7); g.fillStyle = [PAL.ballastLo, PAL.ballastHi, "#8a9899"][h % 3]; g.fillRect(h % 64, (h >>> 8) % 6, 1 + (h >>> 12) % 2, 1); }
      tiles.ballast = c;
      c = canvasOf(48, 12); g = pen(c);
      g.fillStyle = PAL.granite; g.fillRect(0, 0, 48, 12);
      for (let row = 0; row < 2; row++) for (let k = 0; k < 4; k++) {
        const x = k * 12 + (row ? 6 : 0), y = row * 6;
        g.fillStyle = PAL.graniteLo; g.fillRect(x % 48, y, 1, 6); g.fillRect(0, y + 5, 48, 1);
        g.fillStyle = PAL.graniteHi; g.fillRect((x + 1) % 48, y, 10, 1);
      }
      tiles.granite = c;
      c = canvasOf(48, 28); g = c.getContext("2d");
      const grad = g.createRadialGradient(24, 12, 1, 24, 12, 22);
      grad.addColorStop(0, "rgba(255,231,163,.55)"); grad.addColorStop(.5, "rgba(255,231,163,.16)"); grad.addColorStop(1, "rgba(255,231,163,0)");
      g.fillStyle = grad; g.fillRect(0, 0, 48, 28);
      tiles.glow = c;
      tiles.wheel = [0, 1, 2, 3].map(f => paint([
        "..ooo..", ".oSSSo.", "oSsssSo", "oSsHsSo", "oSsssSo", ".oSSSo.", "..ooo.."].map((r, y) => {
        const spoke = [[0, -2], [1, -1], [1, 0], [1, 1]][f], on = (dx, dy) => (dx === spoke[0] && dy === spoke[1]) || (dx === -spoke[0] && dy === -spoke[1]);
        return Array.from(r).map((ch, x) => ch === "s" && on(x - 3, y - 3) ? "o" : ch).join("");
      }), {o: INK, S: PAL.steelLo, s: PAL.steelDk, H: PAL.steelHi}));
    })();

    function draw(st, now) {
      const g = ctx, sh = cam.shake > .2 && !reduced ? Math.round((Math.random() * 2 - 1) * cam.shake) : 0;
      const V = {w: view.w, h: view.h, x: Math.round(cam.x) + sh, top: Math.round(cam.top), nt: normTop(), t: visualTime,
        night: nightness, now: now / 1000, st};
      V.alt = Math.max(0, V.nt - V.top);
      V.travel = Math.round(V.t * SPEED);
      V.hz = Math.round(LAY.horizon - V.nt + V.alt * .25);
      const amt = showAmount();
      paintSky(g, V); paintWater(g, V); paintTrack(g, V); paintUnder(g, V);
      if (V.night > .01) {
        g.globalCompositeOperation = "multiply"; g.globalAlpha = V.night;
        g.fillStyle = "#3e5079"; g.fillRect(0, V.hz, V.w, V.h - V.hz);
        g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
        paintNightLights(g, V);
      }
      paintLamps(g, V);
      paintCars(g, V, st);
      paintStuff(g, V, st);
      if (show) paintHoles(g, V);
      if (amt.freeze > .01) {
        const top = clamp(LAY.shellTop - V.top - 3, 0, V.h);
        g.globalCompositeOperation = "saturation"; g.fillStyle = "rgba(128,128,128," + (amt.freeze * .85).toFixed(3) + ")"; g.fillRect(0, top, V.w, V.h - top);
        g.globalCompositeOperation = "multiply"; g.fillStyle = "rgba(160,176,214," + (amt.freeze * .55).toFixed(3) + ")"; g.fillRect(0, top, V.w, V.h - top);
        g.globalCompositeOperation = "source-over";
      }
      paintParticles(g, V);
      if (show) paintCast(g, V, amt.u);
      if (!show) paintPrompts(g, V, st);
      if (amt.bars > .01) paintBars(g, V, st, amt);
      if (!s) paintAttract(g, V);
    }

    /* ---------- sky ---------- */
    function paintSky(g, V) {
      const N = V.night, hz = V.hz, lift = V.alt * .75;
      for (let y = 0; y < Math.min(V.h, hz); y++) {
        const above = hz - y;
        g.fillStyle = skyAt(above + lift * clamp(above / 40, 0, 1), N); g.fillRect(0, y, V.w, 1);
      }
      g.save(); g.beginPath(); g.rect(0, 0, V.w, Math.max(0, hz)); g.clip();
      if (N > .02) {
        const off = (V.x + V.travel) * .015;
        g.globalAlpha = N;
        STARS.forEach(p => {
          const x = Math.round(wrap(p.x - off, 960)) - 30, y = Math.round(hz - p.d + lift);
          if (x < -2 || x > V.w + 2 || y < -2 || y > hz - 4) return;
          const col = p.kind < 6 ? "#ffffff" : p.kind < 8 ? "#fff6c8" : "#e6f0ff";
          const tw = Math.sin(V.now * 1.7 + p.tw) > .2 || p.kind > 2;
          g.fillStyle = col; g.fillRect(x, y, 1, 1);
          if (p.kind === 0 && tw) { g.fillStyle = "#cfe0f5"; g.fillRect(x - 1, y, 1, 1); g.fillRect(x + 1, y, 1, 1); g.fillRect(x, y - 1, 1, 1); g.fillRect(x, y + 1, 1, 1); }
        });
        g.globalAlpha = 1;
      }
      /* sun by day, moon by night, both in the same corner */
      const bx = Math.round(V.w * .8), by = Math.round(hz - 96 + V.alt * .55);
      if (N < .99) { g.globalAlpha = 1 - N; disc(g, bx, by, 6, "#fede42", "#fff6c8"); g.globalAlpha = 1; }
      if (N > .01) {
        g.globalAlpha = N; disc(g, bx, by, 5, "#eef5fc", "#ffffff");
        g.fillStyle = "#c9d6e6"; g.fillRect(bx - 2, by - 1, 2, 2); g.fillRect(bx + 2, by + 2, 1, 1); g.fillRect(bx + 1, by - 3, 1, 1);
        g.globalAlpha = 1;
      }
      CLOUDS.forEach(([id, x0, d, f, fy]) => {
        const day = harbor["hw-cloud-" + id], dark = harbor["hw-cloud-" + id + ":night"];
        const span = Math.max(960, V.w + 240), x = Math.round(wrap(x0 - (V.x + V.travel) * f, span) - 120);
        if (!day) { if (N < .5) puff(g, x, Math.round(hz - d + V.alt * fy), id); return; }
        const y = Math.round(hz - d + V.alt * fy) - day.height;
        if (y > hz || y + day.height < 0 || x > V.w || x + day.width < 0) return;
        g.drawImage(day, x, y);
        if (N > .01 && dark) { g.globalAlpha = N; g.drawImage(dark, x, y); g.globalAlpha = 1; }
      });
      g.restore();
      /* the far shore sits on the horizon */
      const shore = harbor["hw-shore"], off = Math.round((V.x + V.travel) * .07);
      if (shore) {
        for (let x = -wrap(off, 640); x < V.w; x += 640) {
          g.drawImage(shore, x, hz - 20);
          if (N > .01 && harbor["hw-shore:night"]) { g.globalAlpha = N; g.drawImage(harbor["hw-shore:night"], x, hz - 20); g.globalAlpha = 1; }
        }
      } else {
        g.fillStyle = mix("#5e806c", "#22344a", N * .7); g.fillRect(0, hz - 4, V.w, 4);
        for (let x = -wrap(off, 70); x < V.w; x += 70) { g.fillStyle = mix("#a9b8cb", "#3a4a66", N * .7); g.fillRect(x + 10, hz - 12, 6, 8); g.fillRect(x + 30, hz - 9, 9, 5); }
      }
    }
    function disc(g, cx, cy, r, col, core) {
      for (let y = -r; y <= r; y++) {
        const half = Math.floor(Math.sqrt(r * r - y * y) + .3);
        g.fillStyle = col; g.fillRect(cx - half, cy + y, half * 2 + 1, 1);
      }
      g.fillStyle = core; g.fillRect(cx - 2, cy - 2, 3, 3);
    }
    function puff(g, x, y, id) {
      const wd = {a: 44, b: 34, c: 30, d: 20, e: 46, f: 30}[id] || 30;
      g.fillStyle = "#eef5fc"; g.fillRect(x, y - 4, wd, 4); g.fillRect(x + 6, y - 8, wd - 14, 4); g.fillRect(x + 12, y - 11, wd / 3, 3);
    }

    /* ---------- the harbour ---------- */
    function paintWater(g, V) {
      const hz = V.hz, bands = [[0, "#9cc6ea"], [2, "#84b6e2"], [6, "#70a8db"], [14, "#5e9ad3"], [28, "#4e8cca"], [48, "#4583c3"]];
      if (hz >= V.h) return;
      bands.forEach(([d, col], i) => {
        const y0 = hz + d, y1 = i + 1 < bands.length ? hz + bands[i + 1][0] : V.h;
        if (y1 > 0 && y0 < V.h) { g.fillStyle = col; g.fillRect(0, y0, V.w, y1 - y0); }
      });
      const far = harbor["hw-rip-far"], near = harbor["hw-rip-near"];
      if (far) { const o = Math.round((V.x + V.travel) * .11); for (let x = -wrap(o, 320); x < V.w; x += 320) g.drawImage(far, x, hz); }
      if (near) {
        for (let k = 0; hz + 56 + k * 28 < V.h && k < 4; k++) {
          const o = Math.round((V.x + V.travel) * (.18 + k * .06));
          for (let x = -wrap(o, 260); x < V.w; x += 260) g.drawImage(near, x, hz + 56 + k * 28);
        }
      }
      if (V.night < .9) {
        /* the sun's glitter, under the sun */
        const sx = Math.round(V.w * .8);
        g.globalAlpha = 1 - V.night;
        for (let k = 0; k < 14; k++) {
          const h = hash(k * 53 + Math.floor(V.now * 3 + k) * 7);
          if (h % 3) continue;
          g.fillStyle = k % 2 ? "#ffffff" : "#ddeefb";
          g.fillRect(sx - 14 + (h >>> 4) % 28, hz + 2 + (h >>> 10) % 24, 1 + (h >>> 16) % 3, 1);
        }
        g.globalAlpha = 1;
      }
      BOATS.forEach(([id, x0, e, f], i) => {
        const img = harbor[id];
        if (!img) return;
        const x = Math.round(wrap(x0 - (V.x + V.travel) * f, 1000) - 60), bob = e > 10 && Math.sin(V.now * 1.6 + i) > 0 ? 1 : 0;
        if (x < -20 || x > V.w) return;
        g.drawImage(img, x, hz + e - img.height + bob);
      });
    }
    function paintNightLights(g, V) {
      const off = Math.round((V.x + V.travel) * .07), N = V.night;
      [["hw-lit-a", .35], ["hw-lit-b", .55], ["hw-lit-c", .75]].forEach(([id, at]) => {
        const img = harbor[id];
        if (!img || N <= at) return;
        g.globalAlpha = clamp((N - at) * 4, 0, 1);
        for (let x = -wrap(off, 640); x < V.w; x += 640) g.drawImage(img, x, V.hz - 20);
      });
      const refl = harbor["hw-reflect"];
      if (refl && N > .5) { g.globalAlpha = (N - .5) * 1.1; for (let x = -wrap(off, 640); x < V.w; x += 640) g.drawImage(refl, x, V.hz); }
      g.globalAlpha = 1;
    }
    /* Harborwalk saucer lamps on the far side of the track, going by fast */
    function paintLamps(g, V) {
      const f = .82, span = 230, off = (V.x + V.travel) * f, base = Math.round(-2 - V.nt + V.alt * f), N = V.night;
      const pole = [PAL.lampPole, PAL.lampPoleLo, PAL.lampPoleDk].map(c => mix(c, "#0c1631", N * .55));
      const head = [PAL.lampHeadHi, PAL.lampHead, PAL.lampHeadLo].map(c => mix(c, "#0c1631", N * .5));
      for (let k = -1; k * span < V.w + span; k++) {
        const x = Math.round(k * span - wrap(off, span) + 90), top = base - 120;
        if (x < -30 || x > V.w + 30 || top > V.h) continue;
        g.fillStyle = pole[0]; g.fillRect(x, top, 1, base - top);
        g.fillStyle = pole[1]; g.fillRect(x + 1, top, 1, base - top);
        g.fillStyle = pole[2]; g.fillRect(x + 2, top, 1, base - top);
        g.fillStyle = head[0]; g.fillRect(x - 5, top - 4, 13, 1);
        g.fillStyle = head[1]; g.fillRect(x - 7, top - 3, 17, 1);
        g.fillStyle = head[2]; g.fillRect(x - 5, top - 2, 13, 1);
        if (N > .3) {
          g.globalAlpha = clamp((N - .3) * 2, 0, 1);
          g.fillStyle = "#fff2bf"; g.fillRect(x - 6, top - 2, 15, 1);
          g.drawImage(tiles.glow, x - 23, top - 12);
          g.globalAlpha = 1;
        }
      }
    }

    /* ---------- the track, and the seawall it runs along ---------- */
    function paintTrack(g, V) {
      const y0 = -V.top;
      if (y0 > V.h) return;
      const off = V.x + V.travel;
      for (let x = -wrap(off, 48); x < V.w; x += 48) for (let y = y0 + 8; y < V.h; y += 12) g.drawImage(tiles.granite, x, y);
      for (let x = -wrap(off, 64); x < V.w; x += 64) g.drawImage(tiles.ballast, x, y0 + 2);
      for (let x = -wrap(off, 9); x < V.w; x += 9) { g.fillStyle = PAL.tie; g.fillRect(x, y0 + 2, 6, 2); g.fillStyle = PAL.tieHi; g.fillRect(x, y0 + 2, 6, 1); }
      g.fillStyle = PAL.rail; g.fillRect(0, y0, V.w, 1);
      g.fillStyle = PAL.railMid; g.fillRect(0, y0 + 1, V.w, 1);
    }
    /* bogies and couplers ride under the cars; the wheels turn with the ride */
    function paintUnder(g, V) {
      const spin = tiles.wheel[Math.floor(V.travel / 3) % 4], y = -V.top;
      for (let i = 0; i <= LAST; i++) {
        const x0 = i * CARPX - V.x;
        if (x0 > V.w + 40 || x0 + CARPX + 40 < 0) continue;
        [44, 276].forEach(bx => {
          const cx = x0 + bx;
          g.fillStyle = PAL.bogie; g.fillRect(cx - 15, y - 8, 30, 3);
          g.fillStyle = PAL.bogieHi; g.fillRect(cx - 15, y - 8, 30, 1);
          g.fillStyle = PAL.spring; g.fillRect(cx - 11, y - 10, 3, 2); g.fillRect(cx + 8, y - 10, 3, 2);
          g.drawImage(spin, cx - 13, y - 7); g.drawImage(spin, cx + 6, y - 7);
        });
        if (i < LAST) { g.fillStyle = INK; g.fillRect(x0 + CARPX - 4, y - 9, 8, 2); }
      }
    }

    /* ---------- carriages ---------- */
    function paintCars(g, V, st) {
      const first = Math.max(0, Math.floor((V.x - 40) / CARPX)), last = Math.min(LAST, Math.floor((V.x + V.w + 40) / CARPX));
      const quake = tremor(st), warn = live() && quake.warning && !st.cinematic;
      for (let i = first; i <= last; i++) {
        const x = i * CARPX - V.x, y = LAY.shellTop - V.top;
        if (i === 0) {
          /* the back of the train: a blank end with tail lights */
          g.fillStyle = PAL.steelLo; g.fillRect(x - 8, LAY.roof + 3 - V.top, 8, LAY.lowFloor + 3 - LAY.roof);
          g.fillStyle = INK; g.fillRect(x - 9, LAY.roof + 3 - V.top, 1, LAY.lowFloor + 3 - LAY.roof);
          g.fillStyle = PAL.purple; g.fillRect(x - 8, LAY.lowFloor + 4 - V.top, 8, 1);
          g.fillStyle = Math.floor(V.now * 2) % 2 ? "#ff5a4a" : "#b8322a";
          g.fillRect(x - 6, LAY.lowFloor - 16 - V.top, 3, 3); g.fillRect(x - 6, LAY.upFloor - 18 - V.top, 3, 3);
        }
        g.drawImage(i === LAST ? shells.cab : shells.bi, x, y);
        (i === LAST ? [0] : [0, 1]).forEach(deck => {
          const fl = floorY(deck) - V.top, ceil = (deck ? LAY.upCeil : LAY.lowCeil) - V.top;
          g.fillStyle = PAL.led; g.fillRect(x + 1, ceil + 4, 31, 7);
          const label = warn && Math.floor(V.now * 3) % 2 ? "HOLD ON" : "CAR " + String(w.carCount - i).padStart(2, "0");
          write(g, label, x + 3, ceil + 5, warn && label === "HOLD ON" ? PAL.orange : PAL.amber);
          if (warn) { g.globalAlpha = .1 + .08 * Math.sin(V.now * 12); g.fillStyle = PAL.orange; g.fillRect(x, ceil, CARPX, fl - ceil); g.globalAlpha = 1; }
        });
        if (i === LAST) paintCab(g, V, st, x);
        paintSeats(g, V, st, i);
        if (i < LAST) paintGates(g, V, st, i);
      }
    }
    function paintCab(g, V, st, x) {
      /* blinking lights in the crew level */
      for (let k = 0; k < 11; k++) {
        const on = (hash(k * 13 + Math.floor(V.now * 2 + k * .37)) % 3) === 0;
        g.fillStyle = on ? ["#8cd879", "#ffb000", "#78afe9"][k % 3] : "#2a3240";
        g.fillRect(x + 24 + k * 26, LAY.upCeil + 9 - V.top, 2, 2);
      }
      /* the big green button, on its own pedestal in the cab */
      const bx = WORLD - 20 - V.x, fl = LAY.lowFloor - V.top, pressed = st.phase === "finished" && st.winner;
      g.fillStyle = INK; g.fillRect(bx - 7, fl - 19, 15, 19);
      g.fillStyle = "#506879"; g.fillRect(bx - 6, fl - 18, 13, 18);
      g.fillStyle = "#3a4a5a"; g.fillRect(bx - 6, fl - 6, 13, 1);
      /* the STOP sign hangs on the wall above, clear of name tags */
      g.fillStyle = INK; g.fillRect(bx, fl - 30, 1, 11);
      g.fillStyle = PAL.led; g.fillRect(bx - 7, fl - 37, 15, 7);
      write(g, "STOP", bx - 6, fl - 36, Math.floor(V.now * 2) % 2 || pressed ? PAL.greenHi : PAL.green);
      const glow = .5 + .5 * Math.sin(V.now * 4);
      if (!pressed) { g.globalAlpha = .25 + glow * .25; g.fillStyle = PAL.green; g.fillRect(bx - 5, fl - 17, 11, 9); g.globalAlpha = 1; }
      g.fillStyle = "#163e31"; g.fillRect(bx - 4, fl - 15, 9, 6);
      g.fillStyle = PAL.green; g.fillRect(bx - 3, pressed ? fl - 14 : fl - 16, 7, pressed ? 4 : 5);
      g.fillStyle = PAL.greenHi; g.fillRect(bx - 2, pressed ? fl - 14 : fl - 16, 3, 1);
    }
    function paintSeats(g, V, st, car) {
      const quake = tremor(st), jolt = quake.active && !reduced ? Math.round(Math.sin(V.now * 40)) : 0;
      seatList(st, car, w).forEach(a => {
        const x = Math.round(a.x / WPP) - V.x, fl = floorY(a.deck) - V.top;
        if (x < -20 || x > V.w + 20) return;
        g.drawImage(fixed.seat, x - 4, fl - fixed.seat.height);
        if (a.npc) {
          const art = npcArt(st.seed * 131 + car * 17 + a.deck * 7 + a.index), bob = a.index % 2 ? jolt : 0;
          g.drawImage(art.img, x - 3, fl - art.img.height - bob);
          if (art.prop === "sleep" && !reduced) {
            const ph = (V.now * .7 + a.index * .3) % 1;
            write(g, "z", x + 4 + Math.round(ph * 3), fl - art.img.height - 3 - Math.round(ph * 6), "#9aa6af");
          }
          if (art.prop === "phone" && Math.sin(V.now * 3 + a.index) > .6) { g.fillStyle = "#cfefff"; g.fillRect(x + 4, fl - 9, 1, 1); }
        }
      });
    }
    function paintGates(g, V, st, i) {
      const bx = (i + 1) * CARPX - V.x;
      if (bx < -20 || bx > V.w + 20) return;
      const opensAt = gateAt(st, i + 1, w), prog = st.phase === "idle" ? 1 : clamp((V.t - opensAt) / .6, 0, 1);
      const left = Math.max(0, Math.ceil(opensAt - st.elapsed));
      [0, 1].forEach(deck => {
        const fl = floorY(deck) - V.top;
        if (deck === 1 && i === LAST - 1) {
          /* the front car has one deck: this end is a wall with a sign */
          g.fillStyle = INK; g.fillRect(bx - 8, fl - 32, 9, 32);
          g.fillStyle = "#5b6986"; g.fillRect(bx - 7, fl - 31, 7, 31);
          g.fillStyle = PAL.led; g.fillRect(bx - 18, fl - 31, 13, 13);
          write(g, "↓", bx - 13, fl - 29, PAL.amber); write(g, "S", bx - 13, fl - 23, PAL.amber);
          return;
        }
        g.fillStyle = INK; g.fillRect(bx - 8, fl - 31, 11, 31);
        g.fillStyle = "#22304e"; g.fillRect(bx - 7, fl - 30, 9, 30);
        for (let k = 0; k < 9; k += 2) { g.fillStyle = "#33416a"; g.fillRect(bx - 7 + k, fl - 30, 1, 30); }
        const sh = Math.round(30 * (1 - prog));
        if (sh > 0) {
          g.fillStyle = "#8c98a2"; g.fillRect(bx - 7, fl - 30, 9, sh);
          for (let y = fl - 30; y < fl - 30 + sh; y += 3) { g.fillStyle = "#a9b3bb"; g.fillRect(bx - 7, y, 9, 1); }
          for (let k = 0; k < 9; k++) { g.fillStyle = k % 2 ? INK : PAL.gold; g.fillRect(bx - 7 + k, fl - 31 + sh, 1, 1); }
          if (prog === 0 && sh > 18) {
            g.fillStyle = PAL.gold; g.fillRect(bx - 5, fl - 19, 5, 4); g.fillStyle = INK; g.fillRect(bx - 4, fl - 22, 3, 1); g.fillRect(bx - 4, fl - 21, 1, 2); g.fillRect(bx - 2, fl - 21, 1, 2); g.fillRect(bx - 3, fl - 18, 1, 1);
          }
        }
        g.fillStyle = PAL.led; g.fillRect(bx - 11, fl - 39, 15, 7);
        if (prog < 1 && st.phase !== "idle") write(g, String(Math.min(99, left)), bx - 4, fl - 38, PAL.amber, {align: "center"});
        else write(g, "→", bx - 4, fl - 38, PAL.green, {align: "center"});
      });
    }
    function paintHoles(g, V) {
      show.victims.forEach(v => v.holes.forEach(cy => {
        const x = Math.round(v.x0) - V.x, y = cy - V.top;
        g.fillStyle = INK; g.fillRect(x - 4, y - 1, 9, 4); g.fillRect(x - 3, y - 2, 7, 6);
        g.fillStyle = PAL.steelLo; g.fillRect(x - 5, y - 2, 1, 1); g.fillRect(x + 5, y, 1, 1); g.fillRect(x - 2, y + 4, 1, 1);
      }));
    }

    /* ---------- the mess in the aisle, and the riders ---------- */
    function paintStuff(g, V, st) {
      const first = Math.max(0, Math.floor(V.x / CARPX) - 1), last = Math.min(LAST, Math.floor((V.x + V.w) / CARPX) + 1);
      for (let car = first; car <= last; car++) {
        objectList(st, car, w, V.t).forEach(a => {
          const x = Math.round(a.x / WPP) - V.x, fl = floorY(a.deck) - V.top;
          if (x < -30 || x > V.w + 30) return;
          if (PET_PAINTERS[a.kind]) { PET_PAINTERS[a.kind](g, V, st, a, x, fl, petOf(a, st), car); return; }
          const img = fixed[a.kind];
          if (!img) return;
          const bob = a.vx && Math.floor(V.t * 10 + car) % 2 ? 1 : 0;
          g.drawImage(img, x - Math.floor(img.width / 2), fl - img.height - bob);
          if (a.vx && !reduced) {
            g.fillStyle = "rgba(255,255,255,.75)";
            const r = x + Math.ceil(img.width / 2) + 2, k = Math.floor(V.t * 12) % 3;
            g.fillRect(r + k, fl - 7, 4, 1); g.fillRect(r + 2 - k, fl - 11, 3, 1); g.fillRect(r + 1, fl - 4, 5 - k, 1);
          }
        });
      }
      const me = s ? st.players[st.you] : null, tags = [];
      Object.values(st.players).filter(p => p.hp > 0 && (!show || !show.victims.some(v => v.id === p.id)))
        .sort((a, b) => (a === me) - (b === me)).forEach(p => { const t = paintRider(g, V, st, p, p === me); if (t) tags.push(t); });
      paintTags(g, V, tags);
    }
    function paintTags(g, V, tags) {
      const placed = [];
      tags.sort((a, b) => (b.mine - a.mine) || (b.y - a.y)).forEach(t => {
        t.w = textWidth(t.text) + 2;
        for (let guard = 0; guard < 6 && placed.some(q => Math.abs(q.x - t.x) < (q.w + t.w) / 2 + 1 && Math.abs(q.y - t.y) < 7); guard++) t.y -= 7;
        placed.push(t);
        write(g, t.text, t.x, t.y, t.mine ? "#ffffff" : t.color, {align: "center", outline: INK});
        if (t.mine) {
          const b = reduced ? 0 : Math.round(Math.sin(V.now * 5));
          g.fillStyle = PAL.orange; g.fillRect(t.x - 2, t.y - 6 + b, 5, 1); g.fillRect(t.x - 1, t.y - 5 + b, 3, 1); g.fillRect(t.x, t.y - 4 + b, 1, 1);
        }
      });
    }

    /* ======================================================================
       THE AISLE ANIMALS
       Where the garbage piles used to be. The rules only know a spot and a
       footprint; everything here is show: the dog barks and strains at its
       leash when a rider comes near, the cat swipes out of its carrier, and
       the wild turkey loses it completely when anyone touches it. They all
       freeze with the rest of the train during a send-off (petClock).
       ====================================================================== */
    function petOf(a, st) {
      let pet = pets[a.id];
      if (!pet) {
        const h = hash(strHash(a.id) + st.seed);
        pet = pets[a.id] = {h, phase: (h % 997) / 997 * 6, home: (h >>> 9) % 4 ? -1 : 1,
          flapStart: -9, flapEnd: -9, lastFeather: -1, was: null, textAt: -9};
      }
      return pet;
    }
    function nearestRider(st, a) {
      let best = null;
      const ax = a.x / WPP;
      Object.values(st.players).forEach(p => {
        if (p.hp <= 0 || p.deck !== a.deck || p.seat != null) return;
        const dx = riderState(p).x - ax;
        if (!best || Math.abs(dx) < Math.abs(best.dx)) best = {dx, mine: !!s && p.id === st.you};
      });
      return best;
    }
    function petSound(name, near) {
      if (!near || !near.mine || petClock - petSoundAt < .2) return;
      petSoundAt = petClock; sfx[name]();
    }
    function petSay(pet, text, x, y, col, gap) {
      if (petClock - pet.textAt < (gap || .9)) return;
      pet.textAt = petClock;
      floaters.push({text, x, y, life: .8, col});
    }
    /* the leash: a sagging line from the collar to an owner's hand, or to
       the seat leg when nobody is sitting there to hold it */
    function leash(g, x0, y0, x1, y1, sag, col) {
      const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)) * 2 + 2;
      g.fillStyle = col;
      for (let i = 0; i <= n; i++) {
        const t = i / n;
        g.fillRect(Math.round(lerp(x0, x1, t)), Math.round(lerp(y0, y1, t) + sag * 4 * t * (1 - t)), 1, 1);
      }
    }
    function paintDog(g, V, st, a, x, fl, pet, car) {
      const art = dogArt(pet.h), near = nearestRider(st, a), t = petClock + pet.phase;
      const excited = near && Math.abs(near.dx) < 72;
      const face = near && Math.abs(near.dx) < 110 ? (near.dx < 0 ? -1 : 1) : pet.home;
      let barking;
      if (excited) barking = (t * 3.2) % 1 < .45;
      else { const c = t % 4.8; barking = c < 1.1 && (c * 4) % 1 < .5; }
      const frame = barking ? "bark" : (Math.floor(t * 4) % 2 ? "wag" : "idle");
      const lunge = excited && barking && !reduced ? face : 0;
      const img = art[frame][face < 0 ? "l" : "r"], left = x - Math.floor(img.width / 2) + lunge, top = fl - img.height;
      const cp = DOG.collar[frame], cx = left + (face < 0 ? cp[0] : img.width - 1 - cp[0]), cy = top + cp[1];
      let seat = null;
      seatList(st, car, w).forEach(q => { if (q.deck === a.deck && (!seat || Math.abs(q.x - a.x) < Math.abs(seat.x - a.x))) seat = q; });
      if (seat) {
        const sx = Math.round(seat.x / WPP) - V.x;
        leash(g, cx, cy, seat.npc ? sx + 5 : sx, seat.npc ? fl - 6 : fl - 3, lunge ? 0 : 2, art.leash);
      }
      g.drawImage(img, left, top);
      if (barking) {
        const mx = face < 0 ? left - 1 : left + img.width, my = top + DOG.mouth;
        g.fillStyle = INK;
        [[1, -2], [2, -3], [1, 0], [2, 0], [1, 2], [2, 3]].forEach(([dx, dy]) => g.fillRect(mx + face * dx, my + dy, 1, 1));
        if (pet.was !== "bark") {
          if (excited) petSay(pet, pet.h % 2 ? "ARF" : "WOOF", a.x / WPP - face * 2, floorY(a.deck) - img.height - 9, "#ffffff");
          if (excited) petSound("bark", near);
        }
      }
      pet.was = barking ? "bark" : "quiet";
    }
    function paintCat(g, V, st, a, x, fl, pet) {
      const art = catArt(pet.h), near = nearestRider(st, a), t = petClock + pet.phase;
      const excited = near && Math.abs(near.dx) < 64;
      const dir = near && Math.abs(near.dx) < 110 ? (near.dx < 0 ? -1 : 1) : pet.home;
      const period = excited ? .8 : 3.6, reach = excited ? .65 : .22, ph = (t % period) / period;
      let paw = null;
      if (ph < reach) { const k = ph / reach; paw = k < .18 ? "reach" : k < .42 ? "up" : k < .66 ? "down" : k < .84 ? "up" : "reach"; }
      const rattle = paw && paw !== "reach" && !reduced ? Math.floor(t * 24) % 2 : 0;
      const img = art.carrier, left = x - Math.floor(img.width / 2) + rattle, top = fl - img.height;
      g.drawImage(img, left, top);
      /* eyes: blink now and then, glare red mid-swipe */
      const blink = !paw && (t % 3.1) < .16;
      g.fillStyle = blink ? "#1d2338" : paw && paw !== "reach" ? "#ff5a3c" : art.eye;
      CARRIER.eyes.forEach(([ex, ey]) => g.fillRect(left + ex, top + ey, 1, 1));
      if (paw) {
        const pa = art.paw[paw], root = PAW.roots[paw];
        if (dir < 0) g.drawImage(pa.l, left + CARRIER.door[0] - root[0], top + CARRIER.pawY - root[1]);
        else g.drawImage(pa.r, left + CARRIER.door[1] - (pa.r.width - 1 - root[0]), top + CARRIER.pawY - root[1]);
        if (paw === "up" && pet.was !== "up" && excited) {
          petSay(pet, "HISS", a.x / WPP, floorY(a.deck) - img.height - 9, "#ff8a6a", 1.4);
          petSound("hiss", near);
        }
      }
      pet.was = paw;
    }
    function feather(x, y) {
      const cols = [TURKEY.pal.W, TURKEY.pal.w, TURKEY.pal.b, TURKEY.pal.u];
      particle(x + Math.random() * 10 - 5, y - Math.random() * 8, (Math.random() - .5) * 46, -16 - Math.random() * 26, cols[Math.floor(Math.random() * 4)], 1.3, 34);
    }
    /* touch a turkey (or get flogged by one) and it panics for a while */
    function startFlap(a, st, loud) {
      const pet = petOf(a, st), fresh = pet.flapEnd < petClock;
      pet.flapEnd = petClock + 1.7;
      if (!fresh) return;
      pet.flapStart = petClock;
      const x = a.x / WPP, y = floorY(a.deck) - 9;
      for (let k = 0; k < 8; k++) feather(x, y);
      pet.textAt = -9; petSay(pet, "GOBBLE", x, y - 26, PAL.gold, 0);
      if (loud && petClock - petSoundAt > .15) { petSoundAt = petClock; sfx.gobble(); }
    }
    function petHit(p, st) {
      if (/brak|ceiling/.test(String(p.note || ""))) return;
      const at = p.x + 28, car = Math.min(LAST, Math.floor(at / w.carWidth));
      let best = null;
      [car - 1, car, car + 1].filter(c => c >= 0 && c <= LAST).forEach(c => objectList(st, c, w).forEach(a => {
        const d = Math.abs(a.x - at);
        if (a.kind === "turkey" || a.kind === "dog" || a.kind === "cat") if (a.deck === p.deck && d < a.w / 2 + 18 && (!best || d < best.d)) best = {a, d};
      }));
      if (best && best.a.kind === "turkey") {
        const sx = best.a.x / WPP - cam.x;
        startFlap(best.a, st, sx > -20 && sx < view.w + 20);
      }
    }
    function paintTurkey(g, V, st, a, x, fl, pet) {
      const art = turkeyArt(), t = petClock + pet.phase;
      /* blinking riders can't be hurt, but they can still bump into it */
      if (st.phase === "running" && Object.values(st.players).some(p => p.hp > 0 && p.seat == null && p.deck === a.deck && Math.abs(p.x - a.x) < a.w / 2 + 10 && p.y < a.h - 3)) startFlap(a, st, true);
      let frame, face, hop = 0, jit = 0;
      if (pet.flapEnd > petClock) {
        const e = petClock - pet.flapStart, k = Math.floor(e * (reduced ? 6 : 15)), r = hash(pet.h + k * 7919);
        frame = ["up", "mid", "down"][r % 3];
        if (!reduced) { hop = [0, 1, 3, 5, 2, 4][(r >>> 3) % 6]; jit = (r >>> 7) % 3 - 1; }
        face = hash(pet.h + Math.floor(e * 4) * 31) % 3 ? pet.home : -pet.home;
        if ((r >>> 11) % 3 === 0 && pet.lastFeather !== k) { pet.lastFeather = k; feather(a.x / WPP, floorY(a.deck) - 10 - hop); }
      } else {
        const c = t % 7.3;
        face = Math.floor(t / 7.3) % 2 ? -pet.home : pet.home;
        frame = (c > 2.2 && c < 2.7) || (c > 5.1 && c < 5.45) ? "peck" : "idle";
      }
      const img = art[frame][face < 0 ? "l" : "r"];
      if (hop > 1) { g.fillStyle = "rgba(19,34,69,.28)"; g.fillRect(x - 5, fl - 1, 11, 1); }
      g.drawImage(img, x - Math.floor(img.width / 2) + jit, fl - img.height - hop);
    }
    const PET_PAINTERS = {dog: paintDog, cat: paintCat, turkey: paintTurkey};
    function paintRider(g, V, st, p, mine) {
      const r = riderState(p), art = riderArt(r.look);
      const x = Math.round(r.x) - V.x, fl = Math.round(r.floor) - V.top, lift = Math.round(r.y);
      if (x < -24 || x > V.w + 24) return null;
      let img, left, top;
      if (p.seat !== null && p.seat !== undefined) {
        img = art.sit.r; left = x - 3; top = fl - img.height;
      } else {
        let f = art.stand;
        if (lift > 0) f = art.jump;
        else if (p.invuln > 1.05) f = art.hurt;
        else if (r.moving) f = [art.walk1, art.stand, art.walk2, art.stand][Math.floor(r.dist / 5) % 4];
        img = r.face < 0 ? f.l : f.r;
        left = r.face < 0 ? x - (img.width - f.ax) : x - f.ax;
        top = fl - img.height - lift;
        if (lift > 3) { g.fillStyle = "rgba(19,34,69,.28)"; g.fillRect(x - 3, fl - 1, 7, 1); }
      }
      const blink = p.invuln > 0 && Math.floor(V.now * (reduced ? 4 : 11)) % 2 === 0;
      if (!blink) g.drawImage(img, left, top);
      return {text: mine ? "YOU" : firstName(p.name), x, y: top - 7, color: p.color, mine};
    }
    function paintParticles(g, V) {
      particles.forEach(q => {
        const sz = q.life < q.max * .4 ? 1 : q.size;
        g.fillStyle = q.col; g.fillRect(Math.round(q.x) - V.x, Math.round(q.y) - V.top, sz, sz);
      });
      floaters.forEach(f => write(g, f.text, Math.round(f.x) - V.x, Math.round(f.y) - V.top, f.col, {align: "center", outline: INK}));
    }
    /* the riders on their way out */
    function paintCast(g, V, u) {
      show.victims.forEach(v => {
        const x = Math.round(v.x) - V.x, y = Math.round(v.y) - V.top;
        if (u < PH.b) {
          const fl = v.baseY + 9 - V.top, img = reduced || Math.floor(V.now * 14) % 2 ? v.art.flash : v.art.hurt.r;
          g.drawImage(img, x - 5, fl - img.height);
          write(g, v.name, x, fl - img.height - 8, "#ffffff", {align: "center", outline: INK});
        } else if (u < PH.d) {
          const q = reduced ? 0 : Math.floor(V.now * (v.tier ? 10 : 15)) % 4;
          const img = v.tier === 0 ? v.art.spin[q] : v.tier === 1 ? v.art.halfSpin[q] : v.tier === 2 ? v.art.dotSpin[q] : null;
          if (img) g.drawImage(img, x - Math.floor(img.width / 2), y - Math.floor(img.height / 2));
          else if (Math.floor(V.now * 12) % 2) { g.fillStyle = v.color; g.fillRect(x, y, 1, 1); }
          if (v.tier === 0 && u < PH.c + .08) write(g, v.name, x, y - 20, "#ffffff", {align: "center", outline: INK});
        } else {
          const tw = fixed.twinkle, k = (u - PH.d) / (PH.e - PH.d);
          const img = u < PH.e ? tw[Math.min(tw.length - 1, Math.floor(k * tw.length))] : tw[1];
          if (u >= PH.e) g.globalAlpha = 1 - clamp((u - PH.e) / .12, 0, 1);
          g.drawImage(img, x - 9, y - 9, img.width * 2, img.height * 2);
          g.globalAlpha = 1;
        }
      });
    }
    function keycap(g, x, y, label, accent) {
      const wd = Math.max(7, textWidth(label) + 4);
      g.fillStyle = INK; g.fillRect(x - Math.floor(wd / 2) - 1, y - 1, wd + 2, 10);
      g.fillStyle = accent || "#ffffff"; g.fillRect(x - Math.floor(wd / 2), y, wd, 7);
      g.fillStyle = "#9fbde0"; g.fillRect(x - Math.floor(wd / 2), y + 7, wd, 1);
      write(g, label, x, y + 1, INK, {align: "center"});
    }
    /* what you can do right here, in the key you would press to do it */
    function paintPrompts(g, V, st) {
      if (!s || st.phase !== "running") return;
      const me = st.players[st.you];
      if (!me || me.hp <= 0) return;
      const r = riderState(me), car = Math.min(LAST, Math.floor(me.x / w.carWidth)), off = me.x - car * w.carWidth;
      const bob = reduced ? 0 : Math.round(Math.sin(V.now * 6));
      const quake = tremor(st), urgent = quake.warning || quake.active;
      const act = touchMode ? "B" : "E";
      if (me.seat == null && me.y <= 0) {
        seatList(st, car, w).filter(a => a.deck === me.deck && !a.npc && !a.owner).forEach(a => {
          const x = Math.round(a.x / WPP) - V.x, fl = floorY(a.deck) - V.top, near = Math.abs(a.x - me.x) < 61;
          /* right beside the seat, the prompt hops above your name tag */
          if (near) keycap(g, x + 1, fl - (Math.abs(x + 1 - (Math.round(r.x) - V.x)) < 14 ? 42 : 27) + bob, act, urgent ? PAL.gold : "#ffffff");
          else {
            const pulse = urgent ? Math.floor(V.now * 6) % 2 : 0;
            g.fillStyle = PAL.greenDk; g.fillRect(x - 2 - pulse, fl - 22 + bob, 7 + pulse * 2, 1);
            g.fillStyle = PAL.green; g.fillRect(x - 1, fl - 21 + bob, 5, 1); g.fillRect(x, fl - 20 + bob, 3, 1); g.fillRect(x + 1, fl - 19 + bob, 1, 1);
          }
        });
        if (car < LAST && me.stairs <= 0 && (off < 105 || off > w.carWidth - 105)) {
          const up = me.deck === 0, label = touchMode ? (up ? "↑" : "↓") : (up ? "W" : "S");
          keycap(g, Math.round(r.x) - V.x + (off < 105 ? 12 : -12), Math.round(r.floor) - V.top - 34 + bob, label, PAL.sky);
        }
        if (car === LAST && me.deck === 0 && me.x > w.carWidth * w.carCount - 130) keycap(g, WORLD - 20 - V.x, LAY.lowFloor - V.top - 49 + bob, act, PAL.green);
      }
    }
    function paintBars(g, V, st, amt) {
      const hgt = Math.round(9 * amt.bars), u = amt.u;
      g.fillStyle = "#0c1631"; g.fillRect(0, 0, V.w, hgt); g.fillRect(0, V.h - hgt, V.w, hgt);
      if (hgt < 8 || !show) return;
      const names = show.victims.map(v => v.name).join(" + ");
      const top = u < PH.e ? "UNSCHEDULED DEPARTURE" : "BACK TO THE RIDE";
      const alive = Object.values(st.players).filter(p => p.hp > 0).length;
      const bottom = u < PH.c ? names + " IS OFF THE TRAIN" : u < PH.e ? "✦ " + names + " ✦ SHOOTING STAR" : alive + (alive === 1 ? " RIDER LEFT" : " RIDERS LEFT");
      write(g, top, Math.floor(V.w / 2), 2, PAL.amber, {align: "center"});
      write(g, textWidth(bottom) > V.w - 6 ? names : bottom, Math.floor(V.w / 2), V.h - 7, "#ffffff", {align: "center"});
      if (u < PH.e) { write(g, "II", 4, 2, PAL.sky); }
    }
    function paintAttract(g, V) {
      if (Math.floor(V.now * 1.5) % 2) { g.fillStyle = "#ff5a4a"; g.fillRect(V.w - 32, 5, 3, 3); }
      write(g, "DEMO", V.w - 26, 4, "#ffffff", {outline: INK});
    }

    /* ---------- the HUD and the side windows ---------- */
    const heartSvg = '<svg viewBox="0 0 9 8" aria-hidden="true" focusable="false"><path class="o" d="M1 0h2v1h1v1h1V1h1V0h2v1h1v3H8v1H7v1H6v1H5v1H4V7H3V6H2V5H1V4H0V1h1z"/><path class="f" d="M1 1h2v1h1v1h1V2h1V1h2v3H7v1H6v1H5v1H4V6H3V5H2V4H1z"/><path class="h" d="M2 2h1v1H2z"/></svg>';
    (function hearts() { const box = $("tp-health"); for (let i = 0; i < w.maxHealth; i++) { const h = document.createElement("i"); h.className = "tp-heart"; h.innerHTML = heartSvg; box.append(h); } })();
    /* the legend under the game: each thing that hurts, and the door, seat
       and green button, drawn from the game's own sprites at a whole scale */
    (function legendIcons() {
      const sketch = (wd, ht, draw) => { const c = canvasOf(wd, ht), g = pen(c); draw((x, y, a, b, col) => { g.fillStyle = col; g.fillRect(x, y, a, b); }); return c; };
      /* the door between cars, shutter down and padlocked, as paintGates draws it */
      const door = () => sketch(11, 17, R => {
        R(0, 0, 11, 17, INK); R(1, 1, 9, 15, "#8c98a2");
        for (let y = 1; y < 16; y += 3) R(1, y, 9, 1, "#a9b3bb");
        for (let k = 0; k < 9; k++) R(1 + k, 16, 1, 1, k % 2 ? INK : PAL.gold);
        R(4, 5, 3, 1, INK); R(4, 6, 1, 2, INK); R(6, 6, 1, 2, INK); R(3, 8, 5, 4, PAL.gold); R(5, 9, 1, 1, INK);
      });
      /* the green button on its pedestal in the cab, as paintCab draws it */
      const button = () => sketch(15, 16, R => {
        R(0, 0, 15, 16, INK); R(1, 1, 13, 15, "#506879"); R(1, 12, 13, 1, "#3a4a5a");
        R(3, 4, 9, 6, "#163e31"); R(4, 3, 7, 5, PAL.green); R(5, 3, 3, 1, PAL.greenHi);
      });
      const icons = {
        dog: () => dogArt(0).idle.l, cat: () => catArt(0).carrier, turkey: () => turkeyArt().idle.l,
        scooter: () => fixed.scooter, bike: () => fixed.bike, armchair: () => fixed.armchair, cart: () => fixed.cart,
        seat: () => fixed.seat, door, button
      };
      root.querySelectorAll("canvas[data-tp-icon]").forEach(c => {
        const make = icons[c.dataset.tpIcon];
        if (!make) return;
        /* trim the sprite's empty margin, so small ones (the turkey) read */
        const src = make(), px = pen(src).getImageData(0, 0, src.width, src.height).data;
        let x0 = src.width, y0 = src.height, x1 = -1, y1 = -1;
        for (let y = 0; y < src.height; y++) for (let x = 0; x < src.width; x++) {
          if (px[(y * src.width + x) * 4 + 3]) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
        }
        if (x1 < 0) return;
        const sw = x1 - x0 + 1, sh = y1 - y0 + 1, k = Math.max(1, Math.min(Math.floor(50 / sw), Math.floor(36 / sh)));
        c.width = sw * k; c.height = sh * k;
        const g = pen(c); g.drawImage(src, x0, y0, sw, sh, 0, 0, c.width, c.height);
      });
    })();
    function avatar(p) {
      const art = riderArt(lookOf(p.id, p.color)), src = art.stand.r;
      const c = canvasOf(src.width * 2, src.height * 2), g = pen(c);
      g.drawImage(src, 0, 0, c.width, c.height);
      c.className = "tp-rider-art"; c.setAttribute("aria-hidden", "true");
      return c;
    }
    const keyHint = (kbd, touch) => touchMode ? touch : kbd;
    function updateUI() {
      const state = s || idle, me = state.players[state.you], list = Object.values(state.players);
      const car = me ? Math.min(LAST, Math.floor(me.x / w.carWidth)) : 0;
      const secondsLeft = Math.ceil(Math.max(0, state.duration - state.elapsed));
      setText("tp-time", s ? clockText(secondsLeft) : clockText(w.duration));
      setText("tp-location", "CAR " + String(w.carCount - car).padStart(2, "0") + " · " + (me && me.deck ? "UPPER" : "LOWER"));
      const hp = s ? (me ? me.hp : 0) : w.maxHealth, box = $("tp-health");
      if (box.dataset.hp !== String(hp)) {
        box.dataset.hp = hp;
        Array.from(box.children).forEach((h, i) => h.classList.toggle("empty", i >= hp));
        box.setAttribute("aria-label", hp + " of " + w.maxHealth + " hearts left");
      }
      $("tp-menu").hidden = !!s; $("tp-room").hidden = !s;
      setText("tp-mode", local ? "PRACTICE / 3 AI RIDERS" : (s ? "MULTIPLAYER / SHARED TRAIN" : "PARTY SURVIVAL RACE"));
      if (s) {
        setText("tp-room-code", s.code);
        $("tp-copy").hidden = !!local;
        $("tp-start").hidden = !!local || s.phase !== "lobby";
        $("tp-start").disabled = busy || s.host !== s.you || list.length < 2;
        setText("tp-room-status", local ? "90-second practice. Bots are labelled in the manifest." : s.phase === "lobby" ? (s.host === s.you ? "Share the code. Two or more riders can depart." : "Waiting for the host to start the ride.") : rideName(s.duration) + " · " + list.filter(p => p.hp > 0).length + " still aboard.");
        setText("tp-leave", local ? "End practice" : (s.phase === "finished" ? "Back to the platform" : "Leave this train"));
      }
      setText("tp-count", s ? list.length + " / " + w.capacity : "0 / " + w.capacity);
      /* the manifest */
      const rk = s ? JSON.stringify(list.map(p => [p.id, p.name, p.color, p.hp, p.seat, Math.floor(p.x / w.carWidth)])) : "empty";
      if (rosterKey !== rk) {
        rosterKey = rk; const roster = $("tp-roster"); roster.replaceChildren();
        if (!s) { const p = document.createElement("p"); p.className = "tp-empty"; p.textContent = "Your fellow commuters will appear here. Personal space is optional."; roster.append(p); }
        else list.forEach(p => {
          const row = document.createElement("div"); row.className = "tp-rider" + (!p.hp ? " is-out" : "") + (p.id === state.you ? " is-you" : "");
          const name = document.createElement("span"); name.className = "tp-rider-name"; name.textContent = p.name + (p.id === state.you ? " (you)" : "");
          name.style.setProperty("--rider", p.color);
          const pips = document.createElement("span"); pips.className = "tp-pips"; pips.setAttribute("aria-label", p.hp + " hearts");
          for (let i = 0; i < w.maxHealth; i++) { const d = document.createElement("i"); if (i >= p.hp) d.className = "gone"; pips.append(d); }
          const status = document.createElement("small");
          status.textContent = p.hp ? (p.seat ? "SEATED" : "CAR " + String(w.carCount - Math.min(LAST, Math.floor(p.x / w.carWidth))).padStart(2, "0")) : "TWINKLE ✦";
          const mid = document.createElement("span"); mid.className = "tp-rider-mid"; mid.append(name, pips);
          row.append(avatar(p), mid, status); roster.append(row);
        });
      }
      /* the line map */
      const route = $("tp-route");
      if (route && !route.children.length) for (let i = 0; i < w.carCount; i++) {
        const stop = document.createElement("span"); stop.className = "tp-stop";
        stop.innerHTML = "<b>" + String(w.carCount - i).padStart(2, "0") + "</b>";
        route.append(stop);
      }
      const nextGate = car < LAST ? Math.max(0, gateAt(state, car + 1, w) - state.elapsed) : 0;
      const rk2 = car + ":" + (s ? Math.ceil(nextGate) : "x") + ":" + (s ? 1 : 0);
      if (route && routeKey !== rk2) {
        routeKey = rk2;
        Array.from(route.children).forEach((d, i) => {
          d.className = "tp-stop" + (i === car ? " here" : i < car ? " past" : "") + (i === LAST ? " front" : "");
          const open = !s || state.elapsed >= gateAt(state, i, w);
          d.classList.toggle("locked", i > 0 && !open);
        });
      }
      setText("tp-gate-note", !s ? "Start at car 08. The front car has one deck." : car === LAST ? "Front car. The green button is at the far right; " + keyHint("press E", "tap B") + " to win." : nextGate ? "Car " + String(LAST - car).padStart(2, "0") + " opens in " + Math.ceil(nextGate) + "s. Survive this carriage." : "Door open. Keep heading right to car " + String(LAST - car).padStart(2, "0") + ".");
      /* the warning banner */
      const quake = tremor(state), warn = !!s && s.phase === "running" && (quake.warning || quake.active) && !s.cinematic;
      $("tp-warning").hidden = !warn;
      if (warn) {
        setText("tp-warning", (quake.kind === "brake" ? "BRAKE CHECK" : "ROUGH TRACK") + " · " + (me && me.seat ? "STAY SEATED" : "FIND A SEAT") + (quake.in > 0 ? " · " + quake.in.toFixed(1) + "s" : "!"));
        if (warningId !== quake.id) { warningId = quake.id; sfx.warn(); buzz(40); }
      }
      /* moments worth a sound */
      if (s && me) {
        if (me.seat !== lastMySeat) { if (me.seat) sfx.seat(); lastMySeat = me.seat; }
        const g = car < LAST ? gateAt(state, car + 1, w) : -1;
        if (g > 0 && state.elapsed >= g && lastGateOpen !== g && state.phase === "running") { lastGateOpen = g; sfx.door(); }
      }
      if (s && s.phase !== lastPhase) { if (s.phase === "finished" && s.winner) sfx.win(); lastPhase = s.phase; }
      /* the message line */
      let message = "Eight cars. One green button. A deeply questionable commute.";
      if (s) {
        const spectating = me && me.hp <= 0;
        if (s.phase === "lobby") message = "On the platform. Share your code and wait for everyone to board.";
        else if (s.phase === "countdown") message = "Doors closing. Please question your life choices.";
        else if (s.phase === "finished") message = s.reason;
        else if (s.cinematic) message = "Send-off in progress. Everyone is paused until the camera comes back.";
        else if (spectating) message = "You're out. Watch the riders still aboard race to the front.";
        else if (me && me.noteTime > 0) message = me.note;
        else if (me && me.seat) message = "Seated and safe. " + keyHint("Press E or jump", "Tap B or A") + " to get back in the aisle.";
        else if (car === LAST - 1 && me && me.deck && me.x % w.carWidth > w.carWidth - 135) message = "The front car has one deck. " + keyHint("Press S", "Press ▼") + " at these stairs first.";
        else if (car === LAST) message = "There it is. Reach the green button on the right, then " + keyHint("press E", "tap B") + ".";
        else message = "Head right. Jump the mess. Green arrows mean a free seat. " + (nextGate ? "Next door: " + Math.ceil(nextGate) + "s." : "The next door is OPEN.");
      }
      setText("tp-message", message);
      $("tp-led").className = "tp-led" + (s && s.cinematic ? " paused" : warn ? " warn" : "");
      $("tp-watch").hidden = !s || !me || me.hp > 0 || s.phase !== "running" || !!s.cinematic || !!show;
      setText("tp-scene-tag", !s ? "BOSTON BOUND. PROBABLY." : s.cinematic ? "PAUSED · SEND-OFF" : s.phase === "lobby" ? "BOARDING / " + s.code : "CAR " + String(w.carCount - car).padStart(2, "0") + " / " + String(w.carNames[car] || "").toUpperCase());
      /* the pad lights up what will work */
      const pad = $("tp-pad"), running = !!s && s.phase === "running" && !s.cinematic && me && me.hp > 0;
      const off = me ? me.x - car * w.carWidth : 0;
      const nearSeat = running && me.seat == null && seatList(state, car, w).some(a => a.deck === me.deck && !a.npc && !a.owner && Math.abs(a.x - me.x) < 61);
      const nearStop = running && car === LAST && me.deck === 0 && me.x > w.carWidth * w.carCount - 130;
      const atStairs = running && car < LAST && me.y <= 0 && (off < 105 || off > w.carWidth - 105);
      pad.classList.toggle("is-paused", !!s && (!!s.cinematic || s.phase !== "running" || !me || me.hp <= 0));
      pad.classList.toggle("can-act", !!(nearSeat || nearStop || (running && me.seat)));
      pad.classList.toggle("can-up", !!(atStairs && me.deck === 0));
      pad.classList.toggle("can-down", !!(atStairs && me.deck === 1));
      setText("tp-b-label", running && me.seat ? "STAND" : nearStop ? "STOP" : nearSeat ? "SIT" : "USE");
      /* on a phone the ride takes over the screen once it departs */
      if (touchMode && s && (s.phase === "countdown" || s.phase === "running") && !immersive && !immersiveDismissed) setImmersive(true, false);
      canvas.setAttribute("aria-label", "Side view of a double-decker train. " + keyHint(
        "Move with A and D or the arrow keys, jump with Space, take the stairs with W and S, sit or press the stop button with E.",
        "Move with the pad's left and right, take the stairs with up and down, jump with A, sit or press the stop button with B."));
      /* the countdown and the end of the line */
      const ok = !s ? "idle" : s.phase === "countdown" ? "count-" + Math.ceil(s.countdown) + touchMode : s.phase === "finished" ? "finish-" + s.winner : "none";
      if (overlayKey !== ok) {
        overlayKey = ok; const overlay = $("tp-overlay"); overlay.replaceChildren();
        overlay.hidden = !s || !["countdown", "finished"].includes(s.phase);
        if (!overlay.hidden) {
          const dialog = document.createElement("div"); dialog.className = "tp-dialog";
          if (s.phase === "countdown") {
            const title = document.createElement("h3"); title.textContent = "Mind the closing doors.";
            const count = document.createElement("div"); count.className = "tp-countdown"; count.textContent = Math.ceil(s.countdown) || "GO";
            const p = document.createElement("p"); p.textContent = keyHint("A / D to move · Space to jump · W / S for stairs · E to sit", "◀ ▶ to move · ▲ ▼ for stairs · A to jump · B to sit");
            dialog.append(title, count, p);
          } else {
            const stamp = document.createElement("span"); stamp.className = "tp-stamp"; stamp.textContent = s.winner ? "SERVICE FINALLY SUSPENDED" : "SERVICE NOT GUARANTEED";
            const title = document.createElement("h3"); title.textContent = s.winner ? (s.winner === s.you ? "You stopped the train!" : s.players[s.winner].name + " wins!") : "End of the line.";
            const p = document.createElement("p"); p.textContent = s.reason;
            const b = document.createElement("button"); b.type = "button"; b.textContent = local ? "Ride again →" : "Back to the platform";
            on(b, "click", () => local ? practice() : perform(leave));
            dialog.append(stamp, title, p, b);
          }
          overlay.append(dialog);
        }
      }
    }

    /* ---------- the loop ---------- */
    function makeDemo() {
      const d = new LocalRide(w, "Jordan", Math.floor(Math.random() * 900000), 90);
      d.s.countdown = 0; d.s.phase = "running";
      if (reduced) d.s.elapsed = 14;
      return d;
    }
    let previous = performance.now(), accumulator = 0, demoAcc = 0, uiAt = 0, demoDone = 0;
    function frame(now) {
      if (stopped) return;
      const dt = Math.min(.1, Math.max(0, (now - previous) / 1000)); previous = now;
      if (local && !document.hidden) {
        accumulator += dt;
        while (accumulator >= .05) { local.input("you", takeInput()); local.step(.05); accumulator -= .05; }
      } else accumulator = 0;
      if (!s) {
        if (!demo) { demo = makeDemo(); resetScene(); }
        if (!reduced && visible && !document.hidden) {
          demoAcc += dt;
          while (demoAcc >= .05) {
            if (demo.s.phase === "running") demo.input("you", demo.bot(demo.s.players.you));
            demo.step(.05); demoAcc -= .05;
          }
          if (demo.s.phase === "finished" && (demoDone += dt) > 4) { demo = makeDemo(); demoDone = 0; resetScene(); }
        }
      } else if (demo) { demo = null; }
      const st = s || demo.s;
      if (visible || s) {
        const running = st.phase === "running" && !st.cinematic;
        if (running) visualTime += dt;
        visualTime += (st.elapsed - visualTime) * Math.min(1, dt * 6);
        if (Math.abs(st.elapsed - visualTime) > 1.5) visualTime = st.elapsed;
        const night = document.documentElement.getAttribute("data-sky") === "night" ? 1 : 0;
        nightness = reduced ? night : nightness + clamp(night - nightness, -dt / 3, dt / 3);
        updateRiders(st, dt);
        updateShow(st, dt);
        if (!st.cinematic && !show) petClock += dt;
        if (!show) followCamera(st, dt);
        updateParticles(dt);
        cam.shake = Math.max(0, cam.shake - dt * 12);
        draw(st, now);
      }
      if (now - uiAt > 100) { updateUI(); uiAt = now; }
      raf = requestAnimationFrame(frame);
    }
    if (window.__TP_DEBUG__) root.tpDebug = {get s() { return s; }, get local() { return local; }, get demo() { return demo; }, get show() { return show; }, get cam() { return cam; }, get view() { return view; }, practice, setImmersive, setTouchMode, LocalRide, world: w, objectList, get pets() { return pets; }};
    fitCanvas(); updateUI(); raf = requestAnimationFrame(frame);
    return function cleanup() {
      stopped = true; clearInput(); controller.abort(); clearTimeout(networkTimer); cancelAnimationFrame(raf); resize.disconnect();
      if (seen) seen.disconnect();
      document.documentElement.classList.remove("tp-locked");
      if (document.fullscreenElement === stage) document.exitFullscreen().catch(() => {});
      if (inflight) inflight.abort(); if (audio) audio.close().catch(() => {});
    };
  }
})();
