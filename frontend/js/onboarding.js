// First-run onboarding wizard. Shown by app.js when /api/config/status reports
// the instance is not yet onboarded. Writes each step to /api/onboarding/*.
function renderOnboarding() {
  const S = { activity_source: "manual" };
  let step = 0;
  let examples = { plans: [], courses: [] };
  const STEPS = ["Profile", "Goal", "Plan", "Course", "Activities", "Coaching"];

  const root = document.createElement("div");
  root.id = "onboarding";
  document.body.appendChild(root);

  fetch("/api/onboarding/examples")
    .then(r => (r.ok ? r.json() : null))
    .then(e => { if (e) { examples = e; if (step === 2 || step === 3) render(); } })
    .catch(() => {});

  const val = id => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
  const num = id => { const v = val(id); return v === "" ? null : Number(v); };
  const checked = group => (document.querySelector(`input[name="ob-${group}"]:checked`) || {}).value;

  async function post(path, body, isForm) {
    const opts = isForm
      ? { method: "POST", body }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  function choice(group, value, title, desc, checkedFirst) {
    return `<label class="ob-choice${checkedFirst ? " sel" : ""}">
      <input type="radio" name="ob-${group}" value="${value}"${checkedFirst ? " checked" : ""}>
      <span><span class="ob-choice-t">${title}</span><br><span class="ob-choice-d">${desc}</span></span></label>`;
  }

  // ── Step bodies + submit handlers ─────────────────────────────────────────
  function bodyProfile() {
    return `<h2>About you</h2>
      <p class="ob-hint">Helps the coach personalise. Everything here is optional.</p>
      <label>Name</label><input id="ob-name" placeholder="Your name">
      <div class="ob-row">
        <div><label>Weight (kg)</label><input id="ob-weight" type="number" inputmode="decimal" placeholder="70"></div>
        <div><label>Height (cm)</label><input id="ob-height" type="number" inputmode="decimal" placeholder="175"></div>
      </div>
      <div class="ob-row">
        <div><label>Max HR (bpm)</label><input id="ob-maxhr" type="number" inputmode="numeric" placeholder="185"></div>
        <div><label>Units</label><select id="ob-units"><option value="km">km</option><option value="mi">miles</option></select></div>
        <div><label>Week starts</label><select id="ob-weekstart"><option value="monday">Monday</option><option value="sunday">Sunday</option></select></div>
      </div>
      <label>Background</label><textarea id="ob-bg" placeholder="e.g. recreational runner moving up to trail ultras"></textarea>`;
  }
  async function submitProfile() {
    await post("/api/onboarding/profile", {
      name: val("ob-name"), weight_kg: num("ob-weight"), height_cm: num("ob-height"),
      max_hr: num("ob-maxhr"), background: val("ob-bg"), units: val("ob-units") || "km",
      week_start: val("ob-weekstart") || "monday",
    });
  }

  function bodyGoal() {
    return `<h2>Your goal race</h2>
      <p class="ob-hint">The coach anchors everything to this. Times as H:MM:SS.</p>
      <label>Race name</label><input id="ob-race" placeholder="Skyline 50K">
      <div class="ob-row">
        <div><label>Date</label><input id="ob-date" type="date"></div>
        <div><label>Distance (km)</label><input id="ob-dist" type="number" inputmode="decimal" placeholder="50"></div>
        <div><label>Vert (m)</label><input id="ob-vert" type="number" inputmode="numeric" placeholder="2200"></div>
      </div>
      <div class="ob-row">
        <div><label>Goal time</label><input id="ob-asp" placeholder="6:30:00"></div>
        <div><label>Realistic (fast)</label><input id="ob-lo" placeholder="6:15:00"></div>
        <div><label>Realistic (slow)</label><input id="ob-hi" placeholder="7:00:00"></div>
      </div>
      <label>Notes</label><input id="ob-gnotes" placeholder="start time, wave, anything worth noting">`;
  }
  async function submitGoal() {
    await post("/api/onboarding/goal", {
      race_name: val("ob-race"), race_date: val("ob-date"),
      distance_km: num("ob-dist") || 0, vert_m: num("ob-vert") || 0,
      aspirational_time: val("ob-asp"), realistic_min_time: val("ob-lo"), realistic_max_time: val("ob-hi"),
      notes: val("ob-gnotes"),
    });
  }

  function bodyPlan() {
    const opts = examples.plans.map(p => `<option value="${p}">${p}</option>`).join("");
    return `<h2>Training plan</h2>
      <p class="ob-hint">A weekly CSV plan. Upload your own, start from an example, or skip.</p>
      <div class="ob-choices">
        ${choice("plan", "example", "Use an example plan", "A ready-made generic plan you can edit later.", true)}
        ${choice("plan", "upload", "Upload a CSV", "Columns: Week, Date, Phase, Session, …")}
        ${choice("plan", "skip", "Skip for now", "Add a plan later.")}
      </div>
      <div id="ob-plan-example"><label>Example</label><select id="ob-plan-ex">${opts}</select></div>
      <div id="ob-plan-upload" class="ob-hidden"><label>Plan CSV</label><input id="ob-plan-file" type="file" accept=".csv"></div>`;
  }
  async function submitPlan() {
    const m = checked("plan");
    if (m === "skip") return post("/api/onboarding/plan", { skip: true });
    if (m === "example") return post("/api/onboarding/plan", { example: val("ob-plan-ex") });
    const f = document.getElementById("ob-plan-file").files[0];
    if (!f) throw new Error("Choose a CSV file to upload.");
    const fd = new FormData(); fd.append("file", f);
    return post("/api/onboarding/plan", fd, true);
  }

  function bodyCourse() {
    const opts = examples.courses.map(c => `<option value="${c}">${c}</option>`).join("");
    return `<h2>Race course</h2>
      <p class="ob-hint">Optional. Gives the coach the elevation profile, aid stations and cutoffs.</p>
      <div class="ob-choices">
        ${choice("course", "example", "Use an example course", "A generic course profile.", true)}
        ${choice("course", "minimal", "Generic race (distance + vert)", "No segment detail.")}
        ${choice("course", "upload", "Upload course JSON", "Same shape as the examples.")}
        ${choice("course", "skip", "Skip for now", "")}
      </div>
      <div id="ob-course-example"><label>Example</label><select id="ob-course-ex">${opts}</select></div>
      <div id="ob-course-minimal" class="ob-hidden">
        <label>Race name</label><input id="ob-c-race" placeholder="Skyline 50K">
        <div class="ob-row">
          <div><label>Distance (km)</label><input id="ob-c-dist" type="number" inputmode="decimal"></div>
          <div><label>Vert (m)</label><input id="ob-c-vert" type="number" inputmode="numeric"></div>
        </div>
      </div>
      <div id="ob-course-upload" class="ob-hidden"><label>Course JSON</label><input id="ob-course-file" type="file" accept=".json,application/json"></div>`;
  }
  async function submitCourse() {
    const m = checked("course");
    if (m === "skip") return post("/api/onboarding/course", { skip: true });
    if (m === "example") return post("/api/onboarding/course", { example: val("ob-course-ex") });
    if (m === "minimal") return post("/api/onboarding/course", {
      minimal: { race: val("ob-c-race"), distance_km: num("ob-c-dist") || 0, vert_m: num("ob-c-vert") || 0 },
    });
    const f = document.getElementById("ob-course-file").files[0];
    if (!f) throw new Error("Choose a JSON file to upload.");
    let course;
    try { course = JSON.parse(await f.text()); } catch (e) { throw new Error("That file isn't valid JSON."); }
    return post("/api/onboarding/course", { course });
  }

  function bodySource() {
    return `<h2>Activity source</h2>
      <p class="ob-hint">Where completed runs come from. You can wire this up later.</p>
      <div class="ob-choices">
        ${choice("src", "manual", "Manual entry", "No account needed — log runs yourself.", true)}
        ${choice("src", "strava", "Strava", "Connect via your own Strava API app.")}
        ${choice("src", "garmin", "Garmin", "Advanced / legacy.")}
      </div>`;
  }
  async function submitSource() { S.activity_source = checked("src"); }

  let coachEd = null, dietEd = null;
  function bodyMethod() {
    return `<h2>Coaching style</h2>
      <p class="ob-hint">Set your coach and dietitian. Keep the generic default, describe what you want and let the AI draft it, or write your own.</p>
      <h3 class="ob-persona-h">🏃 Coach</h3><div id="ob-coach-ed"></div>
      <h3 class="ob-persona-h">🥗 Dietitian</h3><div id="ob-diet-ed"></div>`;
  }
  async function submitMethod() {
    const coach = coachEd.getConfig();      // throws (caught by onNext) if invalid
    const diet = dietEd.getConfig();
    await post("/api/persona/config", { persona: "coach", ...coach });
    await post("/api/persona/config", { persona: "dietitian", ...diet });
    await post("/api/onboarding/complete", { activity_source: S.activity_source });
  }

  const BODIES = [bodyProfile, bodyGoal, bodyPlan, bodyCourse, bodySource, bodyMethod];
  const SUBMITS = [submitProfile, submitGoal, submitPlan, submitCourse, submitSource, submitMethod];

  // ── Conditional field visibility ──────────────────────────────────────────
  const show = (id, on) => { const el = document.getElementById(id); if (el) el.classList.toggle("ob-hidden", !on); };
  function toggleConditionals() {
    if (step === 2) { const m = checked("plan"); show("ob-plan-example", m === "example"); show("ob-plan-upload", m === "upload"); }
    if (step === 3) { const m = checked("course"); show("ob-course-example", m === "example"); show("ob-course-minimal", m === "minimal"); show("ob-course-upload", m === "upload"); }
  }

  function render() {
    const dots = STEPS.map((_, i) => `<span class="${i <= step ? "done" : ""}"></span>`).join("");
    const isLast = step === STEPS.length - 1;
    root.innerHTML = `<div class="ob-card">
        <h1><span class="logo">⛰️ Trail Coach</span></h1>
        <p class="ob-sub">Set up your coach — step ${step + 1} of ${STEPS.length}: ${STEPS[step]}</p>
        <div class="ob-steps">${dots}</div>
        <div class="ob-body">${BODIES[step]()}</div>
        <div class="ob-err" id="ob-err"></div>
        <div class="ob-actions">
          <button class="ob-back" id="ob-back"${step === 0 ? ' style="visibility:hidden"' : ""}>Back</button>
          <button class="ob-next" id="ob-next">${isLast ? "Finish" : "Next"}</button>
        </div>
      </div>`;
    root.querySelectorAll(".ob-choice input").forEach(inp => {
      inp.addEventListener("change", () => {
        root.querySelectorAll(`input[name="${inp.name}"]`).forEach(x => x.closest(".ob-choice").classList.toggle("sel", x.checked));
        toggleConditionals();
      });
    });
    toggleConditionals();
    if (step === 5) {  // Coaching step: mount the persona editors
      coachEd = renderPersonaEditor("coach", { mode: "generic" });
      dietEd = renderPersonaEditor("dietitian", { mode: "generic" });
      document.getElementById("ob-coach-ed").appendChild(coachEd.el);
      document.getElementById("ob-diet-ed").appendChild(dietEd.el);
    }
    document.getElementById("ob-back").onclick = () => { if (step > 0) { step--; render(); } };
    document.getElementById("ob-next").onclick = onNext;
  }

  async function onNext() {
    const btn = document.getElementById("ob-next");
    const err = document.getElementById("ob-err");
    err.textContent = "";
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "Saving…";
    try {
      await SUBMITS[step]();
      if (step === STEPS.length - 1) { location.reload(); return; }
      step++;
      render();
    } catch (e) {
      err.textContent = e.message || "Something went wrong.";
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  render();
}
