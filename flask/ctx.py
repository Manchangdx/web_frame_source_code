# -*- coding: utf-8 -*-
"""
    flask.ctx
    ~~~~~~~~~

    Implements the objects required to keep the context.

    :copyright: 2010 Pallets
    :license: BSD-3-Clause
"""
import sys
from functools import update_wrapper

from werkzeug.exceptions import HTTPException

from ._compat import BROKEN_PYPY_CTXMGR_EXIT
from ._compat import reraise
from .globals import _app_ctx_stack
from .globals import _request_ctx_stack
from .signals import appcontext_popped
from .signals import appcontext_pushed


# a singleton sentinel value for parameter defaults
_sentinel = object()


class _AppCtxGlobals(object):
    """A plain object. Used as a namespace for storing data during an
    application context.

    Creating an app context automatically creates this object, which is
    made available as the :data:`g` proxy.

    .. describe:: 'key' in g

        Check whether an attribute is present.

        .. versionadded:: 0.10

    .. describe:: iter(g)

        Return an iterator over the attribute names.

        .. versionadded:: 0.10
    """

    def get(self, name, default=None):
        """Get an attribute by name, or a default value. Like
        :meth:`dict.get`.

        :param name: Name of attribute to get.
        :param default: Value to return if the attribute is not present.

        .. versionadded:: 0.10
        """
        return self.__dict__.get(name, default)

    def pop(self, name, default=_sentinel):
        """Get and remove an attribute by name. Like :meth:`dict.pop`.

        :param name: Name of attribute to pop.
        :param default: Value to return if the attribute is not present,
            instead of raise a ``KeyError``.

        .. versionadded:: 0.11
        """
        if default is _sentinel:
            return self.__dict__.pop(name)
        else:
            return self.__dict__.pop(name, default)

    def setdefault(self, name, default=None):
        """Get the value of an attribute if it is present, otherwise
        set and return a default value. Like :meth:`dict.setdefault`.

        :param name: Name of attribute to get.
        :param: default: Value to set and return if the attribute is not
            present.

        .. versionadded:: 0.11
        """
        return self.__dict__.setdefault(name, default)

    def __contains__(self, item):
        return item in self.__dict__

    def __iter__(self):
        return iter(self.__dict__)

    def __repr__(self):
        top = _app_ctx_stack.top
        if top is not None:
            return "<flask.g of %r>" % top.app.name
        return object.__repr__(self)


def after_this_request(f):
    """Executes a function after this request.  This is useful to modify
    response objects.  The function is passed the response object and has
    to return the same or a new one.

    Example::

        @app.route('/')
        def index():
            @after_this_request
            def add_header(response):
                response.headers['X-Foo'] = 'Parachute'
                return response
            return 'Hello World!'

    This is more useful if a function other than the view function wants to
    modify a response.  For instance think of a decorator that wants to add
    some headers without converting the return value into a response object.

    .. versionadded:: 0.9
    """
    _request_ctx_stack.top._after_request_functions.append(f)
    return f


def copy_current_request_context(f):
    """A helper function that decorates a function to retain the current
    request context.  This is useful when working with greenlets.  The moment
    the function is decorated a copy of the request context is created and
    then pushed when the function is called.  The current session is also
    included in the copied request context.

    Example::

        import gevent
        from flask import copy_current_request_context

        @app.route('/')
        def index():
            @copy_current_request_context
            def do_some_work():
                # do some work here, it can access flask.request or
                # flask.session like you would otherwise in the view function.
                ...
            gevent.spawn(do_some_work)
            return 'Regular response'

    .. versionadded:: 0.10
    """
    top = _request_ctx_stack.top
    if top is None:
        raise RuntimeError(
            "This decorator can only be used at local scopes "
            "when a request context is on the stack.  For instance within "
            "view functions."
        )
    reqctx = top.copy()

    def wrapper(*args, **kwargs):
        with reqctx:
            return f(*args, **kwargs)

    return update_wrapper(wrapper, f)


def has_request_context():
    """If you have code that wants to test if a request context is there or
    not this function can be used.  For instance, you may want to take advantage
    of request information if the request object is available, but fail
    silently if it is unavailable.

    ::

        class User(db.Model):

            def __init__(self, username, remote_addr=None):
                self.username = username
                if remote_addr is None and has_request_context():
                    remote_addr = request.remote_addr
                self.remote_addr = remote_addr

    Alternatively you can also just test any of the context bound objects
    (such as :class:`request` or :class:`g`) for truthness::

        class User(db.Model):

            def __init__(self, username, remote_addr=None):
                self.username = username
                if remote_addr is None and request:
                    remote_addr = request.remote_addr
                self.remote_addr = remote_addr

    .. versionadded:: 0.7
    """
    return _request_ctx_stack.top is not None


def has_app_context():
    """Works like :func:`has_request_context` but for the application
    context.  You can also just do a boolean check on the
    :data:`current_app` object instead.

    .. versionadded:: 0.9
    """
    return _app_ctx_stack.top is not None


class AppContext(object):
    """The application context binds an application object implicitly
    to the current thread or greenlet, similar to how the
    :class:`RequestContext` binds request information.  The application
    context is also implicitly created if a request context is created
    but the application is not on top of the individual application
    context.
    """

    def __init__(self, app):
        #print('【 AppContext 】初始化')
        self.app = app
        self.url_adapter = app.create_url_adapter(None)
        self.g = app.app_ctx_globals_class()

        # Like request context, app contexts can be pushed multiple times
        # but there a basic "refcount" is enough to track them.
        self._refcnt = 0

    def push(self):
        """Binds the app context to the current context."""
        self._refcnt += 1
        if hasattr(sys, "exc_clear"):
            sys.exc_clear()
        _app_ctx_stack.push(self)
        appcontext_pushed.send(self.app)

    def pop(self, exc=_sentinel):
        """Pops the app context."""
        try:
            self._refcnt -= 1
            if self._refcnt <= 0:
                if exc is _sentinel:
                    exc = sys.exc_info()[1]
                self.app.do_teardown_appcontext(exc)
        finally:
            rv = _app_ctx_stack.pop()
        assert rv is self, "Popped wrong app context.  (%r instead of %r)" % (rv, self)
        appcontext_popped.send(self.app)

    def __enter__(self):
        self.push()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.pop(exc_value)

        if BROKEN_PYPY_CTXMGR_EXIT and exc_type is not None:
            reraise(exc_type, exc_value, tb)


class RequestContext(object):
    """
    每次服务器收到请求，都会创建一个该类的实例
    调用此类的是 flask.app.Flask().request_context 方法
    """

    def __init__(self, app, environ, request=None, session=None):
        browser = 'Chrome'
        if 'OPR' in environ['HTTP_USER_AGENT']:
            browser = 'Opera'
        if 'Firefox' in environ['HTTP_USER_AGENT']:
            browser = 'Firefox'
        print('【flask.ctx.RequestContext】初始化', environ['RAW_URI'], '==={}==='.format(browser))
        import threading
        print('【flask.ctx.RequestContext】初始化，线程：', threading.current_thread().getName())
        self.app = app
        # 初始化时，通常不提供 request 和 session 这两个参数
        if request is None:
            # 调用 app.request_class 方法生成的是 
            # werkzeug.wrappers.request 模块中的 Request 类的实例
            # 这个实例有很多属性，例如 cookies 包含请求中的 Cookies 信息
            request = app.request_class(environ)
        self.request = request
        self.url_adapter = None
        try:
            # 路由适配器，等号后面的方法的返回值是 werkzeug.routing.MapAdapter 的实例
            self.url_adapter = app.create_url_adapter(self.request)
        except HTTPException as e:
            self.request.routing_exception = e
        self.flashes = None
        self.session = session

        # Request contexts can be pushed multiple times and interleaved with
        # other request contexts.  Now only if the last level is popped we
        # get rid of them.  Additionally if an application context is missing
        # one is created implicitly so for each level we add this information
        self._implicit_app_ctx_stack = []

        # indicator if the context was preserved.  Next time another context
        # is pushed the preserved context is popped.
        self.preserved = False

        # remembers the exception for pop if there is one in case the context
        # preservation kicks in.
        self._preserved_exc = None

        # Functions that should be executed after the request on the response
        # object.  These will be called before the regular "after_request"
        # functions.
        self._after_request_functions = []

    @property
    def g(self):
        return _app_ctx_stack.top.g

    @g.setter
    def g(self, value):
        _app_ctx_stack.top.g = value

    def copy(self):
        """Creates a copy of this request context with the same request object.
        This can be used to move a request context to a different greenlet.
        Because the actual request object is the same this cannot be used to
        move a request context to a different thread unless access to the
        request object is locked.

        .. versionadded:: 0.10

        .. versionchanged:: 1.1
           The current session object is used instead of reloading the original
           data. This prevents `flask.session` pointing to an out-of-date object.
        """
        return self.__class__(
            self.app,
            environ=self.request.environ,
            request=self.request,
            session=self.session,
        )

    def match_request(self):
        """Can be overridden by a subclass to hook into the matching
        of the request.
        """
        try:
            result = self.url_adapter.match(return_rule=True)
            # result 是元组，第一个元素是路由对象， Rule 实例
            # 第二个元素是字典，字典中包含路由中的参数
            self.request.url_rule, self.request.view_args = result
        except HTTPException as e:
            self.request.routing_exception = e

    def push(self):
        '''
        每次服务器收到请求，都会创建 RequestContext 实例并调用此方法
        此方法顺序完成如下操作：
        1、创建「应用上下文对象」并将其压入「应用上下文栈」的栈顶
        2、将「请求上下文对象」也就是 self 压入「请求上下文栈」的栈顶
        3、根据 self.request.cookies 生成 self.session 
        4、调用 self.match_request 方法给 self.request 定义两个路由相关的属性
        '''
        # 请求上下文栈顶，这会儿肯定是 None
        # 因为现在刚刚创建完「请求上下文对象」，还没把自身压入栈中
        top = _request_ctx_stack.top
        if top is not None and top.preserved:
            top.pop(top._preserved_exc)

        # 应用上下文栈的栈顶，这会儿肯定也是 None，应用上下文对象还没创建呢
        app_ctx = _app_ctx_stack.top
        if app_ctx is None or app_ctx.app != self.app:
            # 创建 AppContext 类的实例，即「应用上下文对象」
            app_ctx = self.app.app_context()
            # 调用「应用上下文对象」的 push 方法
            # 此方法内部会调用「应用上下文栈」的 push 方法
            # 将「应用上下文对象」自身压入「应用上下文栈」的栈顶
            app_ctx.push()
            self._implicit_app_ctx_stack.append(app_ctx)
        else:
            self._implicit_app_ctx_stack.append(None)

        if hasattr(sys, "exc_clear"):
            sys.exc_clear()

        import threading
        print('【flask.ctx.RequestContext().push】线程：', threading.current_thread().getName())
        # 调用「请求上下文栈」的 push 方法将「请求上下文对象」压入栈顶
        _request_ctx_stack.push(self)

        # 多数情况下，当前类实例化时 self.session 都是 None
        if self.session is None:
            # 这个 session_interface 就是 
            # flask.sessions 模块中的 SecureCookieSessionInterface 类的实例
            session_interface = self.app.session_interface
            # 调用 SecureCookieSessionInterface 实例的 open_session 方法
            # 返回 flask.sessions.SecureCookieSession 类的实例
            # 这个实例其实是个类字典对象，它有一些来自请求 Cookies 中的键值对
            # 包括 _fresh _id _user_id csrf_token 等字段
            self.session = session_interface.open_session(self.app, self.request)

            if self.session is None:
                self.session = session_interface.make_null_session(self.app)

        # self.url_adapter 是在当前类初始化的时候定义的属性
        # 属性值为路由适配器，werkzeug.routing.MapAdapter 的实例
        if self.url_adapter is not None:
            # 调用 self.match_request 方法给 self.request 定义两个路由相关的属性
            self.match_request()
        print('【flask.ctx.RequestContext().push】LocalStack().push 完成后')
        print('【flask.ctx.RequestContext().push】线程：', threading.current_thread().getName())

    def pop(self, exc=_sentinel):
        """Pops the request context and unbinds it by doing that.  This will
        also trigger the execution of functions registered by the
        :meth:`~flask.Flask.teardown_request` decorator.

        .. versionchanged:: 0.9
           Added the `exc` argument.
        """
        app_ctx = self._implicit_app_ctx_stack.pop()

        try:
            clear_request = False
            if not self._implicit_app_ctx_stack:
                self.preserved = False
                self._preserved_exc = None
                if exc is _sentinel:
                    exc = sys.exc_info()[1]
                self.app.do_teardown_request(exc)

                # If this interpreter supports clearing the exception information
                # we do that now.  This will only go into effect on Python 2.x,
                # on 3.x it disappears automatically at the end of the exception
                # stack.
                if hasattr(sys, "exc_clear"):
                    sys.exc_clear()

                request_close = getattr(self.request, "close", None)
                if request_close is not None:
                    request_close()
                clear_request = True
        finally:
            rv = _request_ctx_stack.pop()

            # get rid of circular dependencies at the end of the request
            # so that we don't require the GC to be active.
            if clear_request:
                rv.request.environ["werkzeug.request"] = None

            # Get rid of the app as well if necessary.
            if app_ctx is not None:
                app_ctx.pop(exc)

            assert rv is self, "Popped wrong request context. (%r instead of %r)" % (
                rv,
                self,
            )

    def auto_pop(self, exc):
        if self.request.environ.get("flask._preserve_context") or (
            exc is not None and self.app.preserve_context_on_exception
        ):
            self.preserved = True
            self._preserved_exc = exc
        else:
            self.pop(exc)

    def __enter__(self):
        self.push()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        # do not pop the request stack if we are in debug mode and an
        # exception happened.  This will allow the debugger to still
        # access the request object in the interactive shell.  Furthermore
        # the context can be force kept alive for the test client.
        # See flask.testing for how this works.
        self.auto_pop(exc_value)

        if BROKEN_PYPY_CTXMGR_EXIT and exc_type is not None:
            reraise(exc_type, exc_value, tb)

    def __repr__(self):
        return "<%s '%s' [%s] of %s>" % (
            self.__class__.__name__,
            self.request.url,
            self.request.method,
            self.app.name,
        )
