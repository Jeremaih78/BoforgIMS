(() => {
  "use strict";
  const timer = document.getElementById("focusTimer");
  const intervalTimer = document.getElementById("focusIntervalTimer");
  const renderTimer = () => {
    if (!timer) return;
    const started = timer.dataset.started ? new Date(timer.dataset.started) : null;
    const elapsed = started && timer.dataset.running === "1" ? Math.max(0, Date.now() - started.getTime()) : 0;
    const seconds = Number(timer.dataset.baseSeconds || 0) + Math.floor(elapsed / 1000);
    timer.textContent = [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
      .map(value => String(value).padStart(2, "0")).join(":");
    if (intervalTimer) {
      const intervalStarted = intervalTimer.dataset.started ? new Date(intervalTimer.dataset.started) : null;
      const intervalElapsed = intervalStarted && intervalTimer.dataset.running === "1" ? Math.max(0, Date.now() - intervalStarted.getTime()) / 1000 : 0;
      const used = Math.max(0, Number(intervalTimer.dataset.baseSeconds || 0) + Math.floor(intervalElapsed));
      const remaining = Math.max(0, Number(intervalTimer.dataset.minutes) * 60 - used);
      intervalTimer.textContent = remaining > 0 ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")} remaining` : "Interval target reached — continue, pause or finish when ready.";
      intervalTimer.dataset.reached = remaining === 0 ? "1" : "0";
    }
  };
  renderTimer();
  if (timer?.dataset.running === "1") window.setInterval(renderTimer, 1000);

  const notes = document.getElementById("focusNotes");
  const notesForm = document.getElementById("focusNotesForm");
  if (!notes || !notesForm) return;
  const saveState = document.getElementById("focusSaveState");
  const detailedStatus = document.getElementById("focusNotesStatus");
  const retryButton = document.getElementById("focusRetrySave");
  const conflictPanel = document.getElementById("focusNotesConflict");
  const conflictNotes = document.getElementById("focusConflictNotes");
  const normalize = value => value.replace(/\r\n?/g, "\n");
  let baseVersion = notesForm.dataset.notesVersion;
  let savedNotes = normalize(notes.value);
  let generation = 0;
  let dirty = false;
  let pending = null;
  let inFlight = null;
  let conflict = null;
  let fatalError = false;
  let retryDelay = 2000;
  let saveTimeout;
  let retryTimeout;
  let storageAvailable = true;
  let navigationPending = false;
  const approvedForms = new WeakSet();
  const draftPrefix = "tasker:focus-draft:";
  const draftKey = `${draftPrefix}${notesForm.dataset.draftUser}:${notesForm.dataset.draftTask}`;
  const validVersion = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

  function status(message) {
    saveState.textContent = message === "Notes saved" ? message : message.startsWith("Saving") ? "Saving notes…" : conflict ? "Notes conflict" : "Unsaved notes";
    if (detailedStatus) detailedStatus.textContent = message;
    saveState.classList.add("is-visible");
  }

  function persistDraft() {
    try {
      if (!dirty && !pending) window.sessionStorage.removeItem(draftKey);
      else window.sessionStorage.setItem(draftKey, JSON.stringify({
        notes: normalize(notes.value), baseVersion,
        pending: pending ? {notes: pending.notes, version: pending.version} : null,
      }));
    } catch (_error) {
      storageAvailable = false;
    }
  }

  function showConflict(data) {
    conflict = data;
    pending = null;
    retryButton.hidden = true;
    conflictNotes.value = data.notes;
    conflictPanel.hidden = false;
    persistDraft();
    status("Notes changed in another window. Compare the saved notes before choosing which version to keep.");
  }

  function scheduleRetry() {
    if (conflict || fatalError) return;
    const delay = retryDelay;
    retryDelay = Math.min(retryDelay * 2, 30000);
    window.clearTimeout(retryTimeout);
    retryTimeout = window.setTimeout(() => { void flush(); }, delay);
    retryButton.hidden = false;
    status(`Notes not saved. Retrying in ${delay / 1000} seconds. ${storageAvailable ? "Your draft is retained in this tab." : "Draft recovery is unavailable; keep this page open."}`);
  }

  async function saveLoop() {
    while (dirty || pending) {
      if (conflict || fatalError) return false;
      // Retry an uncertain write before newer edits: its response may be lost
      // after commit. The endpoint accepts replay of identical saved content.
      pending ||= {notes: normalize(notes.value), version: baseVersion, generation};
      persistDraft();
      status("Saving notes…");
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const requestTimeout = controller ? window.setTimeout(() => controller.abort(), 15000) : null;
      try {
        const response = await fetch(notesForm.action, {
          method: "POST",
          headers: {"X-CSRFToken": notesForm.querySelector("[name=csrfmiddlewaretoken]").value},
          body: new URLSearchParams({notes: pending.notes, base_version: pending.version}),
          ...(controller ? {signal: controller.signal} : {}),
        });
        if (response.redirected) {
          fatalError = true;
          throw new Error("Sign-in required");
        }
        // Django permission/CSRF errors can be HTML rather than JSON. Classify
        // them before parsing so a revoked session does not retry forever.
        if (response.status >= 400 && response.status < 500 && response.status !== 409) fatalError = true;
        const data = await response.json();
        if (response.status === 409 && data.error === "notes_conflict" && validVersion(data.version) && typeof data.notes === "string") {
          showConflict(data);
          return false;
        }
        if (!response.ok || data.saved !== true || !validVersion(data.version)) {
          if (response.status >= 400 && response.status < 500) fatalError = true;
          throw new Error("Save rejected");
        }
        baseVersion = data.version;
        savedNotes = pending.notes;
        const unchanged = generation === pending.generation && normalize(notes.value) === pending.notes;
        pending = null;
        dirty = !unchanged;
        retryDelay = 2000;
        retryButton.hidden = true;
        persistDraft();
      } catch (_error) {
        persistDraft();
        if (fatalError) {
          retryButton.hidden = false;
          status("Notes were not saved. Check your sign-in, access and note length, then retry. Keep this page open.");
        } else scheduleRetry();
        return false;
      } finally {
        if (requestTimeout !== null) window.clearTimeout(requestTimeout);
      }
    }
    status("Notes saved");
    return true;
  }

  function flush() {
    window.clearTimeout(saveTimeout);
    window.clearTimeout(retryTimeout);
    if (conflict || fatalError) return Promise.resolve(false);
    if (inFlight) return inFlight;
    if (!dirty && !pending) return Promise.resolve(true);
    inFlight = saveLoop().finally(() => { inFlight = null; });
    return inFlight;
  }
  window.TaskerFocusNotes = {flush, isDirty: () => dirty || !!pending || !!inFlight || !!conflict};
  const parkingIsDirty = () => !!window.TaskerFocusParking?.isDirty?.();
  const flushBeforeNavigation = async () => {
    if (parkingIsDirty() && !await window.TaskerFocusParking.flush()) return false;
    return flush();
  };

  notes.addEventListener("input", () => {
    generation += 1;
    dirty = normalize(notes.value) !== savedNotes || !!pending;
    persistDraft();
    status(conflict ? "Your draft is retained. Resolve the saved-note conflict below." : "Unsaved notes");
    window.clearTimeout(saveTimeout);
    if (!conflict && !fatalError) saveTimeout = window.setTimeout(() => { void flush(); }, 700);
  });
  retryButton.addEventListener("click", () => {
    fatalError = false;
    void flush();
  });
  document.getElementById("focusUseSavedNotes").addEventListener("click", () => {
    if (!conflict) return;
    notes.value = conflict.notes;
    savedNotes = conflict.notes;
    baseVersion = conflict.version;
    generation += 1;
    conflict = null;
    pending = null;
    dirty = false;
    conflictPanel.hidden = true;
    persistDraft();
    status("Saved notes loaded. Your local draft was discarded.");
  });
  document.getElementById("focusOverwriteNotes").addEventListener("click", () => {
    if (!conflict) return;
    baseVersion = conflict.version;
    savedNotes = conflict.notes;
    conflict = null;
    pending = null;
    dirty = normalize(notes.value) !== savedNotes;
    conflictPanel.hidden = true;
    void flush();
  });

  document.addEventListener("submit", async event => {
    const form = event.target;
    if (approvedForms.has(form)) { approvedForms.delete(form); return; }
    if (event.defaultPrevented) return;
    if (form === notesForm) { event.preventDefault(); void flush(); return; }
    if (form.hasAttribute?.("data-confirm-complete") && !form.hasAttribute?.("data-definition-review") && !window.confirm("Complete this task and stop the timer?")) {
      event.preventDefault();
      return;
    }
    // Checklist and all other form mutations wait for the latest generation.
    if (!dirty && !pending && !inFlight && !conflict && !parkingIsDirty()) return;
    event.preventDefault();
    if (navigationPending) return;
    navigationPending = true;
    const submitter = event.submitter;
    try {
      if (await flushBeforeNavigation()) {
        approvedForms.add(form);
        try { form.requestSubmit(submitter || undefined); }
        finally { approvedForms.delete(form); }
      }
    } finally { navigationPending = false; }
  });
  document.addEventListener("click", async event => {
    const link = event.target.closest?.("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === "_blank" || link.hasAttribute("download")) return;
    if (link.getAttribute?.("href")?.startsWith("#")) return;
    if (!dirty && !pending && !inFlight && !conflict && !parkingIsDirty()) return;
    event.preventDefault();
    if (navigationPending) return;
    navigationPending = true;
    try {
      if (await flushBeforeNavigation()) window.location.assign(link.href);
    } finally { navigationPending = false; }
  });
  window.addEventListener("beforeunload", event => {
    if (!dirty && !pending && !inFlight) return;
    persistDraft();
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("pagehide", persistDraft);
  window.addEventListener("pageshow", event => { if (event.persisted) window.location.reload(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { persistDraft(); void flush(); }
  });

  try {
    const activeUserKey = `${draftPrefix}active-user`;
    const previousUser = window.sessionStorage.getItem(activeUserKey);
    if (previousUser && previousUser !== notesForm.dataset.draftUser) {
      for (let index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = window.sessionStorage.key(index);
        if (key?.startsWith(draftPrefix)) window.sessionStorage.removeItem(key);
      }
    }
    window.sessionStorage.setItem(activeUserKey, notesForm.dataset.draftUser);
    const draft = JSON.parse(window.sessionStorage.getItem(draftKey) || "null");
    if (draft && typeof draft.notes === "string" && draft.notes.length <= 10000 && validVersion(draft.baseVersion)) {
      if (normalize(draft.notes) === savedNotes) window.sessionStorage.removeItem(draftKey);
      else {
        notes.value = normalize(draft.notes);
        generation += 1;
        dirty = true;
        const pendingText = typeof draft.pending?.notes === "string" ? normalize(draft.pending.notes) : null;
        if (draft.baseVersion !== baseVersion && pendingText !== savedNotes) {
          const serverVersion = baseVersion;
          // Retain the old base in recovery storage. Adopting the server base
          // here would make a second reload silently approve the overwrite.
          baseVersion = draft.baseVersion;
          showConflict({notes: savedNotes, version: serverVersion});
        } else {
          if (draft.baseVersion === baseVersion && draft.pending && typeof draft.pending.notes === "string" && validVersion(draft.pending.version)) {
            pending = {notes: normalize(draft.pending.notes), version: draft.pending.version,
              generation: normalize(draft.pending.notes) === normalize(notes.value) ? generation : generation - 1};
          }
          status("Unsaved draft recovered in this tab. Retrying save…");
          saveTimeout = window.setTimeout(() => { void flush(); }, 700);
        }
      }
    }
  } catch (_error) { storageAvailable = false; }

  const uiPreferences = window.TaskerUIPreferences;
  if (uiPreferences) {
    const updateShortcutLabels = () => uiPreferences.updateShortcutLabels(document, uiPreferences.get());
    updateShortcutLabels();
    document.addEventListener("tasker:preferences-changed", updateShortcutLabels);
  }
  document.addEventListener("keydown", event => {
    if (!uiPreferences) return;
    const action = uiPreferences.actionForEvent(uiPreferences.get(), event, {
      scope: "focus",
      editable: event.target.tagName === "BUTTON" || uiPreferences.isEditableTarget(event.target),
      dialogOpen: !!document.querySelector('.modal.show, dialog[open], [role="dialog"][aria-modal="true"]'),
    });
    let form;
    if (action === "focus_exit") form = document.querySelector(".focus-exit-form");
    else if (action === "focus_complete") form = document.querySelector("[data-confirm-complete]");
    else if (action === "focus_pause") form = document.querySelector(".btn-pause, .btn-resume")?.closest("form");
    else if (action === "focus_block") {
      const modal = document.getElementById("blockedModal");
      if (modal && window.bootstrap?.Modal) {
        event.preventDefault();
        window.bootstrap.Modal.getOrCreateInstance(modal).show();
      }
    }
    if (form) { event.preventDefault(); form.requestSubmit(); }
  });
})();
