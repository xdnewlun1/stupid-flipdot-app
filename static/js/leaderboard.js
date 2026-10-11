// Leaderboard page: the game of the day, today's scores, every game's top 5 and the achievements.
import { $, api, el, safe, toast } from "./common.js";

const when = ts => new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
const time = ts => new Date(ts * 1000).toLocaleTimeString([], { timeStyle: "short" });
const who = n => (n || "").trim() || "---";

function render(d) {
  const title = key => {
    const [id, variant] = key.split(":");
    return (d.titles[id] || id) + (variant ? ` (${variant})` : "");
  };
  const f = d.featured;
  $("lbFeatured").replaceChildren(f.id ? el("div", { class: "item" },
    el("div", { class: "name" }, f.title, el("small", {}, f.best ? `Best ${f.best.score} by ${who(f.best.name)}` : "No score yet. Be the first.")),
    el("button", { class: "primary", onclick: safe(async () => { await api("/api/run", { app: "gameoftheday" }); toast(`Showing ${f.title}, today's game`); }) }, "Show on board"),
  ) : el("p", { class: "muted" }, "No games."));

  $("lbToday").replaceChildren(...(d.today.length ? d.today.map(r => el("div", { class: "item" },
    el("div", { class: "name" }, `${title(r.key)}: ${r.score}`, el("small", {}, `${who(r.name)} · ${time(r.at)}`)))) :
    [el("p", { class: "muted" }, "Nobody has played today yet.")]));

  const keys = Object.keys(d.scores).filter(k => d.scores[k].top.length).sort((a, b) => title(a).localeCompare(title(b)));
  $("lbTables").replaceChildren(...(keys.length ? keys.map(k => el("div", { class: "item" },
    el("div", { class: "name" }, title(k),
      el("small", {}, d.scores[k].top.map((e, i) => `${i + 1}. ${who(e.name)} ${e.score}`).join("   ")),
      el("small", {}, `${d.played[k.split(":")[0]] || 0} games played · best set ${when(d.scores[k].top[0].at)}`)))) :
    [el("p", { class: "muted" }, "No scores yet. Finish a game and it lands here.")]));

  const got = d.achievements.filter(a => a.at).length;
  $("lbBadgeCount").textContent = `${got} of ${d.achievements.length}`;
  $("lbBadges").replaceChildren(...d.achievements.map(a => el("div", { class: "item" + (a.at ? "" : " lbLocked") },
    el("div", { class: "name" }, (a.at ? "★ " : "☆ ") + a.title.charAt(0) + a.title.slice(1).toLowerCase(),
      el("small", {}, a.at ? `${a.how} · ${title(a.key)}, ${when(a.at)}` : a.how)))));
}

export async function loadLeaderboard() {
  render(await api("/api/leaderboard"));
}

$("lbRefresh").onclick = safe(loadLeaderboard);
