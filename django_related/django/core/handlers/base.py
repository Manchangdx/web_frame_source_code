import asyncio
import logging
import types

from asgiref.sync import async_to_sync, sync_to_async

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, MiddlewareNotUsed
from django.core.signals import request_finished
from django.db import connections, transaction
from django.urls import get_resolver, set_urlconf
from django.utils.log import log_response
from django.utils.module_loading import import_string

from .exception import convert_exception_to_response

logger = logging.getLogger('django.request')


# 它的子类 WSGIHandler 是应用对象类，定义在 django.core.handlers.wsgi 模块中
class BaseHandler:
    _view_middleware = None
    _template_response_middleware = None
    _exception_middleware = None
    _middleware_chain = None

    def load_middleware(self, is_async=False):
        print('【django.core.handlers.base.BaseHandler.load_middleware】应用对象初始化')
        # self 是应用对象，初始化时会调用当前方法
        self._view_middleware = []
        self._template_response_middleware = []
        self._exception_middleware = []

        # 默认情况下此变量的值是 self._get_response 方法，它定义在当前类中
        get_response = self._get_response_async if is_async else self._get_response
        
        # 下面这个函数来自 django.core.handlers.exception 模块
        # 此函数是一个装饰器，返回值是函数内的嵌套函数 inner ，调用的时候需要提供请求对象作为参数
        # 下面这个 handler 实际上等同于 self._get_response 方法
        handler = convert_exception_to_response(get_response)
        handler_is_async = is_async

        # 下面的 reversed 是 Python 内置函数
        # 参数是定义在项目配置文件中的中间件列表，返回值是参数倒序的迭代器
        # 这样使得项目配置文件中的中间件列表被倒序初始化（实例化）
        # 在处理请求对象的过程中顺序执行，在处理响应对象的过程中倒序执行
        for middleware_path in reversed(settings.MIDDLEWARE):
            # 此方法用于获取中间件类，Django 内置的中间件通常在 django.contrib 包下面
            middleware = import_string(middleware_path)

            middleware_can_sync = getattr(middleware, 'sync_capable', True)
            middleware_can_async = getattr(middleware, 'async_capable', False)
            if not middleware_can_sync and not middleware_can_async:
                raise RuntimeError(
                    'Middleware %s must have at least one of '
                    'sync_capable/async_capable set to True.' % middleware_path
                )
            elif not handler_is_async and middleware_can_sync:
                middleware_is_async = False
            else:
                middleware_is_async = middleware_can_async

            try:
                # 这里处理一下，实际上 handler 变量本身没有变化
                handler = self.adapt_method_mode(
                    middleware_is_async, 
                    handler, 
                    handler_is_async,
                    debug=settings.DEBUG, 
                    name='middleware %s' % middleware_path,
                )
                #print('【django.core.handlers.base.BaseHandler.load_middleware】middleware:', middleware)
                #print('【django.core.handlers.base.BaseHandler.load_middleware】handler:', handler)

                # middleware 是中间件，它通常是一个类，这里把 handler 函数作为参数获取其实例
                # 实例初始化时，会把参数 handler 赋值给实例自身的 get_response 属性
                mw_instance = middleware(handler)
            except MiddlewareNotUsed as exc:
                if settings.DEBUG:
                    if str(exc):
                        logger.debug('MiddlewareNotUsed(%r): %s', middleware_path, exc)
                    else:
                        logger.debug('MiddlewareNotUsed: %r', middleware_path)
                continue

            if mw_instance is None:
                raise ImproperlyConfigured(
                    'Middleware factory %s returned None.' % middleware_path
                )

            if hasattr(mw_instance, 'process_view'):
                self._view_middleware.insert(
                    0,
                    self.adapt_method_mode(is_async, mw_instance.process_view),
                )
            if hasattr(mw_instance, 'process_template_response'):
                self._template_response_middleware.append(
                    self.adapt_method_mode(is_async, mw_instance.process_template_response),
                )
            if hasattr(mw_instance, 'process_exception'):
                # The exception-handling stack is still always synchronous for
                # now, so adapt that way.
                self._exception_middleware.append(
                    self.adapt_method_mode(False, mw_instance.process_exception),
                )

            # 下面这一行代码导致 handler 变量的值发生变化，参数是中间件类的实例
            # 前面已经提到，下面这个函数来自 django.core.handlers.exception 模块
            # 它是一个装饰器，返回值是函数内的嵌套函数 inner ，调用的时候需要提供请求对象作为参数
            #
            # 每次执行下面这行代码，handler 就变成中间件实例，实例的 get_response 属性就是上一个 handler
            # 也就是说，下面这个 handler 的 get_response 属性值就是定义之前的 handler
            handler = convert_exception_to_response(mw_instance)
            
            handler_is_async = middleware_is_async

        # 此处不会改变 handler 变量指向的对象
        handler = self.adapt_method_mode(is_async, handler, handler_is_async)
        # 它可以看作是中间件链条的第一个中间件类的实例
        self._middleware_chain = handler

    def adapt_method_mode(
        self, is_async, method, method_is_async=None, debug=False, name=None,
    ):
        """
        Adapt a method to be in the correct "mode":
        - If is_async is False:
          - Synchronous methods are left alone
          - Asynchronous methods are wrapped with async_to_sync
        - If is_async is True:
          - Synchronous methods are wrapped with sync_to_async()
          - Asynchronous methods are left alone
        """
        if method_is_async is None:
            method_is_async = asyncio.iscoroutinefunction(method)
        if debug and not name:
            name = name or 'method %s()' % method.__qualname__
        if is_async:
            if not method_is_async:
                if debug:
                    logger.debug('Synchronous %s adapted.', name)
                return sync_to_async(method, thread_sensitive=True)
        elif method_is_async:
            if debug:
                logger.debug('Asynchronous %s adapted.' % name)
            return async_to_sync(method)
        #print('【django.core.handlers.base.BaseHandler.adapt_method_mode】method:', method)
        return method

    def get_response(self, request):
        # self 是「应用对象」，此方法利用「请求对象」创建「响应对象」并返回
        # 参数 request 是「请求对象」，它是 django.core.handlers.wsgi.WSGIRequest 类的实例
        #print('【django.core.handlers.base.BaseHandler.get_response】为创建「响应对象」做准备')

        set_urlconf(settings.ROOT_URLCONF)

        # self._middleware_chain 属性值是一个中间件类的实例
        # 此处调用中间件对象，也就是调用中间件对象的 __call__ 方法
        # 该 __call__ 方法定义在 django.utils.deprecation.MiddlewareMixin 类中
        # 在 __call__ 内部会调用中间件对象的 get_response 方法
        # 此方法本身就是另一个中间件对象，然后继续调用它的 __call__ 方法，链式调用
        # 最终，调用在当前类中定义的 self._get_response 方法返回响应对象
        # 然后链式返回，最后下面这个方法返回响应对象
        response = self._middleware_chain(request)
        response._resource_closers.append(request.close)
        if response.status_code >= 400:
            log_response(
                '%s: %s', response.reason_phrase, request.path,
                response=response,
                request=request,
            )
        return response

    async def get_response_async(self, request):
        """
        Asynchronous version of get_response.

        Funneling everything, including WSGI, into a single async
        get_response() is too slow. Avoid the context switch by using
        a separate async response path.
        """
        # Setup default url resolver for this thread.
        set_urlconf(settings.ROOT_URLCONF)
        response = await self._middleware_chain(request)
        response._resource_closers.append(request.close)
        if response.status_code >= 400:
            await sync_to_async(log_response, thread_sensitive=False)(
                '%s: %s', response.reason_phrase, request.path,
                response=response,
                request=request,
            )
        return response

    def _get_response(self, request):
        print('【django.core.handlers.base.BaseHandler._get_response】为创建「响应对象」做准备')
        response = None
        # self 是「应用对象」
        # 下面的 self.resolve_request 方法定义在当前类中，用于获取请求对应的视图函数及其参数
        # 顺利的话，此方法会返回 django.urls.resolvers.ResolverMatch 类的实例，叫做「路由匹配结果对象」
        # 该实例有一个 __getitem__ 方法，所以可以将自身赋值给三个变量，第一个就是处理请求的视图函数
        callback, callback_args, callback_kwargs = self.resolve_request(request)
        #print('【django.core.handlers.base.BaseHandler._get_response】callback:', callback)
        #print('【django.core.handlers.base.BaseHandler._get_response】callback_args:', callback_args)
        #print('【django.core.handlers.base.BaseHandler._get_response】callback_kwargs:', callback_kwargs)

        for middleware_method in self._view_middleware:
            response = middleware_method(request, callback, callback_args, callback_kwargs)
            if response:
                break

        if response is None:
            # 这里保证视图函数中数据库相关的操作具有原子性，返回值仍是视图函数
            wrapped_callback = self.make_view_atomic(callback)
            # If it is an asynchronous view, run it in a subthread.
            if asyncio.iscoroutinefunction(wrapped_callback):
                wrapped_callback = async_to_sync(wrapped_callback)
            try:
                print('【django.core.handlers.base.BaseHandler._get_response】获得视图对象:', wrapped_callback)
                # 调用视图对象返回「响应对象」，即 django.http.response.HttpResponse 类的实例
                response = wrapped_callback(request, *callback_args, **callback_kwargs)
            except Exception as e:
                response = self.process_exception_by_middleware(e, request)
                if response is None:
                    raise

        # Complain if the view returned None (a common error).
        self.check_response(response, callback)

        # If the response supports deferred rendering, apply template
        # response middleware and then render the response
        if hasattr(response, 'render') and callable(response.render):
            for middleware_method in self._template_response_middleware:
                response = middleware_method(request, response)
                # Complain if the template response middleware returned None (a common error).
                self.check_response(
                    response,
                    middleware_method,
                    name='%s.process_template_response' % (
                        middleware_method.__self__.__class__.__name__,
                    )
                )
            try:
                response = response.render()
            except Exception as e:
                response = self.process_exception_by_middleware(e, request)
                if response is None:
                    raise

        return response

    async def _get_response_async(self, request):
        """
        Resolve and call the view, then apply view, exception, and
        template_response middleware. This method is everything that happens
        inside the request/response middleware.
        """
        response = None
        callback, callback_args, callback_kwargs = self.resolve_request(request)

        # Apply view middleware.
        for middleware_method in self._view_middleware:
            response = await middleware_method(request, callback, callback_args, callback_kwargs)
            if response:
                break

        if response is None:
            wrapped_callback = self.make_view_atomic(callback)
            # If it is a synchronous view, run it in a subthread
            if not asyncio.iscoroutinefunction(wrapped_callback):
                wrapped_callback = sync_to_async(wrapped_callback, thread_sensitive=True)
            try:
                response = await wrapped_callback(request, *callback_args, **callback_kwargs)
            except Exception as e:
                response = await sync_to_async(
                    self.process_exception_by_middleware,
                    thread_sensitive=True,
                )(e, request)
                if response is None:
                    raise

        # Complain if the view returned None or an uncalled coroutine.
        self.check_response(response, callback)

        # If the response supports deferred rendering, apply template
        # response middleware and then render the response
        if hasattr(response, 'render') and callable(response.render):
            for middleware_method in self._template_response_middleware:
                response = await middleware_method(request, response)
                # Complain if the template response middleware returned None or
                # an uncalled coroutine.
                self.check_response(
                    response,
                    middleware_method,
                    name='%s.process_template_response' % (
                        middleware_method.__self__.__class__.__name__,
                    )
                )
            try:
                if asyncio.iscoroutinefunction(response.render):
                    response = await response.render()
                else:
                    response = await sync_to_async(response.render, thread_sensitive=True)()
            except Exception as e:
                response = await sync_to_async(
                    self.process_exception_by_middleware,
                    thread_sensitive=True,
                )(e, request)
                if response is None:
                    raise

        # Make sure the response is not a coroutine
        if asyncio.iscoroutine(response):
            raise RuntimeError('Response is still a coroutine.')
        return response

    def resolve_request(self, request):
        # 判断请求对象是否有此属性，此属性值是一个字符串，指向项目的路由适配模块
        if hasattr(request, 'urlconf'):
            urlconf = request.urlconf
            set_urlconf(urlconf)
            resolver = get_resolver(urlconf)
        else:
            # 此函数来自 django.urls.resolvers 模块
            # 其返回值是 django.urls.resolvers.URLResolver 类的实例
            # 该实例的 urlconf_name 属性值是项目的路由适配模块字符串 'xxxx.urls' 
            # 该实例我们称之为「路由处理对象」
            resolver = get_resolver()
        print('【django.core.handlers.base.BaseHandler.resolve_request】根据请求路径找视图对象，请求路径:', 
                request.path_info)
        # 将请求的绝对路径作为参数调用「路由处理对象」的 resolve 方法
        # 顺利的话，会返回 django.urls.resolvers.ResolverMatch 类的实例，叫做「路由匹配结果对象」
        resolver_match = resolver.resolve(request.path_info)
        request.resolver_match = resolver_match
        return resolver_match

    def check_response(self, response, callback, name=None):
        """
        Raise an error if the view returned None or an uncalled coroutine.
        """
        if not(response is None or asyncio.iscoroutine(response)):
            return
        if not name:
            if isinstance(callback, types.FunctionType):  # FBV
                name = 'The view %s.%s' % (callback.__module__, callback.__name__)
            else:  # CBV
                name = 'The view %s.%s.__call__' % (
                    callback.__module__,
                    callback.__class__.__name__,
                )
        if response is None:
            raise ValueError(
                "%s didn't return an HttpResponse object. It returned None "
                "instead." % name
            )
        elif asyncio.iscoroutine(response):
            raise ValueError(
                "%s didn't return an HttpResponse object. It returned an "
                "unawaited coroutine instead. You may need to add an 'await' "
                "into your view." % name
            )

    # Other utility methods.

    def make_view_atomic(self, view):
        # self 是应用对象，view 是视图函数
        non_atomic_requests = getattr(view, '_non_atomic_requests', set())
        for db in connections.all():
            if db.settings_dict['ATOMIC_REQUESTS'] and db.alias not in non_atomic_requests:
                if asyncio.iscoroutinefunction(view):
                    raise RuntimeError(
                        'You cannot use ATOMIC_REQUESTS with async views.'
                    )
                # TODO 保证视图函数涉及的数据库操作具有原子性
                view = transaction.atomic(using=db.alias)(view)
        return view

    def process_exception_by_middleware(self, exception, request):
        """
        Pass the exception to the exception middleware. If no middleware
        return a response for this exception, return None.
        """
        for middleware_method in self._exception_middleware:
            response = middleware_method(request, exception)
            if response:
                return response
        return None


def reset_urlconf(sender, **kwargs):
    """Reset the URLconf after each request is finished."""
    set_urlconf(None)


request_finished.connect(reset_urlconf)
