// Login backdrop: little pixel-art students on campus behind the ID card.
// One <canvas data-campus>; plain JS, no images. Characters are drawn from tiny text maps (one letter
// per pixel), so each student gets its own hair, skin, uniform and bag colours. Students higher on the
// screen are farther away: smaller, slower and fainter.
//
// They also interact: two who meet may stop and chat (speech bubbles), or wave and say hi as they pass;
// some walk with a friend, some stand in a small group and then split up, and now and then one runs
// (late for class). Still picture for "reduce motion".
//
// Easter egg: every so often a student near the front stops, turns to face the screen and talks to
// whoever is looking (their eyes follow the mouse). Click a student and they hop and react; keep poking
// and the front-facing kid answers back.
(function () {
  "use strict";
  var canvas = document.querySelector("canvas[data-campus]");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 8 x 13 pixel maps, facing right. H hair, S skin, T top, A arm (sleeve), B bag, P pants or skirt,
  // K shoes, "." empty. Two walking frames plus a standing one; long hair and skirt variants.
  var BODY = [
    "..HHHH..",
    ".HHHHHH.",
    ".HHSSSS.",
    "..SSSS..",
    "...SS...",
    ".BTTTT..",
    "BBTTTTA.",
    "BBTTTTS.",
    ".BTTTT..",
  ];
  var LONG_HAIR = [
    "..HHHH..",
    ".HHHHHH.",
    "HHHSSSS.",
    "HHSSSS..",
    "HH.SS...",
  ];
  var LEGS = {
    pants: [
      ["..PPPP..", "..PP.PP.", ".PP...P.", ".KK...KK"],
      ["..PPPP..", "..PPPP..", "...PP...", "...KKK.."],
      ["..PPPP..", "..PP.P..", "..PP.P..", "..KK.KK."],
    ],
    skirt: [
      [".PPPPPP.", "..S...S.", ".S....S.", ".KK...KK"],
      [".PPPPPP.", "...SS...", "...SS...", "...KKK.."],
      [".PPPPPP.", "..S..S..", "..S..S..", "..KK.KK."],
    ],
  };

  // Facing the viewer (for the fourth-wall kid). Eyes are drawn separately so they can follow the mouse.
  var FRONT = [
    "..HHHH..",
    ".HHHHHH.",
    ".HSSSSH.",
    ".SSSSSS.",
    "..SMMS..",
    "...SS...",
    ".TTTTTT.",
    "STTTTTTS",
    "STTTTTTS",
    ".TTTTTT.",
  ];
  var FRONT_HAIR_LONG = [
    "..HHHH..",
    ".HHHHHH.",
    "HHSSSSHH",
    "HSSSSSSH",
    "H.SMMS.H",
  ];
  var FRONT_LEGS = {
    pants: ["..PPPP..", "..P..P..", "..P..P..", ".KK..KK."],
    skirt: [".PPPPPP.", "..S..S..", "..S..S..", ".KK..KK."],
  };
  // What the fourth-wall kid says (one line, then a second one).
  var LINES = [
    ["Psst.", "Lost your ID too?"],
    ["Hi!", "Yes, you. Hi!"],
    ["Uy!", "Log in na."],
    ["...", "I can see you, you know."],
    ["Hey!", "Don't forget your password."],
    ["Oh!", "Same ID photo as last time?"],
  ];
  var POKED = ["Hey!", "Ouch!", "Hehe", "Bakit?", "Stop that!"];

  // Speech bubble contents, "#" = ink. Picked at random while chatting.
  var ICONS = {
    dots: [".....", "#.#.#"],
    hi: ["#...#", "#....", "###.#", "#.#.#"],
    heart: [".#.#.", "#####", ".###.", "..#.."],
    excl: ["#", "#", ".", "#"],
    ask: ["###", "..#", ".#.", "...", ".#."],
    ha: ["#.#..#.", "###.#.#", "#.#.###", "#.#.#.#"],
  };
  var CHAT = ["dots", "dots", "dots", "ha", "ask", "excl", "heart"];

  // Little scripted conversations; the lines take turns between the students in the chat.
  var DIALOGUES = [
    ["Can I copy your assignment?", "No.", "Libre kita lunch!", "...Fine."],
    ["Did you study for the quiz?", "There's a quiz?!"],
    ["Where's your ID?", "Lost it. Again.", "Quik RE-ID mo na!"],
    ["Tara, canteen?", "G!"],
    ["Prof's absent daw!", "Yesss!"],
    ["Done with the thesis?", "Don't ask."],
    ["Pa-share ng notes?", "Libre mo muna ako. Hehe."],
    ["Group project tayo?", "Ako na naman gagawa?!"],
    ["Ang init!", "Aircon sa library, tara!"],
    ["Nag-review ka?", "Mental review lang.", "Same."],
  ];
  // What runners shout on their way past.
  var RUN_LINES = ["I'm late!", "Gotta go fast!", "Late na ako!", "Wait for meee!", "Excuse me!"];

  var SKIN = ["#f1c7a3", "#d9a37c", "#b97c55", "#8d5a3b", "#e8b48c"];
  var HAIR = ["#1f1a17", "#3b2a20", "#14110f", "#5a3a22", "#2b2b33"];
  // Mostly the white school polo, some navy and some yellow PE shirts.
  var TOPS = ["#ffffff", "#ffffff", "#ffffff", "#f4f6fb", "#1f2d7a", "#2f3f95", "#f5c518"];
  var BOTTOMS = ["#1f2d7a", "#232836", "#2d3a6b", "#3a3f4b"];
  var BAGS = ["#c0392b", "#2e86c1", "#1e8449", "#7d3c98", "#f39c12", "#34495e", "#1f2d7a"];
  var SHOES = ["#111111", "#2b2b2b", "#ffffff"];
  var NAVY = "#1f2d7a";
  var FADE = 0.55;   // students are drawn faint so the card stays in front; speech bubbles are not

  function pick(list) { return list[Math.floor(Math.random() * list.length)]; }
  function rand(a, b) { return a + Math.random() * (b - a); }

  var W = 0, H = 0, people = [], nextId = 1;
  var mouse = { x: -1, y: -1 }, pokes = 0, nextFourth = 25 + Math.random() * 25;
  if (location.hash === "#psst") nextFourth = 2;   // a secret way to call the fourth-wall kid right away

  function scaleFor(depth) { return depth < 0.35 ? 2 : depth < 0.75 ? 3 : 4; }
  function yFor(depth) { return Math.round(H * (0.12 + depth * 0.8)); }

  function newPerson(opts) {
    opts = opts || {};
    var depth = opts.depth != null ? opts.depth : Math.random();   // 0 = far (top), 1 = near (bottom)
    var scale = scaleFor(depth);
    var dir = opts.dir || (Math.random() < 0.5 ? 1 : -1);
    var w = 8 * scale;
    var runner = !opts.calm && Math.random() < 0.07;
    return {
      id: nextId++, depth: depth, scale: scale, dir: dir, walkDir: dir,
      y: yFor(depth),
      x: opts.x != null ? opts.x : (opts.anywhere ? Math.random() * W
         : (dir > 0 ? -w - Math.random() * 200 : W + Math.random() * 200)),
      speed: (runner ? 2.4 : 1) * (14 + Math.random() * 16) * (0.6 + scale * 0.2),   // px per second
      runner: runner,
      girl: Math.random() < 0.5,
      colors: { H: pick(HAIR), S: pick(SKIN), T: pick(TOPS), B: pick(BAGS), P: pick(BOTTOMS), K: pick(SHOES) },
      hasBag: Math.random() < 0.75,
      phase: Math.random(),
      state: "walk",       // walk | idle | chat | wave
      timer: 0,            // seconds left in the current state
      wave: 0,             // seconds left with a raised hand (while walking or chatting)
      bubble: null, bubbleT: 0,
      partners: [], friend: null,
      met: {},             // ids already met, so a pair only decides once
      text: null,          // a written speech bubble (fourth-wall kid, pokes) instead of an icon
      hop: 0,              // seconds left in a little jump after being clicked
      alpha: 0.45 + depth * 0.55,
    };
  }

  // A friend walking beside someone: same direction and pace, a step behind.
  function friendOf(p) {
    var f = newPerson({ depth: Math.min(1, p.depth + 0.02), dir: p.dir, calm: true,
                        x: p.x - p.dir * 10 * p.scale });
    f.speed = p.speed; f.scale = p.scale; f.y = p.y + p.scale; f.runner = false;
    p.friend = f; f.friend = p;
    p.met[f.id] = f.met[p.id] = true;
    return f;
  }

  // Two or three students standing together talking, who then go their own ways.
  function group(x, depth) {
    var size = Math.random() < 0.5 ? 2 : 3, members = [], scale = scaleFor(depth);
    for (var i = 0; i < size; i++) {
      var m = newPerson({ depth: depth, calm: true, x: x + i * 10 * scale });
      m.dir = i === 0 ? 1 : -1;                 // first one faces the others
      if (size === 3 && i === 1) m.dir = Math.random() < 0.5 ? 1 : -1;
      m.state = "chat"; m.timer = rand(6, 14);
      members.push(m);
    }
    members.forEach(function (m) {
      m.partners = members.filter(function (o) { return o !== m; });
      members.forEach(function (o) { m.met[o.id] = true; });
    });
    if (Math.random() < 0.6) converse(members, pick(DIALOGUES));
    return members;
  }

  function resize() {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
    var count = Math.max(18, Math.min(46, Math.round(W * H / 26000)));
    people = [];
    while (people.length < count) {
      var r = Math.random();
      if (r < 0.12) people = people.concat(group(rand(0, W - 60), Math.random()));
      else if (r < 0.25) { var p = newPerson({ anywhere: true, calm: true }); people.push(p, friendOf(p)); }
      else people.push(newPerson({ anywhere: true }));
    }
  }

  function say(p, icon, seconds) { p.bubble = icon; p.bubbleT = seconds; }

  function lineTime(line) { return 1.3 + line.length * 0.05; }

  // Start a scripted conversation; returns how long it takes.
  function converse(members, lines) {
    var total = 0.4;
    lines.forEach(function (l) { total += lineTime(l) + 0.3; });
    members.forEach(function (m) { m.scripted = true; m.timer = Math.max(m.timer, total + 1.2); });
    members[0].script = { lines: lines, i: 0, next: 0.4, members: members };
    return total;
  }

  function startChat(a, b, seconds) {
    [a, b].forEach(function (p) { p.state = "chat"; p.timer = seconds; p.scripted = false; });
    a.partners = [b]; b.partners = [a];
    a.dir = b.x > a.x ? 1 : -1; b.dir = -a.dir;  // face each other
    if (Math.random() < 0.7) converse([a, b], pick(DIALOGUES));
    else say(a, Math.random() < 0.5 ? "hi" : "dots", 1.4);
  }

  // When two students meet (same distance from us, walking towards each other), decide once what they do.
  function meet(a, b) {
    if (a.met[b.id] || a.state !== "walk" || b.state !== "walk" || a.runner || b.runner) return;
    if (a.scale !== b.scale || Math.abs(a.y - b.y) > 3 * a.scale || a.walkDir === b.walkDir) return;
    var gap = (b.x - a.x) * a.walkDir;           // > 0 when b is ahead of a
    if (gap <= 0 || gap > 13 * a.scale) return;
    a.met[b.id] = b.met[a.id] = true;
    var roll = Math.random();
    if (roll < 0.5 && !a.friend && !b.friend) startChat(a, b, rand(3, 7));
    else if (roll < 0.8) {
      a.wave = b.wave = 1.1;
      say(a, "hi", 1.1);
      if (Math.random() < 0.6) say(b, "hi", 1.1);
    }
  }

  function rowsFor(p, frame) {
    var rows = (p.girl ? LONG_HAIR : BODY.slice(0, 5)).concat(BODY.slice(5));
    rows = rows.concat(LEGS[p.girl ? "skirt" : "pants"][frame]);
    if (p.wave > 0 && Math.floor(p.wave * 6) % 2 === 0) {    // hand up beside the head, flapping
      rows = rows.slice();
      rows[3] = rows[3].slice(0, 7) + "S";
      rows[4] = rows[4].slice(0, 6) + "AS";
      rows[6] = rows[6].slice(0, 6) + "..";
      rows[7] = rows[7].slice(0, 6) + "..";
    }
    return rows;
  }

  function frontRows(p) {
    var rows = (p.girl ? FRONT_HAIR_LONG : FRONT.slice(0, 5)).concat(FRONT.slice(5));
    rows = rows.concat(FRONT_LEGS[p.girl ? "skirt" : "pants"]);
    if (p.wave > 0 && Math.floor(p.wave * 6) % 2 === 0) {    // waving at you
      rows = rows.slice();
      rows[4] = rows[4].slice(0, 7) + "S";
      rows[5] = rows[5].slice(0, 7) + "S";
      rows[6] = rows[6].slice(0, 7) + "T";
      rows[7] = rows[7].slice(0, 7) + ".";
      rows[8] = rows[8].slice(0, 7) + ".";
    }
    return rows;
  }

  function hopOffset(p) { return p.hop > 0 ? Math.round(Math.sin((1 - p.hop / 0.45) * Math.PI) * 4 * p.scale) : 0; }

  // Each pose (walk frames, standing, waving, facing the viewer; left or right) is drawn pixel by pixel
  // once into a tiny canvas and then copied with one drawImage per frame: far cheaper than ~80 fillRects.
  function sprite(p, rows, front) {
    var up = p.wave > 0 && Math.floor(p.wave * 6) % 2 === 0;
    var key = rows.length + (front ? "f" : p.dir > 0 ? "r" : "l") + rows[rows.length - 1] + rows[rows.length - 3] + (up ? "w" : "");
    var cache = p.sprites || (p.sprites = {});
    if (cache[key]) return cache[key];
    var s = p.scale, c = p.colors, img = document.createElement("canvas");
    img.width = 8 * s; img.height = rows.length * s;
    var g = img.getContext("2d");
    c.M = "#b0554a";
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r];
      for (var i = 0; i < 8; i++) {
        var ch = row.charAt(p.dir > 0 || front ? i : 7 - i);
        if (ch === "." || (ch === "B" && !p.hasBag)) continue;
        g.fillStyle = ch === "A" ? c.T : c[ch];
        g.fillRect(i * s, r * s, s, s);
      }
    }
    return (cache[key] = img);
  }

  function drawPerson(p, frame) {
    var front = p.state === "fourth" && p.facing;
    var rows = front ? frontRows(p) : rowsFor(p, frame);
    var s = p.scale, x0 = Math.round(p.x), y0 = p.y - rows.length * s - hopOffset(p);
    ctx.globalAlpha = p.alpha * FADE;
    ctx.fillStyle = "rgba(31, 45, 122, .18)";          // soft shadow on the ground
    ctx.fillRect(x0 + s, p.y, 6 * s, s);
    ctx.drawImage(sprite(p, rows, front), x0, y0);
    if (front) {                                        // eyes follow the mouse
      var look = mouse.x < 0 ? 0 : Math.max(-1, Math.min(1, Math.round((mouse.x - (x0 + 4 * s)) / 120)));
      ctx.fillStyle = "#1a1a1a";
      ctx.fillRect(x0 + (2 + Math.max(0, look)) * s, y0 + 3 * s, s, s);
      ctx.fillRect(x0 + (5 + Math.min(0, look)) * s, y0 + 3 * s, s, s);
    }
    if (p.text) drawText(p, x0, y0);
    else if (p.bubble) drawBubble(p, x0, y0);
  }

  function drawText(p, x0, y0) {
    var s = p.scale, size = 6 + 2 * s;
    ctx.font = "700 " + size + "px Inter, Arial, sans-serif";
    if (p.textW == null || p.textFor !== p.text) { p.textW = Math.ceil(ctx.measureText(p.text).width); p.textFor = p.text; }
    var tw = p.textW, pad = 6;
    var bw = tw + pad * 2, bh = size + pad * 2;
    var bx = Math.round(Math.max(4, Math.min(W - bw - 4, x0 + 4 * s - bw / 2))), by = y0 - bh - 8;
    ctx.globalAlpha = p.scale >= 3 ? 0.95 : 0.8;        // words stay readable
    ctx.fillStyle = NAVY;
    ctx.fillRect(bx + 2, by, bw - 4, bh); ctx.fillRect(bx, by + 2, bw, bh - 4);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(bx + 2, by + 2, bw - 4, bh - 4);
    ctx.fillStyle = NAVY;
    ctx.fillRect(x0 + 4 * s - 2, by + bh, 4, 4); ctx.fillRect(x0 + 4 * s - 1, by + bh + 4, 2, 2);
    ctx.fillText(p.text, bx + pad, by + pad + size * 0.85);
  }

  function drawBubble(p, x0, y0) {
    var map = ICONS[p.bubble], s = Math.max(2, p.scale - 1);
    if (p.bubble === "dots") {                          // the dots appear one by one, like typing
      var n = 1 + Math.floor((Date.now() / 300) % 3);
      map = [".....", ["#....", "#.#..", "#.#.#"][n - 1]];
    }
    var cw = map[0].length, chh = map.length;
    var bw = (cw + 4) * s, bh = (chh + 4) * s;
    var bx = x0 + (p.dir > 0 ? 5 * p.scale : 3 * p.scale - bw);
    var by = y0 - bh - 2 * s;
    ctx.globalAlpha = Math.min(1, p.alpha * FADE + 0.3);
    ctx.fillStyle = NAVY;                               // outline with clipped corners, then white inside
    ctx.fillRect(bx + s, by, bw - 2 * s, bh);
    ctx.fillRect(bx, by + s, bw, bh - 2 * s);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(bx + s, by + s, bw - 2 * s, bh - 2 * s);
    ctx.fillStyle = NAVY;                               // little tail towards the speaker
    var tx = p.dir > 0 ? bx + s : bx + bw - 2 * s;
    ctx.fillRect(tx, by + bh, s, s);
    ctx.fillStyle = p.bubble === "heart" ? "#e0485c" : NAVY;
    for (var r = 0; r < chh; r++) {
      for (var i = 0; i < cw; i++) {
        if (map[r].charAt(i) === "#") ctx.fillRect(bx + (i + 2) * s, by + (r + 2) * s, s, s);
      }
    }
  }

  function update(p, dt) {
    if (p.bubbleT > 0) { p.bubbleT -= dt; if (p.bubbleT <= 0) { p.bubble = null; p.text = null; } }
    if (p.wave > 0) p.wave -= dt;
    if (p.hop > 0) p.hop -= dt;

    var sc = p.script;
    if (sc) {                                           // the next line of the conversation
      sc.next -= dt;
      if (sc.next <= 0) {
        if (sc.i < sc.lines.length) {
          var who = sc.members[sc.i % sc.members.length], line = sc.lines[sc.i];
          if (who.state !== "fourth") { who.bubble = null; who.text = line; who.bubbleT = lineTime(line); }
          sc.next = lineTime(line) + 0.3;
          sc.i += 1;
        } else {                                        // done: back to small talk
          sc.members.forEach(function (m) { m.scripted = false; });
          p.script = null;
        }
      }
    }

    if (p.state === "fourth") {                         // the easter egg, in steps
      p.timer += dt;
      var t = p.timer;
      if (t < 0.9) { p.facing = false; if (!p.bubble && !p.text) say(p, "excl", 0.9); }
      else if (t < 1.1) { p.facing = true; }
      else if (t < 3.4) { if (!p.text && !p.said1) { p.said1 = true; p.text = p.lines[0]; p.bubbleT = 2.2; p.wave = 1.4; } }
      else if (t < 7.4) { if (!p.text && !p.said2) { p.said2 = true; p.text = p.lines[1]; p.bubbleT = 3.8; } }
      else if (t < 8.6) { if (p.wave <= 0 && !p.waved) { p.waved = true; p.wave = 1.2; } }
      else { p.state = "walk"; p.facing = false; p.text = null; }
      return 2;
    }
    if (p.state === "chat") {
      p.timer -= dt;
      // Without a script, take turns with little icons: now and then someone says something or gestures.
      if (!p.scripted && !p.bubble && !p.text && Math.random() < dt * 0.6) say(p, pick(CHAT), rand(1, 2));
      if (Math.random() < dt * 0.08) p.wave = 0.8;
      if (p.timer <= 0) {                               // goodbye: wave and walk off
        p.state = "walk"; p.partners = []; p.script = null; p.scripted = false;
        p.dir = p.walkDir = Math.random() < 0.5 ? 1 : -1;
        p.wave = 0.8;
      }
      return 2;
    }
    if (p.state === "idle") {                           // standing for a moment (checking a phone)
      p.timer -= dt;
      if (p.timer <= 0) p.state = "walk";
      return 2;
    }
    if (p.friend && p.friend.state === "chat") { p.state = "idle"; p.timer = 0.5; return 2; }
    p.dir = p.walkDir;
    p.x += p.dir * p.speed * dt;
    p.phase += dt * p.speed / ((p.runner ? 4 : 6) * p.scale);
    if (p.friend && !p.scripted && !p.friend.scripted && Math.random() < dt * 0.06) converse([p, p.friend], pick(DIALOGUES));
    else if (p.friend && !p.scripted && !p.bubble && !p.text && Math.random() < dt * 0.25) say(p, pick(CHAT), rand(1, 2));
    if (p.runner && !p.bubble && !p.text && Math.random() < dt * 0.45) { p.text = pick(RUN_LINES); p.bubbleT = 1.3; }
    if (!p.friend && !p.runner && Math.random() < dt * 0.03) { p.state = "idle"; p.timer = rand(1, 3); }
    return Math.floor(p.phase) % 2;
  }

  // Pick a walking student near the front, away from the card in the middle, to look at the viewer.
  function breakFourthWall(lines) {
    var candidates = people.filter(function (p) {
      return p.state === "walk" && p.scale >= 3 && !p.runner && !p.friend
        && p.x > 30 && p.x < W - 60 && Math.abs(p.x - W / 2) > Math.min(260, W * 0.3);
    });
    if (!candidates.length) return null;
    var p = pick(candidates);
    p.state = "fourth"; p.timer = 0; p.facing = false; p.bubble = null; p.text = null;
    p.said1 = p.said2 = p.waved = false;
    p.lines = lines || pick(LINES);
    return p;
  }

  // Clicking a student (anywhere that isn't the card or a control) makes them hop and react.
  function poke(x, y) {
    for (var i = people.length - 1; i >= 0; i--) {      // nearest (drawn last) first
      var p = people[i], s = p.scale;
      if (x >= p.x - s && x <= p.x + 9 * s && y >= p.y - 15 * s && y <= p.y + s) {
        p.hop = 0.45;
        pokes += 1;
        if (p.state === "fourth") { p.text = "Hey, no poking!"; p.bubbleT = 1.6; return; }
        p.text = pick(POKED); p.bubbleT = 1.2;
        if (p.state === "walk" && Math.random() < 0.5) p.walkDir = -p.walkDir;   // startled, turns around
        if (pokes % 6 === 0) breakFourthWall(["Okay okay...", "We'll stop slacking. Log in na!"]);
        return;
      }
    }
  }

  window.addEventListener("mousemove", function (e) { mouse.x = e.clientX; mouse.y = e.clientY; });
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-lanyard-card], .dll-card, .id-preview, a, button, input, label, select, textarea, header")) return;
    poke(e.clientX, e.clientY);
  });

  var last = 0, meetClock = 0, needSort = true;
  var FRAME = 1000 / 30;   // pixel art doesn't need 60 frames a second; half the work
  function tick(now) {
    if (!still) requestAnimationFrame(tick);
    if (last && now - last < FRAME - 1) return;
    var dt = last ? Math.min(0.08, (now - last) / 1000) : 0;
    last = now;
    ctx.clearRect(0, 0, W, H);

    meetClock -= dt;
    if (meetClock <= 0) {                               // who's meeting whom: 5 times a second is plenty
      meetClock = 0.2;
      for (var a = 0; a < people.length; a++) {
        for (var b = a + 1; b < people.length; b++) meet(people[a], people[b]);
      }
    }
    nextFourth -= dt;
    if (nextFourth <= 0) { breakFourthWall(); nextFourth = 25 + Math.random() * 25; }
    if (needSort) { people.sort(function (p, q) { return p.y - q.y; }); needSort = false; }   // farther ones behind
    for (var i = 0; i < people.length; i++) {
      var p = people[i], frame = update(p, dt), w = 8 * p.scale;
      if ((p.x > W + w * 2 && p.dir > 0) || (p.x < -w * 3 && p.dir < 0)) {
        // Walked off the screen: someone new comes in (sometimes with a friend).
        if (p.friend) p.friend.friend = null;
        people[i] = p = newPerson({});
        if (Math.random() < 0.15 && people.length < 60) people.push(friendOf(p));
        needSort = true;
      }
      drawPerson(p, frame);
    }
    ctx.globalAlpha = 1;
  }

  resize();
  // On phones the address bar hides and shows while scrolling, which resizes the window's height only. Then keep
  // everyone walking and just move each student to their depth's place; only a width change starts over.
  window.addEventListener("resize", function () {
    if (window.innerWidth === W) {
      H = canvas.height = window.innerHeight;
      people.forEach(function (p) { p.y = yFor(p.depth); });
      if (still) tick(0);
      return;
    }
    resize(); needSort = true; last = 0; if (still) tick(0);
  });
  requestAnimationFrame(tick);
})();
