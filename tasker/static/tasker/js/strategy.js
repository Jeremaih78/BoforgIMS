/* Accessible typed scenario rows; every preview and commit remains server-authorized. */
(() => {
  'use strict';
  const form = document.querySelector('[data-strategy-scenario-form]');
  if (!form) return;
  const rows = form.querySelector('[data-strategy-change-rows]');
  const add = form.querySelector('[data-strategy-add-change]');
  const total = form.querySelector('[name="changes-TOTAL_FORMS"]');
  const template = form.querySelector('#strategyChangeTemplate');
  let facts = [];
  try { facts = JSON.parse(document.getElementById('strategyTaskInitialData')?.textContent || '[]'); } catch (_) { /* Explicit fields remain usable. */ }
  const source = new Map(facts.map(item => [String(item.id), item]));
  const wire = row => {
    const task = row.querySelector('[name$="-task"]');
    task?.addEventListener('change', () => {
      const value = source.get(task.value);
      if (!value) return;
      for (const key of ['owner', 'priority', 'due_date', 'due_time']) {
        const field = row.querySelector(`[name$="-${key}"]`);
        if (field) field.value = value[key] ?? '';
      }
    });
    const excluded = row.querySelector('[name$="-DELETE"]');
    const setExcluded = () => {
      row.classList.toggle('is-excluded', Boolean(excluded?.checked));
      for (const field of row.querySelectorAll('input,select')) {
        if (field !== excluded) field.disabled = Boolean(excluded?.checked);
      }
    };
    excluded?.addEventListener('change', setExcluded);
    setExcluded();
  };
  for (const row of rows?.querySelectorAll('[data-strategy-change-row]') || []) wire(row);
  if (add && total && template && rows) {
    const update = () => { add.disabled = Number(total.value) >= 10; };
    add.addEventListener('click', () => {
      const index = Number(total.value);
      if (!Number.isInteger(index) || index < 0 || index >= 10) return;
      const fragment = template.content.cloneNode(true);
      for (const element of fragment.querySelectorAll('[name],[id],[for]')) {
        for (const attribute of ['name', 'id', 'for']) {
          if (element.hasAttribute(attribute)) element.setAttribute(attribute, element.getAttribute(attribute).replaceAll('__prefix__', String(index)));
        }
      }
      const row = fragment.querySelector('[data-strategy-change-row]');
      const number = row.querySelector('[data-strategy-row-number]');
      if (number) number.textContent = String(index + 1);
      rows.appendChild(fragment);
      total.value = String(index + 1);
      wire(row);
      update();
      row.querySelector('select')?.focus();
    });
    update();
  }
})();
