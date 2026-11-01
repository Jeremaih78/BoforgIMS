/* Progressive disclosure only. Server services validate every decision. */
document.querySelectorAll('[data-carry-decision]').forEach(form => {
  const action = form.querySelector('[name="action"]');
  const needs = {CLARIFY: ['definition_of_done'], SPLIT: ['title', 'definition_of_done', 'due_date'],
    DELEGATE: ['assignee'], RESCHEDULE: ['due_date'], STOP: []};
  const refresh = () => {
    ['title', 'definition_of_done', 'due_date', 'assignee'].forEach(name => {
      const region = form.querySelector(`[data-improve-field="${name}"]`);
      if (region) region.hidden = !(needs[action.value] || []).includes(name);
    });
  };
  action.addEventListener('change', refresh);
  refresh();
});
