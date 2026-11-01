/* Shared, keyboard-accessible typeahead. The native select remains the source of truth. */
(() => {
  'use strict';
  if (document.body.classList.contains('tasker-app')) return;
  const normalize = text => text.toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  let sequence = 0;
  window.initSearchableSelects = (scope = document) => {
    scope.querySelectorAll('main form input:not([type]), main form input[type="text"], main form input[type="number"], main form input[type="date"], main form textarea').forEach(input => input.classList.add('form-control'));
    scope.querySelectorAll('main select:not([multiple]):not([data-customer-search]):not([data-native-select])').forEach(select => {
      if (select.dataset.searchEnhanced || select.size > 1 || select.closest('[hidden]')) return;
      select.dataset.searchEnhanced = 'true';
      const required = select.required;
      const root = document.createElement('div'); root.className = 'ims-select';
      const input = document.createElement('input'); input.className = 'form-control'; input.type = 'text'; input.autocomplete = 'off'; input.spellcheck = false;
      input.id = `${select.id || 'ims-select-' + (++sequence)}-search`;
      const label = [...document.querySelectorAll('label')].find(label => label.htmlFor === select.id);
      const name = label?.textContent.replace(/:\s*$/, '').trim() || select.getAttribute('aria-label') || select.name.replaceAll('_', ' ');
      input.placeholder = `Search ${name.toLowerCase()}…`; input.setAttribute('aria-label', name);
      input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list'); input.setAttribute('aria-expanded', 'false');
      const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'ims-select__clear'; clear.textContent = '×'; clear.setAttribute('aria-label', `Clear ${name}`);
      const list = document.createElement('div'); list.className = 'ims-select__results'; list.id = `${input.id}-results`; list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', name); list.hidden = true;
      input.setAttribute('aria-controls', list.id);
      const status = document.createElement('span'); status.className = 'visually-hidden'; status.setAttribute('role', 'status');
      select.before(root); root.append(input, clear, select, status); document.body.append(list);
      if (label) label.htmlFor = input.id;
      select.hidden = true; select.required = false; input.required = required;
      let matches = [], active = -1;
      const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
      const position = () => {
        const box = input.getBoundingClientRect();
        const below = window.innerHeight - box.bottom - 8;
        const height = Math.min(300, Math.max(below, box.top - 8));
        Object.assign(list.style, {left: `${box.left}px`, width: `${box.width}px`, maxHeight: `${height}px`, top: below >= Math.min(220, height) ? `${box.bottom + 4}px` : 'auto', bottom: below >= Math.min(220, height) ? 'auto' : `${window.innerHeight - box.top + 4}px`});
      };
      const sync = () => {
        input.value = select.value ? select.selectedOptions[0]?.textContent.trim() || '' : '';
        input.disabled = select.disabled; clear.disabled = select.disabled; input.setCustomValidity(''); close();
      };
      const choose = option => { select.value = option.value; select.dispatchEvent(new Event('change', {bubbles: true})); input.focus(); close(); };
      const setActive = index => {
        if (!matches.length) return;
        active = (index + matches.length) % matches.length;
        [...list.children].forEach((row, i) => row.setAttribute('aria-selected', String(i === active)));
        input.setAttribute('aria-activedescendant', list.children[active].id); list.children[active].scrollIntoView({block: 'nearest'});
      };
      const render = () => {
        const words = normalize(input.value.trim()).split(/\s+/).filter(Boolean);
        const options = [...select.options].filter(option => !option.disabled && option.value);
        const filtered = options.filter(option => words.every(word => normalize(option.textContent).includes(word)));
        matches = filtered.slice(0, 40); list.replaceChildren(); active = -1;
        input.removeAttribute('aria-activedescendant');
        matches.forEach((option, i) => {
          const row = document.createElement('div'); row.id = `${list.id}-${i}`; row.className = 'ims-select__option'; row.setAttribute('role', 'option'); row.setAttribute('aria-selected', 'false'); row.textContent = option.textContent;
          row.addEventListener('pointerdown', event => { event.preventDefault(); choose(option); }); list.append(row);
        });
        if (!matches.length) { const empty = document.createElement('div'); empty.className = 'ims-select__empty'; empty.textContent = 'No matches. Try another name or code.'; list.append(empty); }
        status.textContent = `${filtered.length} matches${filtered.length > 40 ? '. Keep typing to narrow the results.' : '. Use arrow keys and Enter to choose.'}`;
        list.hidden = false; input.setAttribute('aria-expanded', 'true'); position();
      };
      input.addEventListener('focus', () => { if (!select.value) render(); });
      input.addEventListener('click', () => { if (select.value) { input.select(); input.value = ''; } render(); });
      input.addEventListener('input', () => { select.value = ''; input.setCustomValidity(input.value ? 'Choose a suggestion from the list.' : ''); render(); });
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape' || event.key === 'Tab') { close(); return; }
        if (event.key === 'Enter' && !list.hidden) { event.preventDefault(); if (matches.length) choose(matches[active < 0 ? 0 : active]); return; }
        if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
        event.preventDefault(); if (list.hidden) render(); setActive(active < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1) : active + (event.key === 'ArrowDown' ? 1 : -1));
      });
      clear.addEventListener('click', () => { select.value = ''; select.dispatchEvent(new Event('change', {bubbles: true})); input.focus(); render(); });
      input.addEventListener('blur', () => {
        if (!select.value && !input.value) select.dispatchEvent(new Event('change', {bubbles: true}));
        // Restore the committed value if a user only opened the list.
        if (select.value) sync(); else close();
      });
      select.addEventListener('change', sync);
      select.form?.addEventListener('reset', () => setTimeout(sync, 0));
      window.addEventListener('resize', close); window.addEventListener('scroll', event => { if (event.target !== list) close(); }, true);
      sync();
    });
  };
  window.initSearchableSelects();
})();
