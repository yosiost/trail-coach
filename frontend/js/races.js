/* Trail Coach — multi-race switcher + management.
   Loaded after app.js/settings.js; references their globals at runtime only. */

function _escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Fetch all races and refresh both surfaces: the sidebar switcher and the
// Settings manager. Returns the race list.
async function loadRaces() {
  let races = [];
  try {
    const res = await fetch("/api/races");
    const data = await res.json();
    if (Array.isArray(data)) races = data;
  } catch (e) { /* leave empty */ }
  renderRaceSwitcher(races);
  renderRacesManager(races);
  return races;
}

function renderRaceSwitcher(races) {
  const wrap = document.getElementById("race-switcher");
  const sel = document.getElementById("race-select");
  if (!wrap || !sel) return;
  // Only worth showing once there's a choice to make.
  if (races.length < 2) { wrap.hidden = true; sel.innerHTML = ""; return; }
  wrap.hidden = false;
  sel.innerHTML = races.map(r =>
    `<option value="${r.id}"${r.is_active ? " selected" : ""}>${_escapeHtml(r.race_name)}</option>`
  ).join("");
}

function renderRacesManager(races) {
  const host = document.getElementById("races-list");
  if (!host) return;
  if (races.length === 0) {
    host.innerHTML = '<div class="race-empty">No races yet — add one below.</div>';
    return;
  }
  host.innerHTML = races.map(r => `
    <div class="race-row${r.is_active ? " active" : ""}">
      <div class="race-meta">
        <span class="race-name">${_escapeHtml(r.race_name)}</span>
        <span class="race-sub">${_escapeHtml(r.race_date)} · ${_escapeHtml(r.distance_km)}km</span>
      </div>
      ${r.is_active
        ? '<span class="race-badge">current</span>'
        : `<button class="race-activate" data-race="${r.id}">Switch</button>`}
    </div>`).join("");
}

// Activate a race server-side, then refresh the switcher and reload every view.
async function switchRace(raceId) {
  try {
    const res = await fetch(`/api/races/${raceId}/activate`, { method: "POST" });
    if (!res.ok) return;
  } catch (e) { return; }
  await loadRaces();
  if (typeof reloadForActiveRace === "function") await reloadForActiveRace();
}

async function submitNewRace(ev) {
  ev.preventDefault();
  const status = document.getElementById("new-race-status");
  const val = id => (document.getElementById(id).value || "").trim();
  const checked = id => document.getElementById(id).checked;
  status.style.color = "var(--muted)"; status.textContent = "Creating…";
  try {
    const res = await fetch("/api/races", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        race_name: val("nr-name"), race_date: val("nr-date"),
        distance_km: val("nr-dist"), vert_m: val("nr-vert"),
        aspirational_time: val("nr-asp") || "0:00",
        realistic_min_time: val("nr-lo") || "0:00",
        realistic_max_time: val("nr-hi") || "0:00",
        copy: { fuel: checked("nr-copy-fuel"), plan: checked("nr-copy-plan") },
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Create failed");
    status.style.color = "var(--done)"; status.textContent = "Added ✓";
    ev.target.reset();
    const details = document.getElementById("new-race-details");
    if (details) details.open = false;
    // The new race is now active server-side — refresh and load into it.
    await loadRaces();
    if (typeof reloadForActiveRace === "function") await reloadForActiveRace();
  } catch (e) {
    status.style.color = "var(--missed)"; status.textContent = e.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const sel = document.getElementById("race-select");
  if (sel) sel.addEventListener("change", () => switchRace(sel.value));

  // Delegated: "Switch" buttons in the Settings races list.
  const list = document.getElementById("races-list");
  if (list) list.addEventListener("click", (e) => {
    const btn = e.target.closest(".race-activate");
    if (btn) switchRace(btn.dataset.race);
  });

  const form = document.getElementById("new-race-form");
  if (form) form.addEventListener("submit", submitNewRace);
});
