// The campus map in a meetup form: the where, as it is on /find.
//
// BeaconPin.mount(box) takes a .pinbox (templates/_meetwin.html) -- the
// hidden latitude and longitude inputs, a .pin-map, the .pin-where line
// and the landmark chips -- and keeps them all saying the same place. The
// pin starts where the chosen activity usually is (data-lat and data-lng
// on the activity's option) or on the Campus Center, and choosing another
// activity moves it until the person has moved it themselves. A drag of
// the pin, a tap on the map, a chip or a tap on a landmark moves it. Its
// words are the nearest landmark's within 200 m ("Near Healey Library"),
// else "Campus meetup point": the server's own rule (nearest_place_name in
// app.py), which it applies again to what is posted, so this only has to
// agree for the readout.
//
// The chips and the inputs work before Leaflet is up, and without it (a
// test DOM, a blocked CDN): the map is the picture, not the mechanism.
// Leaflet 1.9.4 loads from unpkg the first time a box is mounted, the way
// templates/fixture_new.html loads it.
(function () {
  var NEAR_METRES = 200;
  var ZOOM = 17;
  var CAMPUS_CENTER = { lat: 42.312960, lng: -71.036660 };
  var loading = null;

  function leaflet() {
    if (loading) { return loading; }
    loading = new Promise(function (resolve, reject) {
      if (window.L) { resolve(); return; }
      var css = document.createElement("link");
      css.id = "leaflet-css";
      css.rel = "stylesheet"; css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      document.head.appendChild(css);
      var js = document.createElement("script");
      js.id = "leaflet-js";
      js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      js.onload = resolve; js.onerror = reject;
      document.head.appendChild(js);
    });
    return loading;
  }

  // Great-circle distance, the server's metres_between.
  function metres(a, b) {
    var rad = Math.PI / 180;
    var p1 = a.lat * rad, p2 = b.lat * rad;
    var dp = (b.lat - a.lat) * rad, dl = (b.lng - a.lng) * rad;
    var h = Math.sin(dp / 2) * Math.sin(dp / 2)
          + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * 6371000 * Math.asin(Math.sqrt(h));
  }

  function nearest(places, at) {
    var best = null, span = Infinity;
    places.forEach(function (p) {
      var d = metres(at, p);
      if (d < span) { best = p; span = d; }
    });
    return best && span <= NEAR_METRES ? best : null;
  }

  function mount(box) {
    if (!box || box.dataset.mounted) { return; }
    box.dataset.mounted = "1";
    box.classList.add("js");
    var form = box.closest("form");
    var places = JSON.parse(box.dataset.places || "[]");
    var bounds = JSON.parse(box.dataset.bounds || "null");
    var lat = form.querySelector("input[name=latitude]");
    var lng = form.querySelector("input[name=longitude]");
    var where = box.querySelector(".pin-where");
    var mapEl = box.querySelector(".pin-map");
    var grow = box.querySelector(".pin-grow");
    var chips = box.querySelectorAll(".pin-chips .chip");
    var act = form.querySelector(".act");
    var reduced = !!(window.matchMedia
                     && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var point = null;      // {lat, lng}: where the pin is
    var touched = false;   // moved by hand, so the activity no longer decides
    var map = null, pin = null;

    function inside(p) {
      return !bounds || (p.lat >= bounds.south && p.lat <= bounds.north
                         && p.lng >= bounds.west && p.lng <= bounds.east);
    }
    function activityStart() {
      var opt = act && act.options[act.selectedIndex];
      if (opt && opt.dataset.lat) { return { lat: +opt.dataset.lat, lng: +opt.dataset.lng }; }
      return CAMPUS_CENTER;
    }
    // The line and the chips for a point, and nothing else: what is said
    // while the pin is still being dragged.
    function say(p) {
      var near = nearest(places, p);
      where.textContent = near ? "Near " + near.name : "Campus meetup point";
      chips.forEach(function (c) {
        c.setAttribute("aria-pressed", near && c.dataset.key === near.key ? "true" : "false");
      });
    }
    // `how`: "start" (the activity's place, not a choice), "chip" (a
    // landmark, so the map goes there too) or "map" (a drag or a tap,
    // already in view).
    function place(p, how) {
      point = { lat: p.lat, lng: p.lng };
      if (how !== "start") { touched = true; }
      lat.value = point.lat.toFixed(6);
      lng.value = point.lng.toFixed(6);
      say(point);
      if (pin) { pin.setLatLng([point.lat, point.lng]); }
      if (map && how !== "map") { map.panTo([point.lat, point.lng], { animate: !reduced }); }
    }

    chips.forEach(function (c) {
      c.addEventListener("click", function () {
        place({ lat: +c.dataset.lat, lng: +c.dataset.lng }, "chip");
      });
    });
    if (act) {
      act.addEventListener("change", function () {
        if (!touched) { place(activityStart(), "start"); }
      });
    }
    // Posted: the enhancer resets the form, which empties the inputs. The
    // next meetup starts afresh from its activity's place.
    form.addEventListener("reset", function () {
      setTimeout(function () { touched = false; place(activityStart(), "start"); }, 0);
    });
    // Bigger map (a narrow drawer): the form becomes the map until Done.
    if (grow) {
      grow.addEventListener("click", function () {
        var on = form.classList.toggle("mapping");
        grow.setAttribute("aria-expanded", on ? "true" : "false");
        grow.setAttribute("aria-label", on ? "Back to the form" : "Bigger map");
        grow.textContent = on ? "Done" : "⤢";
        form.scrollTop = 0;
      });
    }
    place(activityStart(), "start");

    leaflet().then(function () {
      var corner = bounds ? [[bounds.south, bounds.west], [bounds.north, bounds.east]] : null;
      map = L.map(mapEl, {
        minZoom: 15, maxZoom: 19,
        // The form around it scrolls; the keys zoom.
        scrollWheelZoom: false,
        maxBounds: corner || undefined, maxBoundsViscosity: 0.85
      }).setView([point.lat, point.lng], ZOOM);
      mapEl.classList.add("beacon-map");
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                  { attribution: "&copy; OpenStreetMap", maxZoom: 19 }).addTo(map);
      if (corner) {
        L.rectangle(corner, { color: "#005a8b", weight: 2, opacity: 0.6, dashArray: "6 6",
                              fillColor: "#005a8b", fillOpacity: 0.04,
                              interactive: false }).addTo(map);
      }
      places.forEach(function (p) {
        L.marker([p.lat, p.lng], {
          icon: L.divIcon({ className: "approved-place-icon", html: '<span class="pin-landmark"></span>',
                            iconSize: [14, 14], iconAnchor: [7, 7] }),
          keyboard: false
        }).bindTooltip(p.name, { direction: "top", offset: [0, -8] })
          .on("click", function () { place(p, "chip"); })
          .addTo(map);
      });
      // The meetup's pin: a navy draft flag with the Meet up pin on it,
      // the colour an event has on the big map. Dragged, it lifts and the
      // words follow it; dropped, it is placed.
      pin = L.marker([point.lat, point.lng], {
        draggable: true, zIndexOffset: 1000,
        icon: L.divIcon({
          className: "beacon-flag-icon",
          html: '<span class="beacon-flag-pin draft" style="--flag-color:#132245;--flag-ink:#fff">'
              + '<span class="flag-head"><svg class="icon" aria-hidden="true"><use href="#icon-meet"></use></svg></span>'
              + '<span class="flag-pole"></span></span>',
          iconSize: [36, 52], iconAnchor: [18, 52]
        })
      }).addTo(map);
      function flag() { var el = pin.getElement(); return el && el.firstElementChild; }
      pin.on("dragstart", function () { var f = flag(); if (f) { f.classList.add("lifted"); } });
      pin.on("drag", function () { say(pin.getLatLng()); });
      pin.on("dragend", function () {
        var f = flag(); if (f) { f.classList.remove("lifted"); }
        var at = pin.getLatLng();
        // Off the campus: back where it was, as the big map does.
        if (inside(at)) { place(at, "map"); } else { place(point, "map"); }
      });
      map.on("click", function (e) { if (inside(e.latlng)) { place(e.latlng, "map"); } });
      mapEl.classList.add("up");
      // Leaflet measures its box once. Drawn in a folded drawer it had no
      // size, and the box changes size as the drawer grows, so it is
      // measured again whenever its size changes, and kept on the pin.
      function fit() {
        if (!mapEl.clientWidth) { return; }
        map.invalidateSize({ pan: false });
        map.setView([point.lat, point.lng], map.getZoom(), { animate: false });
      }
      if (window.ResizeObserver) { new ResizeObserver(fit).observe(mapEl); } else { fit(); }
    }).catch(function () {
      mapEl.classList.add("down");
    });
  }

  window.BeaconPin = { mount: mount };
})();
