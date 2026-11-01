from django.conf import settings
from django.shortcuts import render
from django.utils.cache import patch_cache_control
from .services.pilot_access import feature_access,feature_for_route


class PilotAccessMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):return self.get_response(request)
    def process_view(self,request,view_func,view_args,view_kwargs):
        match=getattr(request,'resolver_match',None)
        if not getattr(settings,'TASKER_PILOT_ACCESS_ENFORCED',False) or not match or 'tasker' not in match.app_names or not request.user.is_authenticated:return None
        if feature_access(request.user,feature_for_route(match.url_name or '')):return None
        response=render(request,'tasker/pilot/access_pending.html',status=403)
        patch_cache_control(response,no_store=True,private=True)
        return response
