// Husky character art for 小奇, rendered to frames by gen_skins.py (headless Chrome).
// Each frame is described by a small spec; drawA() / drawC() turn a spec into SVG.
// The pixel skin is derived from drawA() by gen_skins.py (downsample + palette).

// Frame order is the on-device frame index; keep in sync with include/skins.h.
const FRAMES = {
  idle:           { eyes: "normal",   mouth: "smile",  ears: "normal" },
  blink:          { eyes: "closed",   mouth: "smile",  ears: "normal" },
  sleepy:         { eyes: "closed",   mouth: "yawn",   ears: "down",   extra: "zzz" },
  listen:         { eyes: "big",      mouth: "smile",  ears: "up", tilt: -10, extra: "wave1" },
  listen_b:       { eyes: "big",      mouth: "smile",  ears: "up", tilt: -10, extra: "wave2" },
  think:          { eyes: "up",       mouth: "flat",   ears: "normal", tilt: 6, extra: "q1" },
  think_b:        { eyes: "up",       mouth: "flat",   ears: "normal", tilt: 6, extra: "q2" },
  happy:          { eyes: "happy",    mouth: "smile",  ears: "up",     extra: "heart" },
  happy_talk:     { eyes: "happy",    mouth: "tongue", ears: "up",     extra: "heart" },
  surprised:      { eyes: "surprised",mouth: "o",      ears: "up",     extra: "spark" },
  surprised_talk: { eyes: "surprised",mouth: "O",      ears: "up",     extra: "spark" },
  sad:            { eyes: "sad",      mouth: "frown",  ears: "down",   extra: "tear" },
  sad_talk:       { eyes: "sad",      mouth: "open_s", ears: "down",   extra: "tear" },
  shy:            { eyes: "down",     mouth: "smile",  ears: "normal", tilt: 8, blush: 2, extra: "hearts" },
  shy_talk:       { eyes: "down",     mouth: "open_s", ears: "normal", tilt: 8, blush: 2, extra: "hearts" },
  smug:           { eyes: "wink",     mouth: "tongue_side", ears: "normal", tilt: -5, extra: "spark" },
  smug_talk:      { eyes: "wink",     mouth: "open",   ears: "normal", tilt: -5, extra: "spark" },
  calm:           { eyes: "normal",   mouth: "smile",  ears: "normal" },
  calm_talk:      { eyes: "normal",   mouth: "open",   ears: "normal" },
};

function earPaths(ears) {
  if (ears === "down") return ["M38 92 L18 48 L74 70 Z", "M42 86 L28 58 L66 72 Z"];
  if (ears === "up") return ["M42 82 L50 8 L88 58 Z", "M50 76 L54 26 L78 60 Z"];
  return ["M40 84 L52 22 L86 62 Z", "M50 78 L56 36 L76 62 Z"];
}

function extras(kind, theme) {
  const c = theme === "C"
    ? { y: "#ffd60a", g: "#05ffa1", p: "#ff2a6d", b: "#7c9cff", w: "#00f0ff" }
    : { y: "#ffd60a", g: "#05ffa1", p: "#ff7bac", b: "#7cc4ff", w: "#cfe8ff" };
  switch (kind) {
    case "wave1": return `<path d="M168 72 q9 10 0 20" stroke="${c.g}" stroke-width="5" fill="none" stroke-linecap="round"/>`;
    case "wave2": return `<path d="M168 72 q9 10 0 20 M178 62 q17 20 0 40" stroke="${c.g}" stroke-width="5" fill="none" stroke-linecap="round"/>`;
    case "q1": return `<circle cx="158" cy="44" r="5" fill="${c.y}"/><text x="166" y="40" font-size="30" font-weight="800" fill="${c.y}" font-family="Arial">?</text>`;
    case "q2": return `<circle cx="150" cy="52" r="4" fill="${c.y}"/><circle cx="162" cy="40" r="6" fill="${c.y}"/><text x="170" y="34" font-size="34" font-weight="800" fill="${c.y}" font-family="Arial">?</text>`;
    case "heart": return `<text x="150" y="46" font-size="30" fill="${c.p}">♥</text>`;
    case "hearts": return `<text x="152" y="44" font-size="26" fill="${c.p}">♥</text><text x="22" y="58" font-size="18" fill="${c.p}">♥</text>`;
    case "spark": return `<path d="M164 24 l4 12 l12 4 l-12 4 l-4 12 l-4 -12 l-12 -4 l12 -4 Z" fill="${c.y}"/>`;
    case "tear": return `<path d="M58 120 q-6 12 0 17 q6 -5 0 -17" fill="${c.b}"/>`;
    case "zzz": return `<text x="146" y="50" font-size="22" font-weight="800" fill="${c.w}" font-family="Arial">z</text><text x="164" y="32" font-size="28" font-weight="800" fill="${c.w}" font-family="Arial">Z</text>`;
    default: return "";
  }
}

// ---------------------------------------------------------------- A: chibi
function eyesA(kind) {
  const stroke = (d) => `<path d="${d}" stroke="#1d2433" stroke-width="5" fill="none" stroke-linecap="round"/>`;
  const round = (cx, r, pr, dx, dy) =>
    `<circle cx="${cx}" cy="104" r="${r}" fill="#fff"/><circle cx="${cx + dx}" cy="${104 + dy}" r="${r - 3}" fill="#4fa3ff"/>` +
    `<circle cx="${cx + dx}" cy="${104 + dy}" r="${pr}" fill="#14213d"/><circle cx="${cx + dx - 4}" cy="${104 + dy - 5}" r="4" fill="#fff"/>` +
    `<circle cx="${cx + dx + 4}" cy="${104 + dy + 4}" r="2" fill="#fff"/>`;
  switch (kind) {
    case "closed": return stroke("M56 106 Q70 114 84 106") + stroke("M116 106 Q130 114 144 106");
    case "happy": return stroke("M56 108 Q70 92 84 108") + stroke("M116 108 Q130 92 144 108");
    case "big": return round(70, 16, 8, 0, 0) + round(130, 16, 8, 0, 0);
    case "surprised": return round(70, 17, 5, 0, 0) + round(130, 17, 5, 0, 0);
    case "up": return round(70, 15, 8, -4, -5) + round(130, 15, 8, -4, -5);
    case "down": return round(70, 15, 8, 0, 4) + round(130, 15, 8, 0, 4);
    case "sad":
      return round(70, 15, 8, 0, 3) + round(130, 15, 8, 0, 3) +
        `<path d="M52 86 L82 94" stroke="#3d4656" stroke-width="4" stroke-linecap="round"/><path d="M148 86 L118 94" stroke="#3d4656" stroke-width="4" stroke-linecap="round"/>`;
    case "wink": return round(70, 15, 8, 0, 0) + stroke("M116 108 Q130 92 144 108");
    default: return round(70, 15, 8, 0, 0) + round(130, 15, 8, 0, 0);
  }
}

function mouthA(kind) {
  const line = (d) => `<path d="${d}" stroke="#1d2433" stroke-width="4" fill="none" stroke-linecap="round"/>`;
  switch (kind) {
    case "tongue": return `<path d="M84 140 Q100 170 116 140 Z" fill="#7a2e3b"/><path d="M92 151 Q100 170 108 151 Z" fill="#ff7b9c"/>`;
    case "open": return `<ellipse cx="100" cy="149" rx="11" ry="9" fill="#7a2e3b"/><ellipse cx="100" cy="154" rx="6" ry="3" fill="#ff7b9c"/>`;
    case "open_s": return `<ellipse cx="100" cy="149" rx="7" ry="5" fill="#7a2e3b"/>`;
    case "o": return `<ellipse cx="100" cy="150" rx="6" ry="7" fill="#7a2e3b"/>`;
    case "O": return `<ellipse cx="100" cy="151" rx="9" ry="12" fill="#7a2e3b"/>`;
    case "frown": return line("M88 153 Q100 142 112 153");
    case "flat": return line("M90 147 L110 147");
    case "yawn": return `<ellipse cx="100" cy="152" rx="12" ry="14" fill="#7a2e3b"/><ellipse cx="100" cy="160" rx="7" ry="4" fill="#ff7b9c"/>`;
    case "tongue_side": return line("M86 142 Q93 150 100 142 Q107 150 114 142") + `<path d="M104 146 q8 12 14 2 Z" fill="#ff7b9c"/>`;
    default: return line("M86 142 Q93 150 100 142 Q107 150 114 142");
  }
}

function drawA(f) {
  const [ear, earIn] = earPaths(f.ears);
  const blush = f.blush === 2
    ? `<ellipse cx="48" cy="130" rx="15" ry="9" fill="#ff7b9c" opacity=".75"/><ellipse cx="152" cy="130" rx="15" ry="9" fill="#ff7b9c" opacity=".75"/>`
    : `<ellipse cx="48" cy="132" rx="11" ry="7" fill="#ff9fb6" opacity=".55"/><ellipse cx="152" cy="132" rx="11" ry="7" fill="#ff9fb6" opacity=".55"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"><g transform="rotate(${f.tilt || 0} 100 110)">
    <path d="${ear}" fill="#5b6576" stroke="#3d4656" stroke-width="3" stroke-linejoin="round"/><path d="${earIn}" fill="#f6b8c8"/>
    <g transform="translate(200 0) scale(-1 1)"><path d="${ear}" fill="#5b6576" stroke="#3d4656" stroke-width="3" stroke-linejoin="round"/><path d="${earIn}" fill="#f6b8c8"/></g>
    <ellipse cx="100" cy="118" rx="80" ry="68" fill="#5b6576" stroke="#3d4656" stroke-width="3"/>
    <path d="M100 70 C88 92 62 84 40 104 C32 150 70 182 100 182 C130 182 168 150 160 104 C138 84 112 92 100 70 Z" fill="#fbfbfd"/>
    <ellipse cx="62" cy="86" rx="9" ry="6" fill="#fbfbfd"/><ellipse cx="138" cy="86" rx="9" ry="6" fill="#fbfbfd"/>
    ${eyesA(f.eyes)}${blush}
    <path d="M90 128 Q100 124 110 128 Q108 138 100 140 Q92 138 90 128 Z" fill="#1d2433"/><ellipse cx="96" cy="129" rx="3" ry="2" fill="#6b7488"/>
    ${mouthA(f.mouth)}</g>${extras(f.extra, "A")}</svg>`;
}

// ---------------------------------------------------------------- C: cyber
const glow = (c) => `style="filter:drop-shadow(0 0 3px ${c}) drop-shadow(0 0 6px ${c})"`;

function eyesC(kind) {
  const cyan = "#00f0ff", green = "#05ffa1", yellow = "#ffd60a", blue = "#7c9cff";
  const rect = (x, h, c) => `<rect x="${x}" y="${105 - h / 2}" width="20" height="${h}" rx="6" fill="${c}" ${glow(c)}/>`;
  const chev = (x, c) => `<path d="M${x} 112 L${x + 12} 98 L${x + 24} 112" stroke="${c}" stroke-width="6" fill="none" stroke-linecap="round" ${glow(c)}/>`;
  switch (kind) {
    case "closed": return `<rect x="58" y="104" width="24" height="4" rx="2" fill="${cyan}"/><rect x="118" y="104" width="24" height="4" rx="2" fill="${cyan}"/>`;
    case "happy": return chev(58, green) + chev(118, green);
    case "big": return rect(60, 20, green) + rect(120, 20, green);
    case "surprised":
      return `<circle cx="70" cy="105" r="11" fill="none" stroke="${cyan}" stroke-width="5" ${glow(cyan)}/><circle cx="130" cy="105" r="11" fill="none" stroke="${cyan}" stroke-width="5" ${glow(cyan)}/>`;
    case "up":
      return `<rect x="58" y="96" width="26" height="6" rx="3" fill="${yellow}" ${glow(yellow)}/><rect x="116" y="96" width="26" height="6" rx="3" fill="${yellow}" ${glow(yellow)}/><circle cx="100" cy="104" r="3" fill="${yellow}"/>`;
    case "down": return rect(60, 10, "#ff7bac") + rect(120, 10, "#ff7bac");
    case "sad":
      return `<path d="M58 100 L82 108" stroke="${blue}" stroke-width="6" stroke-linecap="round" ${glow(blue)}/><path d="M142 100 L118 108" stroke="${blue}" stroke-width="6" stroke-linecap="round" ${glow(blue)}/>`;
    case "wink": return rect(60, 16, cyan) + chev(118, green);
    default: return rect(60, 16, cyan) + rect(120, 16, cyan);
  }
}

function mouthC(kind) {
  const pink = "#ff2a6d";
  const path = (d, c = pink, w = 4) => `<path d="${d}" stroke="${c}" stroke-width="${w}" fill="none" stroke-linejoin="round" ${glow(c)}/>`;
  switch (kind) {
    case "tongue": return path("M80 146 L88 154 L96 146 L104 154 L112 146 L120 154");
    case "open": case "open_s": return `<rect x="88" y="143" width="24" height="${kind === "open" ? 12 : 7}" rx="3" fill="none" stroke="${pink}" stroke-width="3" ${glow(pink)}/>`;
    case "o": return `<rect x="95" y="144" width="10" height="10" fill="none" stroke="${pink}" stroke-width="3" ${glow(pink)}/>`;
    case "O": return `<rect x="92" y="141" width="16" height="16" fill="none" stroke="${pink}" stroke-width="3" ${glow(pink)}/>`;
    case "frown": return path("M86 154 L100 146 L114 154", "#7c9cff");
    case "yawn": return `<rect x="88" y="140" width="24" height="20" rx="4" fill="none" stroke="#00f0ff" stroke-width="3" ${glow("#00f0ff")}/>`;
    case "tongue_side": return path("M84 150 L116 150") + `<rect x="104" y="150" width="8" height="8" fill="${pink}"/>`;
    case "flat": return path("M88 150 L112 150", "#ffd60a");
    default: return path("M84 150 L116 150");
  }
}

function drawC(f) {
  const [ear] = earPaths(f.ears);
  const antenna = f.extra && f.extra.startsWith("q") ? "#ffd60a" : f.extra && f.extra.startsWith("wave") ? "#05ffa1" : "#ff2a6d";
  const cheeks = f.blush === 2 ? `<rect x="34" y="124" width="18" height="6" rx="3" fill="#ff7bac" ${glow("#ff7bac")}/><rect x="148" y="124" width="18" height="6" rx="3" fill="#ff7bac" ${glow("#ff7bac")}/>` : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"><g transform="rotate(${f.tilt || 0} 100 110)">
    <line x1="150" y1="60" x2="164" y2="26" stroke="#4a5568" stroke-width="3"/><circle cx="164" cy="24" r="6" fill="${antenna}" ${glow(antenna)}/>
    <path d="${ear}" fill="#141a26" stroke="#b967ff" stroke-width="3" stroke-linejoin="round"/>
    <g transform="translate(200 0) scale(-1 1)"><path d="${ear}" fill="#141a26" stroke="#b967ff" stroke-width="3" stroke-linejoin="round"/></g>
    <ellipse cx="100" cy="118" rx="78" ry="66" fill="#141a26" stroke="#b967ff" stroke-width="3" ${glow("#b967ff")}/>
    <path d="M100 76 C88 96 64 90 44 108 C38 150 72 178 100 178 C128 178 162 150 156 108 C136 90 112 96 100 76 Z" fill="#1e2636" stroke="#00f0ff" stroke-width="1.5" stroke-dasharray="4 3"/>
    <path d="M22 110 Q22 60 100 52 Q178 60 178 110" stroke="#4a5568" stroke-width="6" fill="none"/>
    <rect x="12" y="100" width="16" height="34" rx="6" fill="#2a3346" stroke="#b967ff" stroke-width="2"/><rect x="172" y="100" width="16" height="34" rx="6" fill="#2a3346" stroke="#b967ff" stroke-width="2"/>
    <rect x="44" y="88" width="112" height="34" rx="12" fill="#06121c" stroke="#00f0ff" stroke-width="2"/>
    ${eyesC(f.eyes)}${cheeks}
    <path d="M92 130 L108 130 L100 140 Z" fill="#b967ff"/>
    ${mouthC(f.mouth)}</g>${extras(f.extra, "C")}</svg>`;
}

// Renders every frame of the requested skins into a grid for one screenshot.
// URL: grid.html?size=120&skins=A,C  -> rows in skin order, columns in FRAMES order.
function renderGrid() {
  const q = new URLSearchParams(location.search);
  const size = +(q.get("size") || 120);
  const skins = (q.get("skins") || "A,C").split(",");
  // crisp=1 disables anti-aliasing so the pixel skin gets hard edges.
  if (q.get("crisp") === "1") {
    const st = document.createElement("style");
    st.textContent = "svg, svg * { shape-rendering: crispEdges; text-rendering: optimizeSpeed; }";
    document.head.appendChild(st);
  }
  const draw = { A: drawA, C: drawC };
  document.body.style.cssText = "margin:0;background:transparent";
  for (const s of skins) {
    const row = document.createElement("div");
    row.style.cssText = `display:flex;height:${size}px`;
    for (const name of Object.keys(FRAMES)) {
      const cell = document.createElement("div");
      cell.style.cssText = `width:${size}px;height:${size}px;flex:none`;
      cell.innerHTML = draw[s](FRAMES[name]).replace("<svg ", `<svg width="${size}" height="${size}" `);
      row.appendChild(cell);
    }
    document.body.appendChild(row);
  }
}
