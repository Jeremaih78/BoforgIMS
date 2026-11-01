"""Thin, scoped pilot screens and exports; no deployment or provider requests."""
import csv,io
import uuid
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied,ObjectDoesNotExist
from django.db import IntegrityError
from django.http import Http404,HttpResponse
from django.shortcuts import render,redirect
from django.core.paginator import Paginator
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from tasker import pilot_forms as forms
from tasker.pilot_models import METRICS
from tasker.services import pilots,baseline
from tasker.services.pilot_metrics import pilot_report
from tasker.services.pilot_release import release_readiness,record_release_check,requirement_catalog


def screen(methods=('GET','POST')):
    def wrap(fn):
        @login_required
        @never_cache
        @require_http_methods(list(methods))
        @wraps(fn)
        def view(request,*args,**kwargs):
            if not getattr(settings,'TASKER_PILOT_ENABLED',False):raise Http404
            pilots.actor(request.user)
            try:return fn(request,*args,**kwargs)
            except ObjectDoesNotExist:raise Http404
        return view
    return wrap
def valid(form,request):
    if not form.is_valid():return False
    if set(request.POST)-(set(form.fields)|{'csrfmiddlewaretoken'}):form.add_error(None,'Use the current form; your draft is retained.');return False
    return True
def error(form,exc):form.add_error(None,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'This submission changed or was already recorded. Reload and compare your draft.')
def show(request,name,context,status=200):return render(request,'tasker/pilot/'+name+'.html',context,status=status)
def get(request,pk):return pilots.visible_pilots(request.user).get(pk=pk)
def context(user,pilot):
    report=pilot_report(user=user,pilot=pilot)
    for key,cell in report['cells'].items():cell['label']=key.replace('_',' ').title()
    return {'pilot':pilot,'report':report,'can_manage':pilots.manages(user,pilot),
        'page':Paginator(pilots.observations_for(user,pilot).order_by('-observed_on','-pk'),30).get_page(1),
        'command':forms.CommandForm(initial={'expected_revision':str(pilot.revision)}),
        'control_form':forms.ControlForm(initial={'expected_revision':str(pilot.revision)}),
        'validation_form':forms.ValidationForm(initial={'expected_revision':str(pilot.revision),'report_digest':report['report_digest']}),
        'readiness':release_readiness(user=user,pilot=pilot),
        'rollout_writes_enabled':getattr(settings,'TASKER_ROLLOUT_WRITES_ENABLED',False)}


@screen(('GET',))
def pilot_list(request):
    rows=pilots.visible_pilots(request.user).order_by('-created_at','-pk')
    return show(request,'list',{'page':Paginator(rows,24).get_page(request.GET.get('page')),
        'studies':[s for s in baseline.visible_studies_for(request.user) if baseline._can_manage(request.user,s)]})


@screen()
def pilot_edit(request,pk=None):
    item=get(request,pk) if pk else None
    if item and not pilots.manages(request.user,item):raise PermissionDenied
    raw=request.GET.get('study','')
    if not item and not raw.isdecimal():return redirect('ims:tasker:pilot_list')
    study=item.study if item else baseline.visible_studies_for(request.user).get(pk=int(raw))
    if not baseline._can_manage(request.user,study):raise PermissionDenied
    form=forms.PilotForm(request.POST if request.method=='POST' else None,user=request.user,pilot=item,study=study)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            try:
                item=pilots.save_pilot(user=request.user,pk=pk,**form.cleaned_data)
                return redirect('ims:tasker:pilot_detail',pk=item.pk)
            except (ValidationError,IntegrityError) as exc:error(form,exc);status=409
    return show(request,'form',{'form':form,'title':'Define a prospective pilot','intro':'The owner nominated admin and chose a staggered matched rollout. Freeze role, workload, sources, dates and quality rules before real rollout.','submit':'Save draft protocol'},status)


@screen(('GET',))
def pilot_detail(request,pk):
    item=get(request,pk);ctx=context(request.user,item)
    ctx['control_action']='resume' if item.state=='PAUSED' else 'pause'
    ctx['control_label']='Resume approved pilot' if item.state=='PAUSED' else 'Pause access and recording'
    for member in ctx['report']['members']:member.control_token=uuid.uuid4()
    ctx['page']=Paginator(pilots.observations_for(request.user,item).order_by('-observed_on','-pk'),30).get_page(request.GET.get('page'))
    for row in ctx['page']:row.review_token=uuid.uuid4()
    return show(request,'detail',ctx)


@screen()
def pilot_member(request,pk):
    item=get(request,pk)
    if not pilots.manages(request.user,item):raise PermissionDenied
    form=forms.MemberForm(request.POST if request.method=='POST' else None,user=request.user,pilot=item)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            try:
                pilots.add_member(user=request.user,pk=pk,**form.cleaned_data)
                return redirect('ims:tasker:pilot_detail',pk=pk)
            except (ValidationError,IntegrityError) as exc:error(form,exc);status=409
    return show(request,'form',{'form':form,'title':'Choose one cohort member','intro':'A roster is not access authorization. Match one early and one later employee per block. Later rollout is after the measurement window.','submit':'Add member'},status)


@screen(('POST',))
def pilot_action(request,pk,action):
    item=get(request,pk);form=forms.CommandForm(request.POST) if action=='approve' else forms.ControlForm(request.POST)
    if valid(form,request):
        try:
            if action=='approve':pilots.approve_protocol(user=request.user,pk=pk,**form.cleaned_data)
            else:pilots.control_pilot(user=request.user,pk=pk,action=action,**form.cleaned_data)
            messages.success(request,'Pilot decision recorded. Real deployment and provider activation remain separate actions.')
            return redirect('ims:tasker:pilot_detail',pk=pk)
        except (ValidationError,IntegrityError) as exc:error(form,exc)
    return show(request,'form',{'form':form,'title':'Review pilot control','intro':'The current protocol and operating checks are required. Your entered values are retained.','submit':'Retry'},409)


@screen()
def pilot_observation(request,pk,observation_id=None):
    item=get(request,pk);row=pilots.observations_for(request.user,item).get(pk=observation_id) if observation_id else None
    form=forms.ObservationForm(request.POST if request.method=='POST' else None,user=request.user,pilot=item,row=row)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            data=dict(form.cleaned_data);data['outcome_ids']=list(data.pop('outcomes').values_list('pk',flat=True))
            try:
                pilots.save_observation(user=request.user,pk=pk,observation_id=observation_id,**data)
                return redirect('ims:tasker:pilot_detail',pk=pk)
            except (ValidationError,IntegrityError) as exc:error(form,exc);status=409
    return show(request,'observation_form',{'pilot':item,'can_manage':pilots.manages(request.user,item),'form':form},status)


@screen(('POST',))
def pilot_observation_review(request,pk,observation_id,action):
    form=forms.ReviewForm(request.POST)
    if valid(form,request):
        try:
            pilots.review_observation(user=request.user,pk=pk,observation_id=observation_id,action=action,**form.cleaned_data)
            return redirect('ims:tasker:pilot_detail',pk=pk)
        except (ValidationError,IntegrityError) as exc:error(form,exc)
    return show(request,'form',{'form':form,'title':'Review observation','intro':'Acceptance requires an independent manager and current source proof. Retraction preserves the earlier evidence.','submit':'Retry review'},409)


@screen()
def pilot_release(request,pk):
    item=get(request,pk)
    if not pilots.manages(request.user,item):raise PermissionDenied
    form=forms.ReleaseForm(request.POST if request.method=='POST' else None)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            try:record_release_check(user=request.user,pk=pk,**form.cleaned_data);return redirect('ims:tasker:pilot_release',pk=pk)
            except (ValidationError,IntegrityError) as exc:error(form,exc);status=409
    return show(request,'release',{'pilot':item,'readiness':release_readiness(user=request.user,pilot=item),'form':form,
        'catalog':requirement_catalog()},status)


@screen(('POST',))
def pilot_validate(request,pk):
    form=forms.ValidationForm(request.POST)
    if valid(form,request):
        try:pilots.validate_productivity(user=request.user,pk=pk,**form.cleaned_data);return redirect('ims:tasker:pilot_detail',pk=pk)
        except (ValidationError,IntegrityError) as exc:error(form,exc)
    return show(request,'form',{'form':form,'title':'Review productivity evidence','intro':'Synthetic, incomplete, premature, contaminated or imprecise results cannot pass validation.','submit':'Retry validation'},409)


@screen(('GET',))
def pilot_export(request,pk):
    item=get(request,pk);rows=pilots.observations_for(request.user,item).select_related('pilot__study').prefetch_related('outcomes').order_by('observed_on','employee_id','pk')
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['pilot','synthetic','employee_id','phase','observed_on','status','source_current',*METRICS,'exposed','evidence','source_key'])
    for row in rows.iterator(chunk_size=100):
        from tasker.services.pilot_metrics import _row
        facts=_row(row)
        values=[item.title,item.is_synthetic,row.employee_id,row.phase,str(row.observed_on),row.status,pilots.source_current(row),*[facts[k] for k in METRICS],row.tasker_exposed,row.evidence,row.source_key]
        writer.writerow([baseline._csv_cell(v) for v in values])
    response=HttpResponse(stream.getvalue(),content_type='text/csv; charset=utf-8');response['Content-Disposition']=f'attachment; filename="tasker-pilot-{item.pk}.csv"'
    return response
