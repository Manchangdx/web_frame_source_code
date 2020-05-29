# -*- coding: utf-8 -*-
"""
    flask.sessions
    ~~~~~~~~~~~~~~

    Implements cookie based sessions based on itsdangerous.

    :copyright: 2010 Pallets
    :license: BSD-3-Clause
"""
import hashlib
import warnings
from datetime import datetime

from itsdangerous import BadSignature
from itsdangerous import URLSafeTimedSerializer
from werkzeug.datastructures import CallbackDict

from ._compat import collections_abc
from .helpers import is_ip
from .helpers import total_seconds
from .json.tag import TaggedJSONSerializer


class SessionMixin(collections_abc.MutableMapping):
    """Expands a basic dictionary with session attributes."""

    @property
    def permanent(self):
        """This reflects the ``'_permanent'`` key in the dict."""
        return self.get("_permanent", False)

    @permanent.setter
    def permanent(self, value):
        self["_permanent"] = bool(value)

    #: Some implementations can detect whether a session is newly
    #: created, but that is not guaranteed. Use with caution. The mixin
    # default is hard-coded ``False``.
    new = False

    #: Some implementations can detect changes to the session and set
    #: this when that happens. The mixin default is hard coded to
    #: ``True``.
    modified = True

    #: Some implementations can detect when session data is read or
    #: written and set this when that happens. The mixin default is hard
    #: coded to ``True``.
    accessed = True


class SecureCookieSession(CallbackDict, SessionMixin):
    """Base class for sessions based on signed cookies.

    This session backend will set the :attr:`modified` and
    :attr:`accessed` attributes. It cannot reliably track whether a
    session is new (vs. empty), so :attr:`new` remains hard coded to
    ``False``.
    """

    #: When data is changed, this is set to ``True``. Only the session
    #: dictionary itself is tracked; if the session contains mutable
    #: data (for example a nested dict) then this must be set to
    #: ``True`` manually when modifying that data. The session cookie
    #: will only be written to the response if this is ``True``.
    modified = False

    #: When data is read or written, this is set to ``True``. Used by
    # :class:`.SecureCookieSessionInterface` to add a ``Vary: Cookie``
    #: header, which allows caching proxies to cache different pages for
    #: different users.
    accessed = False

    def __init__(self, initial=None):
        def on_update(self):
            self.modified = True
            self.accessed = True

        super().__init__(initial, on_update)

    def __getitem__(self, key):
        self.accessed = True
        return super(SecureCookieSession, self).__getitem__(key)

    def get(self, key, default=None):
        self.accessed = True
        return super(SecureCookieSession, self).get(key, default)

    def setdefault(self, key, default=None):
        self.accessed = True
        return super(SecureCookieSession, self).setdefault(key, default)


class NullSession(SecureCookieSession):
    """Class used to generate nicer error messages if sessions are not
    available.  Will still allow read-only access to the empty session
    but fail on setting.
    """

    def _fail(self, *args, **kwargs):
        raise RuntimeError(
            "The session is unavailable because no secret "
            "key was set.  Set the secret_key on the "
            "application to something unique and secret."
        )

    __setitem__ = __delitem__ = clear = pop = popitem = update = setdefault = _fail
    del _fail


class SessionInterface(object):
    """
    """

    #: :meth:`make_null_session` will look here for the class that should
    #: be created when a null session is requested.  Likewise the
    #: :meth:`is_null_session` method will perform a typecheck against
    #: this type.
    null_session_class = NullSession

    #: A flag that indicates if the session interface is pickle based.
    #: This can be used by Flask extensions to make a decision in regards
    #: to how to deal with the session object.
    #:
    #: .. versionadded:: 0.10
    pickle_based = False

    def make_null_session(self, app):
        """Creates a null session which acts as a replacement object if the
        real session support could not be loaded due to a configuration
        error.  This mainly aids the user experience because the job of the
        null session is to still support lookup without complaining but
        modifications are answered with a helpful error message of what
        failed.

        This creates an instance of :attr:`null_session_class` by default.
        """
        return self.null_session_class()

    def is_null_session(self, obj):
        """Checks if a given object is a null session.  Null sessions are
        not asked to be saved.

        This checks if the object is an instance of :attr:`null_session_class`
        by default.
        """
        return isinstance(obj, self.null_session_class)

    def get_cookie_domain(self, app):
        """Returns the domain that should be set for the session cookie.

        Uses ``SESSION_COOKIE_DOMAIN`` if it is configured, otherwise
        falls back to detecting the domain based on ``SERVER_NAME``.

        Once detected (or if not set at all), ``SESSION_COOKIE_DOMAIN`` is
        updated to avoid re-running the logic.
        """

        rv = app.config["SESSION_COOKIE_DOMAIN"]

        # set explicitly, or cached from SERVER_NAME detection
        # if False, return None
        if rv is not None:
            return rv if rv else None

        rv = app.config["SERVER_NAME"]

        # server name not set, cache False to return none next time
        if not rv:
            app.config["SESSION_COOKIE_DOMAIN"] = False
            return None

        # chop off the port which is usually not supported by browsers
        # remove any leading '.' since we'll add that later
        rv = rv.rsplit(":", 1)[0].lstrip(".")

        if "." not in rv:
            # Chrome doesn't allow names without a '.'
            # this should only come up with localhost
            # hack around this by not setting the name, and show a warning
            warnings.warn(
                '"{rv}" is not a valid cookie domain, it must contain a ".".'
                " Add an entry to your hosts file, for example"
                ' "{rv}.localdomain", and use that instead.'.format(rv=rv)
            )
            app.config["SESSION_COOKIE_DOMAIN"] = False
            return None

        ip = is_ip(rv)

        if ip:
            warnings.warn(
                "The session cookie domain is an IP address. This may not work"
                " as intended in some browsers. Add an entry to your hosts"
                ' file, for example "localhost.localdomain", and use that'
                " instead."
            )

        # if this is not an ip and app is mounted at the root, allow subdomain
        # matching by adding a '.' prefix
        if self.get_cookie_path(app) == "/" and not ip:
            rv = "." + rv

        app.config["SESSION_COOKIE_DOMAIN"] = rv
        return rv

    def get_cookie_path(self, app):
        """Returns the path for which the cookie should be valid.  The
        default implementation uses the value from the ``SESSION_COOKIE_PATH``
        config var if it's set, and falls back to ``APPLICATION_ROOT`` or
        uses ``/`` if it's ``None``.
        """
        return app.config["SESSION_COOKIE_PATH"] or app.config["APPLICATION_ROOT"]

    def get_cookie_httponly(self, app):
        """Returns True if the session cookie should be httponly.  This
        currently just returns the value of the ``SESSION_COOKIE_HTTPONLY``
        config var.
        """
        return app.config["SESSION_COOKIE_HTTPONLY"]

    def get_cookie_secure(self, app):
        """Returns True if the cookie should be secure.  This currently
        just returns the value of the ``SESSION_COOKIE_SECURE`` setting.
        """
        return app.config["SESSION_COOKIE_SECURE"]

    def get_cookie_samesite(self, app):
        """Return ``'Strict'`` or ``'Lax'`` if the cookie should use the
        ``SameSite`` attribute. This currently just returns the value of
        the :data:`SESSION_COOKIE_SAMESITE` setting.
        """
        return app.config["SESSION_COOKIE_SAMESITE"]

    def get_expiration_time(self, app, session):
        """A helper method that returns an expiration date for the session
        or ``None`` if the session is linked to the browser session.  The
        default implementation returns now + the permanent session
        lifetime configured on the application.
        """
        if session.permanent:
            return datetime.utcnow() + app.permanent_session_lifetime

    def should_set_cookie(self, app, session):
        """Used by session backends to determine if a ``Set-Cookie`` header
        should be set for this session cookie for this response. If the session
        has been modified, the cookie is set. If the session is permanent and
        the ``SESSION_REFRESH_EACH_REQUEST`` config is true, the cookie is
        always set.

        This check is usually skipped if the session was deleted.

        .. versionadded:: 0.11
        """

        return session.modified or (
            session.permanent and app.config["SESSION_REFRESH_EACH_REQUEST"]
        )

    def open_session(self, app, request):
        """This method has to be implemented and must either return ``None``
        in case the loading failed because of a configuration error or an
        instance of a session object which implements a dictionary like
        interface + the methods and attributes on :class:`SessionMixin`.
        """
        raise NotImplementedError()

    def save_session(self, app, session, response):
        """This is called for actual sessions returned by :meth:`open_session`
        at the end of the request.  This is still called during a request
        context so if you absolutely need access to the request you can do
        that.
        """
        raise NotImplementedError()


session_json_serializer = TaggedJSONSerializer()


class SecureCookieSessionInterface(SessionInterface):
    """The default session interface that stores sessions in signed cookies
    through the :mod:`itsdangerous` module.
    """

    #: the salt that should be applied on top of the secret key for the
    #: signing of cookie based sessions.
    salt = "cookie-session"
    #: the hash function to use for the signature.  The default is sha1
    digest_method = staticmethod(hashlib.sha1)
    #: the name of the itsdangerous supported key derivation.  The default
    #: is hmac.
    key_derivation = "hmac"
    #: A python serializer for the payload.  The default is a compact
    #: JSON derived serializer with support for some extra Python types
    #: such as datetime objects or tuples.
    serializer = session_json_serializer
    session_class = SecureCookieSession

    def get_signing_serializer(self, app):
        # 此方法返回令牌生成器，即 URLSafeTimedSerializer 的实例
        # 生成该实例必须提供 secret_key 字符串，参数 salt 可以不提供
        if not app.secret_key:
            return None
        signer_kwargs = dict(
            key_derivation=self.key_derivation, digest_method=self.digest_method
        )
        return URLSafeTimedSerializer(
            app.secret_key,
            salt=self.salt,
            serializer=self.serializer,
            signer_kwargs=signer_kwargs,
        )

    def open_session(self, app, request):
        '''
        每次服务器收到请求，这个方法都会被请求上下文对象的 push 方法调用
        并将调用结果赋值给请求上下文对象的 session 属性
        '''
        # 变量 s 是令牌生成器
        s = self.get_signing_serializer(app)
        if s is None:
            return None
        # 当前方法的参数 request 是 werkzeug.wrappers.request.Request 的实例
        # request.cookies 的值来自浏览器请求
        # 如果浏览器首次访问该网站，自然是没有 cookies 的
        # 非首次访问的话，请求信息中会包含 HTTP_COOKIE 字段
        # request.cookies 的值就是从这个字段解析获得的数据生成的字典
        # app.session_cookie_name 的值是字符串 'session'
        # 具体参见 flask.app.Flask 类中的注释
        # val 的值就是 HTTP_COOKIE 字段的值中的 'session' 部分
        # 这个值其实就是令牌，也叫做加密签名
        val = request.cookies.get(app.session_cookie_name)
        print('【flask.sessions.SecureCookieSessionInterface.open_session】HTTP_COOKIE 中 session 字段的值:')
        print(val)
        if not val:
            return self.session_class()
        # 这个变量是 31 天换算成秒得到的数值
        max_age = total_seconds(app.permanent_session_lifetime)
        try:
            # 令牌生成器 s 的 loads 方法返回一个字典对象
            # 调用 loads 方法需要提供令牌和有效时间参数
            data = s.loads(val, max_age=max_age)
            print('【flask.sessions.SecureCookieSessionInterface.open_session】令牌生成器根据请求的 HTTP_COOKIE 字段解析得到的字典:')
            print(data)
            return self.session_class(data)
        except BadSignature:
            return self.session_class()

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)

        # If the session is modified to be empty, remove the cookie.
        # If the session is empty, return without setting the cookie.
        if not session:
            if session.modified:
                response.delete_cookie(
                    app.session_cookie_name, domain=domain, path=path
                )

            return

        # Add a "Vary: Cookie" header if the session was accessed at all.
        if session.accessed:
            response.vary.add("Cookie")

        if not self.should_set_cookie(app, session):
            return

        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        samesite = self.get_cookie_samesite(app)
        expires = self.get_expiration_time(app, session)
        print('【flask.sessions.SecureCookieSessionInterface().save_session】dict(session)', dict(session))
        val = self.get_signing_serializer(app).dumps(dict(session))
        response.set_cookie(
            app.session_cookie_name,
            val,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            samesite=samesite,
        )
