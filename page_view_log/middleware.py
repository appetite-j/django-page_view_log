from __future__ import unicode_literals
import hashlib
import json
import time

from django.core.cache import cache
from django.utils import timezone

try:
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    class MiddlewareMixin(object):
        pass

from page_view_log.models import UserAgent, Url, ViewName, PageViewLog, PAGE_VIEW_LOG_INCLUDES_ANONYMOUS


class PageViewLogMiddleware(MiddlewareMixin, object):
    def process_request(self, request):
        request.pvl_stime = timezone.now()
        request.pvl_view_name = ''

        # 'cache' the result of this page, to use as the result for any other page request that comes in during its generation.
        mystr = ":".join(str(obj) for obj in [
            request.session.session_key,
            request.user,
            request.META.get('HTTP_AUTHORIZATION'),
            request.META.get('PATH_INFO'),
            request.META.get('QUERY_STRING'),
            json.dumps(request.POST),
            request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest',  # replaces `request.is_ajax()`
            ])
        request.pvl_uid = hashlib.md5(mystr.encode('utf-8')).hexdigest()

        # Try to call dibs on this work
        dibsed = cache.add(request.pvl_uid, "in progress", 60)   # returns False if this key already has a value (someone else has dibsed it)
        if not dibsed:
            # Wait for the other process to complete.
            stime = time.time()
            while cache.get(request.pvl_uid):
                time.sleep(0.2)
                if time.time() > (stime + 60):
                    # it's been 60 seconds. time to give up on waiting and process as normal.
                    return None

            # Don't bother processing. Just return the same response as the last request.
            return cache.get(request.pvl_uid + ":response")

        return None

    def process_view(self, request, view_func, *args, **kwargs):
        request.pvl_view_name = view_func.__name__
        return None

    def process_response(self, request, response):
        if hasattr(request,'pvl_stime'):
            etime = timezone.now()
            gen_time = etime - request.pvl_stime
            gen_time = (gen_time.seconds*1000000) + gen_time.microseconds
        else:
            gen_time = None

        try:
            user_id = int(request.user.id)
        except:
            user_id = None

        if user_id or PAGE_VIEW_LOG_INCLUDES_ANONYMOUS:
            # ip_address
            ip_address = request.META['REMOTE_ADDR']
            if request.META.get('HTTP_CF_CONNECTING_IP'):
                ip_address = request.META['HTTP_CF_CONNECTING_IP']
            if request.META.get('HTTP_X_FORWARDED_FOR'):
                ip_address = request.META['HTTP_X_FORWARDED_FOR'].split(',')[0]

            # user_agent
            user_agent_string = request.META.get('HTTP_USER_AGENT') or ''
            user_agent_hash = hashlib.md5(user_agent_string.encode('utf-8')).hexdigest()
            cache_key = "pvl_%s" % user_agent_hash
            user_agent_id = cache.get(cache_key)
            if not user_agent_id:
                # get or create it from the db
                user_agents = UserAgent.objects.filter(user_agent_hash=user_agent_hash)[:1]
                if user_agents:
                    user_agent = user_agents[0]
                else:
                    # create it
                    user_agent = UserAgent.objects.create(
                        user_agent_hash = user_agent_hash,
                        user_agent_string = user_agent_string,
                        )
                user_agent_id = user_agent.id
                cache.set(cache_key, user_agent_id)

            # url
            url_string = request.META.get('PATH_INFO') or ''
            url_hash = hashlib.md5(url_string.encode('utf-8')).hexdigest()
            cache_key = "pvl_%s" % url_hash
            url_id = cache.get(cache_key)
            if not url_id:
                # get or create it from the db
                urls = Url.objects.filter(url_hash = url_hash)[:1]
                if urls:
                    url = urls[0]
                else:
                    # create it
                    url = Url.objects.create(
                        url_hash = url_hash,
                        url_string = url_string,
                        )
                url_id = url.id
                cache.set(cache_key, url_id)

            # view_name
            view_name_string = getattr(request,'pvl_view_name','')
            view_name_hash = hashlib.md5(view_name_string.encode('utf-8')).hexdigest()
            cache_key = "pvl_%s" % view_name_hash
            view_name_id = cache.get(cache_key)
            if not view_name_id:
                # get or create it from the db
                view_names = ViewName.objects.filter(view_name_hash=view_name_hash)[:1]
                if view_names:
                    view_name = view_names[0]
                else:
                    # create it
                    view_name = ViewName.objects.create(
                        view_name_hash = view_name_hash,
                        view_name_string = view_name_string,
                        )
                view_name_id = view_name.id
                cache.set(cache_key, view_name_id)

            try:
                PageViewLog.objects.create(
                    user_id = user_id,
                    session_key = request.session.session_key,
                    ip_address = ip_address,
                    user_agent_id = user_agent_id,

                    url_id = url_id,
                    view_name_id = view_name_id,
                    gen_time = gen_time,
                    status_code = response.status_code,
                    )
            except Exception as e:
                print("An error occurred saving the PageViewLog: '{}'".format(e))

        # we've finished processing this request, let's cache it in case any other thread is waiting for it.
        if hasattr(request,'pvl_uid'):
            try:
                cache.set(request.pvl_uid + ":response", response, 10)
            except:
                # some responses can't be pickled / cast to string. So we just fail gracefully
                pass
            cache.delete(request.pvl_uid)      # this tells the other thread that we're done.
        return response
