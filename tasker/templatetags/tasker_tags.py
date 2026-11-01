from django import template

register = template.Library()


@register.filter
def pilot_label(value):
    labels={'LOCAL_CANDIDATE_VERIFIED':'Local candidate verified','LOCAL_VERIFICATION_PENDING':'Local verification pending',
        'MANAGER_ONLY':'Reviewed by your manager','NO_GO_PENDING_OPERATING_EVIDENCE':'Real pilot: operating evidence pending',
        'READY_FOR_SEPARATE_AUTHORIZATION':'Ready for a separate rollout decision','VALIDATED_UNDER_PROTOCOL':'Validated under this protocol',
        'READY_FOR_INDEPENDENT_REVIEW':'Ready for independent review','SYNTHETIC_NOT_EVIDENCE':'Synthetic results are not productivity evidence',
        'PENDING_REAL_VALIDATION':'Real productivity validation pending',
        'greater_than_target_not_supported_at_95_percent':'The 95% interval does not support improvement above the target.',
        'cohort_scope_changed':'A cohort member’s current team or company access changed.',
        'missing_or_incomplete_employee_days':'Expected employee-days are missing or incomplete.',
        'baseline_not_prospectively_started':'Baseline collection did not start before its declared window.',
        'protocol_not_prospective':'The protocol was not approved before rollout.',
        'source_evidence_invalidated':'A linked source no longer supports its accepted measurements.',
        'synthetic_data_not_productivity_evidence':'Invented demonstration data cannot validate real productivity.',
        'uncertainty_not_estimable':'There is insufficient comparable evidence to estimate uncertainty.'}
    return labels.get(str(value),str(value).replace('_',' ').capitalize())


@register.filter
def pilot_number(value):
    return 'Unknown' if value is None else f'{value:,.2f}'


@register.filter
def task_duration(value):
    """Render a duration as a compact, human-readable dashboard label."""
    if not value:
        return ""
    total_minutes = max(0, round(value.total_seconds() / 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes} min"
