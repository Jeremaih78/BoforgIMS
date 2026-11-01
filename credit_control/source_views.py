"""Canonical follow-up workflow. Tasker only links here; it cannot post money."""
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .services.tasker_sources import get_collection_task, get_promise, change_collection_task, assign_promise
from .source_forms import SourceRevisionForm, CollectionWorkForm, PromiseAssignmentForm
from .source_policies import can_assign_promise, can_change_source, source_manager


def _task_page(request,item,*,form=None,error="",status=200):
    initial={"expected_revision":item.revision,"assigned_to":item.assigned_to_id,"due_date":item.due_date,"priority":item.priority}
    return render(request,"credit_control/source_task.html",{"item":item,"error":error,
        "work_form":form or CollectionWorkForm(user=request.user,item=item,initial=initial),
        "can_change":can_change_source(request.user,item),"client_key":uuid.uuid4()},status=status)


@login_required
@require_GET
@never_cache
def task_detail(request,pk):
    return _task_page(request,get_collection_task(user=request.user,pk=pk))


@login_required
@require_POST
@never_cache
def task_action(request,pk,action):
    item=get_collection_task(user=request.user,pk=pk)
    if not can_change_source(request.user,item):
        raise PermissionDenied
    initial={"assigned_to":item.assigned_to_id}
    form=CollectionWorkForm(request.POST,user=request.user,item=item,initial=initial) if action=="metadata" else SourceRevisionForm(request.POST)
    if not form.is_valid():
        return _task_page(request,item,form=form if action=="metadata" else None,error="The source action was not saved. Review the form and current state.",status=400)
    values={}
    if action=="metadata":
        values={"assigned_to_id":form.cleaned_data["assigned_to"].pk if form.cleaned_data["assigned_to"] else None,
            "due_date":form.cleaned_data["due_date"].isoformat(),"priority":form.cleaned_data["priority"]}
    try:
        change_collection_task(user=request.user,pk=pk,expected_revision=form.cleaned_data["expected_revision"],
            client_key=form.cleaned_data["client_key"],action=action,values=values)
    except ValidationError as exc:
        item=get_collection_task(user=request.user,pk=pk)
        return _task_page(request,item,form=form if action=="metadata" else None,error="; ".join(exc.messages),status=409)
    messages.success(request,"Collection workflow updated. This does not record or post a payment.")
    return redirect("ims:credit_control:task_detail",pk=pk)


def _promise_page(request,item,*,form=None,error="",status=200):
    can_assign=can_assign_promise(request.user,item)
    return render(request,"credit_control/source_promise.html",{"item":item,"error":error,"can_assign":can_assign,
        "assignment_form":form or PromiseAssignmentForm(user=request.user,item=item,initial={"expected_revision":item.revision,"assigned_to":item.assigned_to_id}) if can_assign else None},status=status)


@login_required
@require_GET
@never_cache
def promise_detail(request,pk):
    return _promise_page(request,get_promise(user=request.user,pk=pk))


@login_required
@require_POST
@never_cache
def promise_assign(request,pk):
    item=get_promise(user=request.user,pk=pk)
    if not can_assign_promise(request.user,item):
        raise PermissionDenied
    form=PromiseAssignmentForm(request.POST,user=request.user,item=item)
    if not form.is_valid():
        return _promise_page(request,item,form=form,error="Responsibility was not saved. Review the form.",status=400)
    try:
        assign_promise(user=request.user,pk=pk,assigned_to_id=form.cleaned_data["assigned_to"].pk if form.cleaned_data["assigned_to"] else None,
            expected_revision=form.cleaned_data["expected_revision"],client_key=form.cleaned_data["client_key"])
    except ValidationError as exc:
        return _promise_page(request,get_promise(user=request.user,pk=pk),form=form,error="; ".join(exc.messages),status=409)
    messages.success(request,"Promise responsibility updated. Payment evidence and promise status are unchanged.")
    return redirect("ims:credit_control:promise_detail",pk=pk)
