/* Progressive enhancement: the original select remains the submitted value. */
document.querySelectorAll('select[data-customer-search]').forEach(select => {
  const root = document.createElement('div');
  root.className = 'customer-combobox';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'form-control customer-combobox__input';
  input.id = `${select.id}-search`;
  input.placeholder = 'Search name, phone or email…';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.required = select.required;
  input.disabled = select.disabled;
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  const list = document.createElement('div');
  list.id = `${select.id}-results`;
  list.className = 'customer-combobox__results';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', 'Matching customers');
  list.hidden = true;
  input.setAttribute('aria-controls', list.id);
  const hint = document.createElement('div');
  hint.id = `${select.id}-hint`;
  hint.className = 'customer-combobox__hint';
  hint.setAttribute('role', 'status');
  input.setAttribute('aria-describedby', hint.id);
  select.before(root);
  root.append(input, list, hint, select);
  document.querySelectorAll('label').forEach(label => {
    if (label.htmlFor === select.id) label.htmlFor = input.id;
  });
  select.hidden = true;
  select.required = false;
  let matches = [], active = -1;
  const normalize = value => value.toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const details = option => [option.dataset.phone, option.dataset.email].filter(Boolean).join(' · ');
  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }
  function sync() {
    const option = select.selectedOptions[0];
    input.value = select.value ? option.textContent : '';
    input.setCustomValidity('');
    hint.textContent = select.value ? details(option) || 'Customer selected' : 'Type to search, then choose a customer.';
    close();
  }
  function choose(option) {
    select.value = option.value;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    input.focus();
    close();
  }
  function render() {
    const query = normalize(input.value.trim());
    const digits = query.replace(/\D/g, '');
    const options = [...select.options].filter(option => option.value && !option.disabled);
    const filtered = options.filter(option => !query ||
      normalize(`${option.textContent} ${details(option)}`).includes(query) ||
      (digits.length >= 3 && (option.dataset.phone || '').replace(/\D/g, '').includes(digits)));
    matches = filtered.slice(0, 40);
    active = -1;
    input.removeAttribute('aria-activedescendant');
    list.replaceChildren();
    matches.forEach((option, index) => {
      const row = document.createElement('div');
      row.id = `${list.id}-${index}`;
      row.className = 'customer-combobox__option';
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', 'false');
      const name = document.createElement('strong');
      name.textContent = option.textContent;
      row.append(name);
      const detail = document.createElement('small');
      detail.textContent = details(option) || `Customer #${option.value}`;
      row.append(detail);
      row.addEventListener('pointerdown', event => event.preventDefault());
      row.addEventListener('click', () => choose(option));
      list.append(row);
    });
    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'customer-combobox__empty';
      empty.textContent = 'No customers found. Try another search or use Add Customer.';
      list.append(empty);
    }
    hint.textContent = filtered.length > 40 ? `${filtered.length} matches. Keep typing to narrow the results.` : `${filtered.length} customer${filtered.length === 1 ? '' : 's'} found. Use ↑ ↓ and Enter to select.`;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }
  input.addEventListener('focus', () => { if (!select.value) render(); });
  input.addEventListener('click', render);
  input.addEventListener('input', () => {
    select.value = '';
    input.setCustomValidity(input.value ? 'Choose a customer from the search results.' : '');
    render();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { close(); return; }
    if (event.key === 'Tab') { close(); return; }
    if (event.key === 'Enter' && !list.hidden) {
      event.preventDefault();
      if (active >= 0) choose(matches[active]);
      else if (matches.length === 1) choose(matches[0]);
    }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    if (list.hidden) render();
    if (!matches.length) return;
    active = active < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1) :
      (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
    [...list.children].forEach((row, index) => row.setAttribute('aria-selected', String(index === active)));
    input.setAttribute('aria-activedescendant', list.children[active].id);
    list.children[active].scrollIntoView({block: 'nearest'});
  });
  root.addEventListener('focusout', event => { if (!root.contains(event.relatedTarget)) close(); });
  document.addEventListener('pointerdown', event => { if (!root.contains(event.target)) close(); });
  select.addEventListener('change', sync);
  select.form?.addEventListener('reset', () => setTimeout(sync, 0));
  sync();
});
