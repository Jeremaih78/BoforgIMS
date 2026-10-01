document.querySelectorAll('[data-label-selection]').forEach(form => {
  const all = form.querySelector('[data-select-all]');
  if (!all) return;
  const boxes = [...form.querySelectorAll('input[name=ids]')];
  const status = form.querySelector('[data-selection-status]');
  const print = form.querySelector('[data-print-labels]');
  function update() {
    const count = all.checked ? Number(form.dataset.total) : boxes.filter(box => box.checked).length;
    status.textContent = `${count} label${count === 1 ? '' : 's'} selected${all.checked ? ' across all pages' : ' on this page'}`;
    print.textContent = count ? `Print ${count} label${count === 1 ? '' : 's'}` : 'Print selected labels';
    print.disabled = count === 0 || count > 1000;
    if (count > 1000) status.textContent += '. Narrow your filters to 1,000 units or fewer.';
  }
  all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
  boxes.forEach(box => box.addEventListener('change', () => { all.checked = false; update(); }));
  form.querySelector('[data-select-page]').addEventListener('click', () => {
    all.checked = false; boxes.forEach(box => { box.checked = true; }); update();
  });
  form.querySelector('[data-clear-selection]').addEventListener('click', () => {
    all.checked = false; boxes.forEach(box => { box.checked = false; }); update();
  });
  update();
});
