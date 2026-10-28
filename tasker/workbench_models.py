"""Owner-scoped saved Work views; filters never confer object authority."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .models import AuditedSoftDeleteModel, Task


WORK_PRESETS = ("all", "mine", "waiting", "duesoon", "unscheduled")
WORK_LAYOUTS = ("list", "board")
FILTER_KEYS = {"q", "priority", "status", "category", "owner", "sort", "archived", "preset", "layout"}


def normalize_work_filters(value):
    if not isinstance(value, dict) or set(value) - FILTER_KEYS:
        raise ValidationError("Use only the supported Work filters.")
    result = {}
    for name, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValidationError("Work filter values must be plain text or identifiers.")
        item = str(item).strip()
        if "\x00" in item:
            raise ValidationError("Work filters cannot contain null characters.")
        if not item:
            continue
        if name == "q" and len(item) > 300:
            raise ValidationError("Search text must be at most 300 characters.")
        if name in ("category", "owner") and (not item.isdecimal() or len(item) > 18 or int(item) < 1):
            raise ValidationError("Choose a valid filter identifier.")
        enums = {"priority": Task.Priority.values, "status": Task.Status.values,
                 "sort": ("due", "priority", "newest", "oldest", "updated", "title"),
                 "preset": WORK_PRESETS, "layout": WORK_LAYOUTS, "archived": ("1",)}
        if name in enums and item not in enums[name]:
            raise ValidationError("Choose a supported Work filter value.")
        result[name] = item
    return result


class SavedWorkView(AuditedSoftDeleteModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_saved_work_views")
    name = models.CharField(max_length=64)
    filters = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [models.UniqueConstraint(fields=("owner", "name"), condition=Q(is_deleted=False), name="tasker_saved_work_name_uniq")]
        indexes = [models.Index(fields=("owner", "is_deleted"), name="tasker_saved_work_owner_idx")]

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        if not self.name or "\x00" in self.name:
            raise ValidationError({"name": "Name this saved view."})
        self.filters = normalize_work_filters(self.filters)
        if self.pk and type(self).all_objects.filter(pk=self.pk).exclude(owner_id=self.owner_id).exists():
            raise ValidationError("A saved view keeps its original owner.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class WorkbenchPreference(models.Model):
    class Preset(models.TextChoices):
        ALL = "all", "All authorized work"
        MINE = "mine", "Mine"
        WAITING = "waiting", "Waiting"
        DUE_SOON = "duesoon", "Due soon"
        UNSCHEDULED = "unscheduled", "Unscheduled"

    class Layout(models.TextChoices):
        LIST = "list", "List"
        BOARD = "board", "Board"

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_workbench_preference")
    preset = models.CharField(max_length=12, choices=Preset.choices, default=Preset.ALL)
    layout = models.CharField(max_length=5, choices=Layout.choices, default=Layout.LIST)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(preset__in=WORK_PRESETS), name="tasker_work_pref_preset_valid"),
                       models.CheckConstraint(condition=Q(layout__in=WORK_LAYOUTS), name="tasker_work_pref_layout_valid")]

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Work preferences"
