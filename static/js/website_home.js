(() => {
  'use strict';
  const menu = document.querySelector('.bh-menu');
  const nav = document.getElementById('home-navigation');
  if (menu && nav) {
    const close = () => { nav.classList.remove('is-open'); menu.setAttribute('aria-expanded', 'false'); };
    menu.addEventListener('click', () => {
      const open = nav.classList.toggle('is-open');
      menu.setAttribute('aria-expanded', String(open));
    });
    nav.addEventListener('click', event => { if (event.target.closest('a')) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && nav.classList.contains('is-open')) { close(); menu.focus(); } });
    document.addEventListener('click', event => { if (!event.target.closest('.bh-header')) close(); });
  }
  const form = document.getElementById('setup-guide');
  const configNode = document.getElementById('home-guide-config');
  if (!form || !configNode) return;
  let config;
  try { config = JSON.parse(configNode.textContent); } catch (_) { return; }
  const goalInput = form.elements.namedItem('goal');
  const budgetInput = form.elements.namedItem('budget');
  const experienceInput = form.elements.namedItem('experience');
  const title = document.getElementById('guide-title');
  const description = document.getElementById('guide-description');
  const whatsapp = document.getElementById('guide-whatsapp');
  const shop = document.getElementById('guide-shop');
  if (![goalInput, budgetInput, experienceInput, title, description, whatsapp, shop].every(Boolean)) return;
  const update = () => {
    const goal = config.goals[goalInput.value];
    const budget = config.budgets[budgetInput.value];
    const experience = config.experience[experienceInput.value];
    if (!goal || !budget || !experience) return;
    title.textContent = goal.title;
    description.textContent = goal.description;
    shop.href = goal.url;
    const message = `Hello Boforg, please help me choose a printing setup.\nI want to make: ${goal.label}.\nBudget: ${budget}.\nExperience: ${experience}.\nPlease confirm equipment availability, compatible supplies, delivery and any training included.`;
    whatsapp.href = `https://wa.me/${config.whatsappNumber}?${new URLSearchParams({text: message})}`;
  };
  let started = false;
  form.addEventListener('change', () => {
    update();
    if (!started && typeof window.gtag === 'function') {
      started = true;
      try { window.gtag('event', 'setup_guide_started', {goal: goalInput.value}); } catch (_) {}
    }
  });
  form.addEventListener('submit', event => { event.preventDefault(); update(); whatsapp.focus({preventScroll: true}); });
  update();
  form.classList.add('bh-guide-enhanced');
})();
