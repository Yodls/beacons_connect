/* Beacons Connect chess: pick a piece up, carry it, put it down.

   Mounted by chat.html on load and again after every live swap, the way
   naval-battleship.js is. Everything it needs is on the board's markup
   (_chess_board.html), so it never asks the server for anything: the legal
   moves, the last move and whose turn it is are all data- attributes.

   A move is sent through the move form with requestSubmit(), which the
   page's enhancer turns into a fetch and a swap -- no reload. The piece is
   already where it was dropped, so the board that comes back looks the same.

   No innerHTML anywhere: the chat page is audited for it, and nothing here
   needs it. */
(function (root) {
  'use strict';

  var GLYPH = { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' };
  var TEXT = '︎';     // text presentation: iOS otherwise draws the pawn as an emoji
  var SOUND_KEY = 'beacon-chess-sound';
  var reduce = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The board as it was before a live swap, so a captured piece can be shown
  // leaving. Set by before(), used once by the next mount().
  var snapshot = null;

  // Whether a piece is in the hand, chosen, or waiting on the promotion
  // picker. A live swap in the middle of that would replace the board under
  // the player's finger -- the other player arriving or leaving changes the
  // pane -- so chat.html asks first, and whenIdle() runs the swap the
  // moment the hand is empty. Only the latest waiting swap is kept.
  var busy = false;
  var waiting = null;
  function setBusy(on) {
    busy = on;
    if (!on && waiting) { var run = waiting; waiting = null; run(); }
  }
  function whenIdle(fn) {
    if (!busy) { fn(); } else { waiting = fn; }
  }
  // A move is being sent: the board that comes back replaces any swap that
  // was waiting, so that one is dropped rather than shown first.
  function sending() { waiting = null; busy = false; }

  // ---------------------------------------------------------------- storage
  function stored(store, key) {
    try { return root[store].getItem(key); } catch (e) { return null; }
  }
  function store(store, key, value) {
    try { root[store].setItem(key, value); } catch (e) { /* private mode */ }
  }

  // ---------------------------------------------------------------- sound
  // Synthesised, so there are no files: a short band-passed noise burst over
  // a falling low tone reads as wood on wood.
  var audioCtx = null;
  var played = [];
  function soundOn() { return stored('localStorage', SOUND_KEY) === 'on'; }
  function audio() {
    var A = root.AudioContext || root.webkitAudioContext;
    if (!A) { return null; }
    if (!audioCtx) { audioCtx = new A(); }
    if (audioCtx.state === 'suspended' && audioCtx.resume) { audioCtx.resume(); }
    return audioCtx;
  }
  function knock(ac, t, freq, len, gain) {
    var n = Math.max(1, Math.floor(ac.sampleRate * len));
    var buf = ac.createBuffer(1, n, ac.sampleRate);
    var d = buf.getChannelData(0);
    for (var i = 0; i < n; i += 1) { d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 4); }
    var src = ac.createBufferSource();
    src.buffer = buf;
    var bp = ac.createBiquadFilter();
    bp.type = 'bandpass'; bp.frequency.value = freq; bp.Q.value = 3.5;
    var g = ac.createGain();
    g.gain.value = gain;
    src.connect(bp); bp.connect(g); g.connect(ac.destination);
    src.start(t);
    var o = ac.createOscillator();
    var og = ac.createGain();
    o.frequency.setValueAtTime(freq / 4, t);
    o.frequency.exponentialRampToValueAtTime(freq / 9, t + len);
    og.gain.setValueAtTime(gain * 0.45, t);
    og.gain.exponentialRampToValueAtTime(0.001, t + len);
    o.connect(og); og.connect(ac.destination);
    o.start(t); o.stop(t + len + 0.02);
  }
  function play(kind) {
    if (!soundOn()) { return; }
    var ac = audio();
    if (!ac) { return; }
    played.push(kind);
    var t = ac.currentTime + 0.01;
    if (kind === 'capture') {
      knock(ac, t, 850, 0.09, 0.9);
      knock(ac, t + 0.075, 600, 0.12, 0.85);
    } else if (kind === 'check') {
      knock(ac, t, 1000, 0.08, 0.8);
      var o = ac.createOscillator();
      var g = ac.createGain();
      o.type = 'sine'; o.frequency.value = 1320;
      g.gain.setValueAtTime(0.22, t + 0.06);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
      o.connect(g); g.connect(ac.destination);
      o.start(t + 0.06); o.stop(t + 0.52);
    } else {
      knock(ac, t, 1100, 0.08, 0.9);
    }
  }

  // ---------------------------------------------------------------- motion
  // FLIP: put the piece back where it came from without a transition, then
  // let it go and let the transition carry it home.
  function slide(pc, dx, dy) {
    if (reduce || (!dx && !dy)) { return; }
    pc.style.transition = 'none';
    pc.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
    pc.getBoundingClientRect();
    pc.style.transition = '';
    pc.classList.add('arrive');
    root.requestAnimationFrame(function () { pc.style.transform = ''; });
    root.setTimeout(function () { pc.classList.remove('arrive'); }, 450);
  }
  function offset(fromEl, toEl) {
    var a = fromEl.getBoundingClientRect();
    var b = toEl.getBoundingClientRect();
    return [a.left - b.left, a.top - b.top];
  }
  function pieceEl(letter) {
    var s = document.createElement('span');
    s.className = 'pc ' + (letter === letter.toUpperCase() ? 'w' : 'b');
    s.dataset.piece = letter;
    s.textContent = GLYPH[letter.toLowerCase()] + TEXT;
    return s;
  }
  function fadeOut(square, letter) {
    if (!square || !letter || reduce) { return; }
    var gone = pieceEl(letter);
    gone.style.opacity = '1';
    square.appendChild(gone);
    gone.getBoundingClientRect();
    gone.classList.add('taken');
    root.setTimeout(function () { gone.remove(); }, 450);
  }
  // Where the rook goes when a king castles: e1g1 moves h1 to f1, and so on.
  function castleRook(from, to) {
    var rank = from[1];
    if (to[0] === 'g') { return ['h' + rank, 'f' + rank]; }
    if (to[0] === 'c') { return ['a' + rank, 'd' + rank]; }
    return null;
  }

  // The other player's move (or the last move, the first time this tab sees
  // the game): slide it in, and show anything it took leaving.
  function arrive(set, board, last, before) {
    var from = last.slice(0, 2);
    var to = last.slice(2, 4);
    var sq = function (n) { return board.querySelector('.sq[data-sq="' + n + '"]'); };
    var toSq = sq(to);
    var fromSq = sq(from);
    var pc = toSq && toSq.querySelector('.pc');
    if (!pc || !fromSq) { return; }
    var d = offset(fromSq, toSq);
    slide(pc, d[0], d[1]);
    var kind = pc.dataset.piece.toLowerCase();
    var took = false;
    if (kind === 'k' && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
      var rook = castleRook(from, to);
      var rTo = rook && sq(rook[1]);
      var rPc = rTo && rTo.querySelector('.pc');
      if (rPc) { var r = offset(sq(rook[0]), rTo); slide(rPc, r[0], r[1]); }
    }
    if (before && before.pieces) {
      if (before.pieces[to]) { fadeOut(toSq, before.pieces[to]); took = true; }
      // En passant: a pawn moving diagonally onto an empty square took the
      // pawn beside it, on the rank it came from.
      if (kind === 'p' && from[0] !== to[0] && !before.pieces[to]) {
        var passed = to[0] + from[1];
        if (before.pieces[passed]) { fadeOut(sq(passed), before.pieces[passed]); took = true; }
      }
    }
    set.dataset.arrived = to;
    play(took ? 'capture' : (board.querySelector('.sq.check') ? 'check' : 'move'));
  }

  // ---------------------------------------------------------------- mount
  function before(scope) {
    var set = (scope || document).querySelector('.chess-set');
    if (!set) { snapshot = null; return; }
    var pieces = {};
    set.querySelectorAll('.sq').forEach(function (s) {
      var pc = s.querySelector('.pc');
      if (pc) { pieces[s.dataset.sq] = pc.dataset.piece; }
    });
    snapshot = { game: set.dataset.game, pieces: pieces };
  }

  function mount(scope) {
    var set = (scope || document).querySelector('.chess-set');
    if (!set || set.dataset.mounted === 'yes') { return; }
    set.dataset.mounted = 'yes';
    var board = set.querySelector('.chess-board');
    var form = set.querySelector('#chessform');
    var field = set.querySelector('#chessmove');
    var frame = set.querySelector('.chess-frame');
    var game = set.dataset.game;
    var last = set.dataset.last || '';
    var seenKey = 'beacon-chess-seen-' + game;
    var legal = (set.dataset.legal || '').split(' ').filter(Boolean);

    // The sound key.
    var key = set.querySelector('.chess-sound');
    if (key) {
      key.setAttribute('aria-pressed', soundOn() ? 'true' : 'false');
      key.addEventListener('click', function () {
        var on = !soundOn();
        store('localStorage', SOUND_KEY, on ? 'on' : 'off');
        key.setAttribute('aria-pressed', on ? 'true' : 'false');
        if (on) { audio(); play('move'); }
      });
    }

    // A move this tab has not shown yet: the other player's, or the first
    // sight of the game. Your own was recorded as seen before it was sent,
    // so the board coming back does not play it a second time.
    var prior = snapshot && snapshot.game === game ? snapshot : null;
    snapshot = null;
    if (last && stored('sessionStorage', seenKey) !== last) {
      store('sessionStorage', seenKey, last);
      arrive(set, board, last, prior);
    }

    var live = set.dataset.turn === '1' && legal.length && field && !field.disabled && board;
    if (!live) { return; }

    // from -> { to: "" for a plain move, "qrbn" for a promotion }
    var targets = {};
    legal.forEach(function (m) {
      var f = m.slice(0, 2);
      var t = m.slice(2, 4);
      targets[f] = targets[f] || {};
      targets[f][t] = (targets[f][t] || '') + (m.slice(4) || '');
    });
    var sqs = {};
    board.querySelectorAll('.sq').forEach(function (s) { sqs[s.dataset.sq] = s; });

    var picked = null;   // a square chosen by tapping, waiting for a second tap
    var drag = null;     // a piece in the hand
    var promo = null;    // the open promotion picker

    function clearMarks() {
      Object.keys(sqs).forEach(function (n) { sqs[n].classList.remove('from', 'hint', 'take', 'over'); });
      // Nothing chosen and nothing in the hand: the board may be swapped.
      if (!drag && !picked && !promo) { setBusy(false); }
    }
    function showFrom(from) {
      clearMarks();
      setBusy(true);
      sqs[from].classList.add('from');
      Object.keys(targets[from] || {}).forEach(function (t) {
        sqs[t].classList.add('hint');
        if (sqs[t].querySelector('.pc')) { sqs[t].classList.add('take'); }
      });
    }
    function legalTo(from, to) { return !!(targets[from] && targets[from][to] !== undefined); }

    function commit(from, to, choice) {
      var uci = from + to + (choice || '');
      var pc = sqs[from].querySelector('.pc');
      var victim = sqs[to].querySelector('.pc');
      // En passant takes the pawn beside the target, not on it.
      if (!victim && pc && pc.dataset.piece.toLowerCase() === 'p' && from[0] !== to[0]) {
        victim = sqs[to[0] + from[1]] && sqs[to[0] + from[1]].querySelector('.pc');
      }
      play(victim ? 'capture' : 'move');
      if (victim) { victim.remove(); }
      if (pc) {
        if (choice) {
          var letter = pc.dataset.piece === 'P' ? choice.toUpperCase() : choice;
          pc.dataset.piece = letter;
          pc.textContent = GLYPH[choice] + TEXT;
        }
        sqs[to].appendChild(pc);
        if (pc.dataset.piece.toLowerCase() === 'k' && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
          var rook = castleRook(from, to);
          var rPc = rook && sqs[rook[0]] && sqs[rook[0]].querySelector('.pc');
          if (rPc) { sqs[rook[1]].appendChild(rPc); }
        }
      }
      sending();
      clearMarks();
      board.classList.remove('live');
      store('sessionStorage', seenKey, uci);
      field.value = uci;
      set.dataset.sent = uci;
      if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
    }

    function closePromo() {
      if (promo) { promo.remove(); promo = null; }
    }
    function choosePromotion(from, to, options) {
      closePromo();
      var pc = sqs[from].querySelector('.pc');
      var white = pc && pc.classList.contains('w');
      var target = sqs[to].getBoundingClientRect();
      var box = frame.getBoundingClientRect();
      promo = document.createElement('div');
      promo.className = 'chess-promo';
      promo.setAttribute('role', 'dialog');
      promo.setAttribute('aria-label', 'Promote to');
      promo.style.left = (target.left - box.left - 6) + 'px';
      promo.style.width = (target.width + 12) + 'px';
      // Downward from a top-row square, upward from a bottom-row one.
      var down = target.top - box.top < box.height / 2;
      if (down) { promo.style.top = (target.top - box.top - 6) + 'px'; }
      else { promo.style.bottom = (box.bottom - target.bottom - 6) + 'px'; }
      ['q', 'r', 'b', 'n'].filter(function (c) { return options.indexOf(c) >= 0; }).forEach(function (c) {
        var b = document.createElement('button');
        b.type = 'button';
        b.dataset.promote = c;
        b.setAttribute('aria-label', { q: 'Queen', r: 'Rook', b: 'Bishop', n: 'Knight' }[c]);
        var g = document.createElement('span');
        g.className = 'pc ' + (white ? 'w' : 'b');
        g.style.fontSize = (target.width * 0.8) + 'px';
        g.textContent = GLYPH[c] + TEXT;
        b.appendChild(g);
        b.addEventListener('click', function (ev) {
          ev.stopPropagation();
          closePromo();
          commit(from, to, c);
        });
        promo.appendChild(b);
      });
      frame.appendChild(promo);
      var first = promo.querySelector('button');
      if (first) { first.focus({ preventScroll: true }); }
    }

    function tryMove(from, to) {
      if (!legalTo(from, to)) { return false; }
      var options = targets[from][to];
      if (options) { choosePromotion(from, to, options); } else { commit(from, to, ''); }
      return true;
    }

    // ---- the piece in the hand
    function makeGhost(pc, sq) {
      var r = sq.getBoundingClientRect();
      var g = document.createElement('div');
      g.className = 'chess-ghost ' + (pc.classList.contains('w') ? 'w' : 'b');
      g.style.width = r.width + 'px';
      g.style.height = r.height + 'px';
      g.style.fontSize = getComputedStyle(pc).fontSize;
      g.textContent = pc.textContent;
      document.body.appendChild(g);
      return g;
    }
    function place(g, x, y, scale) {
      var w = parseFloat(g.style.width);
      g.style.transform = 'translate(' + (x - w / 2) + 'px,' + (y - w * 0.62) + 'px) scale(' + scale + ')';
    }
    function squareAt(x, y) {
      var el = document.elementFromPoint(x, y);
      var s = el && el.closest ? el.closest('.sq') : null;
      return s && board.contains(s) ? s : null;
    }
    // Fly the ghost to a square's centre, then call `done`.
    function landGhost(g, sq, done) {
      var r = sq.getBoundingClientRect();
      if (reduce) { g.remove(); done(); return; }
      g.classList.add('home');
      place(g, r.left + r.width / 2, r.top + r.height * 0.62, 1);
      root.setTimeout(function () { g.remove(); done(); }, 210);
    }

    board.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0 || drag) { return; }
      var sq = ev.target.closest ? ev.target.closest('.sq') : null;
      if (!sq || !board.contains(sq)) { return; }
      var name = sq.dataset.sq;
      closePromo();
      // A second tap, on somewhere the chosen piece can go.
      if (picked && picked !== name && legalTo(picked, name)) {
        ev.preventDefault();
        var f = picked;
        picked = null;
        tryMove(f, name);
        return;
      }
      var pc = sq.querySelector('.pc.mine');
      if (!pc || !targets[name]) {
        if (picked) { picked = null; clearMarks(); }
        return;
      }
      ev.preventDefault();
      // Set before showFrom: its clearMarks would otherwise see an empty
      // hand and let a waiting swap replace the board mid-pickup.
      drag = { from: name, sq: sq, pc: pc, id: ev.pointerId, x: ev.clientX, y: ev.clientY, moved: false, ghost: null };
      showFrom(name);
      if (board.setPointerCapture) { board.setPointerCapture(ev.pointerId); }
    });

    board.addEventListener('pointermove', function (ev) {
      if (!drag || ev.pointerId !== drag.id) { return; }
      if (!drag.moved && Math.abs(ev.clientX - drag.x) + Math.abs(ev.clientY - drag.y) < 5) { return; }
      if (!drag.moved) {
        drag.moved = true;
        drag.ghost = makeGhost(drag.pc, drag.sq);
        set.classList.add('dragging');
      }
      place(drag.ghost, ev.clientX, ev.clientY, 1.18);
      var over = squareAt(ev.clientX, ev.clientY);
      Object.keys(sqs).forEach(function (n) { sqs[n].classList.toggle('over', sqs[n] === over && legalTo(drag.from, n)); });
    });

    function endDrag(ev, cancelled) {
      if (!drag || (ev && ev.pointerId !== drag.id)) { return; }
      var d = drag;
      drag = null;
      set.classList.remove('dragging');
      if (!d.moved) {
        // A tap: choose this piece, or put it down again.
        if (picked === d.from) { picked = null; clearMarks(); }
        else { picked = d.from; }
        return;
      }
      var over = cancelled ? null : squareAt(ev.clientX, ev.clientY);
      var to = over && over.dataset.sq;
      if (to && legalTo(d.from, to)) {
        landGhost(d.ghost, over, function () { tryMove(d.from, to); });
      } else {
        // Not somewhere it can go: back where it came from.
        landGhost(d.ghost, d.sq, function () { clearMarks(); });
      }
    }
    board.addEventListener('pointerup', function (ev) { endDrag(ev, false); });
    board.addEventListener('pointercancel', function (ev) { endDrag(ev, true); });

    // Enter or Space on a focused square: the same as a tap. A click made
    // by the keyboard has detail 0; one made by a pointer was already
    // handled above.
    board.addEventListener('click', function (ev) {
      if (ev.detail !== 0) { return; }
      var sq = ev.target.closest ? ev.target.closest('.sq') : null;
      if (!sq) { return; }
      var name = sq.dataset.sq;
      if (picked && picked !== name && legalTo(picked, name)) {
        var f = picked; picked = null; tryMove(f, name); return;
      }
      if (sq.querySelector('.pc.mine') && targets[name]) {
        if (picked === name) { picked = null; clearMarks(); }
        else { picked = name; showFrom(name); }
      }
    });

    set.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape') { return; }
      if (promo) { closePromo(); clearMarks(); }
      if (picked) { picked = null; clearMarks(); }
    });
    // The one document listener (installed below, once) closes whichever
    // picker is open; it reads this rather than each mount adding its own.
    openPicker = function (target) {
      if (promo && !promo.contains(target)) { closePromo(); clearMarks(); }
    };
  }

  // A tap anywhere else closes the promotion picker and puts the pawn back.
  // Installed once for the page, not once per mount: the board is mounted
  // again after every live swap, and listeners on the document outlive it.
  var openPicker = function () {};
  document.addEventListener('pointerdown', function (ev) { openPicker(ev.target); }, true);

  root.BeaconChess = {
    mount: mount, before: before, play: play, played: played,
    busy: function () { return busy; }, whenIdle: whenIdle
  };
})(window);
