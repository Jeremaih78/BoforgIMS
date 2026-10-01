/* HID scanners type into focused fields. Never intercept ordinary page typing. */
(() => {
  function requestID() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c =>
      (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
  }
  document.querySelectorAll('[data-scanner]').forEach(panel => {
    const form = panel.querySelector('form'), input = form.elements.code;
    const status = panel.querySelector('.scan-feedback'), choices = panel.querySelector('.scan-choices');
    const retry = panel.querySelector('[data-retry]');
    let pending = null, busy = false;
    const storageKey = `boforg-scan:${panel.dataset.endpoint}`;
    try { pending = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) { /* storage can be disabled */ }
    function store() {
      try { if (pending) sessionStorage.setItem(storageKey, JSON.stringify(pending)); else sessionStorage.removeItem(storageKey); } catch (_) { /* in-memory retry still works */ }
    }
    if (pending) { status.textContent = 'An earlier scan has an unconfirmed response. Retry it before scanning another item.'; retry.hidden = false; }
    function beep() {
      if (!panel.querySelector('[data-sound]').checked) return;
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = ctx.createOscillator(), gain = ctx.createGain();
        oscillator.connect(gain); gain.connect(ctx.destination); gain.gain.value = .05;
        oscillator.frequency.value = 880; oscillator.start(); oscillator.stop(ctx.currentTime + .09);
        oscillator.onended = () => ctx.close();
      } catch (_) { /* feedback text is always available */ }
    }
    async function send(code) {
      if (busy) { status.textContent = 'Please wait for the current scan to finish.'; return; }
      if (!pending) pending = {code, key: requestID()};
      store(); busy = true; input.readOnly = true;
      form.querySelector('button').disabled = true; retry.hidden = true;
      choices.replaceChildren(); status.textContent = 'Checking scan…';
      let confirmed = false;
      try {
        const body = new FormData(form); body.set('code', pending.code); body.set('key', pending.key);
        const response = await fetch(panel.dataset.endpoint, {method:'POST', body, credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'}, signal:AbortSignal.timeout(20000)});
        const data = await response.json();
        // A structured rejection did not mutate stock. Transport/server failures
        // are uncertain; retain the same idempotency key for explicit retry.
        if (!response.ok && response.status >= 500) throw new Error('Server could not confirm this scan.');
        confirmed = true; pending = null; store();
        status.textContent = data.error || data.message || 'Scan recorded.';
        if (data.assign_url) {
          const link = document.createElement('a'); link.href = data.assign_url;
          link.className = 'btn btn-outline-light'; link.target = '_blank'; link.rel = 'noopener';
          link.textContent = 'Assign this barcode to an existing product'; choices.append(link);
        }
        status.classList.toggle('scan-error', !response.ok || !!data.warning);
        if (!response.ok) return;
        beep(); input.value = '';
        if (data.units) {
          data.units.forEach(unit => {
            const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-light text-start';
            button.textContent = `${unit.unit_id} · ${unit.serial_number || 'No manufacturer serial'} · ${unit.location || 'Unassigned location'}`;
            button.addEventListener('click', () => send(unit.unit_id)); choices.append(button);
          });
          if (!data.units.length) status.textContent += ' No available units. Check reservations or receive stock.';
        }
        if (panel.hasAttribute('data-navigate') && data.url) { location.assign(data.url); return; }
        const target = document.querySelector('[data-scan-refresh]');
        if (target && !data.needs_unit) {
          try {
            const refreshed = await fetch(location.href, {credentials:'same-origin'});
            const doc = new DOMParser().parseFromString(await refreshed.text(), 'text/html');
            const replacement = doc.querySelector('[data-scan-refresh]');
            if (replacement) target.replaceWith(replacement);
            else status.textContent += ' Refresh the page to see updated records.';
          } catch (_) { status.textContent += ' Saved. Refresh the page to see updated records.'; }
        }
      } catch (_) {
        status.textContent = 'Response not confirmed. Check the connection and retry this same scan; do not add it again.';
        status.classList.add('scan-error'); retry.hidden = false;
      } finally {
        busy = false; input.readOnly = false; form.querySelector('button').disabled = false;
        if (confirmed && panel.querySelector('[data-keep-focus]').checked && (!document.activeElement || panel.contains(document.activeElement) || document.activeElement === document.body)) input.focus();
      }
    }
    form.addEventListener('submit', event => { event.preventDefault(); send(input.value); });
    retry.addEventListener('click', () => { if (pending) send(pending.code); });
  });
  document.querySelectorAll('[data-serial-capture]').forEach(area => {
    const counter = document.createElement('p'); counter.className = 'serial-progress'; counter.setAttribute('role','status'); area.after(counter);
    const quantity = area.form.querySelector(`[name="${area.name.replace(/serials$/, 'quantity')}"]`);
    const rows = document.createElement('div'); rows.className = 'serial-unit-rows'; counter.after(rows);
    function unitRows() {
      const values = area.value.split(/[\n,]/).map(s => s.trim());
      rows.replaceChildren();
      const total = Math.min(Number(quantity?.value || 0), 250);
      for (let index = 0; index < total; index++) {
        const label = document.createElement('label'); label.className = 'd-block mb-2';
        label.textContent = `Unit ${index + 1} · Boforg ID assigned on receipt`;
        const field = document.createElement('input'); field.className = 'form-control'; field.maxLength = 120;
        field.autocomplete = 'off'; field.placeholder = 'Scan manufacturer serial, or leave blank'; field.value = values[index] || '';
        field.addEventListener('input', () => { area.value = [...rows.querySelectorAll('input')].map(e=>e.value).join('\n'); count(); });
        field.addEventListener('keydown', event => {
          if (event.key === 'Enter') { event.preventDefault(); rows.querySelectorAll('input')[index + 1]?.focus(); }
        });
        label.append(field); rows.append(label);
      }
    }
    function count() {
      const serials = area.value.split(/[\n,]/).map(s=>s.trim().toUpperCase()).filter(Boolean);
      const duplicate = new Set(serials).size !== serials.length;
      counter.textContent = `${serials.length} / ${quantity?.value || '?'} manufacturer serials captured. Remaining units receive Boforg IDs.${duplicate ? ' Duplicate serial detected.' : ''}`;
      area.setCustomValidity(duplicate ? 'Remove the duplicate serial.' : '');
    }
    area.addEventListener('input', () => { count(); unitRows(); }); quantity?.addEventListener('input', () => { count(); unitRows(); }); count(); unitRows();
  });
  const diagnostic = document.querySelector('[data-scanner-test]');
  if (diagnostic) diagnostic.addEventListener('submit', event => {
    event.preventDefault(); const code = diagnostic.elements.code.value.trim();
    const status = document.querySelector('[data-test-result]');
    status.textContent = `Input received: ${code}. ${/^\d{13}$/.test(code) ? '13 digits (may be EAN-13; format alone does not verify it).' : 'Identifier received.'} Submit/Enter received. No inventory changed.`;
    diagnostic.elements.code.select();
  });
})();
