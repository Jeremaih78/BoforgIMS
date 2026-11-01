from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from core.permissions import allowed

from .forms import CollectionTaskForm, PromiseToPayForm
from .source_policies import (can_change_source, can_read_collection_task, can_read_promise,
    coherent_promise_check, promise_assignees, source_assignees, source_manager,
    visible_collection_tasks_for, visible_promises_for)

from .models import (
    AuditEvent,
    CollectionTask,
    CreditorAccount,
    CreditorFollowUp,
    DebtorAccount,
    DebtorFollowUp,
    FollowUpTemplate,
    Notification,
    PaymentDispute,
    PromiseToPay,
    ReminderRule,
    RiskRule,
)


@admin.register(DebtorAccount)
class DebtorAccountAdmin(admin.ModelAdmin):
    list_display = ("customer", "total_outstanding", "overdue_balance", "risk_level", "collection_status", "next_follow_up_date")
    list_filter = ("risk_level", "collection_status", "is_active")
    search_fields = ("customer__name",)


@admin.register(CreditorAccount)
class CreditorAccountAdmin(admin.ModelAdmin):
    list_display = ("supplier", "total_outstanding", "overdue_balance", "risk_level", "payment_status", "is_critical_supplier")
    list_filter = ("risk_level", "payment_status", "is_critical_supplier", "is_active")
    search_fields = ("supplier__name",)


@admin.register(DebtorFollowUp)
class DebtorFollowUpAdmin(admin.ModelAdmin):
    list_display = ("customer", "invoice", "follow_up_date", "follow_up_type", "outcome", "status")
    list_filter = ("follow_up_type", "outcome", "status")
    search_fields = ("customer__name", "invoice__number", "subject")


@admin.register(CreditorFollowUp)
class CreditorFollowUpAdmin(admin.ModelAdmin):
    list_display = ("supplier", "bill", "follow_up_date", "follow_up_type", "outcome", "status")
    list_filter = ("follow_up_type", "outcome", "status")
    search_fields = ("supplier__name", "bill__doc_no", "subject")


class PromiseSourceAdminForm(PromiseToPayForm):
    source_revision = forms.UUIDField(required=False,widget=forms.HiddenInput)


class CollectionSourceAdminForm(CollectionTaskForm):
    source_revision = forms.UUIDField(required=False,widget=forms.HiddenInput)


class ScopedCollectionSourceAdmin(admin.ModelAdmin):
    """The alternate admin route shares canonical source scope and assignment."""
    def get_queryset(self, request):
        visible = visible_collection_tasks_for if self.model is CollectionTask else visible_promises_for
        return visible(request.user)

    def has_view_permission(self, request, obj=None):
        if not allowed(request.user, "credit_control.view_"+self.model._meta.model_name, staff=False):
            return False
        reader = can_read_collection_task if self.model is CollectionTask else can_read_promise
        return obj is None or reader(request.user,obj)

    def has_change_permission(self, request, obj=None):
        if not allowed(request.user, "credit_control.change_"+self.model._meta.model_name, staff=False):
            return False
        return self.has_view_permission(request,obj) and (obj is None or can_change_source(request.user,obj))

    def has_add_permission(self, request):
        return self.has_view_permission(request) and allowed(request.user,
            "credit_control.add_"+self.model._meta.model_name, staff=False)

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request,obj) and allowed(request.user,
            "credit_control.delete_"+self.model._meta.model_name, staff=False)

    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request,obj,**kwargs)
        actor = request.user
        source_model = self.model
        class SourceScopedForm(base_form):
            def __init__(self,*args,**form_kwargs):
                form_kwargs["user"] = actor
                super().__init__(*args,**form_kwargs)
                if "source_revision" in self.fields:
                    self.fields["source_revision"].required = bool(obj)
                if self.instance.pk and not self.is_bound:
                    self.initial["source_revision"] = self.instance.revision

            def clean(self):
                cleaned = super().clean()
                if self.instance.pk:
                    # Admin POSTs run inside changeform_view's transaction.
                    # This also makes standalone form validation safe.
                    with transaction.atomic():
                        current = source_model.objects.select_for_update().filter(pk=self.instance.pk).first()
                        if current is None:
                            raise PermissionDenied
                        checks = []
                        if source_model is PromiseToPay:
                            checks = list(CollectionTask.objects.select_for_update().filter(
                                promise_to_pay=current,task_type="CHECK_PROMISE").order_by("pk"))
                        from accounting.models import Company
                        next_company = cleaned.get("company")
                        next_company_id = getattr(next_company,"pk",None)
                        list(Company.objects.select_for_update().filter(pk__in={
                            pk for pk in (current.company_id,next_company_id) if pk is not None}).order_by("pk"))
                        if not can_change_source(actor,current):
                            raise PermissionDenied
                        if cleaned.get("source_revision") != current.revision:
                            raise forms.ValidationError("This source record changed. Reload it before saving.")
                        if source_model is PromiseToPay and any(not coherent_promise_check(current,task) for task in checks):
                            raise forms.ValidationError("A linked check has inconsistent source references. Review its source mapping first.")
                        if source_model is PromiseToPay and checks and any(
                                getattr(cleaned.get(field), "pk", None) != getattr(current, field+"_id")
                                for field in ("company", "customer", "invoice")):
                            raise forms.ValidationError("Review linked check mappings before changing this promise's company, customer or invoice.")
                        if current.company_id != next_company_id and not (
                                source_manager(actor,current.company_id) and source_manager(actor,next_company_id)):
                            raise forms.ValidationError("A source manager must map work between authorized companies.")
                        owner = cleaned.get("assigned_to")
                        if getattr(owner,"pk",None) != current.assigned_to_id:
                            if any(not can_change_source(actor,task) for task in checks):
                                raise PermissionDenied
                            if not source_manager(actor,next_company_id):
                                raise forms.ValidationError("A source manager must change responsibility.")
                            choices = (promise_assignees(actor,current,company_id=next_company_id)
                                if source_model is PromiseToPay else source_assignees(actor,next_company_id,
                                    permission="credit_control.view_"+source_model._meta.model_name))
                            if owner and not choices.filter(pk=owner.pk).exists():
                                raise forms.ValidationError("Choose a currently authorized responsible employee.")
                return cleaned
        return SourceScopedForm

    def save_model(self, request, obj, form, change):
        super().save_model(request,obj,form,change)
        if self.model is PromiseToPay and change and "assigned_to" in form.changed_data:
            # Responsibility has one source of truth even through admin.
            for task in CollectionTask.objects.select_for_update().filter(
                    task_type="CHECK_PROMISE",promise_to_pay=obj).order_by("pk"):
                task.assigned_to = obj.assigned_to
                task.save(update_fields=["assigned_to"])


@admin.register(PromiseToPay)
class PromiseToPayAdmin(ScopedCollectionSourceAdmin):
    form = PromiseSourceAdminForm
    list_display = ("customer", "invoice", "promised_amount", "promised_date", "status", "actual_paid_amount")
    list_filter = ("status",)
    search_fields = ("customer__name", "invoice__number")


@admin.register(PaymentDispute)
class PaymentDisputeAdmin(admin.ModelAdmin):
    list_display = ("dispute_type", "customer", "supplier", "invoice", "bill", "status", "opened_date")
    list_filter = ("dispute_type", "status")
    search_fields = ("customer__name", "supplier__name", "description")


@admin.register(FollowUpTemplate)
class FollowUpTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "audience", "channel", "tone", "is_active")
    list_filter = ("audience", "channel", "tone", "is_active")


@admin.register(CollectionTask)
class CollectionTaskAdmin(ScopedCollectionSourceAdmin):
    form = CollectionSourceAdminForm
    list_display = ("task_type", "customer", "supplier", "due_date", "priority", "status", "assigned_to")
    list_filter = ("task_type", "priority", "status")


@admin.register(ReminderRule)
class ReminderRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "audience", "offset_days", "task_type", "priority", "is_active")
    list_filter = ("audience", "is_active", "priority")


@admin.register(RiskRule)
class RiskRuleAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "weight", "threshold_low", "threshold_medium", "threshold_high", "is_active")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "level", "is_read", "created_at")
    list_filter = ("level", "is_read")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "model_name", "object_id", "action", "user", "created_at")
    list_filter = ("event_type", "action")
