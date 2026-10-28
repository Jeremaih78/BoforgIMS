"""Owner-confirmed directions, installed explicitly for a selected company."""
from django.core.exceptions import PermissionDenied
from django.db import transaction

from accounting.models import Company
from tasker.models import Goal, KeyResult, OrganizationEvent
from tasker.organization import can_manage_company


BUSINESS_DIRECTIONS = (
    ("BIZ-G01", "Reach USD 2m total sales over two years", "Cumulative total sales in USD over the agreed two-year period. Confirm start/end dates, accountable owner, sales recognition, returns/tax treatment, baseline and authoritative accounting evidence.", "Cumulative total sales", "USD", "2000000"),
    ("BIZ-G02", "Become the largest printing equipment and consumables supplier by 2030", "Cover DTF, sublimation, vinyl cutters, 3D printing, laser engraving, paper printers and consumables. Define the market/geography, largest-supplier measure, comparator, baseline, deadline within 2030 and accountable owner.", "Printing supply leadership measure", "", None),
    ("BIZ-G03", "Lead the African business bridge to AI within 36 months", "Lead in AI, automation, SaaS and AI agents, bridging African businesses and artificial intelligence. Confirm the 36-month start/end dates, markets, customer benefit measures, targets, evidence and accountable owner.", "AI leadership and customer benefit measure", "", None),
)


@transaction.atomic
def create_business_goal_drafts(*, user, company):
    company = Company.objects.select_for_update().get(pk=company.pk)
    if not can_manage_company(user, company) or not user.has_perm("tasker.add_goal") or not user.has_perm("tasker.add_keyresult"):
        raise PermissionDenied
    goals = []
    for reference, title, description, metric, unit, target in BUSINESS_DIRECTIONS:
        goal = Goal.all_objects.filter(company=company, reference=reference).first()
        if goal is None:
            goal = Goal.objects.create(company=company, reference=reference, title=title, description=description, status=Goal.Status.DRAFT, created_by=user, updated_by=user)
            KeyResult.objects.create(goal=goal, title=metric, unit=unit, target=target, baseline=None, created_by=user, updated_by=user)
            OrganizationEvent.objects.create(company=company, actor=user, action="business_goal.draft_created", object_type="goal", object_id=str(goal.pk), details={"reference": reference})
        goals.append(goal)
    return goals
