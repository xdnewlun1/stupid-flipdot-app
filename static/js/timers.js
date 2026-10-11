// The Timers app's live controls (param type "timers"): the running timers with pause / resume / cancel, a box to
// start one ("pasta 8m"), the quick starts from the app's presets, and the Pomodoro. Talks to /api/timers, so it
// works whether or not the Timers app is on the board.
import { api, el, iconButton, paramTypes, safe } from "./common.js";

const fmt = s => {
  s = Math.max(0, Math.ceil(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
};

paramTypes.timers = (p, v, id) => {
  const list = el("div", { class: "items", role: "list", "aria-live": "polite" });
  const quick = el("div", { class: "row" });
  const say = el("input", { id, type: "text", placeholder: "pasta 8m, tea 3:30, eggs 7 min", "aria-label": "Start a timer" });
  const status = el("span", { class: "muted", role: "status" });
  const box = el("div", {}, list, el("div", { class: "row" }, say, el("button", { type: "button", class: "primary", onclick: safe(() => send({ command: say.value }).then(() => { say.value = ""; })) }, "Start")), quick, status);
  say.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); send({ command: say.value }).then(() => { say.value = ""; }).catch(err => (status.textContent = err.message)); } });

  // the app's other fields, read when they're used (they sit in the same form)
  const field = name => box.closest(".form")?.querySelector(`[id^="p_timers_${name}_"]`);
  let data = null, timer = null, shownPresets = null, shownRows = null, notes = [];

  async function send(body) {
    status.textContent = "";
    try {
      data = { ...(await api("/api/timers", body)), fetched: Date.now() / 1000 };
      draw();
    } catch (e) {
      status.textContent = e.message;
      throw e;
    }
  }

  function draw() {
    if (!data) return;
    const left = t => t.state === "running" ? t.left - (Date.now() / 1000 - data.fetched) : t.left;
    const text = t => (t.state === "ringing" ? "Done" : t.state === "paused" ? `Paused at ${fmt(t.left)}` : fmt(left(t)))
      + (t.kind === "timer" ? "" : ` · Pomodoro, ${data.pomodoro.sessions || 0} done today`);
    // the countdowns tick in place; the rows (and their buttons, which may have focus) are rebuilt only on a change
    const rowKey = data.timers.map(t => t.id + t.state).join("|");
    if (rowKey === shownRows) {
      data.timers.forEach((t, i) => { if (notes[i]) notes[i].textContent = text(t); });
    } else {
      shownRows = rowKey;
      notes = data.timers.map(t => el("small", {}, text(t)));
      drawRows(notes);
    }
    drawQuick();
  }

  function drawRows(notes) {
    const rows = data.timers.map((t, i) => {
      return el("div", { class: "item", role: "listitem" },
        el("div", { class: "name" }, t.name, notes[i]),
        t.state === "ringing"
          ? el("button", { type: "button", onclick: safe(() => send({ action: "dismiss" })) }, "Stop alarm")
          : t.state === "paused"
            ? iconButton("play", `Resume ${t.name}`, safe(() => send({ action: "resume", name: t.id })))
            : iconButton("pause", `Pause ${t.name}`, safe(() => send({ action: "pause", name: t.id }))),
        iconButton("x", `Cancel ${t.name}`, safe(() => send({ action: "cancel", name: t.id }))));
    });
    list.replaceChildren(...(rows.length ? rows : [el("p", { class: "muted" }, "No timers running.")]));
  }

  function drawQuick() {
    // quick starts: the presets field, then the Pomodoro
    const presets = (field("presets")?.value ?? "").split("\n").map(s => s.trim()).filter(Boolean);
    const key = presets.join("|") + data.pomodoro.on;
    if (key !== shownPresets) {
      shownPresets = key;
      const pomo = data.pomodoro.on
        ? el("button", { type: "button", onclick: safe(() => send({ action: "pomodoro", on: false })) }, "Stop Pomodoro")
        : el("button", { type: "button", onclick: safe(() => send({ action: "pomodoro", on: true, work: +(field("work")?.value || 25), break: +(field("break")?.value || 5) })) }, "Start Pomodoro");
      quick.replaceChildren(...presets.map(s => el("button", { type: "button", onclick: safe(() => send({ command: s })) }, s)), pomo);
    }
  }

  async function poll() {
    if (!box.isConnected && data) return clearInterval(timer);  // the form was closed
    try {
      data = { ...(await api("/api/timers")), fetched: Date.now() / 1000 };
      draw();
    } catch (e) {
      status.textContent = e.message;
    }
  }
  poll();
  timer = setInterval(poll, 1000);
  return { input: box, get: () => null };
};
