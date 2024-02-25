from __future__ import absolute_import
import errno
import logging
import sys
import warnings

from socket import error as SocketError, timeout as SocketTimeout
import socket


from .exceptions import (
    ClosedPoolError,
    ProtocolError,
    EmptyPoolError,
    HeaderParsingError,
    HostChangedError,
    LocationValueError,
    MaxRetryError,
    ProxyError,
    ReadTimeoutError,
    SSLError,
    TimeoutError,
    InsecureRequestWarning,
    NewConnectionError,
)
from .packages.ssl_match_hostname import CertificateError
from .packages import six
from .packages.six.moves import queue
from .connection import (
    port_by_scheme,
    DummyConnection,
    HTTPConnection,
    HTTPSConnection,
    VerifiedHTTPSConnection,
    HTTPException,
    BaseSSLError,
)
from .request import RequestMethods
from .response import HTTPResponse

from .util.connection import is_connection_dropped
from .util.request import set_file_position
from .util.response import assert_header_parsing
from .util.retry import Retry
from .util.timeout import Timeout
from .util.url import (
    get_host,
    parse_url,
    Url,
    _normalize_host as normalize_host,
    _encode_target,
)
from .util.queue import LifoQueue


xrange = six.moves.xrange

logger = logging.getLogger(__name__)

_Default = object()


# Pool objects
class ConnectionPool(object):
    """
    Base class for all connection pools, such as
    :class:`.HTTPConnectionPool` and :class:`.HTTPSConnectionPool`.

    .. note::
       ConnectionPool.urlopen() does not normalize or percent-encode target URIs
       which is useful if your target server doesn't support percent-encoded
       target URIs.
    """

    scheme = None
    QueueCls = LifoQueue

    def __init__(self, host, port=None):
        if not host:
            raise LocationValueError("No host specified.")

        self.host = _normalize_host(host, scheme=self.scheme)
        self._proxy_host = host.lower()
        self.port = port

    def __str__(self):
        return "%s(host=%r, port=%r)" % (type(self).__name__, self.host, self.port)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        # Return False to re-raise any potential exceptions
        return False

    def close(self):
        """
        Close all pooled connections and disable the pool.
        """
        pass


# This is taken from http://hg.python.org/cpython/file/7aaba721ebc0/Lib/socket.py#l252
_blocking_errnos = {errno.EAGAIN, errno.EWOULDBLOCK}


class HTTPConnectionPool(ConnectionPool, RequestMethods):
    """
    Thread-safe connection pool for one host.

    :param host:
        Host used for this HTTP Connection (e.g. "localhost"), passed into
        :class:`httplib.HTTPConnection`.

    :param port:
        Port used for this HTTP Connection (None is equivalent to 80), passed
        into :class:`httplib.HTTPConnection`.

    :param strict:
        Causes BadStatusLine to be raised if the status line can't be parsed
        as a valid HTTP/1.0 or 1.1 status line, passed into
        :class:`httplib.HTTPConnection`.

        .. note::
           Only works in Python 2. This parameter is ignored in Python 3.

    :param timeout:
        Socket timeout in seconds for each individual connection. This can
        be a float or integer, which sets the timeout for the HTTP request,
        or an instance of :class:`urllib3.util.Timeout` which gives you more
        fine-grained control over request timeouts. After the constructor has
        been parsed, this is always a `urllib3.util.Timeout` object.

    :param maxsize:
        Number of connections to save that can be reused. More than 1 is useful
        in multithreaded situations. If ``block`` is set to False, more
        connections will be created but they will not be saved once they've
        been used.

    :param block:
        If set to True, no more than ``maxsize`` connections will be used at
        a time. When no free connections are available, the call will block
        until a connection has been released. This is a useful side effect for
        particular multithreaded situations where one does not want to use more
        than maxsize connections per host to prevent flooding.

    :param headers:
        Headers to include with all requests, unless other headers are given
        explicitly.

    :param retries:
        Retry configuration to use by default with requests in this pool.

    :param _proxy:
        Parsed proxy URL, should not be used directly, instead, see
        :class:`urllib3.connectionpool.ProxyManager`"

    :param _proxy_headers:
        A dictionary with proxy headers, should not be used directly,
        instead, see :class:`urllib3.connectionpool.ProxyManager`"

    :param \\**conn_kw:
        Additional parameters are used to create fresh :class:`urllib3.connection.HTTPConnection`,
        :class:`urllib3.connection.HTTPSConnection` instances.
    """

    scheme = "http"
    ConnectionCls = HTTPConnection
    ResponseCls = HTTPResponse

    def __init__(
        self,
        host,
        port=None,
        strict=False,
        timeout=Timeout.DEFAULT_TIMEOUT,
        maxsize=1,
        block=False,
        headers=None,
        retries=None,
        _proxy=None,
        _proxy_headers=None,
        **conn_kw
    ):
        logger.info(f'[urllib3.connectionpool.HTTPConnectionPoll.__init__] 初始化连接池 {host}:{port}  代理: {_proxy}')
        ConnectionPool.__init__(self, host, port)
        RequestMethods.__init__(self, headers)

        self.strict = strict

        if not isinstance(timeout, Timeout):
            timeout = Timeout.from_float(timeout)

        if retries is None:
            retries = Retry.DEFAULT

        self.timeout = timeout
        self.retries = retries

        self.pool = self.QueueCls(maxsize)
        self.block = block

        self.proxy = _proxy
        self.proxy_headers = _proxy_headers or {}

        # Fill the queue up so that doing get() on it will block properly
        for _ in xrange(maxsize):
            self.pool.put(None)

        # These are mostly for testing and debugging purposes.
        self.num_connections = 0
        self.num_requests = 0
        self.conn_kw = conn_kw

        if self.proxy:
            # Enable Nagle's algorithm for proxies, to avoid packet fragmentation.
            # We cannot know if the user has added default socket options, so we cannot replace the
            # list.
            self.conn_kw.setdefault("socket_options", [])

    def _new_conn(self):
        """
        Return a fresh :class:`HTTPConnection`.
        """
        self.num_connections += 1
        logger.info(
            "[urllib3.connectionpool.HTTPConnectionPoll._new_conn] Starting new HTTP connection (%d): %s:%s",
            self.num_connections,
            self.host,
            self.port or "80",
        )

        conn = self.ConnectionCls(
            host=self.host,
            port=self.port,
            timeout=self.timeout.connect_timeout,
            strict=self.strict,
            **self.conn_kw
        )
        return conn

    def _get_conn(self, timeout=None):
        """
        Get a connection. Will return a pooled connection if one is available.

        If no connections are available and :prop:`.block` is ``False``, then a
        fresh connection is returned.

        :param timeout:
            Seconds to wait before giving up and raising
            :class:`urllib3.exceptions.EmptyPoolError` if the pool is empty and
            :prop:`.block` is ``True``.
        """
        conn = None
        try:
            conn = self.pool.get(block=self.block, timeout=timeout)

        except AttributeError:  # self.pool is None
            raise ClosedPoolError(self, "Pool is closed.")

        except queue.Empty:
            if self.block:
                raise EmptyPoolError(
                    self,
                    "Pool reached maximum size and no more connections are allowed.",
                )
            pass  # Oh well, we'll create a new connection then

        # If this is a persistent connection, check if it got disconnected
        if conn and is_connection_dropped(conn):
            logger.debug("Resetting dropped connection: %s", self.host)
            conn.close()
            if getattr(conn, "auto_open", 1) == 0:
                # This is a proxied connection that has been mutated by
                # httplib._tunnel() and cannot be reused (since it would
                # attempt to bypass the proxy)
                conn = None

        return conn or self._new_conn()

    def _put_conn(self, conn):
        """
        Put a connection back into the pool.

        :param conn:
            Connection object for the current host and port as returned by
            :meth:`._new_conn` or :meth:`._get_conn`.

        If the pool is already full, the connection is closed and discarded
        because we exceeded maxsize. If connections are discarded frequently,
        then maxsize should be increased.

        If the pool is closed, then the connection will be closed and discarded.
        """
        try:
            self.pool.put(conn, block=False)
            return  # Everything is dandy, done.
        except AttributeError:
            # self.pool is None.
            pass
        except queue.Full:
            # This should never happen if self.block == True
            logger.warning("Connection pool is full, discarding connection: %s", self.host)

        # Connection never got put back into the pool, close it.
        if conn:
            conn.close()

    def _validate_conn(self, conn):
        """
        Called right before a request is made, after the socket is created.
        """
        pass

    def _prepare_proxy(self, conn):
        # Nothing to do for HTTP connections.
        pass

    def _get_timeout(self, timeout):
        """ Helper that always returns a :class:`urllib3.util.Timeout` """
        if timeout is _Default:
            return self.timeout.clone()

        if isinstance(timeout, Timeout):
            return timeout.clone()
        else:
            # User passed us an int/float. This is for backwards compatibility,
            # can be removed later
            return Timeout.from_float(timeout)

    def _raise_timeout(self, err, url, timeout_value):
        """Is the error actually a timeout? Will raise a ReadTimeout or pass"""

        if isinstance(err, SocketTimeout):
            raise ReadTimeoutError(
                self, url, "Read timed out. (read timeout=%s)" % timeout_value
            )

        # See the above comment about EAGAIN in Python 3. In Python 2 we have
        # to specifically catch it and throw the timeout error
        if hasattr(err, "errno") and err.errno in _blocking_errnos:
            raise ReadTimeoutError(
                self, url, "Read timed out. (read timeout=%s)" % timeout_value
            )

        # Catch possible read timeouts thrown as SSL errors. If not the
        # case, rethrow the original. We need to do this because of:
        # http://bugs.python.org/issue10272
        if "timed out" in str(err) or "did not complete (read)" in str(
            err
        ):  # Python < 2.7.4
            raise ReadTimeoutError(
                self, url, "Read timed out. (read timeout=%s)" % timeout_value
            )

    def _make_request(
        self, conn, method, url, timeout=_Default, chunked=False, **httplib_request_kw
    ):
        """发送网络请求，获取响应并返回
        """
        self.num_requests += 1

        timeout_obj = self._get_timeout(timeout)
        timeout_obj.start_connect()
        conn.timeout = timeout_obj.connect_timeout

        try:
            self._validate_conn(conn)
        except (SocketTimeout, BaseSSLError) as e:
            self._raise_timeout(err=e, url=url, timeout_value=conn.timeout)
            raise

        # 发送网络请求
        if chunked:
            conn.request_chunked(method, url, **httplib_request_kw)
        else:
            logger.info(
                '[urllib3.connectionpool.HTTPConnectionPoll._make_request] 连接池调用连接对象的 request 方法发送网络请求'
            )
            # self 是连接池
            # conn 是连接对象，urllib3.connection.HTTPConnection 类的实例
            # conn.request 方法定义在 http.client.HTTPConnection 类中
            conn.request(method, url, **httplib_request_kw)

        read_timeout = timeout_obj.read_timeout

        if getattr(conn, "sock", None):
            if read_timeout == 0:
                raise ReadTimeoutError(
                    self, url, "Read timed out. (read timeout=%s)" % read_timeout
                )
            if read_timeout is Timeout.DEFAULT_TIMEOUT:
                conn.sock.settimeout(socket.getdefaulttimeout())
            else:
                conn.sock.settimeout(read_timeout)

        # 获取服务器响应
        try:
            try:
                # Python 2.7
                httplib_response = conn.getresponse(buffering=True)
            except TypeError:
                # Python 3
                try:
                    # 此 getresponse 方法定义在 http.client.HTTPConnection 类中
                    httplib_response = conn.getresponse()
                except BaseException as e:
                    six.raise_from(e, None)
        except (SocketTimeout, BaseSSLError, SocketError) as e:
            self._raise_timeout(err=e, url=url, timeout_value=read_timeout)
            raise

        http_version = getattr(conn, "_http_vsn_str", "HTTP/?")
        logger.info(
            '[urllib3.connectionpool.HTTPConnectionPoll._make_request] 获取服务器响应: %s://%s:%s "%s %s %s" %s %s',
            self.scheme,
            self.host,
            self.port,
            method,
            url,
            http_version,
            httplib_response.status,
            httplib_response.length,
        )

        try:
            assert_header_parsing(httplib_response.msg)
        except (HeaderParsingError, TypeError) as hpe:  # Platform-specific: Python 3
            logger.warning(
                "Failed to parse headers (url=%s): %s",
                self._absolute_url(url),
                hpe,
                exc_info=True,
            )

        return httplib_response

    def _absolute_url(self, path):
        return Url(scheme=self.scheme, host=self.host, port=self.port, path=path).url

    def close(self):
        """
        Close all pooled connections and disable the pool.
        """
        if self.pool is None:
            return
        # Disable access to the pool
        old_pool, self.pool = self.pool, None

        try:
            while True:
                conn = old_pool.get(block=False)
                if conn:
                    conn.close()

        except queue.Empty:
            pass  # Done.

    def is_same_host(self, url):
        """
        Check if the given ``url`` is a member of the same host as this
        connection pool.
        """
        if url.startswith("/"):
            return True

        # TODO: Add optional support for socket.gethostbyname checking.
        scheme, host, port = get_host(url)
        if host is not None:
            host = _normalize_host(host, scheme=scheme)

        # Use explicit default port for comparison when none is given
        if self.port and not port:
            port = port_by_scheme.get(scheme)
        elif not self.port and port == port_by_scheme.get(scheme):
            port = None

        return (scheme, host, port) == (self.scheme, self.host, self.port)

    def urlopen(
        self,
        method,
        url,
        body=None,
        headers=None,
        retries=None,
        redirect=True,
        assert_same_host=True,
        timeout=_Default,
        pool_timeout=None,
        release_conn=None,
        chunked=False,
        body_pos=None,
        **response_kw
    ):
        """从连接池中获取连接对象，发送网络请求，获取响应并返回
        """
        if headers is None:
            headers = self.headers

        if not isinstance(retries, Retry):
            retries = Retry.from_int(retries, redirect=redirect, default=self.retries)

        if release_conn is None:
            release_conn = response_kw.get("preload_content", True)

        if assert_same_host and not self.is_same_host(url):
            raise HostChangedError(self, url, retries)

        if url.startswith("/"):
            url = six.ensure_str(_encode_target(url))
        else:
            url = six.ensure_str(parse_url(url).url)

        conn = None

        release_this_conn = release_conn

        if self.scheme == "http":
            headers = headers.copy()
            headers.update(self.proxy_headers)

        err = None

        clean_exit = False

        body_pos = set_file_position(body, body_pos)

        try:
            timeout_obj = self._get_timeout(timeout)

            host = self.host
            port = self.port
            logger.info(
                f'[urllib3.connectionpool.HTTPConnectionPoll.urlopen] 连接池创建连接对象，真实服务器的地址: {host}:{port}'
            )

            # self 是连接池
            # conn 是连接对象，urllib3.connection.HTTPSConnection 类的实例
            #      其核心就是根据目标 IP & PORT 创建一个客户端套接字
            #      如果有代理，客户端套接字的目标 IP & PORT 就是代理的
            conn = self._get_conn(timeout=pool_timeout)

            conn.timeout = timeout_obj.connect_timeout

            is_new_proxy_conn = self.proxy is not None and not getattr(conn, "sock", None)
            if is_new_proxy_conn:
                logger.info(
                    f'[urllib3.connectionpool.HTTPConnectionPoll.urlopen] 将连接对象设为代理连接，'
                    f'代理服务器的地址: {self.proxy.host}:{self.proxy.port}'
                )
                # 此处的作用是给 conn 设置 _tunnel_host 和 _tunnel_port 属性
                # conn.host 和 conn.port 是代理本地服务器的域名和端口
                # conn._tunnel_host 和 conn._tunnel_port 是真实目标服务器的域名和端口
                self._prepare_proxy(conn)

            # 发送网络请求
            httplib_response = self._make_request(
                conn,
                method,
                url,
                timeout=timeout_obj,
                body=body,
                headers=headers,
                chunked=chunked,
            )

            response_conn = conn if not release_conn else None

            # Pass method to Response for length checking
            response_kw["request_method"] = method

            # Import httplib's response into our own wrapper object
            response = self.ResponseCls.from_httplib(
                httplib_response,
                pool=self,
                connection=response_conn,
                retries=retries,
                **response_kw
            )

            logger.info(f'[urllib3.connectionpool.HTTPConnectionPoll.urlopen] 返回响应对象 {response}')

            # Everything went great!
            clean_exit = True

        except EmptyPoolError:
            # Didn't get a connection from the pool, no need to clean up
            clean_exit = True
            release_this_conn = False
            raise

        except (
            TimeoutError,
            HTTPException,
            SocketError,
            ProtocolError,
            BaseSSLError,
            SSLError,
            CertificateError,
        ) as e:
            # Discard the connection for these exceptions. It will be
            # replaced during the next _get_conn() call.
            clean_exit = False
            if isinstance(e, (BaseSSLError, CertificateError)):
                e = SSLError(e)
            elif isinstance(e, (SocketError, NewConnectionError)) and self.proxy:
                e = ProxyError("Cannot connect to proxy.", e)
            elif isinstance(e, (SocketError, HTTPException)):
                e = ProtocolError("Connection aborted.", e)

            retries = retries.increment(
                method, url, error=e, _pool=self, _stacktrace=sys.exc_info()[2]
            )
            retries.sleep()

            # Keep track of the error for the retry warning.
            err = e

        finally:
            if not clean_exit:
                # We hit some kind of exception, handled or otherwise. We need
                # to throw the connection away unless explicitly told not to.
                # Close the connection, set the variable to None, and make sure
                # we put the None back in the pool to avoid leaking it.
                conn = conn and conn.close()
                release_this_conn = True

            if release_this_conn:
                # Put the connection back to be reused. If the connection is
                # expired then it will be None, which will get replaced with a
                # fresh connection during _get_conn.
                self._put_conn(conn)

        if not conn:
            # Try again
            logger.warning(
                "Retrying (%r) after connection broken by '%r': %s", retries, err, url
            )
            return self.urlopen(
                method,
                url,
                body,
                headers,
                retries,
                redirect,
                assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                **response_kw
            )

        # Handle redirect?
        redirect_location = redirect and response.get_redirect_location()
        if redirect_location:
            if response.status == 303:
                method = "GET"

            try:
                retries = retries.increment(method, url, response=response, _pool=self)
            except MaxRetryError:
                if retries.raise_on_redirect:
                    response.drain_conn()
                    raise
                return response

            response.drain_conn()
            retries.sleep_for_retry(response)
            logger.debug("Redirecting %s -> %s", url, redirect_location)
            return self.urlopen(
                method,
                redirect_location,
                body,
                headers,
                retries=retries,
                redirect=redirect,
                assert_same_host=assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                **response_kw
            )

        # Check if we should retry the HTTP response.
        has_retry_after = bool(response.getheader("Retry-After"))
        if retries.is_retry(method, response.status, has_retry_after):
            try:
                retries = retries.increment(method, url, response=response, _pool=self)
            except MaxRetryError:
                if retries.raise_on_status:
                    response.drain_conn()
                    raise
                return response

            response.drain_conn()
            retries.sleep(response)
            logger.debug("Retry: %s", url)
            return self.urlopen(
                method,
                url,
                body,
                headers,
                retries=retries,
                redirect=redirect,
                assert_same_host=assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                **response_kw
            )

        return response


class HTTPSConnectionPool(HTTPConnectionPool):
    """
    Same as :class:`.HTTPConnectionPool`, but HTTPS.

    When Python is compiled with the :mod:`ssl` module, then
    :class:`.VerifiedHTTPSConnection` is used, which *can* verify certificates,
    instead of :class:`.HTTPSConnection`.

    :class:`.VerifiedHTTPSConnection` uses one of ``assert_fingerprint``,
    ``assert_hostname`` and ``host`` in this order to verify connections.
    If ``assert_hostname`` is False, no verification is done.

    The ``key_file``, ``cert_file``, ``cert_reqs``, ``ca_certs``,
    ``ca_cert_dir``, ``ssl_version``, ``key_password`` are only used if :mod:`ssl`
    is available and are fed into :meth:`urllib3.util.ssl_wrap_socket` to upgrade
    the connection socket into an SSL socket.
    """

    scheme = "https"
    ConnectionCls = HTTPSConnection

    def __init__(
        self,
        host,
        port=None,
        strict=False,
        timeout=Timeout.DEFAULT_TIMEOUT,
        maxsize=1,
        block=False,
        headers=None,
        retries=None,
        _proxy=None,
        _proxy_headers=None,
        key_file=None,
        cert_file=None,
        cert_reqs=None,
        key_password=None,
        ca_certs=None,
        ssl_version=None,
        assert_hostname=None,
        assert_fingerprint=None,
        ca_cert_dir=None,
        **conn_kw
    ):

        HTTPConnectionPool.__init__(
            self,
            host,
            port,
            strict,
            timeout,
            maxsize,
            block,
            headers,
            retries,
            _proxy,
            _proxy_headers,
            **conn_kw
        )

        self.key_file = key_file
        self.cert_file = cert_file
        self.cert_reqs = cert_reqs
        self.key_password = key_password
        self.ca_certs = ca_certs
        self.ca_cert_dir = ca_cert_dir
        self.ssl_version = ssl_version
        self.assert_hostname = assert_hostname
        self.assert_fingerprint = assert_fingerprint

    def _prepare_conn(self, conn):
        """
        Prepare the ``connection`` for :meth:`urllib3.util.ssl_wrap_socket`
        and establish the tunnel if proxy is used.
        """

        if isinstance(conn, VerifiedHTTPSConnection):
            conn.set_cert(
                key_file=self.key_file,
                key_password=self.key_password,
                cert_file=self.cert_file,
                cert_reqs=self.cert_reqs,
                ca_certs=self.ca_certs,
                ca_cert_dir=self.ca_cert_dir,
                assert_hostname=self.assert_hostname,
                assert_fingerprint=self.assert_fingerprint,
            )
            conn.ssl_version = self.ssl_version
        return conn

    def _prepare_proxy(self, conn):
        """为代理设置隧道连接
        """
        conn.set_tunnel(self._proxy_host, self.port, self.proxy_headers)
        conn.connect()

    def _new_conn(self):
        """
        Return a fresh :class:`httplib.HTTPSConnection`.
        """
        self.num_connections += 1
        # logger.info(
        #     "[urllib3.connectionpool.HTTPConnectionPoll._new_conn] Starting new HTTPS connection (%d): %s:%s",
        #     self.num_connections,
        #     self.host,
        #     self.port or "443",
        # )

        if not self.ConnectionCls or self.ConnectionCls is DummyConnection:
            raise SSLError(
                "Can't connect to HTTPS URL because the SSL module is not available."
            )

        actual_host = self.host
        actual_port = self.port
        if self.proxy is not None:
            actual_host = self.proxy.host
            actual_port = self.proxy.port

        conn = self.ConnectionCls(
            host=actual_host,
            port=actual_port,
            timeout=self.timeout.connect_timeout,
            strict=self.strict,
            cert_file=self.cert_file,
            key_file=self.key_file,
            key_password=self.key_password,
            **self.conn_kw
        )

        return self._prepare_conn(conn)

    def _validate_conn(self, conn):
        """
        Called right before a request is made, after the socket is created.
        """
        super(HTTPSConnectionPool, self)._validate_conn(conn)

        # Force connect early to allow us to validate the connection.
        if not getattr(conn, "sock", None):  # AppEngine might not have  `.sock`
            conn.connect()

        if not conn.is_verified:
            warnings.warn(
                (
                    "Unverified HTTPS request is being made to host '%s'. "
                    "Adding certificate verification is strongly advised. See: "
                    "https://urllib3.readthedocs.io/en/latest/advanced-usage.html"
                    "#ssl-warnings" % conn.host
                ),
                InsecureRequestWarning,
            )


def connection_from_url(url, **kw):
    """
    Given a url, return an :class:`.ConnectionPool` instance of its host.

    This is a shortcut for not having to parse out the scheme, host, and port
    of the url before creating an :class:`.ConnectionPool` instance.

    :param url:
        Absolute URL string that must include the scheme. Port is optional.

    :param \\**kw:
        Passes additional parameters to the constructor of the appropriate
        :class:`.ConnectionPool`. Useful for specifying things like
        timeout, maxsize, headers, etc.

    Example::

        >>> conn = connection_from_url('http://google.com/')
        >>> r = conn.request('GET', '/')
    """
    scheme, host, port = get_host(url)
    port = port or port_by_scheme.get(scheme, 80)
    if scheme == "https":
        return HTTPSConnectionPool(host, port=port, **kw)
    else:
        return HTTPConnectionPool(host, port=port, **kw)


def _normalize_host(host, scheme):
    """
    Normalize hosts for comparisons and use with sockets.
    """

    host = normalize_host(host, scheme)

    # httplib doesn't like it when we include brackets in IPv6 addresses
    # Specifically, if we include brackets but also pass the port then
    # httplib crazily doubles up the square brackets on the Host header.
    # Instead, we need to make sure we never pass ``None`` as the port.
    # However, for backward compatibility reasons we can't actually
    # *assert* that.  See http://bugs.python.org/issue28539
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host
