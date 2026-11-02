(() => {
  const form=document.querySelector('[data-money-form]');if(!form)return;
  const kind=form.querySelector('[name="kind"]');
  const update=()=>{
    if(!kind || !['TRANSFER','IN','OUT'].includes(kind.value))return;
    for(const [name,show] of [['source_account',kind.value!=='IN'],['destination_account',kind.value!=='OUT'],['counter_account',kind.value!=='TRANSFER']]){
      const block=form.querySelector(`[data-money-field="${name}"]`);if(!block)continue;
      block.hidden=!show;const input=block.querySelector('select');input.disabled=!show;input.required=show;
      input.dispatchEvent(new Event('change',{bubbles:true}));
      if(show)window.initSearchableSelects?.(block);
    }
  };
  kind?.addEventListener('change',update);update();
  form.addEventListener('focusin',event=>{
    if(!event.target.matches('input:not([type="hidden"]),select,textarea'))return;
    requestAnimationFrame(()=>{
      const actions=form.querySelector('.money-savebar')?.getBoundingClientRect();
      const rect=event.target.getBoundingClientRect();
      if(actions && rect.bottom>actions.top-12 && rect.top<actions.bottom)event.target.scrollIntoView({block:'center'});
    });
  });
  form.addEventListener('submit',event=>{
    if(form.dataset.saving){event.preventDefault();return;}
    setTimeout(()=>{if(event.defaultPrevented)return;form.dataset.saving='1';form.querySelectorAll('button[type="submit"]').forEach(b=>b.disabled=true);},0);
  });
  window.addEventListener('pageshow',()=>{delete form.dataset.saving;form.querySelectorAll('button[type="submit"]').forEach(b=>b.disabled=false);});
})();
