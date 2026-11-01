(() => {
  const form = document.querySelector('[data-dashboard-search]');
  if (!form) return;
  const input = form.querySelector('[name=q]');
  const results = document.getElementById('dashboard-search-results');
  const status = document.getElementById('dashboard-search-status');
  let timer, controller, version = 0, active = -1;
  const links = () => [...results.querySelectorAll('a')];
  const close = () => { ++version; controller?.abort(); clearTimeout(timer); results.hidden = true; active = -1; };
  const run = async () => {
    const query = input.value.trim();
    if (!query) { close(); status.textContent = ''; return; }
    const current = ++version;
    controller?.abort(); controller = new AbortController(); active = -1;
    status.textContent = 'Searching…';
    const url = new URL(form.dataset.searchUrl, location.origin); url.searchParams.set('q', query);
    try {
      const response = await fetch(url, {signal:controller.signal, headers:{Accept:'application/json'}});
      if (!response.ok || response.redirected) throw new Error('Search unavailable');
      const payload = await response.json(); if (current !== version) return;
      results.replaceChildren();
      payload.results.forEach(result => {
        const link = document.createElement('a'); link.className = 'dashboard-search-result'; link.href = result.url;
        const type = document.createElement('span'); type.className = 'dashboard-type'; type.textContent = result.kind;
        const content = document.createElement('span'); const name = document.createElement('strong'); name.textContent = result.label;
        const detail = document.createElement('small'); detail.textContent = result.detail; content.append(name,detail);
        const arrow = document.createElement('i'); arrow.className = 'bi bi-arrow-up-right'; arrow.setAttribute('aria-hidden','true');
        link.append(type,content,arrow); results.append(link);
      });
      status.textContent = payload.results.length ? `${payload.results.length} matches shown. Keep typing to narrow, or use arrow keys and Enter.` : 'No matches. Try a name, phone, SKU or document number.';
      results.hidden = false;
    } catch (error) {
      if (error.name === 'AbortError' || current !== version) return;
      status.textContent = 'Suggestions are unavailable. Press Search to load results.';
    }
  };
  input.addEventListener('input', () => { ++version; controller?.abort(); clearTimeout(timer); active = -1; results.hidden = true; timer = setTimeout(run,180); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { close(); return; }
    const options = links();
    if (event.key === 'Enter' && !results.hidden && active >= 0) { event.preventDefault(); options[active]?.click(); return; }
    if (!['ArrowDown','ArrowUp'].includes(event.key) || results.hidden || !options.length) return;
    event.preventDefault(); active = active < 0 ? (event.key === 'ArrowDown' ? 0 : options.length - 1) : (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    options.forEach((link,index) => link.classList.toggle('is-active', index === active));
    status.textContent = `Selected ${options[active].textContent}. Press Enter to open.`;
    options[active].scrollIntoView({block:'nearest'});
  });
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select,[contenteditable]')) return;
    event.preventDefault(); input.focus(); input.select();
  });
  document.querySelector('[data-dashboard-currency] select')?.addEventListener('change', event => event.target.form.requestSubmit());
})();
