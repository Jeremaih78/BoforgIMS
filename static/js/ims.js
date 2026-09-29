// Contain wide legacy tables and retain native form validation/submitter names.
document.querySelectorAll('main table').forEach(table => {
  if (table.closest('.table-responsive, .table-wrapper')) return;
  const wrapper = document.createElement('div');
  wrapper.className = 'table-responsive';
  wrapper.tabIndex = 0;
  wrapper.setAttribute('role', 'region');
  wrapper.setAttribute('aria-label', 'Scrollable records');
  table.before(wrapper);
  wrapper.append(table);
});
document.querySelectorAll('input[type=number]').forEach(input => {
  input.inputMode = input.step && input.step !== '1' ? 'decimal' : 'numeric';
});
document.querySelectorAll('input[name=phone]').forEach(input => { input.type = 'tel'; });
