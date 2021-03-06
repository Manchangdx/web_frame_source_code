"""Functions for use in URLsconfs."""
from functools import partial
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured

from .resolvers import (
    LocalePrefixPattern, RegexPattern, RoutePattern, URLPattern, URLResolver,
)


def include(arg, namespace=None):
    """此函数返回三元元组，第一个元素是 arg 对应的模块对象
    """

    app_name = None
    if isinstance(arg, tuple):
        # Callable returning a namespace hint.
        try:
            urlconf_module, app_name = arg
        except ValueError:
            if namespace:
                raise ImproperlyConfigured(
                    'Cannot override the namespace for a dynamic module that '
                    'provides a namespace.'
                )
            raise ImproperlyConfigured(
                'Passing a %d-tuple to include() is not supported. Pass a '
                '2-tuple containing the list of patterns and app_name, and '
                'provide the namespace argument to include() instead.' % len(arg)
            )
    else:
        # No namespace hint - use manually provided namespace.
        urlconf_module = arg

    if isinstance(urlconf_module, str):
        # 根据参数 arg 从应用程序中找到对应的模块对象赋值给等号前面的变量
        urlconf_module = import_module(urlconf_module)
    # 下面的变量是模块中的 urlpatterns 列表
    patterns = getattr(urlconf_module, 'urlpatterns', urlconf_module)
    app_name = getattr(urlconf_module, 'app_name', app_name)
    if namespace and not app_name:
        raise ImproperlyConfigured(
            'Specifying a namespace in include() without providing an app_name '
            'is not supported. Set the app_name attribute in the included '
            'module, or pass a 2-tuple containing the list of patterns and '
            'app_name instead.',
        )
    namespace = namespace or app_name
    # Make sure the patterns can be iterated through (without this, some
    # testcases will break).
    if isinstance(patterns, (list, tuple)):
        for url_pattern in patterns:
            pattern = getattr(url_pattern, 'pattern', None)
            if isinstance(pattern, LocalePrefixPattern):
                raise ImproperlyConfigured(
                    'Using i18n_patterns in an included URLconf is not allowed.'
                )
    return (urlconf_module, app_name, namespace)


def _path(route, view, kwargs=None, name=None, Pattern=None):
    if isinstance(view, (list, tuple)):
        # 下面的变量是 django.urls.resolvers.RoutePattern 类的实例
        # 该实例的 _route 属性值是参数 route 的值，它是一个路径字符串
        pattern = Pattern(route, is_endpoint=False)
        # 如果参数 view 是元组/列表，那就一定是三元的
        # 其中第一个元素可能是路由处理模块，也可能是模块中定义的 urlpatterns 列表
        urlconf_module, app_name, namespace = view
        # 下面这个返回值是「路由处理对象」，是 django.ruls.resolvers.URLResolver 类的实例
        return URLResolver(
            pattern,
            urlconf_module,
            kwargs,
            app_name=app_name,
            namespace=namespace,
        )
    # 如果第二个参数 view 是可调用对象
    elif callable(view):
        # 下面的变量是 django.urls.resolvers.RoutePattern 类的实例
        # 该实例的 _route 属性值是参数 route 的值，它是一个路径字符串
        # 该实例的 name 属性值是参数 name 的值
        pattern = Pattern(route, name=name, is_endpoint=True)
        # 返回值是「路由模式对象」，是 django.ruls.resolvers.URLPattern 类的实例
        return URLPattern(pattern, view, kwargs, name)
    else:
        raise TypeError('view must be a callable or a list/tuple in the case of include().')


# 路由处理对象，django.ruls.resolvers.URLResolver 类的实例
path = partial(_path, Pattern=RoutePattern)
re_path = partial(_path, Pattern=RegexPattern)
