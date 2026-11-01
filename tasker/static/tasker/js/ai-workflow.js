/* Status polling never triggers generation or retries. Hidden/offline tabs pause. */
(() => {
  'use strict';
  const form = document.querySelector('[data-ai-work-form]');
  if (form) {
    const feature = form.elements.feature;
    const sync = () => {
      for (const [name, features] of Object.entries({task: ['EXPLAIN', 'BREAKDOWN', 'COORDINATION_SUMMARY'], capture: ['CAPTURE'], team: ['BRIEFING', 'COORDINATION_SUMMARY']})) {
        const visible = features.includes(feature.value);
        const group = form.querySelector(`[data-ai-field="${name}"]`);
        if (group) group.hidden = !visible;
        if (!visible) form.elements[name].value = '';
      }
      form.elements.question.maxLength = feature.value === 'MEETING' ? 16000 : 2000;
    };
    feature.addEventListener('change', sync);
    sync();
  }
  const root = document.querySelector('[data-ai-poll]');
  if (!root) return;
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    if (!document.hidden && navigator.onLine !== false) {
      try {
        const response = await fetch(root.dataset.aiPoll, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
        if (response.status === 403 || response.status === 404) { stopped = true; return; }
        if (response.ok && response.headers.get('content-type')?.includes('application/json')) {
          const state = await response.json();
          if (!state.pending || state.revision !== root.dataset.aiRevision) { window.location.reload(); return; }
        }
      } catch (_) { /* Keep the manual refresh and cancellation controls available. */ }
    }
    window.setTimeout(poll, 5000);
  };
  window.setTimeout(poll, 5000);
})();
