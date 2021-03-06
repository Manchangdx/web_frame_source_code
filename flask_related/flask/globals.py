# -*- coding: utf-8 -*-
"""
    flask.globals
    ~~~~~~~~~~~~~

    Defines all the global objects that are proxies to the current
    active context.

    :copyright: 2010 Pallets
    :license: BSD-3-Clause
"""
from functools import partial

from werkzeug.local import LocalProxy
from werkzeug.local import LocalStack


_request_ctx_err_msg = """\
Working outside of request context.

This typically means that you attempted to use functionality that needed
an active HTTP request.  Consult the documentation on testing for
information about how to avoid this problem.\
"""
_app_ctx_err_msg = """\
Working outside of application context.

This typically means that you attempted to use functionality that needed
to interface with the current application object in some way. To solve
this, set up an application context with app.app_context().  See the
documentation for more information.\
"""


def _lookup_req_object(name): 
    top = _request_ctx_stack.top
    if top is None:
        raise RuntimeError(_request_ctx_err_msg)
    return getattr(top, name)


def _lookup_app_object(name):
    top = _app_ctx_stack.top
    if top is None:
        raise RuntimeError(_app_ctx_err_msg)
    return getattr(top, name)


def _find_app():
    top = _app_ctx_stack.top
    if top is None:
        raise RuntimeError(_app_ctx_err_msg)
    return top.app


"""
请求上下文栈对象和应用上下文栈对象都是 werkzeug.local.LocalStack 类的实例
这两个栈对象在启动应用时创建，它们只会被创建一次
也就是说 LocalStack 类只会被调用两次生成俩栈对象
"""
_request_ctx_stack = LocalStack()
_app_ctx_stack = LocalStack()

"""
下面四行代码创建两个请求上下文代理对象和两个应用上下文代理对象
这四个对象都是 werkzeug.local.LocalProxy 类的实例
此外在 flask.logging 模块中也会初始化一个 
如果使用了 Flask-Login 插件，还会在 flask_login.utils 模块中创建一个请求上下文代理对象 current_user
这六个上下文代理对象都是在启动应用时创建的，它们只会被创建一次


「请求/应用上下文栈对象」 _request_ctx_stack, _app_ctx_stack
        应用启动时创建，创建 1 次永久使用
「请求/应用上下文代理对象」 request, current_app, g, session, current_user
        应用启动时创建，创建 1 次永久使用
「请求/应用上下文对象」 RequestContext(), AppContext()
        每次请求创建 1 次
「请求/响应对象」 Request(), Response()
        每次请求创建 1 次
"""

g = LocalProxy(partial(_lookup_app_object, "g"))

# 应用启动后，服务器收到请求时调用 app 的 __call__ 方法
# 调用此方法的结果就是调用 app 的 wsgi_app 方法
# 后者会创建一个 RequestContext 类的实例，叫做「请求上下文对象」
# 创建「请求上下文对象」过程中会创建一个 flask.wrappers 模块中的 Request 类的实例，即「请求对象」
# 该实例有一个 cookies 属性，包含请求数据中的 Cookie 数据，这是个字典对象
# 所以我们才可以使用 request.cookies
# 该 Request 实例会被赋值给「请求上下文对象」的 request 属性
# 下面这个 request 是「请求上下文代理对象」，其参数是个偏函数
# 此偏函数的调用结果就是当前线程对应的「请求上下文对象」的 request 属性值，即「请求对象」
# 代理的 _get_current_object 方法的返回值就是这个偏函数的调用
# 代理的所有属性都来自「请求上下文对象」的 request 属性值
# 代理是一成不变的，每次收到请求后，调用一次偏函数，代理的属性就替换一次
request = LocalProxy(partial(_lookup_req_object, "request"))

# 应用启动后，服务器收到请求时调用 app 的 __call__ 方法
# 调用此方法的结果就是调用 app 的 wsgi_app 方法
# 后者会创建一个 RequestContext 类的实例，叫做「请求上下文对象」
# 然后 wsgi_app 会调用「请求上下文对象」的 push 方法
# 而 push 方法会调用 app 的 app_context 方法生成 AppContext 类的实例
# 所以每次服务器收到请求，都会生成一个 AppContext 类的实例，叫做「应用上下文对象」
# 生成实例需要传入一个 app 参数，该参数就是应用本身，也就是 Flask 类的实例
# 这个参数会赋值给 AppContext 实例的 app 属性
# 每次请求都会生成新的 AppContext 实例，但其 app 属性值都是同一个，就是应用本身
# _find_app 的方法的返回值就是应用本身，每次都是同一个
# 下面这个 current_app 是「应用上下文代理对象」，调用此对象的任何属性都是调用应用对象 app 的属性
current_app = LocalProxy(_find_app)

# 下面这个 session 是「会话上下文代理对象」
# 参数为偏函数，偏函数的调用结果为 RequestContext 实例的 session 属性
# 这个属性的值就是 flask.sessions 模块中定义的 SecureCookieSession 类的实例，叫做「会话对象」
# 每次服务器收到请求后，调用一次代理的 _get_current_object 方法
# 也就是执行偏函数，获取本次请求生成的「请求上下文对象」的 session 属性值，然后赋值给自身的一系列属性
# 所以代理一成不变，每次请求都会刷新代理的属性
session = LocalProxy(partial(_lookup_req_object, "session"))