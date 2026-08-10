(() => {
  "use strict";
  const timer = document.getElementById("focusTimer");
  const notes = document.getElementById("focusNotes");
  const notesForm = document.getElementById("focusNotesForm");
  const saveState = document.getElementById("focusSaveState");
  let saveTimeout;
  let notesDirty = false;

  const renderTimer = () => {
    if (!timer) return;
    const started = timer.dataset.started ? new Date(timer.dataset.started) : null;
    const elapsed = started && timer.dataset.running === "1" ? Math.max(0, Date.now() - started.getTime()) : 0;
    const seconds = Number(timer.dataset.baseSeconds || 0) + Math.floor(elapsed / 1000);
    const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
    const remainder = String(seconds % 60).padStart(2, "0");
    timer.textContent = `${hours}:${minutes}:${remainder}`;
  };
  renderTimer();
  if (timer?.dataset.running === "1") window.setInterval(renderTimer, 1000);

  const autosave = async () => {
    if (!notesForm || !notes || !notesDirty) return true;
    saveState.textContent = "Saving…";
    saveState.classList.add("is-visible");
    try {
      const response = await fetch(notesForm.action, {
        method: "POST",
        headers: {"X-CSRFToken": notesForm.querySelector("[name=csrfmiddlewaretoken]").value},
        body: new URLSearchParams({notes: notes.value}),
      });
      if (!response.ok || response.redirected) throw new Error("Save failed");
      notesDirty = false;
      saveState.textContent = "Notes saved";
      window.setTimeout(() => saveState.classList.remove("is-visible"), 1600);
      return true;
    } catch (_error) {
      saveState.textContent = "Notes not saved — retrying after your next edit";
      return false;
    }
  };
  notes?.addEventListener("input", () => {
    notesDirty = true;
    window.clearTimeout(saveTimeout);
    saveState.textContent = "Unsaved changes";
    saveState.classList.add("is-visible");
    saveTimeout = window.setTimeout(autosave, 700);
  });

  document.querySelector("[data-confirm-complete]")?.addEventListener("submit", (event) => {
    if (!window.confirm("Complete this task and stop the timer?")) event.preventDefault();
  });
  document.querySelectorAll('form[action*="/focus/"]:not(#focusNotesForm):not(.focus-check)').forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (event.defaultPrevented) return;
      if (!notesDirty) return;
      event.preventDefault();
      window.clearTimeout(saveTimeout);
      if (await autosave()) HTMLFormElement.prototype.submit.call(form);
    });
  });
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    if (event.key === "Escape") document.querySelector(".focus-exit-form")?.requestSubmit();
    if (event.key.toLowerCase() === "b") bootstrap.Modal.getOrCreateInstance(document.getElementById("blockedModal")).show();
    if (event.key.toLowerCase() === "c") document.querySelector("[data-confirm-complete]")?.requestSubmit();
    if (event.code === "Space") {
      event.preventDefault();
      document.querySelector(".btn-pause, .btn-resume")?.closest("form")?.requestSubmit();
    }
  });
})();
