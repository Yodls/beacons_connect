/*
  Community map (Leaflet + OpenStreetMap) for the Meet up page.

  What this file does, top to bottom:
    1. loads Leaflet from unpkg on demand
    2. draws the campus area, the landmark pins and everyone's flags
    3. runs the draft flag: tap the map, drag the dashed pin, fill the form,
       and nothing is saved until "Submit flag" (POST to data-flags-url)
    4. keeps the list, the key and the "N / 3 flags used" count up to date

  Beacon OS look: pins are pixel flags in the UMass Boston palette, with an
  icon from the page's sprite (templates/_icons.html); landmarks are pixel
  lighthouses; the Campus Center wears the UMass Boston logo. The markup and
  styles for all of these live in templates/_community_map.html and .css.
*/
(function () {
  "use strict";

  var root = document.getElementById("campusMapCard");
  if (!root || root.dataset.initialized === "1") { return; }
  root.dataset.initialized = "1";

  var LEAFLET_VERSION = "1.9.4";
  var LEAFLET_CSS = "https://unpkg.com/leaflet@" + LEAFLET_VERSION + "/dist/leaflet.css";
  var LEAFLET_JS = "https://unpkg.com/leaflet@" + LEAFLET_VERSION + "/dist/leaflet.js";
  var CAMPUS_CENTER = [42.31335, -71.03815];
  var CAMPUS_BOUNDS = [[42.3097, -71.0436], [42.3174, -71.0317]];
  // One look per flag type: the pin colour, the colour of the icon on it,
  // and which sprite icon it shows. Brand colours: Orange, Beacon Blue, Sky,
  // Yellow and Navy.
  var FLAG_COLORS = {
    game: "#ff791f",
    study: "#005a8b",
    social: "#78afe9",
    club: "#fede42",
    event: "#132245"
  };
  var FLAG_INK = { game: "#132245", study: "#ffffff", social: "#132245", club: "#132245", event: "#ffffff" };
  var FLAG_ICONS = { game: "game", study: "book", social: "talk", club: "club", event: "star" };
  var FLAG_LETTERS = { game: "G", study: "S", social: "C", club: "H", event: "E" };
  var DEFAULT_COLOR = "#005a8b";
  var CAMPUS_CENTER_KEY = "campus-center";
  var SVG_NS = "http://www.w3.org/2000/svg";

  var apiUrl = root.dataset.flagsUrl;
  var statusEl = document.getElementById("mapStatus");
  var countEl = document.getElementById("mapFlagCount");
  var listEl = document.getElementById("mapFlagList");
  var form = document.getElementById("mapFlagForm");
  var pointEl = document.getElementById("mapFlagPoint");
  var latitudeInput = document.getElementById("mapFlagLatitude");
  var longitudeInput = document.getElementById("mapFlagLongitude");
  var locationNameInput = document.getElementById("mapFlagLocationName");
  var visibilitySelect = document.getElementById("mapFlagVisibility");
  var clubWrap = document.getElementById("mapFlagClubWrap");
  var clubSelect = document.getElementById("mapFlagClub");
  var submitButton = document.getElementById("mapFlagSubmit");
  var locateButton = document.getElementById("mapLocate");
  var refreshButton = document.getElementById("mapRefresh");
  var legendEl = document.getElementById("mapLegend");
  var umbLogo = root.dataset.umbLogo || "";

  var map;
  var flagLayer;
  var placeLayer;
  var draftLayer;
  var userLayer;
  var places = [];
  var flags = [];
  var mine = 0;
  var limit = 3;
  var placeMarkers = {};
  var flagMarkers = {};
  var draftMarker = null;
  var selectedPoint = null;

  function announce(message, kind) {
    statusEl.textContent = message;
    statusEl.classList.toggle("error", kind === "error");
    statusEl.classList.toggle("success", kind === "success");
  }

  function loadLeaflet() {
    return new Promise(function (resolve, reject) {
      if (window.L) { resolve(window.L); return; }

      if (!document.getElementById("leaflet-css")) {
        var link = document.createElement("link");
        link.id = "leaflet-css";
        link.rel = "stylesheet";
        link.href = LEAFLET_CSS;
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
      script.id = "leaflet-js";
      script.src = LEAFLET_JS;
      script.crossOrigin = "";
      script.onload = function () { resolve(window.L); };
      script.onerror = reject;
      document.head.appendChild(script);
    });
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

  function initMap() {
    map = L.map("beaconCampusMap", {
      zoomControl: true,
      minZoom: 15,
      maxZoom: 19,
      maxBounds: CAMPUS_BOUNDS,
      maxBoundsViscosity: 0.85
    }).setView(CAMPUS_CENTER, 17);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
    }).addTo(map);

    // The campus area flags may go in: a dashed Beacon Blue outline.
    L.rectangle(CAMPUS_BOUNDS, {
      color: "#005a8b",
      weight: 2,
      opacity: 0.6,
      dashArray: "6 6",
      fillColor: "#005a8b",
      fillOpacity: 0.04,
      interactive: false
    }).addTo(map);

    placeLayer = L.layerGroup().addTo(map);
    draftLayer = L.layerGroup().addTo(map);
    flagLayer = L.layerGroup().addTo(map);
    userLayer = L.layerGroup().addTo(map);

    map.on("click", function (event) {
      if (campusContains(event.latlng)) {
        selectPoint(event.latlng);
      } else {
        announce("Place the flag inside the shaded UMass Boston campus area.", "error");
      }
    });

    loadFlags();
  }

  function campusContains(latlng) {
    return L.latLngBounds(CAMPUS_BOUNDS).contains(latlng);
  }

  // Markup for an icon from the page's sprite. Names are always one of the
  // fixed keys above, never user text.
  function spriteMarkup(name) {
    return '<svg class="icon" aria-hidden="true" focusable="false"><use href="#icon-' + name + '"/></svg>';
  }

  // The same icon as a DOM node, for the key and the list.
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

  // Landmarks: a pixel lighthouse on a yellow tile, except the Campus
  // Center, which wears the UMass Boston logo.
  function approvedIcon(place) {
    if (place && place.key === CAMPUS_CENTER_KEY && umbLogo) {
      return L.divIcon({
        className: "approved-place-icon",
        html: '<span class="campus-umb-pin" aria-hidden="true"><img src="' + umbLogo +
          '" alt="" width="46" height="22" /></span>',
        iconSize: [58, 34],
        iconAnchor: [29, 34]
      });
    }
    return L.divIcon({
      className: "approved-place-icon",
      html: '<span class="campus-lighthouse-pin" aria-hidden="true">' +
        spriteMarkup("lighthouse") + "</span>",
      iconSize: [28, 38],
      iconAnchor: [14, 38]
    });
  }

  // The draft flag: dashed and bobbing until "Submit flag" is pressed.
  function draftIcon() {
    return L.divIcon({
      className: "draft-position-icon",
      html: '<span class="beacon-flag-pin draft-position-pin">' +
        '<span class="flag-head">' + spriteMarkup("plus") + '</span>' +
        '<span class="flag-pole"></span></span>',
      iconSize: [36, 52],
      iconAnchor: [18, 52]
    });
  }

  function renderPlaces() {
    placeLayer.clearLayers();
    placeMarkers = {};
    places.forEach(function (place) {
      var marker = L.marker([place.latitude, place.longitude], {
        icon: approvedIcon(place),
        title: place.name,
        keyboard: true,
        zIndexOffset: 100
      }).addTo(placeLayer);
      marker.bindTooltip("Landmark: " + place.name, { direction: "top", offset: [0, -36] });
      marker.on("click", function () {
        selectPoint(marker.getLatLng());
        map.flyTo(marker.getLatLng(), 18, { duration: 0.45 });
      });
      placeMarkers[place.key] = marker;
    });
  }

  function suggestedLocationName(latlng) {
    if (!places.length) { return "Campus meetup point"; }
    var nearest = places.reduce(function (best, place) {
      var distance = map.distance(latlng, [place.latitude, place.longitude]);
      return !best || distance < best.distance ? { place: place, distance: distance } : best;
    }, null);
    return nearest.distance <= 200 ? "Near " + nearest.place.name : "Campus meetup point";
  }

  function selectPoint(latlng) {
    if (!campusContains(latlng)) {
      announce("Place the flag inside the shaded UMass Boston campus area.", "error");
      return;
    }

    selectedPoint = L.latLng(latlng.lat, latlng.lng);
    latitudeInput.value = selectedPoint.lat.toFixed(6);
    longitudeInput.value = selectedPoint.lng.toFixed(6);
    pointEl.textContent = "Selected: " + latitudeInput.value + ", " + longitudeInput.value;

    if (!locationNameInput.value.trim() || locationNameInput.dataset.autoFilled === "1") {
      locationNameInput.value = suggestedLocationName(selectedPoint);
      locationNameInput.dataset.autoFilled = "1";
    }

    if (!draftMarker) {
      draftMarker = L.marker(selectedPoint, {
        icon: draftIcon(),
        draggable: true,
        title: "Drag to adjust the flag position",
        zIndexOffset: 800
      }).addTo(draftLayer);
      draftMarker.bindTooltip("Drag to adjust", { direction: "top", offset: [0, -50] });
      draftMarker.on("dragend", function (event) {
        var next = event.target.getLatLng();
        if (!campusContains(next)) {
          event.target.setLatLng(selectedPoint);
          announce("The flag must remain inside the shaded campus area.", "error");
          return;
        }
        selectPoint(next);
      });
    } else {
      draftMarker.setLatLng(selectedPoint);
    }

    announce("Flag position selected. Complete the form or drag the preview pin.", "success");
  }

  function clearSelectedPoint() {
    selectedPoint = null;
    latitudeInput.value = "";
    longitudeInput.value = "";
    pointEl.textContent = "Choose a point on the map";
    locationNameInput.value = "";
    locationNameInput.dataset.autoFilled = "1";
    draftLayer.clearLayers();
    draftMarker = null;
  }

  // A live flag: a square pixel head on a pole, coloured by its type.
  function flagIcon(flag) {
    var color = FLAG_COLORS[flag.category] || DEFAULT_COLOR;
    var ink = FLAG_INK[flag.category] || "#ffffff";
    var icon = FLAG_ICONS[flag.category] || "star";
    return L.divIcon({
      className: "beacon-flag-icon",
      html: '<span class="beacon-flag-pin" style="--flag-color:' + color + ";--flag-ink:" + ink +
        '" data-letter="' + (FLAG_LETTERS[flag.category] || "B") + '">' +
        '<span class="flag-head">' + spriteMarkup(icon) + '</span>' +
        '<span class="flag-pole"></span></span>',
      iconSize: [36, 52],
      iconAnchor: [18, 52],
      popupAnchor: [0, -48]
    });
  }

  function text(tag, className, value) {
    var element = document.createElement(tag);
    if (className) { element.className = className; }
    element.textContent = value;
    return element;
  }

  function expiresText(value) {
    var remaining = new Date(value).getTime() - Date.now();
    if (remaining <= 0) { return "Expired"; }
    var minutes = Math.ceil(remaining / 60000);
    if (minutes < 60) { return "Expires in " + minutes + " min"; }
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return "Expires in " + hours + "h" + (rest ? " " + rest + "m" : "");
  }

  function popupFor(flag) {
    var box = document.createElement("div");
    box.appendChild(text("h3", "flag-popup-title", flag.title));
    box.appendChild(text("p", "flag-popup-meta",
      flag.category_label + " · " + flag.location_name));
    box.appendChild(text("p", "flag-popup-meta",
      "Posted by " + flag.owner_name + " · " + expiresText(flag.expires_at)));
    if (flag.visibility === "club" && flag.club_name) {
      box.appendChild(text("p", "flag-popup-meta", "Visible to " + flag.club_name));
    }
    if (flag.description) { box.appendChild(text("p", "flag-popup-note", flag.description)); }
    box.appendChild(text("p", "flag-popup-id", "Flag " + flag.id));
    if (flag.mine) {
      var remove = text("button", "flag-popup-delete", "Remove my flag");
      remove.type = "button";
      remove.addEventListener("click", function () { deleteFlag(flag.id); });
      box.appendChild(remove);
    }
    return box;
  }

  function renderFlags() {
    flagLayer.clearLayers();
    flagMarkers = {};
    flags.forEach(function (flag) {
      var marker = L.marker([flag.latitude, flag.longitude], {
        icon: flagIcon(flag),
        title: flag.title,
        keyboard: true,
        zIndexOffset: 500
      }).addTo(flagLayer);
      marker.bindPopup(popupFor(flag), { maxWidth: 270 });
      flagMarkers[flag.id] = marker;
    });
    renderFlagList();
  }

  function renderFlagList() {
    listEl.replaceChildren();
    if (!flags.length) {
      listEl.appendChild(text("p", "flag-list-empty",
        "No active flags yet. Be the first to start something on campus."));
      return;
    }

    flags.forEach(function (flag) {
      var color = FLAG_COLORS[flag.category] || DEFAULT_COLOR;
      var item = document.createElement("article");
      item.className = "flag-item";
      item.style.setProperty("--flag-color", color);
      item.style.setProperty("--flag-ink", FLAG_INK[flag.category] || "#ffffff");
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-label", "Show " + flag.title + " on map");

      var top = document.createElement("div");
      top.className = "flag-item-top";
      top.appendChild(text("span", "flag-chip", flag.category_label));
      top.appendChild(text("b", "", flag.title));
      item.appendChild(top);
      item.appendChild(text("small", "", flag.location_name + " · " + expiresText(flag.expires_at)));

      function focusFlag() {
        var marker = flagMarkers[flag.id];
        if (!marker) { return; }
        map.flyTo(marker.getLatLng(), 18, { duration: 0.45 });
        marker.openPopup();
      }
      item.addEventListener("click", focusFlag);
      item.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); focusFlag();
        }
      });
      listEl.appendChild(item);
    });
  }

  function populateControls(data) {
    if (!places.length) {
      places = data.places;

      Object.keys(data.categories).forEach(function (key) {
        var option = document.createElement("option");
        option.value = key;
        option.textContent = data.categories[key];
        form.elements.category.appendChild(option);
      });

      data.durations.forEach(function (minutes) {
        var option = document.createElement("option");
        option.value = minutes;
        option.textContent = minutes < 60 ? minutes + " minutes" :
          (minutes / 60) + ((minutes / 60) === 1 ? " hour" : " hours");
        form.elements.duration.appendChild(option);
      });
      form.elements.duration.value = "60";

      if (data.clubs.length) {
        var clubVisibility = document.createElement("option");
        clubVisibility.value = "club";
        clubVisibility.textContent = "One of my clubs";
        visibilitySelect.appendChild(clubVisibility);
        data.clubs.forEach(function (club) {
          var clubOption = document.createElement("option");
          clubOption.value = club.id;
          clubOption.textContent = club.name;
          clubSelect.appendChild(clubOption);
        });
      }
      renderPlaces();
      renderLegend(data.categories);
    }
  }

  // The key under the map: one swatch per flag type, plus landmarks.
  function renderLegend(categories) {
    if (!legendEl) { return; }
    legendEl.replaceChildren();
    function key(label, swatch) {
      var item = document.createElement("li");
      item.className = "map-key";
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(label));
      legendEl.appendChild(item);
    }
    Object.keys(categories).forEach(function (name) {
      var swatch = document.createElement("span");
      swatch.className = "map-key-swatch";
      swatch.style.setProperty("--flag-color", FLAG_COLORS[name] || DEFAULT_COLOR);
      swatch.style.setProperty("--flag-ink", FLAG_INK[name] || "#ffffff");
      swatch.appendChild(spriteNode(FLAG_ICONS[name] || "star"));
      key(categories[name], swatch);
    });
    var place = document.createElement("span");
    place.className = "map-key-swatch place";
    place.appendChild(spriteNode("lighthouse"));
    key("Landmark", place);
  }

  function updateCount() {
    countEl.textContent = mine + " / " + limit + " flags used";
    submitButton.disabled = mine >= limit;
    submitButton.textContent = mine >= limit ? "Flag limit reached" : "Submit flag";
  }

  function loadFlags(quiet) {
    return request(apiUrl).then(function (data) {
      populateControls(data);
      flags = data.flags;
      mine = data.mine;
      limit = data.limit;
      updateCount();
      renderFlags();
      if (!quiet) { announce(flags.length ? "Campus flags are up to date." : "No active flags yet."); }
    }).catch(function (error) {
      announce(error.message, "error");
      listEl.replaceChildren(text("p", "flag-list-empty", "The campus map could not load."));
    });
  }

  function deleteFlag(id) {
    request(apiUrl + "/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () {
        announce("Your flag was removed.", "success");
        return loadFlags(true);
      })
      .catch(function (error) { announce(error.message, "error"); });
  }

  function pruneExpired() {
    var active = flags.filter(function (flag) {
      return new Date(flag.expires_at).getTime() > Date.now();
    });
    if (active.length !== flags.length) {
      flags = active;
      mine = flags.filter(function (flag) { return flag.mine; }).length;
      updateCount();
      renderFlags();
      announce("An expired flag was removed from the map.");
    } else {
      renderFlagList();
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!selectedPoint || !latitudeInput.value || !longitudeInput.value) {
      announce("Click inside the shaded campus area to choose a flag position.", "error");
      return;
    }
    var data = new FormData(form);
    var payload = {
      title: data.get("title"),
      description: data.get("description"),
      category: data.get("category"),
      duration: data.get("duration"),
      location_name: data.get("location_name"),
      latitude: data.get("latitude"),
      longitude: data.get("longitude"),
      visibility: data.get("visibility"),
      club_id: data.get("club_id") || null
    };
    submitButton.disabled = true;
    request(apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (data) {
      form.elements.title.value = "";
      form.elements.description.value = "";
      announce("Flag placed at " + data.flag.location_name + ".", "success");
      clearSelectedPoint();
      return loadFlags(true);
    }).catch(function (error) {
      announce(error.message, "error");
    }).finally(updateCount);
  });

  locationNameInput.addEventListener("input", function () {
    locationNameInput.dataset.autoFilled = "0";
  });

  visibilitySelect.addEventListener("change", function () {
    clubWrap.hidden = visibilitySelect.value !== "club";
    clubSelect.required = visibilitySelect.value === "club";
  });

  refreshButton.addEventListener("click", function () { loadFlags(); });

  locateButton.addEventListener("click", function () {
    if (!navigator.geolocation) {
      announce("This browser does not provide location services.", "error");
      return;
    }
    locateButton.disabled = true;
    announce("Requesting your location. It will not be uploaded.");
    map.locate({ setView: true, maxZoom: 18, enableHighAccuracy: true, timeout: 12000 });
  });

  function locationFinished() { locateButton.disabled = false; }

  function locationFound(event) {
    locationFinished();
    userLayer.clearLayers();
    var icon = L.divIcon({
      className: "user-position-icon",
      html: '<span class="user-position-dot"></span>',
      iconSize: [18, 18], iconAnchor: [9, 9]
    });
    L.circle(event.latlng, {
      radius: Math.min(event.accuracy, 180), color: "#ff791f", weight: 2,
      fillColor: "#ff791f", fillOpacity: 0.1, interactive: false
    }).addTo(userLayer);
    L.marker(event.latlng, { icon: icon, title: "Your approximate location" }).addTo(userLayer);
    var inside = campusContains(event.latlng);
    announce(inside ?
      "Location found. Click the map to choose a flag position." :
      "You appear to be outside the campus area. Flags must stay inside the shaded boundary.",
      inside ? "success" : "error");
  }

  function locationError(event) {
    locationFinished();
    announce(event.message || "Your location could not be determined.", "error");
  }

  loadLeaflet().then(function () {
    initMap();
    map.on("locationfound", locationFound);
    map.on("locationerror", locationError);
    if (window.Beacon && typeof window.Beacon.every === "function") {
      window.Beacon.every(function () { loadFlags(true); }, 20000);
      window.Beacon.every(pruneExpired, 30000);
    } else {
      window.setInterval(function () { loadFlags(true); }, 20000);
      window.setInterval(pruneExpired, 30000);
    }
  }).catch(function () {
    announce("The open-source map library could not load. Check your internet connection.", "error");
    countEl.textContent = "Map unavailable";
  });
})();
