document.addEventListener('DOMContentLoaded', function () {
  const modalEl = document.getElementById('customerQuickCreateModal');
  const form = document.getElementById('customerQuickCreateForm');
  if (!modalEl || !form || !window.bootstrap) return;

  const modal = new bootstrap.Modal(modalEl);
  const errorsEl = document.getElementById('customerQuickCreateErrors');
  const nameInput = document.getElementById('quick_customer_name');
  let activeSelectId = null;

  function showErrors(errors) {
    if (!errorsEl) return;
    const messages = [];
    Object.keys(errors || {}).forEach((field) => {
      const values = Array.isArray(errors[field]) ? errors[field] : [errors[field]];
      values.forEach((message) => messages.push(`${field}: ${message}`));
    });
    errorsEl.textContent = messages.join(' ');
    errorsEl.classList.toggle('d-none', messages.length === 0);
  }

  document.querySelectorAll('.js-customer-modal-open').forEach((button) => {
    button.addEventListener('click', () => {
      activeSelectId = button.dataset.customerSelect;
      form.reset();
      showErrors({});
      modal.show();
      setTimeout(() => nameInput && nameInput.focus(), 150);
    });
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    showErrors({});
    const response = await fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      showErrors(data.errors || {'Error': 'Customer could not be saved.'});
      return;
    }

    const select = document.getElementById(activeSelectId);
    if (select) {
      const option = new Option(data.customer.name, data.customer.id, true, true);
      option.dataset.phone = data.customer.phone || '';
      option.dataset.email = data.customer.email || '';
      select.add(option, select.options[0] || null);
      select.value = String(data.customer.id);
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    modal.hide();
    modalEl.addEventListener('hidden.bs.modal', () => {
      (document.getElementById(`${activeSelectId}-search`) || select)?.focus();
    }, {once: true});
  });
});
