// The login card hangs on a stretchy lanyard (login page). Plain JS, no libraries.
// Like a real ID lanyard, two straps come down from the top in a V and meet at the clip.
// Each strap is a chain of points (Verlet integration) with soft constraints, so it stretches
// and springs back. Both chains share their last point (the clip). The card is one heavier
// point hanging from the clip. Drag the card by its surface; inputs, buttons and links work as usual.
(function () {
  "use strict";
  var stage = document.querySelector("[data-lanyard]");
  if (!stage) return;
  var svg = stage.querySelector("svg");
  var clip = svg.querySelector(".strap-clip");
  var card = stage.querySelector("[data-lanyard-card]");
  // data-lanyard-fit (login): size the stage to the window. Otherwise the stage's CSS height is used.
  var fitWindow = stage.hasAttribute("data-lanyard-fit");
  var SVGNS = "http://www.w3.org/2000/svg";

  var SEGMENTS = 14;
  var GRAVITY = 0.55;
  var DAMPING = 0.985;        // how long it keeps swinging (closer to 1 = longer)
  var CALM_DAMPING = 0.88;    // while someone is typing in the form
  var STRAP_STIFFNESS = 0.5;  // < 1 = stretchy straps
  var ITERATIONS = 8;
  var INTERACTIVE = "input, button, a, label, select, textarea";   // never start a drag on these

  // Print on the straps, repeating like a school lanyard: DLL, elephant, DLL REDEFINES.
  var PATTERN = [
    { text: "DLL", cls: "deco-main", len: 30 },
    { elephant: true, len: 24 },
    { text: "DLL REDEFINES", cls: "deco-sub", len: 62 }
  ];

  function makeStrap(pathEl, decoEl) {
    var items = [];
    for (var r = 0; r < 6; r++) {
      PATTERN.forEach(function (p) {
        var el;
        if (p.elephant) {
          el = document.createElementNS(SVGNS, "use");
          el.setAttribute("href", "#elephant");
          el.setAttribute("x", "-11"); el.setAttribute("y", "-7.5");
          el.setAttribute("width", "22"); el.setAttribute("height", "15");
          el.setAttribute("class", "deco-elephant");
        } else {
          el = document.createElementNS(SVGNS, "text");
          el.textContent = p.text;
          el.setAttribute("class", p.cls);
          el.setAttribute("text-anchor", "middle");
          el.setAttribute("dominant-baseline", "central");
        }
        decoEl.appendChild(el);
        items.push({ el: el, len: p.len });
      });
    }
    return { path: pathEl, items: items, pts: [], anchor: null };
  }

  // The template has one strap path and one print group; clone them for the second strap.
  var path1 = svg.querySelector(".strap");
  var deco1 = svg.querySelector(".strap-deco");
  var path2 = path1.cloneNode(false); path2.removeAttribute("id");
  var deco2 = deco1.cloneNode(false);
  svg.insertBefore(path2, path1);
  svg.insertBefore(deco2, path1);
  var straps = [makeStrap(path2, deco2), makeStrap(path1, deco1)]; // [left, right]

  var W, H, segLen, spread, cardW, cardH, rawW, rawH, scale = 1, hang;
  var knot = null;   // where both straps meet (the clip)
  var body = null;   // the card's centre
  var moving = [];   // every simulated point except the fixed top ends
  var drag = null;
  var pointer = { x: 0, y: 0 };
  var typing = false;

  // Fit the straps and the card inside the visible window: shorten the straps first,
  // then shrink the card a little on very short screens.
  function measure() {
    rawW = card.offsetWidth;    // layout size, unaffected by the transform
    rawH = card.offsetHeight;
    // GAP leaves room under the card: the straps stretch a little under the card's weight.
    var MIN_DROP = 40, GAP = 56;
    var small = window.innerWidth < 600;
    var maxDrop = small ? 90 : 170;
    var drop;
    if (fitWindow) {
      var top = stage.getBoundingClientRect().top + window.scrollY;
      var avail = window.innerHeight - top - 16;
      scale = Math.max(0.7, Math.min(1, (avail - MIN_DROP - GAP) / rawH));
      cardW = rawW * scale;
      cardH = rawH * scale;
      drop = Math.max(MIN_DROP, Math.min(maxDrop, avail - cardH - GAP));
      stage.style.height = Math.round(drop + cardH + GAP) + "px";
    } else {
      scale = 1;
      cardW = rawW;
      cardH = rawH;
      drop = Math.max(MIN_DROP, Math.min(maxDrop, stage.offsetHeight - cardH - GAP));
    }
    var r = stage.getBoundingClientRect();
    W = r.width; H = r.height;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    spread = small ? 36 : Math.min(70, cardW * 0.2);   // half the gap between the two top ends
    straps[0].anchor = { x: W / 2 - spread, y: -4 };
    straps[1].anchor = { x: W / 2 + spread, y: -4 };
    segLen = Math.sqrt(drop * drop + spread * spread) / SEGMENTS;
    hang = cardH / 2 + 8 * scale; // clip to card centre
  }

  function point(x, y) { return { x: x, y: y, px: x, py: y }; }

  function reset() {
    measure();
    // Start a little to one side so the card drops in with a short swing.
    var total = segLen * SEGMENTS;
    knot = point(W / 2 + total * 0.55, total * 0.75);
    moving = [knot];
    straps.forEach(function (s) {
      s.pts = [];
      for (var i = 0; i <= SEGMENTS; i++) {
        if (i === SEGMENTS) { s.pts.push(knot); break; }
        var t = i / SEGMENTS;
        var p = point(s.anchor.x + (knot.x - s.anchor.x) * t, s.anchor.y + (knot.y - s.anchor.y) * t);
        s.pts.push(p);
        if (i > 0) moving.push(p);
      }
    });
    body = point(knot.x + hang * 0.35, knot.y + hang * 0.95);
  }

  function integrate(p, gravity, damping) {
    var vx = (p.x - p.px) * damping;
    var vy = (p.y - p.py) * damping;
    p.px = p.x; p.py = p.y;
    p.x += vx; p.y += vy + gravity;
  }

  function constrain(a, b, length, stiffness, shareA) {
    var dx = b.x - a.x, dy = b.y - a.y;
    var dist = Math.sqrt(dx * dx + dy * dy) || 0.0001;
    var diff = (dist - length) / dist * stiffness;
    a.x += dx * diff * shareA; a.y += dy * diff * shareA;
    b.x -= dx * diff * (1 - shareA); b.y -= dy * diff * (1 - shareA);
  }

  function pinAnchors() {
    straps.forEach(function (s) { s.pts[0].x = s.anchor.x; s.pts[0].y = s.anchor.y; });
  }

  function rot(x, y, th) {
    var c = Math.cos(th), s = Math.sin(th);
    return { x: x * c - y * s, y: x * s + y * c };
  }

  function constrainStraps() {
    pinAnchors();
    straps.forEach(function (s) {
      for (var j = 0; j < SEGMENTS; j++) {
        constrain(s.pts[j], s.pts[j + 1], segLen, STRAP_STIFFNESS, j === 0 ? 0 : 0.5);
      }
    });
  }

  function step() {
    var damping = typing && !drag ? CALM_DAMPING : DAMPING;
    for (var i = 0; i < moving.length; i++) integrate(moving[i], GRAVITY, damping);

    if (drag) {
      // The card is rigid and pinned at the grabbed spot (under the pointer). The straps pull
      // the clip; the card turns around the grabbed spot so its top follows the clip.
      var g = pointer, th = 0;
      for (var k = 0; k < ITERATIONS; k++) {
        constrainStraps();
        th = Math.atan2(knot.y - g.y, knot.x - g.x) - drag.clipAngle;
        var kOff = rot(drag.clip.x, drag.clip.y, th);
        knot.x = g.x + kOff.x; knot.y = g.y + kOff.y;
      }
      var bOff = rot(drag.lx, drag.ly, th);
      body.px = body.x; body.py = body.y;   // keeps the throw speed for when it's released
      body.x = g.x - bOff.x; body.y = g.y - bOff.y;
    } else {
      integrate(body, GRAVITY, damping);
      for (var n = 0; n < ITERATIONS; n++) {
        constrainStraps();
        constrain(knot, body, hang, 1, 0.8);
      }
    }
    pinAnchors();
    if (!drag) {
      // Keep most of the card on screen.
      var slackX = cardW * 0.35;
      body.x = Math.max(cardW / 2 - slackX, Math.min(W - cardW / 2 + slackX, body.x));
      body.y = Math.max(cardH / 2, Math.min(H - cardH / 2 + 24, body.y));
    }
  }

  // The card's rotation in radians (0 = hanging straight down), same sense as CSS rotate().
  function cardAngle() {
    return -Math.atan2(body.x - knot.x, body.y - knot.y);
  }

  function strapPath(pts) {
    var d = "M " + pts[0].x.toFixed(1) + " " + pts[0].y.toFixed(1);
    for (var i = 1; i < SEGMENTS; i++) {
      var mx = (pts[i].x + pts[i + 1].x) / 2, my = (pts[i].y + pts[i + 1].y) / 2;
      d += " Q " + pts[i].x.toFixed(1) + " " + pts[i].y.toFixed(1) + " " + mx.toFixed(1) + " " + my.toFixed(1);
    }
    return d + " L " + pts[SEGMENTS].x.toFixed(1) + " " + pts[SEGMENTS].y.toFixed(1);
  }

  // Place the print along a strap: each item sits at its distance along the strap,
  // turned to follow the strap's direction there.
  function renderPrint(s) {
    var pts = s.pts, cum = [0];
    for (var i = 1; i <= SEGMENTS; i++) {
      var dx = pts[i].x - pts[i - 1].x, dy = pts[i].y - pts[i - 1].y;
      cum.push(cum[i - 1] + Math.sqrt(dx * dx + dy * dy));
    }
    var total = cum[SEGMENTS];
    var pos = 10, seg = 1;
    for (var k = 0; k < s.items.length; k++) {
      var it = s.items[k];
      var mid = pos + it.len / 2;
      if (pos + it.len > total - 14) { it.el.style.display = "none"; pos += it.len + 8; continue; }
      while (seg < SEGMENTS && cum[seg] < mid) seg++;
      var a = pts[seg - 1], b = pts[seg];
      var t = (mid - cum[seg - 1]) / ((cum[seg] - cum[seg - 1]) || 1);
      var x = a.x + (b.x - a.x) * t, y = a.y + (b.y - a.y) * t;
      var ang = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
      it.el.style.display = "";
      it.el.setAttribute("transform", "translate(" + x.toFixed(1) + " " + y.toFixed(1) + ") rotate(" + ang.toFixed(1) + ")");
      pos += it.len + 8;
    }
  }

  function render() {
    straps.forEach(function (s) {
      s.path.setAttribute("d", strapPath(s.pts));
      renderPrint(s);
    });
    var dx = body.x - knot.x, dy = body.y - knot.y;
    var deg = -Math.atan2(dx, dy) * 180 / Math.PI; // 0 = hanging straight down
    clip.setAttribute("transform", "translate(" + knot.x.toFixed(1) + " " + knot.y.toFixed(1) + ") rotate(" + deg.toFixed(2) + ")");
    // The card box keeps its layout size; scale() shrinks it around its centre.
    card.style.transform = "translate(" + (body.x - rawW / 2).toFixed(1) + "px," + (body.y - rawH / 2).toFixed(1) + "px) rotate(" + deg.toFixed(2) + "deg) scale(" + scale.toFixed(3) + ")";
  }

  function frame() {
    step();
    render();
    requestAnimationFrame(frame);
  }

  function local(e) {
    var r = stage.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  card.addEventListener("pointerdown", function (e) {
    if (e.target.closest(INTERACTIVE)) return; // fields, button and link work normally
    var p = local(e);
    pointer = p;
    // Remember the grabbed spot in the card's own (unrotated) frame, relative to its centre,
    // and where the clip sits relative to that spot.
    var l = rot(p.x - body.x, p.y - body.y, -cardAngle());
    var clipLocal = { x: -l.x, y: -hang - l.y };   // clip is straight above the centre in card space
    drag = { lx: l.x, ly: l.y, clip: clipLocal, clipAngle: Math.atan2(clipLocal.y, clipLocal.x) };
    card.setPointerCapture(e.pointerId);
    card.classList.add("grabbing");
    e.preventDefault();
  });
  card.addEventListener("pointermove", function (e) {
    if (drag) pointer = local(e);
  });
  function release() {
    drag = null;
    card.classList.remove("grabbing");
  }
  card.addEventListener("pointerup", release);
  card.addEventListener("pointercancel", release);

  // Settle the card while someone is typing so the fields hold still.
  card.addEventListener("focusin", function () { typing = true; });
  card.addEventListener("focusout", function () { typing = false; });

  window.addEventListener("resize", function () {
    var oldCentre = W / 2;
    measure();
    var shift = W / 2 - oldCentre;
    moving.forEach(function (p) { p.x += shift; p.px += shift; });
    body.x += shift; body.px += shift;
  });

  // "ready" switches off the no-JavaScript fallback layout, so measure after adding it.
  stage.classList.add("ready");
  reset();
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    for (var n = 0; n < 600; n++) step(); // start at rest; dragging still works
  }
  render();
  frame();
})();
