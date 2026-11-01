/* Preserve focus while filtering lists; the normal GET form is the fallback. */
document.querySelectorAll('[data-live-search]').forEach(form => {
  const input = form.querySelector('[name="q"]');
  const selector = form.dataset.resultsTarget;
  let timer, controller, version = 0, failed = false;
  const status = document.createElement('div'); status.className = 'form-text text-secondary'; status.setAttribute('role', 'status'); form.append(status);
  const run = async () => {
    const current = ++version;
    controller?.abort(); controller = new AbortController();
    const url = new URL(form.action || location.href); url.search = new URLSearchParams(new FormData(form)).toString(); url.hash = '';
    const region = document.querySelector(selector); region.setAttribute('aria-busy', 'true'); status.textContent = 'Searching…';
    document.querySelectorAll('a[href*="price-list.pdf"]').forEach(link => { const target = new URL(link.href); target.search = url.search; link.href = target.href; });
    try {
      const response = await fetch(url, {signal: controller.signal, headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok || response.redirected) throw new Error('Search unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = page.querySelector(selector);
      if (!next) throw new Error('Search unavailable');
      if (current !== version) return;
      region.innerHTML = next.innerHTML; region.setAttribute('aria-busy', 'false');
      window.prepareIMSTables?.(region);
      failed = false;
      history.replaceState(null, '', url); status.textContent = input.value ? `Results for “${input.value}”` : 'Showing all records';
    } catch (error) {
      if (error.name === 'AbortError' || current !== version) return;
      failed = true;
      region.setAttribute('aria-busy', 'false'); status.textContent = 'Live search is unavailable. Press Search to reload.';
    }
  };
  input.addEventListener('input', () => { ++version; controller?.abort(); clearTimeout(timer); timer = setTimeout(run, 250); });
  form.addEventListener('submit', event => { if (failed) return; event.preventDefault(); clearTimeout(timer); run(); });
});
