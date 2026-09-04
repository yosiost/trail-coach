// Reusable persona-methodology editor, used by both the onboarding wizard and
// the Settings panel. renderPersonaEditor(persona, initial) -> { el, getConfig }.
// Modes: generic | describe (AI drafts it) | custom (write it). "describe" and
// "custom" both save as {mode:'custom', text}.
function renderPersonaEditor(persona, initial) {
  initial = initial || { mode: "generic", text: "" };
  const startMode = initial.mode === "custom" ? "custom" : "generic";

  const wrap = document.createElement("div");
  wrap.className = "persona-editor";
  wrap.innerHTML = `
    <div class="pe-choices">
      <label class="pe-choice"><input type="radio" name="pe-${persona}" value="generic" ${startMode === "generic" ? "checked" : ""}> <span>Generic default</span></label>
      <label class="pe-choice"><input type="radio" name="pe-${persona}" value="describe"> <span>Describe it — let the AI draft it</span></label>
      <label class="pe-choice"><input type="radio" name="pe-${persona}" value="custom" ${startMode === "custom" ? "checked" : ""}> <span>Write it myself</span></label>
    </div>
    <div class="pe-describe">
      <textarea class="pe-desc" rows="2" placeholder="Describe the ${persona} you want — e.g. 'high-mileage polarized training with weekly hill sprints'"></textarea>
      <button type="button" class="pe-gen">✨ Generate with AI</button>
      <span class="pe-gen-status"></span>
    </div>
    <textarea class="pe-text" rows="7" placeholder="Methodology (bullet points work well)">${(initial.text || "").replace(/</g, "&lt;")}</textarea>
    <div class="pe-err"></div>`;

  const q = (s) => wrap.querySelector(s);
  const describeBox = q(".pe-describe");
  const textArea = q(".pe-text");
  const descArea = q(".pe-desc");
  const genBtn = q(".pe-gen");
  const genStatus = q(".pe-gen-status");
  const errEl = q(".pe-err");
  const mode = () => wrap.querySelector(`input[name="pe-${persona}"]:checked`).value;

  function sync() {
    const m = mode();
    describeBox.style.display = m === "describe" ? "" : "none";
    textArea.style.display = m === "generic" ? "none" : "";
    errEl.textContent = "";
  }
  wrap.querySelectorAll(`input[name="pe-${persona}"]`).forEach((r) => r.addEventListener("change", sync));
  sync();

  genBtn.addEventListener("click", async () => {
    const description = descArea.value.trim();
    if (!description) { errEl.textContent = "Add a short description first."; return; }
    genBtn.disabled = true; genStatus.textContent = "Generating…"; errEl.textContent = "";
    try {
      const res = await fetch("/api/persona/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona, description }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Generation failed");
      textArea.value = data.methodology;
      textArea.style.display = "";
      genStatus.textContent = "Drafted — edit below, then save.";
    } catch (e) {
      errEl.textContent = e.message;
      genStatus.textContent = "";
    } finally {
      genBtn.disabled = false;
    }
  });

  return {
    el: wrap,
    getConfig() {
      if (mode() === "generic") return { mode: "generic" };
      const text = textArea.value.trim();
      if (!text) throw new Error(`Add or generate a methodology for the ${persona}.`);
      return { mode: "custom", text };
    },
  };
}
