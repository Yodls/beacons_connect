/* Beacons Connect Battleship: aim, fire, and the shot that has just landed.

   The board is server-rendered (templates/_battleship_board.html) and works
   without this file: every open square is then a submit button and fires at
   once. With it, firing is two steps -- pick a square, then Fire -- so a slip
   of the thumb does not waste a shot. The arrow keys move between open
   squares; Enter aims, and Enter again on the aimed square fires.

   chat.html calls BeaconBattleship.mount() on load and after every live swap
   of the game pane. Each mount compares the shots on the board with the ones
   this tab saw last time (sessionStorage), so the newest shot on either chart
   lands with a cannonball and a splash or a burst, and the bar under enemy
   waters says what happened. Nothing here decides anything: the server's
   rules do, and the enemy's ships are never in the page until they are sunk. */
(function (root) {
  "use strict";
  var LETTERS = "ABCDEFGHIJ";
  var reduced = root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function coord(i) { return LETTERS[i % 10] + (Math.floor(i / 10) + 1); }

  function remember(key, value) {
    try {
      if (value === undefined) { return root.sessionStorage.getItem(key); }
      if (value === null) { root.sessionStorage.removeItem(key); } else { root.sessionStorage.setItem(key, value); }
    } catch (e) { /* private window or storage off: the board still works */ }
    return null;
  }

  // Every square that has been fired at on one chart: {index: "hit"|"miss"|"sunk"}
  function shotsOn(grid) {
    var out = {};
    if (!grid) { return out; }
    Array.prototype.forEach.call(grid.children, function (el, i) {
      if (el.classList.contains("sunk")) { out[i] = "sunk"; }
      else if (el.classList.contains("hit")) { out[i] = "hit"; }
      else if (el.classList.contains("miss")) { out[i] = "miss"; }
    });
    return out;
  }

  function sentence(side, i, what, ship, them) {
    var at = coord(Number(i));
    if (side === "theirs") {
      if (what === "sunk") { return "You sank their " + (ship || "ship") + " at " + at + "!"; }
      if (what === "hit") { return "A hit at " + at + "!"; }
      return "You fired at " + at + " and missed.";
    }
    if (what === "sunk") { return them + " sank your " + (ship || "ship") + " at " + at + "."; }
    if (what === "hit") { return them + " hit your " + (ship || "ship") + " at " + at + "."; }
    return them + " fired at " + at + " and missed.";
  }

  function land(cell, what, sprite) {
    cell.classList.add("fresh");
    if (reduced || !sprite) { return; }
    var NS = "http://www.w3.org/2000/svg";
    function fx(kind, symbol) {
      var svg = document.createElementNS(NS, "svg");
      svg.setAttribute("class", "bs-fx " + kind);
      svg.setAttribute("viewBox", "0 0 14 14");
      svg.setAttribute("aria-hidden", "true");
      var use = document.createElementNS(NS, "use");
      use.setAttribute("href", sprite + "#" + symbol);
      svg.appendChild(use);
      cell.appendChild(svg);
      return svg;
    }
    var made = [fx("ball", "bs-ball"), what === "miss" ? fx("splash", "bs-splash") : fx("burst", "bs-burst")];
    root.setTimeout(function () {
      made.forEach(function (el) { if (el.parentNode) { el.parentNode.removeChild(el); } });
    }, 1400);
  }

  // What has landed since this tab last looked, on both charts.
  function landed(game) {
    var id = game.getAttribute("data-game"), seat = game.getAttribute("data-seat");
    var them = game.getAttribute("data-them") || "Your opponent";
    var sprite = game.getAttribute("data-sprite");
    var grids = {
      theirs: game.querySelector(".bs-enemy .bs-cells"),
      mine: game.querySelector(".bs-own .bs-cells")
    };
    var now = { theirs: shotsOn(grids.theirs), mine: shotsOn(grids.mine) };
    var key = "bs-seen:" + id + ":" + seat;
    var before = null;
    try { before = JSON.parse(remember(key) || "null"); } catch (e) { before = null; }
    remember(key, JSON.stringify({ theirs: Object.keys(now.theirs), mine: Object.keys(now.mine) }));
    if (!before) { return; }          // the first look at this game: nothing is new

    var said = [];
    ["theirs", "mine"].forEach(function (side) {
      var grid = grids[side];
      if (!grid) { return; }
      var seen = {};
      (before[side] || []).forEach(function (i) { seen[i] = true; });
      Object.keys(now[side]).forEach(function (i) {
        if (seen[i]) { return; }
        var cell = grid.children[Number(i)];
        if (!cell) { return; }
        land(cell, now[side][i], sprite);
        said.push(sentence(side, i, now[side][i], cell.getAttribute("data-ship"), them));
      });
    });
    // The bar by Fire says only whose turn it is; what landed is shown on
    // the charts. The sentence is kept for screen readers.
    var news = game.querySelector(".bs-news");
    if (news && said.length) { news.textContent = said.join(" "); news.classList.add("sr"); }
  }

  function mount(scope) {
    var from = scope && scope.querySelector ? scope : document;
    var game = from.querySelector(".bs-harbor");
    if (!game || game.getAttribute("data-mounted")) { return; }
    game.setAttribute("data-mounted", "1");

    landed(game);

    var form = game.querySelector("form.bs-shot");
    if (!form) { return; }
    var cells = Array.prototype.slice.call(form.querySelectorAll("button.bs-cell"));
    var open = cells.filter(function (b) { return !b.disabled; });
    var fire = form.querySelector(".bs-fire");
    var target = form.querySelector(".bs-target");
    var prompt = form.querySelector(".bs-prompt");
    var reticle = form.querySelector(".bs-reticle");
    if (!open.length || !fire || !target) { return; }

    var aimKey = "bs-aim:" + game.getAttribute("data-game");
    var aimed = null;

    // Squares aim rather than fire; one of them is in the tab order at a time.
    open.forEach(function (b) {
      b.type = "button";
      b.tabIndex = -1;
      b.setAttribute("aria-pressed", "false");
    });
    open[0].tabIndex = 0;

    function rove(to) {
      open.forEach(function (o) { o.tabIndex = -1; });
      to.tabIndex = 0;
    }

    function aim(b) {
      if (aimed) { aimed.setAttribute("aria-pressed", "false"); }
      aimed = b;
      b.setAttribute("aria-pressed", "true");
      rove(b);
      var i = Number(b.value);
      if (reticle) {
        reticle.style.setProperty("--r", String(Math.floor(i / 10)));
        reticle.style.setProperty("--c", String(i % 10));
        reticle.hidden = false;
      }
      target.value = String(i);
      target.disabled = false;
      fire.disabled = false;
      fire.setAttribute("aria-label", "Fire at " + coord(i));
      remember(aimKey, String(i));
    }

    form.addEventListener("click", function (e) {
      var b = e.target && e.target.closest ? e.target.closest("button.bs-cell") : null;
      if (!b || open.indexOf(b) < 0) { return; }
      // Enter (or Space) on the square already aimed at fires; a pointer
      // only ever aims, so a double tap cannot fire by accident.
      if (b === aimed && e.detail === 0 && !fire.disabled) {
        if (form.requestSubmit) { form.requestSubmit(fire); } else { fire.click(); }
        return;
      }
      aim(b);
    });

    form.addEventListener("keydown", function (e) {
      var b = e.target && e.target.closest ? e.target.closest("button.bs-cell") : null;
      if (!b) { return; }
      var step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -10, ArrowDown: 10 }[e.key];
      if (!step) { return; }
      e.preventDefault();
      var j = Number(b.value);
      for (;;) {
        var r = Math.floor(j / 10), c = j % 10;
        if ((step === -1 && c === 0) || (step === 1 && c === 9) || (step === -10 && r === 0) || (step === 10 && r === 9)) { return; }
        j += step;
        if (!cells[j].disabled) { rove(cells[j]); cells[j].focus(); return; }
      }
    });

    form.addEventListener("submit", function (e) {
      var by = e.submitter;
      // Fire with nothing aimed at (the page is behind, say): send nothing.
      if (by === fire && target.disabled) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      remember(aimKey, null);
      fire.disabled = true;
    });
    // After a shot is sent, Fire stays down until the board comes back.
    form.addEventListener("beacon:done", function () {
      target.disabled = true;
      root.setTimeout(function () { fire.disabled = true; }, 0);
    });

    // Aim kept across a live swap (the other player arriving, say).
    var kept = remember(aimKey);
    if (kept !== null && cells[Number(kept)] && !cells[Number(kept)].disabled) { aim(cells[Number(kept)]); }
  }

  root.BeaconBattleship = { mount: mount };
})(window);
