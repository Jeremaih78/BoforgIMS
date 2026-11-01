/* Labels and values are always written as text, never HTML. */
document.querySelectorAll('[data-custom-fields]').forEach(root => {
  const source = root.querySelector('[data-custom-fields-source]');
  let data;
  try { data = JSON.parse(source.value || '{}'); } catch (_) { return; }
  if (!data || Array.isArray(data) || typeof data !== 'object') return;
  const fields = document.createElement('div');
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'btn btn-outline-primary btn-sm mt-2'; button.textContent = '+ Add custom field';
  source.after(fields, button);
  source.hidden = true;
  const sync = () => {
    const value = Object.create(null);
    let valid = true;
    fields.querySelectorAll('[data-custom-row]').forEach(row => {
      const [label, text] = row.querySelectorAll('input');
      const key = label.value.trim();
      label.setCustomValidity('');
      if (!key && !text.value) return;
      if (!key || Object.hasOwn(value, key)) {
        label.setCustomValidity(key ? 'Use a unique field name.' : 'Enter a field name.'); valid = false;
      }
      value[key] = text.value;
    });
    source.value = JSON.stringify(value);
    button.disabled = fields.children.length >= 12;
    return valid;
  };
  const add = (labelValue = '', textValue = '') => {
    const row = document.createElement('div'); row.dataset.customRow = ''; row.className = 'd-flex gap-2 mb-2';
    const label = document.createElement('input'); label.className = 'form-control'; label.placeholder = 'Field name'; label.maxLength = 60; label.setAttribute('aria-label', 'Custom field name'); label.value = labelValue;
    const text = document.createElement('input'); text.className = 'form-control'; text.placeholder = 'Value'; text.maxLength = 500; text.setAttribute('aria-label', 'Custom field value'); text.value = textValue;
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn-outline-danger btn-sm'; remove.textContent = '×'; remove.setAttribute('aria-label', 'Remove custom field');
    remove.addEventListener('click', () => { row.remove(); sync(); });
    row.append(label, text, remove); fields.append(row); row.addEventListener('input', sync); sync(); return label;
  };
  Object.entries(data).forEach(([key, value]) => add(key, value));
  button.addEventListener('click', () => { if (fields.children.length < 12) add().focus(); });
  source.form?.addEventListener('submit', event => {
    if (!sync()) { event.preventDefault(); fields.querySelector('input:invalid')?.reportValidity(); }
  });
});
