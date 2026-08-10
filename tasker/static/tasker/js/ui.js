(() => {
  const loader = document.getElementById('taskerRouteLoader');
  const config = document.getElementById('taskerUIConfig')?.dataset || {};
  const showLoader = () => loader?.classList.add('is-active');
  const isEditableTarget = (target) => target?.matches('input, textarea, select, [contenteditable="true"]');

  document.querySelectorAll('form').forEach((form) => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"], input[type="submit"]');
      if (!button || button.disabled) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      if (button.tagName === 'BUTTON') {
        const spinner = document.createElement('span');
        spinner.className = 'tasker-submit-spinner';
        spinner.setAttribute('aria-hidden', 'true');
        button.prepend(spinner);
      }
      showLoader();
    });
  });

  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || link.target === '_blank') return;
    const url = new URL(link.href, window.location.href);
    if (url.origin === window.location.origin && !link.hasAttribute('download') && !url.hash) showLoader();
  });

  document.querySelectorAll('.task-card, .tasker-card, .planner-panel').forEach((element, index) => {
    element.dataset.taskerAnimate = '';
    element.style.setProperty('--stagger', Math.min(index, 8));
  });

  document.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || isEditableTarget(event.target)) return;
    const key = event.key.toLowerCase();
    if (key === 'n') {
      event.preventDefault();
      if (event.shiftKey && config.createUrl) window.location.assign(config.createUrl);
      else {
        const modalElement = document.getElementById('quickAddModal');
        if (modalElement) {
          bootstrap.Modal.getOrCreateInstance(modalElement).show();
          modalElement.addEventListener('shown.bs.modal', () => document.getElementById('globalTaskTitle')?.focus(), { once: true });
        }
      }
    } else if (key === 't') {
      if (config.todayUrl) window.location.assign(config.todayUrl);
    } else if (key === 'p') {
      if (config.plannerUrl) window.location.assign(config.plannerUrl);
    } else if (key === '/') {
      const search = document.querySelector('[data-task-search]');
      if (search) { event.preventDefault(); search.focus(); }
    } else if (event.key === '?') {
      const modalElement = document.getElementById('taskerShortcutsModal');
      if (modalElement) bootstrap.Modal.getOrCreateInstance(modalElement).show();
    }
  });
})();
