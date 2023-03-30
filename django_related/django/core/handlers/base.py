import logging
import types

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, MiddlewareNotUsed
from django.core.signals import request_finished
from django.db import connections, transaction
from django.urls import get_resolver, set_urlconf
from django.utils.log import log_response
from django.utils.module_loading import import_string

from .exception import convert_exception_to_response

logger = logging.getLogger('django.request')


class BaseHandler:
    """该类的实例是「应用对象」
    """
    _view_middleware = None
    _template_response_middleware = None
    _exception_middleware = None
    _middleware_chain = None

    def load_middleware(self):
        """应用对象加载中间件
        """
        # self 是「应用对象」，初始化时会调用当前方法
        print('【django.core.handlers.base.BaseHandler.load_middleware】「应用对象」加载中间件')

        # 下面这个列表里面是各中间件实例的 process_view 方法
        # 这些方法在 self._get_response 中会被循环调用
        self._view_middleware = []
        # 下面这个列表里面是各中间件实例的 process_template_response 方法
        # 这些方法在 self._get_response 中会被循环调用
        self._template_response_middleware = []
        # 下面这个列表里面是各中间件实例的 process_exception 方法
        # 这些方法在 self.process_exception_by_middleware 中会被循环调用
        # 而后者在 self._get_response 中被调用
        self._exception_middleware = []

        # 下面这个函数来自 django.core.handlers.exception 模块
        # 此函数是一个装饰器，返回值是函数内的嵌套函数 inner ，调用的时候需要提供「请求对象」作为参数
        # 这个装饰器函数的作用就是捕获参数 self._get_response 处理请求时抛出的异常，返回一个加工过的「响应对象」
        # 调用下面这个 handler 函数实际就是调用 self._get_response 方法
        handler = convert_exception_to_response(self._get_response)

        # 下面的 reversed 是 Python 内置函数
        # 参数是定义在项目配置文件中的中间件列表，返回值是参数列表倒序的迭代器
        # 这样使得项目配置文件中的中间件列表被倒序初始化（实例化）
        # 在处理请求对象的过程中顺序执行，在处理响应对象的过程中倒序执行
        for middleware_path in reversed(settings.MIDDLEWARE):
            # 此方法用于获取中间件类，Django 内置的中间件通常在 django.contrib 包下面
            middleware = import_string(middleware_path)
            print(f'\t【django.core.handlers.base.BaseHandler.load_middleware】{middleware=}')
            try:
                # middleware 是中间件，它通常是一个类，这里把 handler 函数作为参数获取其实例
                # 实例初始化时，会把参数 handler 赋值给实例自身的 get_response 属性
                # 也就是说，mw_instance.get_response 就是 handler 函数
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
                self._view_middleware.insert(0, mw_instance.process_view)
            if hasattr(mw_instance, 'process_template_response'):
                self._template_response_middleware.append(mw_instance.process_template_response)
            if hasattr(mw_instance, 'process_exception'):
                self._exception_middleware.append(mw_instance.process_exception)

            # 下面这一行代码导致 handler 变量的值发生变化，参数是中间件类的实例
            # 前面已经提到，下面这个函数来自 django.core.handlers.exception 模块
            # 它是一个装饰器，返回值是函数内的嵌套函数 inner ，调用的时候需要提供请求对象作为参数

            # 每次执行下面这行代码，handler 就变成中间件实例，实例的 get_response 属性就是上一个 handler
            # 也就是说，下面这个 handler 的 get_response 属性值就是定义之前的 handler
            # 这样就形成了一个函数堆栈，堆栈中各个函数之间是链式调用关系

            # 假设 settings.MIDDLEWARE 列表的顺序是 a b c
            # 这个 for 循环的顺序就是 c b a , 最后一个 handler 就是中间件列表中第一个中间件类的实例
            # 链式调用 handler 的次序就是 a b c
            handler = convert_exception_to_response(mw_instance)

        # 它可以看作是中间件链条的第一个中间件类的实例
        self._middleware_chain = handler

    def make_view_atomic(self, view):
        non_atomic_requests = getattr(view, '_non_atomic_requests', set())
        for db in connections.all():
            if db.settings_dict['ATOMIC_REQUESTS'] and db.alias not in non_atomic_requests:
                view = transaction.atomic(using=db.alias)(view)
        return view

    def get_response(self, request):
        """根据「请求对象」创建「响应对象」

        self 是「应用对象」，此方法利用「请求对象」创建「响应对象」并返回
        request 是「请求对象」，它是 django.core.handlers.wsgi.WSGIRequest 类的实例
        """

        print('【django.core.handlers.base.BaseHandler.get_response】「应用对象」根据「请求对象」创建「响应对象」')
        print('【django.core.handlers.base.BaseHandler.get_response】依次调用中间件对象的 process_request 方法')
        set_urlconf(settings.ROOT_URLCONF)

        # self._middleware_chain 就是第一个中间件类的实例
        # 假设 settings.MIDDLEWARE 列表的顺序是 a b c
        # 此处按照同样的顺序调用中间件对象，也就是调用中间件对象的 __call__ 方法
        # 大部分中间件对象的 __call__ 方法都是 django.utils.deprecation.MiddlewareMixin.__call__
        # 1. 首先调用中间件对象的 process_request 做点事，参数是「请求对象」
        # 2. 然后调用中间件对象的 get_response 获取「响应对象」，此属性其实就是下一个中间件对象，参数依然是「请求对象」
        #    也就是说这步其实就是调用下个中间件对象的 __call__ 方法
        # 3. 这样按照 a b c 的顺序调用每个中间件对象的 __call__ 方法
        #    然后它们都执行了 self.process_request 方法，在调用视图函数之前做了点事
        #    一直到最后一个中间件的 get_response 属性是当前类中定义的 _get_response 方法，就在下面👇
        #    这个方法是真正要调用视图函数处理业务逻辑的
        # 4. 当 _get_response 方法返回「响应对象」后，再按照 c b a 的顺序倒过来继续执行每个中间件对象的 __call__ 方法
        # 5. 在接下来的 __call__ 方法中执行中间件对象的 process_response 方法再做点收尾工作，参数是「响应对象」
        # 6. 最后返回处理好的「响应对象」
        response = self._middleware_chain(request)
        response._closable_objects.append(request)
        if response.status_code >= 400:
            message = f'{response.reason_phrase}: {request.path}'
            print(f'【django.core.handlers.base.BaseHandler.get_response】响应异常: {message}')
        return response

    def _get_response(self, request):
        response = None

        if hasattr(request, 'urlconf'):
            urlconf = request.urlconf
            set_urlconf(urlconf)
            resolver = get_resolver(urlconf)
        else:
            resolver = get_resolver()

        # 这块儿 resolver_match 是根据请求路由匹配到的 django.urls.resolvers.ResolverMatch 类的实例
        resolver_match = resolver.resolve(request.path_info)

        # 这块儿 callback 就是视图类的 as_view 方法的调用
        # 它实际是 django.views.generic.base.View.as_view.view 方法，可以把它当成视图函数
        callback, callback_args, callback_kwargs = resolver_match
        print(
            f'【django.core.handlers.base.BaseHandler._get_response】根据「请求对象」中的路径信息找到对应的视图类: '
            f'{callback.__name__}'
        )

        request.resolver_match = resolver_match

        # 这里可能有一个 django.middleware.csrf.CsrfViewMiddleware.process_view 中间件验证函数
        for middleware_method in self._view_middleware:
            response = middleware_method(request, callback, callback_args, callback_kwargs)
            if response:
                break

        if response is None:
            # 这里保证视图函数中数据库相关的操作具有原子性，返回值仍是视图函数
            wrapped_callback = self.make_view_atomic(callback)
            try:
                # 下面的 wrapped_back 是 django.views.generic.base.View.as_view 方法中的内嵌函数 view
                # 调用该函数找到视图函数，调用视图函数创建并返回「响应对象」，即 django.http.response.HttpResponse 类的实例
                response = wrapped_callback(request, *callback_args, **callback_kwargs)
            except Exception as e:
                response = self.process_exception_by_middleware(e, request)

        if response is None:
            if isinstance(callback, types.FunctionType):    # FBV
                view_name = callback.__name__
            else:                                           # CBV
                view_name = callback.__class__.__name__ + '.__call__'

            raise ValueError(
                "The view %s.%s didn't return an HttpResponse object. It "
                "returned None instead." % (callback.__module__, view_name)
            )

        # 原注释：如果响应支持延迟呈现，则应用模板响应中间件，然后呈现响应
        elif hasattr(response, 'render') and callable(response.render):
            for middleware_method in self._template_response_middleware:
                response = middleware_method(request, response)
                # 如果模板响应中间件返回 None 则抛出异常
                if response is None:
                    raise ValueError(
                        "%s.process_template_response didn't return an "
                        "HttpResponse object. It returned None instead."
                        % (middleware_method.__self__.__class__.__name__)
                    )

            try:
                # 等号后面的 response 是「响应对象」
                # 该对象的 render 方法定义在 django.template.response.SimpleTemplateResponse 类中
                # 该方法会为自身的 content 属性赋值携带渲染完毕的模板文件内容字符串的「响应体字符串对象」
                # 后者是 django.utils.safestring.SafeString 类的实例
                # 该方法的返回值仍是「响应对象」自身
                response = response.render()
            except Exception as e:
                response = self.process_exception_by_middleware(e, request)

        return response

    def process_exception_by_middleware(self, exception, request):
        """
        Pass the exception to the exception middleware. If no middleware
        return a response for this exception, raise it.
        """
        for middleware_method in self._exception_middleware:
            response = middleware_method(request, exception)
            if response:
                return response
        raise


def reset_urlconf(sender, **kwargs):
    """Reset the URLconf after each request is finished."""
    set_urlconf(None)


request_finished.connect(reset_urlconf)
