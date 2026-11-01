"""Source-bound local evidence and separately reviewed real operating readiness."""
import hashlib,json
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from tasker.pilot_models import PilotReleaseEvidence
from .pilots import actor,manages,visible_pilots,_lock,_manager,event,replay,digest,protocol

LOCAL_CHECKS=('regressions','postgres_concurrency','migration_restore','static_manifest','desktop_mobile_keyboard','end_to_end_roles','degraded_recovery')
OPERATING_CHECKS={
    'owner_roster':'Named owner, real registered cohort and matching roles',
    'baseline_sources':'Prospective staffing/output/quality sources and approved protocol',
    'staff_usability':'Real employee usability/accessibility sessions and unresolved issues',
    'backup_restore':'Private production backup/restore rehearsal and recovery owner',
    'supervision':'Staging worker/scheduler supervision, monitoring and rollback owner',
    'mail_setup':'Approved sender, explicit top-manager recipients and verified email delivery',
    'privacy_policy':'Approved private-data, retention, role and source-access policy',
    'goal_definitions':'Remaining company-goal baselines, horizons and accepted-output definitions',
    'security_accessibility':'Credential-owner confirmation, native accessibility and security review',
    'deployment_authorization':'Separate owner authorization for migration/flags/rollout; no invitations from implementation',
    'ai_evaluation':'Live model quality/cost/latency evaluation and private key/pricing setup (only if AI selected)',
}


def source_fingerprint():
    root=Path(settings.BASE_DIR);sha=hashlib.sha256();count=0
    folders=('accounting','config','core','credit_control','customers','ims','inventory','legal','operations','payments','sales','shop','tasker','templates','users','website','static')
    extensions={'.py','.html','.css','.js','.json','.svg','.woff','.woff2','.ttf'}
    paths=[]
    for name in ('manage.py','requirements.txt','pyproject.toml','package.json','package-lock.json'):
        p=root/name
        if p.is_file() and not p.is_symlink():paths.append(p)
    for name in ('TASKER_REQUIREMENTS_MATRIX.md','TASKER_BUSINESS_REQUIREMENTS_2026-10-21.md'):
        p=root/'docs'/name
        if p.is_file() and not p.is_symlink():paths.append(p)
    for folder in folders:
        parent=root/folder
        if parent.is_dir():paths.extend(p for p in parent.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix in extensions and '__pycache__' not in p.parts)
    for path in sorted(paths,key=lambda p:p.relative_to(root).as_posix()):
        data=path.read_bytes()
        if path.suffix not in {'.woff','.woff2','.ttf'}:data=data.replace(b'\r\n',b'\n')
        sha.update(path.relative_to(root).as_posix().encode());sha.update(b'\0');sha.update(hashlib.sha256(data).digest());count+=1
    return sha.hexdigest(),count


def local_evidence():
    path=Path(settings.BASE_DIR)/'output'/'tasker-stage13-release-evidence.json'
    try:source,count=source_fingerprint()
    except OSError:source,count='',0
    result={'source_digest':source,'files':count,'checks':{k:'PENDING' for k in LOCAL_CHECKS},'current':False,'reference':str(path.name)}
    if not path.is_file() or path.is_symlink() or path.stat().st_size>262144:return result
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):return result
    if not isinstance(data,dict) or data.get('source_digest')!=source:return result
    checks=data.get('checks',{})
    if not isinstance(checks,dict):return result
    result['checks']={k:checks.get(k,'PENDING') if checks.get(k) in ('VERIFIED','FAILED','PENDING') else 'PENDING' for k in LOCAL_CHECKS}
    result['current']=True
    return result


def requirement_catalog():
    path=Path(settings.BASE_DIR)/'docs'/'TASKER_REQUIREMENTS_MATRIX.md'
    rows=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.startswith('| '):continue
        parts=[v.strip() for v in line.strip('|').split('|')]
        if len(parts)!=6 or not parts[0].startswith(('T0','T1','BIZ-','X0','EXT-')):continue
        rows.append({'id':parts[0],'source':parts[1],'stage':parts[2],'requirement':parts[3],'evidence':parts[4],'recorded_status':parts[5],
            'conditional':parts[0].startswith('EXT-') or parts[0]=='BIZ-R03'})
    return rows


def release_readiness(*,user,pilot):
    user=actor(user);pilot=visible_pilots(user).get(pk=pilot.pk)
    if not manages(user,pilot):return {'local_gate':'MANAGER_ONLY','ready_for_real_pilot':False,'operating':[],'local':{}}
    local=local_evidence();latest={}
    for row in pilot.release_evidence.filter(source_digest=local['source_digest']).order_by('check_key','-created_at','-pk'):
        latest.setdefault(row.check_key,row)
    operating=[]
    for key,label in OPERATING_CHECKS.items():
        optional=key=='ai_evaluation' and 'ai' not in pilot.features
        row=latest.get(key)
        operating.append({'key':key,'label':label,'status':'DEFERRED' if optional else row.status if row else 'PENDING',
            'reference':row.reference if row else '', 'required':not optional})
    from tasker.job_models import JobWorkerHeartbeat,OutboxJob
    since=timezone.now()-timedelta(minutes=5)
    health={'worker_recent':JobWorkerHeartbeat.objects.filter(role='worker',seen_at__gte=since).exists(),
        'scheduler_recent':JobWorkerHeartbeat.objects.filter(role='scheduler',seen_at__gte=since).exists(),
        'failure_or_expired_lease':OutboxJob.objects.filter(Q(state='FAILED')|Q(state='RUNNING',lease_expires_at__lte=timezone.now())).exists()}
    local_ready=local['current'] and all(v=='VERIFIED' for v in local['checks'].values())
    ready=bool(local_ready and pilot.state=='APPROVED' and pilot.approved_at and not pilot.is_synthetic and pilot.owner_id and pilot.owner.is_active and manages(pilot.owner,pilot) and digest(protocol(pilot))==pilot.protocol_digest
        and all(c['status']=='VERIFIED' for c in operating if c['required']) and health['worker_recent'] and health['scheduler_recent'] and not health['failure_or_expired_lease'])
    return {'local_gate':'LOCAL_CANDIDATE_VERIFIED' if local_ready else 'LOCAL_VERIFICATION_PENDING','local':local,
        'operating':operating,'health':health,'ready_for_real_pilot':ready,'deployment_gate':'READY_FOR_SEPARATE_AUTHORIZATION' if ready else 'NO_GO_PENDING_OPERATING_EVIDENCE'}


@transaction.atomic
def record_release_check(*,user,pk,client_key,check_key,status,reference,summary=''):
    user,pilot=_lock(user,pk,'tasker.change_pilot');_manager(user,pilot)
    if check_key not in OPERATING_CHECKS:raise ValidationError('Choose a known operating check.')
    payload=dict(pk=pk,check_key=check_key,status=status,reference=reference,summary=summary)
    if replay(user,client_key,'release.check',payload):return pilot
    source,_=source_fingerprint()
    row=PilotReleaseEvidence.objects.create(pilot=pilot,check_key=check_key,status=status,reference=reference,summary=summary,source_digest=source,recorded_by=user)
    event(user,pilot,client_key,'release.check',payload,{'evidence_id':row.pk})
    return pilot
