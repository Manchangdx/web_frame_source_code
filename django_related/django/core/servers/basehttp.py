"""
HTTP server that implements the Python WSGI protocol (PEP 333, rev 1.21).

Based on wsgiref.simple_server which is part of the standard library since 2.5.

This is a simple server for use in testing or debugging Django apps. It hasn't
been reviewed for security issues. DON'T USE IT FOR PRODUCTION USE!
"""

import logging
import socket
import socketserver
import sys
from wsgiref import simple_server

from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.wsgi import LimitedStream
from django.core.wsgi import get_wsgi_application
from django.utils.module_loading import import_string

__all__ = ('WSGIServer', 'WSGIRequestHandler')

logger = logging.getLogger('django.server')


def get_internal_wsgi_application():
    """
    Load and return the WSGI application as configured by the user in
    ``settings.WSGI_APPLICATION``. With the default ``startproject`` layout,
    this will be the ``application`` object in ``projectname/wsgi.py``.

    This function, and the ``WSGI_APPLICATION`` setting itself, are only useful
    for Django's internal server (runserver); external WSGI servers should just
    be configured to point to the correct application object directly.

    If settings.WSGI_APPLICATION is not set (is ``None``), return
    whatever ``django.core.wsgi.get_wsgi_application`` returns.
    """
    from django.conf import settings
    # 这个属性值来自项目自身的配置文件，属性值是字符串
    app_path = getattr(settings, 'WSGI_APPLICATION')
    if app_path is None:
        return get_wsgi_application()

    try:
        # 返回的是 django.core.handlers.wsgi.WSGIHandler 类的实例，相当于应用对象
        return import_string(app_path)
    except ImportError as err:
        raise ImproperlyConfigured(
            "WSGI application '%s' could not be loaded; "
            "Error importing module." % app_path
        ) from err


def is_broken_pipe_error():
    exc_type, _, _ = sys.exc_info()
    return issubclass(exc_type, BrokenPipeError)


class WSGIServer(simple_server.WSGIServer):
    """BaseHTTPServer that implements the Python WSGI protocol"""

    request_queue_size = 10

    def __init__(self, *args, ipv6=False, allow_reuse_address=True, **kwargs):
        if ipv6:
            self.address_family = socket.AF_INET6
        self.allow_reuse_address = allow_reuse_address
        super().__init__(*args, **kwargs)

    def handle_error(self, request, client_address):
        if is_broken_pipe_error():
            logger.info("- Broken pipe from %s\n", client_address)
        else:
            super().handle_error(request, client_address)


class ThreadedWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """A threaded version of the WSGIServer"""
    daemon_threads = True


class ServerHandler(simple_server.ServerHandler):
    http_version = '1.1'

    def __init__(self, stdin, stdout, stderr, environ, **kwargs):
        """
        Use a LimitedStream so that unread request data will be ignored at
        the end of the request. WSGIRequest uses a LimitedStream but it
        shouldn't discard the data since the upstream servers usually do this.
        This fix applies only for testserver/runserver.
        """
        try:
            content_length = int(environ.get('CONTENT_LENGTH'))
        except (ValueError, TypeError):
            content_length = 0
        super().__init__(LimitedStream(stdin, content_length), stdout, stderr, environ, **kwargs)

    def cleanup_headers(self):
        super().cleanup_headers()
        # HTTP/1.1 requires support for persistent connections. Send 'close' if
        # the content length is unknown to prevent clients from reusing the
        # connection.
        if 'Content-Length' not in self.headers:
            self.headers['Connection'] = 'close'
        # Persistent connections require threading server.
        elif not isinstance(self.request_handler.server, socketserver.ThreadingMixIn):
            self.headers['Connection'] = 'close'
        # Mark the connection for closing if it's set as such above or if the
        # application sent the header.
        if self.headers.get('Connection') == 'close':
            self.request_handler.close_connection = True

    def close(self):
        self.get_stdin()._read_limited()
        super().close()

    def handle_error(self):
        # Ignore broken pipe errors, otherwise pass on
        if not is_broken_pipe_error():
            super().handle_error()


# 此类的实例就是「请求处理对象」
# 此类的终极父类是 socketserver.BaseRequestHandler 类，初始化方法就在后者中
class WSGIRequestHandler(simple_server.WSGIRequestHandler):
    protocol_version = 'HTTP/1.1'

    def address_string(self):
        # Short-circuit parent method to not call socket.getfqdn
        return self.client_address[0]

    def log_message(self, format, *args):
        extra = {
            'request': self.request,
            'server_time': self.log_date_time_string(),
        }
        if args[1][0] == '4':
            # 0x16 = Handshake, 0x03 = SSL 3.0 or TLS 1.x
            if args[0].startswith('\x16\x03'):
                extra['status_code'] = 500
                logger.error(
                    "You're accessing the development server over HTTPS, but "
                    "it only supports HTTP.\n", extra=extra,
                )
                return

        if args[1].isdigit() and len(args[1]) == 3:
            status_code = int(args[1])
            extra['status_code'] = status_code

            if status_code >= 500:
                level = logger.error
            elif status_code >= 400:
                level = logger.warning
            else:
                level = logger.info
        else:
            level = logger.info

        level(format, *args, extra=extra)

    def get_environ(self):
        # Strip all headers with underscores in the name before constructing
        # the WSGI environ. This prevents header-spoofing based on ambiguity
        # between underscores and dashes both normalized to underscores in WSGI
        # env vars. Nginx and Apache 2.4+ both do this as well.
        for k in self.headers:
            if '_' in k:
                del self.headers[k]

        # 父类的方法定义在 wsgiref.simple_server.WSGIRequestHandler 类中
        return super().get_environ()

    # 此方法在父类 socketserver.BaseRequestHandler 的 __init__ 方法中被调用
    # 服务器套接字收到连接请求，创建一个当前类的实例，叫做「请求处理对象」
    # 实例初始化过程中，将连接的临时套接字对象赋值给实例的 connect 属性，然后调用此方法
    def handle(self):
        import threading
        ct = threading.current_thread()
        print(f'【django.core.servers.basehttp.WSGIRequestHandler.handle】「请求处理对象」开始处理请求，当前为子线程: {ct.name} {ct.ident}')

        # self 是「请求处理对象」
        # HTTP 1.0 为短连接，连接后收发一次数据后自动断开
        # HTTP 1.1 及其以后的版本支持长连接，一次连接可以收发多次数据
        # 下面的属性用于决定连接是否持续
        # 该属性值在 http.server.BaseHTTPRequestHandler.parse_request 方法中
        # 根据请求的 HTTP 协议版本做出改变
        self.close_connection = True
        # 处理一次请求
        self.handle_one_request()
        print(f'【django.core.servers.basehttp.WSGIRequestHandler.handle】{ct.name} {ct.ident} 请求处理完毕，等待下一次请求...\n')

        # 如果是长连接，即客户端的版本是 HTTP 1.1 及其以上，继续处理请求
        while not self.close_connection:
            #print('【django.core.servers.basehttp.WSGIRequestHandler.handle】**************************')
            self.handle_one_request()
        try:
            self.connection.shutdown(socket.SHUT_WR)
        except (AttributeError, OSError):
            pass

    def handle_one_request(self):
        """读取并解析浏览器发送的数据
        """

        # self 是「请求处理对象」
        # 读取一行数据的前 2 ** 8 + 1 个字符，这个数据就是浏览器发送给服务器的数据
        self.raw_requestline = self.rfile.readline(65537)
        if self.raw_requestline:
            print(('【django.core.servers.basehttp.WSGIRequestHandler.handle_one_request】'
                    '请求信息的第一行: {}'.format(self.raw_requestline)))
        # 如果一行的长度超过这个数，就判定它超出了服务器允许的长度范围，返回 414 状态码
        if len(self.raw_requestline) > 65536:
            print('【django.core.servers.basehttp.WSGIRequestHandler.handle_one_request】414')
            self.handle_one_request()
            self.requestline = ''
            self.request_version = ''
            self.command = ''
            self.send_error(414)
            return

        # 下面这个方法在 http.server.BaseHTTPRequestHandler 类里面
        # 解析请求数据的第一行，获取请求方法、路径、协议版本号并赋值给对应的属性
        # 将请求头信息解析成 http.client.HTTPMessage 类的实例，这是一个类字典对象
        # 并将此实例赋值给 self.headers 属性
        if not self.parse_request():  # An error code has been sent, just exit
            return

        # 此类定义在当前模块中，其父类是 wsgiref.simple_server.ServerHandler
        # 后者的父类是 wsgiref.handlers.SimpleHandler（初始化就在此类中） 
        # 后者的父类是 wsgiref.handlers.BaseHandler 
        # 其实例是创建响应对象并作进一步处理的对象，我们称之为「响应处理对象」
        handler = ServerHandler(
            # 参数说明：
            # 1、读取客户端发来的数据的「rfile 流对象」
            # 2、写入返回给客户端的数据的「wfile 流对象」
            # 3、协议相关的错误信息
            # 4、self.get_environ 方法处理请求头中的无效字段
            #    然后调用父类 wsgiref.simple_server.WSGIRequestHandler 的同名方法
            #    这个同名方法会返回一个字典对象，里面是各种请求信息
            self.rfile, self.wfile, self.get_stderr(), self.get_environ()
        )

        # self 是「请求处理对象」，下面的 handler 是「响应处理对象」
        # self.request         临时套接字
        # self.client_address  客户端地址元组
        # self.server          服务器对象
        # 将 self 赋值给「响应处理对象」的 request_handler 属性
        handler.request_handler = self

        # 调用「响应处理对象」的 run 方法，此方法定义在 wsgiref.handlers.BaseHandler 类中
        # self.server 是「服务器对象」，其 get_app 方法定义在 wsgiref.simple_server.WSGIServer 类中
        # 其返回值是服务器对象的 application 属性值，也就是当前模块倒数第二行代码里的 wsgi_handler
        # 所以下面 run 方法的参数就是「应用对象」，即 django.core.handlers.wsgi.WSGIHandler 类的实例
        # 之前的操作是处理请求，下面这步操作就是处理响应以及返回数据给客户端
        handler.run(self.server.get_app())


# 启动项目时，这个方法是核心
# 参数 server_cls 的值是当前模块中定义的 WSGIServer 类
# 后者是 Python 内置模块 wsgiref.simple_server 中的 WSGIServer 类的子类
# 后者是 Python 内置模块 http.server 中的 HTTPServer 类的子类
# 后者是 Python 内置模块 socketserver 中的 TCPServer 类的子类
# 后者是 Python 内置模块 socketserver 中的 BaseServer 类的子类
def run(addr, port, wsgi_handler, ipv6=False, threading=False, server_cls=WSGIServer):
    import threading
    ct = threading.current_thread()
    print('【django.core.servers.basehttp.run】「服务器对象」初始化，当前线程:', ct.name, ct.ident)
    server_address = (addr, port)
    # 通常 threading 的值是 True ，这里调用 type 函数创建一个类
    if threading:
        # 第一个参数是新建类的名字，第二个参数是新建类要继承的父类
        # 新建类 httpd_cls 就是「服务器类」，该类的实例就是「服务器对象」，实例的 socket 属性值就是套接字对象
        httpd_cls = type('WSGIServer', (socketserver.ThreadingMixIn, server_cls), {})
    else:
        httpd_cls = server_cls

    # 对「服务器类」进行实例化得到「服务器对象」，其 socket 属性值就是 TCP 套接字对象
    # 当前函数最后一行代码将启动套接字的持续监听
    # WSGIRequestHandler 是请求处理类，socketserver.BaseRequestHandler 类的子类，其实例是「请求处理对象」
    httpd = httpd_cls(server_address, WSGIRequestHandler, ipv6=ipv6)

    if threading:
        # 原注释翻译：
        # ThreadingMixIn.daemon_threads 指示线程在突然关闭时的行为
        # 例如由用户退出服务器或由自动重新加载器重新启动时
        # True 表示服务器在退出之前不会等待线程终止
        # 这将使自动重新加载器更快，并且可以防止在线程未正确终止的情况下手动杀死服务器。
        httpd.daemon_threads = True
    # 参数 wsgi_handler 是 django.core.handlers.wsgi.WSGIHandler 类的实例
    # 该实例就相当于 Flask 中的 app 应用对象，它会被赋值给「服务器对象」的 application 属性
    # 当浏览器发送请求过来，服务器在处理请求的过程中会根据自身的 application 属性找到应用对象并调用之
    httpd.set_app(wsgi_handler)
    print('【django.core.servers.basehttp.run】等待客户端发送请求...\n')
    # 启动套接字服务器的持续监听，此方法定义在 socketserver.BaseServer 类中
    httpd.serve_forever()
