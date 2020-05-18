# -*- coding: utf-8 -*-
'''
    flask_login.utils
    -----------------
    General utilities.
'''


import hmac
from hashlib import sha512
from functools import wraps
from werkzeug.local import LocalProxy
from werkzeug.security import safe_str_cmp
from werkzeug.urls import url_decode, url_encode

from flask import (_request_ctx_stack, current_app, request, session, url_for,
                   has_request_context)

from ._compat import text_type, urlparse, urlunparse
from .config import COOKIE_NAME, EXEMPT_METHODS
from .signals import user_logged_in, user_logged_out, user_login_confirmed


# 在应用启动时创建该代理对象，这就是当前用户代理对象
# 收到请求后，请求所在线程创建一个请求上下文对象
# 在线程内调用 current_user 时，会运行 _get_user 方法，此方法在当前模块中创建
# 其作用是根据请求信息查询数据库获取用户对象并将其赋值给请求上下文对象的 user 属性
# 最后返回请求上下文对象的 user 属性值，也就是那个用户对象啦
# LocalProxy 类在实例化时会将这个用户对象的全部属性赋值给自身的实例
# 所以 current_user 就是这个用户对象
current_user = LocalProxy(lambda: _get_user())


def encode_cookie(payload, key=None):
    '''
    This will encode a ``unicode`` value into a cookie, and sign that cookie
    with the app's secret key.

    :param payload: The value to encode, as `unicode`.
    :type payload: unicode

    :param key: The key to use when creating the cookie digest. If not
                specified, the SECRET_KEY value from app config will be used.
    :type key: str
    '''
    return u'{0}|{1}'.format(payload, _cookie_digest(payload, key=key))


def decode_cookie(cookie, key=None):
    '''
    This decodes a cookie given by `encode_cookie`. If verification of the
    cookie fails, ``None`` will be implicitly returned.

    :param cookie: An encoded cookie.
    :type cookie: str

    :param key: The key to use when creating the cookie digest. If not
                specified, the SECRET_KEY value from app config will be used.
    :type key: str
    '''
    try:
        payload, digest = cookie.rsplit(u'|', 1)
        if hasattr(digest, 'decode'):
            digest = digest.decode('ascii')  # pragma: no cover
    except ValueError:
        return

    if safe_str_cmp(_cookie_digest(payload, key=key), digest):
        return payload


def make_next_param(login_url, current_url):
    '''
    Reduces the scheme and host from a given URL so it can be passed to
    the given `login` URL more efficiently.

    :param login_url: The login URL being redirected to.
    :type login_url: str
    :param current_url: The URL to reduce.
    :type current_url: str
    '''
    l_url = urlparse(login_url)
    c_url = urlparse(current_url)

    if (not l_url.scheme or l_url.scheme == c_url.scheme) and \
            (not l_url.netloc or l_url.netloc == c_url.netloc):
        return urlunparse(('', '', c_url.path, c_url.params, c_url.query, ''))
    return current_url


def expand_login_view(login_view):
    '''
    Returns the url for the login view, expanding the view name to a url if
    needed.

    :param login_view: The name of the login view or a URL for the login view.
    :type login_view: str
    '''
    if login_view.startswith(('https://', 'http://', '/')):
        return login_view
    else:
        return url_for(login_view)


def login_url(login_view, next_url=None, next_field='next'):
    '''
    Creates a URL for redirecting to a login page. If only `login_view` is
    provided, this will just return the URL for it. If `next_url` is provided,
    however, this will append a ``next=URL`` parameter to the query string
    so that the login view can redirect back to that URL. Flask-Login's default
    unauthorized handler uses this function when redirecting to your login url.
    To force the host name used, set `FORCE_HOST_FOR_REDIRECTS` to a host. This
    prevents from redirecting to external sites if request headers Host or
    X-Forwarded-For are present.

    :param login_view: The name of the login view. (Alternately, the actual
                       URL to the login view.)
    :type login_view: str
    :param next_url: The URL to give the login view for redirection.
    :type next_url: str
    :param next_field: What field to store the next URL in. (It defaults to
                       ``next``.)
    :type next_field: str
    '''
    base = expand_login_view(login_view)

    if next_url is None:
        return base

    parsed_result = urlparse(base)
    md = url_decode(parsed_result.query)
    md[next_field] = make_next_param(base, next_url)
    netloc = current_app.config.get('FORCE_HOST_FOR_REDIRECTS') or \
        parsed_result.netloc
    parsed_result = parsed_result._replace(netloc=netloc,
                                           query=url_encode(md, sort=True))
    return urlunparse(parsed_result)


def login_fresh():
    '''
    This returns ``True`` if the current login is fresh.
    '''
    return session.get('_fresh', False)


def login_user(user, remember=False, duration=None, force=False, fresh=True):
    '''
    '''
    if not force and not user.is_active:
        return False

    # current_app 是 werkzeug.local 模块中的 LocalProxy 类的实例，它指向当前应用
    # current_app.login_manager 是 flask_login.login_manager 模块中的 
    # LoginManager 类的实例，它的 id_attribute 属性的默认值是 'get_id'
    # 这个默认值在 flask_login.config 模块中定义在 ID_ATTRIBUTE 配置项上
    # user 是自定义映射类 User 的实例
    # User 继承了 flask_login.mixins 模块中的 UserMixin 类，该类提供了一个 get_id 方法
    # 所以等号前面的变量 user_id 的值就是 user 对象的 get_id 方法的调用
    # 该方法返回的是 user 的 id 属性值的字符串
    user_id = getattr(user, current_app.login_manager.id_attribute)()
    session['_user_id'] = user_id
    session['_fresh'] = fresh
    # 这个 _session_identifier_generator 方法
    # 是在当前模块中定义的 _create_identifier 函数
    # 它根据请求对象所提供的 IP 地址和用户代理生成 128 位 16 进制字符串
    session['_id'] = current_app.login_manager._session_identifier_generator()

    if remember:
        session['_remember'] = 'set'
        if duration is not None:
            try:
                # equal to timedelta.total_seconds() but works with Python 2.6
                session['_remember_seconds'] = (duration.microseconds +
                                                (duration.seconds +
                                                 duration.days * 24 * 3600) *
                                                10**6) / 10.0**6
            except AttributeError:
                raise Exception('duration must be a datetime.timedelta, '
                                'instead got: {0}'.format(duration))

    # current_app 是 flask.globals 模块中定义的一个应用代理对象
    # 这个对象的 login_manager 属性就是 flask_login.login_manager 模块中
    # 定义的 LoginManager 类的实例
    # 该实例的 _update_request_context_with_user 方法把 user 赋值给
    # 定义在 flask.ctx 模块中的请求上下文类 RequestContext 的实例的 user 属性
    current_app.login_manager._update_request_context_with_user(user)

    # user_logged_in 是来自 flask_login.signals 模块的变量
    # 变量值为 Namespace 类的实例调用自身的 signal 方法，调用参数为 'logged-in'
    # 这个 Namespace 类在 blinker.base 模块中定义
    # 其 signal 方法的返回值是同模块下的 NameSignal 类的实例
    # 也就是说 user_logged_in 的值是 blinker.base 模块中的 NameSignal 类的实例
    # 这个类继承自同模块下的 Signal 类，该类有一个 send 方法
    #
    # current_app 是 flask.globals 模块中定义的一个应用代理对象
    # 其 _get_current_object 方法的返回值是 Flask(__name__) 应用本身
    # _get_user 函数的返回值是在上一行代码中定义的 RquestContext 的实例的 user 属性值
    user_logged_in.send(current_app._get_current_object(), user=_get_user())
    return True


def logout_user():
    '''
    Logs a user out. (You do not need to pass the actual user.) This will
    also clean up the remember me cookie if it exists.
    '''

    user = _get_user()

    if '_user_id' in session:
        session.pop('_user_id')

    if '_fresh' in session:
        session.pop('_fresh')

    if '_id' in session:
        session.pop('_id')

    cookie_name = current_app.config.get('REMEMBER_COOKIE_NAME', COOKIE_NAME)
    if cookie_name in request.cookies:
        session['_remember'] = 'clear'
        if '_remember_seconds' in session:
            session.pop('_remember_seconds')

    user_logged_out.send(current_app._get_current_object(), user=user)

    current_app.login_manager._update_request_context_with_user()
    return True


def confirm_login():
    '''
    This sets the current session as fresh. Sessions become stale when they
    are reloaded from a cookie.
    '''
    session['_fresh'] = True
    session['_id'] = current_app.login_manager._session_identifier_generator()
    user_login_confirmed.send(current_app._get_current_object())


def login_required(func):
    '''
    登录保护，请求对应的视图函数如果使用这个装饰器，就要先通过如下验证才会运行视图函数
    '''
    @wraps(func)
    def decorated_view(*args, **kwargs):
        # 如果请求方法是 OPTIONS 或者设置了免登录，继续执行视图函数
        if request.method in EXEMPT_METHODS:
            return func(*args, **kwargs)
        elif current_app.config.get('LOGIN_DISABLED'):
            return func(*args, **kwargs)
        # 因为 User 映射类继承了 flask_login.mixins 模块中的 UserMixin 类
        # 所以该类的实例都有一个 is_authenticated 属性，属性值为 True
        # 如果当前用户不是使用 User 类注册的用户，那就是匿名用户，就不能继续执行视图函数了
        elif not current_user.is_authenticated:
            # current_app.login_manager 是定义在 
            # flask_login.login_manager 模块中的 LoginManager 类的实例
            # 此实例的 unauthorized 方法
            # 会执行 flash 消息并重定向到 login_view 属性指向的视图函数
            return current_app.login_manager.unauthorized()
        return func(*args, **kwargs)
    return decorated_view


def fresh_login_required(func):
    '''
    If you decorate a view with this, it will ensure that the current user's
    login is fresh - i.e. their session was not restored from a 'remember me'
    cookie. Sensitive operations, like changing a password or e-mail, should
    be protected with this, to impede the efforts of cookie thieves.

    If the user is not authenticated, :meth:`LoginManager.unauthorized` is
    called as normal. If they are authenticated, but their session is not
    fresh, it will call :meth:`LoginManager.needs_refresh` instead. (In that
    case, you will need to provide a :attr:`LoginManager.refresh_view`.)

    Behaves identically to the :func:`login_required` decorator with respect
    to configutation variables.

    .. Note ::

        Per `W3 guidelines for CORS preflight requests
        <http://www.w3.org/TR/cors/#cross-origin-request-with-preflight-0>`_,
        HTTP ``OPTIONS`` requests are exempt from login checks.

    :param func: The view function to decorate.
    :type func: function
    '''
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if request.method in EXEMPT_METHODS:
            return func(*args, **kwargs)
        elif current_app.config.get('LOGIN_DISABLED'):
            return func(*args, **kwargs)
        elif not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        elif not login_fresh():
            return current_app.login_manager.needs_refresh()
        return func(*args, **kwargs)
    return decorated_view


def set_login_view(login_view, blueprint=None):
    '''
    Sets the login view for the app or blueprint. If a blueprint is passed,
    the login view is set for this blueprint on ``blueprint_login_views``.

    :param login_view: The user object to log in.
    :type login_view: str
    :param blueprint: The blueprint which this login view should be set on.
        Defaults to ``None``.
    :type blueprint: object
    '''

    num_login_views = len(current_app.login_manager.blueprint_login_views)
    if blueprint is not None or num_login_views != 0:

        (current_app.login_manager
            .blueprint_login_views[blueprint.name]) = login_view

        if (current_app.login_manager.login_view is not None and
                None not in current_app.login_manager.blueprint_login_views):

            (current_app.login_manager
                .blueprint_login_views[None]) = (current_app.login_manager
                                                 .login_view)

        current_app.login_manager.login_view = None
    else:
        current_app.login_manager.login_view = login_view


def _get_user():
    '''
    根据请求信息获取用户对象并将其赋值给请求上下文对象的 user 属性
    '''
    # current_app.login_manager 为 flask_login.login_manager.LoginManager 的实例
    # 称作「登录管理对象」
    # 登录管理对象的 _load_user 方法用于更新当前请求上下文对象的 user 属性值
    # 其过程是首先从请求数据中获取用户 ID ，然后查询数据库获取用户对象
    # 最后调用登录管理对象的 _update_request_context_with_user 方法
    # 将用户对象赋值给请求上下文对象的 user 属性
    if has_request_context() and not hasattr(_request_ctx_stack.top, 'user'):
        current_app.login_manager._load_user()

    # 请求上下文栈的 top 属性值为请求上下文对象，获取其 user 属性值并返回
    return getattr(_request_ctx_stack.top, 'user', None)


def _cookie_digest(payload, key=None):
    key = _secret_key(key)

    return hmac.new(key, payload.encode('utf-8'), sha512).hexdigest()


def _get_remote_addr():
    address = request.headers.get('X-Forwarded-For', request.remote_addr)
    if address is not None:
        # An 'X-Forwarded-For' header includes a comma separated list of the
        # addresses, the first address being the actual remote address.
        address = address.encode('utf-8').split(b',')[0].strip()
    return address


def _create_identifier():
    user_agent = request.headers.get('User-Agent')
    if user_agent is not None:
        user_agent = user_agent.encode('utf-8')
    # 参数是 IP 地址字符串和用户代理字符串
    # 前者可以确定用户的网络（电脑），后者可以确定浏览器
    base = '{0}|{1}'.format(_get_remote_addr(), user_agent)
    # 如果解释器是 Python2 ，bytes 就是 str 
    if str is bytes:
        base = text_type(base, 'utf-8', errors='replace')  # pragma: no cover
    h = sha512()
    h.update(base.encode('utf8'))
    # 返回根据 IP 地址和用户代理生成的散列值，也就是 16 进制字符串
    # base 值与散列值一一对应
    return h.hexdigest()


def _user_context_processor():
    return dict(current_user=_get_user())


def _secret_key(key=None):
    if key is None:
        key = current_app.config['SECRET_KEY']

    if isinstance(key, text_type):  # pragma: no cover
        key = key.encode('latin1')  # ensure bytes

    return key
