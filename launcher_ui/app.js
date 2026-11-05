"use strict";

/* ================================================================ bridge
   Desktop: pywebview exposes LauncherApi as window.pywebview.api.
   Browser preview (dev_server.py): the same methods over HTTP. */
const bridge = (() => {
  const handlers = {};
  let mode = null;
  let readyPromise = null;

  function waitForDesktop(ms) {
    return new Promise(resolve => {
      if (window.pywebview && window.pywebview.api) return resolve(true);
      window.addEventListener("pywebviewready", () => resolve(true), { once: true });
      setTimeout(() => resolve(!!(window.pywebview && window.pywebview.api)), ms);
    });
  }
  function ready() {
    if (!readyPromise) {
      readyPromise = (async () => {
        const desktop = await waitForDesktop(location.protocol === "file:" ? 20000 : 300);
        mode = desktop ? "desktop" : "http";
        if (mode === "http") setInterval(poll, 600);
        return mode;
      })();
    }
    return readyPromise;
  }
  async function poll() {
    try {
      const response = await fetch("/api/_events");
      (await response.json()).forEach(receive);
    } catch (_) { /* preview server stopped */ }
  }
  async function call(name, ...args) {
    await ready();
    let result;
    if (mode === "desktop") {
      const fn = window.pywebview.api[name];
      if (!fn) throw new Error(`The launcher is missing "${name}". Restart the app.`);
      result = await fn(...args);
    } else {
      const response = await fetch(`/api/${name}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ args }) });
      result = await response.json();
    }
    if (result && typeof result === "object" && result.__error) throw new Error(result.__error);
    return result;
  }
  function receive(message) {
    (handlers[message.event] || []).forEach(fn => {
      try { fn(message.payload || {}); } catch (error) { console.error(error); }
    });
  }
  function on(event, fn) { (handlers[event] = handlers[event] || []).push(fn); }
  return { ready, call, receive, on, get mode() { return mode; } };
})();
window.bridge = bridge;

/* ================================================================ helpers */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "") => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
const plural = (n, word, many = word + "s") => `${n.toLocaleString()} ${n === 1 ? word : many}`;
const ROW = 40;
const KINDS = [["core", "Core"], ["distribution", "Distribution"], ["access", "Access"], ["other", "Other"]];
const KIND_LABEL = { core: "Core switch", distribution: "Distribution switch", access: "Access switch", other: "Device" };
const STATUS_LABEL = { up: "Reachable", filtered: "Answers ping, port closed", down: "Unreachable", unknown: "Not checked yet" };
const INTERVAL_LABEL = m => m === 0 ? "Off" : m < 60 ? `Every ${m} minutes` : m === 60 ? "Every hour" : m < 1440 ? `Every ${m / 60} hours` : "Once a day";
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const isMac = false;

function fmtTime(iso) {
  if (!iso) return "";
  const date = iso instanceof Date ? iso : new Date(iso);
  if (isNaN(date)) return "";
  const now = new Date();
  const seconds = (now - date) / 1000;
  if (seconds < 45 && seconds > -60) return "Just now";
  if (seconds < 3600 && seconds > 0) return `${Math.round(seconds / 60)} min ago`;
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return `Today, ${time}`;
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return `Yesterday, ${time}`;
  const options = { day: "numeric", month: "short" };
  if (date.getFullYear() !== now.getFullYear()) options.year = "numeric";
  return `${date.toLocaleDateString([], options)}, ${time}`;
}
function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function ipNum(ip) {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(ip || "");
  return m ? ((+m[1] * 256 + +m[2]) * 256 + +m[3]) * 256 + +m[4] : Number.MAX_SAFE_INTEGER;
}
function parseAddress(text) {
  const q = text.trim();
  if (!q || /\s/.test(q)) return null;
  const withPort = /^(.+):(\d{1,5})$/.exec(q);
  if (withPort && !withPort[1].includes(":")) return { host: withPort[1], port: withPort[2] };
  return { host: q, port: "" };
}

/* ================================================================ state */
const S = {
  app: {}, theme: "Slate", themes: [], profiles: [], activeProfile: "", defaultProfile: "",
  devices: [], byKey: new Map(), groups: [], recent: [], netbox: { connections: [], history: [], syncing: false },
  settings: {}, intervals: [],
  view: "devices", scope: "all", group: "",
  query: "", filters: { kind: "all", protocol: "any", source: "any", status: "any", tag: "any" },
  sort: { col: "sysname", dir: 1, explicit: false },
  list: [], counts: {}, selected: new Set(), cursor: -1, anchor: -1,
  diag: {}, diagBusy: new Set(), check: null,
  activity: [], unread: 0,
  logs: null, logsLoadedAt: 0, logQuery: "", logPath: "", log: null, logFind: "",
  puttyCount: 0, sessions: null, nbTests: {},
};

function prep(device) {
  device.kind = device.kind || "other";
  device.tags = device.tags || [];
  device._hay = `${device.sysname} ${device.ip} ${device.group} ${device.tags.join(" ")} ${device.notes || ""} ${device.putty_session || ""}`.toLowerCase();
  device._ipn = ipNum(device.ip);
  return device;
}
function setDevices(list) {
  S.devices = (list || []).map(prep);
  S.byKey = new Map(S.devices.map(d => [d.key, d]));
  for (const key of [...S.selected]) if (!S.byKey.has(key) && S.scope !== "recent") S.selected.delete(key);
}
function upsertDevices(list) {
  for (const incoming of list || []) {
    prep(incoming);
    const current = S.byKey.get(incoming.key);
    if (current) {
      // Python leaves out empty fields, so a field missing here was cleared.
      for (const field of Object.keys(current)) if (!(field in incoming)) delete current[field];
      Object.assign(current, incoming);
    } else { S.devices.push(incoming); S.byKey.set(incoming.key, incoming); }
  }
}
function shortPath(path) {
  const parts = String(path || "").split(/[\\/]/).filter(Boolean);
  return parts.length > 3 ? `…\\${parts.slice(-2).join("\\")}` : path;
}
function applyDevicePayload(result) {
  if (result.devices) setDevices(result.devices);
  if (result.groups) S.groups = result.groups;
  if (result.recent) S.recent = result.recent;
}
function tagsInUse() {
  const tags = new Set();
  for (const d of S.devices) for (const t of d.tags) tags.add(t);
  return [...tags].sort((a, b) => a.localeCompare(b));
}
function activeProfile() { return S.profiles.find(p => p.id === S.activeProfile) || S.profiles[0] || {}; }
function deviceFor(key) {
  return S.byKey.get(key) || S.list.find(d => d.key === key) || null;
}
function selectedDevices() { return [...S.selected].map(deviceFor).filter(Boolean); }

/* ================================================================ filtering */
function scopeBase() {
  if (S.scope === "recent") {
    return S.recent.map(r => prep({ ...r, ...(S.byKey.get(r.key) || {}), opened_at: r.opened_at, opened_with: r.opened_with, in_inventory: S.byKey.has(r.key) }));
  }
  if (S.scope === "favorites") return S.devices.filter(d => d.favorite);
  if (S.scope === "group") return S.devices.filter(d => d.group === S.group);
  return S.devices;
}
function searchTerms() {
  const address = parseAddress(S.query);
  const text = address && address.port ? address.host : S.query;
  return text.trim().toLowerCase().split(/\s+/).filter(Boolean);
}
function compute() {
  const terms = searchTerms();
  const f = S.filters;
  const counts = { all: 0, core: 0, distribution: 0, access: 0, other: 0 };
  const out = [];
  for (const d of scopeBase()) {
    if (terms.length && !terms.every(t => d._hay.includes(t))) continue;
    if (f.protocol !== "any" && d.protocol !== f.protocol) continue;
    if (f.source !== "any" && d.source !== f.source) continue;
    if (f.status !== "any" && (d.reachability || "unknown") !== f.status) continue;
    if (f.tag !== "any" && !d.tags.includes(f.tag)) continue;
    counts.all++;
    counts[d.kind] = (counts[d.kind] || 0) + 1;
    if (f.kind !== "all" && d.kind !== f.kind) continue;
    out.push(d);
  }
  sortList(out, terms.join(" "));
  S.list = out;
  S.counts = counts;
}
function sortList(list, query) {
  const { col, dir } = S.sort;
  const statusRank = { up: 0, filtered: 1, down: 2, unknown: 3 };
  const value = d => {
    switch (col) {
      case "ip": return d._ipn;
      case "port": return +d.port;
      case "last": return d.last_success || "";
      case "opened": return d.opened_at || "";
      case "status": return statusRank[d.reachability] ?? 3;
      case "group": return (d.group || "￿").toLowerCase();
      case "tags": return (d.tags[0] || "￿").toLowerCase();
      default: return d.sysname.toLowerCase();
    }
  };
  let compare = (a, b) => {
    const x = value(a), y = value(b);
    if (x < y) return -dir;
    if (x > y) return dir;
    const n1 = a.sysname.toLowerCase(), n2 = b.sysname.toLowerCase();
    return n1 < n2 ? -1 : n1 > n2 ? 1 : 0;
  };
  if (S.scope === "recent" && !S.sort.explicit) compare = (a, b) => (b.opened_at || "").localeCompare(a.opened_at || "");
  if (query) {
    const rank = d => {
      const name = d.sysname.toLowerCase();
      if (d.ip === query || name === query) return 0;
      if (name.startsWith(query)) return 1;
      if (d.ip.startsWith(query)) return 2;
      return 3;
    };
    list.sort((a, b) => rank(a) - rank(b) || compare(a, b));
  } else {
    list.sort(compare);
  }
}
function connectCandidate() {
  const address = parseAddress(S.query);
  if (!address) return null;
  const host = address.host;
  const looksLikeAddress = IPV4.test(host) || (host.includes(".") && /^[A-Za-z0-9.-]+$/.test(host) && S.list.length === 0) || (host.includes(":") && /^[0-9a-fA-F:]+$/.test(host));
  if (!looksLikeAddress) return null;
  const port = address.port || "";
  const exact = S.devices.find(d => d.ip === host && (!port || d.port === port));
  if (exact) return null;
  return { host, port: port || "22" };
}

/* ================================================================ toasts and activity */
function toast({ title, body = "", level = "ok", action = null, timeout }) {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", level === "error" ? "alert" : "status");
  const led = { ok: "up", error: "down", warn: "filtered", info: "unknown" }[level] || "unknown";
  el.innerHTML = `<i class="led ${led}"></i><div><strong>${esc(title)}</strong>${body ? `<p>${esc(body)}</p>` : ""}</div>
    <div style="display:flex;gap:6px;align-items:center">${action ? `<button class="toast-action">${esc(action.label)}</button>` : ""}<button class="toast-close" title="Dismiss">${icon("x")}</button></div>`;
  const remove = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 200); };
  el.querySelector(".toast-close").onclick = remove;
  if (action) el.querySelector(".toast-action").onclick = () => { action.fn(); remove(); };
  $("#toasts").appendChild(el);
  while ($("#toasts").children.length > 4) $("#toasts").firstElementChild.remove();
  setTimeout(remove, timeout ?? (level === "error" ? 9000 : 4200));
}
function fail(error, title = "That didn't work") {
  toast({ title, body: error && error.message ? error.message : String(error), level: "error" });
}
function renderActivityBadge() {
  $("#activity-badge").hidden = S.unread === 0;
}
function renderActivity() {
  const panel = $("#activity-panel");
  if (panel.hidden) return;
  panel.innerHTML = `<header><h2>Notifications</h2>${S.activity.length ? `<button class="text-button" data-action="activity-clear">Clear</button>` : ""}</header>
    <div class="activity-items">${S.activity.length ? S.activity.map(item => `
      <div class="activity-item"><i class="led ${{ ok: "up", error: "down", warn: "filtered" }[item.level] || "unknown"}"></i>
        <div><strong>${esc(item.title)}</strong>${item.body ? `<p>${esc(item.body)}</p>` : ""}<time>${esc(fmtTime(item.at))}</time></div></div>`).join("")
      : `<div class="activity-empty">Connection failures, sync results and check results show up here.</div>`}</div>`;
}

/* ================================================================ modals */
let modalStack = [];
function openModal({ title, intro = "", body = "", footer = "", wide = false, onMount = null, initialFocus = null }) {
  const scrim = document.createElement("div");
  scrim.className = "modal-scrim";
  scrim.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <header><h2>${esc(title)}</h2>${intro ? `<p>${intro}</p>` : ""}</header>
    <div class="modal-body">${body}</div>
    <footer>${footer}</footer></div>`;
  scrim.addEventListener("mousedown", e => { if (e.target === scrim) closeModal(); });
  $("#modal-root").appendChild(scrim);
  const modal = scrim.firstElementChild;
  modalStack.push({ scrim, previous: document.activeElement });
  if (onMount) onMount(modal);
  const focus = initialFocus ? modal.querySelector(initialFocus) : modal.querySelector("input:not([type=checkbox]), select, textarea, .go-button");
  if (focus) setTimeout(() => { focus.focus(); if (focus.select && focus.tagName === "INPUT") focus.select(); }, 30);
  return modal;
}
function closeModal() {
  const top = modalStack.pop();
  if (!top) return;
  top.scrim.remove();
  if (top.previous && document.contains(top.previous)) top.previous.focus();
  if (top.onClose) top.onClose();
}
function confirmDialog({ title, body, confirm = "Confirm", danger = false }) {
  return new Promise(resolve => {
    let answered = false;
    const modal = openModal({
      title, intro: esc(body),
      footer: `<button class="button" data-x="no">Cancel</button><button class="go-button ${danger ? "danger" : ""}" data-x="yes">${esc(confirm)}</button>`,
      initialFocus: "[data-x=yes]",
    });
    const finish = value => { if (answered) return; answered = true; closeModal(); resolve(value); };
    modal.querySelector("[data-x=no]").onclick = () => finish(false);
    modal.querySelector("[data-x=yes]").onclick = () => finish(true);
    modalStack[modalStack.length - 1].onClose = () => { if (!answered) { answered = true; resolve(false); } };
  });
}
function promptDialog({ title, intro = "", label, value = "", confirm = "Save", suggestions = [] }) {
  return new Promise(resolve => {
    let answered = false;
    const listId = "prompt-list";
    const modal = openModal({
      title, intro,
      body: `<label class="field"><span>${esc(label)}</span><input name="value" value="${esc(value)}" list="${listId}">
        <datalist id="${listId}">${suggestions.map(s => `<option value="${esc(s)}">`).join("")}</datalist></label><div class="form-error"></div>`,
      footer: `<button class="button" data-x="no">Cancel</button><button class="go-button" data-x="yes">${esc(confirm)}</button>`,
    });
    const input = modal.querySelector("input");
    const finish = v => { if (answered) return; answered = true; closeModal(); resolve(v); };
    const submit = () => {
      const v = input.value.trim();
      if (!v) { modal.querySelector(".form-error").textContent = `${label} is required.`; return; }
      finish(v);
    };
    modal.querySelector("[data-x=no]").onclick = () => finish(null);
    modal.querySelector("[data-x=yes]").onclick = submit;
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
    modalStack[modalStack.length - 1].onClose = () => { if (!answered) { answered = true; resolve(null); } };
  });
}
function formData(modal) {
  const data = {};
  for (const el of modal.querySelectorAll("[name]")) {
    if (el.type === "checkbox") {
      if (el.dataset.multi) { (data[el.name] = data[el.name] || []); if (el.checked) data[el.name].push(el.value); }
      else data[el.name] = el.checked;
    } else data[el.name] = el.value;
  }
  return data;
}
async function withBusy(button, fn) {
  if (button) button.disabled = true;
  try { return await fn(); } finally { if (button) button.disabled = false; }
}

/* ================================================================ context menu */
function openMenu(x, y, items) {
  const menu = $("#menu");
  menu.innerHTML = items.map((item, i) => {
    if (item === "-") return "<hr>";
    if (item.label && !item.fn) return `<div class="menu-label">${esc(item.label)}</div>`;
    return `<button data-i="${i}" class="${item.danger ? "danger" : ""}">${item.icon ? icon(item.icon) : "<svg></svg>"}<span>${esc(item.label)}</span>${item.kbd ? `<kbd>${esc(item.kbd)}</kbd>` : ""}</button>`;
  }).join("");
  menu.hidden = false;
  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, innerWidth - rect.width - 8) + "px";
  menu.style.top = Math.min(y, innerHeight - rect.height - 8) + "px";
  menu.onclick = e => {
    const button = e.target.closest("button[data-i]");
    if (!button) return;
    closeMenu();
    items[+button.dataset.i].fn();
  };
  const first = menu.querySelector("button");
  if (first) first.focus();
}
function closeMenu() { $("#menu").hidden = true; }

/* ================================================================ rail */
function renderRail() {
  $("#brand-version").textContent = `Chirag, version ${S.app.version || ""}`;
  $("#count-all").textContent = S.devices.length.toLocaleString();
  $("#count-favorites").textContent = S.devices.filter(d => d.favorite).length || "";
  $("#count-recent").textContent = S.recent.length || "";
  $$(".nav-item").forEach(el => {
    const active = el.dataset.view === S.view && (el.dataset.view !== "devices" || el.dataset.scope === S.scope);
    el.classList.toggle("active", active);
  });
  const groups = S.groups;
  $("#group-list").innerHTML = groups.length
    ? groups.map(g => `<button class="group-item ${S.view === "devices" && S.scope === "group" && S.group === g.name ? "active" : ""}" data-action="group" data-group="${esc(g.name)}" title="${esc(g.name)}"><span>${esc(g.name)}</span><em>${g.count || ""}</em></button>`).join("")
    : `<div class="group-empty">Create groups to organise sites or roles. Drag devices onto a group to add them.</div>`;
  const select = $("#profile-select");
  select.innerHTML = S.profiles.map(p => `<option value="${esc(p.id)}" ${p.id === S.activeProfile ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const failing = S.netbox.history[0] && (S.netbox.history[0].connections || []).some(c => !c.ok);
  $("#netbox-flag").hidden = !failing;
  renderPutty();
  renderSyncButton();
}
function renderPutty() {
  const n = S.puttyCount;
  $("#putty-count").textContent = n ? `${plural(n, "PuTTY window")} open` : "No PuTTY windows open";
  $(".putty-status").classList.toggle("live", n > 0);
  $("#close-all").hidden = n === 0;
}
function renderSyncButton() {
  const button = $("#sync-button");
  const last = S.netbox.history[0];
  const failing = last && ((last.connections || []).some(c => !c.ok) || last.error);
  button.classList.toggle("busy", !!S.netbox.syncing);
  button.classList.toggle("alert", !S.netbox.syncing && !!failing);
  button.querySelector("span").textContent = S.netbox.syncing ? "Syncing…" : "Sync NetBox";
  button.title = S.netbox.syncing ? "NetBox sync is running" : failing ? "The last sync had problems. Open NetBox for details. (F5 to sync)" : last ? `Last synced ${fmtTime(last.finished_at)} (F5)` : "Sync from NetBox (F5)";
}

/* ================================================================ devices view */
function scopeTitle() {
  if (S.scope === "favorites") return ["Favorites", "Devices you starred. Press Ctrl+D on any device to add it."];
  if (S.scope === "recent") return ["Recently opened", "The last 20 devices you opened, newest first."];
  if (S.scope === "group") return [S.group, "Devices in this group. Drag devices onto a group in the sidebar to add them."];
  return ["All devices", ""];
}
function buildDevicesView() {
  const view = $("#view-devices");
  const isRecent = S.scope === "recent";
  view.innerHTML = `
    <div class="list-head">
      <div class="list-title"><h1 id="list-title"></h1><p id="list-sub"></p></div>
      <div class="list-actions">
        ${S.scope === "group" ? `<button class="button" data-action="group-menu">${icon("more")}<span>Group</span></button>` : ""}
        ${isRecent ? `<button class="button" data-action="clear-recent">Clear list</button>` : ""}
        <button class="button" data-action="check-visible" id="check-button">${icon("pulse")}<span>Check reachability</span></button>
        <button class="button" data-action="import-csv">${icon("upload")}<span>Import</span></button>
        <button class="go-button" data-action="add-device">${icon("plus")}<span>Add device</span></button>
      </div>
    </div>
    <div class="filters" id="filters"></div>
    <div id="connect-slot"></div>
    <div class="table ${isRecent ? "recent" : ""}" id="table">
      <div class="thead cols" id="thead"></div>
      <div class="progress-strip" id="check-strip" hidden><i style="width:0"></i></div>
      <div class="scroll" id="scroll" tabindex="0" aria-label="Device list">
        <div class="spacer-y" id="spacer"></div>
        <div class="rows" id="rows" role="listbox" aria-multiselectable="true"></div>
        <div id="list-empty" hidden></div>
      </div>
      <div class="status-line" id="status-line"></div>
    </div>`;
  const scroll = $("#scroll");
  scroll.addEventListener("scroll", () => requestAnimationFrame(renderRows));
  const rows = $("#rows");
  rows.addEventListener("mousedown", onRowMouseDown);
  rows.addEventListener("dblclick", e => {
    const row = e.target.closest(".row");
    if (row && !e.target.closest("button")) launchKeys([S.list[+row.dataset.i].key]);
  });
  rows.addEventListener("contextmenu", e => {
    const row = e.target.closest(".row");
    if (!row) return;
    e.preventDefault();
    const i = +row.dataset.i;
    if (!S.selected.has(S.list[i].key)) selectOnly(i);
    showDeviceMenu(e.clientX, e.clientY);
  });
  rows.addEventListener("dragstart", e => {
    const row = e.target.closest(".row");
    if (!row) return;
    const device = S.list[+row.dataset.i];
    if (S.pendingUp) { window.removeEventListener("mouseup", S.pendingUp); S.pendingUp = null; }
    const keys = S.selected.has(device.key) ? [...S.selected] : [device.key];
    e.dataTransfer.setData("text/x-device-keys", JSON.stringify(keys));
    e.dataTransfer.effectAllowed = "copy";
  });
  new ResizeObserver(() => renderRows()).observe(scroll);
}
function refreshDevices({ keepScroll = true } = {}) {
  if (S.view !== "devices") return;
  compute();
  if (S.cursor >= S.list.length) S.cursor = S.list.length - 1;
  renderListHead();
  renderFilters();
  renderConnect();
  renderThead();
  renderRows();
  renderStatusLine();
  if (!keepScroll) $("#scroll").scrollTop = 0;
}
function renderListHead() {
  const [title, sub] = scopeTitle();
  $("#list-title").textContent = title;
  const total = scopeBase().length;
  const shown = S.list.length;
  const filtered = shown !== total;
  $("#list-sub").textContent = sub || (filtered ? `Showing ${shown.toLocaleString()} of ${plural(total, "device")}` : plural(total, "device"));
  if (sub && filtered) $("#list-sub").textContent = `Showing ${shown.toLocaleString()} of ${total.toLocaleString()}. ${sub}`;
}
function renderFilters() {
  const f = S.filters;
  const tags = tagsInUse();
  const chip = (name, value, options) => `<span class="chip-select ${value !== "any" ? "set" : ""}"><select data-filter="${name}">${options.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${icon("chevron")}</span>`;
  const anySet = f.kind !== "all" || f.protocol !== "any" || f.source !== "any" || f.status !== "any" || f.tag !== "any";
  $("#filters").innerHTML = `
    <div class="segmented" role="group" aria-label="Device role">
      <button data-action="kind" data-kind="all" class="${f.kind === "all" ? "on" : ""}">All <em>${(S.counts.all || 0).toLocaleString()}</em></button>
      ${KINDS.filter(([k]) => k !== "other" || S.counts.other || f.kind === "other").map(([k, label]) => `<button data-action="kind" data-kind="${k}" class="k-${k} ${f.kind === k ? "on" : ""}" title="${label} devices"><i class="cord"></i>${label} <em>${(S.counts[k] || 0).toLocaleString()}</em></button>`).join("")}
    </div>
    <div class="chips">
      ${chip("protocol", f.protocol, [["any", "Any protocol"], ["ssh", "SSH"], ["telnet", "Telnet"]])}
      ${chip("status", f.status, [["any", "Any status"], ["up", "Reachable"], ["filtered", "Ping only"], ["down", "Unreachable"], ["unknown", "Not checked"]])}
      ${chip("source", f.source, [["any", "Any source"], ["manual", "Added here"], ["netbox", "From NetBox"]])}
      ${tags.length ? chip("tag", f.tag, [["any", "Any tag"], ...tags.map(t => [t, t])]) : ""}
      ${anySet ? `<button class="text-button reset" data-action="reset-filters">Clear filters</button>` : ""}
    </div>`;
}
function renderConnect() {
  const slot = $("#connect-slot");
  const candidate = connectCandidate();
  if (!candidate) { slot.innerHTML = ""; return; }
  const existing = slot.querySelector("input[name=port]");
  const portValue = existing && existing.dataset.host === candidate.host ? existing.value : candidate.port;
  const telnet = portValue === "23";
  slot.innerHTML = `<div class="connect-row">
    <i class="cord"></i>
    <div class="what"><strong>${esc(candidate.host)}</strong><span>Not in your list yet. Press Enter to open ${telnet ? "Telnet" : "SSH"} and save it as a new device.</span></div>
    <label>Port <input name="port" class="mono-num" value="${esc(portValue)}" data-host="${esc(candidate.host)}" inputmode="numeric"></label>
    <button class="go-button" data-action="connect">${icon("play")}<span>Connect</span></button></div>`;
  const input = slot.querySelector("input[name=port]");
  input.addEventListener("input", () => {
    const t = input.value.trim() === "23";
    slot.querySelector(".what span").textContent = `Not in your list yet. Press Enter to open ${t ? "Telnet" : "SSH"} and save it as a new device.`;
  });
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); quickConnect(); } });
}
function renderThead() {
  const isRecent = S.scope === "recent";
  const head = (col, label, cls = "") => {
    const sorted = S.sort.col === col && (S.sort.explicit || col === "sysname") && !(isRecent && !S.sort.explicit);
    return `<button class="${cls} ${sorted ? "sorted" : ""} ${sorted && S.sort.dir < 0 ? "desc" : ""}" data-action="sort" data-col="${col}">${label}${sorted ? icon("chevron") : ""}</button>`;
  };
  $("#thead").innerHTML = `
    <button class="static" data-action="sort" data-col="status" title="Sort by reachability"><i class="led unknown"></i></button>
    ${head("sysname", "Hostname")}${head("ip", "IP address")}${head("port", "Port")}
    ${head("group", "Group", isRecent ? "hide-md" : "")}
    ${isRecent ? head("opened", "Opened") + `<div class="static faint hide-md">Profile</div>` : head("tags", "Tags", "hide-md") + head("last", "Last opened", "hide-md")}
    <div></div>`;
}
function ledClass(d) {
  if (S.check && S.check.pending && S.check.pending.has(d.key)) return "checking";
  return d.reachability || "unknown";
}
function statusTitle(d) {
  const base = STATUS_LABEL[d.reachability || "unknown"];
  const when = d.last_checked ? `, checked ${fmtTime(d.last_checked).toLowerCase()}` : "";
  const ms = d.reachability === "up" && d.latency_ms ? ` in ${d.latency_ms} ms` : "";
  return base + ms + when;
}
function rowHtml(d, i) {
  const selected = S.selected.has(d.key);
  const isRecent = S.scope === "recent";
  const tags = d.tags.slice(0, 3).map(t => `<span class="tag">${esc(t)}</span>`).join("") + (d.tags.length > 3 ? `<span class="tag">+${d.tags.length - 3}</span>` : "");
  return `<div class="row cols ${selected ? "selected" : ""} ${i === S.cursor ? "cursor" : ""}" data-i="${i}" role="option" aria-selected="${selected}" draggable="true">
    <div class="cell-led"><i class="led ${ledClass(d)}" title="${esc(statusTitle(d))}"></i></div>
    <div class="cell-name k-${d.kind}"><i class="cord"></i><strong title="${esc(d.sysname)}">${esc(d.sysname)}</strong></div>
    <div class="num">${esc(d.ip)}</div>
    <div><span class="num">${esc(d.port)}</span><span class="proto ${d.protocol}">${d.putty_session ? "Session" : d.protocol === "telnet" ? "Telnet" : "SSH"}</span></div>
    <div class="muted ${isRecent ? "hide-md" : ""}">${esc(d.group)}</div>
    ${isRecent
      ? `<div class="muted">${esc(fmtTime(d.opened_at))}</div><div class="faint hide-md">${esc(d.opened_with || "")}</div>`
      : `<div class="hide-md"><div class="tags">${tags}</div></div><div class="faint hide-md">${esc(fmtTime(d.last_success))}</div>`}
    <div class="star-cell"><button data-action="star" class="${d.favorite ? "on" : ""}" title="${d.favorite ? "Remove from favorites" : "Add to favorites"} (Ctrl+D)" tabindex="-1">${icon("star")}</button></div>
  </div>`;
}
function renderRows() {
  const scroll = $("#scroll");
  if (!scroll || S.view !== "devices") return;
  const n = S.list.length;
  $("#spacer").style.height = n * ROW + "px";
  const top = scroll.scrollTop;
  const height = scroll.clientHeight || 600;
  const start = Math.max(0, Math.floor(top / ROW) - 6);
  const end = Math.min(n, Math.ceil((top + height) / ROW) + 6);
  const rows = $("#rows");
  rows.style.transform = `translateY(${start * ROW}px)`;
  let html = "";
  for (let i = start; i < end; i++) html += rowHtml(S.list[i], i);
  rows.innerHTML = html;
  const empty = $("#list-empty");
  empty.hidden = n > 0;
  if (!n) empty.innerHTML = emptyStateHtml();
}
function emptyStateHtml() {
  if (!S.devices.length && S.scope === "all") {
    return `<div class="empty"><h2>Add your first devices</h2><p>Import a CSV with ip, hostname and port columns, sync from NetBox, or type an IP address in the search box to connect straight away.</p>
      <div class="row-actions"><button class="go-button" data-action="import-csv">${icon("upload")}<span>Import CSV</span></button><button class="button" data-action="go" data-view="netbox">${icon("netbox")}<span>Set up NetBox</span></button><button class="button" data-action="add-device">${icon("plus")}<span>Add device</span></button></div></div>`;
  }
  if (S.scope === "favorites" && !S.query) return `<div class="empty"><h2>No favorites yet</h2><p>Select a device and press Ctrl+D, or click the star at the end of its row.</p></div>`;
  if (S.scope === "recent" && !S.query) return `<div class="empty"><h2>Nothing opened yet</h2><p>Devices you open appear here so you can get back to them quickly.</p></div>`;
  if (S.scope === "group" && !S.query) return `<div class="empty"><h2>${esc(S.group)} is empty</h2><p>Drag devices from All devices onto this group, or right-click devices and choose Move to group.</p></div>`;
  const address = connectCandidate();
  return `<div class="empty"><h2>No devices match</h2><p>${address ? "Use Connect above to open this address." : "Check the spelling, or clear the filters to search everything."}</p>
    ${S.filters.kind !== "all" || S.filters.protocol !== "any" || S.filters.status !== "any" || S.filters.source !== "any" || S.filters.tag !== "any" ? `<div class="row-actions"><button class="button" data-action="reset-filters">Clear filters</button></div>` : ""}</div>`;
}
function renderStatusLine() {
  const line = $("#status-line");
  if (!line) return;
  const selected = S.selected.size;
  const check = S.check;
  line.innerHTML = `
    <span>${selected ? `${plural(selected, "device")} selected` : `${plural(S.list.length, "device")}`}</span>
    ${check ? `<span>Checking ${check.done.toLocaleString()} of ${check.total.toLocaleString()}</span><button class="text-button" data-action="cancel-check">Stop</button>` : ""}
    <span class="spacer"></span>
    <span>Opening as <b>${esc(activeProfile().name || "")}</b></span>`;
  const strip = $("#check-strip");
  if (strip) {
    strip.hidden = !check;
    if (check) strip.firstElementChild.style.width = `${(check.done / Math.max(1, check.total)) * 100}%`;
  }
}

/* ---------------------------------------------------------------- selection */
function onRowMouseDown(e) {
  if (e.button !== 0) return;
  const row = e.target.closest(".row");
  if (!row) return;
  const i = +row.dataset.i;
  const device = S.list[i];
  if (e.target.closest("[data-action=star]")) return;
  if (e.shiftKey && S.anchor >= 0) {
    const [a, b] = [Math.min(S.anchor, i), Math.max(S.anchor, i)];
    if (!e.ctrlKey) S.selected.clear();
    for (let k = a; k <= b; k++) S.selected.add(S.list[k].key);
    S.cursor = i;
  } else if (e.ctrlKey || e.metaKey) {
    if (S.selected.has(device.key)) S.selected.delete(device.key); else S.selected.add(device.key);
    S.cursor = i; S.anchor = i;
  } else {
    if (S.selected.has(device.key) && S.selected.size > 1) {
      // Keep a multi-selection in case the user drags it onto a group; a plain click narrows it on mouseup.
      S.cursor = i;
      const up = () => { window.removeEventListener("mouseup", up); S.pendingUp = null; selectOnly(i); };
      S.pendingUp = up;
      window.addEventListener("mouseup", up);
      renderRows();
      return;
    }
    selectOnly(i, false);
  }
  $("#scroll").focus({ preventScroll: true });
  afterSelection();
}
function selectOnly(i, render = true) {
  S.selected.clear();
  if (i >= 0 && S.list[i]) S.selected.add(S.list[i].key);
  S.cursor = i; S.anchor = i;
  if (render) afterSelection();
}
function afterSelection() {
  renderRows();
  renderStatusLine();
  renderInspector();
}
function moveCursor(delta, extend = false) {
  if (!S.list.length) return;
  let i = S.cursor < 0 ? (delta > 0 ? 0 : S.list.length - 1) : S.cursor + delta;
  i = Math.max(0, Math.min(S.list.length - 1, i));
  if (extend) {
    if (S.anchor < 0) S.anchor = S.cursor < 0 ? i : S.cursor;
    S.selected.clear();
    const [a, b] = [Math.min(S.anchor, i), Math.max(S.anchor, i)];
    for (let k = a; k <= b; k++) S.selected.add(S.list[k].key);
    S.cursor = i;
  } else {
    selectOnly(i, false);
  }
  ensureVisible(i);
  afterSelection();
}
function ensureVisible(i) {
  const scroll = $("#scroll");
  if (!scroll) return;
  const top = i * ROW, bottom = top + ROW;
  if (top < scroll.scrollTop) scroll.scrollTop = top;
  else if (bottom > scroll.scrollTop + scroll.clientHeight) scroll.scrollTop = bottom - scroll.clientHeight;
}
function selectAllVisible() {
  S.selected = new Set(S.list.map(d => d.key));
  if (S.cursor < 0 && S.list.length) S.cursor = 0;
  afterSelection();
}

/* ================================================================ inspector */
function renderInspector() {
  const el = $("#inspector");
  $("#app").classList.toggle("wide", S.view !== "devices");
  if (S.view !== "devices") return;
  const devices = selectedDevices();
  if (devices.length === 0) el.innerHTML = overviewHtml();
  else if (devices.length === 1) el.innerHTML = deviceHtml(devices[0]);
  else el.innerHTML = multiHtml(devices);
  wireInspector(devices);
}
function overviewHtml() {
  const counts = { core: 0, distribution: 0, access: 0, other: 0 };
  let up = 0, down = 0;
  for (const d of S.devices) { counts[d.kind] = (counts[d.kind] || 0) + 1; if (d.reachability === "up") up++; if (d.reachability === "down") down++; }
  const total = Math.max(1, S.devices.length);
  return `<div class="overview">
    <h2>${plural(S.devices.length, "device")}</h2>
    <p>${up || down ? `${up.toLocaleString()} reachable and ${down.toLocaleString()} unreachable at their last check.` : "Select a device to see its details, or start typing to find one."}</p>
    <div class="role-bars">${KINDS.map(([k, label]) => `<div class="role-bar k-${k}"><span>${label}</span><b>${(counts[k] || 0).toLocaleString()}</b><div class="track"><i style="width:${((counts[k] || 0) / total) * 100}%"></i></div></div>`).join("")}</div>
    <div class="hint-list">
      <div><span>Find a device</span><kbd>Ctrl K</kbd></div>
      <div><span>Open the selected device</span><kbd>Enter</kbd></div>
      <div><span>Open every selected device</span><kbd>Ctrl Enter</kbd></div>
      <div><span>Copy IP address</span><kbd>Ctrl C</kbd></div>
      <div><span>Check connection</span><kbd>Ctrl T</kbd></div>
      <div><span>All shortcuts</span><kbd>?</kbd></div>
    </div></div>`;
}
function deviceHtml(d) {
  const diag = S.diag[d.key];
  const busy = S.diagBusy.has(d.key);
  const profile = activeProfile();
  const sourceName = d.source === "netbox" ? (S.netbox.connections.find(c => c.id === d.netbox_connection_id) || {}).name || "NetBox" : "";
  const logs = (S.logs || []).filter(l => l.ip === d.ip && l.port === d.port).slice(0, 5);
  const how = d.putty_session ? `Saved PuTTY session “${esc(d.putty_session)}”` : `${d.protocol === "telnet" ? "Telnet" : "SSH"} on port ${esc(d.port)}${d.protocol === "telnet" ? ". You sign in by hand on Telnet." : ""}`;
  const openLabel = d.putty_session ? "Open saved session" : d.protocol === "telnet" ? "Open Telnet session" : "Open SSH session";
  const twins = S.devices.filter(x => x.ip === d.ip && x.port === d.port && x.key !== d.key).length;
  const groups = S.groups.map(g => g.name);
  const checkRows = diag ? `
      <div class="check"><i class="led ${diag.port.ok ? "up" : "down"}"></i><b>Port ${esc(d.port)}</b><span>${esc(diag.port.detail)}</span></div>
      ${diag.ping ? `<div class="check"><i class="led ${diag.ping.ok ? "up" : "down"}"></i><b>Ping</b><span>${esc(diag.ping.detail)}</span></div>` : ""}
      ${diag.port.banner ? `<div class="check"><i></i><b>Banner</b><span class="banner-text selectable">${esc(diag.port.banner)}</span></div>` : ""}`
    : `<div class="check"><i class="led ${d.reachability || "unknown"}"></i><b>Status</b><span>${esc(statusTitle(d))}</span></div>`;
  return `<div class="k-${d.kind}"><div class="ins-band"></div>
    <div class="ins-head">
      <div class="ins-kind"><i class="cord"></i><span>${KIND_LABEL[d.kind]}${sourceName ? `, synced from ${esc(sourceName)}` : ""}${d.in_inventory === false ? ", not saved in your list" : ""}</span></div>
      <div class="ins-name">${esc(d.sysname)}</div>
      <div class="ins-addr">
        <span class="num">${esc(d.ip)}</span>
        <button class="icon-button ghost" data-action="copy" data-text="${esc(d.ip)}" data-what="IP address" title="Copy IP address (Ctrl+C)">${icon("copy")}</button>
        ${d.sysname !== d.ip ? `<button class="text-button" data-action="copy" data-text="${esc(d.sysname)}" data-what="Hostname" title="Copy hostname (Ctrl+Shift+C)">Copy hostname</button>` : ""}
      </div>
      <div class="ins-proto">${how}</div>
      <div class="ins-actions">
        <button class="go-button" data-action="open-selected" title="Enter">${icon("play")}<span>${openLabel}</span></button>
        <button class="icon-button ${d.favorite ? "on" : ""}" data-action="fav-toggle" title="${d.favorite ? "Remove from favorites" : "Add to favorites"} (Ctrl+D)">${icon("star")}</button>
        <button class="icon-button" data-action="device-menu" title="More actions">${icon("more")}</button>
      </div>
      ${d.protocol === "ssh" && !d.putty_session && !profile.has_password ? `<p class="faint" style="margin:10px 0 0;font-size:12.5px">${esc(profile.name)} has no saved password, so PuTTY will ask you to sign in.</p>` : ""}
      ${twins ? `<p class="faint" style="margin:10px 0 0;font-size:12.5px">${plural(twins, "other device")} in your list use${twins === 1 ? "s" : ""} the same IP address and port. Search for ${esc(d.ip)} to see ${twins === 1 ? "it" : "them"}.</p>` : ""}
    </div>
    <div class="ins-section">
      <h3>Connection check <button class="button" data-action="diagnose" ${busy ? "disabled" : ""} title="Ctrl+T">${icon("pulse")}<span>${busy ? "Checking…" : "Check now"}</span></button></h3>
      <div class="checks">${checkRows}</div>
      ${d.last_failure ? `<p class="failure" style="margin:10px 0 0;font-size:12.5px">Last failure ${esc(fmtTime(d.last_failure.slice(0, 19)).toLowerCase())}: ${esc(d.last_failure.slice(20))}</p>` : ""}
    </div>
    <div class="ins-section">
      <h3>Details</h3>
      <dl class="facts">
        <dt>Group</dt><dd><span class="inline-select"><select id="ins-group" aria-label="Group">
          <option value="">No group</option>${groups.map(g => `<option ${g === d.group ? "selected" : ""}>${esc(g)}</option>`).join("")}<option value="__new">New group…</option></select>${icon("chevron")}</span></dd>
        <dt>Tags</dt><dd><div class="tag-editor">${d.tags.map(t => `<span class="tag">${esc(t)}<button data-action="remove-tag" data-tag="${esc(t)}" title="Remove ${esc(t)}">${icon("x")}</button></span>`).join("")}<input id="ins-tag" placeholder="Add tag" list="tag-suggestions"><datalist id="tag-suggestions">${tagsInUse().map(t => `<option value="${esc(t)}">`).join("")}</datalist></div></dd>
        <dt>Last opened</dt><dd>${d.last_success ? esc(fmtTime(d.last_success)) : `<span class="faint">Never</span>`}</dd>
        <dt>Added by</dt><dd>${d.source === "netbox" ? "NetBox sync" : "You (manual, CSV or quick connect)"}</dd>
      </dl>
      <label class="field" style="margin-top:14px"><span>Notes</span><textarea class="notes" id="ins-notes" placeholder="Rack, site contact, change ticket…">${esc(d.notes)}</textarea></label>
    </div>
    <div class="ins-section">
      <h3>Session logs <small>${logs.length ? "" : profile.save_output ? "None yet" : "Logging is off"}</small></h3>
      ${logs.length ? `<div class="log-links">${logs.map(l => `<button class="log-link" data-action="open-log" data-path="${esc(l.path)}"><span>${esc(fmtTime(l.opened_at || l.modified))}</span><span>${fmtSize(l.size)}</span></button>`).join("")}</div>`
        : `<p class="faint" style="margin:0;font-size:12.5px">${profile.save_output ? "Logs appear here after you open a session." : `Turn on session logging in the ${esc(profile.name)} profile to keep a record of each session.`}</p>`}
    </div>
    <div class="ins-foot">
      <button class="button" data-action="edit-device" title="F2">${icon("edit")}<span>Edit</span></button>
      <button class="button danger" data-action="delete-selected" title="Delete">${icon("trash")}<span>Delete</span></button>
    </div></div>`;
}
function multiHtml(devices) {
  const allFav = devices.every(d => d.favorite);
  return `<div class="overview ins-multi">
    <h2>${plural(devices.length, "device")} selected</h2>
    <p>Act on all of them at once.</p>
    <div class="action-list">
      <button class="go-button" data-action="open-selected">${icon("play")}<span>Open ${plural(devices.length, "session")}</span></button>
      <button class="button" data-action="check-selected">${icon("pulse")}<span>Check reachability</span><kbd>Ctrl T</kbd></button>
      <button class="button" data-action="move-group">${icon("folder")}<span>Move to group</span></button>
      <button class="button" data-action="add-tag-bulk">${icon("tag")}<span>Add tag</span></button>
      <button class="button" data-action="fav-toggle">${icon("star")}<span>${allFav ? "Remove from favorites" : "Add to favorites"}</span><kbd>Ctrl D</kbd></button>
      <button class="button" data-action="copy-ips">${icon("copy")}<span>Copy IP addresses</span><kbd>Ctrl C</kbd></button>
      <button class="button" data-action="copy-names">${icon("copy")}<span>Copy hostnames</span><kbd>Ctrl Shift C</kbd></button>
      <button class="button" data-action="export-selected">${icon("download")}<span>Export to CSV</span></button>
      <button class="button danger" data-action="delete-selected">${icon("trash")}<span>Delete</span><kbd>Del</kbd></button>
    </div></div>`;
}
function wireInspector(devices) {
  if (devices.length !== 1) return;
  const d = devices[0];
  const group = $("#ins-group");
  if (group) group.addEventListener("change", async () => {
    let name = group.value;
    if (name === "__new") {
      name = await promptDialog({ title: "New group", label: "Group name", confirm: "Create and move" });
      if (!name) { renderInspector(); return; }
    }
    await setGroup([d.key], name);
  });
  const tagInput = $("#ins-tag");
  if (tagInput) tagInput.addEventListener("keydown", async e => {
    if (e.key === "Enter" && tagInput.value.trim()) {
      e.preventDefault();
      try {
        const result = await bridge.call("add_tags", [d.key], tagInput.value);
        upsertDevices(result.devices);
        refreshDevices(); renderInspector();
        setTimeout(() => $("#ins-tag") && $("#ins-tag").focus(), 0);
      } catch (error) { fail(error); }
    }
  });
  const notes = $("#ins-notes");
  if (notes) notes.addEventListener("blur", async () => {
    if (notes.value.trim() === (d.notes || "").trim()) return;
    try {
      const result = await bridge.call("update_notes", d.key, notes.value);
      upsertDevices([result.device]);
      toast({ title: "Notes saved", level: "ok", timeout: 1800 });
    } catch (error) { fail(error); }
  });
}

/* ================================================================ device actions */
async function launchKeys(keys) {
  if (!keys.length) return;
  if (keys.length > 8 && !(await confirmDialog({ title: `Open ${keys.length} sessions?`, body: `This opens ${keys.length} PuTTY windows at once.`, confirm: `Open ${keys.length} sessions` }))) return;
  try {
    let result;
    if (S.scope === "recent" && keys.length === 1 && !S.byKey.has(keys[0])) {
      const d = deviceFor(keys[0]);
      result = await bridge.call("launch_recent", d.ip, d.port, d.sysname);
    } else {
      result = await bridge.call("launch", keys);
    }
    if (result.devices) upsertDevices(result.devices);
    if (result.recent) S.recent = result.recent;
    const launched = result.launched || [];
    const first = launched[0];
    const note = first && first.prompt_login ? " PuTTY will ask you to sign in." : first && first.log ? " Logging this session." : "";
    toast({ title: launched.length === 1 ? `Opening ${first.sysname}` : `Opening ${launched.length} sessions`, body: `Using the ${first ? first.profile : ""} profile.${note}`, level: "ok", timeout: 2600 });
    (result.errors || []).forEach(message => toast({ title: "One session didn't open", body: message, level: "error" }));
    renderRail();
    refreshDevices();
    renderInspector();
    setTimeout(pollPutty, 1500);
    if (first && first.log) setTimeout(() => loadLogs(true), 2500);
  } catch (error) {
    fail(error, "Couldn't open PuTTY");
    if (/PuTTY wasn't found/.test(error.message)) editProfile(S.activeProfile);
  }
}
async function quickConnect() {
  const candidate = connectCandidate();
  if (!candidate) return;
  const portInput = $("#connect-slot input[name=port]");
  const port = portInput ? portInput.value.trim() : candidate.port;
  try {
    const result = await bridge.call("quick_connect", candidate.host, port, "");
    applyDevicePayload(result);
    const launched = result.launched[0];
    toast({ title: `Opening ${launched.sysname}`, body: result.created ? "Saved to your device list." : `Using the ${launched.profile} profile.`, level: "ok", timeout: 2600 });
    S.query = "";
    $("#omni").value = "";
    compute();
    const index = S.list.findIndex(d => d.key === result.device.key);
    renderRail();
    refreshDevices();
    if (index >= 0) { selectOnly(index); ensureVisible(index); }
    setTimeout(pollPutty, 1500);
  } catch (error) { fail(error, "Couldn't connect"); }
}
async function toggleFavorite(keys) {
  const devices = keys.map(deviceFor).filter(Boolean);
  if (!devices.length) return;
  const favorite = !devices.every(d => d.favorite);
  try {
    const result = await bridge.call("set_favorite", keys, favorite);
    upsertDevices(result.devices);
    renderRail(); refreshDevices(); renderInspector();
    toast({ title: favorite ? `Added ${devices.length === 1 ? devices[0].sysname : plural(devices.length, "device")} to favorites` : `Removed from favorites`, level: "ok", timeout: 1800 });
  } catch (error) { fail(error); }
}
async function setGroup(keys, name) {
  try {
    const result = await bridge.call("set_group", keys, name);
    applyDevicePayload(result);
    renderRail(); refreshDevices(); renderInspector();
    toast({ title: name ? `Moved ${plural(result.count, "device")} to ${name}` : `Removed ${plural(result.count, "device")} from its group`, level: "ok", timeout: 2200 });
  } catch (error) { fail(error); }
}
async function moveToGroupDialog(keys) {
  const name = await promptDialog({ title: `Move ${plural(keys.length, "device")} to a group`, intro: "Pick an existing group or type a new name.", label: "Group", suggestions: S.groups.map(g => g.name), confirm: "Move" });
  if (name) await setGroup(keys, name);
}
async function addTagDialog(keys) {
  const tags = await promptDialog({ title: `Tag ${plural(keys.length, "device")}`, intro: "Separate several tags with commas.", label: "Tags", suggestions: tagsInUse(), confirm: "Add tags" });
  if (!tags) return;
  try {
    const result = await bridge.call("add_tags", keys, tags);
    upsertDevices(result.devices);
    refreshDevices(); renderInspector();
  } catch (error) { fail(error); }
}
async function copyText(text, what) {
  try {
    const result = await bridge.call("copy_text", text);
    if (!result.copied && navigator.clipboard) await navigator.clipboard.writeText(text);
    toast({ title: `${what} copied`, body: text.length > 80 ? text.slice(0, 80) + "…" : text, level: "ok", timeout: 1600 });
  } catch (error) {
    try { await navigator.clipboard.writeText(text); toast({ title: `${what} copied`, level: "ok", timeout: 1600 }); } catch (_) { fail(error, "Couldn't copy"); }
  }
}
function copySelection(field) {
  const devices = selectedDevices();
  if (!devices.length) return;
  const text = devices.map(d => d[field]).join("\r\n");
  copyText(text, field === "ip" ? (devices.length > 1 ? "IP addresses" : "IP address") : (devices.length > 1 ? "Hostnames" : "Hostname"));
}
async function diagnose(key) {
  if (S.diagBusy.has(key)) return;
  S.diagBusy.add(key);
  renderInspector();
  try {
    const result = await bridge.call("diagnose_device", key);
    S.diag[key] = result.result;
    upsertDevices([result.device]);
    const status = result.result.status;
    if (status !== "up") toast({ title: `${result.device.sysname} is ${status === "filtered" ? "answering ping, but the port is closed" : "unreachable"}`, body: result.result.port.detail, level: status === "filtered" ? "warn" : "error" });
  } catch (error) { fail(error, "Check failed"); }
  S.diagBusy.delete(key);
  refreshDevices(); renderInspector();
}
async function checkKeys(keys) {
  if (!keys.length) return;
  if (keys.length === 1) return diagnose(keys[0]);
  if (keys.length > 600 && !(await confirmDialog({ title: `Check ${keys.length.toLocaleString()} devices?`, body: "Large checks can take a few minutes. You can keep working while it runs, or stop it at any time.", confirm: "Start check" }))) return;
  S.pendingCheckKeys = keys;
  try {
    await bridge.call("check_devices", keys);
  } catch (error) { S.pendingCheckKeys = null; fail(error); }
}
async function deleteKeys(keys) {
  const devices = keys.map(deviceFor).filter(Boolean);
  if (!devices.length) return;
  const netbox = devices.filter(d => d.source === "netbox").length;
  const ok = await confirmDialog({
    title: devices.length === 1 ? `Delete ${devices[0].sysname}?` : `Delete ${plural(devices.length, "device")}?`,
    body: `This removes ${devices.length === 1 ? "it" : "them"} from your device list. Session logs are kept.${netbox ? ` ${netbox === devices.length ? "NetBox devices" : `${netbox} NetBox device${netbox === 1 ? "" : "s"}`} come back on the next sync unless removed in NetBox.` : ""}`,
    confirm: "Delete", danger: true,
  });
  if (!ok) return;
  try {
    const result = await bridge.call("delete_devices", devices.map(d => d.key));
    applyDevicePayload(result);
    S.selected.clear();
    renderRail(); refreshDevices(); renderInspector();
    toast({ title: `Deleted ${plural(result.removed, "device")}`, level: "ok", timeout: 2200 });
  } catch (error) { fail(error); }
}
async function exportKeys(keys) {
  try {
    const result = await bridge.call("export_devices", keys && keys.length ? keys : null, null);
    if (!result.cancelled) toast({ title: `Exported ${plural(result.count, "device")}`, body: result.path, level: "ok" });
  } catch (error) { fail(error, "Export failed"); }
}
function showDeviceMenu(x, y) {
  const devices = selectedDevices();
  if (!devices.length) return;
  const keys = devices.map(d => d.key);
  const one = devices.length === 1 ? devices[0] : null;
  const allFav = devices.every(d => d.favorite);
  const items = [
    { label: one ? "Open session" : `Open ${devices.length} sessions`, icon: "play", kbd: one ? "Enter" : "Ctrl Enter", fn: () => launchKeys(keys) },
    "-",
    { label: one ? "Copy IP address" : "Copy IP addresses", icon: "copy", kbd: "Ctrl C", fn: () => copySelection("ip") },
    { label: one ? "Copy hostname" : "Copy hostnames", icon: "copy", kbd: "Ctrl Shift C", fn: () => copySelection("sysname") },
    "-",
    { label: one ? "Check connection" : "Check reachability", icon: "pulse", kbd: "Ctrl T", fn: () => checkKeys(keys) },
    { label: allFav ? "Remove from favorites" : "Add to favorites", icon: "star", kbd: "Ctrl D", fn: () => toggleFavorite(keys) },
    { label: "Move to group…", icon: "folder", fn: () => moveToGroupDialog(keys) },
  ];
  if (devices.some(d => d.group)) items.push({ label: "Remove from group", icon: "x", fn: () => setGroup(keys, "") });
  items.push({ label: "Add tag…", icon: "tag", fn: () => addTagDialog(keys) });
  if (one && one.in_inventory !== false) items.push({ label: "Edit device", icon: "edit", kbd: "F2", fn: () => editDevice(one.key) });
  items.push({ label: "Export to CSV…", icon: "download", fn: () => exportKeys(keys) });
  if (devices.every(d => d.in_inventory !== false)) items.push("-", { label: one ? "Delete device" : `Delete ${devices.length} devices`, icon: "trash", kbd: "Del", danger: true, fn: () => deleteKeys(keys) });
  openMenu(x, y, items);
}

/* ---------------------------------------------------------------- device form */
async function editDevice(key) {
  const d = key ? deviceFor(key) : null;
  if (S.sessions === null) {
    try { S.sessions = (await bridge.call("putty_sessions")).sessions; } catch (_) { S.sessions = []; }
  }
  const modal = openModal({
    title: d ? `Edit ${d.sysname}` : "Add a device",
    intro: d ? "" : "Port 23 opens Telnet. Any other port opens SSH.",
    body: `
      <label class="field"><span>Hostname</span><input name="sysname" value="${esc(d ? d.sysname : "")}" placeholder="For example bld2-acc-01"><small>Leave empty to use the IP address.</small></label>
      <div class="field-row ip-port">
        <label class="field"><span>IP address or DNS name</span><input name="ip" class="mono-num" value="${esc(d ? d.ip : "")}" placeholder="10.20.30.40"></label>
        <label class="field"><span>Port</span><input name="port" class="mono-num" value="${esc(d ? d.port : "22")}" inputmode="numeric"></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Group</span><input name="group" value="${esc(d ? d.group : (S.scope === "group" ? S.group : ""))}" list="group-options" placeholder="None"><datalist id="group-options">${S.groups.map(g => `<option value="${esc(g.name)}">`).join("")}</datalist></label>
        <label class="field"><span>Tags</span><input name="tags" value="${esc(d ? d.tags.join(", ") : "")}" placeholder="Comma separated" list="tag-options"><datalist id="tag-options">${tagsInUse().map(t => `<option value="${esc(t)}">`).join("")}</datalist></label>
      </div>
      <label class="field"><span>Saved PuTTY session</span><input name="putty_session" value="${esc(d ? d.putty_session : "")}" list="session-options" placeholder="None. Open with the profile's credentials">
        <datalist id="session-options">${(S.sessions || []).map(s => `<option value="${esc(s.name)}">${esc(s.host)}</option>`).join("")}</datalist>
        <small>${S.sessions && S.sessions.length ? `${plural(S.sessions.length, "saved session")} found in PuTTY.` : "Use a session saved in PuTTY to keep its own settings and keys."}</small></label>
      <label class="field"><span>Notes</span><textarea name="notes">${esc(d ? d.notes : "")}</textarea></label>
      <div class="form-error"></div>`,
    footer: `<button class="button" data-x="cancel">Cancel</button><button class="go-button" data-x="save">${d ? "Save changes" : "Add device"}</button>`,
    initialFocus: d ? "[name=sysname]" : "[name=ip]",
  });
  const save = async () => {
    const data = formData(modal);
    try {
      const result = await bridge.call("save_device", d ? d.key : "", data);
      applyDevicePayload(result);
      closeModal();
      compute();
      const index = S.list.findIndex(x => x.key === result.device.key);
      renderRail(); refreshDevices();
      if (index >= 0) { selectOnly(index); ensureVisible(index); } else renderInspector();
      toast({ title: d ? "Device saved" : `Added ${result.device.sysname}`, level: "ok", timeout: 2000 });
    } catch (error) { modal.querySelector(".form-error").textContent = error.message; }
  };
  modal.querySelector("[data-x=cancel]").onclick = closeModal;
  modal.querySelector("[data-x=save]").onclick = save;
  modal.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(); } });
}

/* ---------------------------------------------------------------- CSV import */
async function importCsv() {
  let preview;
  try { preview = await bridge.call("preview_import", null); } catch (error) { return fail(error, "Couldn't read the file"); }
  if (preview.cancelled) return;
  const blocked = preview.errors.length > 0;
  const modal = openModal({
    title: `Import ${preview.name}`, wide: true,
    intro: blocked ? "Some rows need fixing before anything is imported." : "Check the preview, then import. Devices already in your list are skipped.",
    body: `
      <div class="stat-line">
        <div><b>${preview.valid.toLocaleString()}</b><span>valid rows</span></div>
        <div><b>${preview.new.toLocaleString()}</b><span>new devices</span></div>
        <div><b>${preview.existing.toLocaleString()}</b><span>already in your list</span></div>
        <div><b>${preview.errors.length.toLocaleString()}</b><span>rows with problems</span></div>
      </div>
      ${blocked ? `<ul class="error-list">${preview.errors.slice(0, 50).map(e => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
      ${preview.sample.length ? `<table class="preview-table"><thead><tr><th>Hostname</th><th>IP address</th><th>Port</th><th>Group</th><th>Tags</th></tr></thead><tbody>
        ${preview.sample.map(d => `<tr><td>${esc(d.sysname)}</td><td class="num">${esc(d.ip)}</td><td class="num">${esc(d.port)}</td><td>${esc(d.group)}</td><td>${esc((d.tags || []).join(", "))}</td></tr>`).join("")}</tbody></table>` : ""}`,
    footer: `<button class="button" data-x="cancel">Cancel</button><button class="go-button" data-x="import" ${blocked || !preview.new ? "disabled" : ""}>${preview.new ? `Import ${plural(preview.new, "device")}` : "Nothing new to import"}</button>`,
  });
  modal.querySelector("[data-x=cancel]").onclick = closeModal;
  modal.querySelector("[data-x=import]").onclick = async e => withBusy(e.currentTarget, async () => {
    try {
      const result = await bridge.call("import_devices", preview.path);
      applyDevicePayload(result);
      closeModal();
      renderRail(); refreshDevices(); renderInspector();
      toast({ title: `Imported ${plural(result.added, "device")}`, level: "ok" });
    } catch (error) { fail(error, "Import failed"); }
  });
}

/* ================================================================ groups */
async function newGroup(keysToMove = null) {
  const name = await promptDialog({ title: "New group", intro: "Groups help you organise sites, buildings or customers.", label: "Group name", confirm: "Create group" });
  if (!name) return;
  try {
    if (keysToMove && keysToMove.length) await setGroup(keysToMove, name);
    else { S.groups = (await bridge.call("create_group", name)).groups; renderRail(); }
    goTo("devices", "group", name);
  } catch (error) { fail(error); }
}
function groupMenu(name, x, y) {
  openMenu(x, y, [
    { label: "Open group", icon: "folder", fn: () => goTo("devices", "group", name) },
    { label: "Rename group…", icon: "edit", fn: () => renameGroup(name) },
    { label: "Check reachability", icon: "pulse", fn: () => checkKeys(S.devices.filter(d => d.group === name).map(d => d.key)) },
    "-",
    { label: "Delete group", icon: "trash", danger: true, fn: () => deleteGroup(name) },
  ]);
}
async function renameGroup(name) {
  const next = await promptDialog({ title: `Rename ${name}`, label: "Group name", value: name, confirm: "Rename" });
  if (!next || next === name) return;
  try {
    applyDevicePayload(await bridge.call("rename_group", name, next));
    if (S.scope === "group" && S.group === name) S.group = next;
    renderRail(); buildAndRefresh();
  } catch (error) { fail(error); }
}
async function deleteGroup(name) {
  const count = S.devices.filter(d => d.group === name).length;
  const ok = await confirmDialog({ title: `Delete the ${name} group?`, body: count ? `The ${plural(count, "device")} in it stay in your list without a group.` : "The group is empty.", confirm: "Delete group", danger: true });
  if (!ok) return;
  try {
    applyDevicePayload(await bridge.call("delete_group", name));
    if (S.scope === "group" && S.group === name) { S.scope = "all"; S.group = ""; }
    renderRail(); buildAndRefresh();
  } catch (error) { fail(error); }
}

/* ================================================================ session logs */
async function loadLogs(force = false) {
  if (!force && S.logs && Date.now() - S.logsLoadedAt < 20000) return;
  try {
    const result = await bridge.call("list_logs");
    S.logs = result.logs;
    S.logFolders = result.folders;
    S.logsLoadedAt = Date.now();
    if (S.view === "logs") renderLogs();
    if (S.view === "devices" && S.selected.size === 1) renderInspector();
  } catch (error) { fail(error, "Couldn't list session logs"); }
}
function renderLogs() {
  const view = $("#view-logs");
  const profile = activeProfile();
  const logs = (S.logs || []).filter(l => {
    if (!S.logQuery) return true;
    return `${l.sysname} ${l.ip} ${l.profile} ${l.name}`.toLowerCase().includes(S.logQuery.toLowerCase());
  });
  const current = S.log;
  view.innerHTML = `<div class="page" style="flex:none;padding-bottom:0">
      <div class="page-head"><div><h1>Session logs</h1><p>${profile.save_output ? `PuTTY saves a log of every session opened with ${esc(profile.name)}.` : `Logging is off for ${esc(profile.name)}. Turn it on in the profile to save each session's output.`}</p></div>
      <div class="section-actions">
        ${profile.save_output ? "" : `<button class="button" data-action="profile-edit" data-id="${esc(profile.id)}">${icon("edit")}<span>Turn on logging</span></button>`}
        <button class="button" data-action="logs-folder">${icon("folder")}<span>Open log folder</span></button>
        <button class="button" data-action="logs-refresh">${icon("sync")}<span>Refresh</span></button></div></div></div>
    <div class="logs-layout">
      <div class="log-list">
        <div class="search"><input id="log-search" placeholder="Filter by hostname, IP or profile" value="${esc(S.logQuery)}"></div>
        <div class="log-items">${S.logs === null ? `<div class="activity-empty">Loading logs…</div>` : logs.length ? logs.slice(0, 400).map(l => `
          <button class="log-item ${current && current.path === l.path ? "on" : ""}" data-action="log-select" data-path="${esc(l.path)}">
            <strong>${esc(l.sysname || l.name)}</strong>
            <span><span>${esc(l.ip ? `${l.ip}:${l.port}` : l.folder)}</span><span>${esc(fmtTime(l.opened_at || l.modified))}, ${fmtSize(l.size)}</span></span>
          </button>`).join("") : `<div class="activity-empty">${S.logQuery ? "No logs match." : "No session logs yet."}</div>`}</div>
      </div>
      <div class="log-view">${current ? `
        <header><div><h2>${esc(current.sysname || current.name)}</h2><p>${esc(current.ip ? `${current.ip}:${current.port}, ` : "")}${esc(fmtTime(current.opened_at || current.modified))}, ${fmtSize(current.size)}${current.truncated ? ". Showing the last 400 KB" : ""}</p></div>
          <div class="log-find"><input id="log-find" placeholder="Find in log" value="${esc(S.logFind)}">
          <button class="button" data-action="log-notepad">${icon("external")}<span>Notepad</span></button>
          <button class="icon-button" data-action="log-reveal" title="Show in folder">${icon("folder")}</button></div></header>
        <pre class="log-text" id="log-text"></pre>` : `<div class="empty"><h2>Choose a log</h2><p>Pick a session on the left to read what happened. Use Find to search the output.</p></div>`}</div>
    </div>`;
  if (current) paintLogText();
  const search = $("#log-search");
  search.addEventListener("input", () => { S.logQuery = search.value; renderLogsKeepFocus("#log-search"); });
  const find = $("#log-find");
  if (find) find.addEventListener("input", () => { S.logFind = find.value; paintLogText(); });
}
function renderLogsKeepFocus(selector) {
  const el = $(selector);
  const pos = el ? el.selectionStart : 0;
  renderLogs();
  const next = $(selector);
  if (next) { next.focus(); next.setSelectionRange(pos, pos); }
}
function paintLogText() {
  const pre = $("#log-text");
  if (!pre || !S.log) return;
  const text = S.log.text || "";
  if (!S.logFind) { pre.textContent = text || "(This log is empty.)"; return; }
  const needle = S.logFind.toLowerCase();
  const lower = text.toLowerCase();
  let html = "", index = 0, hits = 0;
  while (true) {
    const found = lower.indexOf(needle, index);
    if (found < 0 || hits > 2000) break;
    html += esc(text.slice(index, found)) + `<mark>${esc(text.slice(found, found + needle.length))}</mark>`;
    index = found + needle.length; hits++;
  }
  html += esc(text.slice(index));
  pre.innerHTML = html;
  const first = pre.querySelector("mark");
  if (first) first.scrollIntoView({ block: "center" });
}
async function selectLog(path) {
  const meta = (S.logs || []).find(l => l.path === path) || { path, name: path.split(/[\\/]/).pop() };
  try {
    const result = await bridge.call("read_log", path);
    S.log = { ...meta, ...result };
    renderLogs();
    const pre = $("#log-text");
    if (pre && !S.logFind) pre.scrollTop = pre.scrollHeight;
  } catch (error) { fail(error, "Couldn't open the log"); }
}

/* ================================================================ NetBox page */
function renderNetbox() {
  const view = $("#view-netbox");
  const nb = S.netbox;
  const lastByConnection = {};
  for (const entry of nb.history) for (const c of entry.connections || []) if (!lastByConnection[c.id]) lastByConnection[c.id] = { ...c, at: entry.finished_at };
  const interval = +S.settings.sync_interval_minutes || 0;
  const next = nb.next_sync_at ? (new Date(nb.next_sync_at) <= new Date() ? "in the next minute" : fmtTime(nb.next_sync_at).toLowerCase()) : "";
  const enabledCount = nb.connections.filter(c => c.enabled).length;
  view.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>NetBox</h1><p>Pull active access, distribution and core switches into your device list. Devices you added yourself are never removed by a sync.</p></div>
      <div class="section-actions"><button class="button" data-action="nb-add">${icon("plus")}<span>Add connection</span></button>
      <button class="go-button" data-action="sync-netbox" ${nb.syncing || !enabledCount ? "disabled" : ""}>${icon("sync")}<span>${nb.syncing ? "Syncing…" : enabledCount === nb.connections.length ? "Sync now" : `Sync ${plural(enabledCount, "connection")}`}</span></button></div></div>

    <div class="section"><header><div><h2>Connections</h2><p>Switch a connection off to leave it out of manual and scheduled syncs.</p></div></header>
      <div class="item-list">${nb.connections.length ? nb.connections.map(c => {
        const last = lastByConnection[c.id];
        const test = S.nbTests[c.id];
        return `<div class="item">
          <label class="switch" title="${c.enabled ? "Included in sync" : "Left out of sync"}"><input type="checkbox" data-change="nb-toggle" data-id="${esc(c.id)}" ${c.enabled ? "checked" : ""}><i></i></label>
          <div><h3>${esc(c.name)} ${last ? (last.ok ? `<span class="pill go">${plural(last.count, "device")}</span>` : `<span class="pill bad">Failed</span>`) : `<span class="pill">Not synced yet</span>`}</h3>
            <p>${esc(c.url)}</p>
            <p>${esc(c.roles.map(r => r[0].toUpperCase() + r.slice(1)).join(", "))} switches${c.manufacturers ? ` from ${esc(c.manufacturers.split(",").join(", "))}` : ""}${c.sites ? ` at ${esc(c.sites.split(",").join(", "))}` : ", all sites"}. Status ${esc(c.status)}.</p>
            ${last && !last.ok ? `<p class="failure">${esc(last.error)}</p>` : ""}
            ${test ? `<p class="${test.ok ? "" : "failure"}">${esc(test.text)}</p>` : ""}</div>
          <div class="item-actions"><button class="button" data-action="nb-test" data-id="${esc(c.id)}">Test</button><button class="icon-button" data-action="nb-edit" data-id="${esc(c.id)}" title="Edit">${icon("edit")}</button><button class="icon-button" data-action="nb-delete" data-id="${esc(c.id)}" title="Delete">${icon("trash")}</button></div>
        </div>`;
      }).join("") : `<div class="empty" style="margin:0;padding:22px 18px"><h2>No NetBox connections</h2><p>Add your NetBox URL and an API token to keep your device list in step with NetBox.</p><div class="row-actions"><button class="go-button" data-action="nb-add">${icon("plus")}<span>Add connection</span></button></div></div>`}</div></div>

    <div class="section"><header><div><h2>Automatic sync</h2><p>Runs in the background while the launcher is open.</p></div></header>
      <div class="settings-row"><div><h3>Sync schedule</h3><p>${interval ? (next ? `Next sync ${next}.` : "Turn on a connection to schedule syncs.") : "Only when you press Sync."}${S.settings.last_sync_at ? ` Last successful sync ${fmtTime(S.settings.last_sync_at).toLowerCase()}.` : ""}</p></div>
        <span class="chip-select ${interval ? "set" : ""}"><select data-setting="sync_interval_minutes">${S.intervals.map(m => `<option value="${m}" ${m === interval ? "selected" : ""}>${INTERVAL_LABEL(m)}</option>`).join("")}</select>${icon("chevron")}</span></div>
      <div class="settings-row"><div><h3>Sync when the launcher opens</h3><p>Starts a sync a few seconds after the window appears.</p></div>
        <label class="switch"><input type="checkbox" data-setting="sync_on_startup" ${S.settings.sync_on_startup ? "checked" : ""}><i></i></label></div>
    </div>

    <div class="section"><header><div><h2>Sync history</h2><p>The last ${nb.history.length || 40} syncs.</p></div></header>
      ${nb.history.length ? `<table class="history"><thead><tr><th>When</th><th>Started by</th><th>Changes</th><th>Connections</th><th>Took</th></tr></thead><tbody>
        ${nb.history.map(h => {
          const failed = (h.connections || []).filter(c => !c.ok);
          return `<tr><td>${esc(fmtTime(h.finished_at))}</td><td>${esc({ manual: "You", scheduled: "Schedule", startup: "App start" }[h.trigger] || h.trigger)}</td>
            <td class="delta"><span class="plus">+${h.added}</span> added, ${h.updated} updated, <span class="minus">−${h.removed}</span> removed</td>
            <td>${(h.connections || []).length - failed.length} of ${(h.connections || []).length} succeeded${failed.length || h.error ? `<div class="errors">${failed.map(c => `${esc(c.name)}: ${esc(c.error)}`).join("<br>")}${h.error ? esc(h.error) : ""}</div>` : ""}</td>
            <td class="num">${esc(h.duration_s)} s</td></tr>`;
        }).join("")}</tbody></table>` : `<div class="section-body muted">No syncs yet.</div>`}
    </div></div>`;
}
async function editNetbox(id) {
  const existing = id ? S.netbox.connections.find(c => c.id === id) : null;
  let c = existing;
  if (!c) {
    const suggestion = await bridge.call("suggest_netbox_name");
    c = { ...suggestion.defaults, name: suggestion.name, enabled: true };
  }
  const roles = ["core", "distribution", "access"];
  const modal = openModal({
    title: existing ? `Edit ${existing.name}` : "Add a NetBox connection",
    intro: "Self-signed and internal certificates are accepted.",
    body: `
      <label class="field"><span>Name</span><input name="name" value="${esc(c.name)}"></label>
      <label class="field"><span>NetBox URL</span><input name="url" value="${esc(c.url)}" placeholder="https://netbox.example.net"></label>
      <label class="field"><span>API token</span><input name="api_key" type="password" placeholder="${existing && existing.has_key ? "Saved. Leave empty to keep it" : "Paste a read-only token"}" autocomplete="off"></label>
      <div class="field"><span>Device roles to sync</span><div class="checks-inline">${roles.map(r => `<label class="k-${r}"><input type="checkbox" name="roles" value="${r}" data-multi="1" ${c.roles.includes(r) ? "checked" : ""}><i class="cord"></i>${r[0].toUpperCase() + r.slice(1)}</label>`).join("")}</div>
        <small>Hostnames ending in -core, -dis or -acc decide the role. The NetBox role is used when a hostname has no suffix.</small></div>
      <div class="field-row">
        <label class="field"><span>Manufacturers</span><input name="manufacturers" value="${esc(c.manufacturers)}" placeholder="huawei, juniper"><small>NetBox slugs, comma separated.</small></label>
        <label class="field"><span>Sites</span><input name="sites" value="${esc(c.sites)}" placeholder="All sites"><small>Site slugs. Leave empty for every site.</small></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Device status</span><select name="status">${["active", "planned", "staged", "failed", "offline"].map(s => `<option ${s === c.status ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label class="field"><span>Skip these models</span><input name="ignored_models" value="${esc(c.ignored_models)}"></label>
      </div>
      <label class="check-field"><input type="checkbox" name="enabled" ${c.enabled ? "checked" : ""}><span>Include in syncs<small>Turn off to keep the connection without syncing it.</small></span></label>
      <div class="form-error"></div>`,
    footer: `<button class="button" data-x="cancel">Cancel</button><button class="go-button" data-x="save">${existing ? "Save changes" : "Add connection"}</button>`,
  });
  modal.querySelector("[data-x=cancel]").onclick = closeModal;
  modal.querySelector("[data-x=save]").onclick = async () => {
    const data = { ...formData(modal), id: existing ? existing.id : "" };
    data.roles = data.roles || [];
    try {
      const result = await bridge.call("save_netbox_connection", data);
      S.netbox = { ...S.netbox, ...result.netbox };
      closeModal();
      renderNetbox(); renderRail();
      toast({ title: existing ? "Connection saved" : "Connection added", body: "Use Test to check the URL and token.", level: "ok" });
    } catch (error) { modal.querySelector(".form-error").textContent = error.message; }
  };
}
async function testNetbox(id, button) {
  await withBusy(button, async () => {
    button.textContent = "Testing…";
    try {
      const result = await bridge.call("test_netbox_connection", id);
      S.nbTests[id] = { ok: true, text: `Connected to NetBox ${result.version || ""} in ${result.ms} ms. ${plural(result.count, "device")} match the manufacturer, site and status filters.` };
    } catch (error) {
      S.nbTests[id] = { ok: false, text: `Test failed: ${error.message}` };
    }
  });
  renderNetbox();
}
async function syncNetbox() {
  if (S.netbox.syncing) return;
  if (!S.netbox.connections.length) { goTo("netbox"); toast({ title: "Add a NetBox connection first", level: "info" }); return; }
  try {
    await bridge.call("sync_netbox", null, "manual");
    S.netbox.syncing = true;
    renderSyncButton();
    if (S.view === "netbox") renderNetbox();
  } catch (error) { fail(error, "Couldn't start the sync"); }
}

/* ================================================================ settings page */
function renderSettings() {
  const view = $("#view-settings");
  const swatches = {
    Slate: ["#dfe4ea", "#f8fafb", "#0d7658", "#ddaa00", "#0d9fb1"],
    Midnight: ["#0d141b", "#151f29", "#2db98a", "#f1c12e", "#2bc3d5"],
    Ocean: ["#d4e3e2", "#f6fafa", "#0a6f66", "#d6a400", "#0b98a8"],
    Graphite: ["#17181a", "#222427", "#e2a042", "#f0c23a", "#36c2cf"],
  };
  view.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Settings</h1><p>Profiles hold the credentials and PuTTY options used to open sessions. Everything is stored on this PC only.</p></div></div>

    <div class="section"><header><div><h2>Connection profiles</h2><p>Passwords are encrypted with your Windows account.</p></div>
      <div class="section-actions"><button class="button" data-action="profiles-import">${icon("upload")}<span>Import</span></button><button class="button" data-action="profiles-export">${icon("download")}<span>Export</span></button><button class="go-button" data-action="profile-add">${icon("plus")}<span>Add profile</span></button></div></header>
      <div class="item-list">${S.profiles.map(p => `<div class="item">
        <i class="led ${p.putty_found ? (p.id === S.activeProfile ? "up" : "unknown") : "down"}" title="${p.putty_found ? "" : "PuTTY not found at this path"}"></i>
        <div><h3>${esc(p.name)}${p.id === S.activeProfile ? `<span class="pill go">In use</span>` : ""}${p.id === S.defaultProfile ? `<span class="pill">Default</span>` : ""}</h3>
          <p>${p.username ? `Signs in as ${esc(p.username)}${p.has_password ? " with a saved password" : ", PuTTY asks for the password"}` : "No username saved"}. ${p.save_output ? `Logs sessions to <span title="${esc(p.log_directory)}">${esc(shortPath(p.log_directory))}</span>.` : "Session logging off."}</p>
          ${p.putty_found ? "" : `<p class="failure">PuTTY wasn't found at ${esc(p.putty_path)}.</p>`}</div>
        <div class="item-actions">
          ${p.id === S.activeProfile ? "" : `<button class="button" data-action="profile-use" data-id="${esc(p.id)}">Use</button>`}
          <button class="icon-button" data-action="profile-edit" data-id="${esc(p.id)}" title="Edit">${icon("edit")}</button>
          <button class="icon-button" data-action="profile-more" data-id="${esc(p.id)}" title="More">${icon("more")}</button></div></div>`).join("")}</div></div>

    <div class="section"><header><div><h2>Appearance</h2><p>Role colours follow fibre and copper cable jackets: yellow core, aqua distribution, blue access.</p></div></header>
      <div class="themes">${S.themes.map(t => `<button class="theme-card ${t.name === S.theme ? "on" : ""}" data-action="theme" data-theme="${esc(t.name)}">
        <div class="swatch"><i style="background:${swatches[t.name][0]}"></i><div style="background:${swatches[t.name][1]}"><b style="background:${swatches[t.name][2]};width:70%"></b><b style="background:${swatches[t.name][3]};width:45%"></b><b style="background:${swatches[t.name][4]};width:58%"></b></div></div>
        <span>${esc(t.name)}</span><small>${esc(t.description)}</small></button>`).join("")}</div></div>

    <div class="section"><header><div><h2>Opening sessions</h2></div></header>
      <div class="settings-row"><div><h3>Check the device when a session opens</h3><p>Tests the port alongside PuTTY and shows a notification if the device can't be reached or PuTTY closes with an error.</p></div>
        <label class="switch"><input type="checkbox" data-setting="preflight_check" ${S.settings.preflight_check ? "checked" : ""}><i></i></label></div>
      <div class="settings-row"><div><h3>Connection check timeout</h3><p>How long a ping or port test waits for an answer.</p></div>
        <span class="chip-select"><select data-setting="diagnostic_timeout">${[1, 2, 3, 5, 8, 10].map(s => `<option value="${s}" ${+S.settings.diagnostic_timeout === s ? "selected" : ""}>${s} second${s === 1 ? "" : "s"}</option>`).join("")}</select>${icon("chevron")}</span></div>
    </div>

    <div class="section"><header><div><h2>Device list</h2><p>${plural(S.devices.length, "device")}, ${plural(S.groups.length, "group")}.</p></div>
      <div class="section-actions"><button class="button" data-action="import-csv">${icon("upload")}<span>Import CSV</span></button><button class="button" data-action="export-csv">${icon("download")}<span>Export CSV</span></button><button class="button" data-action="clear-recent">Clear recently opened</button></div></header></div>

    <div class="section"><header><div><h2>Backup</h2><p>Profiles, NetBox connections, groups and preferences in one file. Passwords in a backup only open on this Windows account.</p></div>
      <div class="section-actions"><button class="button" data-action="backup">${icon("download")}<span>Back up settings</span></button><button class="button" data-action="restore">${icon("upload")}<span>Restore</span></button></div></header></div>

    <div class="section"><header><div><h2>Updates and data</h2><p>You have version ${esc(S.app.version)}. Your data is in <span title="${esc(S.app.data_dir)}">${esc(shortPath(S.app.data_dir))}</span>.</p></div>
      <div class="section-actions"><button class="button" data-action="open-data-folder">${icon("folder")}<span>Open data folder</span></button><button class="button" data-action="check-updates">${icon("sync")}<span>Check for updates</span></button></div></header></div>

    <div class="section"><header><div><h2>Keyboard shortcuts</h2><p>Press ? anywhere to see these.</p></div></header>${shortcutGrid()}</div>
  </div>`;
}
const SHORTCUTS = [
  ["Find a device or type an IP", "Ctrl K"], ["Open the selected device", "Enter"],
  ["Open every selected device", "Ctrl Enter"], ["Move through the list", "↑ ↓"],
  ["Extend the selection", "Shift ↑ ↓"], ["Select all shown", "Ctrl A"],
  ["Copy IP address", "Ctrl C"], ["Copy hostname", "Ctrl Shift C"],
  ["Check connection", "Ctrl T"], ["Add or remove favorite", "Ctrl D"],
  ["Edit device", "F2"], ["Delete device", "Del"],
  ["Add a device", "Ctrl N"], ["Import a CSV", "Ctrl I"],
  ["Sync NetBox", "F5"], ["Switch profile", "Ctrl Shift P"],
  ["All devices, favorites, recent", "Ctrl 1 2 3"], ["Logs, NetBox, settings", "Ctrl 4 5 6"],
  ["Close all PuTTY windows", "Ctrl Shift Q"], ["Clear search or close", "Esc"],
];
function shortcutGrid() {
  return `<div class="shortcut-grid">${SHORTCUTS.map(([what, keys]) => `<div><span>${esc(what)}</span><kbd>${esc(keys)}</kbd></div>`).join("")}</div>`;
}
async function editProfile(id) {
  const existing = id ? S.profiles.find(p => p.id === id) : null;
  let p = existing;
  if (!p) {
    const s = await bridge.call("suggest_profile_name");
    p = { id: "", name: s.name, username: "", has_password: false, putty_path: s.putty_path, save_output: false, log_directory: s.log_directory };
  }
  const modal = openModal({
    title: existing ? `Edit ${existing.name}` : "Add a profile",
    intro: "SSH sessions sign in with these credentials. Telnet sessions always ask you to sign in.",
    body: `
      <label class="field"><span>Profile name</span><input name="name" value="${esc(p.name)}"></label>
      <div class="field-row">
        <label class="field"><span>TACACS username</span><input name="username" value="${esc(p.username)}" autocomplete="off"></label>
        <label class="field"><span>Password</span><input name="password" type="password" placeholder="${p.has_password ? "Saved. Leave empty to keep it" : "Optional"}" autocomplete="new-password"></label>
      </div>
      ${p.has_password ? `<label class="check-field"><input type="checkbox" name="clear_password"><span>Forget the saved password<small>PuTTY will ask for it each time.</small></span></label>` : ""}
      <label class="field"><span>PuTTY program</span><div class="with-button"><input name="putty_path" value="${esc(p.putty_path)}"><button class="button" data-x="browse-putty">Browse</button></div></label>
      <label class="check-field"><input type="checkbox" name="save_output" ${p.save_output ? "checked" : ""}><span>Save a log of each session<small>PuTTY writes the session output to a dated file you can read under Session logs.</small></span></label>
      <label class="field"><span>Log folder</span><div class="with-button"><input name="log_directory" value="${esc(p.log_directory)}"><button class="button" data-x="browse-logs">Browse</button></div></label>
      <div class="form-error"></div>`,
    footer: `<button class="button" data-x="cancel">Cancel</button><button class="go-button" data-x="save">${existing ? "Save profile" : "Add profile"}</button>`,
  });
  const browse = async (kind, name) => {
    try {
      const result = await bridge.call("pick_path", kind, modal.querySelector(`[name=${name}]`).value);
      if (result.path) modal.querySelector(`[name=${name}]`).value = result.path;
    } catch (error) { modal.querySelector(".form-error").textContent = error.message; }
  };
  modal.querySelector("[data-x=browse-putty]").onclick = () => browse("putty", "putty_path");
  modal.querySelector("[data-x=browse-logs]").onclick = () => browse("folder", "log_directory");
  modal.querySelector("[data-x=cancel]").onclick = closeModal;
  modal.querySelector("[data-x=save]").onclick = async () => {
    const data = { ...formData(modal), id: existing ? existing.id : "" };
    try {
      const result = await bridge.call("save_profile", data);
      S.profiles = result.profiles; S.activeProfile = result.active_profile_id; S.defaultProfile = result.default_profile_id;
      closeModal();
      renderAll();
      toast({ title: existing ? "Profile saved" : `Added ${result.profile.name}`, body: existing ? "" : "New sessions now open with this profile.", level: "ok" });
    } catch (error) { modal.querySelector(".form-error").textContent = error.message; }
  };
}
async function exportProfilesDialog() {
  const modal = openModal({
    title: "Export profiles",
    intro: "Move profiles to another PC. Passwords are left out unless you include them.",
    body: `<div class="field"><span>Profiles</span>${S.profiles.map(p => `<label class="check-field"><input type="checkbox" name="ids" value="${esc(p.id)}" data-multi="1" checked><span>${esc(p.name)}<small>${esc(p.username || "No username")}</small></span></label>`).join("")}</div>
      <label class="check-field"><input type="checkbox" name="include_passwords"><span>Include saved passwords<small>They are written to the file as plain text. Only use this for a file you'll keep private.</small></span></label>
      <div class="form-error"></div>`,
    footer: `<button class="button" data-x="cancel">Cancel</button><button class="go-button" data-x="save">${icon("download")}<span>Export</span></button>`,
  });
  modal.querySelector("[data-x=cancel]").onclick = closeModal;
  modal.querySelector("[data-x=save]").onclick = async () => {
    const data = formData(modal);
    if (!data.ids || !data.ids.length) { modal.querySelector(".form-error").textContent = "Choose at least one profile."; return; }
    try {
      const result = await bridge.call("export_profiles", data.ids, data.include_passwords, null);
      if (result.cancelled) return;
      closeModal();
      toast({ title: `Exported ${plural(result.count, "profile")}`, body: result.path, level: "ok" });
    } catch (error) { modal.querySelector(".form-error").textContent = error.message; }
  };
}

/* ================================================================ navigation */
function goTo(view, scope = null, group = "") {
  closeMenu();
  const viewChanged = view !== S.view || (view === "devices" && (scope || S.scope) !== S.scope) || (scope === "group" && group !== S.group);
  S.view = view;
  if (view === "devices") {
    if (scope) S.scope = scope;
    if (scope === "group") S.group = group;
    if (viewChanged) { S.selected.clear(); S.cursor = -1; S.anchor = -1; if (S.scope === "recent") S.sort.explicit = false; }
  }
  for (const name of ["devices", "logs", "netbox", "settings"]) $(`#view-${name}`).hidden = name !== view;
  renderRail();
  if (view === "devices") { if (viewChanged || !$("#scroll")) buildDevicesView(); refreshDevices({ keepScroll: !viewChanged }); }
  if (view === "logs") { renderLogs(); loadLogs(); }
  if (view === "netbox") renderNetbox();
  if (view === "settings") renderSettings();
  renderInspector();
}
function buildAndRefresh() {
  if (S.view === "devices") { buildDevicesView(); refreshDevices(); renderInspector(); }
}
function renderAll() {
  document.body.dataset.theme = S.theme;
  renderRail();
  if (S.view === "devices") refreshDevices();
  if (S.view === "logs") renderLogs();
  if (S.view === "netbox") renderNetbox();
  if (S.view === "settings") renderSettings();
  renderInspector();
}

/* ================================================================ click and change handlers */
const ACTIONS = {
  "go": el => goTo(el.dataset.view, el.dataset.scope || null),
  "group": el => goTo("devices", "group", el.dataset.group),
  "new-group": () => newGroup(),
  "group-menu": el => { const r = el.getBoundingClientRect(); groupMenu(S.group, r.left, r.bottom + 4); },
  "close-all-putty": () => closeAllPutty(),
  "sync-netbox": () => syncNetbox(),
  "toggle-activity": () => toggleActivity(),
  "activity-clear": async () => { await bridge.call("clear_activity"); S.activity = []; renderActivity(); },
  "shortcuts": () => showShortcuts(),
  "star": el => { const row = el.closest(".row"); if (row) toggleFavorite([S.list[+row.dataset.i].key]); },
  "sort": el => {
    const col = el.dataset.col;
    if (S.sort.col === col && (S.sort.explicit || col === "sysname")) S.sort.dir *= -1; else { S.sort.col = col; S.sort.dir = col === "last" || col === "opened" ? -1 : 1; }
    S.sort.explicit = true;
    refreshDevices();
  },
  "kind": el => { S.filters.kind = el.dataset.kind; S.cursor = -1; refreshDevices({ keepScroll: false }); },
  "reset-filters": () => { S.filters = { kind: "all", protocol: "any", source: "any", status: "any", tag: "any" }; refreshDevices({ keepScroll: false }); },
  "connect": () => quickConnect(),
  "add-device": () => editDevice(null),
  "import-csv": () => importCsv(),
  "export-csv": () => exportKeys(null),
  "export-selected": () => exportKeys([...S.selected]),
  "check-visible": () => {
    const keys = S.selected.size > 1 ? [...S.selected] : S.list.map(d => d.key);
    checkKeys(keys);
  },
  "check-selected": () => checkKeys([...S.selected]),
  "cancel-check": () => bridge.call("cancel_check"),
  "open-selected": () => launchKeys([...S.selected]),
  "copy": el => copyText(el.dataset.text, el.dataset.what || "Text"),
  "copy-ips": () => copySelection("ip"),
  "copy-names": () => copySelection("sysname"),
  "diagnose": () => { const d = selectedDevices()[0]; if (d) diagnose(d.key); },
  "fav-toggle": () => toggleFavorite([...S.selected]),
  "move-group": () => moveToGroupDialog([...S.selected]),
  "add-tag-bulk": () => addTagDialog([...S.selected]),
  "edit-device": () => { const d = selectedDevices()[0]; if (d) editDevice(d.key); },
  "delete-selected": () => deleteKeys([...S.selected]),
  "device-menu": el => { const r = el.getBoundingClientRect(); showDeviceMenu(r.left - 160, r.bottom + 4); },
  "remove-tag": async el => {
    const d = selectedDevices()[0];
    try { const result = await bridge.call("remove_tags", [d.key], el.dataset.tag); upsertDevices(result.devices); refreshDevices(); renderInspector(); } catch (error) { fail(error); }
  },
  "open-log": el => { goTo("logs"); selectLog(el.dataset.path); },
  "clear-recent": async () => {
    if (!(await confirmDialog({ title: "Clear recently opened?", body: "This only empties the Recently opened list. Devices and logs stay.", confirm: "Clear list" }))) return;
    try { S.recent = (await bridge.call("clear_recent")).recent; renderRail(); if (S.view === "devices") refreshDevices(); toast({ title: "Recently opened cleared", level: "ok", timeout: 1800 }); } catch (error) { fail(error); }
  },
  "logs-refresh": () => loadLogs(true),
  "logs-folder": async () => { try { await bridge.call("open_folder", "logs"); } catch (error) { fail(error); } },
  "log-select": el => selectLog(el.dataset.path),
  "log-notepad": async () => { try { await bridge.call("open_log", S.log.path); } catch (error) { fail(error); } },
  "log-reveal": async () => { try { await bridge.call("reveal_log", S.log.path); } catch (error) { fail(error); } },
  "nb-add": () => editNetbox(null),
  "nb-edit": el => editNetbox(el.dataset.id),
  "nb-test": el => testNetbox(el.dataset.id, el),
  "nb-delete": async el => {
    const c = S.netbox.connections.find(x => x.id === el.dataset.id);
    if (!(await confirmDialog({ title: `Delete ${c.name}?`, body: "Devices it synced stay in your list.", confirm: "Delete connection", danger: true }))) return;
    try { S.netbox = { ...S.netbox, ...(await bridge.call("delete_netbox_connection", c.id)).netbox }; renderNetbox(); renderRail(); } catch (error) { fail(error); }
  },
  "profile-add": () => editProfile(null),
  "profile-edit": el => editProfile(el.dataset.id),
  "profile-use": el => switchProfile(el.dataset.id),
  "profile-more": el => {
    const p = S.profiles.find(x => x.id === el.dataset.id);
    const r = el.getBoundingClientRect();
    openMenu(r.left - 170, r.bottom + 4, [
      { label: "Make default", icon: "check", fn: async () => { try { const res = await bridge.call("set_default_profile", p.id); S.profiles = res.profiles; S.defaultProfile = res.default_profile_id; renderSettings(); } catch (error) { fail(error); } } },
      { label: "Export this profile…", icon: "download", fn: async () => { try { const res = await bridge.call("export_profiles", [p.id], false, null); if (!res.cancelled) toast({ title: "Profile exported", body: res.path, level: "ok" }); } catch (error) { fail(error); } } },
      "-",
      { label: "Delete profile", icon: "trash", danger: true, fn: async () => {
        if (!(await confirmDialog({ title: `Delete ${p.name}?`, body: "Its saved password is removed too. Session logs stay on disk.", confirm: "Delete profile", danger: true }))) return;
        try { const res = await bridge.call("delete_profile", p.id); S.profiles = res.profiles; S.activeProfile = res.active_profile_id; S.defaultProfile = res.default_profile_id; renderAll(); } catch (error) { fail(error); }
      } },
    ]);
  },
  "profiles-import": async () => {
    try {
      const res = await bridge.call("import_profiles", null);
      if (res.cancelled) return;
      S.profiles = res.profiles; S.activeProfile = res.active_profile_id; S.defaultProfile = res.default_profile_id;
      renderAll();
      toast({ title: `Imported ${plural(res.imported.length, "profile")}`, body: res.imported.join(", "), level: "ok" });
    } catch (error) { fail(error, "Import failed"); }
  },
  "profiles-export": () => exportProfilesDialog(),
  "theme": async el => {
    try { S.theme = (await bridge.call("set_theme", el.dataset.theme)).theme; document.body.dataset.theme = S.theme; renderSettings(); } catch (error) { fail(error); }
  },
  "backup": async () => { try { const res = await bridge.call("backup_settings", null); if (!res.cancelled) toast({ title: "Settings backed up", body: res.path, level: "ok" }); } catch (error) { fail(error, "Backup failed"); } },
  "restore": async () => {
    if (!(await confirmDialog({ title: "Restore settings from a backup?", body: "Profiles, NetBox connections, groups and preferences are replaced by the backup. Your device list stays as it is.", confirm: "Choose backup" }))) return;
    try { const res = await bridge.call("restore_settings", null); if (res.cancelled) return; await loadState(); toast({ title: "Settings restored", level: "ok" }); } catch (error) { fail(error, "Restore failed"); }
  },
  "check-updates": async el => withBusy(el, async () => {
    try {
      const res = await bridge.call("check_updates");
      toast({ title: res.message, level: res.available ? "info" : "ok", action: res.available ? { label: "Download", fn: () => bridge.call("open_url", res.url) } : null, timeout: res.available ? 15000 : 4000 });
    } catch (error) { fail(error, "Update check failed"); }
  }),
  "open-data-folder": async () => { try { await bridge.call("open_folder", "data"); } catch (error) { fail(error); } },
};
document.addEventListener("click", e => {
  const el = e.target.closest("[data-action]");
  if (!$("#menu").hidden && !e.target.closest("#menu")) closeMenu();
  if (!$("#activity-panel").hidden && !e.target.closest("#activity-panel") && !e.target.closest("[data-action=toggle-activity]")) $("#activity-panel").hidden = true;
  if (!el) return;
  const fn = ACTIONS[el.dataset.action];
  if (fn) { e.preventDefault(); fn(el, e); }
});
document.addEventListener("change", async e => {
  const el = e.target;
  if (el.dataset.filter) { S.filters[el.dataset.filter] = el.value; S.cursor = -1; refreshDevices({ keepScroll: false }); return; }
  if (el.dataset.setting) {
    const value = el.type === "checkbox" ? el.checked : +el.value;
    try {
      const result = await bridge.call("update_settings", { [el.dataset.setting]: value });
      S.settings = result.settings;
      S.netbox = { ...S.netbox, ...result.netbox };
      if (S.view === "netbox") renderNetbox();
      toast({ title: "Setting saved", level: "ok", timeout: 1400 });
    } catch (error) { fail(error); }
    return;
  }
  if (el.dataset.change === "nb-toggle") {
    try { S.netbox = { ...S.netbox, ...(await bridge.call("set_netbox_enabled", el.dataset.id, el.checked)).netbox }; renderNetbox(); } catch (error) { fail(error); }
  }
});
$("#profile-select").addEventListener("change", e => switchProfile(e.target.value));
async function switchProfile(id) {
  try {
    S.activeProfile = (await bridge.call("set_active_profile", id)).active_profile_id;
    const p = activeProfile();
    toast({ title: `Now opening sessions as ${p.name}`, body: p.username ? `Signs in as ${p.username}.` : "", level: "ok", timeout: 2200 });
    renderAll();
  } catch (error) { fail(error); }
}
async function closeAllPutty() {
  await pollPutty();
  if (!S.puttyCount) { toast({ title: "No PuTTY windows are open", level: "info" }); return; }
  const ok = await confirmDialog({ title: `Close all ${plural(S.puttyCount, "PuTTY window")}?`, body: "Every running PuTTY window closes immediately, including ones you opened outside the launcher. Unsaved terminal work is lost.", confirm: "Close all", danger: true });
  if (!ok) return;
  try {
    const result = await bridge.call("close_all_putty");
    toast({ title: `Closed ${plural(result.closed, "PuTTY window")}`, body: result.failed.length ? `Couldn't close ${result.failed.length}.` : "", level: result.failed.length ? "warn" : "ok" });
  } catch (error) { fail(error); }
  pollPutty();
}
function toggleActivity() {
  const panel = $("#activity-panel");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) { S.unread = 0; renderActivityBadge(); renderActivity(); }
}
function showShortcuts() {
  if (modalStack.length) return;
  const modal = openModal({ title: "Keyboard shortcuts", wide: true, body: shortcutGrid(), footer: `<button class="go-button" data-x="ok">Done</button>` });
  modal.querySelector("[data-x=ok]").onclick = closeModal;
}

/* group drop targets and group context menu */
const groupList = $("#group-list");
groupList.addEventListener("dragover", e => {
  const item = e.target.closest(".group-item");
  if (!item || !e.dataTransfer.types.includes("text/x-device-keys")) return;
  e.preventDefault();
  $$(".group-item.drop").forEach(x => x !== item && x.classList.remove("drop"));
  item.classList.add("drop");
});
groupList.addEventListener("dragleave", e => { const item = e.target.closest(".group-item"); if (item) item.classList.remove("drop"); });
groupList.addEventListener("drop", e => {
  const item = e.target.closest(".group-item");
  if (!item) return;
  e.preventDefault();
  item.classList.remove("drop");
  const keys = JSON.parse(e.dataTransfer.getData("text/x-device-keys") || "[]");
  if (keys.length) setGroup(keys, item.dataset.group);
});
groupList.addEventListener("contextmenu", e => {
  const item = e.target.closest(".group-item");
  if (!item) return;
  e.preventDefault();
  groupMenu(item.dataset.group, e.clientX, e.clientY);
});
const favNav = $('.nav-item[data-scope="favorites"]');
favNav.addEventListener("dragover", e => { if (e.dataTransfer.types.includes("text/x-device-keys")) { e.preventDefault(); favNav.classList.add("drop"); } });
favNav.addEventListener("dragleave", () => favNav.classList.remove("drop"));
favNav.addEventListener("drop", async e => {
  e.preventDefault(); favNav.classList.remove("drop");
  const keys = JSON.parse(e.dataTransfer.getData("text/x-device-keys") || "[]");
  if (!keys.length) return;
  try { upsertDevices((await bridge.call("set_favorite", keys, true)).devices); renderRail(); refreshDevices(); renderInspector(); toast({ title: `Added ${plural(keys.length, "device")} to favorites`, level: "ok", timeout: 1800 }); } catch (error) { fail(error); }
});

/* ================================================================ omnibar and keyboard */
const omni = $("#omni");
omni.addEventListener("input", () => {
  S.query = omni.value;
  if (S.view !== "devices") goTo("devices", S.scope === "recent" || S.scope === "favorites" || S.scope === "group" ? S.scope : "all");
  compute();
  S.selected.clear();
  if (S.list.length && S.query.trim()) selectOnly(0, false); else { S.cursor = -1; S.anchor = -1; }
  refreshDevices({ keepScroll: false });
  renderInspector();
});
omni.addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { e.preventDefault(); moveCursor(1, e.shiftKey); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveCursor(-1, e.shiftKey); }
  else if (e.key === "Enter") { e.preventDefault(); handleEnter(e.ctrlKey); }
  else if (e.key === "Escape") {
    e.preventDefault();
    if (omni.value) { omni.value = ""; S.query = ""; S.selected.clear(); S.cursor = -1; refreshDevices({ keepScroll: false }); renderInspector(); }
    else omni.blur();
  }
});
function handleEnter(all) {
  if (connectCandidate()) { quickConnect(); return; }
  if (all && S.selected.size) { launchKeys([...S.selected]); return; }
  const target = S.list[S.cursor] || (S.selected.size === 1 ? deviceFor([...S.selected][0]) : null);
  if (target) launchKeys([target.key]);
}
function typing(e) {
  const t = e.target;
  return t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable;
}
document.addEventListener("keydown", e => {
  const key = e.key;
  const ctrl = e.ctrlKey || e.metaKey;
  // Browser shortcuts that would reload, print or search the page.
  if (key === "F5" || (ctrl && (key === "r" || key === "R"))) { e.preventDefault(); if (!modalStack.length) syncNetbox(); return; }
  if (ctrl && (key === "p" || key === "P") && !e.shiftKey) { e.preventDefault(); return; }
  if (ctrl && (key === "f" || key === "F" || key === "k" || key === "K")) { e.preventDefault(); if (!modalStack.length) { omni.focus(); omni.select(); } return; }
  if (key === "Escape") {
    if (!$("#menu").hidden) { closeMenu(); return; }
    if (modalStack.length) { closeModal(); return; }
    if (!$("#activity-panel").hidden) { $("#activity-panel").hidden = true; return; }
    if (!typing(e) && S.selected.size) { S.selected.clear(); S.cursor = -1; afterSelection(); }
    return;
  }
  if (modalStack.length) return;
  if (!$("#menu").hidden) {
    const buttons = $$("#menu button");
    const index = buttons.indexOf(document.activeElement);
    if (key === "ArrowDown" || key === "ArrowUp") { e.preventDefault(); const next = buttons[(index + (key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]; if (next) next.focus(); }
    return;
  }
  if (ctrl && e.shiftKey && (key === "P" || key === "p")) { e.preventDefault(); cycleProfile(); return; }
  if (ctrl && e.shiftKey && (key === "Q" || key === "q")) { e.preventDefault(); closeAllPutty(); return; }
  if (ctrl && ["1", "2", "3", "4", "5", "6"].includes(key)) {
    e.preventDefault();
    const targets = [["devices", "all"], ["devices", "favorites"], ["devices", "recent"], ["logs"], ["netbox"], ["settings"]];
    goTo(...targets[+key - 1]);
    return;
  }
  if (ctrl && (key === "n" || key === "N")) { e.preventDefault(); editDevice(null); return; }
  if (ctrl && (key === "i" || key === "I")) { e.preventDefault(); importCsv(); return; }
  const inField = typing(e) && e.target !== omni;
  if (inField) return;
  if (key === "?" && e.target !== omni) { e.preventDefault(); showShortcuts(); return; }
  if (S.view !== "devices") return;
  const inOmni = e.target === omni;
  // Keys that act on the list only when focus is on the list (or nowhere in particular), so buttons keep Enter and Space.
  const onList = e.target === document.body || e.target.id === "scroll" || !!e.target.closest("#rows");
  if ((key === "ArrowDown" || key === "ArrowUp") && (onList || inOmni)) { e.preventDefault(); moveCursor(key === "ArrowDown" ? 1 : -1, e.shiftKey); return; }
  if (onList && (key === "Home" || key === "End")) { e.preventDefault(); moveCursor(key === "Home" ? -1e9 : 1e9, e.shiftKey); return; }
  if (onList && (key === "PageDown" || key === "PageUp")) { e.preventDefault(); const step = Math.max(1, Math.floor(($("#scroll").clientHeight || 400) / ROW) - 1); moveCursor(key === "PageDown" ? step : -step, e.shiftKey); return; }
  if (key === "Enter" && onList) { e.preventDefault(); handleEnter(ctrl); return; }
  if (ctrl && (key === "a" || key === "A") && onList) { e.preventDefault(); selectAllVisible(); return; }
  if (ctrl && (key === "c" || key === "C")) {
    if (inOmni && omni.selectionStart !== omni.selectionEnd) return;
    if (!S.selected.size) return;
    if (String(window.getSelection())) return;
    e.preventDefault(); copySelection(e.shiftKey ? "sysname" : "ip"); return;
  }
  if (!S.selected.size) return;
  if (ctrl && (key === "t" || key === "T")) { e.preventDefault(); checkKeys([...S.selected]); return; }
  if (ctrl && (key === "d" || key === "D")) { e.preventDefault(); toggleFavorite([...S.selected]); return; }
  if ((key === "F2" || (ctrl && (key === "e" || key === "E"))) && S.selected.size === 1) { e.preventDefault(); editDevice([...S.selected][0]); return; }
  if (key === "Delete" && onList) { e.preventDefault(); deleteKeys([...S.selected]); return; }
  if (key === "ContextMenu" || (e.shiftKey && key === "F10")) {
    e.preventDefault();
    const row = $("#rows .row.cursor") || $("#rows .row.selected");
    const rect = row ? row.getBoundingClientRect() : { left: 300, bottom: 200 };
    showDeviceMenu(rect.left + 120, rect.bottom);
  }
});
function cycleProfile() {
  if (S.profiles.length < 2) { toast({ title: "Only one profile", body: "Add another under Settings to switch between them.", level: "info" }); return; }
  const index = S.profiles.findIndex(p => p.id === S.activeProfile);
  switchProfile(S.profiles[(index + 1) % S.profiles.length].id);
}
document.addEventListener("contextmenu", e => {
  if (typing(e) || e.target.closest(".selectable, .log-text, .ins-name, .num")) return;
  e.preventDefault();
});

/* ================================================================ events from Python */
bridge.on("notify", item => {
  S.activity.unshift(item);
  S.activity = S.activity.slice(0, 100);
  if ($("#activity-panel").hidden) S.unread++;
  renderActivityBadge();
  renderActivity();
  if (item.level === "error" || item.level === "warn" || /sync|check/i.test(item.title)) {
    toast({ title: item.title, body: item.body, level: item.level, timeout: item.level === "error" ? 12000 : 5000,
      action: item.key && S.byKey.has(item.key) ? { label: "Show", fn: () => revealDevice(item.key) } : null });
  }
});
bridge.on("device_updated", ({ device }) => {
  upsertDevices([device]);
  if (S.view === "devices") { renderRows(); if (S.selected.has(device.key)) renderInspector(); }
});
bridge.on("check_started", ({ total }) => {
  S.check = { total, done: 0, pending: new Set(S.pendingCheckKeys || []) };
  S.pendingCheckKeys = null;
  if (S.view === "devices") { renderStatusLine(); renderRows(); }
});
bridge.on("check_progress", ({ done, total, devices }) => {
  upsertDevices(devices);
  if (!S.check) S.check = { total, done, pending: new Set() };
  S.check.done = done; S.check.total = total;
  for (const d of devices) S.check.pending.delete(d.key);
  if (S.view === "devices") { renderRows(); renderStatusLine(); }
});
bridge.on("check_done", () => {
  S.check = null;
  if (S.view === "devices") { refreshDevices(); renderInspector(); }
});
bridge.on("sync_started", () => { S.netbox.syncing = true; renderSyncButton(); if (S.view === "netbox") renderNetbox(); });
bridge.on("sync_finished", payload => {
  S.netbox = { ...S.netbox, ...payload.netbox, syncing: false };
  applyDevicePayload(payload);
  S.settings.last_sync_at = payload.netbox.last_sync_at;
  renderRail();
  if (S.view === "devices") refreshDevices();
  if (S.view === "netbox") renderNetbox();
  renderInspector();
});
function revealDevice(key) {
  goTo("devices", "all");
  S.query = ""; omni.value = "";
  S.filters = { kind: "all", protocol: "any", source: "any", status: "any", tag: "any" };
  compute();
  const index = S.list.findIndex(d => d.key === key);
  refreshDevices();
  if (index >= 0) { selectOnly(index); ensureVisible(index); }
}

/* ================================================================ startup */
async function pollPutty() {
  try { S.puttyCount = (await bridge.call("putty_status")).count; renderPutty(); } catch (_) { /* not fatal */ }
}
async function loadState() {
  const state = await bridge.call("get_state");
  S.app = state.app; S.theme = state.theme; S.themes = state.themes;
  S.profiles = state.profiles; S.activeProfile = state.active_profile_id; S.defaultProfile = state.default_profile_id;
  setDevices(state.devices); S.groups = state.groups; S.recent = state.recent;
  S.netbox = state.netbox; S.settings = state.settings; S.intervals = state.sync_intervals;
  S.activity = state.activity || [];
  document.body.dataset.theme = S.theme;
  if (state.load_errors && state.load_errors.length) {
    toast({ title: `Skipped ${plural(state.load_errors.length, "row")} in your device file`, body: state.load_errors[0], level: "warn", timeout: 10000 });
  }
  goTo(S.view, S.view === "devices" ? S.scope : null, S.group);
}
(async function start() {
  try {
    await bridge.ready();
    await loadState();
    $("#boot").remove();
    omni.focus();
    pollPutty();
    setInterval(pollPutty, 6000);
    loadLogs();
    setInterval(() => { if (S.view === "devices") renderStatusLine(); }, 60000);
  } catch (error) {
    $("#boot").innerHTML = `<div class="empty"><h2>The launcher couldn't start</h2><p>${esc(error.message)}</p><button class="go-button" onclick="location.reload()">Try again</button></div>`;
  }
})();
