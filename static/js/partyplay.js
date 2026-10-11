// /play modes for the party games (apps/pack_party.py, apps/party_*.py): a lobby (join / start) for every party
// game, answer buttons for the quiz, a drawing canvas or guess box for Pictionary and the step grid for the drum
// machine. play.html calls partyMode() on every status update; the mode follows the running app, the way the
// Doom and NES button rows do. State comes from GET /api/party (polled, and on every status change); actions go to
// POST /api/party, strokes and drum edits over the WebSocket when it's open.

const MODES = { party: "pad", rhythm: "pad", quiz: "quiz", pictionary: "draw", drummachine: "drum" };
const POLL = { pad: 1500, quiz: 700, draw: 900, drum: 1000 };
const LETTERS = "ABCD";

let panel = null, app = null, mode = null, seat = 1, wsSend = null, st = null, timer = null, busy = false, gen = 0;
let fetchedAt = 0;

const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c instanceof Node ? c : String(c));
  return e;
};

export function partyMode(runner, player, send) {
  panel ??= document.getElementById("party");
  seat = player;
  wsSend = send;
  const next = runner?.app in MODES ? runner.app : null;
  if (next !== app) {
    app = next;
    mode = next ? MODES[next] : null;
    st = null;
    drawer.reset();
    gen++;
    document.body.dataset.party = mode || "";
    document.body.dataset.partyView = "";
    panel.hidden = !mode;
    panel.dataset.view = "";
    panel.replaceChildren();
    clearTimeout(timer);
    if (!mode) return;
  }
  if (mode) refresh();
}

async function refresh() {
  if (!mode || busy) return;
  const my = gen;
  busy = true;
  clearTimeout(timer);
  try {
    const r = await fetch(`/api/party?seat=${seat}`, { cache: "no-store" });
    if (r.ok) { const body = await r.json(); if (my === gen && body.party) show(body.party); }
  } catch {}
  busy = false;
  if (my === gen && mode) timer = setTimeout(refresh, POLL[mode]);
}

async function act(action, data = {}, viaWs = false) {
  if (viaWs && wsSend?.({ type: "party", action, seat, data })) return;
  try {
    const r = await fetch("/api/party", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, seat, data }) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `error ${r.status}`);
    if (body.party) show(body.party);
    return body;
  } catch (e) {
    note(`Couldn't do that: ${e.message}`);
  }
}

function note(msg) {
  const n = panel.querySelector(".pnote");
  if (n) n.textContent = msg;
}

function show(s) {
  st = s;
  fetchedAt = performance.now();
  const lobby = s.state === "title" && s.mode !== "drum";
  const view = lobby ? "lobby" : s.celebrating || s.state === "over" ? "end" : s.mode;
  if (view === "lobby") return renderLobby(s);
  if (view === "end") return renderEnd(s);
  if (s.mode === "quiz") return renderQuiz(s);
  if (s.mode === "draw") return renderDraw(s);
  if (s.mode === "drum") return renderDrum(s);
  renderPad(s);
}

// one persistent layout per view: rebuilt when the view changes, updated in place otherwise
function frame(view, build) {
  document.body.dataset.partyView = view;
  if (panel.dataset.view !== view) {
    panel.dataset.view = view;
    panel.replaceChildren(...build());
  }
  return panel;
}

const seatName = (s, n) => s.names?.[n] || `P${n}`;

function scoresLine(s) {
  const seats = Object.keys(s.totals || {});
  if (!seats.length) return "";
  return seats.map(n => `${seatName(s, n)} ${s.totals[n]}`).join(" · ");
}

// ---- lobby -----------------------------------------------------------------------------------

function renderLobby(s) {
  frame("lobby", () => [
    h("p", { class: "ptitle", id: "pLobbyTitle" }),
    h("p", { class: "pmuted", id: "pLobbyWho" }),
    h("div", { class: "prow" },
      h("button", { class: "primary", id: "pJoin", onclick: () => act("join") }),
      h("button", { id: "pLeave", onclick: () => act("leave") }, "Leave"),
      h("button", { id: "pStart", onclick: () => act("start") }, "Start now")),
    h("div", { class: "prow", id: "pAdjust", role: "group", "aria-label": "Rounds" },
      h("span", { class: "pmuted" }, "Rounds"),
      h("button", { "aria-label": "Fewer rounds", onclick: () => act("adjust", { delta: -1 }) }, "−"),
      h("output", { id: "pRounds", "aria-live": "polite" }),
      h("button", { "aria-label": "More rounds", onclick: () => act("adjust", { delta: 1 }) }, "+"),
      h("span", { class: "pmuted" }, "Games get harder as they go.")),
    h("p", { class: "pnote", role: "status" }),
  ]);
  const adj = panel.querySelector("#pAdjust");
  adj.hidden = s.adjust !== "rounds";
  panel.querySelector("#pRounds").textContent = s.rounds ?? "";
  const joined = s.seats || [];
  panel.querySelector("#pLobbyTitle").textContent = s.joined ? `You're in as player ${seat}.` : "Join the game";
  panel.querySelector("#pLobbyWho").textContent = joined.length
    ? `Joined: ${joined.map(n => `P${n}`).join(", ")}. It starts a few seconds after the last player joins, or press Start now.`
    : "Press Join (or A on a controller). Pick your player number above first.";
  const join = panel.querySelector("#pJoin");
  join.textContent = `Join as player ${seat}`;
  join.hidden = s.joined;
  panel.querySelector("#pLeave").hidden = !s.joined;
  if (s.note) note(s.note);
}

function renderEnd(s) {
  frame("end", () => [h("p", { class: "ptitle", id: "pEnd" }), h("p", { class: "pmuted", id: "pEndScores" })]);
  const top = Math.max(...Object.values(s.totals || { x: 0 }));
  const champs = Object.keys(s.totals || {}).filter(n => s.totals[n] === top).map(n => seatName(s, n));
  panel.querySelector("#pEnd").textContent = champs.length ? `${champs.join(" and ")} won!` : "Game over";
  panel.querySelector("#pEndScores").textContent = scoresLine(s);
}

// ---- pad games (Flipdot Party, Rhythm): the pad stays, a line of status above it ---------------

function renderPad(s) {
  frame("pad", () => [h("p", { class: "pmuted pline", id: "pPad" })]);
  let line = "";
  if (s.app === "party") line = s.game ? `Round ${s.round}/${s.rounds}: ${s.game.toLowerCase()}. ${s.hint ? s.hint.charAt(0) + s.hint.slice(1).toLowerCase() : ""}` : "";
  if (s.app === "rhythm") line = s.score != null ? `Score ${s.score} · combo ${s.combo} (best ${s.best})${s.live ? " · live music" : " · practice beat"}` : "Press A when a note reaches the line.";
  panel.querySelector("#pPad").textContent = [line, scoresLine(s)].filter(Boolean).join(" — ");
}

// ---- quiz ------------------------------------------------------------------------------------

function renderQuiz(s) {
  frame("quiz", () => [
    h("p", { class: "pmuted", id: "qHead" }),
    h("p", { class: "pquestion", id: "qText" }),
    h("button", { class: "pbuzz primary", id: "qBuzz", onpointerdown: e => { e.preventDefault(); navigator.vibrate?.(30); act("buzz"); } }, "Buzz"),
    h("div", { class: "panswers", role: "group", "aria-label": "Answers" },
      [0, 1, 2, 3].map(i => h("button", { "data-i": i, onclick: () => act("answer", { i }) },
        h("b", {}, LETTERS[i]), h("span", {})))),
    h("p", { class: "pnote", role: "status", "aria-live": "polite" }),
    h("p", { class: "pmuted", id: "qScores" }),
  ]);
  const mine = s.buzzer === seat && s.phase === "answer";
  panel.querySelector("#qHead").textContent = s.number ? `Question ${s.number} of ${s.count}` + (s.left != null ? ` · ${Math.ceil(s.left)} s` : "") : "";
  panel.querySelector("#qText").textContent = s.question || "";
  const buzz = panel.querySelector("#qBuzz");
  buzz.disabled = !(s.phase === "open" || s.phase === "read") || s.locked;
  buzz.textContent = s.locked ? (s.early ? "Too soon: out of this one" : "Locked out") : s.phase === "read" ? "Wait for it…" : "Buzz";
  panel.querySelectorAll(".panswers button").forEach(b => {
    const i = +b.dataset.i;
    b.querySelector("span").textContent = s.answers?.[i] || "";
    b.disabled = !mine;
    b.classList.toggle("right", s.correct === i);
    b.classList.toggle("wrong", s.result && s.result.choice === i && !s.result.right && s.phase === "reveal");
  });
  let msg = "";
  if (mine) msg = "You buzzed: answer now (A, B, X, Y on a controller).";
  else if (s.phase === "answer") msg = `${seatName(s, s.buzzer)} buzzed.`;
  else if (s.phase === "reveal" && s.result?.seat != null) msg = `${seatName(s, s.result.seat)} ${s.result.right ? "got it" : "got it wrong"} (${s.result.points > 0 ? "+" : ""}${s.result.points}).`;
  else if (s.phase === "reveal") msg = "Time's up.";
  note(msg);
  panel.querySelector("#qScores").textContent = scoresLine(s);
}

// ---- Pictionary ------------------------------------------------------------------------------

const drawer = {
  canvas: null, ctx: null, w: 84, h: 41, pic: null, turn: null, pen: 1, size: 1, last: null, segs: [], flushT: null,
  reset() { this.turn = null; this.canvas = null; this.segs = []; },
  paint() {
    const { ctx, w, h, pic } = this;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#f4f4f0";
    for (let i = 0; i < w * h; i++) if (pic[i]) ctx.fillRect(i % w, (i / w) | 0, 1, 1);
  },
  plot(x, y) {
    for (let dy = 0; dy < this.size; dy++) for (let dx = 0; dx < this.size; dx++) {
      const xx = x + dx, yy = y + dy;
      if (xx >= 0 && yy >= 0 && xx < this.w && yy < this.h) this.pic[yy * this.w + xx] = this.pen;
    }
  },
  line(x0, y0, x1, y1) {
    const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0), 1);
    for (let i = 0; i <= n; i++) this.plot(x0 + Math.trunc((x1 - x0) * i / n), y0 + Math.trunc((y1 - y0) * i / n));
    this.segs.push([x0, y0, x1, y1]);
    this.paint();
    this.flushT ??= setTimeout(() => this.flush(), 40);
  },
  flush() {
    this.flushT = null;
    if (!this.segs.length) return;
    const segs = this.segs.splice(0, 200);
    act("draw", { segs, v: this.pen, size: this.size }, true);
    if (this.segs.length) this.flushT = setTimeout(() => this.flush(), 40);
  },
  at(e) {
    const r = this.canvas.getBoundingClientRect();
    return [Math.floor((e.clientX - r.left) / r.width * this.w), Math.floor((e.clientY - r.top) / r.height * this.h)];
  },
  attach(canvas, w, h) {
    this.canvas = canvas; this.w = w; this.h = h;
    canvas.width = w; canvas.height = h;
    this.ctx = canvas.getContext("2d");
    this.pic = new Uint8Array(w * h);
    canvas.addEventListener("pointerdown", e => {
      e.preventDefault();
      try { canvas.setPointerCapture(e.pointerId); } catch {}
      this.last = this.at(e); this.line(...this.last, ...this.last);
    });
    canvas.addEventListener("pointermove", e => {
      if (!this.last) return;
      const p = this.at(e); this.line(...this.last, ...p); this.last = p;
    });
    const end = () => { this.last = null; this.flush(); };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
  },
  load(bits) {
    if (!bits) return;
    for (let i = 0; i < this.pic.length; i++) this.pic[i] = bits.charCodeAt(i) === 49 ? 1 : 0;
    this.paint();
  },
};

function renderDraw(s) {
  const role = s.role || "watcher";
  if (role === "drawer" && s.phase !== "reveal") {
    frame("drawer", () => {
      const cv = h("canvas", { class: "pdraw", "aria-label": "Draw here; the board shows it as you draw" });
      drawer.attach(cv, s.w, s.h);
      const tool = (label, fn, pressed) => h("button", { "aria-pressed": String(!!pressed), onclick: e => { fn(); syncTools(e.currentTarget.parentElement); } }, label);
      return [
        h("p", { class: "ptitle", id: "dWord" }),
        h("p", { class: "pmuted", id: "dHead" }),
        cv,
        h("div", { class: "prow ptools" },
          tool("Pen", () => { drawer.pen = 1; }, true), tool("Eraser", () => { drawer.pen = 0; }),
          tool("Thick", () => { drawer.size = drawer.size === 2 ? 1 : 2; }),
          h("button", { onclick: () => { drawer.pic.fill(0); drawer.paint(); act("clear"); } }, "Clear"),
          h("button", { class: "ghost", onclick: () => act("skip") }, "Give up")),
        h("p", { class: "pnote", role: "status" }),
      ];
    });
    if (drawer.turn !== s.turn) { drawer.turn = s.turn; drawer.load(s.pic); }
    panel.querySelector("#dWord").textContent = s.word ? `Draw: ${s.word}` : "";
    panel.querySelector("#dHead").textContent = s.phase === "ready" ? "Get ready: the clock starts in a moment."
      : `${s.left != null ? Math.ceil(s.left) + " s left" : ""} · don't write letters`;
    note((s.feed || []).slice(-3).map(f => `${f.name}: ${f.text}`).join(" · "));
    return;
  }
  frame("guesser", () => [
    h("p", { class: "ptitle", id: "gHead" }),
    h("p", { class: "phint", id: "gHint" }),
    h("form", { class: "prow pguess", onsubmit: e => {
      e.preventDefault();
      const inp = e.currentTarget.querySelector("input");
      if (inp.value.trim()) act("guess", { text: inp.value.trim() });
      inp.value = "";
    } },
      h("label", { class: "sr", for: "gInput" }, "Your guess"),
      h("input", { id: "gInput", type: "text", autocomplete: "off", autocapitalize: "none", maxlength: 40, placeholder: "Type a guess", enterkeyhint: "send" }),
      h("button", { class: "primary", type: "submit" }, "Guess")),
    h("ul", { class: "pfeed", id: "gFeed", "aria-live": "polite" }),
    h("p", { class: "pmuted", id: "gScores" }),
    h("p", { class: "pnote", role: "status" }),
  ]);
  const head = s.phase === "reveal" ? (s.solved ? `${s.solved} got it: ${s.word}` : `Nobody got it: ${s.word}`)
    : s.phase === "ready" ? `${s.drawer_name} is about to draw` : `${s.drawer_name} is drawing${s.left != null ? ` · ${Math.ceil(s.left)} s` : ""}`;
  panel.querySelector("#gHead").textContent = head;
  panel.querySelector("#gHint").textContent = s.phase === "reveal" ? "" : `${s.hint || ""}`;
  const inp = panel.querySelector("#gInput");
  inp.disabled = s.phase !== "draw" || role !== "guesser" || s.guessed;
  panel.querySelector(".pguess button").disabled = inp.disabled;
  panel.querySelector("#gFeed").replaceChildren(...(s.feed || []).slice().reverse().map(f =>
    h("li", { class: f.right ? "right" : "" }, h("b", {}, f.name), " ", f.text)));
  panel.querySelector("#gScores").textContent = scoresLine(s);
  if (role === "watcher") note("You're not in this game: watch along on the board.");
}

function syncTools(row) {
  const [pen, eraser, thick] = row.querySelectorAll("button");
  pen.setAttribute("aria-pressed", String(drawer.pen === 1));
  eraser.setAttribute("aria-pressed", String(drawer.pen === 0));
  thick.setAttribute("aria-pressed", String(drawer.size === 2));
}

// ---- drum machine ----------------------------------------------------------------------------

let raf = null;

function renderDrum(s) {
  frame("drum", () => {
    const pats = h("select", { id: "dmPats", "aria-label": "Pattern" });
    return [
      h("div", { class: "prow" },
        h("button", { class: "primary", id: "dmPlay", onclick: () => act(st?.playing ? "stop" : "play") }, "Play"),
        h("label", { class: "pfield" }, "Tempo ", h("input", { id: "dmBpm", type: "number", min: 40, max: 240, step: 1, inputmode: "numeric",
          onchange: e => act("set", { bpm: +e.target.value }) }), " BPM"),
        h("label", { class: "pfield" }, "Swing ", h("input", { id: "dmSwing", type: "range", min: 50, max: 75, step: 1,
          onchange: e => act("set", { swing: +e.target.value }) })),
        h("label", { class: "pfield" }, h("input", { id: "dmFollow", type: "checkbox", onchange: e => act("set", { follow: e.target.checked }) }), " Follow the music")),
      h("div", { class: "pgrid", id: "dmGrid", role: "grid", "aria-label": "Steps" }),
      h("div", { class: "prow" }, pats,
        h("button", { onclick: () => pats.value && act("load", { name: pats.value }) }, "Load"),
        h("input", { id: "dmName", type: "text", maxlength: 30, placeholder: "Name", "aria-label": "Pattern name" }),
        h("button", { onclick: () => { const n = panel.querySelector("#dmName").value.trim(); if (n) act("save", { name: n }); else note("Type a name to save the pattern."); } }, "Save"),
        h("button", { class: "ghost", onclick: () => act("clear") }, "Clear")),
      h("p", { class: "pnote", role: "status" }),
    ];
  });
  panel.querySelector("#dmPlay").textContent = s.playing ? "Stop" : "Play";
  const bpm = panel.querySelector("#dmBpm");
  if (document.activeElement !== bpm) bpm.value = s.bpm;
  bpm.disabled = s.follow;
  const sw = panel.querySelector("#dmSwing");
  if (document.activeElement !== sw) sw.value = s.swing;
  panel.querySelector("#dmFollow").checked = s.follow;
  const pats = panel.querySelector("#dmPats");
  const names = s.patterns.map(p => p.name).join("\n");
  if (pats.dataset.names !== names) {
    pats.dataset.names = names;
    pats.replaceChildren(...s.patterns.map(p => new Option(p.preset ? `${p.name} (built in)` : p.name, p.name)));
  }
  if (document.activeElement !== pats) pats.value = s.name;
  const grid = panel.querySelector("#dmGrid");
  if (grid.childElementCount !== s.rows.length) {
    grid.replaceChildren(...s.rows.map((row, r) => h("div", { class: "prowgrid", role: "row" },
      h("span", { class: "plabel", role: "rowheader" }, row.label.split(" (")[0]),
      h("div", { class: "pcells" }, Array.from({ length: 16 }, (_, i) => h("button", {
        class: "pcell", role: "gridcell", "data-r": r, "data-s": i, "aria-label": `${row.label.split(" (")[0]} step ${i + 1}`,
        onclick: e => toggleCell(e.currentTarget),
      }))))));
  }
  grid.querySelectorAll(".pcell").forEach(b => {
    const on = s.grid[+b.dataset.r][+b.dataset.s] === "x";
    if (b.dataset.pending) return;
    b.setAttribute("aria-pressed", String(on));
  });
  note(s.quiet ? "Quiet hours: the loud instruments play softly." : "");
  cancelAnimationFrame(raf);
  if (s.playing) raf = requestAnimationFrame(playhead);
  else grid.querySelectorAll(".now").forEach(b => b.classList.remove("now"));
}

function toggleCell(b) {
  const on = b.getAttribute("aria-pressed") !== "true";
  b.setAttribute("aria-pressed", String(on));
  b.dataset.pending = "1";
  const r = +b.dataset.r, s = +b.dataset.s;
  const sent = wsSend?.({ type: "party", action: "toggle", seat, data: { r, s, on } });
  const done = () => { delete b.dataset.pending; };
  if (sent) setTimeout(() => { done(); refresh(); }, 250);
  else act("toggle", { r, s, on }).finally(done);
}

function playhead() {
  if (!st || st.mode !== "drum" || !st.playing || st.step == null) return;
  const elapsed = performance.now() - fetchedAt + (st.since_ms || 0);
  const cur = (st.step + Math.floor(elapsed / st.step_ms)) % 16;
  panel.querySelectorAll(".pcell").forEach(b => b.classList.toggle("now", +b.dataset.s === cur));
  raf = requestAnimationFrame(playhead);
}
