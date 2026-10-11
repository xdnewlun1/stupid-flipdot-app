// Settings → Plugins: Python plugins (plugins.py) and Pixlet apps from the Tronbyt catalogue (pixlet.py).
import { $, api, bus, confirmDialog, el, loadCatalog, safe, toast, undoable } from "./common.js";
import { renderAppList } from "./apps.js";

const kb = n => (n < 1024 ? `${n} bytes` : `${Math.round(n / 1024)} KB`);
const when = ts => new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const groupShown = () => !document.querySelector('.setGroup[data-set="plugins"]').hidden;

// the Apps list (and every app picker) follows what's loaded
async function refreshApps() {
  try { await loadCatalog(); renderAppList(); } catch {}
}

// ---- reading the code: a wide dialog with every file, and the trust switch when it's needed ----
function codeDialog(name, src, { trust = false, title = null } = {}) {
  return new Promise(resolve => {
    const files = src.files.map(f => el("details", { class: "plFile", open: src.files.length === 1 || f.path.endsWith("__init__.py") },
      el("summary", {}, f.path, el("span", { class: "muted" }, ` · ${kb(f.size)}`)),
      el("pre", { class: "plCode", tabindex: "0" }, f.text)));
    const box = el("input", { type: "checkbox", id: "plTrustBox" });
    const ok = el("button", { type: "submit", value: "ok", class: "primary", disabled: trust }, "Turn it on");
    box.onchange = () => { ok.disabled = !box.checked; };
    const d = el("dialog", { class: "dlg plDlg", "aria-labelledby": "plDlgTitle" },
      el("form", { method: "dialog" },
        el("h2", { id: "plDlgTitle" }, title || `Code of ${name}`),
        trust ? el("p", { class: "muted dlgMsg" }, "Read the code before you turn it on. It runs inside the server with full access: it can read your settings and passwords, change files and reach your network.") : null,
        el("div", { class: "plFiles" }, files),
        trust ? el("label", { class: "plTrust" }, box, "I've read this code and I trust it") : null,
        el("div", { class: "row dlgButtons" },
          el("button", { type: "button", onclick: () => d.close("") }, trust ? "Not now" : "Close"),
          trust ? ok : null)));
    document.body.append(d);
    d.addEventListener("close", () => { d.remove(); resolve(d.returnValue === "ok"); });
    d.showModal();
  });
}

async function reviewAndEnable(name, src = null) {
  src ||= await api(`/api/plugins/${name}/source`);
  if (!await codeDialog(name, src, { trust: true, title: `Turn on ${name}?` })) {
    toast(`${name} is installed but off`);
    return load();
  }
  const r = await api(`/api/plugins/${name}`, { enabled: true, trust: true, hash: src.hash });
  await afterChange(r.plugin.error ? null : `${name} is on`);
  if (r.plugin.error) toast(`${name} didn't load: see its error below`, true);
}

async function afterChange(msg) {
  await load();
  await refreshApps();
  if (msg) toast(msg);
}

// ---- the list --------------------------------------------------------------------
function pluginItem(p) {
  const meta = [p.source === "folder" ? "plugins folder" : "installed", p.package ? `${p.files} files` : null, kb(p.size),
    p.version ? `version ${p.version}` : null].filter(Boolean).join(" · ");
  const appsLine = p.apps.length ? `Apps: ${p.apps.map(a => a.title).join(", ")}` : (p.enabled ? "" : "Off");
  const toggle = el("input", { type: "checkbox", checked: p.enabled, "aria-label": `${p.name} on` });
  toggle.onchange = safe(async () => {
    if (toggle.checked && p.needs_trust) { toggle.checked = false; return reviewAndEnable(p.name); }
    const r = await api(`/api/plugins/${p.name}`, { enabled: toggle.checked });
    await afterChange(toggle.checked ? (r.plugin.error ? null : `${p.name} is on`) : `${p.name} is off`);
  });
  const status = p.error ? el("details", { class: "plError" },
    el("summary", { class: "warnText" }, p.reason || (p.loaded ? "An app crashed" : "Didn't load"),
      p.error_at ? el("span", { class: "muted" }, ` · ${when(p.error_at)}`) : ""),
    el("pre", {}, p.error)) : p.reason ? el("p", { class: "warnText plReason" }, p.reason) : null;
  return el("div", { class: "item plItem" },
    el("div", { class: "name" }, p.name,
      el("small", {}, meta),
      p.description ? el("small", {}, p.description) : "",
      appsLine ? el("small", {}, appsLine) : "",
      p.needs_trust ? el("small", { class: "warnText" }, "Off until you read the code and confirm you trust it") : ""),
    el("label", { class: "plSwitch" }, toggle, el("span", {}, "On")),
    el("button", { onclick: safe(async () => codeDialog(p.name, await api(`/api/plugins/${p.name}/source`))) }, "Code"),
    p.enabled ? el("button", { onclick: safe(async () => {
      const r = await api(`/api/plugins/${p.name}/reload`, {});
      render(r);
      await refreshApps();
      const now = r.plugins.find(x => x.name === p.name);
      toast(now?.error ? `${p.name} has a problem: see below` : `${p.name} reloaded`, !!now?.error);
    }) }, "Reload") : "",
    p.source === "installed" ? el("button", { class: "danger", onclick: safe(async () => {
      await api(`/api/plugins/${p.name}/delete`, {});
      await afterChange();
      undoable(`${p.name} deleted`, { undo: async () => { await api(`/api/plugins/${p.name}/restore`, {}); await afterChange(`${p.name} is back`); } });
    }) }, "Delete") : "",
    status ? el("div", { class: "plStatus" }, status) : "");
}

function render(info) {
  $("plFolder").textContent = info.folder || "plugins";
  const on = info.plugins.filter(p => p.enabled).length;
  $("plSummary").textContent = info.plugins.length
    ? `${info.plugins.length} plugin${info.plugins.length === 1 ? "" : "s"}, ${on} on. An app that crashes ${info.max_fails} times in a row turns its plugin off.`
    : "";
  $("plProblems").replaceChildren(...info.problems.map(t => el("p", { class: "warnText" }, t)));
  $("plList").replaceChildren(...(info.plugins.length ? info.plugins.map(pluginItem)
    : [el("p", { class: "muted" }, "No plugins yet. Install one below, or copy a .py file into the plugins folder and press Reload plugins.")]));
  if (info.pixlet) renderPixlet(info.pixlet);
}

export async function loadPlugins() {
  render(await api("/api/plugins"));
}
const load = () => loadPlugins().catch(e => toast(e.message, true));

// ---- installing --------------------------------------------------------------------
async function installed(res) {
  await load();
  await reviewAndEnable(res.plugin.name, res.source);
}

async function withReplace(send, what) {
  try {
    return await send(false);
  } catch (e) {
    if (!/already installed/.test(e.message)) throw e;
    if (!await confirmDialog({ title: "Replace the installed plugin?", message: `${e.message.replace(" Replace it?", "")} Installing ${what} replaces its code; it stays off until you've read the new code.`, confirmText: "Replace", danger: true })) return null;
    return send(true);
  }
}

$("plReload").onclick = safe(async () => {
  const r = await api("/api/plugins/reload", {});
  render(r);
  await refreshApps();
  const bad = r.plugins.filter(p => p.error).length;
  toast(bad ? `Reloaded: ${bad} with a problem` : "Plugins reloaded", !!bad);
});
$("plUpload").onclick = safe(async () => {
  const f = $("plFile").files[0];
  if (!f) { $("plFile").focus(); throw new Error("Choose a .py or .zip file first."); }
  const res = await withReplace(replace => api(`/api/plugins/upload?filename=${encodeURIComponent(f.name)}${replace ? "&replace=1" : ""}`, f, { raw: true }), f.name);
  if (!res) return;
  $("plFile").value = "";
  await installed(res);
});
$("plFetch").onclick = safe(async () => {
  const url = $("plUrl").value.trim();
  if (!url) { $("plUrl").focus(); throw new Error("Paste the plugin's web address first."); }
  const res = await withReplace(replace => api("/api/plugins/install", { url, replace }), "from this address");
  if (!res) return;
  $("plUrl").value = "";
  await installed(res);
});

// ---- Pixlet apps -----------------------------------------------------------------------
function renderPixlet(px) {
  $("pxMissing").hidden = !!px.binary;
  $("pxMissing").textContent = px.hint || "";
  $("pxInstalled").replaceChildren(...px.installed.map(a => el("div", { class: "item" },
    el("div", { class: "name" }, a.title, el("small", {}, `${a.summary || ""} · by ${a.author || "unknown"} · ${a.license}`)),
    el("a", { href: a.page, target: "_blank", rel: "noopener" }, "Source"),
    el("button", { onclick: () => bus.dispatchEvent(new CustomEvent("tab", { detail: "apps" })) }, "Open in Apps"),
    el("button", { class: "danger", onclick: safe(async () => {
      renderPixlet(await api(`/api/pixlet/apps/${a.name}/delete`, {}));
      await refreshApps();
      undoable(`${a.title} removed`, { undo: async () => { renderPixlet(await api(`/api/pixlet/apps/${a.name}/restore`, {})); await refreshApps(); search(); } });
      search();
    }) }, "Remove"))));
}

let searchTimer = null, searchSeq = 0;
async function search(refresh = false) {
  const seq = ++searchSeq;
  const q = new URLSearchParams({ q: $("pxSearch").value.trim(), category: $("pxCategory").value });
  if (refresh) q.set("refresh", "1");
  const r = await api(`/api/pixlet/catalog?${q}`);
  if (seq !== searchSeq) return;
  const sel = $("pxCategory");
  if (sel.options.length <= 1) sel.append(...r.categories.map(c => el("option", { value: c }, c[0].toUpperCase() + c.slice(1))));
  $("pxCount").textContent = `${r.matches} of ${r.total} apps${r.matches > r.apps.length ? `, showing ${r.apps.length}` : ""}` +
    (r.fetched ? ` · catalogue from ${when(r.fetched)}` : "");
  $("pxResults").replaceChildren(...r.apps.map(a => el("div", { class: "item" },
    el("div", { class: "name" }, a.displayName || a.name,
      el("small", {}, [a.summary, a.author ? `by ${a.author}` : null, a.category].filter(Boolean).join(" · "))),
    el("a", { href: a.page, target: "_blank", rel: "noopener" }, "Source"),
    a.installed ? el("span", { class: "muted" }, "Installed") : el("button", { onclick: safe(async () => {
      const r2 = await api("/api/pixlet/install", { name: a.name });
      renderPixlet(r2);
      await refreshApps();
      toast(`${r2.app.title} is in Apps → Plugins`);
      search();
    }) }, "Install"))));
}
const searchSafe = refresh => search(refresh).catch(e => toast(e.message, true));
$("pxSearch").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => searchSafe(), 250); });
$("pxCategory").onchange = () => searchSafe();
$("pxRefresh").onclick = safe(async () => { await search(true); toast("Catalogue updated"); });

function opened() {
  load();
  if (!$("pxResults").childElementCount) searchSafe();
}
bus.addEventListener("settingsGroup", e => { if (e.detail === "plugins") opened(); });
$("setNav").addEventListener("click", e => { if (e.target.closest('[data-set="plugins"]')) opened(); });
bus.addEventListener("tabShown", e => { if (e.detail === "settings" && groupShown()) opened(); });
