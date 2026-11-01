(() => {
  'use strict';
  const form = document.querySelector('[data-document-composer]');
  if (!form) return;
  const rows = form.querySelector('[data-document-lines]');
  const count = form.querySelector('[name="lines-TOTAL_FORMS"]');
  const comboRows = form.querySelector('[data-document-combos]');
  const comboCount = form.querySelector('[name="combos-TOTAL_FORMS"]');
  const total = form.querySelector('[data-document-total]');
  const recalculate = () => {
    let sum = Number(total.dataset.fixedTotal || 0);
    const currencies = new Set();
    rows.querySelectorAll('[data-document-line]').forEach(row => {
      if (row.hidden) return;
      const selected = row.querySelector('[name$="product"]').value;
      const amount = selected ? Math.round(Number(row.querySelector('[name$="quantity"]').value || 0) * Number(row.querySelector('[name$="unit_price"]').value || 0) * 100) / 100 : 0;
      row.querySelector('[data-line-total]').textContent = amount.toFixed(2);
      sum += amount;
      const picker = row.querySelector('[data-product-combobox]');
      if (selected && picker.dataset.currency) currencies.add(picker.dataset.currency);
    });
    comboRows.querySelectorAll('[data-document-combo]').forEach(row => {
      if (row.hidden) return;
      const picker = row.querySelector('[data-product-combobox]');
      const selected = row.querySelector('[name$="combo"]').value;
      const quantity = Number(row.querySelector('[name$="quantity"]').value || 0);
      const amount = selected ? Math.round(Number(picker.dataset.comboPrice || 0) * quantity * 100) / 100 : 0;
      const saving = selected ? Math.round(Number(picker.dataset.comboDiscount || 0) * quantity * 100) / 100 : 0;
      row.querySelector('[data-combo-total]').textContent = amount.toFixed(2);
      row.querySelector('[data-combo-saving]').textContent = saving > 0 ? `Save ${saving.toFixed(2)}` : '';
      if (selected && picker.dataset.currency) currencies.add(picker.dataset.currency);
      sum += amount;
    });
    form.querySelector('[data-combo-empty]').hidden = [...comboRows.children].some(row => !row.hidden);
    total.textContent = sum.toFixed(2);
    form.querySelector('[data-total-currency]').textContent = [...currencies].join(' / ');
  };
  form.querySelectorAll('[data-add-document-line]').forEach(button => button.addEventListener('click', () => {
    if (Number(count.value) >= 100) return;
    const html = document.getElementById('empty-document-line').innerHTML.replaceAll('__prefix__', count.value);
    rows.insertAdjacentHTML('beforeend', html);
    count.value = Number(count.value) + 1;
    window.initProductComboboxes(rows);
    rows.lastElementChild.querySelector('.product-combobox__input').focus();
  }));
  form.querySelectorAll('[data-add-document-combo]').forEach(button => button.addEventListener('click', () => {
    if (Number(comboCount.value) >= 100) return;
    comboRows.insertAdjacentHTML('beforeend', document.getElementById('empty-document-combo').innerHTML.replaceAll('__prefix__', comboCount.value));
    comboCount.value = Number(comboCount.value) + 1;
    window.initProductComboboxes(comboRows);
    recalculate();
    comboRows.lastElementChild.querySelector('.product-combobox__input').focus();
  }));
  comboRows.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-document-combo]');
    if (!button) return;
    const row = button.closest('[data-document-combo]');
    row.querySelector('[name$="DELETE"]').checked = true;
    row.hidden = true;
    row.querySelectorAll('input').forEach(input => {
      input.required = false; input.setCustomValidity('');
      if (!input.name.endsWith('-DELETE')) input.disabled = true;
    });
    recalculate();
  });
  rows.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-document-line]');
    if (!button) return;
    const row = button.closest('[data-document-line]');
    row.querySelector('[name$="DELETE"]').checked = true;
    row.hidden = true;
    row.querySelectorAll('input, textarea').forEach(input => {
      input.required = false; input.setCustomValidity('');
      if (!input.name.endsWith('-id') && !input.name.endsWith('-DELETE')) input.disabled = true;
    });
    recalculate();
  });
  form.addEventListener('input', recalculate);
  form.addEventListener('product:selected', recalculate);
  form.addEventListener('combo:selected', recalculate);
  form.addEventListener('catalog:cleared', recalculate);
  // Keep the focused control above the sticky totals/actions on short screens.
  form.addEventListener('focusin', event => {
    const target = event.target;
    if (!target.matches('input:not([type="hidden"]), textarea, select')) return;
    requestAnimationFrame(() => {
      if (document.activeElement !== target) return;
      const control = target.getBoundingClientRect();
      const actions = form.querySelector('.document-savebar').getBoundingClientRect();
      if (actions.top < window.innerHeight && control.bottom > actions.top - 12 && control.top < actions.bottom) {
        target.scrollIntoView({block:'center', inline:'nearest'});
      }
    });
  });
  form.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      form.requestSubmit(form.querySelector('[value="save"]'));
    }
  });
  form.addEventListener('submit', event => {
    if (form.dataset.saving === 'true') { event.preventDefault(); return; }
    setTimeout(() => {
      if (event.defaultPrevented) return;
      form.dataset.saving = 'true';
      form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
    }, 0);
  });
  window.addEventListener('pageshow', () => {
    delete form.dataset.saving;
    form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = false; });
  });
  // Hide genuinely blank extra rows on an edit, while keeping a ready row on new documents.
  if (Number(form.querySelector('[name="lines-INITIAL_FORMS"]').value) > 0) {
    rows.querySelectorAll('[data-document-line]').forEach(row => {
      if (!row.querySelector('[name$="product"]').value && !row.querySelector('[name$="description"]').value) {
        row.querySelector('[name$="DELETE"]').checked = true;
        row.hidden = true;
      }
    });
  }
  recalculate();
})();
