# -*- coding: utf-8 -*-
"""
    flask.views
    ~~~~~~~~~~~

    This module provides class-based views inspired by the ones in Django.

    :copyright: 2010 Pallets
    :license: BSD-3-Clause
"""
from ._compat import with_metaclass
from .globals import request


http_method_funcs = frozenset(
    ["get", "post", "head", "options", "delete", "put", "trace", "patch"]
)


class View(object):

    methods = None

    provide_automatic_options = None

    decorators = ()

    def dispatch_request(self):
        raise NotImplementedError()

    @classmethod
    def as_view(cls, name, *class_args, **class_kwargs):
        # 这个类方法的返回值就是下面的内嵌函数 view 

        def view(*args, **kwargs):
            self = view.view_class(*class_args, **class_kwargs)
            print(f'【flask.views.View.as_view.view】视图类实例: {self}')
            if kwargs:
                print(f'【flask.views.View.as_view.view】请求路径 path 参数: {kwargs}')
            print('【cmblab.libs.views.APIView.dispatch_request】开始在项目内部处理请求...')
            return self.dispatch_request(*args, **kwargs)

        if cls.decorators:
            view.__name__ = name
            view.__module__ = cls.__module__
            for decorator in cls.decorators:
                view = decorator(view)

        view.view_class = cls
        view.__name__ = name
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.methods = cls.methods
        view.provide_automatic_options = cls.provide_automatic_options
        return view


class MethodViewType(type):
    """Metaclass for :class:`MethodView` that determines what methods the view
    defines.
    """

    def __init__(cls, name, bases, d):
        super(MethodViewType, cls).__init__(name, bases, d)

        if "methods" not in d:
            methods = set()

            for base in bases:
                if getattr(base, "methods", None):
                    methods.update(base.methods)

            for key in http_method_funcs:
                if hasattr(cls, key):
                    methods.add(key.upper())

            # If we have no method at all in there we don't want to add a
            # method list. This is for instance the case for the base class
            # or another subclass of a base method view that does not introduce
            # new methods.
            if methods:
                cls.methods = methods


# 此类的参数是一个函数，函数的返回值是临时基类
# 临时基类在派生子类时调用临时基类自身的临时元类的 __new__ 方法
# 这个 __new__ 方法会调用 MethodViewType 这个元类创建子类，并且子类的父类是 View 类
# 也就是说 MethodView 的父类是 View ，元类是 MethodViewType
class MethodView(with_metaclass(MethodViewType, View)):
# 上一行代码等同于 class MethodView(View, metaclass=MethodViewType):

    # 这个方法会被父类的类方法 View.as_view 中定义的 view 方法调用
    def dispatch_request(self, *args, **kwargs):
        print('args:', args)
        print('kw:', kw)
        meth = getattr(self, request.method.lower(), None)

        # If the request method is HEAD and we don't have a handler for it
        # retry with GET.
        if meth is None and request.method == "HEAD":
            meth = getattr(self, "get", None)

        assert meth is not None, "Unimplemented method %r" % request.method
        return meth(*args, **kwargs)
