# -*- coding: utf-8 -*-

from werkzeug.routing import Map, Rule
from werkzeug.exceptions import NotFound


def log_request(self):
    log = self.server.log
    if log:
        if hasattr(log, 'info'):
            log.info(self.format_request() + '\n')
        else:
            log.write(self.format_request() + '\n')


# Monkeys are made for freedom.
try:
    import gevent
    from geventwebsocket.gunicorn.workers import GeventWebSocketWorker as Worker
except ImportError:
    pass


if 'gevent' in locals():
    # Freedom-Patch logger for Gunicorn.
    if hasattr(gevent, 'pywsgi'):
        gevent.pywsgi.WSGIHandler.log_request = log_request


class SocketMiddleware(object):

    def __init__(self, wsgi_app, app, socket):
        self.ws = socket
        self.app = app
        self.wsgi_app = wsgi_app

    # 请求进来之后，首先调用应用对象的 wsig_app 方法，其实就是这个方法
    def __call__(self, environ, start_response):
        # self.ws 是当前模块中的 Sockets 类的实例，叫做「套接字对象」 
        # 该对象的 url_map 属性值是 werkzeug.routing 模块中的 Map 类的实例
        # 此实例的 bind_to_environ 方法根据请求信息创建一个 MapAdapter 类的实例并返回
        # MapAdapter 类也是定义在 werkzeug.routing 模块中
        adapter = self.ws.url_map.bind_to_environ(environ)
        try:
            # 匹配请求信息，返回视图函数和请求参数字典
            # 只有 flask_sockets.Sockets 类的实例注册的蓝图下的视图函数对应的请求才会匹配
            # 如果匹配不到，就会抛出 NotFound 异常，转而由 Flask 应用对象处理
            handler, values = adapter.match()
            #print('[flask_sockets.SocketMiddleware.__call__] handler:', handler)
            #print('[flask_sockets.SocketMiddleware.__call__] values:', values)
            # 这个变量的值是 geventwebsocket.websocket 模块中的 WebSocket 类的实例
            # 安装 flask-sockets 库时会安装 gevent-websocket 作为依赖库
            environment = environ['wsgi.websocket']
            #print('[flask_sockets.SocketMiddleware.__call__] environment:', environment)

            # 启动应用上下文对象和请求上下文对象
            with self.app.app_context():
                with self.app.request_context(environ):
                    # 调用视图函数，WebSocket 类的实例作为参数
                    # 视图函数可能处于阻塞状态，那么本次请求执行到这里也会阻塞
                    handler(environment, **values)
                    return []
        except (NotFound, KeyError):
            return self.wsgi_app(environ, start_response)


# 这个类的定位与 Flask 是相同的角色，可以看做是「应用对象」
class Sockets(object):

    def __init__(self, app=None):
        #: Compatibility with 'Flask' application.
        #: The :class:`~werkzeug.routing.Map` for this instance. You can use
        #: this to change the routing converters after the class was created
        #: but before any routes are connected.
        self.url_map = Map()

        #: Compatibility with 'Flask' application.
        #: All the attached blueprints in a dictionary by name. Blueprints
        #: can be attached multiple times so this dictionary does not tell
        #: you how often they got attached.
        self.blueprints = {}
        self._blueprint_order = []

        if app:
            self.init_app(app)

    def init_app(self, app):
        app.wsgi_app = SocketMiddleware(app.wsgi_app, app, self)

    def route(self, rule, **options):

        def decorator(f):
            endpoint = options.pop('endpoint', None)
            self.add_url_rule(rule, endpoint, f, **options)
            return f
        return decorator

    def add_url_rule(self, rule, _, f, **options):
        # 参数 rule 是路径，_ 是蓝图名字+视图函数名字的字符串 ，f 是对应的视图函数
        self.url_map.add(Rule(rule, endpoint=f))

    def register_blueprint(self, blueprint, **options):
        """
        Registers a blueprint for web sockets like for 'Flask' application.

        Decorator :meth:`~flask.app.setupmethod` is not applied, because it
        requires ``debug`` and ``_got_first_request`` attributes to be defined.
        """
        first_registration = False

        if blueprint.name in self.blueprints:
            assert self.blueprints[blueprint.name] is blueprint, (
                'A blueprint\'s name collision occurred between %r and '
                '%r.  Both share the same name "%s".  Blueprints that '
                'are created on the fly need unique names.'
                % (blueprint, self.blueprints[blueprint.name], blueprint.name))
        else:
            self.blueprints[blueprint.name] = blueprint
            self._blueprint_order.append(blueprint)
            first_registration = True

        # 与 Flask.register_blueprint 方法一样，调用蓝图自身的 register 方法
        blueprint.register(self, options, first_registration)


# CLI sugar.
if 'Worker' in locals():
    worker = Worker
