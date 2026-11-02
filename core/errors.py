"""Production error responses stay usable when the database or assets fail."""
from uuid import uuid4
import logging
from django.http import JsonResponse,HttpResponse
from django.template.loader import get_template
from django.shortcuts import render


def _response(request,status,title,message):
    reference=uuid4().hex[:12]
    if status==500:
        logging.getLogger(__name__).error('IMS error reference=%s',reference,exc_info=True)
    if request.META.get('HTTP_HOST','').lower().startswith('api.') or 'application/json' in request.headers.get('Accept',''):
        response=JsonResponse({'detail':message,'reference':reference},status=status)
    else:
        response=HttpResponse(get_template('errors/error.html').render({'status':status,'title':title,'message':message,'reference':reference}),status=status)
    response['Cache-Control']='no-store'
    response['X-Request-ID']=reference
    return response


def bad_request(request,exception):return _response(request,400,'Check the request','Check the link, dates or filters and try again.')
def forbidden(request,exception):return _response(request,403,'Access required','Your account does not have permission for this action. Contact your administrator if you need access.')
def not_found(request,exception):return _response(request,404,'Page not found','The page may have moved, or this record is no longer available.')
def server_error(request):return _response(request,500,'Something interrupted this request','Return to the workspace and check recent activity before retrying your action.')
