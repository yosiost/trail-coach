// Settings panel — race/goal editor + coach/dietitian persona config.
// Split out of app.js; loaded after it (references its globals at runtime only).
// ── Settings: coach & dietitian personas ─────────────────────────
let settingsLoaded = false;
async function loadSettingsGoal() {
  const form = document.getElementById("settings-goal-form");
  if (!form) return;
  try {
    const g = await (await fetch("/api/goal")).json();
    if (!g || g.error) return;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ""; };
    set("sg-name", g.race_name); set("sg-date", g.race_date);
    set("sg-dist", g.distance_km); set("sg-vert", g.vert_m);
    set("sg-asp", g.aspirational_time_hms); set("sg-lo", g.realistic_min_hms); set("sg-hi", g.realistic_max_hms);
  } catch (e) { /* no goal yet */ }
  const status = document.getElementById("settings-goal-status");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    status.textContent = ""; status.style.color = "var(--muted)";
    const val = id => document.getElementById(id).value.trim();
    try {
      const res = await fetch("/api/goal", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          race_name: val("sg-name"), race_date: val("sg-date"),
          distance_km: val("sg-dist"), vert_m: val("sg-vert"),
          aspirational_time: val("sg-asp"), realistic_min_time: val("sg-lo"), realistic_max_time: val("sg-hi"),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Save failed");
      status.style.color = "var(--done)"; status.textContent = "Saved ✓";
      // dependent views may change (race plan / this week / progress)
      raceLoaded = false; weekLoaded = false; profileLoaded = false;
      if (typeof loadGoal === "function") loadGoal().then(syncRaceTargetTime);
    } catch (e) { status.style.color = "var(--missed)"; status.textContent = e.message; }
  };
}

async function renderSettings() {
  await loadSettingsGoal();
  let cfg = { coach: { mode: "generic", text: "" }, dietitian: { mode: "generic", text: "" } };
  try { cfg = await (await fetch("/api/persona/config")).json(); } catch (e) { /* use defaults */ }
  const coachEd = renderPersonaEditor("coach", cfg.coach);
  const dietEd = renderPersonaEditor("dietitian", cfg.dietitian);
  const coachHost = document.getElementById("settings-coach");
  const dietHost = document.getElementById("settings-diet");
  coachHost.innerHTML = ""; dietHost.innerHTML = "";
  coachHost.appendChild(coachEd.el); dietHost.appendChild(dietEd.el);

  const status = document.getElementById("settings-status");
  document.getElementById("settings-save").onclick = async () => {
    status.textContent = "";
    let coach, diet;
    try { coach = coachEd.getConfig(); diet = dietEd.getConfig(); }
    catch (e) { status.textContent = e.message; return; }
    try {
      for (const [persona, c] of [["coach", coach], ["dietitian", diet]]) {
        const res = await fetch("/api/persona/config", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ persona, ...c }),
        });
        if (!res.ok) throw new Error((await res.json()).error || "Save failed");
      }
      status.textContent = "Saved ✓";
    } catch (e) { status.textContent = e.message; }
  };
  settingsLoaded = true;
}
