// Settings → Maintenance (version, restart, updates) and Backup (automatic backups in a folder): server.py's
// /api/system, /api/restart, /api/update*, /api/backups*.
import { $, api, autosave, confirmDialog, el, fillField as fill, forgetFields, safe, toast, undoable } from "./common.js";

const fmtBytes = n => n == null ? "?" : n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(2)} GB`;
const fmtTime = t => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
function fmtSpan(s) {
  const d = Math.floor(s / 86400), h = Math.floor(s / 3600) % 24, m = Math.floor(s / 60) % 60;
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- version, uptime, restart ----
let system = null;
export async function refreshOps() {
  system = await api("/api/system");
  const v = system;
  const when = v.date ? new Date(v.date).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : null;
  $("opVersion").replaceChildren(v.version || v.commit?.slice(0, 8) || "Unknown (not a git checkout)",
    v.commit ? el("small", { class: "opsLine" }, `Commit ${v.commit.slice(0, 8)}${when ? `, ${when}` : ""}${v.branch ? ` on ${v.branch}` : ""}${v.subject ? `: ${v.subject}` : ""}`) : "");
  $("opUptime").textContent = `${fmtSpan(v.uptime_s)} (since ${fmtTime(v.started)})` + (v.systemd ? " · run by systemd" : "");
  fill("upRemote", v.update_source?.remote || "");
  fill("upBranch", v.update_source?.branch || "");
  showJob(v.update);
  await refreshBackups();
}

// After a restart request: wait until a server with a new start time answers.
async function waitForRestart(before, note) {
  const t0 = Date.now();
  await sleep(1500);
  while (Date.now() - t0 < 90000) {
    try {
      const s = await api("/api/system");
      if (s.started !== before) { note(`Back after ${Math.round((Date.now() - t0) / 1000)} s.`); await refreshOps(); return true; }
    } catch { /* still down */ }
    note(`Waiting for the server… ${Math.round((Date.now() - t0) / 1000)} s`);
    await sleep(1000);
  }
  note("The server hasn't come back after 90 s. Check it on the machine (journalctl --user -u flipdot).");
  return false;
}

async function restartNow() {
  const before = system?.started ?? (await api("/api/system")).started;
  const j = await api("/api/restart", {});
  const msg = $("opRestartMsg");
  msg.textContent = j.message;
  return waitForRestart(before, t => { msg.textContent = t; });
}

$("opRestart").onclick = safe(async () => {
  if (!await confirmDialog({ title: "Restart the server?", confirmText: "Restart",
    message: "The board stops for a few seconds, then carries on with the schedule. Settings that only take effect at start (a restored backup, Sendspin) apply now." })) return;
  await restartNow();
});

// Offered after a backup has been staged for restore.
export async function offerRestart(message) {
  if (!await confirmDialog({ title: "Restart now to restore?", message, confirmText: "Restart now" })) return;
  $("opRestartMsg").textContent = "";
  const msg = t => toast(t);
  const before = system?.started ?? (await api("/api/system")).started;
  await api("/api/restart", {});
  toast("Restarting…");
  await waitForRestart(before, msg);
}

// ---- updates ----
function commitList(commits, total) {
  $("upCommits").replaceChildren(...commits.map(c => el("div", { class: "item" },
    el("span", { class: "name" }, c.subject, el("small", {}, `${c.short} · ${new Date(c.date).toLocaleDateString()} · ${c.author}`)))),
    ...(total > commits.length ? [el("span", { class: "muted" }, `and ${total - commits.length} more`)] : []));
}

$("upCheck").onclick = safe(async () => {
  $("upResult").textContent = "Fetching…";
  $("upRun").hidden = $("upResetCfg").hidden = true;
  $("upCommits").replaceChildren();
  let c;
  try {
    c = await api("/api/update/check", {});
  } catch (e) {
    $("upResult").textContent = `Couldn't check: ${e.message}`;
    return;
  }
  const src = `${c.remote}/${c.branch}`;
  const parts = [];
  if (!c.incoming) parts.push(c.ahead ? `No new commits on ${src}; this checkout has ${c.ahead} of its own.` : `Up to date with ${src}.`);
  else parts.push(`${c.incoming} new commit${c.incoming === 1 ? "" : "s"} on ${src}:`);
  const warn = c.blocked ? `Can't update: ${c.blocked}` : null;
  if (warn) parts.push(warn);
  if (c.config_resettable) parts.push("All of config.yaml's changes are already kept in config.local.yaml, so it can go back to the repo version without changing any setting.");
  $("upResult").replaceChildren(...parts.map(p => el("p", { class: p === warn ? "opsWarn" : "" }, p)));
  commitList(c.commits, c.incoming);
  $("upRun").hidden = !c.can_update;
  $("upResetCfg").hidden = !c.config_resettable;
});

$("upResetCfg").onclick = safe(async () => {
  if (!await confirmDialog({ title: "Put config.yaml back to the repo version?", confirmText: "Put back",
    message: "Your values stay in effect: they're kept in config.local.yaml. This only undoes the edits in config.yaml, so updates can run." })) return;
  await api("/api/update/reset-config", {});
  toast("config.yaml is back to the repo version");
  $("upCheck").click();
});

function showJob(job) {
  if (!job || job.state === "idle") return;
  const box = $("upResult");
  if (job.state === "running") box.textContent = `Updating: ${job.log?.at(-1) || job.step}…`;
  else if (job.state === "restarting") box.textContent = job.message;
  else if (job.state === "failed") box.replaceChildren(el("p", { class: "opsWarn" }, `The last update failed: ${job.error}`));
  else if (job.state === "done" && job.finished && Date.now() / 1000 - (job.restarted || job.finished) < 3600) box.textContent = job.message;
}

$("upRun").onclick = safe(async () => {
  if (!await confirmDialog({ title: "Update and restart?", confirmText: "Update",
    message: "Saved data is backed up first. The board stops for a few seconds while the server restarts. If the new version fails its check, the app goes back to this version." })) return;
  const before = system?.started;
  $("upRun").hidden = true;
  let job = await api("/api/update", {});
  while (job.state === "running") {
    showJob(job);
    await sleep(1000);
    job = await api("/api/update").catch(() => ({ state: "restarting", message: "Restarting…" }));
  }
  showJob(job);
  if (job.state === "failed") { toast(`Update failed: ${job.error}`, true); return; }
  if (job.state === "restarting" || (job.state === "done" && job.to && job.to !== system?.commit)) {
    // returns at once when the restarted server already answered the poll above
    $("upCommits").replaceChildren();
    const msg = job.message;
    await waitForRestart(before, t => { $("upResult").textContent = `${msg} ${t}`; });
  }
});

let srcTimer = null;
const saveSource = () => {
  clearTimeout(srcTimer);
  srcTimer = setTimeout(safe(() => autosave("upSaved", async () => {
    const j = await api("/api/update/source", { remote: $("upRemote").value.trim(), branch: $("upBranch").value.trim() });
    forgetFields(["upRemote", "upBranch"]);
    fill("upRemote", j.update_source.remote);
    fill("upBranch", j.update_source.branch);
  })), 300);
};
["upRemote", "upBranch"].forEach(id => $(id).addEventListener("change", saveSource));

// ---- automatic backups ----
const BK_FIELDS = ["bkSchedule", "bkTime", "bkWeekday", "bkFolder", "bkKeep"];
let hiddenNames = new Set();  // deleted, waiting for Undo to run out

function showScheduleFields() {
  const s = $("bkSchedule").value;
  $("bkTime").closest(".row").previousElementSibling.hidden = $("bkTime").closest(".row").hidden = s === "off";
  $("bkWeekday").hidden = s !== "weekly";
}

export async function refreshBackups() {
  const b = await api("/api/backups");
  const cfg = b.settings;
  fill("bkSchedule", cfg.schedule);
  fill("bkTime", cfg.time);
  fill("bkWeekday", String(cfg.weekday));
  fill("bkFolder", cfg.folder);
  fill("bkKeep", cfg.keep);
  showScheduleFields();
  const last = b.last;
  $("bkLast").replaceChildren(
    last ? el("span", { class: last.ok ? "" : "opsWarn" }, last.ok
      ? `${fmtTime(last.time)}, OK (${fmtBytes(last.size)}, ${last.seconds} s${last.tag === "auto" ? "" : `, ${last.tag.replace("-", " ")}`})`
      : `${fmtTime(last.time)}, failed: ${last.error}`) : "None yet",
    b.running ? el("small", { class: "opsLine" }, "A backup is being made now…") : "",
    b.next ? el("small", { class: "opsLine" }, `Next: ${fmtTime(b.next)}`) : "");
  const low = b.free != null && b.free < b.needed + 52428800;
  $("bkFree").replaceChildren(el("span", { class: low ? "opsWarn" : "" },
    `${fmtBytes(b.free)} free in ${b.folder}; a backup takes about ${fmtBytes(b.needed)}` + (low ? ". Not enough room for another one." : "")));
  const list = b.backups.filter(x => !hiddenNames.has(x.name));
  $("bkList").replaceChildren(...(list.length ? list.map(x => el("div", { class: "item" },
    el("span", { class: "name" }, fmtTime(x.time), el("small", {}, `${fmtBytes(x.size)} · ${x.tag === "auto" ? "scheduled" : x.tag.replace("-", " ")} · ${x.name}`)),
    el("div", { class: "row tight" },
      el("button", { onclick: () => el("a", { href: `/api/backups/${encodeURIComponent(x.name)}`, download: x.name }).click() }, "Download"),
      el("button", { onclick: safe(() => restoreFrom(x)) }, "Restore…"),
      el("button", { class: "danger", onclick: safe(() => removeBackup(x)) }, "Delete")))) : [el("span", { class: "muted" }, "No backups in this folder yet.")]));
}

async function restoreFrom(x) {
  if (!await confirmDialog({ title: `Restore the backup from ${fmtTime(x.time)}?`, confirmText: "Restore", danger: true,
    message: "It replaces all saved data (settings, playlists, scenes, drawings, slideshows, videos, fonts and config.local.yaml) when the server restarts. Anything saved after the backup was made is lost." })) return;
  const j = await api(`/api/storage/restore?confirm=1&name=${encodeURIComponent(x.name)}`, {});
  await offerRestart(j.message);
}

async function removeBackup(x) {
  hiddenNames.add(x.name);
  await refreshBackups();
  undoable(`Deleted the backup from ${fmtTime(x.time)}`, {
    undo: async () => { hiddenNames.delete(x.name); await refreshBackups(); },
    commit: async () => { await api(`/api/backups/${encodeURIComponent(x.name)}/delete`, {}); hiddenNames.delete(x.name); await refreshBackups(); },
  });
}

$("bkNow").onclick = safe(async () => {
  const j = await api("/api/backups", {});
  toast(`Backup written: ${j.backup.name} (${fmtBytes(j.backup.size)})`);
  await refreshBackups();
});

let bkTimer = null;
const saveBackupSettings = () => {
  showScheduleFields();
  clearTimeout(bkTimer);
  bkTimer = setTimeout(safe(() => autosave("bkSaved", async () => {
    await api("/api/backups/settings", { schedule: $("bkSchedule").value, time: $("bkTime").value || "03:00",
      weekday: +$("bkWeekday").value, folder: $("bkFolder").value.trim(), keep: Math.round(+$("bkKeep").value) || 7 });
    forgetFields(BK_FIELDS);
    await refreshBackups();
  })), 300);
};
BK_FIELDS.forEach(id => $(id).addEventListener("change", saveBackupSettings));
