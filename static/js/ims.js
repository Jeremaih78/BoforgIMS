// Contain wide legacy tables and retain native form validation/submitter names.
window.prepareIMSTables = (scope = document) => scope.querySelectorAll('table').forEach(table => {
  if (!table.closest('main')) return;
  if (table.closest('.table-responsive, .table-wrapper')) return;
  const wrapper = document.createElement('div');
  wrapper.className = 'table-responsive';
  wrapper.tabIndex = 0;
  wrapper.setAttribute('role', 'region');
  wrapper.setAttribute('aria-label', 'Scrollable records');
  table.before(wrapper);
  wrapper.append(table);
});
window.prepareIMSTables();
document.querySelectorAll('input[type=number]').forEach(input => {
  input.inputMode = input.step && input.step !== '1' ? 'decimal' : 'numeric';
});
document.querySelectorAll('input[name=phone]').forEach(input => { input.type = 'tel'; });
