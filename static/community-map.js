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
   10. start ........... wiring the buttons, "See on map" links, ?flag=<id>

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
  // U-Find shows its own wordmark instead of an icon.
  var KINDS = {
    game:   { color: "#ff791f", ink: "#132245", icon: "game" },
    study:  { color: "#005a8b", ink: "#ffffff", icon: "book" },
    social: { color: "#78afe9", ink: "#132245", icon: "talk" },
    club:   { color: "#fede42", ink: "#132245", icon: "club" },
    event:  { color: "#132245", ink: "#ffffff", icon: "star" },
    ufind:  { color: "#0b3159", ink: "#ffffff", icon: null }
  };
  // Who can see a flag, in the words the editor uses.
  var AUDIENCE = {
    "public": "Everyone",
    friends: "My friend(s)",
    club: "One of my clubs",
    going: "Only people who are in"
  };

  // ---------- 2. page elements ----------
  function $(id) { return document.getElementById(id); }
  var el = {
    status: $("mapStatus"), total: $("mapFlagTotal"), count: $("mapFlagCount"),
    legend: $("mapLegend"), detail: $("mapFlagDetail"), drop: $("mapDrop"),
    locate: $("mapLocate"), form: $("mapFlagForm"), formTitle: $("flagFormTitle"),
    draftTag: $("mapDraftTag"), draftNote: $("mapDraftNote"), kinds: $("mapKinds"),
    kindChips: $("mapKindChips"), titleLabel: $("mapTitleLabel"), priceWrap: $("mapPriceWrap"),
    point: $("mapFlagPoint"), lat: $("mapFlagLatitude"), lng: $("mapFlagLongitude"),
    locName: $("mapFlagLocationName"), durationWrap: $("mapDurationWrap"),
    visibility: $("mapFlagVisibility"), friendPick: $("mapFriendPick"),
    friendChips: $("mapFriendChips"), friendSearch: $("mapFriendSearch"),
    friendList: $("mapFriendList"), friendMore: $("mapFriendMore"),
    clubWrap: $("mapFlagClubWrap"), club: $("mapFlagClub"), safety: $("mapUfindSafety"),
    error: $("mapFormError"), submit: $("mapFlagSubmit"), cancel: $("mapFlagCancel")
  };
  var urls = {
    flags: root.dataset.flagsUrl,
    umb: root.dataset.umbLogo || "",
    ufind: root.dataset.ufindLogo || "",
    chat: root.dataset.chatUrl || "",   // ends in /0: swap in the chat's id
    rsvp: root.dataset.rsvpUrl || ""    // contains /0/: swap in the meetup's id
  };

  // ---------- state ----------
  var map, placeLayer, flagLayer, userLayer;
  var info = { flags: [], categories: {}, order: [], durations: [], places: [],
               clubs: [], friends: [], mine: 0, limit: 3 };
  var mode = "idle";      // idle | placing | draft
  var draft = null;       // the flag being drafted or edited (see startDraft)
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
      if (flag.visibility === "club") { return "You, for " + flag.club_name + " members"; }
      if (flag.visibility === "friends") { return "You, for " + (names.length ? listNames(names) : "your friends"); }
      if (flag.visibility === "going") { return "Your meetup. Only people who are in can see it."; }
      return (flag.event ? "Your meetup." : "Posted by you.") + " Everyone can see it.";
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

  function initMap() {
    map = L.map("beaconCampusMap", {
      zoomControl: true, minZoom: 15, maxZoom: 19,
      maxBounds: CAMPUS_BOUNDS, maxBoundsViscosity: 0.85
    }).setView(CAMPUS_CENTER, 17);

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

    map.on("click", function (event) {
      if (mode === "idle") { return; }
      if (!campusContains(event.latlng)) {
        announce("Flags have to go inside the dashed campus area.", "error");
        return;
      }
      if (mode === "placing") { startDraft(event.latlng); } else { moveDraft(event.latlng); }
    });
    map.on("locationfound", locationFound);
    map.on("locationerror", function (event) {
      el.locate.disabled = false;
      announce(event.message || "Your location couldn't be found. Check the browser's location setting.", "error");
    });
  }

  // Landmarks: a pixel lighthouse on a yellow tile; the Campus Center wears
  // the UMass Boston logo.
  function placeIcon(place) {
    if (place.key === CAMPUS_CENTER_KEY && urls.umb) {
      return L.divIcon({
        className: "approved-place-icon",
        html: '<span class="campus-umb-pin" aria-hidden="true"><img src="' + urls.umb +
          '" alt="" width="46" height="22" /></span>',
        iconSize: [58, 34], iconAnchor: [29, 34]
      });
    }
    return L.divIcon({
      className: "approved-place-icon",
      html: '<span class="campus-lighthouse-pin" aria-hidden="true">' + spriteMarkup("lighthouse") + "</span>",
      iconSize: [28, 38], iconAnchor: [14, 38]
    });
  }

  function renderPlaces() {
    placeLayer.clearLayers();
    info.places.forEach(function (place) {
      var marker = L.marker([place.latitude, place.longitude], {
        icon: placeIcon(place), title: place.name, keyboard: false, zIndexOffset: -100
      }).addTo(placeLayer);
      marker.bindTooltip(place.name, { direction: "top", offset: [0, -36] });
      // While placing, a landmark is a fine place to drop a flag.
      marker.on("click", function () {
        if (mode === "placing") { startDraft(marker.getLatLng()); }
        else if (mode === "draft") { moveDraft(marker.getLatLng()); }
      });
    });
  }

  // A flag pin: a square pixel head on a pole. U-Find shows its wordmark.
  function flagIcon(category, options) {
    var look = kindOf(category);
    var ufind = category === "ufind" && urls.ufind;
    var classes = "beacon-flag-pin" + (ufind ? " ufind" : "") +
      (options.on ? " on" : "") + (options.draft ? " draft" : "");
    var head = ufind
      ? '<img src="' + urls.ufind + '" alt="" width="60" height="16" />'
      : spriteMarkup(options.draft && !options.keepIcon ? "plus" : look.icon);
    return L.divIcon({
      className: "beacon-flag-icon",
      html: '<span class="' + classes + '" style="--flag-color:' + look.color + ";--flag-ink:" + look.ink + '">' +
        '<span class="flag-head">' + head + '</span><span class="flag-pole"></span></span>',
      iconSize: ufind ? [80, 52] : [36, 52],
      iconAnchor: ufind ? [40, 52] : [18, 52]
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
      marker.on("click", function () { select(flag.id); });
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
  }

  function renderLegend() {
    el.legend.replaceChildren();
    info.order.forEach(function (key) {
      var item = node("li", "map-key");
      if (key === "ufind" && urls.ufind) {
        var img = node("img", "map-key-ufind");
        img.src = urls.ufind; img.alt = labelOf(key); img.width = 60; img.height = 16;
        item.appendChild(img);
      } else {
        var swatch = node("span", "map-key-swatch");
        swatch.style.setProperty("--flag-color", kindOf(key).color);
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(labelOf(key)));
      }
      el.legend.appendChild(item);
    });
  }

  function renderCount() {
    el.total.textContent = info.flags.length;
    var full = info.mine >= info.limit;
    el.count.textContent = full
      ? "You have " + info.limit + " flags up, the most at once. Take one down to drop another."
      : "You have " + info.mine + " of " + info.limit + " flags up. Meetup flags don't count.";
    el.drop.disabled = full && mode === "idle";
  }

  // ---------- 5. the flag card ----------
  function select(id, fly, quiet) {
    selected = id;
    confirmDown = null;
    renderDetail();
    if (!quiet) { renderMarkers(); }
    var flag = findFlag(id);
    if (flag && fly && map) { map.flyTo([flag.latitude, flag.longitude], 18, { duration: 0.45 }); }
  }

  function tag(text, extra) { return node("span", "tag" + (extra ? " " + extra : ""), text); }

  function button(text, className, onClick) {
    var b = node("button", className, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function renderDetail() {
    // Rebuild only when something on the card changed, so a refresh never
    // pulls a button out from under somebody about to press it.
    var current = selected && mode === "idle" ? findFlag(selected) : null;
    var key = mode + "|" + confirmDown + "|" + JSON.stringify(current) +
      (current ? expiresText(current.expires_at) : "");
    if (key === shownCard) { return; }
    shownCard = key;
    el.detail.replaceChildren();
    if (mode === "placing") {
      el.detail.appendChild(node("p", "flag-detail fd-hint",
        "Tap the map where you want people to find you."));
      return;
    }
    var flag = selected && mode === "idle" ? findFlag(selected) : null;
    if (!flag) { return; }
    var look = kindOf(flag.category);
    var card = node("div", "flag-detail");
    card.style.setProperty("--flag-color", look.color);
    card.style.setProperty("--flag-ink", look.ink);

    var top = node("div", "fd-top");
    var text = node("div", "grow");
    text.appendChild(node("b", "fd-title", flag.title));
    text.appendChild(node("span", "fd-sub", flag.location_name));
    text.appendChild(node("span", "fd-sub", whoLine(flag)));
    top.appendChild(text);
    var close = node("button", "fd-close");
    close.type = "button";
    close.setAttribute("aria-label", "Close this flag's details");
    close.appendChild(spriteNode("close"));
    close.addEventListener("click", function () { select(null); });
    top.appendChild(close);
    card.appendChild(top);

    if (flag.description) { card.appendChild(node("p", "fd-note", flag.description)); }

    var tags = node("div", "fd-tags");
    if (flag.category === "ufind" && urls.ufind) {
      var uf = tag("", "ufind");
      var img = node("img"); img.src = urls.ufind; img.alt = "U-Find";
      uf.appendChild(img); tags.appendChild(uf);
      tags.appendChild(tag(flag.price ? "$" + flag.price : "Free", "price"));
    } else {
      tags.appendChild(tag(labelOf(flag.category), "kind"));
    }
    if (!flag.event) { tags.appendChild(tag(expiresText(flag.expires_at))); }
    if (flag.mine || flag.visibility === "going") { tags.appendChild(tag(AUDIENCE[flag.visibility])); }
    card.appendChild(tags);

    // A meetup's flag: when it is, how many are in, and the way in.
    var ev = flag.event;
    if (ev) {
      var line = node("div", "fd-event");
      if (ev.when) { line.appendChild(node("span", "", ev.when)); }
      line.appendChild(node("span", "", ev.going + " of " + ev.max + " going"));
      if (ev.im_going && !flag.mine) { line.appendChild(node("span", "", "You're in")); }
      card.appendChild(line);
    }

    var actions = node("div", "fd-actions");
    if (flag.mine) {
      actions.appendChild(button("Edit", "map-button", function () { startEdit(flag); }));
      if (ev) {
        var nextAudience = flag.visibility === "public" ? "going" : "public";
        actions.appendChild(button(
          flag.visibility === "public" ? "Only people who are in" : "Make public",
          "map-button", function () { saveAudience(flag, nextAudience); }));
      }
      actions.appendChild(button(confirmDown === flag.id ? "Tap again to take down" : "Take down",
        "map-button warn", function () {
          if (confirmDown !== flag.id) { confirmDown = flag.id; renderDetail(); return; }
          takeDown(flag);
        }));
    }
    if (ev && !flag.mine && !ev.im_going && ev.open) {
      // A real form, so "I'm in" works the same as on the meetup's card.
      var form = node("form");
      form.method = "post";
      form.action = urls.rsvp.replace("/0/", "/" + ev.post_id + "/");
      var back = node("input"); back.type = "hidden"; back.name = "back"; back.value = "find";
      form.appendChild(back);
      var join = node("button", "", ev.full ? "It's full" : "I'm in");
      join.type = "submit"; join.disabled = ev.full;
      form.appendChild(join);
      actions.appendChild(form);
    }
    if (ev && ev.chat_id) {
      var chat = node("a", "key", "Open meetup chat");
      chat.href = urls.chat.replace(/\/0$/, "/" + ev.chat_id);
      actions.appendChild(chat);
    }
    if (actions.children.length) { card.appendChild(actions); }
    if (flag.mine) { card.appendChild(node("p", "fd-hint", "Drag your flag on the map to move it.")); }
    el.detail.appendChild(card);
  }

  // ---------- 6. the draft ----------
  function setMode(next) {
    mode = next;
    root.classList.toggle("placing", mode !== "idle");
    el.form.hidden = mode !== "draft";
    el.drop.parentNode.hidden = mode === "draft";
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
      lat: latlng.lat, lng: latlng.lng, labelTouched: false,
      visibility: "public", picked: [], club: info.clubs.length ? String(info.clubs[0].id) : ""
    };
    fillForm({ title: "", description: "", price: "", location_name: nearestPlaceName(latlng) });
    setMode("draft");
    announce("Draft flag placed. Fill it in, then press Submit flag.", "success");
    setTimeout(function () { el.form.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 60);
  }

  // Editing one of your flags: the same editor, filled in.
  function startEdit(flag) {
    draft = {
      editing: flag.id, event: !!flag.event, category: flag.category,
      lat: flag.latitude, lng: flag.longitude, labelTouched: true,
      visibility: flag.visibility,
      picked: (flag.friends || []).map(function (f) { return f.id; }),
      club: flag.club_id ? String(flag.club_id) : (info.clubs.length ? String(info.clubs[0].id) : "")
    };
    fillForm({ title: flag.title, description: flag.description, price: flag.price || "",
               location_name: flag.location_name });
    setMode("draft");
    announce("Editing your flag. Drag it or tap the map to move it.", "success");
    setTimeout(function () { el.form.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 60);
  }

  function moveDraft(latlng) {
    draft.lat = latlng.lat; draft.lng = latlng.lng;
    if (!draft.labelTouched) { el.locName.value = nearestPlaceName(latlng); }
    showPoint();
    renderMarkers();
  }

  function showPoint() {
    el.lat.value = draft.lat.toFixed(6);
    el.lng.value = draft.lng.toFixed(6);
    el.point.textContent = (draft.editing ? "At " : "Selected: ") + el.lat.value + ", " + el.lng.value;
  }

  function cancelDraft() {
    draft = null;
    el.error.hidden = true;
    setMode("idle");
    announce("");
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
    f.price.value = values.price || "";
    el.locName.value = values.location_name || "";
    el.error.hidden = true;
    el.formTitle.textContent = draft.editing ? (draft.event ? "Edit your meetup's flag" : "Edit your flag") : "New flag";
    el.draftTag.textContent = draft.editing ? "Editing" : "Draft";
    el.draftNote.textContent = draft.editing
      ? "Changes show once you save them. Tap the map or drag the dashed flag to move it."
      : "Only you can see this until you submit it. Tap the map or drag the dashed flag to move it.";
    el.submit.textContent = draft.editing ? "Save changes" : "Submit flag";
    el.cancel.textContent = draft.editing ? "Cancel" : "Discard";

    // The type chips. A meetup's flag can't become a U-Find listing.
    el.kindChips.replaceChildren();
    info.order.forEach(function (key) {
      var label = node("label", "kind-chip");
      var input = node("input");
      input.type = "radio"; input.name = "category"; input.value = key;
      input.checked = key === draft.category;
      input.disabled = draft.event && key === "ufind";
      input.addEventListener("change", function () { draft.category = key; kindChanged(); });
      var face = node("span");
      face.style.setProperty("--flag-color", kindOf(key).color);
      if (key === "ufind" && urls.ufind) {
        var img = node("img"); img.src = urls.ufind; img.alt = labelOf(key); face.appendChild(img);
      } else {
        face.appendChild(node("i"));
        face.appendChild(document.createTextNode(labelOf(key)));
      }
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

  // U-Find asks what you're selling and for a price, with a safety note.
  function kindChanged() {
    var ufind = draft.category === "ufind";
    el.titleLabel.textContent = ufind ? "What are you selling?" : "Title";
    el.form.elements.title.placeholder = ufind ? "Mini fridge, textbook, bike..." : "Pickup frisbee after class";
    el.form.elements.description.placeholder = ufind
      ? "Condition, pickup time, how you take payment" : "How to find you, what to bring, who can join";
    el.priceWrap.hidden = !ufind;
    el.safety.hidden = !ufind;
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
    if (draft.category === "ufind") { body.price = f.price.value; }
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
      showFormError(draft.category === "ufind" ? "Say what you're selling." : "Give your flag a title.");
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
          : "Only people who are in can see this flag now.", "success");
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
      if (mode === "idle") { renderDetail(); }
      if (focus) { map.setView([focus.latitude, focus.longitude], 18); }
      if (!quiet) {
        announce(info.flags.length ? "Tap a flag to see what's happening." : "No flags yet. Drop the first one.");
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
  el.form.addEventListener("submit", submitDraft);
  el.cancel.addEventListener("click", cancelDraft);
  el.visibility.addEventListener("change", audienceChanged);
  el.club.addEventListener("change", function () { if (draft) { draft.club = el.club.value; } });
  el.locName.addEventListener("input", function () { if (draft) { draft.labelTouched = true; } });
  el.friendSearch.addEventListener("input", renderFriendList);
  // Enter in the friend search would submit the whole form.
  el.friendSearch.addEventListener("keydown", function (event) {
    if (event.key === "Enter") { event.preventDefault(); }
  });
  el.locate.addEventListener("click", function () {
    if (!map) { return; }
    if (!navigator.geolocation) {
      announce("This browser can't share a location.", "error");
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
    root.scrollIntoView({ behavior: "smooth", block: "start" });
    select(flag.id, true);
  });

  loadLeaflet().then(function () {
    initMap();
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
    el.count.textContent = "Map unavailable";
    el.drop.disabled = true;
  });
})();
