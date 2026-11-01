"""Canonical Inventory reviews; reads never mutate stock or refresh a projection."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import BigIntegerField, Case, F, IntegerField, Value, When
from django.db.models.functions import Cast
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from inventory.models import Product
from inventory.replenishment_forms import CompanyChoiceForm, ResponsibilityForm, ReviewForm
from inventory.replenishment_models import ReplenishmentPlan
from inventory.replenishment_policies import (can_change_plan, can_manage_plan, source_read_capable,
    visible_companies_for, visible_plans_for)
from inventory.services.replenishment import (configure_plan, change_plan, effective_plan_state, get_plan)


def _reader(request):
    if not source_read_capable(request.user):
        raise PermissionDenied


def _present(item):
    item.current = effective_plan_state(item)
    item.need_label = dict(ReplenishmentPlan.NeedState.choices).get(item.current["need_state"],"Source review needed")
    item.review_label = dict(ReplenishmentPlan.ReviewState.choices).get(item.current["review_state"],"Review the source")
    if not item.is_enabled or item.current["need_state"] != "LOW":
        item.review_label = "No active procurement step"
    item.can_progress = item.is_enabled and item.current["need_state"] == "LOW"
    return item


@login_required
@require_GET
@never_cache
def replenishment_list(request):
    _reader(request)
    state = request.GET.get("state","low")
    scope = request.GET.get("scope","visible")
    error = ""
    if state not in ("low","clear","all") or scope not in ("visible","mine"):
        state,scope,error = "low","visible","Choose a supported replenishment view. Current low-stock reviews are shown below."
    rows = visible_plans_for(request.user).select_related("product","company","assigned_to")
    if scope == "mine":
        rows = rows.filter(assigned_to=request.user)
    if state != "all":
        rows = rows.filter(is_enabled=True,product__is_active=True,product__track_inventory=True,
            product__quantity__gte=0,product__reserved__gte=0,product__reserved__lte=F("product__quantity"),
            product__reorder_level__gte=0).annotate(_available=Cast("product__quantity",BigIntegerField())-Cast("product__reserved",BigIntegerField()))
        rows = rows.filter(_available__lte=F("product__reorder_level")) if state == "low" else rows.filter(
            _available__gt=F("product__reorder_level"))
    if state == "low":
        rows = rows.annotate(_stockout=Case(When(_available=0,then=Value(0)),default=Value(1),output_field=IntegerField())).order_by(
            "_stockout", F("next_review_on").asc(nulls_last=True), "product__name", "pk")
    else:
        rows = rows.order_by("product__name","pk")
    page = Paginator(rows,24).get_page(request.GET.get("page"))
    items = [_present(item) for item in page.object_list]
    return render(request,"inventory/replenishment/list.html",{"items":items,"page_obj":page,
        "state":state,"scope":scope,"can_configure":visible_companies_for(request.user,manage=True).exists(),
        "error":error},status=400 if error else 200)


def _detail(request,item,*,form=None,error="",status=200):
    item = _present(item)
    initial = {"expected_revision":item.revision,"expected_stock_version":item.current["stock_version"],
        "review_state":item.current["review_state"],"next_review_on":item.next_review_on,"notes":item.notes}
    review_form = form if form is not None else ReviewForm(initial=initial)
    can_change = can_change_plan(request.user,item)
    has_retained_draft = bool(form is not None and form.is_bound)
    can_submit_review = can_change and item.can_progress
    if has_retained_draft and not can_submit_review:
        # Widget-only read-only state keeps submitted values. Field.disabled
        # would replace bound values with initial data and lose the draft.
        review_form.fields["review_state"].widget.attrs["disabled"] = True
        for name in ("next_review_on","notes"):
            review_form.fields[name].widget.attrs["readonly"] = True
    return render(request,"inventory/replenishment/detail.html",{"item":item,
        "can_manage":can_manage_plan(request.user,item),"can_change":can_change,
        "review_form":review_form,"has_retained_draft":has_retained_draft,
        "show_review_form":can_change and (item.can_progress or has_retained_draft),
        "can_submit_review":can_submit_review,"error":error},status=status)


@login_required
@require_GET
@never_cache
def replenishment_detail(request,pk):
    return _detail(request,get_plan(user=request.user,pk=pk))


@login_required
@require_POST
@never_cache
def replenishment_action(request,pk):
    item = get_plan(user=request.user,pk=pk)
    if not can_change_plan(request.user,item):
        raise PermissionDenied
    form = ReviewForm(request.POST)
    if not form.is_valid():
        return _detail(request,item,form=form,error="The review was not saved. Check the fields below.",status=400)
    try:
        change_plan(user=request.user,pk=pk,**form.cleaned_data)
    except ValidationError as exc:
        return _detail(request,get_plan(user=request.user,pk=pk),form=form,error="; ".join(exc.messages),status=409)
    messages.success(request,"Procurement review saved.")
    return redirect("ims:inventory:replenishment_detail",pk=pk)


@login_required
@require_http_methods(["GET","POST"])
@never_cache
def replenishment_configure(request,pk=None):
    _reader(request)
    item = get_plan(user=request.user,pk=pk) if pk is not None else None
    if item is not None and not can_manage_plan(request.user,item):
        raise PermissionDenied
    if not visible_companies_for(request.user,manage=True).exists():
        raise PermissionDenied
    initial = {"expected_revision":str(item.revision) if item else "new"}
    preselected_product = None
    if item:
        initial.update(product=item.product_id,company=item.company_id,assigned_to=item.assigned_to_id,
            is_enabled=item.is_enabled,next_review_on=item.next_review_on,notes=item.notes)
    else:
        proposed = request.GET.get("product","")
        if proposed.isdecimal() and len(proposed) <= 19 and 0 < int(proposed) <= 9223372036854775807:
            product = Product.objects.filter(pk=int(proposed),replenishment_plan__isnull=True).only("pk").first()
            if product:
                preselected_product = initial["product"] = product.pk
    company_data = request.GET.copy()
    if not company_data.get("company") and item:
        company_data["company"] = item.company_id
    company_form = CompanyChoiceForm(company_data if company_data.get("company") else None,user=request.user,prefix="setup")
    # Prefix is presentation-only for the GET selector. Keep a stable, plain
    # company query parameter to prepopulate eligible employees.
    if company_data.get("company"):
        company_form = CompanyChoiceForm({"setup-company":company_data["company"]},user=request.user,prefix="setup")
    selected_company = company_form.cleaned_data.get("company") if company_form.is_bound and company_form.is_valid() else None
    if selected_company:
        initial["company"] = selected_company.pk
    form = ResponsibilityForm(request.POST if request.method=="POST" else None,
        user=request.user,item=item,initial=initial)
    error,status = "",200
    if request.method == "POST":
        if form.is_valid():
            values = form.cleaned_data
            try:
                saved = configure_plan(user=request.user,product_id=values["product"].pk,
                    company_id=values["company"].pk,
                    assigned_to_id=values["assigned_to"].pk if values["assigned_to"] else None,
                    is_enabled=values["is_enabled"],expected_revision=values["expected_revision"],
                    client_key=values["client_key"],next_review_on=values["next_review_on"],notes=values["notes"])
            except ValidationError as exc:
                error,status = "; ".join(exc.messages),409
            else:
                messages.success(request,"Procurement responsibility saved.")
                return redirect("ims:inventory:replenishment_detail",pk=saved.pk)
        else:
            status = 400
            error = "Responsibility was not saved. Review the fields below."
    return render(request,"inventory/replenishment/configure.html",{"form":form,"item":item,"error":error,
        "company_form":company_form,"selected_company":selected_company,
        "preselected_product":preselected_product,
        "setup_companies":visible_companies_for(request.user,manage=True),
        "show_form":bool(item or selected_company or request.method=="POST")},status=status)
