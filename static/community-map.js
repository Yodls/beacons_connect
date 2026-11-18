/*
  Campus map (Leaflet + OpenStreetMap) for the Meet up page.

  What this file does, top to bottom:
    1. settings ........ map area, flag looks, how often to refresh
    2. page elements ... everything it drives, found by id in _community_map.html
    3. helpers ......... status line, requests, icons, text
    4. the map ......... Leaflet with OpenStreetMap tiles, landmarks, flags;
                         flags on the same spot share one pin, listed on a tap
    5. the flag card ... what you see when you tap a flag, and its buttons
    6. a meetup's pin .. the pin the "Post a meetup" form puts on the map
    7. saving .......... move or lock (PATCH), take down (DELETE)
    8. loading ......... the flag list, refreshed every 20 seconds
    9. start ........... wiring the buttons, "See on map" links, ?flag=<id>,
                         and window.BeaconMap for other scripts

  Every pin a student puts here is a meetup's, and it is made by posting
  the meetup: the form (_meetup_form.html) is a real form the page sends,
  and this file only lends it a pin to place. Who can do what is decided by
  the server (app.py, "Campus map"); this file only offers the buttons. Your
  own pin can be dragged on the map. A meetup's pin is seen by whoever the
  meetup is for: everyone, a club's members, the friends its author picked,
  or only the people who are in once its author locks it.
*/
(function () {
  "use strict";

  var root = document.getElementById("campusMapCard");
  if (!root || root.dataset.initialized === "1") { return; }
  root.dataset.initialized = "1";

  // ---------- 1. settings ----------
  var LEAFLET_VERSION = "1.9.4";
  var LEAFLET_CSS = "https://unpkg.com/leaflet@" + LEAFLET_VERSION + "/dist/leaflet.css";
  var LEAFLET_JS = "https://unpkg.com/leaflet@" + LEAFLET_VERSION + "/dist/leaflet.js";
  // OpenStreetMap's own tiles. They need the site's address as the referrer,
  // so they load on the live site but not from a file opened on a computer.
  var TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  // The campus area flags may go in (the dashed outline on the map).
  var CAMPUS_BOUNDS = [[42.3097, -71.0436], [42.3174, -71.0317]];
  // The zoom the map opens at, on the middle of that area: the whole campus
  // in view on a computer, its edges a step out on a phone. The map comes
  // back to this view when a meetup has just been posted (campusView).
  var CAMPUS_ZOOM = 16;
  // Close enough to put a pin on the right building: where the meetup
  // form's pin is placed (startPick).
  var PLACE_ZOOM = 17;
  // Close enough to tell one building's flags from the next: where a "See
  // on map" link or a flag picked from a list flies to.
  var FLAG_ZOOM = 18;
  var CAMPUS_CENTER_KEY = "campus-center";
  var REFRESH_MS = 20000;
  var SVG_NS = "http://www.w3.org/2000/svg";

  // How each kind of flag looks: pin colour, icon colour, sprite icon.
  var KINDS = {
    game:   { color: "#ff791f", ink: "#132245", icon: "game" },
    study:  { color: "#005a8b", ink: "#ffffff", icon: "book" },
    social: { color: "#78afe9", ink: "#132245", icon: "talk" },
    club:   { color: "#fede42", ink: "#132245", icon: "club" },
    event:  { color: "#132245", ink: "#ffffff", icon: "star" },
    // A campus-wide meetup (a fixture; "Campus-Wide" on the map). Gold,
    // because it is the one pin the university put there rather than a
    // student, and nothing else on the map is.
    home:   { color: "#fede42", ink: "#132245", icon: "stadium" }
  };

  // ---------- 2. page elements ----------
  function $(id) { return document.getElementById(id); }
  var el = {
    status: $("mapStatus"), fit: $("mapFit"),
    locate: $("mapLocate"),
    // The row the "Post a meetup" key stands in under the map, where the
    // screen is too small for it to stand on the map (homeKey, in 6). The
    // row steps aside while the form is open, and for a flag's card on a
    // phone.
    keys: root.querySelector(".map-drop-row"),
    // The map's own box: what floats on the map is in it.
    stage: $("mapStage"),
    // The "Post a meetup" key and the form it opens (see 6).
    meet: $("mapMeet"), meetForm: $("mapMeetForm")
  };
  var urls = {
    flags: root.dataset.flagsUrl,
    umb: root.dataset.umbLogo || "",
    healey: root.dataset.healeyLogo || "",
    rec: root.dataset.recLogo || "",
    chat: root.dataset.chatUrl || "",   // ends in /0: swap in the chat's id
    rsvp: root.dataset.rsvpUrl || "",   // contains /0/: swap in the meetup's id
    close: root.dataset.closeUrl || ""  // the same, for cancelling a meetup
  };

  // ---------- state ----------
  var map, placeLayer, flagLayer, userLayer;
  var info = { flags: [], categories: {}, places: [] };
  var mode = "idle";      // idle | pick
  var ready = false;      // the map is up: the keys under it can be pressed
  var pick = null;        // the meetup pin the form is placing (see 6)
  var selected = null;    // id of the flag shown in the card
  var dragging = false;   // a pin is being dragged: hold refreshes
  var focusId = root.dataset.focus || "";
  var confirmDown = null; // id of a flag whose "Cancel meetup" or "Take down" was pressed once
  var shownCard = "";     // what the card last showed, so refreshes don't rebuild it

  // ---------- 3. helpers ----------
  function announce(message, kind) {
    el.status.textContent = message || "";
    el.status.classList.toggle("error", kind === "error");
    el.status.classList.toggle("success", kind === "success");
    // Said while a card is open on the map: not across the card (5a).
    seatStatus();
  }

  function request(url, options) {
    options = options || {};
    options.headers = Object.assign({
      "Accept": "application/json",
      "X-Requested-With": "XMLHttpRequest"
    }, options.headers || {});
    return fetch(url, options).then(function (response) {
      if (response.status === 204) { return null; }
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) { throw new Error(body.error || "The map request failed."); }
        return body;
      });
    });
  }
  function sendJSON(url, method, body) {
    return request(url, { method: method, headers: { "Content-Type": "application/json" },
                          body: JSON.stringify(body) });
  }

  // An element with a class and text. textContent, never innerHTML, for
  // anything a student typed.
  function node(tag, className, text) {
    var e = document.createElement(tag);
    if (className) { e.className = className; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  // An icon from the page's sprite (templates/_icons.html).
  function spriteNode(name) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", "#icon-" + name);
    svg.appendChild(use);
    return svg;
  }
  // The same, as markup for Leaflet's divIcons. Names are fixed keys only.
  function spriteMarkup(name) {
    return '<svg class="icon" aria-hidden="true" focusable="false"><use href="#icon-' + name + '"/></svg>';
  }
  function kindOf(category) { return KINDS[category] || KINDS.event; }
  function labelOf(category) { return info.categories[category] || category; }

  function expiresText(value) {
    var remaining = new Date(value).getTime() - Date.now();
    if (remaining <= 0) { return "Expired"; }
    var minutes = Math.ceil(remaining / 60000);
    if (minutes < 60) { return "Ends in " + minutes + " min"; }
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return "Ends in " + hours + " hr" + (rest ? " " + rest + " min" : "");
  }
  function firstName(name) { return String(name || "").split(" ")[0]; }
  function listNames(names) {
    if (names.length <= 2) { return names.join(" and "); }
    return names.length + " friends";
  }
  // One line saying whose flag this is and who else can see it.
  function whoLine(flag) {
    var names = (flag.friends || []).map(function (f) { return firstName(f.name); });
    if (flag.mine) {
      // A meetup's card says who may see it on its own line.
      if (flag.event) { return "Your meetup"; }
      if (flag.visibility === "club") { return "You, for " + flag.club_name + " members"; }
      if (flag.visibility === "friends") { return "You, for " + (names.length ? listNames(names) : "your friends"); }
      return "Posted by you. Everyone can see it.";
    }
    if (flag.visibility === "club") { return flag.club_name + " members. Posted by " + flag.owner_name; }
    if (flag.visibility === "friends") { return "Shared with you by " + flag.owner_name; }
    if (flag.event) { return flag.owner_name + "'s meetup"; }
    return "Posted by " + flag.owner_name;
  }
  // Who a meetup's pin is for, when that is not everybody: one line on its
  // card, so nobody takes a pin for friends or a club for a public one.
  function audienceLine(flag) {
    if (flag.visibility === "going") { return "Locked: only people who are in can see it"; }
    if (flag.visibility === "club") { return "Only " + flag.club_name + " members can see it"; }
    if (flag.visibility === "friends") {
      if (!flag.mine) { return "Only friends " + firstName(flag.owner_name) + " picked can see it"; }
      var names = (flag.friends || []).map(function (f) { return firstName(f.name); });
      return "Only " + (names.length ? listNames(names) : "the friends you picked") + " can see it";
    }
    return "";
  }
  function findFlag(id) {
    for (var i = 0; i < info.flags.length; i += 1) {
      if (info.flags[i].id === id) { return info.flags[i]; }
    }
    return null;
  }
  function nearestPlaceName(latlng) {
    if (!info.places.length || !map) { return "Campus meetup point"; }
    var best = null;
    info.places.forEach(function (place) {
      var d = map.distance(latlng, [place.latitude, place.longitude]);
      if (!best || d < best.d) { best = { name: place.name, d: d }; }
    });
    return best.d <= 200 ? "Near " + best.name : "Campus meetup point";
  }

  // ---------- 4. the map ----------
  function loadLeaflet() {
    return new Promise(function (resolve, reject) {
      if (window.L) { resolve(window.L); return; }
      if (!document.getElementById("leaflet-css")) {
        var link = document.createElement("link");
        link.id = "leaflet-css"; link.rel = "stylesheet"; link.href = LEAFLET_CSS;
        link.crossOrigin = "";
        document.head.appendChild(link);
      }
      var existing = document.getElementById("leaflet-js");
      if (existing) {
        existing.addEventListener("load", function () { resolve(window.L); }, { once: true });
        existing.addEventListener("error", reject, { once: true });
        return;
      }
      var script = document.createElement("script");
      script.id = "leaflet-js"; script.src = LEAFLET_JS; script.crossOrigin = "";
      script.onload = function () { resolve(window.L); };
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  function campusContains(latlng) { return L.latLngBounds(CAMPUS_BOUNDS).contains(latlng); }

  // Leaflet's locationerror carries the browser's own words ("Origin does
  // not have permission to use Geolocation service", "User denied
  // Geolocation"), which tell nobody what to do. The code is the same in
  // every browser, so the advice is keyed on that.
  var LOCATE_ADVICE = {
    1: "Location is blocked for this site. Allow it in your browser's site settings, "
       + "and check that Location Services is on for your browser.",
    2: "Your location couldn't be found right now. Try again outdoors or with Wi-Fi on.",
    3: "Finding you took too long. Try again."
  };

  // The view the page opens with: the middle of the campus area at
  // CAMPUS_ZOOM. Given a point (a meetup just posted), the view is moved the
  // least it takes to have that point's flag whole on the map, off the
  // status line along the map's foot. The campus is a little taller than a
  // computer's map at this zoom, and a phone's map shows only its middle, so
  // a pin put near an edge would otherwise be posted out of sight. Not
  // animated: a card is placed against where its flag stands (placeCallout),
  // and the flag has to be standing there.
  function campusView(include) {
    var zoom = CAMPUS_ZOOM;
    var centre = map.project(L.latLngBounds(CAMPUS_BOUNDS).getCenter(), zoom);
    if (include) {
      var size = map.getSize(), at = map.project(include, zoom);
      var x = at.x - centre.x, y = at.y - centre.y;   // the flag's foot, from the map's middle
      // The room the flag needs round its foot. Above: the raised flag is
      // about 64px tall. Beside: its cloth flies to the right of its pole.
      // Under: the status line's one line.
      var top = 76, left = 34, right = 64, foot = 62;
      centre.x += Math.max(0, x - (size.x / 2 - right)) + Math.min(0, x + (size.x / 2 - left));
      centre.y += Math.max(0, y - (size.y / 2 - foot)) + Math.min(0, y + (size.y / 2 - top));
    }
    map.setView(map.unproject(centre, zoom), zoom, { animate: false });
  }

  function initMap() {
    map = L.map("beaconCampusMap", {
      zoomControl: true, minZoom: 15, maxZoom: 19,
      // The map may be panned a whole campus past the dashed area on every
      // side (flags still have to go inside it): a flag's callout stands
      // on the flag, and one near the north edge, zoomed out, needs the
      // map to pan that far to show it.
      maxBounds: L.latLngBounds(CAMPUS_BOUNDS).pad(1), maxBoundsViscosity: 0.85
    });
    campusView();

    var tiles = L.tileLayer(TILE_URL, {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
    }).addTo(map);
    // Say so if OpenStreetMap refuses every tile, rather than leaving a blank map.
    var tileLoaded = false, tileFailed = 0;
    tiles.on("tileload", function () { tileLoaded = true; });
    tiles.on("tileerror", function () {
      tileFailed += 1;
      if (!tileLoaded && tileFailed === 4) {
        announce("The map couldn't load. Try again in a moment.", "error");
      }
    });

    // The campus area flags may go in: a dashed Beacon Blue outline.
    L.rectangle(CAMPUS_BOUNDS, {
      color: "#005a8b", weight: 2, opacity: 0.6, dashArray: "6 6",
      fillColor: "#005a8b", fillOpacity: 0.04, interactive: false
    }).addTo(map);

    placeLayer = L.layerGroup().addTo(map);
    flagLayer = L.layerGroup().addTo(map);
    userLayer = L.layerGroup().addTo(map);

    map.on("click", function (event) {
      // A tap on the map puts a landmark back, and on a phone shuts the
      // flag's card or a pile's list under the map (Leaflet shuts the
      // callout itself).
      // While a meetup's pin is being placed, a tap moves the pin there.
      if (mode === "pick") { movePick(event.latlng); return; }
      pickPlace(null);
      if (phone() && (selected || listing)) { select(null); }
    });
    // Which flags share a pin depends on the zoom: regroup at every one, and
    // let an open list or card follow (a pile split, a flag joined one).
    map.on("zoomend", function () {
      if (dragging) { return; }
      renderMarkers();
      if (mode === "idle") { renderDetail(); }
      // A zoom moves the flag on the map: its card finds its room again.
      // (This is also where a link's flight to a flag ends.)
      placeCallout(findFlag(selected || listing));
    });
    // The map dragged with a card open: the card goes with its flag, so
    // what it covers of the map's keys and the status line is looked at
    // again.
    map.on("moveend", function () {
      if (calling) { seatStatus(); hideCovered(); }
    });
    map.on("locationfound", locationFound);
    map.on("locationerror", function (event) {
      el.locate.disabled = false;
      announce(LOCATE_ADVICE[event.code]
        || "Your location couldn't be found. Check the browser's location setting.", "error");
    });
  }

  // Landmarks. The Campus Center wears the UMass Boston logo, the Healey
  // Library its own sign and the Clark Athletic Center the UMass Boston
  // Recreation mark; every other landmark is a pixel lighthouse on a yellow
  // tile with the building's short name under it.
  //
  // They sit back so the flags stand out: shown at half size and
  // see-through (the stylesheet does both, scaling the full-size pin from
  // its top left corner into the half-size box Leaflet is given here). Tap
  // one and it comes forward at full size with its name over it; tap it
  // again, or the map, to put it back.
  function halfIcon(options) {
    var w = options.iconSize[0], h = options.iconSize[1];
    options.fullHeight = h;
    options.iconSize = [w / 2, h / 2];
    options.iconAnchor = [w / 4, h / 2];
    return L.divIcon(options);
  }
  var PLACE_LOGOS = {
    "healey-library": { url: "healey", look: "healey", size: [48, 48] },
    "clark-athletic-center": { url: "rec", look: "rec", size: [66, 51] }
  };
  var PLACE_TAGS = {
    "wheatley-hall": "WHEAT", "mccormack-hall": "MCORM",
    "university-hall": "UHALL", "integrated-sciences-complex": "ISC"
  };
  function placeIcon(place) {
    var logo = PLACE_LOGOS[place.key];
    if (logo && urls[logo.url]) {
      return halfIcon({
        className: "approved-place-icon",
        html: '<span class="campus-logo-pin ' + logo.look + '" aria-hidden="true"><img src="' + urls[logo.url] +
          '" alt="" /></span>',
        iconSize: logo.size, iconAnchor: [logo.size[0] / 2, logo.size[1]]
      });
    }
    var tag = PLACE_TAGS[place.key];
    if (tag) {
      return halfIcon({
        className: "approved-place-icon",
        html: '<span class="campus-lighthouse-pin tagged" aria-hidden="true">' + spriteMarkup("lighthouse") +
          '<b class="pin-tag">' + tag + "</b></span>",
        iconSize: [44, 50], iconAnchor: [22, 50]
      });
    }
    if (place.key === CAMPUS_CENTER_KEY && urls.umb) {
      return halfIcon({
        className: "approved-place-icon",
        html: '<span class="campus-umb-pin" aria-hidden="true"><img src="' + urls.umb +
          '" alt="" width="46" height="22" /></span>',
        iconSize: [58, 34], iconAnchor: [29, 34]
      });
    }
    return halfIcon({
      className: "approved-place-icon",
      html: '<span class="campus-lighthouse-pin" aria-hidden="true">' + spriteMarkup("lighthouse") + "</span>",
      iconSize: [28, 38], iconAnchor: [14, 38]
    });
  }

  var pickedPlace = null;
  function dressPlace(marker, on) {
    var el = marker.getElement(), o = marker.options.icon.options;
    if (el) { el.classList.toggle("place-on", on); }
    marker.setZIndexOffset(on ? 900 : -100);
    marker.unbindTooltip();
    marker.bindTooltip(marker.options.title, {
      direction: "top", permanent: on, offset: [0, 2 - (on ? o.fullHeight : o.iconSize[1])]
    });
    if (on) { marker.openTooltip(); }
  }
  function pickPlace(marker) {
    var next = marker && marker !== pickedPlace ? marker : null;
    if (pickedPlace) { dressPlace(pickedPlace, false); }
    pickedPlace = next;
    if (next) { dressPlace(next, true); }
  }

  function renderPlaces() {
    placeLayer.clearLayers();
    pickedPlace = null;
    info.places.forEach(function (place) {
      var marker = L.marker([place.latitude, place.longitude], {
        icon: placeIcon(place), title: place.name, keyboard: false, zIndexOffset: -100
      }).addTo(placeLayer);
      var tall = marker.options.icon.options.iconSize[1];
      marker.bindTooltip(place.name, { direction: "top", offset: [0, 2 - tall] });
      // While a meetup's pin is being placed, a landmark is a fine place
      // for it.
      marker.on("click", function () {
        if (mode === "pick") { movePick(marker.getLatLng()); }
        else { pickPlace(marker); }
      });
    });
  }

  // A flag pin: a navy pole with a waving cloth in the kind's colour and
  // its icon on it (drawn by _map_chrome.css). For flags on the same spot,
  // `count` puts how many on the cloth and `behind` (the next flag's kind)
  // a second cloth peeking out behind it.
  function flagIcon(category, options) {
    var look = kindOf(category);
    var classes = "beacon-flag-pin" + (options.on ? " on" : "") +
      (options.draft ? " draft" : "") + (options.count ? " stack" : "");
    var head = spriteMarkup(options.draft && !options.keepIcon ? "plus" : look.icon);
    var behind = options.count
      ? '<span class="flag-head behind" style="--flag-color:' + kindOf(options.behind).color + '"></span>' : "";
    var count = options.count
      ? '<span class="flag-count">' + (options.count > 9 ? "9+" : options.count) + "</span>" : "";
    var html = '<span class="' + classes + '" style="--flag-color:' + look.color + ";--flag-ink:" + look.ink + '">' +
      behind + '<span class="flag-head">' + head + '</span><span class="flag-pole"></span>' + count + "</span>";
    return L.divIcon({
      className: "beacon-flag-icon",
      html: html,
      iconSize: options.count ? [44, 52] : [36, 52],
      iconAnchor: [18, 52]
    });
  }

  // ---------- 4a. flags on the same spot ----------
  // Flags whose pins would cover each other at this zoom are drawn as one
  // flag with a count on its cloth: a pile. They pile up often, not by
  // chance: a meetup posted without a pin goes on its place's landmark. A
  // tap opens a list of them (the flag card's window, section 5), and a
  // row opens that flag's card, with a key back to the list. Grouped afresh
  // at every zoom, so flags a few metres apart share a pin zoomed out and
  // stand apart zoomed in.
  var STACK_ACROSS = 28;  // px between two flags' feet that still overlap,
  var STACK_UPDOWN = 40;  // across and up or down (the pin is 36 by 52)
  var listing = null;     // a flag in the pile whose list is open
  var pileOf = {};        // flag id -> every flag in its pile (piles of two or more)

  // Greedy, in the order the server sends them (soonest to end first, so
  // the soonest leads its group): a flag joins the first group whose lead
  // its foot is near, or starts its own.
  function groupFlags(flags) {
    var groups = [];
    flags.forEach(function (flag) {
      var at = map.latLngToLayerPoint([flag.latitude, flag.longitude]);
      for (var i = 0; i < groups.length; i += 1) {
        if (Math.abs(at.x - groups[i].at.x) < STACK_ACROSS && Math.abs(at.y - groups[i].at.y) < STACK_UPDOWN) {
          groups[i].flags.push(flag);
          return;
        }
      }
      groups.push({ at: at, flags: [flag] });
    });
    return groups;
  }

  // "A", "A and B", "A, B and C": every one, for a stack's title.
  function listAll(names) {
    if (names.length <= 2) { return names.join(" and "); }
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }

  // Where a flag is drawn: on its pile's pin when it is in one, else on its
  // own point. A card or list stands there.
  function drawnAt(flag) {
    var pile = pileOf[flag.id];
    var at = pile ? pile[0] : flag;
    return L.latLng(at.latitude, at.longitude);
  }

  function drawFlag(flag) {
    var mine = flag.mine && mode === "idle";
    var marker = L.marker([flag.latitude, flag.longitude], {
      icon: flagIcon(flag.category, { on: flag.id === selected }),
      title: labelOf(flag.category) + " flag: " + flag.title + (mine ? " (drag to move)" : ""),
      keyboard: true, draggable: mine,
      // flags stay out of the way while a new one is being placed
      interactive: mode === "idle",
      zIndexOffset: flag.id === selected ? 1000 : 500
    }).addTo(flagLayer);
    marker.on("click", function () { announce(""); select(flag.id); });
    if (mine) {
      marker.on("dragstart", function () { dragging = true; select(flag.id, false, true); });
      marker.on("dragend", function () {
        dragging = false;
        moveFlag(flag, marker.getLatLng(), marker);
      });
    }
  }

  function drawStack(group) {
    var lead = group.flags[0];
    // Raised, as a chosen flag is, while its list or one of its cards is open.
    var on = group.flags.some(function (f) { return f.id === selected || f.id === listing; });
    var marker = L.marker([lead.latitude, lead.longitude], {
      icon: flagIcon(lead.category, { count: group.flags.length, behind: group.flags[1].category, on: on }),
      title: group.flags.length + " flags here: " +
        listAll(group.flags.map(function (f) { return f.title; })) + ". Tap to see them.",
      keyboard: true, interactive: mode === "idle", zIndexOffset: on ? 1000 : 600
    }).addTo(flagLayer);
    marker.on("click", function () { announce(""); openPile(group.flags); });
  }

  // A pile's list: any card shuts and the list opens in its place.
  function openPile(flags) {
    selected = null;
    listing = flags[0].id;
    confirmDown = null;
    renderMarkers();
    renderDetail();
  }

  function renderMarkers() {
    if (!map) { return; }
    flagLayer.clearLayers();
    pileOf = {};
    groupFlags(info.flags).forEach(function (group) {
      if (group.flags.length === 1) { drawFlag(group.flags[0]); return; }
      group.flags.forEach(function (f) { pileOf[f.id] = group.flags; });
      drawStack(group);
    });
    // The open callout stands on its flag (or its pile) where that is drawn
    // now.
    var open = findFlag(selected || listing);
    // Only if that is somewhere else (a flag moved, a pile split): a card
    // that has not changed places is not put down again at every refresh.
    if (callout && callout.isOpen() && open && !callout.getLatLng().equals(drawnAt(open))) {
      callout.setLatLng(drawnAt(open));
      placeCallout(open);
    }
    if (pick) {
      // The flag the meetup will get, so the pin being placed already looks
      // like it: the meetup's navy one with a star, or, for an activity
      // written in, the colour of the type the form has chosen.
      var spot = L.marker([pick.lat, pick.lng], {
        icon: flagIcon(pick.kind, { draft: true, keepIcon: true }),
        draggable: true, title: "Your meetup. Drag to move it.", zIndexOffset: 2000
      }).addTo(flagLayer);
      spot.on("dragend", function () { movePick(spot.getLatLng()); });
    }
  }

  // The map's key (each kind of flag in its colour, on the map's bottom
  // edge) is written by the page, not drawn here: see _community_map.html.

  // ---------- 5. the flag card ----------
  // The flag you tapped opens in a callout on the map: a Beacon OS window
  // by the flag, wherever there is room for it, with a tail pointing at the
  // flag (a Leaflet popup, placed by placeCallout and dressed by
  // _community_map.css). On a phone, where a callout would not
  // fit over 300px of tiles, the same card opens as its own window under
  // the map instead (5b). One card is built either way; only its home
  // differs.
  var PHONE = window.matchMedia("(max-width: 560px)");
  var CALLOUT_EDGE = 12;  // px the callout keeps from the map's top and sides
  var CALLOUT_FOOT = 26;  // and from its foot: clear of the map's credit line
  var CALLOUT_GAP = 12;   // px between the flag and the callout, where its tail is
  var callout = null;     // the Leaflet popup, made the first time it is needed
  var calling = false;    // it is open on the map (the Post a meetup key steps aside)
  var shutting = false;   // the callout is being closed from here, not by a tap
  var below = null;       // the phone's window under the map, made when first needed
  var belowHolds = "";    // what it holds ("pile:<id>" or "card:<id>")
  var landing = null;     // the map is being brought to a flag: where the flag will stand on it

  function phone() { return PHONE.matches; }
  function still() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  // fly: the map is brought to the flag first (a link named it; it may be
  // anywhere). A tap on a flag does not fly: the map stays where it is and
  // the card opens where there is room (placeCallout).
  function select(id, fly, quiet) {
    selected = id;
    // A card takes the place of a pile's list, and shutting it (id null)
    // shuts both. Its back key (buildCard) reopens the list.
    listing = null;
    confirmDown = null;
    // The markers first: the card opens on its flag (or its pile) where that
    // is drawn.
    if (!quiet) { renderMarkers(); }
    var flag = findFlag(id);
    // Your own flag can be dragged when it stands alone: said in the status
    // line, on a wide screen. Before the card opens, which keeps off that line.
    if (flag && flag.mine && !phone() && !pileOf[flag.id]) { announce("Drag your flag on the map to move it."); }
    // The card is placed against where the flag will land, not where it is.
    landing = flag && fly && map ? flyToFlag(flag) : null;
    renderDetail();
    landing = null;
  }

  // Where a flag named by a link is brought to on the map, and the view
  // that has it there. On a wide screen a little left of the middle, so its
  // card has the room beside it; on a phone at the map's foot, just above
  // the card under the map.
  function viewFor(flag, zoom) {
    var size = map.getSize();
    var want = phone() ? L.point(size.x / 2, Math.max(70, size.y - 40))
      : L.point(Math.round(size.x / 2 - Math.min(170, size.x / 4)), Math.round(size.y / 2 + 32));
    var centre = map.unproject(map.project(drawnAt(flag), zoom).add(size.divideBy(2)).subtract(want), zoom);
    return { centre: centre, want: want };
  }
  function flyToFlag(flag) {
    var view = viewFor(flag, FLAG_ZOOM);
    map.flyTo(view.centre, FLAG_ZOOM, { duration: 0.45 });
    return view.want;
  }

  function tag(text, extra) { return node("span", "tag" + (extra ? " " + extra : ""), text); }

  // A key on the card. Its click goes no further than the key: a key that
  // redraws the card (a list row, the back key, the "Tap again" of Take
  // down) leaves the tap with a target no longer inside the callout, and
  // Leaflet would take it for a tap on the map and shut the callout.
  function onTap(el, fn) {
    el.addEventListener("click", function (event) { event.stopPropagation(); fn(event); });
  }
  function button(text, className, onClick) {
    var b = node("button", className, text);
    b.type = "button";
    onTap(b, onClick);
    return b;
  }

  // Somebody's avatar: their picture when they have set one, otherwise their
  // initials on the colour their id gives them, the same as _ui.html's face()
  // draws everywhere else. Small, except in the friend picker (small false).
  function avatar(name, id, photo, small) {
    var size = small === false ? "" : " sm";
    var a;
    if (photo) {
      a = document.createElement("img");
      a.className = "avatar" + size;
      a.src = photo;
      a.alt = "";
      a.loading = "lazy";
    } else {
      var parts = String(name || "").split(" ").filter(Boolean);
      var initials = (parts.length ? parts[0][0] : "") + (parts.length > 1 ? parts[parts.length - 1][0] : "");
      a = node("span", "avatar" + size + " av-" + (Math.abs(parseInt(id, 10) || 0) % 6), initials.toUpperCase());
      a.setAttribute("aria-hidden", "true");
    }
    a.title = name || "";
    return a;
  }

  // "Ira", "Ira and Mo", "Ira, Mo and Sam", "Ira, Mo and 3 more".
  function peopleText(names) {
    if (names.length <= 1) { return names.join(""); }
    if (names.length <= 3) { return names.slice(0, -1).join(", ") + " and " + names[names.length - 1]; }
    return names.slice(0, 2).join(", ") + " and " + (names.length - 2) + " more";
  }

  // "Today", "Tomorrow" or "In 3 days", by the campus's own calendar; and
  // "Happening now" for the first hours after it starts.
  function dayKey(date) {
    return date.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  }
  function soonText(ev) {
    if (!ev || !ev.starts_at) { return ""; }
    var at = new Date(ev.starts_at);
    if (isNaN(at.getTime())) { return ""; }
    var gone = Date.now() - at.getTime();
    if (gone >= 0) { return gone < 3 * 3600000 ? "Happening now" : ""; }
    var days = Math.round((Date.parse(dayKey(at)) - Date.parse(dayKey(new Date()))) / 86400000);
    if (days === 0) { return "Today"; }
    if (days === 1) { return "Tomorrow"; }
    if (days > 1 && days < 14) { return "In " + days + " days"; }
    return "";
  }

  // One line of the card: an icon, then whatever is put in the text span.
  function line(list, icon) {
    var li = node("li", "fd-line");
    li.appendChild(spriteNode(icon));
    var text = node("span", "fd-text");
    li.appendChild(text);
    list.appendChild(li);
    return text;
  }

  // The window's title bar: a back key when there is somewhere to go back
  // to, the icon and what it is, and the close key.
  function windowBar(icon, label, back) {
    var bar = node("div", "fd-bar");
    if (back) {
      var key = node("button", "fd-back");
      key.type = "button";
      key.setAttribute("aria-label", back.label);
      key.title = back.label;
      key.appendChild(spriteNode("arrow"));
      onTap(key, back.go);
      bar.appendChild(key);
    }
    bar.appendChild(spriteNode(icon));
    bar.appendChild(node("span", "fd-kind", label));
    var close = node("button", "fd-close");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.appendChild(spriteNode("close"));
    onTap(close, function () { select(null); });
    bar.appendChild(close);
    return bar;
  }

  // The card itself: a title bar in the flag's colour, who put it there and
  // what it is, a line each for when, where and who, the note, the keys.
  // A flag in a pile gets a key back to the pile's list.
  function buildCard(flag, pile) {
    var look = kindOf(flag.category);
    var ev = flag.event;
    var card = node("div", "flag-detail" + (ev ? " is-event" : ""));
    card.style.setProperty("--flag-color", look.color);
    card.style.setProperty("--flag-ink", look.ink);
    // "Study meetup", "Game meetup": the server's words for what this is.
    card.appendChild(windowBar(look.icon, flag.bar_label || (ev ? "Meetup" : labelOf(flag.category)), pile ? {
      label: "All " + pile.length + " flags here",
      go: function () { openPile(pile); }
    } : null));

    var body = node("div", "fd-body");
    var head = node("div", "fd-head");
    head.appendChild(avatar(flag.owner_name, flag.owner_id, flag.owner_photo));
    var text = node("div", "grow");
    text.appendChild(node("b", "fd-title", flag.title));
    text.appendChild(node("span", "fd-sub", whoLine(flag)));
    head.appendChild(text);
    body.appendChild(head);

    var lines = node("ul", "fd-lines");
    if (ev) {
      var when = line(lines, "calendar");
      var soon = soonText(ev);
      if (ev.when) { when.appendChild(node("b", "", ev.when)); }
      if (soon) { when.appendChild(tag(soon, "soon")); }
      if (!ev.when && !soon) { when.appendChild(node("span", "", "Time to be arranged")); }
    } else {
      line(lines, "calendar").appendChild(node("span", "", expiresText(flag.expires_at)));
    }
    line(lines, "meet").appendChild(node("span", "", flag.location_name));
    if (ev) {
      var who = line(lines, "people");
      var people = ev.people || [];
      if (people.length) {
        var faces = node("span", "fd-faces");
        people.slice(0, 5).forEach(function (p) { faces.appendChild(avatar(p.name, p.id, p.photo)); });
        who.appendChild(faces);
      }
      var names = people.map(function (p) { return firstName(p.name); });
      who.appendChild(node("span", "", (names.length ? peopleText(names) + " · " : "") +
        ev.going + " of " + ev.max + " going"));
      // Your own meetup does not need the tag: "Your meetup" says you're in.
      if (ev.im_going && !flag.mine) { who.appendChild(tag("You're in", "in")); }
      // Who can see it, when that is not everybody.
      var only = audienceLine(flag);
      if (only) { line(lines, "lock").appendChild(node("span", "", only)); }
    }
    body.appendChild(lines);

    if (flag.description) { body.appendChild(node("p", "fd-note", flag.description)); }

    var actions = node("div", "fd-actions");
    if (ev && !flag.mine && !ev.im_going && ev.open) {
      // A real form, so "I'm in" works the same as on the meetup's card.
      var form = node("form");
      form.method = "post";
      form.action = urls.rsvp.replace("/0/", "/" + ev.post_id + "/");
      var back = node("input"); back.type = "hidden"; back.name = "back"; back.value = "find";
      form.appendChild(back);
      var join = node("button", "fd-join", ev.full ? "It's full" : "I'm in");
      join.type = "submit"; join.disabled = ev.full;
      form.appendChild(join);
      actions.appendChild(form);
    }
    if (ev && ev.chat_id) {
      var chat = node("a", "key", "Open meetup chat");
      chat.href = urls.chat.replace(/\/0$/, "/" + ev.chat_id);
      actions.appendChild(chat);
    }
    // Anyone who can see a flag can share it: pasted into a chat, the link
    // shows as the flag's card, but only to people allowed to see the flag.
    actions.appendChild(button("Copy link", "map-button", function () { copyLink(flag); }));
    if (flag.mine) {
      // Your own pin's keys, smaller, on a row of their own.
      var own = node("div", "fd-own");
      if (ev && ev.lockable) {
        // Locked, only the people who are in see the pin. Unlocking gives it
        // back to whoever the meetup is for, which the server decides.
        var locked = flag.visibility === "going";
        own.appendChild(button(locked ? "Unlock meetup" : "Lock meetup",
          "map-button small", function () { saveAudience(flag, !locked); }));
      }
      if (ev) {
        // A meetup and its pin are one thing, so the pin comes down by
        // cancelling the meetup. A real form, as "I'm in" is, sent by the
        // page; the first tap only asks.
        var off = node("form");
        off.method = "post";
        off.action = urls.close.replace("/0/", "/" + ev.post_id + "/");
        var offBack = node("input"); offBack.type = "hidden"; offBack.name = "back"; offBack.value = "find";
        off.appendChild(offBack);
        var cancel = node("button", "map-button small warn fd-cancel",
          confirmDown === flag.id ? "Tap again to cancel" : "Cancel meetup");
        cancel.type = "submit";
        cancel.addEventListener("click", function (event) {
          // The second tap is left alone: it sends the form.
          if (confirmDown === flag.id) { return; }
          // The first only asks, and redraws the card to do it, so the tap
          // goes no further (see onTap): its target is about to leave the
          // callout, and Leaflet would take it for a tap on the map.
          event.preventDefault();
          event.stopPropagation();
          confirmDown = flag.id;
          renderDetail();
        });
        off.appendChild(cancel);
        own.appendChild(off);
      } else {
        own.appendChild(button(confirmDown === flag.id ? "Tap again to take down" : "Take down",
          "map-button small warn", function () {
            if (confirmDown !== flag.id) { confirmDown = flag.id; renderDetail(); return; }
            takeDown(flag);
          }));
      }
      actions.appendChild(own);
    }
    if (!flag.mine) {
      // Reporting somebody else's pin. An <a> rather than a button: it
      // opens a page to write on, and the server re-checks that this pin is
      // one you were allowed to see before it quotes it back to anybody.
      // flag.id is the public_id -- row ids stay internal, and /report
      // takes the same public one the delete endpoint does.
      var tell = node("a", "map-button quiet", "Report");
      tell.href = "/report?kind=abuse&flag=" + encodeURIComponent(flag.id);
      actions.appendChild(tell);
    }
    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  // A pile's list: a row for each flag in it, soonest to end first (the
  // server's order), each opening that flag's card.
  function rowLine(flag) {
    var ev = flag.event;
    if (ev) {
      return (ev.when || "Time to be arranged") + " · " + ev.going + " of " + ev.max + " going";
    }
    return labelOf(flag.category) + " · " + expiresText(flag.expires_at);
  }
  function buildList(pile) {
    var list = node("div", "flag-detail flag-pile");
    list.style.setProperty("--flag-color", KINDS.event.color);
    list.style.setProperty("--flag-ink", KINDS.event.ink);
    list.appendChild(windowBar("meet", pile.length + " flags here", null));
    var rows = node("ul", "fd-rows");
    pile.forEach(function (flag) {
      var li = node("li");
      var row = node("button", "fd-row");
      row.type = "button";
      var swatch = node("span", "map-key-swatch");
      swatch.style.setProperty("--flag-color", kindOf(flag.category).color);
      row.appendChild(swatch);
      // The title with its tags beside it, then the line under both at the
      // row's full width, so a tag never crowds out when or who.
      var text = node("span", "grow");
      var head = node("span", "fd-row-head");
      head.appendChild(node("b", "fd-row-title", flag.title));
      var soon = soonText(flag.event);
      if (soon) { head.appendChild(tag(soon, "soon")); }
      if (flag.mine) { head.appendChild(tag("Yours")); }
      else if (flag.event && flag.event.im_going) { head.appendChild(tag("You're in", "in")); }
      text.appendChild(head);
      text.appendChild(node("span", "fd-row-line", rowLine(flag)));
      row.appendChild(text);
      onTap(row, function () { announce(""); select(flag.id); });
      li.appendChild(row);
      rows.appendChild(li);
    });
    list.appendChild(rows);
    return list;
  }

  function renderDetail() {
    // Rebuild only when something on the card changed, so a refresh never
    // pulls a button out from under somebody about to press it.
    var current = selected && mode === "idle" ? findFlag(selected) : null;
    var pile = current ? pileOf[current.id] || null
      : (listing && mode === "idle" ? pileOf[listing] || null : null);
    // The list's pile split (zoomed in) or emptied: it shuts.
    if (!current && listing && !pile) { listing = null; }
    var key = mode + "|" + confirmDown + "|" + (phone() ? "below" : "callout") + "|" +
      JSON.stringify(current) +
      (current ? expiresText(current.expires_at) + soonText(current.event) : "") + "|" +
      (pile ? pile.map(function (f) {
        return current ? f.id : f.id + rowLine(f) + soonText(f.event) + f.title + (f.event && f.event.im_going);
      }).join(",") : "");
    if (key === shownCard) { return; }
    shownCard = key;
    var shown = current || (pile ? pile[0] : null);
    if (!shown || !map) { closeCallout(); closeBelow(); return; }
    var card = current ? buildCard(current, pile) : buildList(pile);
    if (phone()) { closeCallout(); showBelow(card, shown); }
    else { closeBelow(); showCallout(card, shown); }
  }

  // ---------- 5a. the callout ----------
  // A tap on a flag never moves the map. Leaflet's popup hangs a fixed way
  // above its point and moves the map until it fits, and a card is most of
  // a map tall, so nearly every flag slid the map down to its foot. Here the
  // card is put, whole, in the first place it fits: above its flag, below
  // it, to its right, to its left, inside the map and clear of what floats
  // on it. It is still Leaflet's popup, so it rides with its flag when the
  // map is dragged; only its offset from the flag is worked out each time.

  // What floats on the map, as boxes in px on it: the map's keys (zoom,
  // locate, fit), and the status line where it is saying something, in its
  // own corner. (The credit line is kept clear of by CALLOUT_FOOT.)
  function calloutBlocks() {
    var box = map.getContainer(), m = box.getBoundingClientRect();
    function boxOf(key) {
      var r = key ? key.getBoundingClientRect() : null;
      return !r || !r.width || !r.height ? null
        : { left: r.left - m.left - 6, right: r.right - m.left + 6, top: r.top - m.top - 6, bottom: r.bottom - m.top + 6 };
    }
    el.status.classList.remove("aside");
    el.status.style.maxWidth = "";
    return {
      keys: [box.querySelector(".leaflet-control-zoom"), el.locate, el.fit].map(boxOf).filter(Boolean),
      status: boxOf(el.status)
    };
  }
  // The card open on the map, as a box on the screen; null when none is.
  function cardBox() {
    return calling && callout && callout.isOpen() ? callout.getElement().getBoundingClientRect() : null;
  }
  function touching(a, b) {
    return a.width > 0 && a.height > 0 &&
           a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  }
  // The status line sits bottom left, as wide as its words. Where an open
  // card has had to take that corner, the line takes the room there is
  // beside the card along the map's foot instead: on the card's left or
  // (the stylesheet's .aside: the corner the Post a meetup key leaves free
  // while a card is open) on its right, whichever is wider, wrapping if it
  // must. So a message never lies across the card. Back as it was when the
  // card shuts or stands clear of it.
  function seatStatus() {
    var line = el.status;
    line.classList.remove("aside");
    line.style.maxWidth = "";
    var card = cardBox();
    if (!card || !line.textContent || !touching(line.getBoundingClientRect(), card)) { return; }
    var stage = el.stage.getBoundingClientRect();
    var seats = [{ aside: false, room: card.left - stage.left - 22 },
                 { aside: true, room: stage.right - card.right - 22 }]
      .sort(function (a, b) { return b.room - a.room; });
    for (var i = 0; i < seats.length; i += 1) {
      if (seats[i].room < 150) { break; }
      line.classList.toggle("aside", seats[i].aside);
      line.style.maxWidth = Math.floor(seats[i].room) + "px";
      if (!touching(line.getBoundingClientRect(), card)) { return; }
    }
    line.classList.remove("aside");
    line.style.maxWidth = "";
  }
  // A key of the map's that the open card has had to cover (a short map: a
  // card is nearly its height, and cannot clear them all) steps aside
  // until the card shuts or moves off it, as the Post a meetup key does:
  // a key is never drawn across the card, over the card's own keys.
  function hideCovered() {
    var card = cardBox();
    [map.getContainer().querySelector(".leaflet-control-zoom"), el.locate, el.fit].forEach(function (key) {
      if (!key) { return; }
      key.classList.remove("covered");
      if (card && touching(key.getBoundingClientRect(), card)) { key.classList.add("covered"); }
    });
  }
  // `at`: where the flag's foot is on the map, or will be (select's landing).
  function placeCallout(flag, at) {
    if (!callout || !callout.isOpen() || !flag) { return; }
    var box = callout.getElement();
    var w = box.offsetWidth, h = box.offsetHeight, size = map.getSize();
    var p = at || map.latLngToContainerPoint(drawnAt(flag));
    // The flag itself, raised as a chosen one is: its pole at p, its cloth
    // (and a pile's count) to the right.
    var pin = { left: p.x - 24, right: p.x + (pileOf[flag.id] ? 62 : 46), top: p.y - 66, bottom: p.y + 2 };
    var EDGE = CALLOUT_EDGE, FOOT = CALLOUT_FOOT, GAP = CALLOUT_GAP;
    function within(v, low, high) { return Math.max(low, Math.min(v, Math.max(low, high))); }
    var across = within(p.x - w / 2, EDGE, size.x - EDGE - w);
    var down = within((pin.top + pin.bottom) / 2 - h / 2, EDGE, size.y - FOOT - h);
    var places = [
      { side: "above", left: across, top: pin.top - GAP - h },
      { side: "below", left: across, top: pin.bottom + GAP },
      { side: "right", left: pin.right + GAP, top: down },
      { side: "left", left: pin.left - GAP - w, top: down }
    ];
    var blocks = calloutBlocks();
    function covers(c, b) {
      var across = Math.min(c.left + w, b.right) - Math.max(c.left, b.left);
      var down = Math.min(c.top + h, b.bottom) - Math.max(c.top, b.top);
      return across > 0 && down > 0 ? across * down : 0;
    }
    places.forEach(function (c) {
      // How far it sticks out of the map, how much of the map's keys it
      // lands on (px squared), and whether it lands on the status line.
      c.out = Math.max(0, EDGE - c.left) + Math.max(0, c.left + w - (size.x - EDGE)) +
              Math.max(0, EDGE - c.top) + Math.max(0, c.top + h - (size.y - FOOT));
      c.over = blocks.keys.reduce(function (sum, b) { return sum + covers(c, b); }, 0);
      c.said = blocks.status ? covers(c, blocks.status) : 0;
    });
    // The first that is whole on the map and on nothing. Failing that, the
    // first that is on no key (the status line then moves beside the card:
    // seatStatus). Failing that (a short map: a card is nearly its height),
    // the whole one on the least of the keys, which step aside for it
    // (hideCovered). And if none is whole, the one that sticks out least.
    var whole = places.filter(function (c) { return !c.out; });
    var best = whole.filter(function (c) { return !c.over && !c.said; })[0] ||
               whole.filter(function (c) { return !c.over; })[0] ||
               whole.slice().sort(function (a, b) { return a.over - b.over; })[0] ||
               places.slice().sort(function (a, b) { return a.out - b.out; })[0];
    // No place takes it whole: the best of them, kept inside the map.
    var left = within(best.left, EDGE, size.x - EDGE - w);
    var top = within(best.top, EDGE, size.y - FOOT - h);
    // Leaflet stands the popup's bottom centre at its point plus this.
    callout.options.offset = L.point(Math.round(left + w / 2 - p.x), Math.round(top + h - p.y));
    callout.setLatLng(callout.getLatLng());
    // The tail, on the side that faces the flag, level with it.
    ["above", "below", "right", "left"].forEach(function (side) {
      box.classList.toggle("at-" + side, side === best.side);
    });
    var upright = best.side === "above" || best.side === "below";
    var tail = upright ? within(p.x - left, 24, w - 24)
                       : within((pin.top + pin.bottom) / 2 - top, 24, h - 24);
    box.style.setProperty("--tail", Math.round(tail) + "px");
    // Where the tail meets the card's title bar (the card under its flag,
    // or beside it with the flag level with the bar) it takes the bar's
    // colour, so the bar does not get a white notch; elsewhere the card's
    // white.
    var card = box.querySelector(".flag-detail");
    var onBar = best.side === "below" || (!upright && tail < 46);
    box.style.setProperty("--tail-fill",
      onBar && card ? (getComputedStyle(card).getPropertyValue("--flag-color").trim() || "#fff") : "#fff");
    seatStatus();
    hideCovered();
  }

  function showCallout(card, flag) {
    if (!callout) {
      callout = L.popup({
        className: "beacon-callout", closeButton: false,
        minWidth: 280, maxWidth: 320,
        // Never moved to fit: the card is put where there is room.
        autoPan: false
      });
      // Leaflet shuts the callout on a tap on the map or Escape: the flag
      // (or the pile) is then no longer the chosen one, and its pin stands
      // down.
      map.on("popupclose", function (event) {
        if (event.popup === callout) { calling = false; showKey(); seatStatus(); hideCovered(); }
        if (event.popup !== callout || shutting || !(selected || listing)) { return; }
        selected = null;
        listing = null;
        shownCard = "";
        renderMarkers();
      });
    }
    // A long card scrolls inside the callout rather than growing past the
    // map's height (its frame is 4px of that).
    callout.options.maxHeight = Math.max(160, map.getSize().y - CALLOUT_EDGE - CALLOUT_FOOT - 4);
    callout.setLatLng(drawnAt(flag));
    callout.setContent(card);
    // The Post a meetup key steps off the map while a card is open on it
    // (showKey): the card has the whole map to open in.
    calling = true;
    showKey();
    if (!callout.isOpen()) { callout.openOn(map); }
    placeCallout(flag, landing);
  }
  function closeCallout() {
    if (!callout || !callout.isOpen()) { return; }
    shutting = true;
    map.closePopup(callout);
    shutting = false;
  }

  // ---------- 5b. under the map (phones) ----------
  // The card as its own window right under the map, where the Post a
  // meetup key stands; it steps aside while the card is open and comes back
  // when it shuts. The map keeps its size, and the page scrolls so the flag
  // and its card share the screen. The map itself moves only when it has
  // to: if the flag would be under the top bar once the page has scrolled
  // to the card, it is moved down just far enough to stay in view (it used
  // to be slid to the map's foot at every tap).
  function showBelow(card, flag) {
    if (!below) {
      below = node("div", "map-below");
      below.hidden = true;
      var pane = root.querySelector(".map-pane-act");
      pane.insertBefore(below, pane.firstChild);
    }
    var was = !below.hidden;
    // What the window now holds: a pile's list, or one flag's card. Moving
    // from one to the other (a row, the back key) scrolls again, since the
    // two are not the same height; a refresh of the same one does not.
    var holds = (card.classList.contains("flag-pile") ? "pile:" : "card:") + flag.id;
    var moved = was && holds !== belowHolds;
    belowHolds = holds;
    below.replaceChildren(card);
    below.hidden = false;
    el.keys.hidden = true;
    // The map's key steps aside with them (the stylesheet's .carded): the
    // card is fitted to the screen with its flag, and has the room.
    root.classList.add("carded");
    if (moved) {
      var foot = map.latLngToContainerPoint(drawnAt(flag)).y;
      window.requestAnimationFrame(function () { showTogether(foot); });
    }
    if (was) { return; }
    if (!still()) {
      below.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }],
                    { duration: 180, easing: "ease-out" });
    }
    // A link brought the map to the flag: it is landing at the map's foot,
    // just above its card (viewFor), and nothing more is moved.
    if (landing) {
      var lands = landing.y;
      window.requestAnimationFrame(function () { showTogether(lands); });
      return;
    }
    // A tap: the map stays, unless the flag would be hidden. Worked out
    // once the card is laid out, so its height is known.
    var foot = map.latLngToContainerPoint(drawnAt(flag)).y;
    window.requestAnimationFrame(function () {
      var view = screenFor(foot);
      // Under the top bar by this much once the page has scrolled to show
      // the whole card; and no lower than the map's own foot.
      var hidden = Math.max(view.down, 0) - view.most;
      var shift = Math.max(0, Math.min(hidden, map.getSize().y - 12 - foot));
      if (shift > 2) { map.panBy([0, -shift], { animate: !still() }); } else { shift = 0; }
      showTogether(foot + shift);
    });
  }
  // With the flag's foot `footY` px down the map: how far the page has to
  // scroll to show the whole card above the tab bar (down), and how far it
  // can before the flag goes under the top bar (most).
  function screenFor(footY) {
    var bar = document.querySelector("nav.tabbar");
    var floor = bar && getComputedStyle(bar).position === "fixed"
      ? bar.getBoundingClientRect().top : window.innerHeight;
    var head = document.querySelector(".topbar");
    var ceiling = head && /sticky|fixed/.test(getComputedStyle(head).position)
      ? head.getBoundingClientRect().bottom : 0;
    var pinTop = map.getContainer().getBoundingClientRect().top + footY - 64;
    return { down: below.getBoundingClientRect().bottom + 8 - floor, most: pinTop - (ceiling + 8) };
  }
  // Scroll so the whole card is above the tab bar, but never so far that
  // the flag goes under the top bar.
  function showTogether(footY) {
    var view = screenFor(footY);
    var by = Math.min(Math.max(view.down, 0), view.most);
    if (Math.abs(by) > 2) { window.scrollBy({ top: by, behavior: still() ? "auto" : "smooth" }); }
  }
  function closeBelow() {
    if (!below || below.hidden) { return; }
    below.hidden = true;
    below.replaceChildren();
    belowHolds = "";
    el.keys.hidden = mode === "pick";
    root.classList.remove("carded");
  }
  // Escape puts the card or the list away. Leaflet shuts its popup on
  // Escape only while the map itself has focus, which a tap inside the
  // callout never gives it, so it is done here for both homes.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || mode !== "idle" || !(selected || listing)) { return; }
    if ((below && !below.hidden) || (callout && callout.isOpen())) { select(null); }
  }, true);
  // Crossing the phone width moves the card from one home to the other.
  // (addListener: Safari before 14 has no addEventListener here.)
  (PHONE.addEventListener ? PHONE.addEventListener.bind(PHONE, "change") : PHONE.addListener.bind(PHONE))(
    function () {
      shownCard = "";
      if (mode === "idle") { renderDetail(); }
    });
  // "I'm in" pressed on the card: the page's enhancer sends the form and
  // stays here, and the card is redrawn from fresh flags.
  root.addEventListener("beacon:done", function (event) {
    var from = event.target && event.target.closest ? event.target.closest(".flag-detail") : null;
    if (from) { loadFlags(true); }
  });

  // The flag's own address, copied for pasting into a chat.
  function copyLink(flag) {
    var link = location.origin + "/find?flag=" + encodeURIComponent(flag.id);
    var done = function () { announce("Link copied. Paste it into any chat to share this flag.", "success"); };
    var fallback = function () {
      // Older browsers, and pages not served over https: a hidden field.
      var field = node("textarea");
      field.value = link;
      field.setAttribute("readonly", "");
      field.style.position = "fixed"; field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      field.remove();
      if (ok) { done(); } else { announce("Copy this link to share the flag: " + link); }
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(link).then(done, fallback);
    } else {
      fallback();
    }
  }

  // ---------- 6. a meetup's pin, for the form on /find ----------
  // Not a flag of its own: the meetup form owns the point, and the server
  // makes the meetup's flag when it is posted. This only lets the person
  // put the pin somewhere and tells the form where, in words as well. The
  // form itself (#mapMeetForm) is shown under the map for as long as the
  // pin is out (setMode), and the page's own script fills it in.

  // The "Post a meetup" key has two homes. Where the map has its full
  // height (from 701px: the stylesheet makes it 45% of the screen up to
  // 700) the key stands on the map's bottom right corner, the way map apps
  // place the one thing to do, and nothing is left under the map, so the
  // window ends at the flags strip (the stylesheet's .key-on-map). Under
  // that the map is too small to share, and the key is in its row under
  // the map. Only once the map is up: until then, and if it never comes
  // up, the key stays where the page was written with it.
  var ROOMY = window.matchMedia("(min-width: 701px)");
  function homeKey() {
    if (!el.meet || !el.stage) { return; }
    var onMap = ready && ROOMY.matches;
    var home = onMap ? el.stage : el.keys;
    if (el.meet.parentNode !== home) { home.appendChild(el.meet); }
    root.classList.toggle("key-on-map", onMap);
  }
  (ROOMY.addEventListener ? ROOMY.addEventListener.bind(ROOMY, "change") : ROOMY.addListener.bind(ROOMY))(homeKey);
  // When the key shows: once the map is up, and not while the form is open
  // or a flag's card is. On the map a card's callout is fitted over its
  // flag wherever the flag is, which can leave the flag itself in the
  // key's corner, behind it, with the callout's tail pointing at the key.
  // So the key steps aside for a card there, as its row does on a phone,
  // and comes back when the card is shut.
  function showKey() {
    if (el.meet) { el.meet.hidden = !ready || mode !== "idle" || calling; }
  }

  function setMode(next) {
    mode = next;
    if (mode !== "idle") { listing = null; }   // posting shuts a pile's list
    root.classList.toggle("placing", mode !== "idle");
    // The meetup form opens under the map while its pin is being placed,
    // and the Post a meetup key steps aside for it wherever it stands (as
    // its row does for a flag's card there, on a phone).
    if (el.meetForm) { el.meetForm.hidden = mode !== "pick"; }
    el.keys.hidden = mode === "pick" || !!(below && !below.hidden);
    showKey();
    renderDetail();
    renderMarkers();
  }

  function startPick(options) {
    selected = null;
    pick = { lat: options.lat, lng: options.lng, kind: options.kind || "event",
             onMove: options.onMove, onDone: options.onDone };
    setMode("pick");
    // Zoomed in to place the pin, with the pin in the middle of the map, or
    // in the middle of the stretch of it the page says will still be on
    // the screen (options.within: px down the map, worked out now that the
    // form is open under it). One move: a second one asked for while this
    // zoom plays would be dropped by Leaflet.
    var zoom = Math.max(map.getZoom(), PLACE_ZOOM);
    var centre = L.latLng(pick.lat, pick.lng);
    var stretch = options.within ? options.within() : null;
    if (stretch && stretch[1] > stretch[0]) {
      var want = (stretch[0] + stretch[1]) / 2;
      centre = map.unproject(map.project(centre, zoom).add([0, map.getSize().y / 2 - want]), zoom);
    }
    map.setView(centre, zoom);
    announce("Tap the map or drag the pin to where you'll meet.");
  }

  function movePick(latlng) {
    if (!pick) { return; }
    if (!campusContains(latlng)) {
      announce("Meetups have to be inside the dashed campus area.", "error");
      renderMarkers();   // puts a dragged pin back where it was
      return;
    }
    pick.lat = latlng.lat; pick.lng = latlng.lng;
    announce("");
    renderMarkers();
    if (pick.onMove) {
      pick.onMove({ lat: pick.lat, lng: pick.lng, near: nearestPlaceName(latlng) });
    }
  }

  function endPick() {
    if (!pick) { return; }
    var done = pick.onDone;
    pick = null;
    setMode("idle");
    announce("");
    if (done) { done(); }
  }

  // ---------- 7. saving ----------
  function flagUrl(id) { return urls.flags + "/" + encodeURIComponent(id); }

  // Dragging one of your pins saves its new spot straight away. For a
  // meetup's pin the server rewrites where it is, in words, on the pin and
  // on the meetup's card.
  function moveFlag(flag, latlng, marker) {
    if (!campusContains(latlng)) {
      marker.setLatLng([flag.latitude, flag.longitude]);
      announce("Pins have to stay inside the dashed campus area.", "error");
      return;
    }
    flag.latitude = latlng.lat; flag.longitude = latlng.lng;   // stay put while saving
    sendJSON(flagUrl(flag.id), "PATCH", { latitude: latlng.lat, longitude: latlng.lng })
      .then(function () {
        announce("Pin moved.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); loadFlags(true); });
  }

  // Lock a meetup's pin to the people who are in, or open it again. "open"
  // is all this says: the server gives the pin back to whoever the meetup
  // is for, and never to anybody more.
  function saveAudience(flag, lock) {
    sendJSON(flagUrl(flag.id), "PATCH", { visibility: lock ? "going" : "open" })
      .then(function () {
        announce(lock ? "Meetup locked. Only people who are in can see its pin now."
          : "Meetup unlocked. Its pin is back for the people it was posted for.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); });
  }

  // For a pin that is not a meetup's (one dropped before meetups took this
  // job over). A meetup's pin comes down by cancelling the meetup.
  function takeDown(flag) {
    request(flagUrl(flag.id), { method: "DELETE" })
      .then(function () {
        selected = null;
        announce("Your flag was taken down.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); });
  }

  // ---------- 8. loading ----------
  function loadFlags(quiet) {
    return request(urls.flags).then(function (data) {
      var first = !info.places.length;
      info.flags = data.flags;
      info.categories = data.categories;
      if (first) {
        info.places = data.places;
        renderPlaces();
      }
      if (selected && !findFlag(selected)) { selected = null; }
      // A "See on map" link names a flag: open it once it has loaded.
      var focus = focusId ? findFlag(focusId) : null;
      if (focus) { selected = focus.id; listing = null; focusId = ""; }
      if (!dragging) { renderMarkers(); }
      // The map to the flag first (where a link's flag is brought: viewFor),
      // then its card, put where there is room beside it. Not animated: the
      // card is placed against where the flag stands.
      if (focus) { map.setView(viewFor(focus, FLAG_ZOOM).centre, FLAG_ZOOM, { animate: false }); }
      if (mode === "idle") { renderDetail(); }
      // The first load's hint. Not over an error already on the line (the
      // locate key refused on an insecure page, say): an error outlasts a
      // hint.
      if (!quiet && !el.status.classList.contains("error")) {
        announce(focus ? "" : info.flags.length
          ? "Tap a flag to see what's happening." : "No meetups on the map yet. Post the first one.");
      }
    }).catch(function (error) {
      announce(error.message, "error");
    });
  }

  function pruneExpired() {
    var now = Date.now();
    var live = info.flags.filter(function (f) { return new Date(f.expires_at).getTime() > now; });
    if (live.length !== info.flags.length) {
      info.flags = live;
      if (selected && !findFlag(selected)) { selected = null; }
      if (!dragging) { renderMarkers(); }
    }
    if (mode === "idle") { renderDetail(); }   // keeps "Ends in" current
  }

  function locationFound(event) {
    el.locate.disabled = false;
    userLayer.clearLayers();
    L.circle(event.latlng, {
      radius: Math.min(event.accuracy, 180), color: "#ff791f", weight: 2,
      fillColor: "#ff791f", fillOpacity: 0.1, interactive: false
    }).addTo(userLayer);
    L.marker(event.latlng, {
      icon: L.divIcon({ className: "user-position-icon", html: '<span class="user-position-dot"></span>',
                        iconSize: [18, 18], iconAnchor: [9, 9] }),
      title: "You are here", keyboard: false
    }).addTo(userLayer);
    var inside = campusContains(event.latlng);
    announce(inside ? "Showing where you are. Your location stays in this browser."
      : "You're outside the campus area right now, so a meetup can't go where you are.",
      inside ? "success" : "error");
  }

  // ---------- 9. start ----------
  // The meetup form waits behind its key until the map is up; without this
  // script running at all it is simply there under the map.
  if (el.meetForm) { el.meetForm.hidden = true; }
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && mode === "pick") { endPick(); }
  });
  // Back to the whole campus, wherever the map has been dragged or zoomed.
  if (el.fit) {
    el.fit.addEventListener("click", function () {
      if (map) { map.fitBounds(CAMPUS_BOUNDS, { padding: [6, 6] }); }
    });
  }
  el.locate.addEventListener("click", function () {
    if (!map) { return; }
    if (!navigator.geolocation) {
      announce("This browser can't share a location.", "error");
      return;
    }
    // Browsers only share a location with a secure page. Over plain http --
    // the site opened by its LAN address on a phone, say -- the request is
    // refused before anybody is asked, so do not make it.
    if (!window.isSecureContext) {
      announce("Location only works on the secure (https) version of the site.", "error");
      return;
    }
    el.locate.disabled = true;
    announce("Finding you. Your location stays in this browser.");
    map.locate({ setView: true, maxZoom: 18, enableHighAccuracy: true, timeout: 12000 });
  });

  // "See on map" on a meetup card: open that flag here without reloading.
  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("a[data-flag]") : null;
    if (!link || !map) { return; }
    var flag = findFlag(link.dataset.flag);
    if (!flag) { return; }   // not loaded yet: let the link reload the page
    event.preventDefault();
    if (mode !== "idle") { endPick(); }
    announce("");
    root.scrollIntoView({ behavior: "smooth", block: "start" });
    select(flag.id, true);
  });

  // A small handle for other scripts on the page: refresh the flags, open
  // one by id, or re-measure the map after it was hidden.
  window.BeaconMap = {
    refresh: function () { return loadFlags(true); },
    focus: function (id) {
      if (!findFlag(id)) { return false; }
      if (mode !== "idle") { endPick(); }
      announce("");
      select(id, true);
      return true;
    },
    resize: function () { if (map) { map.invalidateSize(); } },
    // The meetup form on /find: put its pin on the map and let it be moved.
    // `onMove({lat, lng, near})` hears every move; `onDone()` hears Done.
    // `kind` is the type the form has chosen, for the pin's colour.
    pick: function (options) {
      if (!map) { return false; }
      startPick(options);
      return true;
    },
    // The form's type changed: the pin being placed changes colour with it.
    pickKind: function (kind) {
      if (!pick || !map) { return; }
      pick.kind = kind;
      renderMarkers();
    },
    // Moves the pin without the person touching the map (a place was picked).
    pickAt: function (lat, lng) {
      if (!pick || !map) { return; }
      pick.lat = lat; pick.lng = lng;
      renderMarkers();
      map.panTo([lat, lng]);
    },
    unpick: function () { endPick(); },
    // After a meetup is posted: the map goes back to the view the page
    // opens with, and the meetup's card opens on its flag there. The card
    // is put where there is room beside the flag (placeCallout), so the
    // campus stays in view: when it always stood above its flag, opening it
    // slid the map down until the campus was half off it. The flag is found
    // as your own meetup flag at the point it was posted with, the newest
    // one there: a second meetup posted on the same spot (the Campus Center,
    // where every pin starts) must open its own card, not the first one's.
    showPosted: function (lat, lng) {
      if (!map) { return Promise.resolve(false); }
      campusView(L.latLng(lat, lng));
      return loadFlags(true).then(function () {
        var found = null;
        info.flags.forEach(function (f) {
          if (f.mine && f.event &&
              Math.abs(f.latitude - lat) < 1e-5 && Math.abs(f.longitude - lng) < 1e-5 &&
              (!found || f.created_at > found.created_at)) { found = f; }
        });
        if (found) { select(found.id); }
        return !!found;
      });
    }
  };

  loadLeaflet().then(function () {
    initMap();
    ready = true;
    homeKey();
    showKey();
    // For the page's own script: the map can take a pin now. The Post tab
    // arrives asking for the form (data-post), and it waits for this.
    root.dispatchEvent(new CustomEvent("beacon:mapready"));
    loadFlags();
    if (window.Beacon && typeof window.Beacon.every === "function") {
      window.Beacon.every(function () { if (!dragging) { loadFlags(true); } }, REFRESH_MS);
      window.Beacon.every(pruneExpired, 30000);
    } else {
      window.setInterval(function () { if (!dragging) { loadFlags(true); } }, REFRESH_MS);
      window.setInterval(pruneExpired, 30000);
    }
  }).catch(function () {
    announce("The map library couldn't load. Check your internet connection.", "error");
    if (el.fit) { el.fit.disabled = true; }
    // No map to put a pin on: the meetup form is shown as it would be
    // without JavaScript, and the server pins the meetup on its place.
    if (el.meetForm) { el.meetForm.hidden = false; }
  });
})();
