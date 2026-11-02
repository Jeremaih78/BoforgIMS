"""Idempotent document submissions and optimistic checks for stale editors."""
import hashlib
import json
from uuid import UUID
from django.core.exceptions import ValidationError
from sales.models import DocumentSubmission


def payload_hash(data):
    payload={key:data.getlist(key) for key in sorted(data) if key not in {'csrfmiddlewaretoken','submission_key','save_action'}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def edit_signature(document):
    if document is None:
        return ''
    header={name:getattr(document,name,None) for name in ['customer_id','date','due_date','reference','notes','terms','custom_fields','status','tax_added']}
    lines=list(document.lines.order_by('pk').values('id','product_id','combo_id','description','quantity','unit_price','tax_rate_percent','line_total'))
    return hashlib.sha256(json.dumps({'header':header,'lines':lines},sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def key_from_data(data):
    try:return UUID(str(data.get('submission_key','')))
    except (ValueError,TypeError):return None


def verify(submission,*,kind,target_id,user,data):
    if (submission.kind,submission.target_id,submission.created_by_id,submission.payload_hash)!=(kind,target_id,user.pk,payload_hash(data)):
        raise ValidationError('This save key belongs to different document details. Reload the editor before saving.')
    return submission


def completed_submission(*,kind,target_id,user,data):
    key=key_from_data(data)
    submission=DocumentSubmission.objects.filter(pk=key).first() if key else None
    if submission:
        verify(submission,kind=kind,target_id=target_id,user=user,data=data)
    return submission


def claim_submission(*,kind,target_id,user,data):
    key=key_from_data(data)
    if key is None:
        return None,True  # Compatibility for previously released clients.
    submission,created=DocumentSubmission.objects.get_or_create(pk=key,defaults={'kind':kind,'target_id':target_id,'created_by':user,'payload_hash':payload_hash(data)})
    if not created:verify(submission,kind=kind,target_id=target_id,user=user,data=data)
    return submission,created
