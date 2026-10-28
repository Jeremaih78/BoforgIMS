from django.db import migrations, models


def backfill_quota_history(apps, schema_editor):
    """Preserve historical usage without copying content or assuming no bill.

    Local disabled/permission/configuration/prompt/rejected-quota requests did
    not reach the provider. Single-attempt measured usage is authoritative. Older
    provider failures or missing usage lack an input bound; conservatively
    consume their configured daily allowance for that historical day rather
    than invent a zero token charge. Existing rows/usage totals are preserved.
    """
    Usage = apps.get_model("tasker", "AIUsageLog")
    Configuration = apps.get_model("tasker", "AIConfiguration")
    limits = dict(Configuration.objects.using(schema_editor.connection.alias).values_list("pk", "daily_token_limit"))
    rows = Usage.objects.using(schema_editor.connection.alias).exclude(status="DISABLED").exclude(
        error_type__in=("AIPermissionError", "AIConfigurationError", "AIPromptError", "AIRateLimitError")
    ).exclude(error_code__in=("ai_permission_denied", "ai_configuration_error", "ai_prompt_error", "ai_rate_limited"))
    for log in rows.iterator(chunk_size=500):
        measured = log.total_tokens > 0
        # The old service persisted only the final response's token totals.
        # A positive total therefore does not measure earlier billed retries.
        unknown_attempt_usage = not measured or log.attempts > 1
        daily_allowance = limits.get(log.configuration_id, 250000)
        Usage.objects.using(schema_editor.connection.alias).filter(pk=log.pk).update(
            quota_reserved_at=log.started_at,
            quota_charged_tokens=max(log.total_tokens, daily_allowance) if unknown_attempt_usage else log.total_tokens,
            quota_usage_estimated=unknown_attempt_usage,
            quota_provider_attempts=log.attempts,
            quota_settled_at=log.completed_at or log.started_at,
        )


class Migration(migrations.Migration):
    dependencies = [("tasker", "0010_alter_taskattachment_file")]

    operations = [
        migrations.AddField(model_name="aiusagelog", name="quota_reserved_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="aiusagelog", name="quota_reserved_tokens", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="aiusagelog", name="quota_charged_tokens", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="aiusagelog", name="quota_attempt_token_budget", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="aiusagelog", name="quota_provider_attempts", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="aiusagelog", name="quota_usage_estimated", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="aiusagelog", name="quota_settled_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(backfill_quota_history, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="aiusagelog", constraint=models.CheckConstraint(condition=models.Q(quota_settled_at__isnull=True) | models.Q(quota_reserved_tokens=0), name="tasker_ai_quota_settled_clear")),
        migrations.AddIndex(model_name="aiusagelog", index=models.Index(fields=["user", "quota_reserved_at"], name="tasker_ai_quota_user_idx")),
    ]
