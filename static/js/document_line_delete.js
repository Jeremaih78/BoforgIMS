(function () {
  document.addEventListener('submit', async (event) => {
      const form = event.target.closest('.js-line-delete-form');
      if (!form) return;
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;

      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        alert(data.error || 'Line could not be removed.');
        if (button) button.disabled = false;
        return;
      }

      const row = form.closest('tr');
      if (row) row.remove();
      for (const [selector,key] of [['[data-document-subtotal]','subtotal'],['[data-document-tax]','tax']]) {
        const node=document.querySelector(selector);if(node && data[key]!==undefined)node.textContent=data[key];
      }
      const totalEl = document.querySelector(form.dataset.totalTarget);
      if (totalEl) totalEl.textContent = data.total;
  });
})();
