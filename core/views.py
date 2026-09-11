from pathlib import PurePosixPath

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET

from core.permissions import allowed


@login_required
@require_GET
def private_document(request, name):
    from accounting.models import Expense
    from inventory.models import ShipmentCost
    from credit_control.models import DebtorFollowUp
    from tasker.models import TaskAttachment
    from tasker.policies import visible_tasks_for

    field = None
    for model, field_name, permission in (
        (Expense, 'attachment', 'accounting.view_journalentry'),
        (ShipmentCost, 'supporting_document', 'inventory.view_shipment'),
        (DebtorFollowUp, 'attachment', 'credit_control.view_debtorfollowup'),
    ):
        if allowed(request.user, permission):
            record = model.objects.filter(**{field_name: name}).first()
            if record:
                field = getattr(record, field_name)
                break
    if field is None and request.user.has_perm('tasker.view_task'):
        attachment = TaskAttachment.objects.filter(file=name, task__in=visible_tasks_for(request.user)).first()
        if attachment:
            field = attachment.file
    if not field:
        raise Http404
    try:
        response = FileResponse(field.open('rb'), as_attachment=True, filename=PurePosixPath(name).name)
    except FileNotFoundError:
        raise Http404
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
