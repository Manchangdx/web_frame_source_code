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


'''
请求上下文栈和应用上下文栈都是 LocalStack 类的实例
这两个栈在启动应用时创建，它们只会被创建一次
也就是说 LocalStack 类只会被调用两次生成俩栈对象
'''
_request_ctx_stack = LocalStack()
_app_ctx_stack = LocalStack()

'''
下面四行代码创建两个请求上下文对象和两个应用上下文对象，这四个对象都是 LocalProxy 类的实例
此外在 flask.logging 模块中也会初始化一个 
如果使用了 Flask-Login 插件
还会在 flask_login.utils 模块中创建一个请求上下文对象 current_user
这六个上下文对象都是在启动应用时创建的，它们只会被创建一次
'''

current_app = LocalProxy(_find_app)
g = LocalProxy(partial(_lookup_app_object, "g"))
# 应用启动后，服务器收到请求时调用 app 的 __call__ 方法
# 调用此方法的结果就是调用 app 的 wsgi_app 方法
# 后者会创建一个 RequestContext 类的实例，叫做「请求上下文对象」
# 创建请求上下文对象过程中会创建一个 flask.wrappers 模块中的 Request 类的实例
# 该实例有一个 cookies 属性，包含请求数据中的 Cookie 数据，这是个字典对象
# 所以我们才可以使用 request.cookies
# 该 Request 实例会被赋值给请求上下文对象的 request 属性
# 下面这个 request 是一个请求代理
# 其参数是个偏函数，其调用结果就是 RequestContext 实例的 request 属性值
# 请求代理的 _get_current_object 方法的返回值就是这个 request 属性值
# 请求代理的所有属性都来自「请求上下文对象」
# 请求代理是一成不变的，每次收到请求后，调用一次偏函数，请求代理的属性就替换一次
request = LocalProxy(partial(_lookup_req_object, "request"))
# 参数为偏函数，偏函数的调用结果为 RequestContext 实例的 session 属性
# 这个属性的值为 flask.sessions 模块中定义的 SecureCookieSession 类的实例
# 该类继承了 CallbackDict 类，后者又继承了 dict 类
# 这个属性的值才是真正的 session ，下面的 session 是一个代理对象
session = LocalProxy(partial(_lookup_req_object, "session"))