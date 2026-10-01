/* ================================================================
   script.js: every script for the beacon center campus map page.
   part 1 is the map widget, part 2 is the page around it. each
   part is wrapped in its own function so their names never mix.
   the map runs first because the page script calls window.beaconMap.
   ================================================================ */

/* ================================================================
   script: widget behavior
   plain javascript, no libraries. wrapped in one function so
   none of these names leak into the host website.
   ================================================================ */
(() => {
  // ---------- script: 1. dom helpers ----------

  // variable: the widget root, every lookup is scoped inside it
  const root = document.getElementById('beaconMap');
  // function: find one element inside the widget
  const $ = (selector) => root.querySelector(selector);
  // variable: the side panel, shows welcome, sign in, home and the new flag form
  const panel = $('#bcPanel');
  // variable: the flag popup that opens beside a clicked marker
  const popup = $('#bcPopup');
  // variable: the map area the popup is positioned inside
  const mapArea = $('.bcMap');

  // ---------- script: 2. settings and demo data ----------

  // variable: fake "current time" for the demo
  const demoNow = '2026-09-21T12:00';
  // variable: how many active flags one student may have
  const maxFlags = 3;
  // variable: flag slots, each student can fill each slot once [value, label]
  const flagSlots = [
    ['current', 'Current event'],
    ['later', 'Later event'],
    ['other', 'Other use'],
  ];
  // variable: meetup places. x / y are % positions on the map, label is the short map text, lonLat kept for real gps later
  const places = {
    'Campus Center': { x: 61.46, y: 63.4, label: 'Campus Center', lonLat: [-71.03666, 42.31296] },
    'Healey Library': { x: 37.13, y: 54.02, label: 'Healey Library', lonLat: [-71.03972, 42.31362] },
    'University Hall': { x: 70.31, y: 58.3, label: 'University Hall', lonLat: [-71.03555, 42.31332] },
    'Clark Athletic Center': { x: 38.28, y: 33.17, label: 'Clark Athletics', lonLat: [-71.03957, 42.31508] },
    'Wheatley Hall': { x: 48.49, y: 75.85, label: 'Wheatley', lonLat: [-71.03829, 42.31208] },
    'McCormack Hall': { x: 40.03, y: 66.38, label: 'McCormack', lonLat: [-71.03935, 42.31275] },
  };
  // variable: sample events on the map. type is 'group' or 'p2p' (two-person), visibility is 'public' or 'private'
  let events = [
    {
      id: 1,
      title: 'RPS between classes',
      place: 'Campus Center',
      type: 'group',
      visibility: 'public',
      owner: 'Maya R.',
      desc: 'A casual rock-paper-scissors meetup between classes. Find us in the main lobby; newcomers are welcome.',
      start: '2026-09-21T12:00',
      end: '2026-09-21T13:00',
      created: '2026-09-21T11:40',
      slot: 'current',
    },
    {
      id: 2,
      title: 'Beacon project catch-up',
      place: 'Healey Library',
      type: 'group',
      visibility: 'private',
      group: 'Beacon project team',
      owner: 'Jordan L.',
      desc: 'Our project team is meeting near the library entrance before finding a study space. Bring your map ideas and wireframes.',
      start: '2026-09-21T14:00',
      end: '2026-09-21T15:30',
      created: '2026-09-21T11:20',
      slot: 'later',
    },
    {
      id: 3,
      title: 'Meet Alex after class',
      place: 'University Hall',
      type: 'p2p',
      visibility: 'private',
      owner: 'Alex K.',
      peer: 'Alex K.',
      consent: true,
      desc: 'Meet in the main lobby after class, then walk over to the Campus Center together.',
      start: '2026-09-21T12:30',
      end: '2026-09-21T13:15',
      created: '2026-09-21T11:30',
      slot: 'other',
    },
  ];

  // ---------- script: 3. state (values that change while the page is open) ----------

  let signedIn = false; // variable: true after the demo sign-in
  let selectedId = null; // variable: id of the highlighted marker
  let nextId = 10; // variable: id handed to the next new flag
  let popupId = null; // variable: id of the flag shown in the popup, null when closed

  // ---------- script: 4. small helpers ----------

  // function: make user text safe to put inside html
  const escapeHtml = (text) =>
    String(text).replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  // function: turns a date string into short readable text, e.g. sep 21, 12:00 pm
  const formatDate = (value) =>
    new Date(value).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  // function: html for one sprite icon
  const icon = (name) => `<svg class="bcIcon" aria-hidden="true"><use href="#bcIcon${name}"/></svg>`;
  // function: html for one label / value row in the panel or popup
  const rowHtml = (label, value) => `<div class="bcRow"><span>${label}</span>${escapeHtml(value)}</div>`;
  // function: flags owned by the demo user
  const myFlags = () => events.filter((e) => e.owner === 'You');
  // function: look up one event by id
  const findEvent = (id) => events.find((e) => e.id === id);
  // function: show a message in the status line
  const announce = (message) => {
    const status = $('#bcStatus');
    status.hidden = false;
    status.textContent = message;
  };

  // ---------- script: 5. map rendering ----------

  // function: draw the place name labels once at start up
  function renderLabels() {
    // loop: one label per place, nudged just under the place point
    $('#bcLabels').innerHTML = Object.entries(places)
      .map(
        ([name, p]) =>
          `<span class="bcPlace" data-place="${escapeHtml(name)}" style="left:${p.x}%;top:${p.y + 1.8}%">${escapeHtml(p.label)}</span>`,
      )
      .join('');
  }

  // function: redraw the flag counter and every marker
  function renderMarkers() {
    $('#bcCount').textContent = signedIn ? `${myFlags().length} / ${maxFlags} flags used` : 'Campus preview';

    // criteria: guests only see public events, signed in students see all
    const visible = events.filter((e) => signedIn || e.visibility === 'public');

    // loop: build one marker button per visible event
    $('#bcMarkers').innerHTML = visible
      .map((e) => {
        const p = places[e.place];
        // variable: how many earlier events share this place, used to fan markers out sideways
        const stack = events.filter((o) => o.place === e.place && o.id < e.id).length;
        const isPrivate = e.visibility === 'private';
        const classes = ['bcMarker', e.type === 'p2p' && 'bcP2p', selectedId === e.id && 'bcSelected']
          .filter(Boolean)
          .join(' ');
        return (
          `<button class="${classes}" data-event="${e.id}" style="left:${p.x + stack * 5}%;top:${p.y}%" aria-label="${escapeHtml(e.title)}${isPrivate ? ', private' : ''}">` +
          `<span class="bcLighthouse" aria-hidden="true"></span>` +
          (isPrivate ? `<span class="bcPrivateBadge">${icon('Lock')}</span>` : '') +
          `</button>`
        );
      })
      .join('');

    // loop: give every marker its click handler
    $('#bcMarkers')
      .querySelectorAll('button')
      .forEach((button) => {
        button.onclick = () => {
          // criteria: guests are sent to sign in first
          if (!signedIn) return showLogin();
          selectedId = Number(button.dataset.event);
          renderMarkers();
          showSummary(selectedId);
        };
      });
  }

  // ---------- script: 6. flag popup (card beside the marker, bottom sheet on phones) ----------

  // function: fill the popup with html, show it next to the marker and move focus into it
  function openPopup(id, html) {
    popupId = id;
    popup.innerHTML = `<button class="bcPopupClose" aria-label="Close details">×</button><div class="bcPopupBody">${html}</div>`;
    popup.hidden = false;
    // event: × closes the popup
    popup.querySelector('.bcPopupClose').onclick = () => closePopup();
    placePopup();
    popup.focus({ preventScroll: true });
  }

  // function: hide the popup, clear the highlight, and optionally put focus back on the marker
  function closePopup(returnFocus = true) {
    // criteria: nothing to do if it is already closed
    if (popup.hidden) return;
    const id = popupId;
    popup.hidden = true;
    popupId = selectedId = null;
    renderMarkers();
    if (returnFocus) $(`[data-event="${id}"]`)?.focus();
  }

  // function: put the popup beside its marker, flipping sides and staying inside the map
  function placePopup() {
    const marker = $(`[data-event="${popupId}"]`);
    popup.classList.remove('bcOnLeft', 'bcOnRight');
    popup.style.left = popup.style.top = '';
    // criteria: on phones the css makes it a fixed bottom sheet, so no positioning is needed
    if (!marker || getComputedStyle(popup).position === 'fixed') return;

    const map = mapArea.getBoundingClientRect();
    const pin = marker.getBoundingClientRect();
    const gap = 10; // variable: space between marker and card
    const edge = 8; // variable: minimum space from the map edges
    const width = popup.offsetWidth;
    const height = popup.offsetHeight;
    const pinMiddle = pin.top + pin.height / 2 - map.top;

    // criteria: open on the right of the marker unless that would run off the map
    const onRight = pin.right - map.left + gap + width <= map.width - edge;
    const left = onRight ? pin.right - map.left + gap : pin.left - map.left - gap - width;
    // criteria: line the card up with the marker, but keep it inside the map top and bottom
    const top = Math.max(edge, Math.min(pinMiddle - 30, map.height - height - edge));

    popup.style.left = Math.max(edge, left) + 'px';
    popup.style.top = top + 'px';
    // variable: arrow height, clamped so it never slides off the card corners
    popup.style.setProperty('--arrowY', Math.max(12, Math.min(pinMiddle - top - 6, height - 24)) + 'px');
    popup.classList.add(onRight ? 'bcOnRight' : 'bcOnLeft');
  }

  // function: popup view 1, short summary of the selected event
  function showSummary(id) {
    const e = findEvent(id);
    // criteria: stop if the event no longer exists
    if (!e) return;

    // criteria: pick the visibility tag text
    let tag;
    if (e.visibility === 'public') tag = 'Public · All Beacon students';
    else if (e.type === 'p2p') tag = e.consent ? 'Private · Both students agreed' : 'Private · Consent pending';
    else tag = 'Private · ' + escapeHtml(e.group);

    openPopup(
      id,
      `
      <div class="bcEyebrow">${e.type === 'p2p' ? 'Two-person beacon' : 'Group beacon'}</div>
      <h3>${escapeHtml(e.title)}</h3>
      <div class="bcTag">${tag}</div>
      ${rowHtml('Meetup location', e.place)}
      ${rowHtml('Starts', formatDate(e.start))}
      ${rowHtml('Expires', formatDate(e.end))}
      <button class="bcButton bcPrimary bcFull" id="bcView">View event ${icon('ArrowRight')}</button>`,
    );

    // event: expand to the full details
    $('#bcView').onclick = () => showDetail(id);
  }

  // function: popup view 2, full event details with remove / consent actions
  function showDetail(id) {
    const e = findEvent(id);
    const isP2p = e.type === 'p2p';
    const isMine = e.owner === 'You';

    // criteria: who can see this event
    const audience = isP2p ? 'Only you and ' + e.peer : e.visibility === 'public' ? 'All Beacon students' : e.group;

    openPopup(
      id,
      `
      <div class="bcEyebrow">Event details</div>
      <h3>${escapeHtml(e.title)}</h3>
      <p>${escapeHtml(e.desc)}</p>
      ${rowHtml('Dropped by', e.owner)}
      ${rowHtml('Meetup location', e.place)}
      ${rowHtml('Visibility', audience)}
      ${rowHtml('Dropped', formatDate(e.created))}
      ${rowHtml('Starts → expires', formatDate(e.start) + ' → ' + formatDate(e.end))}
      ${isP2p ? rowHtml('Mutual consent', e.consent ? 'Both students have agreed' : 'Invitation pending; shared only after acceptance.') : ''}
      ${isP2p && !e.consent ? '<button class="bcButton bcFull" id="bcConsent">Simulate recipient acceptance</button>' : ''}
      ${isMine ? '<button class="bcButton bcFull" id="bcRemove">Remove my flag</button>' : ''}
      <button class="bcButton bcFull" id="bcBack">Back</button>`,
    );

    // event: back to the summary
    $('#bcBack').onclick = () => showSummary(id);

    // criteria: remove button only exists on your own flags
    if (isMine) {
      // event: delete the flag, free its slot and close the popup
      $('#bcRemove').onclick = () => {
        events = events.filter((v) => v.id !== id);
        closePopup(false);
        showHome();
        announce('Your flag was removed. Its slot is available again.');
      };
    }

    // criteria: consent button only exists on pending two-person flags
    if (isP2p && !e.consent) {
      // event: pretend the other student accepted
      $('#bcConsent').onclick = () => {
        e.consent = true;
        showDetail(id);
        announce('Demo recipient accepted. Both students can now see this beacon.');
      };
    }
  }

  // ---------- script: 7. side panel screens ----------

  // function: panel screen 1, demo sign in
  function showLogin() {
    panel.innerHTML = `
      <div class="bcEyebrow">1 · Start</div>
      <h3>Sign in to Beacon Center</h3>
      <p class="bcMuted">Preview account only. No real credentials needed.</p>
      <form class="bcFields" id="bcLogin">
        <label>Account name<input value="tyler.demo" readonly autocomplete="off"></label>
        <label>Password<input type="password" value="beacon-demo" readonly autocomplete="off"></label>
        <button type="button" class="bcButton bcPrimary bcFull">Enter demo map</button>
      </form>
      <div class="bcRow"><span>Student verification</span>UMass Boston student access will connect to Beacon Center.</div>`;

    const enter = $('#bcLogin button');
    // event: sign in, swap the toolbar buttons and open the first event
    enter.onclick = () => {
      signedIn = true;
      $('#bcStart').hidden = true;
      $('#bcDrop').hidden = false;
      $('#bcAccount').textContent = 'Tyler · Demo student account';
      selectedId = 1;
      renderMarkers();
      showHome();
      showSummary(1);
      announce('Demo signed in · Sep 21, 2026 · 12:00 PM');
    };
    enter.focus();
  }

  // function: panel screen 2, home after sign in, points people at the map
  function showHome() {
    panel.innerHTML = `
      <div class="bcEyebrow">Signed in</div>
      <h3>Pick a flag on the map</h3>
      <p class="bcMuted">Select any beacon to see its details, or drop your own.</p>
      ${rowHtml('Your flags', `${myFlags().length} of ${maxFlags} active`)}
      <button class="bcButton bcPrimary bcFull" id="bcHomeDrop">${icon('Plus')}Drop a flag</button>`;

    // event: same as the toolbar drop a flag button
    $('#bcHomeDrop').onclick = showCreateForm;
  }

  // function: panel screen 3, form for dropping a new flag
  function showCreateForm() {
    // criteria: block the form when all flags are used
    if (myFlags().length >= maxFlags) {
      return announce('You have 3 active flags. Remove one of your flags before adding another.');
    }
    // the form lives in the side panel, so close any open popup first
    closePopup(false);

    // variable: slots this student already filled
    const usedSlots = myFlags().map((e) => e.slot);
    // loop: slot options, used ones are disabled
    const slotOptions = flagSlots
      .map(([value, label]) => {
        const used = usedSlots.includes(value);
        return `<option value="${value}" ${used ? 'disabled' : ''}>${label}${used ? ' · used' : ''}</option>`;
      })
      .join('');
    // loop: one option per place
    const placeOptions = Object.keys(places)
      .map((name) => `<option>${escapeHtml(name)}</option>`)
      .join('');

    panel.innerHTML = `
      <div class="bcEyebrow">New beacon</div>
      <h3>Bring people together</h3>
      <form class="bcFields" id="bcCreate">
        <label>Flag type<select name="type"><option value="group">Group event</option><option value="p2p">Two-person meetup</option></select></label>
        <label>Flag slot<select name="slot">${slotOptions}</select></label>
        <label>Title<input name="title" required maxlength="65" placeholder="e.g. Study break and tic-tac-toe"></label>
        <label>Meetup location<select name="place">${placeOptions}</select></label>
        <div class="bcMuted">Example locations; final list needs campus approval.</div>
        <label id="bcVisibilityLabel">Visibility<select name="visibility"><option value="public">All Beacon students</option><option value="private">My group only</option></select></label>
        <label id="bcGroupLabel" hidden>Designated group<select name="group"><option>Beacon project team</option><option>Campus gaming group</option></select></label>
        <label id="bcPeerLabel" hidden>Invite a student<select name="peer"><option>Alex K.</option><option>Maya R.</option></select></label>
        <label>Starts<input name="start" type="datetime-local" value="2026-09-21T12:30" required></label>
        <label>Expires<input name="end" type="datetime-local" value="2026-09-21T13:30" required></label>
        <label>Brief description<textarea name="desc" maxlength="450" required placeholder="What’s happening, and where should people meet?"></textarea></label>
        <div class="bcMuted" id="bcConsentNote" hidden>Your invitation becomes shared after the other student accepts.</div>
        <div class="bcError" id="bcError" role="alert"></div>
        <button type="button" class="bcButton bcPrimary" id="bcSubmit">Drop flag</button>
        <button type="button" class="bcButton" id="bcCancel">Cancel</button>
      </form>`;

    const form = $('#bcCreate');
    const showError = (message) => {
      $('#bcError').textContent = message;
    };

    // function: show / hide fields to match the chosen flag type
    function updateFields() {
      const isP2p = form.elements.type.value === 'p2p';
      $('#bcVisibilityLabel').hidden = isP2p;
      $('#bcPeerLabel').hidden = !isP2p;
      $('#bcGroupLabel').hidden = isP2p || form.elements.visibility.value !== 'private';
      $('#bcConsentNote').hidden = !isP2p;
      $('#bcSubmit').textContent = isP2p ? 'Send demo invitation' : 'Drop flag';
    }

    // event: re-check fields when type or visibility changes
    form.elements.type.onchange = updateFields;
    form.elements.visibility.onchange = updateFields;
    // event: cancel goes back to the home screen
    $('#bcCancel').onclick = showHome;
    // event: enter key submits, except inside the description box
    form.onkeydown = (ev) => {
      if (ev.key === 'Enter' && ev.target.tagName !== 'TEXTAREA') {
        ev.preventDefault();
        $('#bcSubmit').click();
      }
    };

    // event: validate, then add the new flag
    $('#bcSubmit').onclick = () => {
      // criteria: built in browser checks (required, max length)
      if (!form.reportValidity()) return;

      const data = new FormData(form);
      const title = data.get('title').trim();
      const desc = data.get('desc').trim();
      const start = data.get('start');
      const end = data.get('end');
      const type = data.get('type');
      const slot = data.get('slot');

      // criteria: no blank text after trimming spaces
      if (!title || !desc) return showError('Enter a title and description.');
      // criteria: expiry must be after the start and after "now"
      if (new Date(end) <= new Date(start) || new Date(end) <= new Date(demoNow))
        return showError('Expiry must be after the start and the demo’s current time.');
      // criteria: flag limit and one flag per slot
      if (myFlags().length >= maxFlags || usedSlots.includes(slot))
        return showError('That flag slot is already in use.');

      // variable: the new flag, two-person flags are always private
      const flag = {
        id: nextId++,
        owner: 'You',
        title,
        desc,
        start,
        end,
        created: demoNow,
        type,
        slot,
        visibility: type === 'p2p' ? 'private' : data.get('visibility'),
        place: data.get('place'),
        peer: data.get('peer'),
        group: data.get('group'),
        consent: false,
      };
      events.push(flag);
      selectedId = flag.id;
      renderMarkers();
      showHome();
      showDetail(flag.id);
      announce(
        type === 'p2p'
          ? 'Demo invitation pending. Only your own marker is shown until acceptance.'
          : 'Your demo flag is on the map.',
      );
    };
  }

  // ---------- script: 8. buttons, keys and resizing ----------

  // event: start opens the sign in screen
  $('#bcStart').onclick = showLogin;
  // event: drop a flag opens the new flag form
  $('#bcDrop').onclick = showCreateForm;
  // event: toggle the sample gps marker (no real location is used)
  $('#bcGps').onclick = () => {
    const button = $('#bcGps');
    const show = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', String(show));
    $('#bcAvatar').hidden = !show;
    announce(
      show
        ? 'GPS preview only: sample avatar at a fixed position. No location is requested or shared.'
        : 'GPS placeholder hidden. Location sharing remains off.',
    );
  };

  // event: escape key closes the popup
  root.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closePopup();
  });
  // event: clicking empty map (the drawing itself, not a marker or the popup) closes the popup
  mapArea.addEventListener('click', (ev) => {
    if (ev.target.closest('.bcBase')) closePopup(false);
  });
  // event: keep the popup beside its marker when the widget changes size
  new ResizeObserver(() => {
    if (popupId !== null) placePopup();
  }).observe(mapArea);

  // ---------- script: 8b. page hooks, used by the homepage around the widget ----------
  window.beaconMap = {
    // function: flash a place label, and open its first visible flag when signed in
    focusPlace(name) {
      root.querySelectorAll('.bcPlace').forEach((l) => l.classList.toggle('bcFlash', l.dataset.place === name));
      const e = events.find((v) => v.place === name && (signedIn || v.visibility === 'public'));
      if (signedIn && e) {
        selectedId = e.id;
        renderMarkers();
        showSummary(e.id);
      } else
        announce(
          signedIn
            ? name + ' has no active flags right now.'
            : name + ' highlighted. Select Start to see flag details.',
        );
    },
    // function: open the drop-a-flag form, signing in first if needed
    create() {
      if (!signedIn) {
        showLogin();
        announce('Sign in with the demo account, then drop a flag.');
      } else showCreateForm();
    },
  };

  // ---------- script: 9. start up ----------
  renderLabels();
  renderMarkers();
})();

/* ================================================================
   script: page behavior
   toast, bottom nav, scroll spy, actions and map search.
   talks to the map only through window.beaconMap.
   ================================================================ */
(() => {
  // ---------- settings ----------
  const TOAST_MS = 3200; // how long a toast stays on screen
  const TOP_OFFSET = 60; // scroll distance where the dock switches back to Home
  const SPY_SECTIONS = ['campus-map']; // sections the dock follows

  // ---------- dom helpers ----------
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => document.querySelectorAll(selector);

  // ---------- elements ----------
  const toast = $('#toast');
  const searchInput = $('[data-search-input]');
  let toastTimer;

  // ---------- toast ----------
  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('is-visible'), TOAST_MS);
  }

  // ---------- scrolling and bottom nav ----------
  function scrollToSection(id) {
    document.getElementById(id).scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function setActiveNav(id) {
    $$('.dock [data-scroll]').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.scroll === id);
    });
  }

  // ---------- map search ----------
  // finds the first map place whose name contains the search text, then flashes it on the map
  function searchMap(text) {
    const query = text.toLowerCase().trim();
    if (!query) return;

    const match = [...$$('#beaconMap .bcPlace')].find(
      (label) => label.dataset.place.toLowerCase().includes(query) || label.textContent.toLowerCase().includes(query),
    );

    if (!match) {
      showToast(`No campus place matches "${text.trim()}".`);
      return;
    }
    scrollToSection('campus-map');
    window.beaconMap?.focusPlace(match.dataset.place);
  }

  // ---------- actions: one function per data-action value ----------
  const actions = {
    // "Create meetup" uses the map's own drop-a-flag flow
    create: () => {
      scrollToSection('campus-map');
      window.beaconMap?.create();
    },
  };

  // any data-action without its own function (chat, notifications, games, meetups, hubs)
  const showPlaceholder = () => showToast('This is a layout placeholder in the wireframe.');

  // ---------- event wiring ----------
  // one click listener for the whole page: data-scroll buttons and data-action buttons
  document.addEventListener('click', (event) => {
    const scrollButton = event.target.closest('[data-scroll]');
    if (scrollButton) {
      setActiveNav(scrollButton.dataset.scroll);
      scrollToSection(scrollButton.dataset.scroll);
      return;
    }

    const actionButton = event.target.closest('[data-action]');
    if (actionButton) {
      const run = actions[actionButton.dataset.action] || showPlaceholder;
      run(actionButton);
    }
  });

  // ---------- scroll spy: the dock follows the section in view ----------
  const spy = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting && window.scrollY >= TOP_OFFSET) setActiveNav(entry.target.id);
      });
    },
    { rootMargin: '-45% 0px -50% 0px' },
  );
  SPY_SECTIONS.forEach((id) => spy.observe(document.getElementById(id)));

  window.addEventListener(
    'scroll',
    () => {
      if (window.scrollY < TOP_OFFSET) setActiveNav('top');
    },
    { passive: true },
  );

  // ---------- search: Enter looks up a place on the map ----------
  searchInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') searchMap(searchInput.value);
  });
})();
