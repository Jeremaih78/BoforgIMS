"""Explicit reporting structures; permission groups never imply membership."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .models import AuditedSoftDeleteModel


class Team(AuditedSoftDeleteModel):
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_teams")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["company__name", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], condition=Q(is_deleted=False), name="tasker_team_live_name_uniq")]
        permissions = [("manage_organization", "Can manage assigned company structures"), ("view_company_work", "Can view work in assigned companies")]

    def __str__(self):
        return f"{self.name} ({self.company})"


class TeamMembership(AuditedSoftDeleteModel):
    class Role(models.TextChoices):
        EMPLOYEE = "EMPLOYEE", "Employee"
        MANAGER = "MANAGER", "Team manager"

    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_team_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.EMPLOYEE)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["team__name", "user__username"]
        constraints = [models.UniqueConstraint(fields=["team", "user"], condition=Q(is_deleted=False), name="tasker_membership_live_uniq")]
        indexes = [models.Index(fields=["user", "is_active", "role"], name="tasker_member_user_role_idx")]

    def __str__(self):
        return f"{self.user} — {self.team.name} ({self.get_role_display()})"


class OrganizationAccess(AuditedSoftDeleteModel):
    """An explicit company grant, independent of Django role capabilities."""
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_access_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_organization_access")
    is_active = models.BooleanField(default=True)
    can_manage = models.BooleanField(default=False)
    can_view_all = models.BooleanField(default=False)
    can_receive_reports = models.BooleanField(default=False)

    class Meta:
        ordering = ["company__name", "user__username"]
        constraints = [models.UniqueConstraint(fields=["company", "user"], condition=Q(is_deleted=False), name="tasker_org_access_live_uniq")]
        indexes = [models.Index(fields=["user", "is_active"], name="tasker_org_access_user_idx")]

    def __str__(self):
        return f"{self.user} — {self.company}"


class OrganizationEvent(models.Model):
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, null=True, blank=True)
    team = models.ForeignKey(Team, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Organization audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Organization audit events are immutable.")
