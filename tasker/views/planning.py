"""Local capacity, deliberate schedule acceptance and dependency screens."""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from tasker.models import Availability, OrganizationAccess, ScheduleBlock, Task, WorkPolicy
from tasker.organization import managed_teams_for
from tasker.planning_forms import AvailabilityForm, EffortForm, ScheduleForm, WorkPolicyForm
from tasker.policies import can_change_task, visible_tasks_for
from tasker.services.capacity import capacity_for_day
from tasker.services.effort import effort_for_task, set_remaining_effort
from tasker.services.scheduling import accept_schedule, cancel_schedule, preview_schedule, save_schedule_draft


def policy_zone(user):
    from tasker.services.work_dates import work_zone
    return work_zone(user)


def planning_date(request, *, subject=None):
    raw = request.GET.get("date", "")
    if not raw:
        return timezone.now().astimezone(policy_zone(subject or request.user)).date()
    try:
        day = date.fromisoformat(raw)
        if len(raw) != 10 or not 1900 <= day.year <= 2200:
            raise ValueError
        return day
    except ValueError:
        raise Http404("Choose an ISO calendar date.") from None


def _editable_tasks(user):
    tasks = visible_tasks_for(user)
    if not user.has_perm("tasker.change_task"):
        return tasks.none()
    if not user.is_superuser:
        companies = OrganizationAccess.objects.filter(user=user, is_active=True, company__is_active=True, can_manage=True).values("company_id") if user.has_perm("tasker.manage_organization") or user.has_perm("tasker.manage_tasker") else []
        tasks = tasks.filter(Q(owner=user) | Q(team_id__in=managed_teams_for(user).values("pk")) | Q(company_id__in=companies))
    return tasks


def _task(request, slug, *, edit=False):
    task = get_object_or_404(visible_tasks_for(request.user), slug=slug)
    if edit and not can_change_task(request.user, task):
        raise PermissionDenied
    return task


def _block(request, pk, *, edit=False):
    if pk < 1 or len(str(pk))>18:
        raise Http404
    block = get_object_or_404(ScheduleBlock.objects.select_related("task", "owner"), pk=pk, task__in=visible_tasks_for(request.user))
    if edit and not can_change_task(request.user, block.task):
        raise PermissionDenied
    return block


def _errors(form, error):
    form.add_error(None, error if isinstance(error, ValidationError) else str(error))


def _plan_url(block):
    zone = policy_zone(block.owner)
    return reverse("ims:tasker:planning_home") + "?" + urlencode({"date":block.starts_at.astimezone(zone).date().isoformat(), "owner":block.owner_id})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_GET
def planning_home(request):
    subject = request.user
    raw = request.GET.get("owner", "")
    if raw:
        if not raw.isdecimal() or len(raw)>18 or int(raw)<1:
            raise Http404
        subject = get_object_or_404(get_user_model(), pk=int(raw), is_active=True)
    day = planning_date(request, subject=subject)
    try:
        capacity = capacity_for_day(user=subject, day=day, actor=request.user)
    except ValidationError as error:
        return render(request,"tasker/planning/error.html",{"planning_error":" ".join(error.messages)},status=400)
    zone = policy_zone(subject)
    start = datetime.combine(day,time.min,tzinfo=zone)
    end = datetime.combine(day+timedelta(days=1),time.min,tzinfo=zone)
    blocks = list(ScheduleBlock.objects.filter(owner=subject, task__in=visible_tasks_for(request.user), task__owner=F("owner"), starts_at__lt=end, ends_at__gt=start).select_related("task").order_by("starts_at","pk")[:100])
    for block in blocks:
        block.local_start, block.local_end = block.starts_at.astimezone(zone), block.ends_at.astimezone(zone)
    return render(request,"tasker/planning/home.html",{"capacity":capacity,"blocks":blocks,"plan_date":day,"previous_date":day-timedelta(days=1),"next_date":day+timedelta(days=1),"subject":subject,"is_self":subject.pk==request.user.pk,"zone_name":zone.key})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_http_methods(["GET","POST"])
def work_policy(request):
    from tasker.services.capacity import save_work_policy
    policy = WorkPolicy.objects.filter(owner=request.user).first()
    initial = {"timezone_name":"Africa/Harare","weekdays":[0,1,2,3,4],"start_time":"08:00","end_time":"17:00"}
    if policy:
        initial.update({name:getattr(policy,name) for name in ("timezone_name","weekdays","start_time","end_time","buffer_minutes")})
        initial["expected_version"] = policy.revision
    form = WorkPolicyForm(request.POST if request.method=="POST" else None,initial=initial)
    if request.method=="POST" and form.is_valid():
        try:
            save_work_policy(user=request.user,**form.cleaned_data)
        except (ValidationError,ValueError) as error:
            _errors(form,error)
        else:
            messages.success(request,"Your work hours and interruption buffer are saved.")
            return redirect("ims:tasker:planning_home")
    return render(request,"tasker/planning/policy.html",{"form":form,"policy":policy})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_http_methods(["GET","POST"])
def availability_home(request):
    from tasker.services.capacity import save_availability
    zone=policy_zone(request.user)
    day=planning_date(request)
    form=AvailabilityForm(request.POST if request.method=="POST" else None,zone=zone,initial={"starts_at":f"{day.isoformat()}T12:00","ends_at":f"{day.isoformat()}T13:00","kind":"COMMITMENT"})
    if request.method=="POST" and form.is_valid():
        data=form.cleaned_data.copy(); data.pop("expected_version",None)
        try:
            save_availability(user=request.user,**data)
        except (ValidationError,ValueError) as error:
            _errors(form,error)
        else:
            messages.success(request,"Your unavailable interval is saved; task deadlines are unchanged.")
            return redirect(reverse("ims:tasker:availability_home")+"?date="+day.isoformat())
    items=list(Availability.objects.filter(owner=request.user).order_by("-starts_at","-pk")[:100])
    for item in items:
        item.local_start,item.local_end=item.starts_at.astimezone(zone),item.ends_at.astimezone(zone)
    return render(request,"tasker/planning/availability.html",{"form":form,"items":items,"plan_date":day,"zone_name":zone.key})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def availability_remove(request,pk):
    from tasker.services.capacity import remove_availability
    if pk < 1 or len(str(pk))>18:
        raise Http404
    item=get_object_or_404(Availability.objects,pk=pk,owner=request.user)
    try:
        remove_availability(user=request.user,item=item,expected_version=request.POST.get("expected_version",""))
    except (ValidationError,ValueError) as error:
        return render(request,"tasker/planning/error.html",{"planning_error":str(error)},status=409)
    messages.success(request,"Unavailable interval removed; its audit record is retained.")
    return redirect("ims:tasker:availability_home")


@login_required
@permission_required(("tasker.view_task","tasker.change_task"),raise_exception=True)
@never_cache
@require_http_methods(["GET","POST"])
def schedule_edit(request,pk=None):
    block=_block(request,pk,edit=True) if pk else None
    selected=block.task if block else _task(request,request.GET["task"],edit=True) if request.GET.get("task") else None
    if request.method=="GET" and selected is None:
        return render(request,"tasker/planning/choose_task.html",{"tasks":_editable_tasks(request.user).filter(archived=False).exclude(status__in=(Task.Status.COMPLETED,Task.Status.CANCELLED)).order_by("title")[:100]})
    # A delegated task is planned in its owner's work timezone.
    if request.method=="POST" and not selected:
        raw=request.POST.get("task","")
        if raw.isdecimal() and len(raw)<=18:
            selected=_editable_tasks(request.user).filter(pk=int(raw)).first()
    zone=policy_zone(selected.owner if selected else request.user)
    day=planning_date(request,subject=selected.owner if selected else None)
    initial={"starts_at":f"{day.isoformat()}T08:00","ends_at":f"{day.isoformat()}T09:00"}
    if selected: initial["task"]=selected.pk
    if block:
        initial.update(starts_at=block.starts_at.astimezone(zone).strftime("%Y-%m-%dT%H:%M"),ends_at=block.ends_at.astimezone(zone).strftime("%Y-%m-%dT%H:%M"),expected_version=block.revision)
    tasks=_editable_tasks(request.user).filter(archived=False).exclude(status__in=(Task.Status.COMPLETED,Task.Status.CANCELLED)).order_by("title")
    if selected: tasks=tasks.filter(pk=selected.pk)
    form=ScheduleForm(request.POST if request.method=="POST" else None,zone=zone,tasks=tasks,initial=initial)
    if request.method=="POST" and form.is_valid():
        try:
            saved=save_schedule_draft(user=request.user,block=block,**form.cleaned_data)
        except (ValidationError,ValueError) as error:
            _errors(form,error)
        else:
            return redirect("ims:tasker:schedule_detail",pk=saved.pk)
    return render(request,"tasker/planning/schedule_form.html",{"form":form,"scheduled_block":block,"selected_task":selected,"zone_name":zone.key})


def _preview(request,block,*,error="",status=200):
    block=_block(request,block.pk)
    if not can_change_task(request.user,block.task):
        zone=policy_zone(block.owner)
        return render(request,"tasker/planning/block_readonly.html",{"scheduled_block":block,"zone_name":zone.key,"local_start":block.starts_at.astimezone(zone),"local_end":block.ends_at.astimezone(zone)})
    try:
        preview=preview_schedule(user=request.user,task=block.task,starts_at=block.starts_at,ends_at=block.ends_at,block=block)
    except ValidationError as exc:
        return render(request,"tasker/planning/error.html",{"planning_error":"Capacity cannot be resolved for this timezone boundary. The saved block is retained. "+" ".join(exc.messages)},status=400)
    zone=policy_zone(block.owner)
    return render(request,"tasker/planning/schedule_detail.html",{"scheduled_block":block,"preview":preview,"planning_error":error,"zone_name":zone.key,"local_start":block.starts_at.astimezone(zone),"local_end":block.ends_at.astimezone(zone),"can_edit":can_change_task(request.user,block.task),"back_url":_plan_url(block),"override_draft":request.POST.get("override_reason","")[:1000]},status=status)


@login_required
@permission_required("tasker.view_task",raise_exception=True)
@never_cache
@require_GET
def schedule_detail(request,pk):
    return _preview(request,_block(request,pk))


@login_required
@permission_required(("tasker.view_task","tasker.change_task"),raise_exception=True)
@never_cache
@require_POST
def schedule_accept(request,pk):
    block=_block(request,pk,edit=True)
    try:
        accept_schedule(user=request.user,block=block,expected_version=request.POST.get("expected_version",""),preview_version=request.POST.get("preview_version",""),override_reason=request.POST.get("override_reason",""))
    except (ValidationError,ValueError) as error:
        block.refresh_from_db()
        return _preview(request,block,error=str(error),status=409)
    messages.success(request,"Work block accepted. The task deadline is unchanged.")
    return redirect(_plan_url(block))


@login_required
@permission_required(("tasker.view_task","tasker.change_task"),raise_exception=True)
@never_cache
@require_POST
def schedule_cancel(request,pk):
    block=_block(request,pk,edit=True)
    try:
        cancel_schedule(user=request.user,block=block,expected_version=request.POST.get("expected_version",""))
    except (ValidationError,ValueError) as error:
        block.refresh_from_db()
        return _preview(request,block,error=str(error),status=409)
    messages.success(request,"Work block cancelled; its history is retained.")
    return redirect(_plan_url(block))


@login_required
@permission_required(("tasker.view_task","tasker.change_task"),raise_exception=True)
@never_cache
@require_http_methods(["GET","POST"])
def task_effort(request,slug):
    task=_task(request,slug,edit=True)
    effort=effort_for_task(task=task)
    form=EffortForm(request.POST if request.method=="POST" else None,initial={"remaining_minutes":effort["remaining_minutes"],"expected_version":effort.get("version")})
    if request.method=="POST" and form.is_valid():
        try:
            set_remaining_effort(user=request.user,task=task,**form.cleaned_data)
        except (ValidationError,ValueError) as error:
            _errors(form,error)
        else:
            messages.success(request,"Remaining effort updated. Task acceptance and deadlines are unchanged.")
            return redirect(task.get_absolute_url())
    return render(request,"tasker/planning/effort.html",{"task":task,"effort":effort,"form":form})


@login_required
@permission_required("tasker.view_task",raise_exception=True)
@never_cache
@require_http_methods(["GET","POST"])
def task_dependencies(request,slug):
    from tasker.services.dependencies import add_dependency, dependency_candidates, readiness_for, remove_dependency
    task=_task(request,slug,edit=request.method=="POST")
    error=""
    if request.method=="POST":
        try:
            expected=request.POST.get("expected_version","")
            action=request.POST.get("action","")
            raw=request.POST.get("prerequisite" if action=="add" else "dependency_id","")
            if not raw.isdecimal() or len(raw)>18 or int(raw)<1:
                raise ValidationError("Choose a valid prerequisite or dependency link.")
            if action=="add":
                prerequisite=get_object_or_404(visible_tasks_for(request.user),pk=int(raw))
                add_dependency(task=task,prerequisite=prerequisite,user=request.user,expected_version=expected)
            elif action=="remove":
                remove_dependency(task=task,dependency_id=int(raw),user=request.user,expected_version=expected)
            else:
                raise ValidationError("Choose a supported dependency action.")
        except (ValidationError,ValueError) as exc:
            error=" ".join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
        else:
            messages.success(request,"Dependency updated. Readiness reflects current prerequisite outcomes.")
            return redirect("ims:tasker:task_dependencies",slug=task.slug)
    readiness=readiness_for(task=task,user=request.user)
    return render(request,"tasker/planning/dependencies.html",{"task":task,"readiness":readiness,"planning_error":error,"candidates":dependency_candidates(task=task,user=request.user),"can_edit":can_change_task(request.user,task)},status=409 if error else 200)
