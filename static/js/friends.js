// Friend messages: the owner's inbox, history and settings for messages sent from friend links.
import { $, api, ask, autosave, el, fillField as fill, forgetFields, safe, thumbCanvas, toast } from "./common.js";

let timer = null;
const when = ts => new Date(ts * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

function state(m, now) {
  if (m.status === "pending") return m.flagged ? "waiting (may contain rude words)" : "waiting for approval";
  if (m.status === "declined") return "declined";
  if (m.pinned_until && m.pinned_until > now) return `pinned until ${when(m.pinned_until)}`;
  if (m.shown_at) return `shown ${when(m.shown_at)}`;
  return m.at && m.at > now ? `scheduled for ${when(m.at)}` : "showing next";
}

function item(m, now) {
  const act = (action, body = {}) => safe(async () => render(await api(`/api/friends/messages/${m.id}/${action}`, body)));
  const pin = safe(async () => {
    const v = await ask({ title: "Pin this message", label: "Hours on the board", value: "3", confirmText: "Pin",
      message: "It stays on the board until then. Other notifications still show, then it comes back.",
      validate: v => (+v > 0 && +v <= 168 ? null : "Between 0.1 and 168 hours.") });
    if (v == null) return;
    render(await api(`/api/friends/messages/${m.id}/pin`, { hours: +v }));
    toast(`Pinned for ${v} hours`);
  });
  const pinned = m.pinned_until && m.pinned_until > now;
  const buttons = [];
  if (m.status === "pending") buttons.push(el("button", { class: "primary", onclick: act("approve") }, m.at && m.at > now ? "Approve" : "Approve and show"),
    el("button", { onclick: act("decline") }, "Decline"));
  else if (m.status === "declined") buttons.push(el("button", { onclick: act("approve") }, "Approve"));
  else if (!m.shown_at) buttons.push(el("button", { onclick: act("show") }, "Show now"), el("button", { onclick: act("decline") }, "Cancel"));
  else {
    buttons.push(el("button", { onclick: act("show") }, "Show again"));
    buttons.push(pinned ? el("button", { onclick: act("unpin") }, "Unpin") : el("button", { onclick: pin }, "Pin…"));
  }
  if (m.status !== "pending") buttons.push(el("button", { class: "danger", onclick: act("delete") }, "Delete"));
  const text = m.text || "(a doodle)";
  return el("div", { class: "item fmItem" + (m.status === "pending" ? " editing" : "") },
    m.rows ? thumbCanvas(m.rows, 2) : "",
    el("div", { class: "name" }, el("span", { class: "fmText" }, text),
      el("small", {}, `${m.name} · sent ${when(m.created)} · ${state(m, now)}`)),
    el("div", { class: "row tight wrapRow" }, ...buttons));
}

function render(info) {
  const s = info.settings, now = info.now;
  fill("fmLength", s.max_length);
  fill("fmSeconds", s.display_seconds);
  fill("fmRateCount", s.rate_count);
  fill("fmRateMin", s.rate_minutes);
  fill("fmProfanity", s.profanity_filter, "checked");
  document.querySelectorAll(".fmCount").forEach(b => {
    b.textContent = info.pending || "";
    b.hidden = !info.pending;
    b.title = `${info.pending} waiting for approval`;
  });
  const pending = info.messages.filter(m => m.status === "pending");
  const rest = info.messages.filter(m => m.status !== "pending");
  $("fmInbox").replaceChildren(...(pending.length ? pending.map(m => item(m, now))
    : [el("p", { class: "muted" }, "Nothing waiting. Messages from friends who need approval appear here.")]));
  $("fmHistory").replaceChildren(...(rest.length ? rest.map(m => item(m, now))
    : [el("div", { class: "empty" }, el("p", { class: "muted" }, "No messages yet. Make a friend link in Settings, People & access, and send it to someone."),
      el("a", { href: "#settings/people" }, "Open People & access"))]));
}

export async function loadFriends() {
  render(await api("/api/friends"));
  clearTimeout(timer);
  let n = 0;
  timer = setTimeout(function tick() {  // every 15 s on this page, every minute elsewhere (for the count)
    const here = document.body.dataset.tab === "friends";
    if (!document.hidden && (here || ++n % 4 === 0)) api("/api/friends").then(render).catch(() => {});
    timer = setTimeout(tick, 15000);
  }, 15000);
}

const FIELDS = ["fmLength", "fmSeconds", "fmRateCount", "fmRateMin", "fmProfanity"];
const save = values => autosave("fmSaved", async () => {
  const info = await api("/api/friends/settings", values);
  forgetFields(FIELDS);
  render(info);
});
const num = (id, key) => { $(id).onchange = safe(() => save({ [key]: +$(id).value })); };
num("fmLength", "max_length");
num("fmSeconds", "display_seconds");
num("fmRateCount", "rate_count");
num("fmRateMin", "rate_minutes");
$("fmProfanity").onchange = safe(() => save({ profanity_filter: $("fmProfanity").checked }));
