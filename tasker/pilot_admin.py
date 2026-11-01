from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from tasker.pilot_models import Pilot,PilotMember,PilotObservation,PilotEvent,PilotReleaseEvidence,PilotValidation
from tasker.services.pilots import visible_pilots,observations_for,manages


class PilotEvidenceAdmin(admin.ModelAdmin):
    actions=None
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False
    def has_view_permission(self,request,obj=None):return bool(getattr(settings,'TASKER_PILOT_ENABLED',False) and super().has_view_permission(request,obj))
    def get_queryset(self,request):
        rows=super().get_queryset(request)
        if not self.has_view_permission(request):return rows.none()
        try:
            pilots=visible_pilots(request.user)
            if self.model is Pilot:return pilots
            manager_ids=[p.pk for p in pilots if manages(request.user,p)]
            if self.model is PilotObservation:
                from django.db.models import Q
                from tasker.services.baseline import visible_observations_for
                return rows.filter(pilot__in=pilots).filter(Q(pilot_id__in=manager_ids)|Q(employee=request.user)).filter(Q(phase='FOLLOWUP')|Q(baseline_observation__in=visible_observations_for(request.user)))
            # Roster and governance/audit history include peers: manager only.
            return rows.filter(pilot_id__in=manager_ids)
        except PermissionDenied:return rows.none()
    def get_exclude(self,request,obj=None):return ('outcomes',) if self.model is PilotObservation else ()


for model in (Pilot,PilotMember,PilotObservation,PilotEvent,PilotReleaseEvidence,PilotValidation):admin.site.register(model,PilotEvidenceAdmin)
