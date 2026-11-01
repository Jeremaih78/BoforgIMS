"""Source-authorized operational actions without a second completion authority."""
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from tasker.business_forms import BusinessActionFilterForm
from tasker.services import business_links


def _enabled():
    if not business_links.enabled():
        raise Http404


def _present(user, link):
    # Live authorization and source facts replace possibly delayed cached owner,
    # status and deadline fields. This call never reconciles or writes on GET.
    return business_links.present_business_link(user=user, link=link)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def business_list(request):
    _enabled()
    data = request.GET.copy()
    data.setdefault("state", "active")
    data.setdefault("scope", "mine")
    form = BusinessActionFilterForm(data)
    valid = form.is_valid()
    choices = form.cleaned_data if valid else {"state": "active", "source": "", "scope": "mine"}
    rows = business_links.visible_business_links_for(request.user,
        state=choices["state"], source_type=choices.get("source") or None,
        mine=choices["scope"] == "mine").select_related("owner", "company")
    page = Paginator(rows, 24).get_page(request.GET.get("page"))
    items = []
    for item in page.object_list:
        try:
            current = _present(request.user, item)
        except PermissionDenied:
            # A source can leave this actor's scope between page selection and
            # presentation. Its saved title/body must not remain visible.
            continue
        # Keep the selected queue honest if a source changes after SQL page
        # selection but before its live presentation is read.
        if choices["scope"] == "mine" and current.owner_id != request.user.pk:
            continue
        active = current.status in ("OPEN", "IN_PROGRESS", "UNASSIGNED")
        if (choices["state"] == "active" and not active) or (choices["state"] == "closed" and active):
            continue
        items.append(current)
    return render(request, "tasker/business/list.html", {"filter_form": form,
        "page_obj": page, "items": items}, status=200 if valid else 400)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def business_detail(request, pk):
    _enabled()
    # get_business_link already presents the current, authorized source copy.
    item = business_links.get_business_link(user=request.user, pk=pk)
    return render(request, "tasker/business/detail.html", _detail_context(request.user,item))


def _detail_context(user,item,form=None,error=None):
    from tasker.services.business_outcomes import can_attribute
    from tasker.business_forms import BusinessContributionForm
    from tasker.business_outcome_models import BusinessOutcomeEvidence
    can=can_attribute(user,item)
    return {'item':item,'can_attribute':can,'contribution_form':form or (
        BusinessContributionForm(user=user,link=item) if can else None), 'evidence_error':error,
        'outcome_proof':BusinessOutcomeEvidence.objects.filter(link=item).select_related('outcome').first()}


@login_required
@permission_required('tasker.view_task',raise_exception=True)
@require_POST
@never_cache
def business_contribution(request,pk):
    _enabled()
    from tasker.business_forms import BusinessContributionForm
    from tasker.services.business_outcomes import attribute
    item=business_links.get_business_link(user=request.user,pk=pk)
    form=BusinessContributionForm(request.POST,user=request.user,link=item)
    if form.is_valid():
        try:
            attribute(user=request.user,link_id=pk,**form.cleaned_data)
            return redirect('ims:tasker:business_detail',pk=pk)
        except ValidationError as exc:
            form.add_error(None,' '.join(exc.messages))
    return render(request,'tasker/business/detail.html',_detail_context(request.user,item,form=form),status=409)


@login_required
@permission_required('tasker.view_task',raise_exception=True)
@require_POST
@never_cache
def business_accept_output(request,pk):
    _enabled()
    from tasker.services.business_outcomes import accept_source_output
    item=business_links.get_business_link(user=request.user,pk=pk)
    try:
        accept_source_output(user=request.user,link_id=pk,expected_revision=request.POST.get('expected_revision'))
        return redirect('ims:tasker:business_detail',pk=pk)
    except ValidationError as exc:
        return render(request,'tasker/business/detail.html',_detail_context(request.user,item,
            error=' '.join(exc.messages)),status=409)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def business_open_source(request, pk):
    _enabled()
    item = business_links.get_business_link(user=request.user, pk=pk)
    return redirect(business_links.business_source_url(user=request.user, link=item))
