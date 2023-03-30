import re
from io import BytesIO

from django.conf import settings
from django.core import signals
from django.core.handlers import base
from django.http import HttpRequest, QueryDict, parse_cookie
from django.urls import set_script_prefix
from django.utils.encoding import repercent_broken_unicode
from django.utils.functional import cached_property

_slashes_re = re.compile(br'/+')


class LimitedStream:
    """Wrap another stream to disallow reading it past a number of bytes."""
    def __init__(self, stream, limit, buf_size=64 * 1024 * 1024):
        self.stream = stream
        self.remaining = limit
        self.buffer = b''
        self.buf_size = buf_size

    def _read_limited(self, size=None):
        if size is None or size > self.remaining:
            size = self.remaining
        if size == 0:
            return b''
        result = self.stream.read(size)
        self.remaining -= len(result)
        return result

    def read(self, size=None):
        if size is None:
            result = self.buffer + self._read_limited()
            self.buffer = b''
        elif size < len(self.buffer):
            result = self.buffer[:size]
            self.buffer = self.buffer[size:]
        else:  # size >= len(self.buffer)
            result = self.buffer + self._read_limited(size - len(self.buffer))
            self.buffer = b''
        return result

    def readline(self, size=None):
        while b'\n' not in self.buffer and \
              (size is None or len(self.buffer) < size):
            if size:
                # since size is not None here, len(self.buffer) < size
                chunk = self._read_limited(size - len(self.buffer))
            else:
                chunk = self._read_limited()
            if not chunk:
                break
            self.buffer += chunk
        sio = BytesIO(self.buffer)
        if size:
            line = sio.readline(size)
        else:
            line = sio.readline()
        self.buffer = sio.read()
        return line


# 父类定义在 django.http.request 模块中
class WSGIRequest(HttpRequest):
    # 该类的实例是「请求对象」
    def __init__(self, environ):
        script_name = get_script_name(environ)
        path_info = get_path_info(environ) or '/'
        print(f'【django.core.handlers.wsgi.WSGIRequest.__init__】初始化「请求对象」, {path_info=}')
        self.environ = environ
        # 请求的相对路径
        self.path_info = path_info
        self.path = '%s/%s' % (script_name.rstrip('/'),
                               path_info.replace('/', '', 1))
        self.META = environ
        self.META['PATH_INFO'] = path_info
        self.META['SCRIPT_NAME'] = script_name
        self.method = environ['REQUEST_METHOD'].upper()
        self._set_content_type_params(environ)
        try:
            content_length = int(environ.get('CONTENT_LENGTH'))
        except (ValueError, TypeError):
            content_length = 0
        self._stream = LimitedStream(self.environ['wsgi.input'], content_length)
        self._read_started = False
        self.resolver_match = None

    def _get_scheme(self):
        return self.environ.get('wsgi.url_scheme')

    @cached_property
    def GET(self):
        raw_query_string = get_bytes_from_wsgi(self.environ, 'QUERY_STRING', '')
        return QueryDict(raw_query_string, encoding=self._encoding)

    def _get_post(self):
        if not hasattr(self, '_post'):
            self._load_post_and_files()
        return self._post

    def _set_post(self, post):
        self._post = post

    @cached_property
    def COOKIES(self):
        raw_cookie = get_str_from_wsgi(self.environ, 'HTTP_COOKIE', '')
        return parse_cookie(raw_cookie)

    @property
    def FILES(self):
        if not hasattr(self, '_files'):
            self._load_post_and_files()
        return self._files

    # 下面这行代码的作用是给「请求对象」加个 POST 属性
    # 调用该 POST 属性时，处理请求体生成一个类字典对象赋值给 self._post 属性，其实也就是 self.POST 属性
    # 中间件 django.middleware.csrf.CsrfViewMiddleware 处理请求过程中会调用自身的 process_view 方法
    # 该方法会调用「请求对象」的 POST 属性
    POST = property(_get_post, _set_post)


class WSGIHandler(base.BaseHandler):
    """应用对象类，该类的实例就是「应用对象」，相当于 flask.app.Flask 类的实例

    注意「应用对象」在启动服务时就会创建，比创建「(套接字)服务器对象」还要早
    每次请求进入后，调用该实例的 __call__ 方法处理请求
    """

    request_class = WSGIRequest

    def __init__(self, *args, **kwargs):
        """初始化「应用对象」，此方法在启动服务时就会执行
        """
        super().__init__(*args, **kwargs)
        print(f'【django.core.handlers.wsgi.WSGIHandler.__init__】「应用对象」初始化: {args=}; {kwargs=}')
        # 填充中间件，此方法定义在 django.core.handlers.base.BaseHandler 类中
        self.load_middleware()

    def __call__(self, environ, start_response):
        """客户端发来请求后「响应处理对象」调用此方法，这是符合 WSGI 标准的 HTTP 函数

        Args:
            self           :「应用对象」，服务启动后创建的全局唯一的对象
            environ        : 类字典对象，里面包含全部请求信息
            start_response : 发送 HTTP 响应的函数

        Process:
            1. 利用 environ 创建「请求对象」
            2. 利用「请求对象」生成「响应对象」
            3. 返回「响应对象」
        """
        print(
            '【django.core.handlers.wsgi.WSGIHandler.__call__】调用「应用对象」的 __call__ 方法，'
            '这是符合 WSGI 标准的 HTTP 函数，从这里开始进入到 Web 应用处理请求的阶段'
        )
        # for k, v in environ.items():
        #    print(f'\t\t{k:<33}{v}')

        set_script_prefix(get_script_name(environ))
        signals.request_started.send(sender=self.__class__, environ=environ)

        # self.request_class 是当前模块中定义的 WSGIRequest 类
        # 此处对其进行实例化并赋值给 request 变量，我们称之为「请求对象」
        request = self.request_class(environ)

        # 此 get_response 方法定义在 django.core.handlers.base.BaseHandler 类中
        # 把「请求对象」作为参数调用此方法，返回「响应对象」
        # 后者是 django.http.response.HttpResponse 类的实例
        response = self.get_response(request)

        print(f'【django.core.handlers.wsgi.WSGIHandler.__call__】获得「响应对象」: {response}')

        response._handler_class = self.__class__

        status = '%d %s' % (response.status_code, response.reason_phrase)
        response_headers = [
            *response.items(),
            *(('Set-Cookie', c.output(header='')) for c in response.cookies.values()),
        ]
        # 给「服务处理对象」增加 status 和 headers 属性
        start_response(status, response_headers)
        if getattr(response, 'file_to_stream', None) is not None and environ.get('wsgi.file_wrapper'):
            response = environ['wsgi.file_wrapper'](response.file_to_stream, response.block_size)
        return response


def get_path_info(environ):
    """Return the HTTP request's PATH_INFO as a string."""
    path_info = get_bytes_from_wsgi(environ, 'PATH_INFO', '/')

    return repercent_broken_unicode(path_info).decode()


def get_script_name(environ):
    """
    Return the equivalent of the HTTP request's SCRIPT_NAME environment
    variable. If Apache mod_rewrite is used, return what would have been
    the script name prior to any rewriting (so it's the script name as seen
    from the client's perspective), unless the FORCE_SCRIPT_NAME setting is
    set (to anything).
    """
    if settings.FORCE_SCRIPT_NAME is not None:
        return settings.FORCE_SCRIPT_NAME

    # If Apache's mod_rewrite had a whack at the URL, Apache set either
    # SCRIPT_URL or REDIRECT_URL to the full resource URL before applying any
    # rewrites. Unfortunately not every Web server (lighttpd!) passes this
    # information through all the time, so FORCE_SCRIPT_NAME, above, is still
    # needed.
    script_url = get_bytes_from_wsgi(environ, 'SCRIPT_URL', '') or get_bytes_from_wsgi(environ, 'REDIRECT_URL', '')

    if script_url:
        if b'//' in script_url:
            # mod_wsgi squashes multiple successive slashes in PATH_INFO,
            # do the same with script_url before manipulating paths (#17133).
            script_url = _slashes_re.sub(b'/', script_url)
        path_info = get_bytes_from_wsgi(environ, 'PATH_INFO', '')
        script_name = script_url[:-len(path_info)] if path_info else script_url
    else:
        script_name = get_bytes_from_wsgi(environ, 'SCRIPT_NAME', '')

    return script_name.decode()


def get_bytes_from_wsgi(environ, key, default):
    """
    Get a value from the WSGI environ dictionary as bytes.

    key and default should be strings.
    """
    value = environ.get(key, default)
    # Non-ASCII values in the WSGI environ are arbitrarily decoded with
    # ISO-8859-1. This is wrong for Django websites where UTF-8 is the default.
    # Re-encode to recover the original bytestring.
    return value.encode('iso-8859-1')


def get_str_from_wsgi(environ, key, default):
    """
    Get a value from the WSGI environ dictionary as str.

    key and default should be str objects.
    """
    value = get_bytes_from_wsgi(environ, key, default)
    return value.decode(errors='replace')
