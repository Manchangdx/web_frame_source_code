import time
from importlib import import_module

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.core.exceptions import SuspiciousOperation
from django.utils.cache import patch_vary_headers
from django.utils.deprecation import MiddlewareMixin
from django.utils.http import http_date


class SessionMiddleware(MiddlewareMixin):
    # RemovedInDjango40Warning: when the deprecation ends, replace with:
    #   def __init__(self, get_response):
    def __init__(self, get_response=None):
        self._get_response_none_deprecation(get_response)
        self.get_response = get_response
        self._async_check()
        engine = import_module(settings.SESSION_ENGINE)
        self.SessionStore = engine.SessionStore

    def process_request(self, request):
        # self 是「中间件对象」，settings.SESSION_COOKIE_NAME 的值是 'sessionid'
        # 登录之前，请求的 COOKIES 中没有 sessionid 字段
        # POST 登录请求成功时，响应对象中会包含该字段
        session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        # 给「请求对象」增加一个 session 属性
        # 属性值是 django.contrib.sessions.backends.db.SessionStore 类的实例
        request.session = self.SessionStore(session_key)

    def process_response(self, request, response):
        """
        If request.session was modified, or if the configuration is to save the
        session every time, save the changes and set a session cookie or delete
        the session cookie if the session has been emptied.
        """
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response
        # First check if we need to delete this cookie.
        # The session should be deleted only if the session is entirely empty.
        if settings.SESSION_COOKIE_NAME in request.COOKIES and empty:
            # 退出登录时，执行下面这个方法设置 response.cookies 中的 sessionid 字段值为空字符串
            # 这样浏览器收到响应后，会重置 sessionid 字段
            # 因为对应的值是空字符串，所以就在浏览器 Cookie 中移除该字段
            response.delete_cookie(
                settings.SESSION_COOKIE_NAME,
                path=settings.SESSION_COOKIE_PATH,
                domain=settings.SESSION_COOKIE_DOMAIN,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            patch_vary_headers(response, ('Cookie',))
        else:
            if accessed:
                patch_vary_headers(response, ('Cookie',))
            if (modified or settings.SESSION_SAVE_EVERY_REQUEST) and not empty:
                if request.session.get_expire_at_browser_close():
                    max_age = None
                    expires = None
                else:
                    max_age = request.session.get_expiry_age()
                    expires_time = time.time() + max_age
                    expires = http_date(expires_time)
                # Save the session data and refresh the client cookie.
                # Skip session save for 500 responses, refs #3881.
                if response.status_code != 500:
                    try:
                        # 在 django.contrib.auth.__init__.login 函数的执行过程中调用过一次该方法
                        # 向 django_session 数据表中添加了一条数据
                        # 此处再次调用该方法，修改前面添加的那条数据中的 session_data 字段值，使之完整
                        request.session.save()
                    except UpdateError:
                        raise SuspiciousOperation(
                            "The request's session was deleted before the "
                            "request completed. The user may have logged "
                            "out in a concurrent request, for example."
                        )
                    # 给 response.cookies 属性字典中添加一组键值对
                    # key 是 'sessionid'
                    # value 是很长的字符串：
                    # 'Set-Cookie: sessionid=jr56mfliwgejxo4num4msgpsd8yzpde5; 
                    #  expires=Wed, 24 Feb 2021 14:55:25 GMT; 
                    #  HttpOnly; 
                    #  Max-Age=1209600; 
                    #  Path=/; 
                    #  SameSite=Lax'
                    response.set_cookie(
                        settings.SESSION_COOKIE_NAME,
                        request.session.session_key, max_age=max_age,
                        expires=expires, domain=settings.SESSION_COOKIE_DOMAIN,
                        path=settings.SESSION_COOKIE_PATH,
                        secure=settings.SESSION_COOKIE_SECURE or None,
                        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                        samesite=settings.SESSION_COOKIE_SAMESITE,
                    )
        return response
