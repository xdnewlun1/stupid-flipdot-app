// Schedule page: rules that show a playlist or anything else at set times (every week, between dates each year,
// or one day; clock times or sunrise/sunset), which rule wins now, and a timeline of what plays when.
// The rules are worked out on the server (timetable.py); this page edits them and shows its preview.
import {
  $, api, appOptions, autosave, board, bus, catalog, compCache, drawingsCache, el, galleriesCache, iconButton, paramForm,
  safe, scenesCache, toast, undoable, videosCache,
} from "./common.js";

let schedule = { enabled: true, default: "", rules: [] }, OFF = "__off__", location = null;
let playlists = [];
let hours = 24, preview = null;
const open = new Set();  // rules whose app options are showing
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WHEN = [["weekly", "Every week"], ["dates", "Between dates, every year"], ["once", "On one day"]];
const TARGETS = [["playlist", "Playlist"], ["layers", "Layer set"], ["slideshow", "Slideshow"], ["video", "Video"],
  ["scene", "Picture"], ["drawing", "Drawing"], ["app", "App"], ["off", "Board off (all black)"], ["nothing", "Nothing"]];
const SPEC_PARAM = { layers: "composition", slideshow: "gallery", video: "video", scene: "scene", drawing: "drawing" };

export async function loadSchedule() {
  const [s, p] = await Promise.all([api("/api/schedule"), api("/api/playlists")]);
  schedule = s.schedule; OFF = s.off; location = s.location;
  playlists = Object.keys(p.playlists);
  render();
  await loadPreview();
}

async function saveSchedule() {
  const j = await autosave("schSaved", () => api("/api/schedule", { schedule }));
  schedule = j.schedule;
  render();
  await loadPreview();
}

// ---- rule pieces -------------------------------------------------------------------

const newId = () => "r" + Math.random().toString(16).slice(2, 8);

function targetType(rule) {
  if (rule.spec) return Object.keys(SPEC_PARAM).find(t => t === rule.spec.app) || "app";
  if (rule.playlist === OFF) return "off";
  return rule.playlist ? "playlist" : "nothing";
}

// [value, label] choices for a target type
function choices(type) {
  if (type === "playlist") return playlists.map(n => [n, n]);
  if (type === "layers") return compCache.names.map(n => [n, n]);
  if (type === "slideshow") return galleriesCache.list.map(g => [g.id, g.name]);
  if (type === "video") return videosCache.list.filter(v => v.status === "ready").map(v => [v.id, v.name]);
  if (type === "scene") return scenesCache.list.map(s => [s.id, s.name]);
  if (type === "drawing") return drawingsCache.list.map(d => [d.id, d.name]);
  return [];
}

function setTarget(rule, type, value) {
  delete rule.spec;
  if (type === "playlist") rule.playlist = value ?? playlists[0] ?? "";
  else if (type === "off") rule.playlist = OFF;
  else if (type === "nothing") rule.playlist = "";
  else {
    rule.playlist = "";
    if (type === "app") {
      const app = catalog.byId[value] || catalog.byId.clock || catalog.apps[0];
      rule.spec = { app: app.id, params: paramForm(app).values() };
    } else {
      const first = value ?? choices(type)[0]?.[0];
      rule.spec = { app: type, params: first != null ? { [SPEC_PARAM[type]]: first } : {} };
    }
  }
}

// "HH:MM" | "sunrise+30" | "sunset-15" <-> {kind, time, offset}
function parseTime(v) {
  const m = /^(sunrise|sunset)([+-]\d+)?$/.exec(v || "");
  if (m) return { kind: m[1], offset: +(m[2] || 0) };
  return { kind: "time", time: v === "24:00" ? "00:00" : (v || "00:00") };
}
const fmtTime = t => (t.kind === "time" ? t.time : t.kind + (t.offset ? (t.offset > 0 ? `+${t.offset}` : `${t.offset}`) : ""));

function timeField(label, value, onChange) {
  const t = parseTime(value);
  const kind = el("select", { "aria-label": `${label}: kind of time` },
    el("option", { value: "time" }, "At"), el("option", { value: "sunrise" }, "Sunrise"), el("option", { value: "sunset" }, "Sunset"));
  kind.value = t.kind;
  const time = el("input", { type: "time", value: t.time || "00:00", "aria-label": label });
  const mins = el("input", { type: "number", min: 0, max: 720, step: 5, value: Math.abs(t.offset || 0), inputmode: "numeric", style: "width:72px", "aria-label": `${label}: minutes` });
  const side = el("select", { "aria-label": `${label}: before or after` }, el("option", { value: "1" }, "min after"), el("option", { value: "-1" }, "min before"));
  side.value = (t.offset || 0) < 0 ? "-1" : "1";
  const sync = () => { time.hidden = kind.value !== "time"; mins.hidden = side.hidden = kind.value === "time"; };
  sync();
  const read = () => fmtTime(kind.value === "time" ? { kind: "time", time: time.value || "00:00" }
    : { kind: kind.value, offset: Math.round(+mins.value || 0) * +side.value });
  const changed = safe(() => { sync(); return onChange(read()); });
  for (const c of [kind, time, mins, side]) c.onchange = changed;
  return el("span", { class: "schTime" }, el("span", { class: "muted" }, label), kind, time, mins, side);
}

function mdField(label, value, onChange) {
  const [mm, dd] = (value || "01-01").slice(-5).split("-").map(Number);
  const month = el("select", { "aria-label": `${label}: month` }, MONTHS.map((m, i) => el("option", { value: i + 1, selected: i + 1 === mm }, m)));
  const day = el("input", { type: "number", min: 1, max: 31, value: dd, inputmode: "numeric", style: "width:64px", "aria-label": `${label}: day` });
  const changed = safe(() => onChange(`${String(month.value).padStart(2, "0")}-${String(Math.max(1, Math.min(31, +day.value || 1))).padStart(2, "0")}`));
  month.onchange = day.onchange = changed;
  return el("span", { class: "schTime" }, el("span", { class: "muted" }, label), day, month);
}

function daysField(rule) {
  return el("div", { class: "days", role: "group", "aria-label": "Days of the week" }, DAYS.map((d, di) => el("button", {
    class: rule.days.includes(di) ? "active" : "", "aria-pressed": String(rule.days.includes(di)),
    onclick: safe(() => {
      rule.days = rule.days.includes(di) ? rule.days.filter(x => x !== di) : [...rule.days, di].sort();
      return saveSchedule();
    }),
  }, d)));
}

function targetField(rule) {
  const type = targetType(rule);
  const kind = el("select", { "aria-label": "What to show" }, TARGETS.map(([v, l]) => el("option", { value: v, selected: v === type }, l)));
  kind.onchange = safe(() => { setTarget(rule, kind.value); if (kind.value === "app") open.add(rule.id); return saveSchedule(); });
  const parts = [kind];
  if (type === "app") {
    const app = el("select", { "aria-label": "App" }, appOptions(rule.spec.app));
    app.onchange = safe(() => { setTarget(rule, "app", app.value); return saveSchedule(); });
    parts.push(app, el("button", { type: "button", "aria-expanded": String(open.has(rule.id)), onclick: () => {
      open.has(rule.id) ? open.delete(rule.id) : open.add(rule.id); render();
    } }, open.has(rule.id) ? "Hide options" : "Options"));
  } else if (!["off", "nothing"].includes(type)) {
    const opts = choices(type);
    const cur = type === "playlist" ? rule.playlist : rule.spec?.params?.[SPEC_PARAM[type]];
    const pick = el("select", { "aria-label": TARGETS.find(t => t[0] === type)[1] },
      opts.length ? opts.map(([v, l]) => el("option", { value: v, selected: v === cur }, l)) : el("option", { value: "" }, "None saved yet"));
    if (cur && !opts.some(([v]) => v === cur)) pick.prepend(el("option", { value: cur, selected: true }, `${cur} (missing)`));
    pick.onchange = safe(() => { setTarget(rule, type, pick.value); return saveSchedule(); });
    parts.push(pick);
  }
  return el("span", { class: "schTarget" }, el("span", { class: "muted" }, "Show"), ...parts);
}

function appOptionsForm(rule) {
  const app = catalog.byId[rule.spec.app];
  if (!app) return null;
  if (!app.params.length) return el("p", { class: "muted" }, "This app has no options.");
  const form = paramForm(app, rule.spec.params);
  let timer = null;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(safe(async () => { rule.spec.params = form.values(); await autosave("schSaved", () => api("/api/schedule", { schedule })); await loadPreview(); }), 500);
  };
  form.el.addEventListener("input", changed);
  form.el.addEventListener("change", changed);
  return el("div", { class: "schOptions" }, form.el);
}

// ---- the rules list -------------------------------------------------------------------

function ruleStatus(i) {
  const cur = preview?.current;
  if (!cur || !preview.enabled) return null;
  if (cur.rule === i) return el("span", { class: "schBadge on" }, preview.following ? "Showing now" : "Wins now (schedule paused)");
  if (cur.also?.includes(i)) return el("span", { class: "schBadge" }, `Matches now, but rule ${cur.rule + 1} is higher`);
  return null;
}

function ruleCard(rule, i) {
  const n = schedule.rules.length;
  const on = el("input", { type: "checkbox", checked: rule.enabled, "aria-label": `Rule ${i + 1} on` });
  on.onchange = safe(() => { rule.enabled = on.checked; return saveSchedule(); });
  const when = el("select", { "aria-label": "Repeats" }, WHEN.map(([v, l]) => el("option", { value: v, selected: v === rule.when }, l)));
  when.onchange = safe(() => {
    rule.when = when.value;
    if (rule.when === "dates") { rule.from ||= "12-01"; rule.to ||= "01-01"; }
    if (rule.when === "once") rule.date ||= new Date().toISOString().slice(0, 10);
    return saveSchedule();
  });
  const dateRow = [];
  if (rule.when === "once") {
    const d = el("input", { type: "date", value: rule.date, "aria-label": "Date" });
    d.onchange = safe(() => { if (d.value) { rule.date = d.value; return saveSchedule(); } });
    dateRow.push(d);
  } else {
    if (rule.when === "dates") {
      dateRow.push(mdField("From", rule.from, v => { rule.from = v; return saveSchedule(); }),
        mdField("to", rule.to, v => { rule.to = v; return saveSchedule(); }));
    }
    dateRow.push(daysField(rule));
  }
  const move = d => safe(async () => {
    const j = i + d;
    if (j < 0 || j >= n) return;
    [schedule.rules[i], schedule.rules[j]] = [schedule.rules[j], schedule.rules[i]];
    await saveSchedule();
  });
  const status = ruleStatus(i);
  return el("div", { class: "item schRule" + (rule.enabled ? "" : " disabled") + (status?.classList.contains("on") ? " winning" : ""), "data-rule": rule.id },
    el("div", { class: "schHead" },
      el("label", { class: "schOn" }, on, el("b", {}, `Rule ${i + 1}`)),
      status,
      el("span", { class: "spacer" }),
      el("div", { class: "group" },
        iconButton("up", "Move up (wins over the rules below it)", move(-1)),
        iconButton("down", "Move down", move(1)),
        iconButton("x", "Remove rule", safe(async () => {
          const [gone] = schedule.rules.splice(i, 1);
          await saveSchedule();
          undoable("Removed the rule", { undo: async () => { schedule.rules.splice(i, 0, gone); await saveSchedule(); } });
        }), "danger"))),
    el("div", { class: "schLine" }, when, ...dateRow),
    el("div", { class: "schLine" },
      timeField("From", rule.start, v => { rule.start = v; return saveSchedule(); }),
      timeField("until", rule.end, v => { rule.end = v === "00:00" ? "24:00" : v; return saveSchedule(); })),
    el("div", { class: "schLine" }, targetField(rule)),
    rule.spec && targetType(rule) === "app" && open.has(rule.id) ? appOptionsForm(rule) : null);
}

function render() {
  $("schEnabled").checked = !!schedule.enabled;
  const opts = sel => [el("option", { value: "", selected: !sel }, "Nothing"),
    el("option", { value: OFF, selected: sel === OFF }, "Off (all black)"),
    ...playlists.map(n => el("option", { value: n, selected: n === sel }, n))];
  $("schDefault").replaceChildren(...opts(schedule.default));
  const usesSun = schedule.rules.some(r => /sun/.test(r.start + r.end));
  $("schSunNote").textContent = usesSun && !location
    ? "Sunrise and sunset need a location (Settings → Weather). Until then they count as 06:30 and 18:30."
    : location ? `Sunrise and sunset for ${location.name || `${(+location.lat).toFixed(2)}, ${(+location.lon).toFixed(2)}`}` : "";
  if (!schedule.rules.length) $("schRules").replaceChildren(el("p", { class: "muted" }, "No rules yet, so the default playlist plays all day. Press Add rule for changes by time of day, or Add one-off event for a party."));
  else $("schRules").replaceChildren(el("div", { class: "items" }, schedule.rules.map(ruleCard)));
  renderNow();
}

// ---- now, and the timeline ---------------------------------------------------------------

const hm = iso => iso.slice(11, 16);
const hmMin = s => +s.slice(0, 2) * 60 + +s.slice(3, 5);
const minutesOf = iso => hmMin(iso.slice(11, 16));

function renderNow() {
  const box = $("schNow");
  const p = preview;
  if (!p) return box.replaceChildren();
  const c = p.current;
  if (!p.enabled) return box.replaceChildren(el("b", {}, "The schedule is off."), " Turn on Follow the schedule to use these rules.");
  const what = c.label || "Nothing";
  const why = c.rule == null ? "no rule matches, so the default plays" : `rule ${c.rule + 1} wins`;
  const until = c.until ? ` until ${c.until.slice(0, 10) === p.now.slice(0, 10) ? hm(c.until) : `${DAYS[(new Date(c.until).getDay() + 6) % 7]} ${hm(c.until)}`}` : "";
  const also = c.also?.length ? ` Rule${c.also.length > 1 ? "s" : ""} ${c.also.map(i => i + 1).join(", ")} also match${c.also.length > 1 ? "" : "es"} but ${c.also.length > 1 ? "are" : "is"} lower in the list.` : "";
  const parts = [el("span", { class: "muted" }, "Now: "), el("b", {}, what), ` (${why}${until}).${also}`];
  if (!p.following) {
    parts.push(" ", el("span", { class: "warnText" }, "The schedule is paused because something else was shown. "),
      el("button", { onclick: safe(async () => { await api("/api/schedule/resume", {}); toast("Back on the schedule"); await loadPreview(); }) }, "Resume schedule"));
  }
  box.replaceChildren(...parts);
}

async function loadPreview() {
  preview = await api(`/api/schedule/preview?hours=${hours}`);
  renderNow();
  renderTimeline();
  document.querySelectorAll("#schRules .schRule").forEach((card, i) => {
    const old = card.querySelector(".schBadge");
    const st = ruleStatus(i);
    if (old) old.remove();
    if (st) card.querySelector(".schHead .spacer").before(st);
    card.classList.toggle("winning", !!st?.classList.contains("on"));
  });
}

function colorFor(seg, palette) {
  if (seg.playlist === OFF && !seg.spec) return "off";
  if (!seg.label || seg.label === "Nothing") return "none";
  if (!palette.has(seg.label)) palette.set(seg.label, palette.size % 8);
  return `c${palette.get(seg.label)}`;
}

function renderTimeline() {
  const p = preview;
  if (!p) return;
  document.querySelectorAll("#schRange [data-hours]").forEach(b => {
    b.classList.toggle("active", +b.dataset.hours === hours);
    b.setAttribute("aria-pressed", String(+b.dataset.hours === hours));
  });
  const palette = new Map();
  const segs = p.segments.map(s => ({ ...s, color: colorFor(s, palette) }));
  const rows = p.days.map((d, k) => {
    const day0 = `${d.date}T00:00`, day1 = k + 1 < p.days.length ? `${p.days[k + 1].date}T00:00` : null;
    const mine = segs.filter(s => s.end > day0 && (!day1 || s.start < day1));
    const bar = el("div", { class: "schBar" });
    for (const s of mine) {
      const a = s.start < day0 ? 0 : minutesOf(s.start), b = day1 && s.end >= day1 || s.end.slice(0, 10) !== d.date ? 1440 : minutesOf(s.end);
      if (b <= a) continue;
      const who = s.rule == null ? "default" : `rule ${s.rule + 1}`;
      const text = `${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}–${b === 1440 ? "24:00" : `${String(Math.floor(b / 60)).padStart(2, "0")}:${String(b % 60).padStart(2, "0")}`} ${s.label || "Nothing"} (${who})`;
      bar.append(el("div", { class: `schSeg ${s.color}`, style: `left:${a / 14.4}%;width:${(b - a) / 14.4}%`, title: text, "aria-label": text, role: "img" },
        (b - a) / 1440 > 0.09 ? el("span", {}, s.label || "Nothing") : null));
    }
    if (p.quiet.enabled) {
      const qa = hmMin(p.quiet.start), qb = hmMin(p.quiet.end);
      const spans = qa < qb ? [[qa, qb]] : [[qa, 1440], [0, qb]];
      for (const [x, y] of spans) bar.append(el("div", { class: "schQuiet", style: `left:${x / 14.4}%;width:${(y - x) / 14.4}%`, title: "Quiet hours: fewer flips" }));
    }
    for (const [k2, label] of [["sunrise", "Sunrise"], ["sunset", "Sunset"]]) {
      bar.append(el("div", { class: `schSun ${k2}`, style: `left:${d[k2] / 14.4}%`, title: `${label} ${String(Math.floor(d[k2] / 60)).padStart(2, "0")}:${String(d[k2] % 60).padStart(2, "0")}` }));
    }
    if (d.date === p.now.slice(0, 10)) bar.append(el("div", { class: "schNowMark", style: `left:${minutesOf(p.now) / 14.4}%`, title: `Now ${hm(p.now)}` }));
    const dt = new Date(`${d.date}T12:00`);
    const name = k === 0 ? "Today" : k === 1 ? "Tomorrow" : `${DAYS[(dt.getDay() + 6) % 7]} ${dt.getDate()}`;
    return el("div", { class: "schDay" }, el("span", { class: "schDayName" }, name), bar);
  });
  const ticks = el("div", { class: "schTicks", "aria-hidden": "true" }, el("span", { class: "schDayName" }),
    el("div", { class: "schTickRow" }, [0, 3, 6, 9, 12, 15, 18, 21, 24].map(h => el("span", { style: `left:${h / 0.24}%` }, `${String(h).padStart(2, "0")}`))));
  const legend = el("ul", { class: "schLegend" }, [...palette].map(([label, i]) => el("li", {}, el("span", { class: `sw c${i}` }), label)),
    segs.some(s => s.color === "off") ? el("li", {}, el("span", { class: "sw off" }), "Off (all black)") : null,
    segs.some(s => s.color === "none") ? el("li", {}, el("span", { class: "sw none" }), "Nothing") : null,
    p.quiet.enabled ? el("li", {}, el("span", { class: "sw quiet" }), "Quiet hours") : null,
    el("li", {}, el("span", { class: "sw sun" }), "Sunrise / sunset"));
  // the same as a list, for small screens and screen readers (today, or the first changes of the week)
  const list = el("ol", { class: "schList" }, segs.slice(0, hours === 24 ? 50 : 30).map(s => el("li", {},
    el("span", { class: "when" }, `${hours === 24 ? "" : `${DAYS[(new Date(s.start).getDay() + 6) % 7]} `}${hm(s.start)}–${s.end.slice(0, 10) !== s.start.slice(0, 10) && hm(s.end) === "00:00" ? "24:00" : hm(s.end)}`),
    el("span", { class: `sw ${s.color}` }), el("b", {}, s.label || "Nothing"),
    el("span", { class: "muted" }, s.rule == null ? "default" : `rule ${s.rule + 1}`))));
  $("schTimeline").replaceChildren(ticks, ...rows, legend, el("details", { class: "schListBox" }, el("summary", {}, "As a list"), list));
}

$("schRange").addEventListener("click", e => {
  const b = e.target.closest("[data-hours]");
  if (b) { hours = +b.dataset.hours; safe(loadPreview)(); }
});
$("schEnabled").onchange = safe(() => { schedule.enabled = $("schEnabled").checked; return saveSchedule(); });
$("schDefault").onchange = safe(() => { schedule.default = $("schDefault").value; return saveSchedule(); });
$("schAdd").onclick = safe(() => {
  schedule.rules.push({ id: newId(), enabled: true, when: "weekly", days: [0, 1, 2, 3, 4], start: "07:00", end: "09:00", playlist: playlists[0] || "" });
  return saveSchedule();
});
$("schAddEvent").onclick = safe(async () => {
  // next Saturday evening; one-off events go to the top so they win over the weekly rules
  const d = new Date();
  d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7 || 7));
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  schedule.rules.unshift({ id: newId(), enabled: true, when: "once", date, days: [0, 1, 2, 3, 4, 5, 6], start: "18:00", end: "23:00", playlist: playlists[0] || "" });
  await saveSchedule();
  toast("Added at the top, so it wins over the other rules. Pick the day, times and what to show.");
});

// reload when shown, when playlists or saved things change; the preview's "now" every minute while on screen
bus.addEventListener("tabShown", e => { if (e.detail === "schedule") loadSchedule().catch(err => toast(err.message, true)); });
for (const ev of ["playlists", "refs"]) bus.addEventListener(ev, () => { if (!$("tab-schedule").hidden) loadSchedule().catch(() => {}); });
let lastMode = "";
bus.addEventListener("state", () => {
  const r = board.runner, k = r ? `${r.mode}|${r.schedule_rule_id}|${r.schedule_enabled}` : "";
  if (k !== lastMode && !$("tab-schedule").hidden) { lastMode = k; loadPreview().catch(() => {}); }
});
setInterval(() => { if (!$("tab-schedule").hidden && !document.hidden) loadPreview().catch(() => {}); }, 60000);
