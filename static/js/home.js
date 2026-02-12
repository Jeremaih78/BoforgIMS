(() => {
  const header = document.querySelector('.site-header');
  const navToggle = document.querySelector('.nav-toggle');
  const primaryNav = document.getElementById('primary-nav');

  if (header && navToggle && primaryNav) {
    navToggle.addEventListener('click', () => {
      const isOpen = header.classList.toggle('nav-open');
      navToggle.setAttribute('aria-expanded', String(isOpen));
    });

    primaryNav.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', () => {
        if (header.classList.contains('nav-open')) {
          header.classList.remove('nav-open');
          navToggle.setAttribute('aria-expanded', 'false');
        }
      });
    });
  }

  const accordions = document.querySelectorAll('[data-accordion] .faq-question');
  accordions.forEach((btn) => {
    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      const targetId = btn.getAttribute('aria-controls');
      const panel = targetId ? document.getElementById(targetId) : null;
      btn.setAttribute('aria-expanded', String(!expanded));
      if (panel) {
        panel.hidden = expanded;
      }
    });
  });

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (e) => {
      const target = document.querySelector(link.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
})();
