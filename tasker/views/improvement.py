"""Employee improvement workspace. All writes use scoped domain services."""
from datetime import date,timedelta
from functools import wraps
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist,PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import redirect,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods,require_POST

from tasker import improvement_forms as forms
from tasker.improvement_models import DayClosure,ExperimentResult,SOPExecution,WeeklyReview
from tasker.models import DailyPlan,DailyReview,Task
from tasker.organization import can_manage_team,visible_teams_for,require_task_access
from tasker.services import improvement as work,improvement_reviews as reviews,routines
from tasker.services.captures import visible_captures_for
from tasker.services.coordination import scoped_tasks_for
from tasker.services.work_dates import work_date
from tasker.services.workbench import work_version


def screen(methods=('GET','POST')):
    def decorate(fn):
        @login_required
        @never_cache
        @require_http_methods(list(methods))
        @wraps(fn)
        def view(request,*args,**kwargs):
            if not getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False):raise Http404
            work.actor(request.user)
            try:return fn(request,*args,**kwargs)
            except ObjectDoesNotExist:raise Http404
        return view
    return decorate


def error_text(error):
    return ' '.join(error.messages) if isinstance(error,ValidationError) else str(error) if isinstance(error,ValueError) else 'This request changed or was already processed. Reload and compare your retained draft.'


def valid(form,request):
    if not form.is_valid():return False
    if set(request.POST)-(set(form.fields)|{'csrfmiddlewaretoken'}):
        form.add_error(None,'Use the current form. Your entered values are retained.');return False
    return True


def show(request,name,context=None,status=200):
    context=context or {}
    context.setdefault('nav',name.split('_')[0])
    return render(request,'tasker/improvement/'+name+'.html',context,status=status)


def team_scope(request):
    raw=request.GET.get('team','')
    if not raw:return None
    if not raw.isdecimal():raise Http404
    team=visible_teams_for(request.user).filter(pk=int(raw)).first()
    if not team:raise PermissionDenied
    return team


@screen()
def home(request):
    team=team_scope(request)
    raw=request.GET.get('week','')
    try:week=date.fromisoformat(raw) if raw else work_date(request.user)-timedelta(days=work_date(request.user).weekday())
    except ValueError:raise Http404
    try:facts=reviews.weekly_facts(user=request.user,team=team,week_start=week)
    except ValidationError:raise Http404
    can_record=request.user.has_perm('tasker.add_weeklyreview') and (not team or can_manage_team(request.user,team))
    form=forms.WeeklyForm(request.POST if request.method=='POST' else None,shared=bool(team),initial={'week_start':week})
    status=200
    if request.method=='POST':
        if not can_record:raise PermissionDenied
        status=400
        if valid(form,request):
            try:
                if form.cleaned_data['week_start']!=week:raise ValidationError('The review week changed. Reload this scope.')
                reviews.record_weekly_review(user=request.user,team=team,**form.cleaned_data)
                messages.success(request,'Weekly review recorded.');return redirect(request.get_full_path())
            except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc));status=409
    history=work.scoped_records(WeeklyReview,request.user).filter(week_start=week,team=team).order_by('-created_at','-pk')[:10] if request.user.has_perm('tasker.view_weeklyreview') else []
    carry=work.carry_candidates(request.user) if not team else None
    return show(request,'weekly',{'facts':facts,'team':team,'teams':visible_teams_for(request.user),'form':form,
        'history':history,'can_record':can_record,'carry':list(carry[:12]) if carry is not None else [],
        'carry_total':carry.count() if carry is not None else None,'nav':'weekly'},status)


@screen(('GET',))
def sop_list(request):
    rows=work.templates_for(request.user).select_related('owner','team').order_by('archived','title','pk')
    return show(request,'sop_list',{'page':Paginator(rows,24).get_page(request.GET.get('page')),'nav':'sop'})


@screen()
def sop_edit(request,pk=None):
    item=work.templates_for(request.user).get(pk=pk) if pk else None
    if item and not work.can_edit(request.user,item):raise PermissionDenied
    form=forms.SOPForm(request.POST if request.method=='POST' else None,user=request.user,template=item)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            try:
                saved=work.publish_sop(user=request.user,pk=pk,**form.cleaned_data)
                messages.success(request,'SOP version published. Existing executions are preserved.')
                return redirect('ims:tasker:sop_detail',pk=saved.pk)
            except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc));status=409
    return show(request,'form',{'form':form,'title':'Publish a new SOP version' if item else 'Create a reusable SOP',
        'intro':'A clear outcome and small checklist make repeat work easier. Choose sharing deliberately.',
        'submit':'Publish SOP','nav':'sop'},status)


def sop_context(user,item,run_form=None):
    return {'item':item,'version':item.versions.get(number=item.latest_version),'versions':item.versions.order_by('-number')[:30],
        'run_form':run_form or forms.SOPRunForm(initial={'expected_revision':str(item.revision),'due_date':work_date(user)}),
        'command':forms.CommandForm(initial={'expected_revision':str(item.revision)}),'can_edit':work.can_edit(user,item),
        'nav':'sop'}


@screen(('GET',))
def sop_detail(request,pk):
    item=work.templates_for(request.user).get(pk=pk)
    context=sop_context(request.user,item)
    raw=request.GET.get('version','')
    if raw:
        if not raw.isdecimal():raise Http404
        context['version']=item.versions.get(number=int(raw))
    return show(request,'sop_detail',context)


@screen(('POST',))
def sop_run(request,pk):
    item=work.templates_for(request.user).get(pk=pk)
    form=forms.SOPRunForm(request.POST)
    if valid(form,request):
        try:
            task=work.use_sop(user=request.user,pk=pk,**form.cleaned_data)
            return redirect(task.get_absolute_url())
        except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
    return show(request,'sop_detail',sop_context(request.user,item,form),409)


@screen(('POST',))
def sop_archive(request,pk):
    form=forms.CommandForm(request.POST)
    item=work.templates_for(request.user).get(pk=pk)
    if valid(form,request):
        try:
            work.archive_sop(user=request.user,pk=pk,archived=not item.archived,**form.cleaned_data)
            return redirect('ims:tasker:sop_detail',pk=pk)
        except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
    context=sop_context(request.user,item);context['command']=form
    return show(request,'sop_detail',context,409)


@screen(('GET',))
def routine_list(request):
    rows=routines.routines_for(request.user).select_related('template','owner','team').order_by('paused','next_on','pk')
    return show(request,'routine_list',{'page':Paginator(rows,24).get_page(request.GET.get('page')),
        'templates':work.templates_for(request.user).filter(archived=False).order_by('title')[:100],'nav':'routine'})


@screen()
def routine_edit(request,pk=None):
    item=routines.routines_for(request.user).select_related('template').get(pk=pk) if pk else None
    if item and not routines.can_configure(request.user,item):raise PermissionDenied
    raw=request.GET.get('sop','')
    if not item and not raw.isdecimal():return redirect('ims:tasker:routine_list')
    template=item.template if item else work.templates_for(request.user).get(pk=int(raw))
    form=forms.RoutineForm(request.POST if request.method=='POST' else None,user=request.user,template=template,row=item)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            data=dict(form.cleaned_data);data['owner_id']=data.pop('owner').pk
            try:
                item=routines.save_routine(user=request.user,pk=pk,**data)
                return redirect('ims:tasker:routine_detail',pk=item.pk)
            except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc));status=409
    return show(request,'form',{'form':form,'title':'Recurring work','intro':'Set an explicit calendar and missed-work policy. Release time creates a task; it does not start a timer or approve an output.','submit':'Save routine','nav':'routine'},status)


def routine_context(user,item):
    reasons={'calendar_exception':'Ambiguous or non-existent local time','explicit_exception':'Deliberately skipped date',
        'missed_day_skipped':'Past day skipped','newer_occurrence_due':'A newer occurrence was due','prior_work_open':'Earlier work is still open',
        'no_effective_sop_version':'No published SOP version at release time','missed_range_skipped':'Missed calendar range skipped'}
    occurrences=list(item.occurrences.select_related('task','version').order_by('-scheduled_on','-pk')[:30])
    for row in occurrences:
        row.reason_label=reasons.get(row.reason_code,'')
        row.task_readable=bool(row.task_id and scoped_tasks_for(user).filter(pk=row.task_id).exists())
    return {'item':item,'occurrences':occurrences,'exceptions':item.exceptions.order_by('-scheduled_on')[:20],
        'command':forms.CommandForm(initial={'expected_revision':str(item.revision)}),
        'exception_form':forms.ExceptionForm(initial={'expected_revision':str(item.revision)}),
        'can_edit':routines.can_configure(user,item),'nav':'routine'}


@screen(('GET',))
def routine_detail(request,pk):return show(request,'routine_detail',routine_context(request.user,routines.routines_for(request.user).select_related('template','owner','team').get(pk=pk)))


@screen(('POST',))
def routine_action(request,pk,action):
    item=routines.routines_for(request.user).get(pk=pk)
    form=forms.ExceptionForm(request.POST) if action=='skip' else forms.CommandForm(request.POST)
    if valid(form,request):
        try:
            if action in ('pause','resume'):routines.set_paused(user=request.user,pk=pk,paused=action=='pause',**form.cleaned_data)
            elif action=='skip':routines.skip_date(user=request.user,pk=pk,**form.cleaned_data)
            else:raise Http404
            return redirect('ims:tasker:routine_detail',pk=pk)
        except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
    context=routine_context(request.user,item)
    context['exception_form' if action=='skip' else 'command']=form
    return show(request,'routine_detail',context,409)


@screen()
def carry_decision(request,slug):
    task=scoped_tasks_for(request.user).get(slug=slug)
    from tasker.policies import can_change_task
    if not can_change_task(request.user,task):raise PermissionDenied
    form=forms.CarryForm(request.POST if request.method=='POST' else None,user=request.user,task=task)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            try:
                work.decide_carry(user=request.user,task_id=task.pk,**form.cleaned_data)
                messages.success(request,'Carry-over decision recorded.');return redirect('ims:tasker:improvement_home')
            except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc));status=409
    return show(request,'carry',{'task':task,'form':form,'nav':'weekly'},status)


@screen(('GET',))
def ideas(request):
    team=team_scope(request)
    return show(request,'ideas',{'ideas':reviews.idea_review(user=request.user,team=team),'team':team,
        'teams':visible_teams_for(request.user),'nav':'idea'})


@screen()
def experiment_create(request,capture_id):
    capture=visible_captures_for(request.user).get(pk=capture_id)
    if capture.owner_id!=request.user.pk:raise PermissionDenied
    form=forms.ExperimentForm(request.POST if request.method=='POST' else None,user=request.user,capture=capture)
    status=200
    if request.method=='POST':
        status=400
        if valid(form,request):
            try:
                trial=reviews.promote_experiment(user=request.user,capture_id=capture.pk,**form.cleaned_data)
                return redirect('ims:tasker:experiment_detail',pk=trial.pk)
            except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc));status=409
    return show(request,'form',{'form':form,'title':'Try a small experiment','intro':'Define a measurable test. Team experiments require an idea already deliberately shared with that team. The original idea body is retained at its source.','submit':'Create experiment','nav':'experiment'},status)


@screen(('GET',))
def experiment_list(request):
    rows=work.experiments_for(request.user).select_related('owner','team','task').order_by('review_on','pk')
    return show(request,'experiment_list',{'page':Paginator(rows,24).get_page(request.GET.get('page')),'nav':'experiment'})


def experiment_context(user,item,form=None):
    results=list(item.results.select_related('experiment__task','accepted_by','outcome').order_by('-created_at','-pk')[:20])
    accepted=set(item.results.filter(origin_id__in=[row.pk for row in results]).values_list('origin_id',flat=True))
    for row in results:
        row.current=reviews.result_current(row);row.acceptance_recorded=row.pk in accepted
    return {'item':item,'results':results,'form':form or forms.ResultForm(initial={'expected_revision':str(item.revision)}),
        'ownership_changed':item.owner_id!=item.task.owner_id,
        'owner_form':forms.ExperimentOwnerForm(initial={'expected_revision':str(item.revision),'expected_task_version':work_version(item.task)}),
        'command':forms.CommandForm(initial={'expected_revision':str(item.revision)}),
        'source_readable':visible_captures_for(user).filter(pk=item.source_capture_id).exists(),
        'task_readable':scoped_tasks_for(user).filter(pk=item.task_id).exists(),
        'can_record':item.owner_id==user.pk or item.team_id and can_manage_team(user,item.team_id),
        'can_accept':can_manage_team(user,item.team_id) if item.team_id else item.owner_id==user.pk,'nav':'experiment'}


@screen(('POST',))
def experiment_owner(request,pk):
    form=forms.ExperimentOwnerForm(request.POST)
    if valid(form,request):
        try:
            reviews.sync_experiment_owner(user=request.user,pk=pk,**form.cleaned_data)
            messages.success(request,'Experiment ownership aligned with the delegated task. Earlier observations remain historical.')
            return redirect('ims:tasker:experiment_detail',pk=pk)
        except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
    item=work.experiments_for(request.user).select_related('task','owner','team').get(pk=pk)
    context=experiment_context(request.user,item);context['owner_form']=form
    return show(request,'experiment_detail',context,409)


@screen()
def experiment_detail(request,pk):
    item=work.experiments_for(request.user).select_related('task','owner','team').get(pk=pk)
    context=experiment_context(request.user,item)
    if request.method=='POST':
        form=forms.ResultForm(request.POST)
        if valid(form,request):
            try:
                reviews.record_result(user=request.user,pk=pk,**form.cleaned_data)
                return redirect('ims:tasker:experiment_detail',pk=pk)
            except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
        return show(request,'experiment_detail',experiment_context(request.user,item,form),409)
    return show(request,'experiment_detail',context)


@screen(('POST',))
def experiment_accept(request,pk,result_id):
    form=forms.CommandForm(request.POST)
    if valid(form,request):
        try:
            reviews.accept_result(user=request.user,pk=pk,result_id=result_id,**form.cleaned_data)
            return redirect('ims:tasker:experiment_detail',pk=pk)
        except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
    item=work.experiments_for(request.user).get(pk=pk)
    context=experiment_context(request.user,item);context['command']=form
    return show(request,'experiment_detail',context,409)


def day_context(user,plan,review):
    from tasker.services.day_closure import review_version
    from tasker.services.planner import plan_version
    closure=DayClosure.objects.filter(owner=user,plan=plan).first()
    return {'closure':closure,'day_action_form':forms.DayActionForm(initial={
        'expected_revision':str(closure.revision) if closure else 'new','expected_plan_version':plan_version(plan),
        'expected_review_version':review_version(review)})}


@screen(('POST',))
def day_action(request,plan_id,action):
    from tasker.services.day_closure import change_day
    if action not in ('close','reopen'):raise Http404
    form=forms.DayActionForm(request.POST)
    if valid(form,request):
        try:
            change_day(user=request.user,plan_id=plan_id,closed=action=='close',**form.cleaned_data)
            messages.success(request,'Your day is closed. Shared work facts are queued for reporting.' if action=='close' else 'Your day is reopened. A declaration revision is recorded.')
            return redirect('ims:tasker:daily_review')
        except (ValidationError,IntegrityError,ValueError) as exc:form.add_error(None,error_text(exc))
    return show(request,'form',{'form':form,'title':'Review your End day declaration',
        'intro':'Your draft is retained. Recheck the current plan and private review before closing or reopening.',
        'submit':'Retry declaration','nav':'weekly'},409)
