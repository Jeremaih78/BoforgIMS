"""Source-scoped read-only admin; all decisions use the versioned source pages."""
from django.contrib import admin
from django_hosts.resolvers import reverse
from django.utils.html import format_html

from inventory.replenishment_models import ReplenishmentPlan
from inventory.replenishment_policies import can_read_plan, source_read_capable, visible_plans_for


@admin.register(ReplenishmentPlan)
class ReplenishmentPlanAdmin(admin.ModelAdmin):
    list_display = ("product","company","assigned_to","is_enabled","review_state","next_review_on","source_review")
    list_filter = ("is_enabled","review_state")
    readonly_fields = tuple(field.name for field in ReplenishmentPlan._meta.fields)+( "source_review",)

    def get_queryset(self,request):
        return visible_plans_for(request.user).select_related("product","company","assigned_to")

    def has_view_permission(self,request,obj=None):
        return source_read_capable(request.user) and (obj is None or can_read_plan(request.user,obj))

    def has_add_permission(self,request):
        return False

    def has_change_permission(self,request,obj=None):
        return False

    def has_delete_permission(self,request,obj=None):
        return False

    @admin.display(description="Source workflow")
    def source_review(self,obj):
        return format_html('<a href="{}">Open Inventory review</a>',reverse(
            "ims:inventory:replenishment_detail",kwargs={"pk":obj.pk},host="ims"))
