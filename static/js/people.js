// Settings → People & access: share links for players, friends and viewers (access.py on the server).
import { $, api, bus, confirmDialog, el, safe, toast } from "./common.js";

const ROLE_TEXT = {
  friend: "Sends messages",
  player: "Plays games",
  viewer: "Watches the board",
};
const day = ts => new Date(ts * 1000).toLocaleDateString([], { dateStyle: "medium" });
const ago = ts => {
  const s = Date.now() / 1000 - ts;
  return s < 3600 ? "within the hour" : s < 86400 ? "today" : `${Math.round(s / 86400)} days ago`;
};

function qrCanvas(rows, cell = 5) {
  const c = el("canvas", { class: "paQr", role: "img", "aria-label": "QR code for the link" });
  c.width = c.height = rows.length * cell;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = "#000";
  rows.forEach((r, y) => [...r].forEach((v, x) => { if (v === "1") ctx.fillRect(x * cell, y * cell, cell, cell); }));
  return c;
}

// the link exists only now: it isn't stored, so it can't be shown again later (make a new one instead)
function showLink(res, fresh) {
  const t = res.token;
  const copy = safe(async () => {
    await navigator.clipboard.writeText(res.link);
    toast("Link copied");
  });
  const share = navigator.share ? safe(async () => {
    try { await navigator.share({ title: "Flipdot board", text: `A link to the flipdot board for ${t.name}`, url: res.link }); } catch {}
  }) : null;
  $("paNew").replaceChildren(el("div", { class: "paShare" },
    qrCanvas(res.qr),
    el("div", { class: "paShareText" },
      el("h3", {}, `${fresh ? "Link for" : "New link for"} ${t.name}`),
      el("p", { class: "muted" }, "Send this link or let them scan the code. It's shown only now: if it gets lost, make a new link from the list below.",
        fresh ? "" : " The old link has stopped working."),
      el("input", { type: "text", readonly: true, value: res.link, class: "paLinkField", "aria-label": "Share link", onfocus: e => e.target.select() }),
      el("div", { class: "row" },
        el("button", { class: "primary", onclick: copy }, "Copy link"),
        share ? el("button", { onclick: share }, "Share…") : "",
        el("button", { onclick: safe(async () => { await api("/api/access/qr", { link: res.link, name: t.name }); toast("The QR code is on the board for a minute"); }) }, "Show the code on the board"),
        el("button", { class: "ghost", onclick: () => $("paNew").replaceChildren() }, "Done")))));
  $("paNew").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function render(info) {
  $("paWarn").hidden = info.password;
  const list = info.tokens;
  $("paList").replaceChildren(...(list.length ? list.map(t => {
    const meta = [ROLE_TEXT[t.role] || t.role,
      t.expired ? "expired" : t.expires ? `until ${day(t.expires)}` : "no expiry",
      t.last_used ? `last used ${ago(t.last_used)}` : "not used yet"];
    const trusted = t.role === "friend" ? el("label", { class: "paTrust" },
      el("input", { type: "checkbox", checked: !!t.trusted, onchange: safe(async e => {
        await api(`/api/access/${t.id}`, { trusted: e.target.checked });
        toast(e.target.checked ? `${t.name}'s messages show without approval` : `${t.name}'s messages wait for approval`);
      }) }), "No approval needed") : "";
    return el("div", { class: "item" + (t.expired ? " paExpired" : "") },
      el("div", { class: "name" }, t.name, el("small", { class: t.expired ? "warnText" : "" }, meta.join(" · "))),
      trusted,
      el("button", { onclick: safe(async () => {
        if (!await confirmDialog({ title: `New link for ${t.name}?`, message: "The current link stops working right away.", confirmText: "Make a new link" })) return;
        showLink(await api(`/api/access/${t.id}/rotate`, {}), false);
        load();
      }) }, "New link"),
      el("button", { class: "danger", onclick: safe(async () => {
        if (!await confirmDialog({ title: `Revoke ${t.name}'s link?`, message: "It stops working right away. Their messages stay in the history.", confirmText: "Revoke", danger: true })) return;
        render({ ...info, ...(await api(`/api/access/${t.id}/revoke`, {})) });
        toast(`${t.name}'s link no longer works`);
      }) }, "Revoke"));
  }) : [el("p", { class: "muted" }, "No links yet.")]));
}

export async function loadPeople() {
  render(await api("/api/access"));
}
const load = () => loadPeople().catch(e => toast(e.message, true));

function roleChanged() {
  const friend = $("paRole").value === "friend";
  $("paTrusted").hidden = $("paTrustedLabel").hidden = !friend;
}
$("paRole").onchange = roleChanged;
roleChanged();
$("paCreate").onclick = safe(async () => {
  const name = $("paName").value.trim();
  if (!name) { $("paName").focus(); throw new Error("Give the link a name, e.g. who it's for."); }
  const days = +$("paExpires").value;
  const res = await api("/api/access", {
    name, role: $("paRole").value, trusted: $("paRole").value === "friend" && $("paTrusted").checked,
    expires: days ? Math.floor(Date.now() / 1000 + days * 86400) : null,
  });
  $("paName").value = "";
  showLink(res, true);
  load();
});
bus.addEventListener("settingsGroup", e => { if (e.detail === "people") load(); });
$("setNav").addEventListener("click", e => { if (e.target.closest('[data-set="people"]')) load(); });
bus.addEventListener("tabShown", e => {
  if (e.detail === "settings" && !document.querySelector('.setGroup[data-set="people"]').hidden) load();
});
