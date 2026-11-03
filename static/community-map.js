/*
  Campus map (Leaflet + OpenStreetMap) for the Meet up page.

  What this file does, top to bottom:
    1. settings ........ map area, flag looks, how often to refresh
    2. page elements ... everything it drives, found by id in _community_map.html
    3. helpers ......... status line, requests, icons, text
    4. the map ......... Leaflet with OpenStreetMap tiles, landmarks, flags
    5. the flag card ... what you see when you tap a flag, and its buttons
    6. the draft ....... "Drop a flag", the dashed draft flag and its editor
    7. friends ......... the "My friend(s)" picker
    8. saving .......... create (POST), edit or move (PATCH), take down (DELETE)
    9. loading ......... the flag list, refreshed every 20 seconds
   10. start ........... wiring the buttons, "See on map" links, ?flag=<id>,
                         and window.BeaconMap for other scripts

  Who can do what is decided by the server (app.py, "Campus map"); this file
  only offers the buttons. Your own flags can be dragged on the map and
  edited from their card. A meetup's flag (an "event flag") is seen only by
  the people who said they're in, until its author makes it public.
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
  var CAMPUS_CENTER = [42.31335, -71.03815];
  var CAMPUS_BOUNDS = [[42.3097, -71.0436], [42.3174, -71.0317]];
  var CAMPUS_CENTER_KEY = "campus-center";
  var REFRESH_MS = 20000;
  var FRIENDS_SHOWN = 6;
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
  // Who can see a flag, in the words the editor uses.
  var AUDIENCE = {
    "public": "Everyone",
    friends: "My friend(s)",
    club: "One of my clubs",
    // A meetup's flag, kept to the people who are in. "Locked" is the state
    // on the card; the button that does it says "Lock meetup".
    going: "Locked"
  };

  // ---------- 2. page elements ----------
  function $(id) { return document.getElementById(id); }
  var el = {
    status: $("mapStatus"), total: $("mapFlagTotal"), fit: $("mapFit"),
    legend: $("mapLegend"), detail: $("mapFlagDetail"), drop: $("mapDrop"),
    locate: $("mapLocate"), form: $("mapFlagForm"), formTitle: $("flagFormTitle"),
    draftTag: $("mapDraftTag"), draftNote: $("mapDraftNote"), kinds: $("mapKinds"),
    kindChips: $("mapKindChips"), titleLabel: $("mapTitleLabel"),
    lat: $("mapFlagLatitude"), lng: $("mapFlagLongitude"),
    locName: $("mapFlagLocationName"), durationWrap: $("mapDurationWrap"),
    visibility: $("mapFlagVisibility"), friendPick: $("mapFriendPick"),
    friendChips: $("mapFriendChips"), friendSearch: $("mapFriendSearch"),
    friendList: $("mapFriendList"), friendMore: $("mapFriendMore"),
    clubWrap: $("mapFlagClubWrap"), club: $("mapFlagClub"),
    error: $("mapFormError"), submit: $("mapFlagSubmit"), cancel: $("mapFlagCancel"),
    // /find only: the "Post a meetup" key and the form it opens (see 6b).
    meet: $("mapMeet"), meetForm: $("mapMeetForm")
  };
  var urls = {
    flags: root.dataset.flagsUrl,
    umb: root.dataset.umbLogo || "",
    healey: root.dataset.healeyLogo || "",
    rec: root.dataset.recLogo || "",
    chat: root.dataset.chatUrl || "",   // ends in /0: swap in the chat's id
    rsvp: root.dataset.rsvpUrl || ""    // contains /0/: swap in the meetup's id
  };

  // ---------- state ----------
  var map, placeLayer, flagLayer, userLayer;
  var info = { flags: [], categories: {}, order: [], durations: [], places: [],
               clubs: [], friends: [], mine: 0, limit: 3 };
  var mode = "idle";      // idle | placing | draft | pick
  var ready = false;      // the map is up: the keys under it can be pressed
  var draft = null;       // the flag being drafted or edited (see startDraft)
  var pick = null;        // the meetup pin the form on /find is placing (see 6b)
  var selected = null;    // id of the flag shown in the card
  var dragging = false;   // a pin is being dragged: hold refreshes
  var focusId = root.dataset.focus || "";
  var confirmDown = null; // id of a flag whose "Take down" was pressed once
  var shownCard = "";     // what the card last showed, so refreshes don't rebuild it

  // ---------- 3. helpers ----------
  function announce(message, kind) {
    el.status.textContent = message || "";
    el.status.classList.toggle("error", kind === "error");
    el.status.classList.toggle("success", kind === "success");
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

  // On a touch screen, one-finger drag pans the map -- so a thumb trying to
  // scroll past 350px of tiles moves the tiles instead and the page never
  // moves. The map starts locked and a tap unlocks it; a tap anywhere else
  // locks it again. Mouse users never see this.
  //
  // "Elsewhere" means outside the map's whole stage, not just the tiles: the
  // Locate me button and the status line sit beside the tiles in the stage,
  // and tapping Locate me used to count as leaving the map and lock it again.
  var unlockMap = function () {};
  function lockUntilTap(container) {
    var touch = ("ontouchstart" in window) || navigator.maxTouchPoints > 0;
    if (!touch) { return; }
    var stage = container.closest(".campus-map-stage") || container;
    var button = node("button", "map-unlock", "Tap to move the map");
    button.type = "button";
    function lock() {
      map.dragging.disable(); map.touchZoom.disable();
      if (!button.isConnected) { container.appendChild(button); }
    }
    function unlock() {
      map.dragging.enable(); map.touchZoom.enable();
      button.remove();
    }
    unlockMap = unlock;
    button.addEventListener("click", function (ev) { ev.stopPropagation(); unlock(); });
    document.addEventListener("touchstart", function (ev) {
      if (!stage.contains(ev.target)) { lock(); }
    }, { passive: true });
    lock();
  }

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

  function initMap() {
    map = L.map("beaconCampusMap", {
      zoomControl: true, minZoom: 15, maxZoom: 19,
      // The map may be panned half a campus past the dashed area on every
      // side (flags still have to go inside it): a flag's callout stands
      // on the flag, and one on the north edge needs the map to pan that
      // far to show it.
      maxBounds: L.latLngBounds(CAMPUS_BOUNDS).pad(0.5), maxBoundsViscosity: 0.85
    }).setView(CAMPUS_CENTER, 17);
    lockUntilTap(document.getElementById("beaconCampusMap"));

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
        announce("Map tiles didn't load. OpenStreetMap only serves them to the site's own web address.", "error");
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

    // The map's box changes size when a flag's sheet grows it on a phone.
    // Leaflet only watches the window, so it is told here. While a sheet
    // is up the map keeps its top edge still (the flag stays where it was
    // tapped) rather than its centre.
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        map.invalidateSize({ pan: !root.classList.contains("showing"), animate: false });
      }).observe(map.getContainer());
    }

    map.on("click", function (event) {
      // A tap on the map puts a landmark back, and on a phone puts the
      // flag's sheet away (Leaflet shuts the callout itself).
      if (mode === "idle") { pickPlace(null); if (phone() && selected) { select(null); } return; }
      if (mode === "pick") { movePick(event.latlng); return; }
      if (!campusContains(event.latlng)) {
        announce("Flags have to go inside the dashed campus area.", "error");
        return;
      }
      if (mode === "placing") { startDraft(event.latlng); } else { moveDraft(event.latlng); }
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
      // While placing, a landmark is a fine place to drop a flag.
      marker.on("click", function () {
        if (mode === "placing") { startDraft(marker.getLatLng()); }
        else if (mode === "draft") { moveDraft(marker.getLatLng()); }
        else if (mode === "pick") { movePick(marker.getLatLng()); }
        else { pickPlace(marker); }
      });
    });
  }

  // A flag pin: a navy pole with a waving cloth in the kind's colour and
  // its icon on it (drawn by _map_chrome.css).
  function flagIcon(category, options) {
    var look = kindOf(category);
    var classes = "beacon-flag-pin" +
      (options.on ? " on" : "") + (options.draft ? " draft" : "");
    var head = spriteMarkup(options.draft && !options.keepIcon ? "plus" : look.icon);
    return L.divIcon({
      className: "beacon-flag-icon",
      html: '<span class="' + classes + '" style="--flag-color:' + look.color + ";--flag-ink:" + look.ink + '">' +
        '<span class="flag-head">' + head + '</span><span class="flag-pole"></span></span>',
      iconSize: [36, 52],
      iconAnchor: [18, 52]
    });
  }

  function renderMarkers() {
    if (!map) { return; }
    flagLayer.clearLayers();
    info.flags.forEach(function (flag) {
      if (draft && draft.editing === flag.id) { return; }   // the draft stands in for it
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
    });
    if (draft) {
      var pin = L.marker([draft.lat, draft.lng], {
        icon: flagIcon(draft.category, { draft: true, keepIcon: true }),
        draggable: true, title: "Your draft flag. Drag to move it.", zIndexOffset: 2000
      }).addTo(flagLayer);
      pin.on("dragend", function () {
        var next = pin.getLatLng();
        if (!campusContains(next)) {
          announce("Flags have to go inside the dashed campus area.", "error");
          pin.setLatLng([draft.lat, draft.lng]);
          return;
        }
        moveDraft(next);
      });
    }
    if (pick) {
      var spot = L.marker([pick.lat, pick.lng], {
        icon: flagIcon("event", { draft: true, keepIcon: true }),
        draggable: true, title: "Your meetup. Drag to move it.", zIndexOffset: 2000
      }).addTo(flagLayer);
      spot.on("dragend", function () { movePick(spot.getLatLng()); });
    }
  }

  function renderLegend() {
    el.legend.replaceChildren();
    info.order.forEach(function (key) {
      var item = node("li", "map-key");
      {
        var swatch = node("span", "map-key-swatch");
        swatch.style.setProperty("--flag-color", kindOf(key).color);
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(labelOf(key)));
      }
      el.legend.appendChild(item);
    });
  }

  function renderCount() {
    // Your flags up, of the most you can have, in the map's title bar.
    var full = info.mine >= info.limit;
    el.total.textContent = info.mine + "/" + info.limit;
    el.total.classList.toggle("full", full);
    el.total.title = full
      ? "All " + info.limit + " of your flags are up. Take one down to drop another."
      : info.mine + " of your " + info.limit + " flags are up. Meetup flags don't count.";
    el.drop.disabled = full && mode === "idle";
  }

  // ---------- 5. the flag card ----------
  // The flag you tapped opens in a callout on the map: a Beacon OS window
  // standing on the flag with a tail pointing at it (a Leaflet popup,
  // dressed by _community_map.css). On a phone, where a callout would not
  // fit over 300px of tiles, the same card rises as a sheet over the foot
  // of the map instead, and the map grows a little to keep the flag in
  // view above it. One card is built either way; only its home differs.
  var PHONE = window.matchMedia("(max-width: 560px)");
  var CALLOUT_LIFT = 68;  // px from the flag's point up to the callout's foot
  var CALLOUT_FOOT = 12;  // the callout's own margin under that, holding its tail
  var SHEET_MS = 220;
  var callout = null;     // the Leaflet popup, made the first time it is needed
  var shutting = false;   // the callout is being closed from here, not by a tap
  var sheet = null;       // the phone's sheet, made the first time it is needed
  var sheetMoving = null; // its slide, so a second tap doesn't fight the first
  var sheetHeight = 0;    // how tall the sheet stands (its card may scroll inside)
  var sheetBand = 0;      // the band of map left above it
  var mapBase = 0;        // the map's height before a sheet grew it
  var mapTall = 0;        // and once it has
  var flying = false;     // select() is flying to the flag: no other nudges
  var stage = root.querySelector(".campus-map-stage");

  function phone() { return PHONE.matches; }
  function still() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  function select(id, fly, quiet) {
    selected = id;
    confirmDown = null;
    // Flying there puts the card in view itself, so the callout's own
    // nudge to fit on opening is not wanted on top of it.
    flying = !!fly;
    if (callout) { callout.options.autoPan = !fly; }
    renderDetail();
    flying = false;
    if (callout) { callout.options.autoPan = true; }
    if (!quiet) { renderMarkers(); }
    var flag = findFlag(id);
    if (flag && fly && map) { flyToFlag(flag); }
    // Your own flag can be dragged: said in the status line, where there
    // is room for it (not over the phone's band of map).
    if (flag && flag.mine && !phone()) { announce("Drag your flag on the map to move it."); }
  }

  // Fly to a flag so that its card lands in the middle of the map: the
  // point flown to is the flag's, moved by half the card's height (up for a
  // callout standing on the flag, down for a sheet under it).
  function flyToFlag(flag) {
    var zoom = 18;
    var at = L.latLng(flag.latitude, flag.longitude);
    var lift = 0;
    if (phone()) {
      // The flag's foot near the bottom of the band above the sheet.
      if (sheetHeight) { lift = Math.max(60, sheetBand - 14) - mapTall / 2; }
    } else if (callout && callout.isOpen()) {
      lift = callout.getElement().offsetHeight / 2 + CALLOUT_LIFT + CALLOUT_FOOT;
      // Once there, let the callout nudge the map if any of it is still
      // off the edge.
      map.once("moveend", function () { if (callout.isOpen()) { callout.update(); } });
    }
    var centre = lift ? map.unproject(map.project(at, zoom).subtract([0, lift]), zoom) : at;
    map.flyTo(centre, zoom, { duration: 0.45 });
  }

  function tag(text, extra) { return node("span", "tag" + (extra ? " " + extra : ""), text); }

  function button(text, className, onClick) {
    var b = node("button", className, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  // Somebody's avatar: their initials on the colour their id gives them,
  // the same as _ui.html's macro draws everywhere else.
  function avatar(name, id) {
    var parts = String(name || "").split(" ").filter(Boolean);
    var initials = (parts.length ? parts[0][0] : "") + (parts.length > 1 ? parts[parts.length - 1][0] : "");
    var a = node("span", "avatar sm av-" + (Math.abs(parseInt(id, 10) || 0) % 6), initials.toUpperCase());
    a.setAttribute("aria-hidden", "true");
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

  // The card itself: a title bar in the flag's colour, who put it there and
  // what it is, a line each for when, where and who, the note, the keys.
  function buildCard(flag) {
    var look = kindOf(flag.category);
    var ev = flag.event;
    var card = node("div", "flag-detail" + (ev ? " is-event" : ""));
    card.style.setProperty("--flag-color", look.color);
    card.style.setProperty("--flag-ink", look.ink);

    var bar = node("div", "fd-bar");
    bar.appendChild(spriteNode(look.icon));
    bar.appendChild(node("span", "fd-kind", ev ? "Meetup" : labelOf(flag.category)));
    bar.appendChild(node("span", "fd-grip"));
    var close = node("button", "fd-close");
    close.type = "button";
    close.setAttribute("aria-label", "Close this flag's details");
    close.appendChild(spriteNode("close"));
    close.addEventListener("click", function () { select(null); });
    bar.appendChild(close);
    card.appendChild(bar);

    var body = node("div", "fd-body");
    var head = node("div", "fd-head");
    head.appendChild(avatar(flag.owner_name, flag.owner_id));
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
        people.slice(0, 5).forEach(function (p) { faces.appendChild(avatar(p.name, p.id)); });
        who.appendChild(faces);
      }
      var names = people.map(function (p) { return firstName(p.name); });
      who.appendChild(node("span", "", (names.length ? peopleText(names) + " · " : "") +
        ev.going + " of " + ev.max + " going"));
      // Your own meetup needs neither: "Your meetup" says you're in, and
      // the Lock meetup key says everyone can see it.
      if (ev.im_going && !flag.mine) { who.appendChild(tag("You're in", "in")); }
      if (flag.visibility === "going") {
        line(lines, "lock").appendChild(node("span", "", "Locked: only people who are in can see it"));
      }
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
      // Your own flag's keys, smaller, on a row of their own.
      var own = node("div", "fd-own");
      own.appendChild(button("Edit", "map-button small", function () { startEdit(flag); }));
      if (ev) {
        var nextAudience = flag.visibility === "public" ? "going" : "public";
        own.appendChild(button(
          flag.visibility === "public" ? "Lock meetup" : "Make public",
          "map-button small", function () { saveAudience(flag, nextAudience); }));
      }
      own.appendChild(button(confirmDown === flag.id ? "Tap again to take down" : "Take down",
        "map-button small warn", function () {
          if (confirmDown !== flag.id) { confirmDown = flag.id; renderDetail(); return; }
          takeDown(flag);
        }));
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

  function renderDetail() {
    // Rebuild only when something on the card changed, so a refresh never
    // pulls a button out from under somebody about to press it.
    var current = selected && mode === "idle" ? findFlag(selected) : null;
    var key = mode + "|" + confirmDown + "|" + (phone() ? "sheet" : "callout") + "|" +
      JSON.stringify(current) +
      (current ? expiresText(current.expires_at) + soonText(current.event) : "");
    if (key === shownCard) { return; }
    shownCard = key;
    el.detail.replaceChildren();
    if (mode === "placing") {
      el.detail.appendChild(node("p", "fd-hint", "Tap the map where you want people to find you."));
    }
    if (!current || !map) { closeCallout(); closeSheet(); return; }
    var card = buildCard(current);
    if (phone()) { closeCallout(); showSheet(card, current); }
    else { closeSheet(); showCallout(card, current); }
  }

  // ---------- 5a. the callout ----------
  function showCallout(card, flag) {
    if (!callout) {
      callout = L.popup({
        className: "beacon-callout", closeButton: false,
        minWidth: 280, maxWidth: 320, offset: [0, -CALLOUT_LIFT],
        autoPan: true, autoPanPaddingTopLeft: [16, 12], autoPanPaddingBottomRight: [64, 12]
      });
      // Leaflet shuts the callout on a tap on the map or Escape: the flag
      // is then no longer the selected one, and its pin stands down.
      map.on("popupclose", function (event) {
        if (event.popup !== callout || shutting || !selected) { return; }
        selected = null;
        shownCard = "";
        renderMarkers();
      });
    }
    // A long card scrolls inside the callout rather than growing it past
    // what fits over the flag: the flag and the tail stay in view.
    callout.options.maxHeight = Math.max(160, map.getSize().y - CALLOUT_LIFT - CALLOUT_FOOT - 28);
    callout.setLatLng([flag.latitude, flag.longitude]);
    callout.setContent(card);
    if (!callout.isOpen()) { callout.openOn(map); }
  }
  function closeCallout() {
    if (!callout || !callout.isOpen()) { return; }
    shutting = true;
    map.closePopup(callout);
    shutting = false;
  }

  // ---------- 5b. the sheet (phones) ----------
  function showSheet(card, flag) {
    if (!sheet) {
      sheet = node("div", "map-sheet");
      sheet.hidden = true;
      stage.appendChild(sheet);
      swipeToClose(sheet);
    }
    var was = !sheet.hidden;
    if (sheetMoving) { sheetMoving.cancel(); sheetMoving = null; }
    sheet.replaceChildren(card);
    sheet.hidden = false;
    var band = sizeSheet();
    if (!was && !still()) {
      sheetMoving = sheet.animate([{ transform: "translateY(100%)" }, { transform: "translateY(0)" }],
                                  { duration: SHEET_MS, easing: "ease-out" });
      sheetMoving.onfinish = function () { sheetMoving = null; };
    }
    // The flag into the band of map left above the sheet, its foot near
    // the band's bottom so the whole pin shows (the map keeps its top edge
    // still while it grows, so this holds once it has).
    if (!was && !flying) {
      var want = L.point(map.getSize().x / 2, Math.max(60, band - 14));
      var by = map.latLngToContainerPoint([flag.latitude, flag.longitude]).subtract(want);
      if (Math.abs(by.x) > 2 || Math.abs(by.y) > 2) { map.panBy(by, { animate: !still() }); }
    }
    // The page scrolls so the sheet's foot is clear of the tab bar.
    if (!was) { window.requestAnimationFrame(clearTabBar); }
  }
  function clearTabBar() {
    var bar = document.querySelector("nav.tabbar");
    var floor = bar && getComputedStyle(bar).position === "fixed"
      ? bar.getBoundingClientRect().top : window.innerHeight;
    // The map's height as it will be once it has grown, not as it is
    // mid-growth.
    var box = map.getContainer();
    var tall = parseFloat(box.style.height) || box.getBoundingClientRect().height;
    var foot = stage.getBoundingClientRect().top + tall;
    if (foot + 8 > floor) { window.scrollBy({ top: foot + 8 - floor, behavior: still() ? "auto" : "smooth" }); }
  }
  function closeSheet() {
    if (!sheet || sheet.hidden) { return; }
    if (sheetMoving) { sheetMoving.cancel(); sheetMoving = null; }
    var done = function () { sheet.hidden = true; sheet.replaceChildren(); sizeSheet(); };
    if (still()) { done(); return; }
    sheetMoving = sheet.animate([{ transform: "translateY(0)" }, { transform: "translateY(100%)" }],
                                { duration: SHEET_MS, easing: "ease-in" });
    sheetMoving.onfinish = function () { sheetMoving = null; done(); };
  }
  // Sizes the map to its sheet: tall enough to show the whole card with a
  // band of map above it, up to most of the screen (the card scrolls past
  // that). Tells the stage how tall the sheet is (--sheet-h: the status
  // line moves up out of its way) and gives back the band's height.
  function sizeSheet() {
    var box = map.getContainer();
    var natural = 0;
    if (sheet && !sheet.hidden) {
      sheet.style.maxHeight = "none";
      natural = sheet.offsetHeight;
      sheet.style.maxHeight = "";
    }
    if (!natural) {
      sheetHeight = sheetBand = mapBase = mapTall = 0;
      stage.style.removeProperty("--sheet-h");
      box.style.height = "";
      root.classList.remove("showing");
      return 0;
    }
    if (!mapBase) { mapBase = box.getBoundingClientRect().height; }
    // 96px of map above the sheet: room for the whole pin and a little
    // around it.
    mapTall = Math.max(mapBase, Math.min(natural + 96, window.innerHeight * 0.7));
    sheetHeight = Math.min(natural, mapTall - 56);
    sheetBand = mapTall - sheetHeight;
    stage.style.setProperty("--sheet-h", sheetHeight + "px");
    root.classList.add("showing");
    box.style.height = mapTall + "px";
    return sheetBand;
  }
  // A finger drawn down the sheet's title bar puts it away.
  function swipeToClose(box) {
    var startY = null;
    box.addEventListener("touchstart", function (event) {
      var bar = event.target.closest ? event.target.closest(".fd-bar") : null;
      startY = bar && event.touches.length === 1 ? event.touches[0].clientY : null;
    }, { passive: true });
    box.addEventListener("touchend", function (event) {
      if (startY === null) { return; }
      var dy = event.changedTouches[0].clientY - startY;
      startY = null;
      if (dy > 50) { select(null); }
    }, { passive: true });
  }
  // Escape puts the card away. Leaflet does this for its popup only while
  // the map itself has focus, which a tap inside the callout never gives
  // it, so it is done here for both homes.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !selected) { return; }
    if ((sheet && !sheet.hidden) || (callout && callout.isOpen())) { select(null); }
  });
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

  // ---------- 6. the draft ----------
  function setMode(next) {
    mode = next;
    root.classList.toggle("placing", mode !== "idle");
    el.form.hidden = mode !== "draft";
    // The meetup form opens under the map while its pin is being placed,
    // and the keys under the map step aside for whichever form is open.
    if (el.meetForm) { el.meetForm.hidden = mode !== "pick"; }
    el.drop.parentNode.hidden = mode === "draft" || mode === "pick";
    if (el.meet) { el.meet.hidden = !ready || mode !== "idle"; }
    el.drop.classList.toggle("cancel", mode === "placing");
    el.drop.querySelector("span").textContent = mode === "placing" ? "Cancel" : "Drop a flag";
    renderCount();
    renderDetail();
    renderMarkers();
  }

  // A new flag where the map was tapped, with sensible defaults.
  function startDraft(latlng) {
    draft = {
      editing: null, event: false, category: "social",
      lat: latlng.lat, lng: latlng.lng,
      visibility: "public", picked: [], club: info.clubs.length ? String(info.clubs[0].id) : ""
    };
    fillForm({ title: "", description: "", location_name: nearestPlaceName(latlng) });
    setMode("draft");
    announce("Draft flag placed. Fill it in, then press Submit flag.", "success");
    setTimeout(function () { el.form.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 60);
  }

  // Editing one of your flags: the same editor, filled in.
  function startEdit(flag) {
    draft = {
      editing: flag.id, event: !!flag.event, category: flag.category,
      lat: flag.latitude, lng: flag.longitude,
      visibility: flag.visibility,
      picked: (flag.friends || []).map(function (f) { return f.id; }),
      club: flag.club_id ? String(flag.club_id) : (info.clubs.length ? String(info.clubs[0].id) : "")
    };
    fillForm({ title: flag.title, description: flag.description,
               location_name: flag.location_name });
    setMode("draft");
    announce("Editing your flag. Drag it or tap the map to move it.", "success");
    setTimeout(function () { el.form.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 60);
  }

  // The flag's label is where it is, in words, and follows the pin: nobody
  // types a "where" for a flag, the map is the where.
  function moveDraft(latlng) {
    draft.lat = latlng.lat; draft.lng = latlng.lng;
    el.locName.value = nearestPlaceName(latlng);
    showPoint();
    renderMarkers();
  }

  function showPoint() {
    el.lat.value = draft.lat.toFixed(6);
    el.lng.value = draft.lng.toFixed(6);
  }

  function cancelDraft() {
    if (pick) { endPick(); return; }
    draft = null;
    el.error.hidden = true;
    setMode("idle");
    announce("");
  }

  // ---------- 6b. a meetup's pin, for the form on /find ----------
  // Not a flag of its own: the meetup form owns the point, and the server
  // makes the meetup's flag when it is posted. This only lets the person
  // put the pin somewhere and tells the form where, in words as well. The
  // form itself (#mapMeetForm) is shown under the map for as long as the
  // pin is out (setMode), and the page's own script fills it in.
  function startPick(options) {
    if (mode === "placing" || mode === "draft") { draft = null; el.error.hidden = true; }
    selected = null;
    pick = { lat: options.lat, lng: options.lng, onMove: options.onMove, onDone: options.onDone };
    setMode("pick");
    unlockMap();
    map.setView([pick.lat, pick.lng], Math.max(map.getZoom(), 17));
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

  function option(value, text) {
    var o = node("option", "", text);
    o.value = value;
    return o;
  }

  function fillForm(values) {
    var f = el.form.elements;
    f.title.value = values.title || "";
    f.description.value = values.description || "";
    el.locName.value = values.location_name || "";
    el.error.hidden = true;
    el.formTitle.textContent = draft.editing ? (draft.event ? "Edit your meetup's flag" : "Edit your flag") : "New flag";
    el.draftTag.textContent = draft.editing ? "Editing" : "Draft";
    el.draftNote.textContent = draft.editing
      ? "Changes show once you save them. Tap the map or drag the dashed flag to move it."
      : "Only you can see this until you submit it. Tap the map or drag the dashed flag to move it.";
    el.submit.textContent = draft.editing ? "Save changes" : "Submit flag";
    el.cancel.textContent = draft.editing ? "Cancel" : "Discard";

    // The type chips.
    el.kindChips.replaceChildren();
    info.order.forEach(function (key) {
      var label = node("label", "kind-chip");
      var input = node("input");
      input.type = "radio"; input.name = "category"; input.value = key;
      input.checked = key === draft.category;
      input.addEventListener("change", function () { draft.category = key; kindChanged(); });
      var face = node("span");
      face.style.setProperty("--flag-color", kindOf(key).color);
      face.appendChild(node("i"));
      face.appendChild(document.createTextNode(labelOf(key)));
      label.appendChild(input); label.appendChild(face);
      el.kindChips.appendChild(label);
    });

    // How long it stays up: hand-dropped flags only. An edit can keep its time.
    var duration = f.duration;
    duration.replaceChildren();
    if (draft.editing) { duration.appendChild(option("", "Keep its current time")); }
    info.durations.forEach(function (minutes) {
      duration.appendChild(option(String(minutes), minutes < 60 ? minutes + " minutes"
        : (minutes / 60) + (minutes === 60 ? " hour" : " hours")));
    });
    duration.value = draft.editing ? "" : "60";
    el.durationWrap.hidden = draft.event;

    // Who can see it: a meetup's flag is for the people who are in, or everyone.
    el.visibility.replaceChildren();
    (draft.event ? ["going", "public"] : ["public", "friends", "club"]).forEach(function (key) {
      var o = option(key, AUDIENCE[key]);
      if (key === "going") { o.textContent = "Locked (only people who are in)"; }
      if (key === "club" && !info.clubs.length) { o.disabled = true; o.textContent += " (join a club first)"; }
      if (key === "friends" && !info.friends.length) { o.disabled = true; o.textContent += " (add friends first)"; }
      el.visibility.appendChild(o);
    });
    el.visibility.value = draft.visibility;

    el.club.replaceChildren();
    info.clubs.forEach(function (club) { el.club.appendChild(option(String(club.id), club.name)); });
    if (draft.club) { el.club.value = draft.club; }

    el.friendSearch.value = "";
    kindChanged();
    audienceChanged();
    showPoint();
  }

  // Every kind asks the same two things now that selling is not one of
  // them. Kept as a function because the draft still re-renders on a change.
  function kindChanged() {
    el.titleLabel.textContent = "Title";
    el.form.elements.title.placeholder = "Pickup frisbee after class";
    el.form.elements.description.placeholder =
      "How to find you, what to bring, who can join";
    renderMarkers();
  }

  function audienceChanged() {
    draft.visibility = el.visibility.value;
    el.clubWrap.hidden = draft.visibility !== "club";
    el.friendPick.hidden = draft.visibility !== "friends";
    if (draft.visibility === "friends") { renderFriendChips(); renderFriendList(); }
  }

  // ---------- 7. friends ----------
  function friendName(id) {
    for (var i = 0; i < info.friends.length; i += 1) {
      if (info.friends[i].id === id) { return info.friends[i].name; }
    }
    return "Friend";
  }

  function renderFriendChips() {
    el.friendChips.replaceChildren();
    if (!draft.picked.length) { el.friendChips.appendChild(node("span", "none", "Nobody picked yet.")); return; }
    draft.picked.forEach(function (id) {
      var chip = button(firstName(friendName(id)), "fchip", function () {
        draft.picked = draft.picked.filter(function (x) { return x !== id; });
        renderFriendChips(); renderFriendList();
      });
      chip.setAttribute("aria-label", "Remove " + friendName(id));
      chip.appendChild(spriteNode("close"));
      el.friendChips.appendChild(chip);
    });
  }

  // Six at a time; the search finds anyone else.
  function renderFriendList() {
    var q = el.friendSearch.value.trim().toLowerCase();
    var matches = info.friends.filter(function (f) { return !q || f.name.toLowerCase().indexOf(q) !== -1; });
    el.friendList.replaceChildren();
    matches.slice(0, FRIENDS_SHOWN).forEach(function (f) {
      var row = node("label", "pickrow");
      var box = node("input"); box.type = "checkbox"; box.checked = draft.picked.indexOf(f.id) !== -1;
      box.addEventListener("change", function () {
        draft.picked = box.checked ? draft.picked.concat([f.id])
          : draft.picked.filter(function (x) { return x !== f.id; });
        renderFriendChips();
      });
      var tick = node("span", "cbox"); tick.appendChild(spriteNode("check"));
      var parts = f.name.split(/\s+/);
      var avatar = node("span", "avatar av-" + (f.id % 6),
        (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase());
      avatar.setAttribute("aria-hidden", "true");
      row.appendChild(box); row.appendChild(tick); row.appendChild(avatar);
      row.appendChild(node("span", "grow", f.name));
      el.friendList.appendChild(row);
    });
    var more = !matches.length ? "No friends named “" + el.friendSearch.value.trim() + "”. Try a first name."
      : matches.length > FRIENDS_SHOWN
        ? (q ? "Showing " + FRIENDS_SHOWN + " of " + matches.length + " matches. Keep typing to narrow it down."
             : "Showing " + FRIENDS_SHOWN + " of your " + matches.length + " friends. Search to find anyone else.")
        : "";
    el.friendMore.textContent = more;
    el.friendMore.hidden = !more;
  }

  // ---------- 8. saving ----------
  function flagUrl(id) { return urls.flags + "/" + encodeURIComponent(id); }

  function formPayload() {
    var f = el.form.elements;
    var body = {
      title: f.title.value, description: f.description.value,
      category: draft.category, location_name: el.locName.value,
      latitude: el.lat.value, longitude: el.lng.value,
      visibility: el.visibility.value
    };
    if (!draft.event && f.duration.value) { body.duration = f.duration.value; }
    if (body.visibility === "club") { body.club_id = el.club.value; }
    if (body.visibility === "friends") { body.friend_ids = draft.picked.slice(); }
    return body;
  }

  function showFormError(message) {
    el.error.textContent = message;
    el.error.hidden = false;
    announce(message, "error");
  }

  function submitDraft(event) {
    event.preventDefault();
    if (!el.form.elements.title.value.trim()) {
      showFormError("Give your flag a title.");
      el.form.elements.title.focus();
      return;
    }
    if (el.visibility.value === "friends" && !draft.picked.length) {
      showFormError("Pick at least one friend to share it with.");
      el.friendSearch.focus();
      return;
    }
    el.submit.disabled = true;
    var editing = draft.editing;
    var saving = editing ? sendJSON(flagUrl(editing), "PATCH", formPayload())
                         : sendJSON(urls.flags, "POST", formPayload());
    saving.then(function (reply) {
      draft = null;
      setMode("idle");
      announce(editing ? "Flag updated." : "Your flag is live at " + reply.flag.location_name + ".", "success");
      return loadFlags(true).then(function () { select(reply.flag.id); });
    }).catch(function (error) {
      showFormError(error.message);
    }).finally(function () { el.submit.disabled = false; });
  }

  // Dragging one of your flags saves its new spot straight away.
  function moveFlag(flag, latlng, marker) {
    if (!campusContains(latlng)) {
      marker.setLatLng([flag.latitude, flag.longitude]);
      announce("Flags have to go inside the dashed campus area.", "error");
      return;
    }
    flag.latitude = latlng.lat; flag.longitude = latlng.lng;   // stay put while saving
    sendJSON(flagUrl(flag.id), "PATCH", { latitude: latlng.lat, longitude: latlng.lng })
      .then(function () {
        announce("Flag moved. Everyone who can see it sees the new spot.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); loadFlags(true); });
  }

  function saveAudience(flag, visibility) {
    sendJSON(flagUrl(flag.id), "PATCH", { visibility: visibility })
      .then(function () {
        announce(visibility === "public" ? "Everyone can see this meetup's flag now."
          : "Meetup locked. Only people who are in can see its flag now.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); });
  }

  function takeDown(flag) {
    request(flagUrl(flag.id), { method: "DELETE" })
      .then(function () {
        selected = null;
        announce(flag.event ? "Flag taken down. The meetup is still on." : "Your flag was taken down.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); });
  }

  // ---------- 9. loading ----------
  function loadFlags(quiet) {
    return request(urls.flags).then(function (data) {
      var first = !info.order.length;
      info.flags = data.flags;
      info.mine = data.mine;
      info.limit = data.limit;
      info.categories = data.categories;
      info.order = data.category_order || Object.keys(data.categories);
      info.durations = data.durations;
      info.clubs = data.clubs;
      info.friends = data.friends || [];
      if (first) {
        info.places = data.places;
        renderPlaces();
        renderLegend();
      }
      if (selected && !findFlag(selected)) { selected = null; }
      // A "See on map" link names a flag: open it once it has loaded.
      var focus = focusId ? findFlag(focusId) : null;
      if (focus) { selected = focus.id; focusId = ""; }
      renderCount();
      if (!dragging) { renderMarkers(); }
      // The flag first, then its card, which nudges the map to fit it. Not
      // animated: a zoom still playing would end by putting the map back
      // where it was aiming, undoing the card's nudge.
      if (focus) { map.setView([focus.latitude, focus.longitude], 18, { animate: false }); }
      if (mode === "idle") { renderDetail(); }
      if (!quiet) {
        announce(focus ? "" : info.flags.length
          ? "Tap a flag to see what's happening." : "No flags yet. Drop the first one.");
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
      renderCount();
      if (!dragging) { renderMarkers(); }
    }
    if (mode === "idle") { renderDetail(); }   // keeps "Ends in" current
  }

  function locationFound(event) {
    el.locate.disabled = false;
    // You just asked to see yourself on the map, so you can move around
    // there straight away rather than tapping to unlock it first.
    unlockMap();
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
      : "You're outside the campus area right now, so flags can't go where you are.",
      inside ? "success" : "error");
  }

  // ---------- 10. start ----------
  el.drop.addEventListener("click", function () {
    if (mode === "placing") { setMode("idle"); announce(""); return; }
    if (info.mine >= info.limit) { return; }
    selected = null;
    setMode("placing");
    announce("Tap the map where you want people to find you.");
  });
  // The meetup form waits behind its key until the map is up; without this
  // script running at all it is simply there under the map.
  if (el.meetForm) { el.meetForm.hidden = true; }
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && mode === "pick") { endPick(); }
  });
  el.form.addEventListener("submit", submitDraft);
  el.cancel.addEventListener("click", cancelDraft);
  el.visibility.addEventListener("change", audienceChanged);
  el.club.addEventListener("change", function () { if (draft) { draft.club = el.club.value; } });
  el.friendSearch.addEventListener("input", renderFriendList);
  // Enter in the friend search would submit the whole form.
  el.friendSearch.addEventListener("keydown", function (event) {
    if (event.key === "Enter") { event.preventDefault(); }
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
      announce("Showing your location needs the site's secure (https) address.", "error");
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
    if (mode !== "idle") { cancelDraft(); }
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
      if (mode !== "idle") { cancelDraft(); }
      announce("");
      select(id, true);
      return true;
    },
    resize: function () { if (map) { map.invalidateSize(); } },
    // The meetup form on /find: put its pin on the map and let it be moved.
    // `onMove({lat, lng, near})` hears every move; `onDone()` hears Done.
    pick: function (options) {
      if (!map) { return false; }
      startPick(options);
      return true;
    },
    // Moves the pin without the person touching the map (a place was picked).
    pickAt: function (lat, lng) {
      if (!pick || !map) { return; }
      pick.lat = lat; pick.lng = lng;
      renderMarkers();
      map.panTo([lat, lng]);
    },
    unpick: function () { endPick(); },
    // After a meetup is posted: show its flag, found as your own meetup
    // flag at the point it was posted with.
    focusAt: function (lat, lng) {
      return loadFlags(true).then(function () {
        var found = null;
        info.flags.forEach(function (f) {
          if (f.mine && f.category === "event" &&
              Math.abs(f.latitude - lat) < 1e-5 && Math.abs(f.longitude - lng) < 1e-5) { found = f; }
        });
        if (found) { select(found.id, true, true); renderMarkers(); }
        return !!found;
      });
    }
  };

  loadLeaflet().then(function () {
    initMap();
    ready = true;
    if (el.meet) { el.meet.hidden = false; }
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
    el.total.textContent = "\u2013";
    if (el.fit) { el.fit.disabled = true; }
    el.drop.disabled = true;
    // No map to put a pin on: the meetup form is shown as it would be
    // without JavaScript, and the server pins the meetup on its place.
    if (el.meetForm) { el.meetForm.hidden = false; }
  });
})();
