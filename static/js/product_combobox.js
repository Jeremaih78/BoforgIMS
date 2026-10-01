(() => {
  'use strict';

  const escapeHtml = (value) => {
    const element = document.createElement('div');
    element.textContent = value == null ? '' : String(value);
    return element.innerHTML;
  };

  document.querySelectorAll('[data-product-combobox]').forEach((root) => {
    const hidden = root.querySelector('input[type="hidden"]');
    const input = root.querySelector('.product-combobox__input');
    const results = root.querySelector('.product-combobox__results');
    const clearButton = root.querySelector('.product-combobox__clear');
    const form = root.closest('form');
    const priceInput = form?.querySelector('[name="unit_price"]');
    const taxInput = form?.querySelector('[name="tax_rate_percent"]');
    let options = [];
    let activeIndex = -1;
    let debounceTimer;
    let requestController;
    let searchVersion = 0;
    let selectedLabel = input.value;

    const close = () => {
      searchVersion += 1;
      requestController?.abort();
      clearTimeout(debounceTimer);
      results.hidden = true;
      results.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      options = [];
      activeIndex = -1;
    };

    const setActive = (index) => {
      if (!options.length) return;
      activeIndex = (index + options.length) % options.length;
      options.forEach((option, optionIndex) => option.classList.toggle('active', optionIndex === activeIndex));
      const active = options[activeIndex];
      input.setAttribute('aria-activedescendant', active.id);
      active.scrollIntoView({ block: 'nearest' });
    };

    const select = (product) => {
      hidden.value = product.id;
      selectedLabel = `${product.name} (${product.sku})`;
      input.value = selectedLabel;
      if (priceInput) priceInput.value = product.price;
      if (taxInput) taxInput.value = product.tax_rate;
      input.setCustomValidity('');
      close();
      input.dispatchEvent(new CustomEvent('product:selected', { bubbles: true, detail: product }));
    };

    const render = (payload) => {
      if (!payload.results.length) {
        results.innerHTML = '<div class="list-group-item text-secondary">No matching active products</div>';
        results.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        options = [];
        return;
      }
      results.innerHTML = payload.results.map((product, index) => {
        const stock = product.track_inventory ? `${product.available_stock} available` : 'Stock not tracked';
        return `<button type="button" id="${results.id}-option-${index}" class="list-group-item list-group-item-action product-combobox__option" role="option" data-index="${index}">
          <span class="d-flex justify-content-between gap-3">
            <span><strong>${escapeHtml(product.name)}</strong> <span class="text-secondary">${escapeHtml(product.sku)}</span></span>
            <strong class="text-nowrap">${escapeHtml(product.currency)} ${escapeHtml(product.price)}</strong>
          </span>
          <span class="d-flex justify-content-between gap-3 small text-secondary">
            <span>${escapeHtml(product.category)}</span><span>${escapeHtml(stock)}</span>
          </span>
        </button>`;
      }).join('') + (payload.has_more ? '<div class="list-group-item small text-secondary text-center">Keep typing to narrow the results</div>' : '');
      options = Array.from(results.querySelectorAll('.product-combobox__option'));
      options.forEach((option) => option.addEventListener('mousedown', (event) => {
        event.preventDefault();
        select(payload.results[Number(option.dataset.index)]);
      }));
      results.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      setActive(0);
    };

    const search = async () => {
      close();
      const version = searchVersion;
      const query = input.value.trim();
      requestController = new AbortController();
      results.innerHTML = '<div class="list-group-item text-secondary"><span class="spinner-border spinner-border-sm me-2"></span>Searching…</div>';
      results.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      try {
        const url = new URL(root.dataset.searchUrl, window.location.origin);
        url.searchParams.set('q', query);
        const response = await fetch(url, {
          headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
          signal: requestController.signal,
        });
        if (!response.ok) throw new Error(`Search failed (${response.status})`);
        const payload = await response.json();
        if (version !== searchVersion || input.value.trim() !== query) return;
        render(payload);
      } catch (error) {
        if (error.name === 'AbortError' || version !== searchVersion) return;
        results.innerHTML = '<div class="list-group-item text-danger">Product search is unavailable. Try again.</div>';
      }
    };

    input.addEventListener('input', () => {
      if (input.value !== selectedLabel) hidden.value = '';
      close();
      debounceTimer = setTimeout(search, 180);
    });
    input.addEventListener('focus', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(search, 0);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); setActive(activeIndex + 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(activeIndex - 1); }
      else if (event.key === 'Enter') { event.preventDefault(); if (activeIndex >= 0) options[activeIndex].dispatchEvent(new MouseEvent('mousedown')); }
      else if (event.key === 'Escape') close();
    });
    clearButton.addEventListener('click', () => {
      hidden.value = '';
      input.value = '';
      selectedLabel = '';
      close();
      input.focus();
    });
    form?.addEventListener('submit', (event) => {
      if (event.submitter?.name === 'add_line' && !hidden.value) {
        event.preventDefault();
        input.setCustomValidity('Select a product from the search results.');
        input.reportValidity();
        input.focus();
      }
    });
    document.addEventListener('click', (event) => { if (!root.contains(event.target)) close(); });
  });
})();
