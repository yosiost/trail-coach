/* Trail Coach — frontend logic */

// ── Panel switching ──────────────────────────────────────────────
function switchPanel(target) {
  document.querySelectorAll(".nav-item, .tab-item").forEach(n => {
    n.classList.toggle("active", n.dataset.panel === target);
  });
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  document.getElementById(`panel-${target}`).classList.add("active");
  if (target === "week" && !weekLoaded) populateWeekSelect().then(() => loadWeek());
  if (target === "plan" && !planLoaded) loadPlan();
  if (target === "progress") loadProgress();
  if (target === "course" && !courseLoaded) renderCourse();
  if (target === "race" && !raceLoaded) renderRace();
  if (target === "profile" && !profileLoaded) renderProfile();
  if (target === "trails" && !trailsLoaded) renderLocations();
  if (target === "settings" && !settingsLoaded) renderSettings();
}

// ── Profile: course facts + cutoffs (data-driven from /api/course) ──
let profileLoaded = false;
async function renderProfile() {
  try { await loadCourseData(); } catch (e) { /* no course */ }
  const meta = document.getElementById("profile-meta");
  const wrap = document.getElementById("profile-img-wrap");
  if (!COURSE_DATA) { if (meta) meta.textContent = "No course configured yet — add one during onboarding."; profileLoaded = true; return; }
  const c = COURSE_DATA;
  if (meta) meta.innerHTML = `<strong>${c.distance_km}km · ${c.vert_m}m↑</strong>${c.high_point ? ` · High point: ${c.high_point}` : ""}${c.route ? `<br><span style="color:var(--muted)">${c.route}</span>` : ""}`;
  const cutoffs = c.cutoffs || [];
  if (wrap) wrap.innerHTML = cutoffs.length
    ? `<table class="trail-table"><thead><tr><th>Checkpoint</th><th>Cutoff</th></tr></thead><tbody>${cutoffs.map(x => `<tr><td>${x.point}</td><td>${x.time}</td></tr>`).join("")}</tbody></table>`
    : "";
  profileLoaded = true;
}

// ── Locations: the athlete's training-location knowledge base ──────
let trailsLoaded = false;
async function renderLocations() {
  const host = document.getElementById("locations-list");
  if (!host) { trailsLoaded = true; return; }
  let refs = [];
  try { const r = await fetch("/api/references?category=trails"); if (r.ok) refs = await r.json(); } catch (e) { /* none */ }
  const items = (Array.isArray(refs) ? refs : []).filter(r => r.name !== "course_profile");
  host.innerHTML = items.length
    ? items.map(r => `<div class="loc-card"><h3>${r.name.replace(/_/g, " ")}</h3><p>${r.content}</p></div>`).join("")
    : `<p class="course-source">No training locations recorded yet — tell the coach about a spot in chat (e.g. "my weekend long run is a 12km loop with 600m of climbing") and it remembers.</p>`;
  trailsLoaded = true;
}


document.querySelectorAll(".nav-item, .tab-item").forEach(item => {
  item.addEventListener("click", () => switchPanel(item.dataset.panel));
});

// ── Chat (sessions stored in localStorage — survives server restarts) ─────
const messagesEl      = document.getElementById("chat-messages");
const inputEl         = document.getElementById("chat-input");
const sendBtn         = document.getElementById("chat-send");
const sessionListEl   = document.getElementById("session-list");
const chatSidebar     = document.getElementById("chat-sidebar");
const sidebarOverlay  = document.getElementById("chat-sidebar-overlay");
const btnNewChat      = document.getElementById("btn-new-chat");
const btnHistory      = document.getElementById("btn-history");
const btnCloseSidebar = document.getElementById("btn-close-sidebar");
const personaBtns     = document.querySelectorAll(".persona-btn");

const STORAGE_KEY = "trail-chat-sessions";

const PERSONA_LABELS     = { coach: "Coach", dietitian: "Dietitian", analyst: "Analyst" };
const PERSONA_PLACEHOLDER = { coach: "Ask your coach…", dietitian: "Ask your dietitian…", analyst: "Ask your analyst…" };
const PERSONA_TAGS       = { coach: "🏃 Coach", dietitian: "🥗 Dietitian", analyst: "📊 Analyst" };

// In-memory session list (metadata only; no messages). Loaded from server.
let _sessions = [];
// Full message history for the active session.
let _currentMessages = [];
let currentSessionId = null;
let currentPersona = "coach";

function setPersona(persona) {
  currentPersona = PERSONA_LABELS[persona] ? persona : "coach";
  personaBtns.forEach(b => b.classList.toggle("active", b.dataset.persona === currentPersona));
  inputEl.placeholder = PERSONA_PLACEHOLDER[currentPersona];
  const sessionMeta = _sessions.find(s => s.id === currentSessionId);
  if (sessionMeta) sessionMeta.persona = currentPersona;
}

personaBtns.forEach(btn => {
  btn.addEventListener("click", () => setPersona(btn.dataset.persona));
});

function makeTitle(text) {
  const t = text.trim();
  if (t.length <= 50) return t;
  const cut = t.slice(0, 50);
  const sp  = cut.lastIndexOf(" ");
  return (sp > 20 ? cut.slice(0, sp) : cut) + "…";
}

function appendMsg(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function openSidebar()  { chatSidebar.classList.add("open");    sidebarOverlay.classList.add("visible"); }
function closeSidebar() { chatSidebar.classList.remove("open"); sidebarOverlay.classList.remove("visible"); }

btnHistory.addEventListener("click", openSidebar);
btnCloseSidebar.addEventListener("click", closeSidebar);
sidebarOverlay.addEventListener("click", closeSidebar);

function formatSessionDate(isoStr) {
  const d        = new Date(isoStr);
  const diffDays = Math.floor((Date.now() - d) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7)  return d.toLocaleDateString("en-US", { weekday: "short" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function renderSessionList() {
  sessionListEl.innerHTML = "";
  if (_sessions.length === 0) {
    sessionListEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:11px">No previous chats</div>';
    return;
  }
  _sessions.forEach(s => {
    const item = document.createElement("div");
    item.className = "session-item" + (s.id === currentSessionId ? " active" : "");
    item.dataset.id = s.id;
    const msgs = Math.floor((s.message_count || 0) / 2);
    const personaTag = PERSONA_TAGS[s.persona] || PERSONA_TAGS.coach;
    item.innerHTML =
      `<div class="session-title">${s.title.replace(/</g, "&lt;")}</div>` +
      `<div class="session-meta">${formatSessionDate(s.updated_at)} · ${msgs} msgs · ${personaTag}</div>` +
      `<button class="session-delete" title="Delete">✕</button>`;
    item.addEventListener("click", e => {
      if (!e.target.classList.contains("session-delete")) switchSession(s.id);
    });
    item.querySelector(".session-delete").addEventListener("click", e => {
      e.stopPropagation();
      deleteSession(s.id);
    });
    sessionListEl.appendChild(item);
  });
}

async function switchSession(id) {
  try {
    const res = await fetch(`/api/sessions/${id}`);
    if (!res.ok) return;
    const s = await res.json();
    currentSessionId    = id;
    _currentMessages    = s.messages || [];
    setPersona(s.persona || "coach");
    messagesEl.innerHTML = "";
    _currentMessages.forEach(m => appendMsg(m.role, m.content));
    renderSessionList();
    closeSidebar();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  } catch (e) {
    console.error("switchSession failed:", e);
  }
}

function newChat() {
  currentSessionId     = null;
  _currentMessages     = [];
  setPersona("coach");
  messagesEl.innerHTML = "";
  appendMsg("system", "New conversation — ask your coach or dietitian anything.");
  renderSessionList();
  closeSidebar();
  inputEl.focus();
}

async function deleteSession(id) {
  try {
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  } catch (e) {
    console.error("deleteSession failed:", e);
  }
  _sessions = _sessions.filter(s => s.id !== id);
  if (id === currentSessionId) newChat();
  else renderSessionList();
}

async function loadSessionsList() {
  try {
    const res  = await fetch("/api/sessions");
    const data = await res.json();
    _sessions  = Array.isArray(data) ? data : [];
  } catch (e) {
    _sessions = [];
  }
  renderSessionList();
}

// One-time migration: move any existing localStorage sessions to the server.
// Only clears localStorage after confirming the server can read them back.
async function migrateLocalStorage() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  let stored;
  try { stored = JSON.parse(raw); } catch { localStorage.removeItem(STORAGE_KEY); return; }
  if (!Array.isArray(stored) || stored.length === 0) { localStorage.removeItem(STORAGE_KEY); return; }

  const results = await Promise.allSettled(stored.map(s =>
    fetch(`/api/sessions/${s.id}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(s),
    }).then(r => r.ok ? r : Promise.reject(r.status))
  ));

  const anySucceeded = results.some(r => r.status === "fulfilled");
  if (!anySucceeded) return; // keep localStorage if server is unreachable

  // Verify at least one session is readable before wiping localStorage
  try {
    const check = await fetch("/api/sessions");
    const list  = await check.json();
    if (Array.isArray(list) && list.length > 0) {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (e) {
    // keep localStorage
  }
}

btnNewChat.addEventListener("click", newChat);

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  inputEl.style.height = "auto";
  sendBtn.disabled = true;

  // Ensure we have an active session
  let sessionId  = currentSessionId;
  let sessionMeta = _sessions.find(s => s.id === sessionId);
  const isNew    = !sessionMeta;
  const now      = new Date().toISOString();

  if (isNew) {
    sessionId = Date.now().toString();
    currentSessionId = sessionId;
    sessionMeta = {
      id:            sessionId,
      title:         makeTitle(text),
      created_at:    now,
      updated_at:    now,
      message_count: 0,
      persona:       currentPersona,
    };
    _sessions.unshift(sessionMeta);
  }

  const messages = [..._currentMessages, { role: "user", content: text }];
  _currentMessages = messages;
  appendMsg("user", text);
  const thinking = appendMsg("thinking", `${PERSONA_LABELS[currentPersona]} is thinking…`);

  try {
    const res = await fetch("/api/chat/stream", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ messages, persona: currentPersona }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({ error: res.statusText }));
      thinking.remove();
      _currentMessages = messages.slice(0, -1);
      appendMsg("system", `Error: ${data.error}`);
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let fullReply    = "";
    let planUpdated  = false;
    let fuelUpdated  = false;
    let assistantBubble = null;
    let sseBuffer    = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      sseBuffer += decoder.decode(value, { stream: true });
      const parts = sseBuffer.split("\n\n");
      sseBuffer = parts.pop(); // keep any incomplete event

      for (const part of parts) {
        for (const line of part.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.error) {
            thinking.remove();
            _currentMessages = messages.slice(0, -1);
            appendMsg("system", `Error: ${evt.error}`);
            return;
          }

          if (evt.token !== undefined) {
            if (!assistantBubble) {
              thinking.remove();
              assistantBubble = appendMsg("assistant", "");
            }
            fullReply += evt.token;
            assistantBubble.textContent = fullReply;
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }

          if (evt.done) {
            planUpdated = evt.plan_updated;
            fuelUpdated = evt.fuel_updated;
          }
        }
      }
    }

    if (!assistantBubble) {
      thinking.remove();
      assistantBubble = appendMsg("assistant", fullReply || "(no response)");
    }

    _currentMessages = [...messages, { role: "assistant", content: fullReply }];

    // Update in-memory metadata
    sessionMeta.updated_at    = new Date().toISOString();
    sessionMeta.message_count = _currentMessages.length;
    _sessions = [sessionMeta, ..._sessions.filter(s => s.id !== sessionId)];

    // Persist to server
    fetch(`/api/sessions/${sessionId}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        title:      sessionMeta.title,
        messages:   _currentMessages,
        created_at: sessionMeta.created_at,
        updated_at: sessionMeta.updated_at,
        persona:    sessionMeta.persona || currentPersona,
      }),
    }).catch(e => console.error("session persist failed:", e));

    // Reload plan if coach updated it
    if (planUpdated) {
      planLoaded = false;
      if (document.getElementById("panel-plan").classList.contains("active")) {
        loadPlan();
      }
    }

    // Reload race fueling if the dietitian updated it
    if (fuelUpdated) {
      raceLoaded = false;
      if (document.getElementById("panel-race").classList.contains("active")) {
        renderRace();
      }
    }
  } catch (e) {
    thinking.remove();
    _currentMessages = messages.slice(0, -1);
    appendMsg("system", `Network error: ${e.message}`);
  } finally {
    renderSessionList();
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

sendBtn.addEventListener("click", sendMessage);
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// Initialise sessions from server (with localStorage migration).
migrateLocalStorage().then(loadSessionsList);

// auto-resize textarea
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + "px";
});

// ── Week panel ───────────────────────────────────────────────────
let weekLoaded = false;
let selectedWeekStart = null;
let detailView = false;
let lastWeekData = null;

const titleEl    = document.getElementById("week-title");
const summaryEl  = document.getElementById("week-summary");
const tbodyEl    = document.getElementById("week-tbody");
const tableEl    = document.getElementById("week-table");
const detailEl   = document.getElementById("week-detail-cards");
const weekSelect = document.getElementById("week-select");

function zoneClass(z) { return z ? `zone-${z.replace(/[^A-Za-z0-9]/g, "")}` : ""; }

function statusBadge(status) {
  const labels = { done: "Done", missed: "Missed", today: "Today", future: "—", rest: "Rest" };
  return `<span class="badge badge-${status}">${labels[status] || status}</span>`;
}

function stravaLink(id) {
  if (!id) return "";
  return ` <a href="https://www.strava.com/activities/${id}" target="_blank" style="color:#fc4c02;font-size:11px;text-decoration:none" title="View on Strava">↗ Strava</a>`;
}

function renderWeek(data) {
  lastWeekData = data;
  titleEl.textContent = `${data.week_num} · ${data.phase} · ${data.week_label}`;
  const s = data.summary;
  const errorHtml = data.garmin_error
    ? `<div style="color:var(--missed);margin-left:auto">⚠ Strava: ${data.garmin_error}</div>`
    : "";
  summaryEl.innerHTML = `
    <div>Distance: <span>${s.done_km} / ${s.plan_km} km</span></div>
    <div>Vert: <span>${s.done_vert} / ${s.plan_vert} m</span></div>
    <div>Progress: <span>${s.pct}%</span></div>
    ${errorHtml}
  `;

  tbodyEl.innerHTML = data.rows.map(row => {
    const planned = [
      row.planned_km   ? `${row.planned_km} km` : "",
      row.planned_vert ? `${row.planned_vert}m↑` : "",
    ].filter(Boolean).join(" · ");

    const zone = row.planned_zone
      ? `<span class="${zoneClass(row.planned_zone)}">${row.planned_zone}</span>` : "—";

    let actual = "—", hrCell = "—";
    const actuals = row.actuals || (row.actual ? [row.actual] : []);
    if (actuals.length > 0) {
      actual = actuals.map(a => {
        let s = `${a.distance_km} km · ${a.duration_min} min`;
        if (a.elev_gain_m) s += ` · ${a.elev_gain_m}m↑`;
        s += stravaLink(a.id);
        return s;
      }).join("<br>");
      const first = actuals[0];
      if (first.avg_hr) {
        hrCell = `<span class="${zoneClass(first.zone)}">${first.avg_hr} bpm (${first.zone})</span>`;
      }
    }

    return `<tr>
      <td>${row.day}</td>
      <td>${row.session}</td>
      <td>${planned || "—"} ${zone}</td>
      <td>${actual}</td>
      <td>${hrCell}</td>
      <td>${statusBadge(row.status)}</td>
    </tr>`;
  }).join("");

  if (detailView) renderDetail(data);
}

function renderDetail(data) {
  detailEl.innerHTML = data.rows.map(row => {
    const metrics = [
      row.planned_km   && row.planned_km   !== "0" ? `📏 ${row.planned_km} km`    : "",
      row.planned_vert && row.planned_vert !== "0" ? `↑ ${row.planned_vert} m`    : "",
      row.duration     && row.duration     !== "0" ? `⏱ ${row.duration} min`      : "",
      row.planned_zone && row.planned_zone !== "—" ? `❤️ ${row.planned_zone}`      : "",
      row.rpe          && row.rpe          !== "—" ? `RPE ${row.rpe}`              : "",
    ].filter(Boolean).join("  ·  ");

    let actualHtml = "";
    const detailActuals = row.actuals || (row.actual ? [row.actual] : []);
    if (detailActuals.length > 0) {
      actualHtml = detailActuals.map(a => {
        const parts = [`${a.distance_km} km`, `${a.duration_min} min`];
        if (a.elev_gain_m) parts.push(`${a.elev_gain_m}m↑`);
        if (a.avg_hr) parts.push(`${a.avg_hr} bpm (${a.zone})`);
        return `<div class="detail-actual">✅ ${parts.join(" · ")}${stravaLink(a.id)}</div>`;
      }).join("");
    } else if (row.status === "missed") {
      actualHtml = `<div class="detail-actual missed">❌ Missed</div>`;
    }

    const notes = row.notes && row.notes !== "—"
      ? `<div class="detail-notes">${row.notes}</div>` : "";

    return `<div class="detail-card status-${row.status}">
      <div class="detail-card-header">
        <span class="detail-day">${row.day}</span>
        ${statusBadge(row.status)}
      </div>
      <div class="detail-session">${row.session}</div>
      ${metrics ? `<div class="detail-metrics">${metrics}</div>` : ""}
      ${notes}
      ${actualHtml}
    </div>`;
  }).join("");
}

function setDetailView(on) {
  detailView = on;
  const wrap = tableEl.parentElement.classList.contains("table-wrap") ? tableEl.parentElement : tableEl;
  wrap.style.display     = on ? "none" : "";
  detailEl.style.display = on ? ""     : "none";
  document.getElementById("btn-toggle-detail").classList.toggle("active", on);
  if (on && lastWeekData) renderDetail(lastWeekData);
}

function currentWeekStart() {
  const today = new Date();
  const day   = today.getDay(); // 0=Sun
  today.setDate(today.getDate() - day);
  return today.toISOString().slice(0, 10);
}

async function populateWeekSelect() {
  try {
    const res   = await fetch("/api/weeks");
    const weeks = await res.json();
    if (!Array.isArray(weeks)) return;
    const current = currentWeekStart();
    weekSelect.innerHTML = "";
    weeks.forEach(w => {
      const opt    = document.createElement("option");
      opt.value    = w.start;
      const start  = new Date(w.start + "T00:00:00");
      const end    = new Date(w.end   + "T00:00:00");
      const fmt    = d => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      let label    = `${w.week_num} · ${w.phase} (${fmt(start)}–${fmt(end)})`;
      if (w.start === current) label = "▶ " + label;
      opt.textContent = label;
      weekSelect.appendChild(opt);
    });
    weekSelect.value = current;
    selectedWeekStart = current;
  } catch (e) {
    console.error("Failed to load weeks", e);
  }
}

async function loadWeek(start = selectedWeekStart, force = false) {
  if (!start) return;
  selectedWeekStart = start;
  titleEl.textContent = "Loading…";
  tbodyEl.innerHTML = "";
  summaryEl.innerHTML = "";
  detailEl.innerHTML = "";
  try {
    const url  = `/api/week?start=${start}${force ? "&force=1" : ""}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (data.error) { titleEl.textContent = `Error: ${data.error}`; return; }
    renderWeek(data);
    weekLoaded = true;
  } catch (e) {
    titleEl.textContent = `Error: ${e.message}`;
  }
}

weekSelect.addEventListener("change", () => loadWeek(weekSelect.value));
document.getElementById("btn-refresh-week").addEventListener("click", () => loadWeek(selectedWeekStart, true));
document.getElementById("btn-toggle-detail").addEventListener("click", () => setDetailView(!detailView));

// ── Full Plan panel ──────────────────────────────────────────────
let planLoaded = false;
let planRows = [];
const planTbody = document.getElementById("plan-tbody");
const planFilter = document.getElementById("plan-filter");

function renderPlan(rows) {
  const today = new Date().toISOString().slice(0, 10);
  planTbody.innerHTML = rows.map(r => {
    const isPast   = r.date < today;
    const isToday  = r.date === today;
    const rowClass = isToday ? "style='background:#2a1a00'" : isPast ? "style='opacity:0.55'" : "";
    return `<tr ${rowClass}>
      <td>${r.date}</td>
      <td>${r.week}</td>
      <td>${r.phase}</td>
      <td>${r.session}</td>
      <td>${r.km || "—"}</td>
      <td>${r.vert || "—"}</td>
      <td><span class="${zoneClass(r.zone)}">${r.zone || "—"}</span></td>
    </tr>`;
  }).join("");
}

async function loadPlan() {
  planTbody.innerHTML = "<tr><td colspan='7' style='color:var(--muted);padding:20px'>Loading…</td></tr>";
  try {
    const res = await fetch("/api/plan");
    planRows = await res.json();
    if (planRows.error) { planTbody.innerHTML = `<tr><td colspan='7'>Error: ${planRows.error}</td></tr>`; return; }
    if (!planRows.length) {
      planTbody.innerHTML = `<tr><td colspan='7' style='color:var(--muted);padding:20px'>No training plan yet. Click <strong>✨ Generate plan</strong> to have the coach build one from your goal race — or upload a CSV.</td></tr>`;
    } else {
      renderPlan(planRows);
    }
    planLoaded = true;
  } catch (e) {
    planTbody.innerHTML = `<tr><td colspan='7'>Error: ${e.message}</td></tr>`;
  }
}

const btnGeneratePlan = document.getElementById("btn-generate-plan");
if (btnGeneratePlan) btnGeneratePlan.addEventListener("click", async () => {
  const status = document.getElementById("plan-gen-status");
  if (!confirm("Generate a training plan from your goal race? This replaces any existing plan.")) return;
  btnGeneratePlan.disabled = true;
  const label = btnGeneratePlan.textContent;
  btnGeneratePlan.textContent = "Generating…";
  status.style.display = "block";
  status.style.color = "var(--muted)";
  status.textContent = "Building your plan — this can take up to a minute…";
  try {
    const res = await fetch("/api/plan/generate", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Generation failed");
    status.style.color = "var(--done)";
    status.textContent = `Done — ${data.weeks} weeks, ${data.sessions} sessions. Loading…`;
    planLoaded = false; weekLoaded = false; raceLoaded = false;  // refresh dependent views
    await loadPlan();
    await populateWeekSelect();
    setTimeout(() => { status.style.display = "none"; }, 4000);
  } catch (e) {
    status.style.color = "var(--missed)";
    status.textContent = e.message;
  } finally {
    btnGeneratePlan.disabled = false;
    btnGeneratePlan.textContent = label;
  }
});

planFilter.addEventListener("input", () => {
  const q = planFilter.value.toLowerCase();
  if (!q) { renderPlan(planRows); return; }
  renderPlan(planRows.filter(r =>
    r.week.toLowerCase().includes(q) ||
    r.session.toLowerCase().includes(q) ||
    r.phase.toLowerCase().includes(q) ||
    r.date.includes(q)
  ));
});

// ── Progress panel ──────────────────────────────────────────────
let predictionChart = null;
const goalCardEl      = document.getElementById("goal-summary-card");
const analyzeBtn      = document.getElementById("btn-analyze-now");
const analyzeStatusEl = document.getElementById("analyze-status");
const historyListEl   = document.getElementById("prediction-history-list");
const goalForm        = document.getElementById("goal-edit-form");

function fmtHms(hms) {
  const parts = hms.split(":");
  return parts[2] === "00" ? `${parts[0]}:${parts[1]}` : hms;
}

async function loadGoal() {
  try {
    const res = await fetch("/api/goal");
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

async function loadPredictions(limit = 50) {
  try {
    const res  = await fetch(`/api/goal/predictions?limit=${limit}`);
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  } catch (e) {
    return [];
  }
}

function syncRaceTargetTime(goal) {
  const el = document.getElementById("race-target-time");
  if (el && goal && !goal.error) el.textContent = `~${fmtHms(goal.aspirational_time_hms)}`;
  const meta = document.getElementById("race-meta");
  if (meta && goal && !goal.error) {
    meta.innerHTML = `<strong>${goal.race_name}</strong> · ${goal.race_date} · ${goal.distance_km}km · ${goal.vert_m}m↑ · Target <span id="race-target-time">~${fmtHms(goal.aspirational_time_hms)}</span>`;
  }
}

function renderGoalSummary(goal) {
  if (!goal || goal.error) {
    goalCardEl.innerHTML = `<div style="color:var(--muted)">No active goal set.</div>`;
    return;
  }
  goalCardEl.innerHTML = `
    <div class="goal-card-header">
      <div class="goal-race-name">${goal.race_name}</div>
      <div class="goal-race-date">${goal.race_date} · ${goal.days_to_race}d away</div>
    </div>
    <div class="goal-card-targets">
      <div>Aspirational: <span>${fmtHms(goal.aspirational_time_hms)}</span></div>
      <div>Realistic band: <span>${fmtHms(goal.realistic_min_hms)}–${fmtHms(goal.realistic_max_hms)}</span></div>
    </div>
    <div class="goal-card-meta">${goal.distance_km}km · ${goal.vert_m}m↑</div>
  `;
  syncRaceTargetTime(goal);
}

function renderPredictionChart(goal, predictions) {
  const canvas = document.getElementById("prediction-chart");
  if (!canvas || typeof Chart === "undefined") return;
  const labels = predictions.map(p =>
    new Date(p.predicted_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })
  );
  const predictedHrs    = predictions.map(p => p.predicted_time_sec / 3600);
  const lowHrs          = goal ? predictions.map(() => goal.realistic_min_sec / 3600) : [];
  const highHrs         = goal ? predictions.map(() => goal.realistic_max_sec / 3600) : [];
  const aspirationalHrs = goal ? predictions.map(() => goal.aspirational_time_sec / 3600) : [];

  if (predictionChart) predictionChart.destroy();
  predictionChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Realistic min", data: lowHrs, borderWidth: 0, pointRadius: 0,
          backgroundColor: "rgba(249,115,22,0.08)", fill: "+1",
        },
        {
          label: "Realistic max", data: highHrs, borderWidth: 0, pointRadius: 0,
          backgroundColor: "rgba(249,115,22,0.08)", fill: false,
        },
        {
          label: "Aspirational", data: aspirationalHrs, borderColor: "#22c55e",
          borderDash: [6, 4], borderWidth: 2, pointRadius: 0, fill: false,
        },
        {
          label: "Predicted", data: predictedHrs, borderColor: "#f97316",
          backgroundColor: "#f97316", borderWidth: 2, pointRadius: 4, fill: false, tension: 0.15,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#ccc" } } },
      scales: {
        x: { ticks: { color: "#999" }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { ticks: { color: "#999", callback: v => `${v}h` }, grid: { color: "rgba(255,255,255,0.05)" } },
      },
    },
  });
}

function renderPredictionHistory(predictions) {
  if (predictions.length === 0) {
    historyListEl.innerHTML =
      `<div style="color:var(--muted);padding:12px">No predictions yet — click "Analyze Now" to get your first read.</div>`;
    return;
  }
  const reversed = [...predictions].reverse(); // most recent first
  historyListEl.innerHTML = reversed.map(p => `
    <div class="prediction-item verdict-${p.verdict.replace(/[^a-z-]/g, "")}">
      <div class="prediction-item-header">
        <span class="prediction-date">${p.predicted_at.slice(0, 10)}</span>
        <span class="prediction-time">${fmtHms(p.predicted_time_hms)}</span>
        <span class="prediction-verdict">${p.verdict}</span>
      </div>
      <div class="prediction-reasoning">${p.reasoning.replace(/</g, "&lt;")}</div>
    </div>
  `).join("");
}

async function loadProgress() {
  const [goal, predictions] = await Promise.all([loadGoal(), loadPredictions()]);
  renderGoalSummary(goal);
  renderPredictionChart(goal, predictions);
  renderPredictionHistory(predictions);
  if (goal && !goal.error) {
    document.getElementById("goal-aspirational").value  = fmtHms(goal.aspirational_time_hms);
    document.getElementById("goal-realistic-min").value = fmtHms(goal.realistic_min_hms);
    document.getElementById("goal-realistic-max").value = fmtHms(goal.realistic_max_hms);
  }
}

const ANALYZE_TRIGGER_PROMPT =
  "Analyze my current training progress against my active race goal. Review recent weeks, " +
  "compare to the goal, and save a race prediction with your verdict.";

analyzeBtn.addEventListener("click", async () => {
  analyzeBtn.disabled = true;
  analyzeStatusEl.style.display = "";
  analyzeStatusEl.textContent = "Analyzing… (this can take a minute)";
  try {
    const res = await fetch("/api/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        messages: [{ role: "user", content: ANALYZE_TRIGGER_PROMPT }],
        persona:  "analyst",
      }),
    });
    const data = await res.json();
    if (data.error) {
      analyzeStatusEl.textContent = `Error: ${data.error}`;
    } else {
      analyzeStatusEl.style.display = "none";
      await loadProgress();
    }
  } catch (e) {
    analyzeStatusEl.textContent = `Network error: ${e.message}`;
  } finally {
    analyzeBtn.disabled = false;
  }
});

goalForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    aspirational_time:  document.getElementById("goal-aspirational").value.trim(),
    realistic_min_time: document.getElementById("goal-realistic-min").value.trim(),
    realistic_max_time: document.getElementById("goal-realistic-max").value.trim(),
  };
  try {
    const res = await fetch("/api/goal", {
      method:  "PUT",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) { alert(`Error: ${data.error}`); return; }
    await loadProgress();
  } catch (e) {
    alert(`Network error: ${e.message}`);
  }
});

// wrap tables in scroll containers after DOM ready
document.addEventListener("DOMContentLoaded", async () => {
  // First-run gate: an unconfigured instance shows the onboarding wizard instead
  // of the (empty) app. If the check fails, fall through to the app as normal.
  try {
    const st = await (await fetch("/api/config/status")).json();
    if (st && st.onboarded === false && typeof renderOnboarding === "function") {
      renderOnboarding();
      return;
    }
  } catch (e) { /* status unavailable — load the app normally */ }

  ["week", "plan"].forEach(id => {
    const panel = document.getElementById(`panel-${id}`);
    const table = panel.querySelector("table");
    if (table && !table.parentElement.classList.contains("table-wrap")) {
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    }
  });

  await loadSessionsList();
  if (_sessions.length > 0) {
    switchSession(_sessions[0].id);
  } else {
    appendMsg("system", "Trail Coach ready — ask your coach anything.");
  }

  loadGoal().then(syncRaceTargetTime);

  inputEl.focus();
});

// ── Race panel (Plan + Fueling sub-views) ─────────────────────────
let raceLoaded = false;

// Per-checkpoint race pacing is not yet derived from the course data — the panel
// shows an empty state until it is (see roadmap: data-drive the Race Plan panel).
// The Fueling sub-view below is fully data-driven from /api/race/fuel.
const RACE_PLAN = [];
const STORY_END_KM = [];

// Story chapters are coarser than checkpoint sections, so several checkpoints
// can share one chapter. Show the full narrative only on the checkpoint that
// ends the chapter; give mid-chapter checkpoints a pointer to it instead of a
// duplicate paragraph.
function storyForCheckpoint(cp) {
  const i = STORY_END_KM.findIndex(e => cp.km <= e + 0.001);
  if (i < 0 || !COURSE_STORY[i]) return null;
  const isEnd = Math.abs(cp.km - STORY_END_KM[i]) < 0.001;
  const endCp = RACE_PLAN.find(c => Math.abs(c.km - STORY_END_KM[i]) < 0.001);
  return { story: COURSE_STORY[i], isEnd, endName: endCp ? endCp.name : null };
}

const RACE_TYPE_BADGE = {
  aid:        { label: "AID",   cls: "aid" },
  cutoff:     { label: "CUT",   cls: "cutoff" },
  critical:   { label: "CRUX",  cls: "critical" },
  checkpoint: { label: "CP",    cls: "checkpoint" },
  start:      { label: "START", cls: "aid" },
  finish:     { label: "FIN",   cls: "finish" },
};

function renderRacePlan() {
  const tbody = document.getElementById("race-plan-tbody");
  if (!RACE_PLAN.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--muted);padding:16px">Per-checkpoint pacing isn't set up yet. See the <strong>Course</strong> tab for the elevation profile, and the <strong>Fueling Plan</strong> tab for your race fueling.</td></tr>`;
    return;
  }
  tbody.innerHTML = RACE_PLAN.map((cp, i) => {
    const prev = RACE_PLAN[i - 1];
    const badge = RACE_TYPE_BADGE[cp.type];
    const badgeHtml = badge ? `<span class="race-badge race-badge-${badge.cls}">${badge.label}</span>` : "";
    const flag = cp.flag ? ` ${cp.flag}` : "";

    // Standalone section stats (this checkpoint relative to the previous one).
    let detailInner;
    if (prev) {
      const secKm   = +(cp.km - prev.km).toFixed(1);
      const secUp   = cp.up - prev.up;
      const secDown = cp.down - prev.down;
      const net     = cp.alt - prev.alt;
      const grade   = secKm > 0 ? Math.round((secUp / (secKm * 1000)) * 100) : 0;
      const cutoffLine = cp.cutoff
        ? `<div class="race-detail-cut">⏱ Cutoff ${cp.cutoff}${cp.eta ? ` · ETA ${cp.eta}` : ""}</div>` : "";
      const s = storyForCheckpoint(cp);
      let storyHtml = "";
      if (s && s.isEnd) {
        storyHtml = `<div class="race-detail-story">
             <div class="race-detail-story-label">🎥 On course · ${s.story.name}</div>
             <p>${s.story.text}</p>
           </div>`;
      } else if (s) {
        storyHtml = `<div class="race-detail-story">
             <div class="race-detail-story-label">🎥 Part of “${s.story.name}”</div>
             <p class="race-detail-story-note">Mid-section waypoint — full course story shown under ${s.endName}.</p>
           </div>`;
      }
      detailInner = `
        <div class="race-detail-section">${prev.name} → ${cp.name}</div>
        <div class="race-detail-stats">
          <span>📏 ${secKm} km</span>
          <span class="up">↑ ${secUp}m</span>
          <span class="down">↓ ${secDown}m</span>
          <span>${net >= 0 ? "+" : ""}${net}m net</span>
          <span>~${grade}% avg</span>
        </div>
        ${cutoffLine}
        <div class="race-detail-notes">${cp.notes}</div>
        ${storyHtml}`;
    } else {
      detailInner = `
        <div class="race-detail-section">${cp.name} · ${cp.alt}m</div>
        <div class="race-detail-notes">${cp.notes}</div>`;
    }

    return `
      <tr class="race-row row-${cp.type}" data-idx="${i}">
        <td><span class="race-cp-name">${cp.name}${flag}</span> ${badgeHtml}<span class="race-chevron">›</span></td>
        <td>${cp.km}</td>
        <td>${cp.up}m</td>
        <td>${cp.eta || "—"}</td>
      </tr>
      <tr class="race-detail-row" data-detail="${i}" hidden>
        <td colspan="4"><div class="race-detail-card">${detailInner}</div></td>
      </tr>`;
  }).join("");

  tbody.querySelectorAll(".race-row").forEach(row => {
    row.addEventListener("click", () => {
      const idx = row.dataset.idx;
      const detail = tbody.querySelector(`.race-detail-row[data-detail="${idx}"]`);
      const open = !detail.hidden;
      detail.hidden = open;
      row.classList.toggle("expanded", !open);
    });
  });
}

// Fueling by aid-station segment — served from /api/race/fuel so the dietitian's
// chat edits (update_race_fuel_plan) flow straight into this panel.
async function renderRaceFuel() {
  const list = document.getElementById("race-fuel-list");
  let data;
  try {
    const res = await fetch("/api/race/fuel");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    list.innerHTML = `<div class="fuel-total">Couldn't load fueling plan (${e.message}).</div>`;
    return;
  }
  const segments = data.segments || [];
  list.innerHTML = segments.map(f => {
    const rate = f.rate;
    const rateCls = rate >= 80 ? "on" : rate >= 65 ? "mid" : "low";
    return `
      <div class="fuel-card${f.crux ? " crux" : ""}">
        <div class="fuel-card-head">
          <span class="fuel-seg">${f.seg}${f.flag ? ` ${f.flag}` : ""}</span>
          <span class="fuel-rate fuel-rate-${rateCls}">~${rate} g/hr</span>
        </div>
        <div class="fuel-card-meta">⏱ ${f.dur} · ${f.carbs}g carbs</div>
        <div class="fuel-card-food">${f.food}</div>
      </div>`;
  }).join("") +
    `<div class="fuel-total">Total ≈ ${data.total_carbs}g carbs across the race</div>`;
}

// Segmented Plan / Fueling toggle.
document.querySelectorAll(".race-seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.raceView;
    document.querySelectorAll(".race-seg-btn").forEach(b =>
      b.classList.toggle("active", b === btn));
    document.querySelectorAll(".race-view").forEach(v =>
      v.classList.toggle("active", v.id === `race-view-${view}`));
  });
});

// Build the checkpoint list from the course's segments + cutoffs (no hardcoded
// race data). Cumulative up/down are approximated from per-segment alt deltas.
function buildRacePlanFromCourse(course) {
  const segs = (course && course.segments) || [];
  if (!segs.length) return [];
  const cutoffFor = (name) => {
    for (const c of (course.cutoffs || [])) {
      if (c.point === name || c.point.includes(name) || name.includes(c.point)) return c.time;
    }
    return null;
  };
  const startName = (course.route || "").split("→")[0].trim() || "Start";
  const first = segs[0];
  const cps = [{ name: startName, km: first.from_km, alt: first.start_alt, up: 0, down: 0, cutoff: cutoffFor(startName), type: "start", notes: "" }];
  let up = 0, down = 0;
  segs.forEach((s) => {
    up += Math.max(0, s.end_alt - s.start_alt);
    down += Math.max(0, s.start_alt - s.end_alt);
    const dest = (s.name.includes("→") ? s.name.split("→").pop() : s.name).trim();
    const cutoff = cutoffFor(dest);
    const isFinish = /finish/i.test(dest);
    cps.push({ name: dest, km: s.to_km, alt: s.end_alt, up, down, cutoff, notes: s.text || "",
      type: isFinish ? "finish" : (cutoff ? "cutoff" : "aid"), flag: isFinish ? "🏁" : "" });
  });
  return cps;
}

async function renderRace() {
  try {
    await loadCourseData();
  } catch (e) { /* narrative optional — render the plan without it */ }
  // Derive checkpoints + story-anchor km from the loaded course (data-driven).
  RACE_PLAN.length = 0;
  buildRacePlanFromCourse(COURSE_DATA).forEach((cp) => RACE_PLAN.push(cp));
  STORY_END_KM.length = 0;
  COURSE_STORY.forEach((ch) => STORY_END_KM.push(ch.to_km));
  renderRacePlan();
  renderRaceFuel();
  raceLoaded = true;
}

// ── Course recon (from POV video frame analysis) ──────────────────
let courseLoaded = false;

// Course data (segments + elevation) loads from the shared source of truth at
// /api/course (DB-backed, legacy-file fallback), which also backs the AI coach
// — keeping them in sync. Returns 404 when no course is configured yet.
let COURSE_STORY = [];
let RACE_ELEVATION = [];
let COURSE_DATA = null;

async function loadCourseData() {
  if (COURSE_DATA) return;
  const res = await fetch("/api/course");
  if (res.status === 404) { COURSE_STORY = []; RACE_ELEVATION = []; COURSE_DATA = null; return; }
  if (!res.ok) throw new Error(`course data ${res.status}`);
  const data = await res.json();
  COURSE_DATA = data;
  COURSE_STORY = data.segments || [];
  RACE_ELEVATION = data.elevation || [];
}

let courseChart = null;

function renderCourseChart() {
  const canvas = document.getElementById("course-elevation-chart");
  if (!canvas || typeof Chart === "undefined") return;
  if (courseChart) courseChart.destroy();
  courseChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: RACE_ELEVATION.map(p => `${p.km}`),
      datasets: [{
        label: "Elevation (m)", data: RACE_ELEVATION.map(p => p.alt),
        borderColor: "#f97316", backgroundColor: "rgba(249,115,22,0.12)",
        borderWidth: 2, pointRadius: 3, fill: true, tension: 0.2,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { title: { display: true, text: "km", color: "#999" }, ticks: { color: "#999" }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { ticks: { color: "#999", callback: v => `${v}m` }, grid: { color: "rgba(255,255,255,0.05)" } },
      },
    },
  });
}

async function renderCourse() {
  try {
    await loadCourseData();
  } catch (e) {
    const el = document.getElementById("course-story");
    if (el) el.innerHTML = `<p class="story-load-error">Couldn't load course data. Reload to retry.</p>`;
    return;
  }
  renderCourseChart();
  const el = document.getElementById("course-story");
  el.innerHTML = COURSE_STORY.map((ch, i) => `
    <section class="story-chapter">
      <div class="story-chapter-num">${String(i + 1).padStart(2, "0")}</div>
      ${ch.img
        ? `<img class="story-chapter-img" src="/img/course/${ch.img}" alt="${ch.name}" loading="lazy" />`
        : `<div class="story-chapter-img story-chapter-img-missing">No footage — 2026 reroute not filmed</div>`}
      <h3>${ch.name}</h3>
      <div class="story-chapter-caption">${ch.caption}</div>
      <p>${ch.text}</p>
    </section>
  `).join("");
  courseLoaded = true;
}
