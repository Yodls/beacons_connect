/* Beacon Connect Revolutionary Naval boards; draws top-down ink ships.
   No enemy locations enter the DOM unless the server marks the ship sunk. */
(function (root) {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var art = '<path d="M5 14 Q15 3 35 3 L77 4 Q90 6 96 14 Q89 22 77 24 L35 25 Q15 25 5 14Z" fill="rgba(43,97,156,.35)" stroke="currentColor" stroke-width="2"/>'+
    '<path d="M17 14 L86 14 M23 8 L23 20 M78 9 L78 19" stroke="currentColor" stroke-width="1.4" fill="none"/>'+
    '<ellipse cx="46" cy="14" rx="8" ry="8" fill="none" stroke="currentColor" stroke-width="1.7"/>'+
    '<ellipse cx="68" cy="14" rx="5" ry="6" fill="none" stroke="currentColor" stroke-width="1.4"/>'+
    '<path d="M46 5 L57 14 L46 23 M68 8 L76 14 L68 20" stroke="currentColor" stroke-width="1.5" fill="none"/>'+
    '<path d="M31 7 L37 21 M35 8 L41 21 M86 11 L88 17" stroke="currentColor" stroke-width=".8" fill="none"/>';
  function drawShips(grid, ships) {
    if (!grid || !Array.isArray(ships)) return;
    grid.querySelectorAll('.naval-ship-overlay').forEach(function (el) { el.remove(); });
    var squares = grid.querySelectorAll('.naval-cell');
    if (squares.length !== 100) return;
    var gridBox = grid.getBoundingClientRect();
    ships.forEach(function (ship) {
      var cells = ship.cells;
      if (!Array.isArray(cells) || !cells.length || cells.some(function (n) {return !Number.isInteger(n) || n < 0 || n >= 100;})) return;
      var ends = cells.slice().sort(function (a, b) { return a - b; });
      var a = squares[ends[0]].getBoundingClientRect();
      var b = squares[ends[ends.length - 1]].getBoundingClientRect();
      var vertical = ends.length > 1 && ends[1] - ends[0] === 10;
      var svg = document.createElementNS(NS,'svg');
      svg.classList.add('naval-ship-overlay');
      svg.setAttribute('viewBox', vertical ? '0 0 28 100' : '0 0 100 28');
      svg.setAttribute('preserveAspectRatio','none');
      svg.setAttribute('aria-hidden','true');
      svg.style.left = (a.left - gridBox.left + 2) + 'px';
      svg.style.top = (a.top - gridBox.top + 2) + 'px';
      svg.style.width = (b.right - a.left - 4) + 'px';
      svg.style.height = (b.bottom - a.top - 4) + 'px';
      svg.style.color = 'var(--nav-ink)';
      var g = document.createElementNS(NS,'g');
      if (vertical) g.setAttribute('transform','translate(28 0) rotate(90)');
      g.innerHTML = art;
      svg.appendChild(g);
      grid.appendChild(svg);
    });
  }
  function drawFromAttributes(scope) {
    (scope || document).querySelectorAll('.naval-square-grid[data-fleet]').forEach(function (grid) {
      try { drawShips(grid, JSON.parse(grid.dataset.fleet)); } catch (err) { /* missing or stale data */ }
    });
  }
  function mountMultiplayer() {
    var rootEl = document.querySelector('.naval-game');
    if (!rootEl || rootEl.dataset.navalMounted === 'yes') return;
    rootEl.dataset.navalMounted = 'yes';
    var shotForm = document.getElementById('naval-shot-form');
    var shotInput = document.getElementById('naval-selected-coordinate');
    var fire = document.getElementById('naval-fire');
    var aimLabel = document.getElementById('naval-aim-label');
    if (shotForm && shotInput && fire && aimLabel) {
      var enabled = shotForm.querySelectorAll('.naval-enemy-grid button:not([disabled])');
      enabled.forEach(function (square) {
        square.type = 'button'; // no-JS fallback: the original submit buttons work directly
        square.addEventListener('click',function () {
          enabled.forEach(function (b) { b.classList.remove('aimed'); b.setAttribute('aria-pressed','false'); });
          square.classList.add('aimed');
          square.setAttribute('aria-pressed','true');
          shotInput.disabled = false;
          shotInput.name = 'move';
          shotInput.value = square.value;
          var i = Number(square.value);
          aimLabel.textContent = 'Target: ' + 'ABCDEFGHIJ'[i % 10] + (Math.floor(i / 10) + 1);
          fire.disabled = false;
        });
      });
      shotForm.addEventListener('submit',function () { fire.disabled = true; });
    }
    drawFromAttributes(rootEl);
    var job;
    var rerender = function () { cancelAnimationFrame(job); job = requestAnimationFrame(function () { drawFromAttributes(rootEl); }); };
    if (typeof ResizeObserver !== 'undefined') { var ro = new ResizeObserver(rerender); rootEl.querySelectorAll('.naval-square-grid').forEach(function (g) {ro.observe(g);}); }
    else window.addEventListener('resize',rerender);
  }
  root.BeaconNaval = { drawShips:drawShips, drawFromAttributes:drawFromAttributes, mountMultiplayer:mountMultiplayer };
})(window);
