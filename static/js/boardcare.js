// Quiet hours (Schedule page); board health (Settings → Maintenance: disc wear heatmap + exercise); panel setup (Settings → Board hardware).
import { $, api, autosave, confirmDialog, el, fillField as fill, forgetFields, safe, toast } from "./common.js";

let settings = {};

export async function loadBoardCare(s) {
  const h = s.board_health || {};
  fill("wearAuto", !!h.auto_exercise, "checked");
  fill("wearTime", h.exercise_time || "12:00");
  await Promise.all([loadWear(), loadPanels(), loadQuietHours(s)]);
}

// quiet hours live on the Schedule page
export async function loadQuietHours(s) {
  settings = s;
  const q = s.quiet_hours || {};
  fill("qhEnabled", !!q.enabled, "checked");
  fill("qhStart", q.start || "22:00");
  fill("qhEnd", q.end || "07:00");
  fill("qhMax", q.max_flips ?? 150);
  fill("qhSlow", q.slowdown ?? 3);
  await refreshQuiet();
}

async function refreshQuiet() {
  const { runner } = await api("/api/board");
  const q = settings.quiet_hours || {};
  $("qhStatus").textContent = runner.quiet_hours ? `Active now (until ${q.end})`
    : q.enabled ? `Not active now (starts ${q.start})` : "Off";
}

// quiet hours save themselves when a field changes
let qhTimer = null;
const saveQuiet = safe(() => autosave("qhSaved", async () => {
  settings = await api("/api/settings", { quiet_hours: {
    enabled: $("qhEnabled").checked, start: $("qhStart").value || "22:00", end: $("qhEnd").value || "07:00",
    max_flips: Math.max(10, Math.min(2000, +$("qhMax").value || 150)),
    slowdown: Math.max(1, Math.min(10, +$("qhSlow").value || 1)),
  } });
  const q = settings.quiet_hours;
  forgetFields(["qhEnabled", "qhStart", "qhEnd", "qhMax", "qhSlow"]);
  fill("qhEnabled", !!q.enabled, "checked"); fill("qhStart", q.start); fill("qhEnd", q.end); fill("qhMax", q.max_flips); fill("qhSlow", q.slowdown);
  await refreshQuiet();
}));
for (const id of ["qhEnabled", "qhStart", "qhEnd", "qhMax", "qhSlow"])
  $(id).addEventListener("change", () => { clearTimeout(qhTimer); qhTimer = setTimeout(saveQuiet, 300); });

// ---- board health ----
let wear = null;
const CELL = 8;

// one hue, dark to bright: rarely flipped discs stay dark, busy ones glow (square-root scale so a few
// very busy discs don't wash out everything else)
function heat(t) {
  const stops = [[26, 20, 8], [233, 185, 73], [255, 243, 207]];
  const k = t < 0.6 ? 0 : 1, f = k ? (t - 0.6) / 0.4 : t / 0.6;
  return `rgb(${stops[k].map((a, i) => Math.round(a + (stops[k + 1][i] - a) * f)).join(",")})`;
}

function drawWear() {
  const { width: w, height: h, counts, max } = wear;
  const c = $("wearMap"), g = c.getContext("2d");
  c.width = w * CELL; c.height = h * CELL;
  g.fillStyle = "#050505";
  g.fillRect(0, 0, c.width, c.height);
  const top = Math.sqrt(max || 1);
  for (let i = 0; i < counts.length; i++) {
    g.fillStyle = heat(Math.sqrt(counts[i]) / top);
    g.beginPath();
    g.arc((i % w) * CELL + CELL / 2, Math.floor(i / w) * CELL + CELL / 2, CELL * 0.42, 0, Math.PI * 2);
    g.fill();
  }
}

async function loadWear() {
  wear = await api("/api/wear");
  drawWear();
  const n = x => x.toLocaleString();
  const spot = s => `${s.x},${s.y} (${n(s.count)})`;
  $("wearMin").textContent = n(wear.min);
  $("wearMax").textContent = n(wear.max);
  $("wearSummary").textContent = `${n(wear.total)} flips since ${new Date(wear.since * 1000).toLocaleDateString()}` +
    ` · average ${n(Math.round(wear.total / wear.counts.length))} per disc` +
    ` · most: ${wear.most.slice(0, 3).map(spot).join(", ")} · least: ${wear.least.slice(0, 3).map(spot).join(", ")}`;
}

$("wearMap").addEventListener("mousemove", e => {
  if (!wear) return;
  const r = e.currentTarget.getBoundingClientRect();
  const x = Math.floor((e.clientX - r.left) / r.width * wear.width), y = Math.floor((e.clientY - r.top) / r.height * wear.height);
  const v = wear.counts[y * wear.width + x];
  $("wearHover").textContent = v === undefined ? "" : `disc ${x},${y}: ${v.toLocaleString()} flips`;
});
$("wearMap").addEventListener("mouseleave", () => { $("wearHover").textContent = ""; });
$("wearRefresh").onclick = safe(loadWear);
$("wearExercise").onclick = safe(async () => {
  const j = await api("/api/wear/exercise", {});
  toast(`Flipping ${j.discs} discs for about ${Math.ceil(j.seconds)} s`);
  setTimeout(() => loadWear().catch(() => {}), (j.seconds + 2) * 1000);
});
const saveHealth = safe(() => autosave("wearSaved", async () => {
  settings = await api("/api/settings", { board_health: { auto_exercise: $("wearAuto").checked, exercise_time: $("wearTime").value || "12:00" } });
  forgetFields(["wearAuto", "wearTime"]);
  fill("wearAuto", !!settings.board_health.auto_exercise, "checked"); fill("wearTime", settings.board_health.exercise_time);
}));
$("wearAuto").onchange = saveHealth;
$("wearTime").onchange = saveHealth;

// ---- board setup ----
let panels = [];
let step = -1;
let panelsEdited = false;  // unsaved edits in the table: don't reload over them

async function loadPanels() {
  if (panelsEdited) return renderPanels();
  panels = (await api("/api/panels")).panels;
  renderPanels();
}

function renderPanels() {
  const num = (p, k, min, max) => el("input", { type: "number", min, max, value: p[k], "aria-label": k,
    oninput: e => { p[k] = e.target.value; panelsEdited = true; } });
  $("psRows").replaceChildren(...panels.map((p, i) => el("tr", { class: i === step ? "current" : "" },
    el("td", {}, String(i + 1)),
    el("td", {}, num(p, "column", 1, 32)),
    el("td", {}, num(p, "row", 1, 32)),
    el("td", {}, el("input", { type: "text", value: p.ip, "aria-label": "ip", oninput: e => { p.ip = e.target.value.trim(); panelsEdited = true; } })),
    el("td", {}, num(p, "port", 1, 65535)),
    el("td", {}, num(p, "address", 0, 255)),
    el("td", {}, (() => {
      const s = el("select", { "aria-label": "rotate", onchange: e => { p.rotate = +e.target.value; panelsEdited = true; } },
        el("option", { value: "0" }, "0°"), el("option", { value: "180" }, "180°"));
      s.value = String(p.rotate || 0);
      return s;
    })()),
    el("td", {}, el("button", { class: "ghost", title: "Show this panel on the board", onclick: safe(() => identify(i)) }, "Show"),
      el("button", { class: "ghost danger", title: "Remove this panel", onclick: () => { panels.splice(i, 1); panelsEdited = true; renderPanels(); } }, "Remove")),
  )));
  $("psStep").textContent = step < 0 ? "" : `Panel ${step + 1} of ${panels.length}: column ${panels[step].column}, row ${panels[step].row}, port ${panels[step].port}, address ${panels[step].address}`;
}

async function identify(i) {
  step = i === null ? -1 : i;
  await api("/api/panels/identify", i === null ? {} : { index: i });
  renderPanels();
}

$("psNext").onclick = safe(() => identify(step < 0 ? 0 : (step + 1) % panels.length));
$("psPrev").onclick = safe(() => identify(step <= 0 ? panels.length - 1 : step - 1));
$("psAll").onclick = safe(async () => { await identify(null); $("psStep").textContent = "Every panel shows its own column/row, port and address"; });
$("psDone").onclick = safe(async () => { step = -1; await api("/api/fill", { color: "black" }); renderPanels(); });
$("psAdd").onclick = () => {
  const last = panels[panels.length - 1] || { column: 0, row: 1, ip: "192.168.11.100", port: 5000, address: 0 };
  panels.push({ ...last, column: +last.column + 1, rotate: 0 });
  panelsEdited = true;
  renderPanels();
};
$("psSave").onclick = safe(async () => {
  if (!await confirmDialog({ title: "Write this panel mapping to config.yaml?", message: "The board switches to it right away.", confirmText: "Save to config.yaml" })) return;
  const j = await api("/api/panels", { panels });
  panels = j.panels;
  panelsEdited = false;
  renderPanels();
  $("psMsg").textContent = j.applied ? "Saved and applied" : j.message;
  toast(j.applied ? "Saved to config.yaml and applied" : j.message);
});
