# -*- coding: utf-8 -*-
"""
    werkzeug.local
    ~~~~~~~~~~~~~~

    This module implements context-local objects.

    :copyright: 2007 Pallets
    :license: BSD-3-Clause
"""
import copy
from functools import update_wrapper

from ._compat import implements_bool
from ._compat import PY2
from .wsgi import ClosingIterator

# since each thread has its own greenlet we can just use those as identifiers
# for the context.  If greenlets are not available we fall back to the
# current thread ident depending on where it is.
try:
    from greenlet import getcurrent as get_ident
except ImportError:
    try:
        from thread import get_ident
    except ImportError:
        from _thread import get_ident


def release_local(local):
    """Releases the contents of the local for the current context.
    This makes it possible to use locals without a manager.

    Example::

        >>> loc = Local()
        >>> loc.foo = 42
        >>> release_local(loc)
        >>> hasattr(loc, 'foo')
        False

    With this function one can release :class:`Local` objects as well
    as :class:`LocalStack` objects.  However it is not possible to
    release data held by proxies that way, one always has to retain
    a reference to the underlying local object in order to be able
    to release it.

    .. versionadded:: 0.6.1
    """
    local.__release_local__()


class Local(object):
    '''
    应用启动后，该类会被实例化两次，实例化发生在 LocalStack 类的实例化过程之中
    '''
    __slots__ = ("__storage__", "__ident_func__")

    def __init__(self):
        # __storage__ 属性是空字典
        # 服务器收到请求后，LocalStack 实例执行 push 方法
        # 将当前线程的主协程对象作为 key ，{'stack': [obj]} 这样一个字典作为 value 
        # 存入 __storage__ 中，其中列表里的 obj 就是 push 方法的参数
        # 它的值只有两种情况：AppContext 或 RequestContext 类的实例
        # 处理完请求后，LocalStack 实例执行 pop 方法将 __storage__ 中的键值对清空
        object.__setattr__(self, "__storage__", {})
        # __ident_func__ 属性值是一个函数，这个函数的调用就是当前协程
        object.__setattr__(self, "__ident_func__", get_ident)

    def __iter__(self):
        return iter(self.__storage__.items())

    def __call__(self, proxy):
        """Create a proxy for a name."""
        return LocalProxy(self, proxy)

    def __release_local__(self):
        print('【werkzeug.local.Local.__release_local__】self.__ident_func__():', 
                self.__ident_func__())
        self.__storage__.pop(self.__ident_func__(), None)

    def __getattr__(self, name):
        try:
            return self.__storage__[self.__ident_func__()][name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        ident = self.__ident_func__()
        storage = self.__storage__
        try:
            storage[ident][name] = value
        except KeyError:
            storage[ident] = {name: value}

    def __delattr__(self, name):
        try:
            del self.__storage__[self.__ident_func__()][name]
        except KeyError:
            raise AttributeError(name)


class LocalStack(object):
    """
    应用启动时创建两个该类的实例：
    请求上下文栈 _request_ctx_stack 和应用上下文栈 _app_ctx_stack
    每个栈都有一个 _local 属性，属性值是当前模块中的 Local 类的实例
    """

    def __init__(self):
        self._local = Local()

    def __release_local__(self):
        self._local.__release_local__()

    @property
    def __ident_func__(self):
        return self._local.__ident_func__

    @__ident_func__.setter
    def __ident_func__(self, value):
        object.__setattr__(self._local, "__ident_func__", value)

    def __call__(self):
        def _lookup():
            rv = self.top
            if rv is None:
                raise RuntimeError("object unbound")
            return rv

        return LocalProxy(_lookup)

    def push(self, obj):
        '''将当前线程中的上下文对象压入栈中'''
        import threading
        print('【werkzeug.local.Localstack.push】将上下文对象压入栈中：', obj)
        # 服务器收到请求后，LocalStack 实例执行 push 方法
        # 将当前协程对象作为 key ，{'stack': [obj]} 这样一个字典作为 value 
        # 存入 __storage__ 属性中，其中列表里的 obj 就是 push 方法的参数
        # 该参数的值只有两种情况：AppContext 或 RequestContext 类的实例
        # 处理完请求后，LocalStack 实例执行 pop 方法将 __storage__ 中的键值对清空
        rv = getattr(self._local, "stack", None)
        if rv is None:
            self._local.stack = rv = []
        rv.append(obj)
        print("【werkzeug.local.Localstack.push】上下文栈.__storage__[当前协程]['stack']：", rv)
        return rv

    def pop(self):
        '''将当前线程的主协程对应的键值对从栈中移除'''
        import threading
        print('【werkzeug.local.Localstack.pop 】当前线程：', threading.current_thread().getName())
        stack = getattr(self._local, "stack", None)
        if stack is None:
            return None
        elif len(stack) == 1:
            release_local(self._local)
            return stack[-1]
        else:
            return stack.pop()

    @property
    def top(self):
        """
        返回的值通常是当前用户请求过程中创建的 AppContext 或 RequestContext 类的实例
        """
        try:
            # self._local 是 Local 类的实例
            # 其 stack[-1] 的值是该实例的 __storage__ 属性的当前线程的协程对应的值
            return self._local.stack[-1]
        except (AttributeError, IndexError):
            return None


class LocalManager(object):
    """Local objects cannot manage themselves. For that you need a local
    manager.  You can pass a local manager multiple locals or add them later
    by appending them to `manager.locals`.  Every time the manager cleans up,
    it will clean up all the data left in the locals for this context.

    The `ident_func` parameter can be added to override the default ident
    function for the wrapped locals.

    .. versionchanged:: 0.6.1
       Instead of a manager the :func:`release_local` function can be used
       as well.

    .. versionchanged:: 0.7
       `ident_func` was added.
    """

    def __init__(self, locals=None, ident_func=None):
        if locals is None:
            self.locals = []
        elif isinstance(locals, Local):
            self.locals = [locals]
        else:
            self.locals = list(locals)
        if ident_func is not None:
            self.ident_func = ident_func
            for local in self.locals:
                object.__setattr__(local, "__ident_func__", ident_func)
        else:
            self.ident_func = get_ident

    def get_ident(self):
        """Return the context identifier the local objects use internally for
        this context.  You cannot override this method to change the behavior
        but use it to link other context local objects (such as SQLAlchemy's
        scoped sessions) to the Werkzeug locals.

        .. versionchanged:: 0.7
           You can pass a different ident function to the local manager that
           will then be propagated to all the locals passed to the
           constructor.
        """
        return self.ident_func()

    def cleanup(self):
        """Manually clean up the data in the locals for this context.  Call
        this at the end of the request or use `make_middleware()`.
        """
        for local in self.locals:
            release_local(local)

    def make_middleware(self, app):
        """Wrap a WSGI application so that cleaning up happens after
        request end.
        """

        def application(environ, start_response):
            return ClosingIterator(app(environ, start_response), self.cleanup)

        return application

    def middleware(self, func):
        """Like `make_middleware` but for decorating functions.

        Example usage::

            @manager.middleware
            def application(environ, start_response):
                ...

        The difference to `make_middleware` is that the function passed
        will have all the arguments copied from the inner application
        (name, docstring, module).
        """
        return update_wrapper(self.make_middleware(func), func)

    def __repr__(self):
        return "<%s storages: %d>" % (self.__class__.__name__, len(self.locals))


@implements_bool
class LocalProxy(object):
    """
    """

    __slots__ = ("__local", "__dict__", "__name__", "__wrapped__")

    def __init__(self, local, name=None):
        '''
        参数 local 的值通常是一个函数
        其返回值是 AppContext 或 RequestContext 的实例的某个属性
        '''
        # 如果使用 object 的 __setattr__ 定义属性，需要提供仨参数
        # 其中第二个参数为属性名的字符串，如果是私有属性，就得加上类名
        # 设置 __local 属性值为一个函数
        # 此函数可以直接调用，返回值是 AppContext 或 RequestContext 的实例的某个属性
        object.__setattr__(self, "_LocalProxy__local", local)
        object.__setattr__(self, "__name__", name)
        if callable(local) and not hasattr(local, "__release_local__"):
            # "local" is a callable that is not an instance of Local or
            # LocalManager: mark it as a wrapped function.
            object.__setattr__(self, "__wrapped__", local)

    def _get_current_object(self):
        '''
        该方法的返回值是 RequestContext 的实例的 session 属性值
        '''
        if not hasattr(self.__local, "__release_local__"):
            return self.__local()
        try:
            return getattr(self.__local, self.__name__)
        except AttributeError:
            raise RuntimeError("no object bound to %s" % self.__name__)

    @property
    def __dict__(self):
        '''
        '''
        try:
            return self._get_current_object().__dict__
        except RuntimeError:
            raise AttributeError("__dict__")

    def __repr__(self):
        try:
            obj = self._get_current_object()
        except RuntimeError:
            return "<%s unbound>" % self.__class__.__name__
        return repr(obj)

    def __bool__(self):
        try:
            return bool(self._get_current_object())
        except RuntimeError:
            return False

    def __unicode__(self):
        try:
            return unicode(self._get_current_object())  # noqa
        except RuntimeError:
            return repr(self)

    def __dir__(self):
        try:
            return dir(self._get_current_object())
        except RuntimeError:
            return []

    def __getattr__(self, name):
        if name == "__members__":
            return dir(self._get_current_object())
        return getattr(self._get_current_object(), name)

    def __setitem__(self, key, value):
        self._get_current_object()[key] = value

    def __delitem__(self, key):
        del self._get_current_object()[key]

    if PY2:
        __getslice__ = lambda x, i, j: x._get_current_object()[i:j]

        def __setslice__(self, i, j, seq):
            self._get_current_object()[i:j] = seq

        def __delslice__(self, i, j):
            del self._get_current_object()[i:j]

    __setattr__ = lambda x, n, v: setattr(x._get_current_object(), n, v)
    __delattr__ = lambda x, n: delattr(x._get_current_object(), n)
    __str__ = lambda x: str(x._get_current_object())
    __lt__ = lambda x, o: x._get_current_object() < o
    __le__ = lambda x, o: x._get_current_object() <= o
    __eq__ = lambda x, o: x._get_current_object() == o
    __ne__ = lambda x, o: x._get_current_object() != o
    __gt__ = lambda x, o: x._get_current_object() > o
    __ge__ = lambda x, o: x._get_current_object() >= o
    __cmp__ = lambda x, o: cmp(x._get_current_object(), o)  # noqa
    __hash__ = lambda x: hash(x._get_current_object())
    __call__ = lambda x, *a, **kw: x._get_current_object()(*a, **kw)
    __len__ = lambda x: len(x._get_current_object())
    __getitem__ = lambda x, i: x._get_current_object()[i]
    __iter__ = lambda x: iter(x._get_current_object())
    __contains__ = lambda x, i: i in x._get_current_object()
    __add__ = lambda x, o: x._get_current_object() + o
    __sub__ = lambda x, o: x._get_current_object() - o
    __mul__ = lambda x, o: x._get_current_object() * o
    __floordiv__ = lambda x, o: x._get_current_object() // o
    __mod__ = lambda x, o: x._get_current_object() % o
    __divmod__ = lambda x, o: x._get_current_object().__divmod__(o)
    __pow__ = lambda x, o: x._get_current_object() ** o
    __lshift__ = lambda x, o: x._get_current_object() << o
    __rshift__ = lambda x, o: x._get_current_object() >> o
    __and__ = lambda x, o: x._get_current_object() & o
    __xor__ = lambda x, o: x._get_current_object() ^ o
    __or__ = lambda x, o: x._get_current_object() | o
    __div__ = lambda x, o: x._get_current_object().__div__(o)
    __truediv__ = lambda x, o: x._get_current_object().__truediv__(o)
    __neg__ = lambda x: -(x._get_current_object())
    __pos__ = lambda x: +(x._get_current_object())
    __abs__ = lambda x: abs(x._get_current_object())
    __invert__ = lambda x: ~(x._get_current_object())
    __complex__ = lambda x: complex(x._get_current_object())
    __int__ = lambda x: int(x._get_current_object())
    __long__ = lambda x: long(x._get_current_object())  # noqa
    __float__ = lambda x: float(x._get_current_object())
    __oct__ = lambda x: oct(x._get_current_object())
    __hex__ = lambda x: hex(x._get_current_object())
    __index__ = lambda x: x._get_current_object().__index__()
    __coerce__ = lambda x, o: x._get_current_object().__coerce__(x, o)
    __enter__ = lambda x: x._get_current_object().__enter__()
    __exit__ = lambda x, *a, **kw: x._get_current_object().__exit__(*a, **kw)
    __radd__ = lambda x, o: o + x._get_current_object()
    __rsub__ = lambda x, o: o - x._get_current_object()
    __rmul__ = lambda x, o: o * x._get_current_object()
    __rdiv__ = lambda x, o: o / x._get_current_object()
    if PY2:
        __rtruediv__ = lambda x, o: x._get_current_object().__rtruediv__(o)
    else:
        __rtruediv__ = __rdiv__
    __rfloordiv__ = lambda x, o: o // x._get_current_object()
    __rmod__ = lambda x, o: o % x._get_current_object()
    __rdivmod__ = lambda x, o: x._get_current_object().__rdivmod__(o)
    __copy__ = lambda x: copy.copy(x._get_current_object())
    __deepcopy__ = lambda x, memo: copy.deepcopy(x._get_current_object(), memo)
