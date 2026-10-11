// React to music, shared by slideshows and the generative apps: the table of what each part of the music does
// (slidefx's react map), the live status line ("Listening: Snapcast · ≈ 104 BPM") and the two app param types
// built from them: "music" (the React to music switch with its status) and "react_map" (the effects-on-top table).
import { api, el, paramTypes, safe } from "./common.js";

const FX = [["invert", "Invert"], ["sweep", "Invert sweep"], ["flash", "Flash"], ["glitch", "Glitch"], ["mirror", "Mirror"],
  ["kaleidoscope", "Kaleidoscope"], ["wave", "Wave"], ["scanlines", "Scanlines"], ["pixelate", "Pixelate"], ["outline", "Outline"],
  ["static", "Static"]];
const SIGNALS = {
  bass: ["Bass", "kick drum, low notes"], lowmid: ["Low-mid", "bass guitar, warmth"], mid: ["Mid", "vocals, guitars, keys"],
  high: ["High", "hi-hats, cymbals"], beat: ["Beat", "each beat as it hits"], level: ["Loudness", "the overall volume"],
};
const SENS = [[0, "Any sound"], [20, "High"], [35, "Medium"], [50, "Low"], [70, "Only the peaks"]];
const MOVING = new Set(["wave", "scanlines", "flash", "glitch", "static"]);  // effects with a speed (slidefx.MOTION)
const FOLLOW = [["fixed", "Fixed"], ["signal", "Its signal: louder is faster"], ["tempo", "Tempo: faster songs move faster"], ["both", "Tempo and signal"]];
const LOCKS = [[0, "Not locked to the beat"], [1, "1 cycle per beat"], [2, "1 cycle per 2 beats"], [4, "1 cycle per 4 beats"]];
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];
const BANDS = new Set(["bass", "lowmid", "mid", "high"]);  // rows that choose what they follow (slidefx.REACT_TO)
const REACT_TO = [["level", "Follows: level"], ["hits", "Follows: notes and hits"], ["both", "Follows: level and hits"]];
const BOTH_MIX = [0.65, 0.6];  // slidefx.BOTH_MIX
const SOURCES = { sendspin: "Sendspin", snapcast: "Snapcast", test: "test signal" };

// The table: one row per signal. onChange(rows) gets a copy of all rows after each edit. motion: the speed
// defaults for an effect that moves ({effect: {follow, speed, lock}}); without it, the server fills them in.
export function reactTable({ box = null, onChange, motion = {} }) {
  box ||= el("div", { class: "reactRows", role: "group", "aria-label": "What each part of the music does" });
  let rows = [];
  const save = () => onChange(rows.map(r => ({ ...r })));

  function reactRow(i) {
    const r0 = rows[i];
    const [name, hint] = SIGNALS[r0.signal];
    const fx = el("select", { "aria-label": `Effect for ${name}` },
      el("option", { value: "none" }, "Nothing"),
      ...(r0.signal === "level" ? [el("option", { value: "scale" }, "Tone others down when quiet")] : []),
      ...FX.map(([id, label]) => el("option", { value: id }, label)));
    const amt = el("input", { type: "range", min: 0, max: 100, step: 5, "aria-label": `Amount for ${name}` });
    const out = el("output", { class: "rangeVal" });
    const sens = el("select", { class: "sens", "aria-label": `Sensitivity for ${name}`, title: "How loud this part has to be before the effect starts" });
    const react = el("select", { class: "react", "aria-label": `What ${name} follows`, title: "Level: how loud this part is. Notes and hits: each new note or drum hit, so a steady loud part still moves." },
      ...REACT_TO.map(([v, l]) => el("option", { value: v }, l)));
    react.hidden = !BANDS.has(r0.signal);
    const follow = el("select", { class: "follow", "aria-label": `What sets the speed for ${name}` }, ...FOLLOW.map(([v, l]) => el("option", { value: v }, l)));
    const lock = el("select", { "aria-label": `Lock ${name}'s effect to the beat` }, ...LOCKS.map(([v, l]) => el("option", { value: v }, l)));
    const speed = el("select", { "aria-label": `Speed for ${name}` }, ...SPEEDS.map(v => el("option", { value: v }, `${v}×`)));
    const setRow = (k, v) => { rows[i] = { ...rows[i], [k]: v }; row.fill(rows[i]); return save(); };
    react.onchange = safe(() => setRow("react", react.value));
    // a new effect starts with that effect's own speed defaults
    fx.onchange = safe(() => {
      const { signal, amount, threshold, react: follows } = rows[i];
      rows[i] = { signal, effect: fx.value, amount, threshold, ...(follows ? { react: follows } : {}), ...(motion[fx.value] || {}) };
      row.fill(rows[i]);
      return save();
    });
    follow.onchange = safe(() => setRow("follow", follow.value));
    lock.onchange = safe(() => setRow("lock", Number(lock.value)));
    speed.onchange = safe(() => setRow("speed", Number(speed.value)));
    amt.oninput = () => (out.textContent = `${amt.value}%`);
    amt.onchange = safe(() => setRow("amount", Number(amt.value)));
    sens.onchange = safe(() => setRow("threshold", Number(sens.value)));
    const row = el("div", { class: "reactRow", "data-signal": r0.signal },
      el("div", { class: "sig" }, el("b", {}, name), el("small", {}, hint),
        el("div", { class: "reactMeter", role: "meter", "aria-label": `${name} level`, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": 0 }, el("span"))),
      el("div", { class: "ctl" }, fx, el("label", { class: "amt" }, "Amount", amt, out), react, sens),
      el("div", { class: "spd" }, el("span", { class: "muted" }, "Speed follows"), follow, lock, speed));
    row.fill = r => {
      const moving = MOVING.has(r.effect) && r.follow;
      row.classList.toggle("moving", !!moving);
      if (moving) {
        follow.value = r.follow;
        lock.value = String(r.lock);
        const near = SPEEDS.reduce((a, b) => Math.abs(b - r.speed) < Math.abs(a - r.speed) ? b : a);
        speed.value = String(near);
        lock.hidden = r.follow !== "tempo";
        speed.hidden = r.follow === "tempo" && r.lock > 0;  // locked: the beat sets the speed
      }
      fx.value = r.effect;
      if (r.react) react.value = r.react;
      row.dataset.react = r.react || "level";
      amt.value = r.amount;
      out.textContent = `${r.amount}%`;
      const opts = SENS.some(([v]) => v === r.threshold) ? SENS : [...SENS, [r.threshold, `Custom (${r.threshold}%)`]].sort((a, b) => a[0] - b[0]);
      sens.replaceChildren(...opts.map(([v, label]) => el("option", { value: v }, `Sensitivity: ${label}`)));
      sens.value = String(r.threshold);
      // the beat is a short pulse and loudness-scaling has no threshold: no sensitivity choice for those
      sens.hidden = r.signal === "beat" || r.effect === "scale";
      row.classList.toggle("off", r.effect === "none");
    };
    row.fill(r0);
    return row;
  }

  return {
    el: box,
    get rows() { return rows.map(r => ({ ...r })); },
    fill(next, rebuild = false) {
      rows = next.map(r => ({ ...r }));
      if (rebuild || box.children.length !== rows.length) box.replaceChildren(...rows.map((_, i) => reactRow(i)));
      else rows.forEach((r, i) => box.children[i].fill(r));
    },
    setMeters(j) {
      const on = j.live || j.generated;
      for (const row of box.children) {
        const sig = row.dataset.signal;
        const lvl = j.levels[sig] ?? 0, hit = (j.hits || {})[sig] ?? 0, mode = row.dataset.react;
        const band = mode === "hits" ? hit : mode === "both" ? Math.min(1, BOTH_MIX[0] * lvl + BOTH_MIX[1] * hit) : lvl;
        const v = on ? (sig === "beat" ? j.beat : band) : 0;
        const m = row.querySelector(".reactMeter");
        m.firstChild.style.width = `${Math.round(v * 100)}%`;
        m.setAttribute("aria-valuenow", Math.round(v * 100));
      }
    },
  };
}

// The status line from /api/audio/levels: where the music comes from, the tempo, quiet hours.
export function setStatus(node, j) {
  node.classList.toggle("on", !!j.receiving);
  const quiet = j.quiet ? " · quiet hours: paused" : "";
  const dot = el("span", { class: "dot", "aria-hidden": "true" });
  const tempo = j.live || j.generated ? ` · ${j.bpm_sure ? `≈ ${j.bpm} BPM` : "tempo unsure"}` : "";
  if (j.connected && j.receiving) {
    node.replaceChildren(dot, `Listening: ${SOURCES[j.source] || j.source}${tempo}${quiet}`);
  } else if (j.connected) {
    node.replaceChildren(dot, j.text + (j.generated ? " · using the generated beat" : "") + tempo + quiet);
  } else {
    node.replaceChildren(dot, `${j.source ? j.text : "No live audio"}${j.generated ? ", using the generated beat" : ""}${tempo} — `,
      el("a", { href: "#settings/music" }, "Settings → Music"), `.${quiet}`);
  }
}

// ---- app param types ----

// Polls the levels for the app forms on the page: fast while a "React to music" switch is on and an effects
// table is on screen (its meters), slowly for just the status lines, not at all while the page is hidden.
const watchers = new Set();  // { status, fallback(), on(), table }
let pollTimer = null, polling = false;
function schedule() {
  clearTimeout(pollTimer);
  for (const w of [...watchers]) if (w.seen && !w.status.isConnected) watchers.delete(w);
  if (!watchers.size || document.hidden) return;
  const fast = [...watchers].some(w => w.on() && w.table?.el.isConnected && w.table.el.offsetParent !== null);
  pollTimer = setTimeout(poll, fast ? 250 : 2500);
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const byFallback = {};
    for (const w of watchers) {
      if (!w.status.isConnected) continue;
      w.seen = true;
      const fb = w.fallback();
      byFallback[fb] ||= await api(`/api/audio/levels${fb === "beat" ? "?fallback=beat" : ""}`).catch(() => null);
      const j = byFallback[fb];
      if (!j) continue;
      setStatus(w.status, j);
      if (w.on()) w.table?.setMeters(j);
    }
  } finally {
    polling = false;
    schedule();
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
const watch = w => { watchers.add(w); setTimeout(poll, 0); };

// the React to music switch, with the music's status under it
paramTypes.music = (p, v, id) => {
  const box = el("input", { id, type: "checkbox", checked: !!v });
  const status = el("p", { class: "reactStatus", role: "status" }, el("span", { class: "dot", "aria-hidden": "true" }), "Checking for music…");
  const input = el("div", { class: "musicSwitch" }, box, status);
  const sibling = name => input.closest(".form")?.querySelector(`[id*="_${name}_"]`);  // paramForm ids: p_<app>_<param>_<n>
  const w = { status, on: () => box.checked, fallback: () => sibling("react_fallback")?.value || "none" };
  w.table = { get el() { return sibling("react_fx"); }, setMeters: j => sibling("react_fx")?.setMeters?.(j) };
  box.addEventListener("change", () => poll());
  watch(w);
  return { input, get: () => box.checked };
};

// the table of effects on top of an app (the slideshows' table)
paramTypes.react_map = (p, v, id) => {
  const table = reactTable({ onChange: () => {}, motion: p.motion || {} });
  table.fill(Array.isArray(v) && v.length ? v : p.default || []);
  const input = el("div", { id, class: "wide reactMap" }, table.el);
  input.setMeters = j => table.setMeters(j);
  return { input, get: () => table.rows };
};
