"""Deterministic operational guidance and explicit board attribution."""
from datetime import date
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .business_links import business_actions_for, enabled, present_business_link, visible_business_links_for
from .business_outcomes import team_for, _owner_team_valid


def next_business_move(user, *, selected_task=None, focus_active=False):
    from .pilot_access import feature_access
    if not feature_access(user,'business'):return None
    if not enabled() or focus_active:
        return None
    today=timezone.localdate()
    panel=business_actions_for(user,limit=8,due_by=today)
    available=[item for item in panel['items'] if item.due_date is None or item.due_date<=today]
    if not available:
        return None
    priority={'CRITICAL':0,'HIGH':1,'MEDIUM':2,'LOW':3}
    def rank(item):
        return (priority.get(item.priority,3),0 if item.due_date and item.due_date<today else 1,
            item.due_date or date.max)
    best=min(available,key=rank)
    if selected_task is not None and rank(selected_task)<=rank(best):
        return None
    return {'item':best,'reason':'This source action leads by priority, then overdue status and deadline. Review its current source before continuing.'}


def board_business_work(user, *, pairs, company_id=None, team_id=None, limit=200):
    result={'lanes':{},'unallocated':[],'omitted':0,'total':0}
    from .pilot_access import feature_access
    if not feature_access(user,'business'):return result
    if not enabled():
        return result
    try:
        rows=visible_business_links_for(user,state='active')
    except PermissionDenied:
        return result
    if company_id:
        rows=rows.filter(company_id=company_id)
    result['total']=rows.count()
    result['omitted']=max(0,result['total']-limit)
    for link in rows[:limit]:
        try:
            current=present_business_link(user=user,link=link)
        except PermissionDenied:
            continue
        team=team_for(current)
        if team_id and team!=team_id:
            continue
        record={'id':str(current.pk),'title':current.title,'url':current.get_absolute_url(),
            'priority':current.priority,'status':current.status_label,'due_date':current.due_date.isoformat() if current.due_date else None,
            'owner_id':current.owner_id,'team_id':team,'company':current.company.name if current.company else ''}
        record['company_id']=current.company_id
        pair=(current.owner_id,team)
        if team and pair in pairs and _owner_team_valid(current,team):
            result['lanes'].setdefault(pair,[]).append(record)
        else:
            result['unallocated'].append(record)
    return result
