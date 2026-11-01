from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_hosts.resolvers import reverse as host_reverse

from accounting.models import Company
from inventory.replenishment_policies import company_ids, current_actor
from .forms import ConfigureForm, ReviewForm
from .models import KINDS
from .policies import can_manage, can_setup_company, capable, setup_companies, visible
from .registry import CHECKLISTS, SOURCES, facts, spec_for
from . import services


def _context(user):
    actor = current_actor(user)
    kinds = [(kind,label) for kind,label in KINDS if capable(actor,kind)]
    if not kinds:
        raise PermissionDenied
    return {'kinds':kinds,'companies':Company.objects.filter(pk__in=company_ids(actor)).order_by('name')}


@login_required
@require_GET
@never_cache
def home(request):
    context = _context(request.user)
    rows = visible(request.user).select_related('company','team','assigned_to').order_by('next_review_on','pk')
    kind = request.GET.get('kind','')
    company = request.GET.get('company','')
    if kind:
        spec_for(kind)
        rows = rows.filter(kind=kind)
    if company:
        if not str(company).isdecimal():
            raise PermissionDenied
        rows = rows.filter(company_id=company)
    page = Paginator(rows,24).get_page(request.GET.get('page'))
    for row in page:
        row.current = facts(row)
    context.update(page=page,selected_kind=kind,selected_company=company)
    return render(request,'operations/home.html',context)


def _detail_context(user,row,form=None):
    spec = spec_for(row.kind)
    manager = can_manage(user,row.kind,row.company_id,row.team_id)
    can_review = capable(user,row.kind,work=True) and (manager or row.assigned_to_id == user.pk)
    native_url = ''
    if spec.native_route and capable(user,row.kind,work=True):
        native_url=host_reverse(spec.native_route,kwargs={'pk':row.source_id},host='ims')
    elif user.is_staff and user.has_perm(spec.permission):
        app,model=spec.model.split('.')
        native_url=host_reverse(f'admin:{app}_{model.lower()}_change',args=(row.source_id,),host='ims')
    return dict(row=row,manager=manager,can_review=can_review,native_url=native_url,
        form=form or ReviewForm(row=row,manager=manager),active=row.current['status'] in ('OPEN','IN_PROGRESS'))


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def detail(request,pk):
    row = services.get_row(request.user,pk)
    context = _detail_context(request.user,row)
    if request.method == 'POST':
        if not context['can_review']:
            raise PermissionDenied
        form = ReviewForm(request.POST,row=row,manager=context['manager'])
        status = 400
        if form.is_valid():
            allowed_fields=set(form.fields)|{'csrfmiddlewaretoken'}
            if set(request.POST)-allowed_fields:
                form.add_error(None,'Use the current operational review form.')
            else:
                data=form.cleaned_data
                try:
                    services.review(user=request.user,pk=pk,
                        **{key:data[key] for key in ('expected_revision','expected_source_version','client_key','next_review_on','notes')},
                        checklist={label:data[f'check_{i}'] for i,label in enumerate(CHECKLISTS.get(row.kind,()))},
                        accept_quality=data.get('accept_quality',False),contact_done=data.get('contact_done',False))
                    return redirect('ims:operations:detail',pk=pk)
                except ValidationError as exc:
                    form.add_error(None,' '.join(exc.messages));status=409
        context.update(form=form,retained=True)
        return render(request,'operations/detail.html',context,status=status)
    return render(request,'operations/detail.html',context)


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def configure(request,pk=None):
    actor=current_actor(request.user)
    row=services.get_row(actor,pk) if pk else None
    kind=row.kind if row else request.GET.get('kind','shipment_work')
    spec_for(kind)
    companies=setup_companies(actor)
    raw_company=str(row.company_id) if row else request.GET.get('company','')
    company=companies.filter(pk=int(raw_company)).first() if raw_company.isdecimal() else None
    context=dict(kinds=[(k,label) for k,label in KINDS if capable(actor,k)],companies=companies,
        kind=kind,company=company,row=row)
    if company and not can_setup_company(actor,kind,company.pk):
        raise PermissionDenied
    if row and not can_manage(actor,kind,row.company_id,row.team_id):
        raise PermissionDenied
    if not company:
        if request.method == 'POST':
            raise PermissionDenied
        return render(request,'operations/configure.html',context)
    form=ConfigureForm(request.POST if request.method=='POST' else None,user=actor,kind=kind,company_id=company.pk,row=row)
    status=200
    if request.method=='POST':
        status=400
        if form.is_valid():
            data=form.cleaned_data
            try:
                if set(request.POST)-(set(form.fields)|{'csrfmiddlewaretoken'}):
                    raise ValidationError('Use the current responsibility form.')
                item=services.configure(user=actor,kind=kind,source_id=data['source'].pk,company_id=company.pk,
                    team_id=data['team'].pk if data['team'] else None,
                    assigned_to_id=data['assigned_to'].pk if data['assigned_to'] else None,
                    **{key:data.get(key) for key in ('enabled','priority','next_review_on','contact_interval_days','sla_due_on','expected_revision','client_key')})
                return redirect('ims:operations:detail',pk=item.pk)
            except ValidationError as exc:
                form.add_error(None,' '.join(exc.messages));status=409
    context['form']=form
    return render(request,'operations/configure.html',context,status=status)
