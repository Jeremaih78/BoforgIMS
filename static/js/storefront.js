(() => {
  let toastTimer;
  let cartQueue = Promise.resolve();
  const notify = (message, isError = false) => {
    const toast = document.getElementById('cart-notification');
    if (!toast) return;
    clearTimeout(toastTimer);
    toast.textContent = isError ? 'Unable to update: ' + message : message;
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
  };
  const flashMessages = document.querySelector('[data-store-messages]');
  if (flashMessages) {
    const messages = [...flashMessages.querySelectorAll('.store-message')];
    flashMessages.hidden = true;
    notify(messages.map(message => message.textContent.trim()).join(' '), messages.some(message => message.dataset.level.includes('error')));
  }
  document.addEventListener('submit', async e => {
    const form = e.target.closest('form[data-cart-add], form[data-cart-remove]');
    if (!form || new URL(form.action, location.href).origin !== location.origin) return;
    e.preventDefault();
    if (form.dataset.pending) return;
    const submit = e.submitter || form.querySelector('button[type="submit"], button:not([type])');
    form.dataset.pending = 'true';
    form.setAttribute('aria-busy', 'true');
    if (submit) submit.disabled = true;
    const previous = cartQueue;
    let release;
    cartQueue = new Promise(resolve => { release = resolve; });
    await previous;
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'Accept': 'application/json'}, redirect: 'error'
      });
      const isJson = (response.headers.get('content-type') || '').includes('application/json');
      const result = isJson ? await response.json() : {};
      if (!response.ok) {
        notify(result.message || 'Could not update your cart. Check availability or refresh the page and try again.', true);
        return;
      }
      if (!isJson) throw new Error('Unexpected cart response');
      const counter = document.getElementById('cart-counter');
      if (counter) counter.textContent = String(result.cart_count);
      if (result.cart_html) {
        const content = document.getElementById('cart-content');
        if (content) {
          const x = window.scrollX, y = window.scrollY;
          const hadFocus = content.contains(document.activeElement);
          // Retain the occupied space so removing a final/bottom row cannot
          // shrink the document and force the browser back up the page.
          content.style.minHeight = content.getBoundingClientRect().height + 'px';
          const fragment = document.createElement('template');
          fragment.innerHTML = result.cart_html;
          const updated = fragment.content.querySelector('#cart-content');
          if (updated) content.replaceChildren(...updated.childNodes);
          if (hadFocus) {
            const target = content.querySelector('button, a');
            if (target) target.focus({preventScroll: true});
          }
          window.scrollTo({left: x, top: y, behavior: 'instant'});
        }
      }
      notify(result.message);
      if (result.analytics && typeof window.gtag === 'function') {
        try {
          const {event, ...data} = result.analytics;
          if (data.value !== undefined) data.value = Number(data.value);
          window.gtag('event', event, data);
        } catch (_) {}
      }
    } catch (_) {
      // The server may have accepted the POST: do not automatically resend it.
      notify('Could not confirm the update. Check your cart before trying again.', true);
    } finally {
      release();
      delete form.dataset.pending;
      form.removeAttribute('aria-busy');
      if (submit) submit.disabled = false;
    }
  });
  const button = document.querySelector('.store-menu');
  const nav = document.querySelector('#store-navigation');
  if (button && nav) {
    const close = () => { nav.classList.remove('is-open'); button.setAttribute('aria-expanded', 'false'); };
    button.addEventListener('click', () => { const open = nav.classList.toggle('is-open'); button.setAttribute('aria-expanded', String(open)); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && nav.classList.contains('is-open')) { close(); button.focus(); } });
    nav.addEventListener('click', e => { if (e.target.closest('a')) close(); });
  }
  for (const id of ['store-events', 'store-page-event', 'store-purchase-events']) {
    const node = document.getElementById(id);
    if (!node || typeof window.gtag !== 'function') continue;
    try {
      const value = JSON.parse(node.textContent);
      for (const data of (Array.isArray(value) ? value : [value])) {
        if (!data || !data.event) continue;
        const {event, ...parameters} = data;
        if (parameters.value !== undefined) parameters.value = Number(parameters.value);
        window.gtag('event', event, parameters);
      }
    } catch (_) {}
  }
  // One delegated handler; analytics is optional and must never block navigation.
  document.addEventListener('click', e => {
    const link = e.target.closest('[data-event]');
    if (!link || typeof window.gtag !== 'function') return;
    try { window.gtag('event', link.dataset.event, {item_id: link.dataset.item || '', transport_type: 'beacon'}); } catch (_) {}
  });
  window.boforgTrackWhatsApp = () => { if (typeof window.gtag === 'function') window.gtag('event', 'whatsapp_click'); };
})();
