import inspect
import re

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.middleware.csrf import rotate_token
from django.utils.crypto import constant_time_compare
from django.utils.module_loading import import_string

from .signals import user_logged_in, user_logged_out, user_login_failed

SESSION_KEY = '_auth_user_id'
BACKEND_SESSION_KEY = '_auth_user_backend'
HASH_SESSION_KEY = '_auth_user_hash'
REDIRECT_FIELD_NAME = 'next'


def load_backend(path):
    return import_string(path)()


def _get_backends(return_tuples=False):
    backends = []
    for backend_path in settings.AUTHENTICATION_BACKENDS:
        backend = load_backend(backend_path)
        backends.append((backend, backend_path) if return_tuples else backend)
    if not backends:
        raise ImproperlyConfigured(
            'No authentication backends have been defined. Does '
            'AUTHENTICATION_BACKENDS contain anything?'
        )
    return backends


def get_backends():
    return _get_backends(return_tuples=False)


def _clean_credentials(credentials):
    """
    Clean a dictionary of credentials of potentially sensitive info before
    sending to less secure functions.

    Not comprehensive - intended for user_login_failed signal
    """
    SENSITIVE_CREDENTIALS = re.compile('api|token|key|secret|password|signature', re.I)
    CLEANSED_SUBSTITUTE = '********************'
    for key in credentials:
        if SENSITIVE_CREDENTIALS.search(key):
            credentials[key] = CLEANSED_SUBSTITUTE
    return credentials


def _get_user_session_key(request):
    # This value in the session is always serialized to a string, so we need
    # to convert it back to Python whenever we access it.
    # get_user_model 函数定义在当前模块中，返回值是用户映射类
    # 该映射类的 _meta.pk.to_python 的返回值是实例自身的 id 属性值
    return get_user_model()._meta.pk.to_python(request.session[SESSION_KEY])


def authenticate(request=None, **credentials):
    """
    If the given credentials are valid, return a User object.
    """
    for backend, backend_path in _get_backends(return_tuples=True):
        backend_signature = inspect.signature(backend.authenticate)
        try:
            backend_signature.bind(request, **credentials)
        except TypeError:
            # This backend doesn't accept these credentials as arguments. Try the next one.
            continue
        try:
            user = backend.authenticate(request, **credentials)
        except PermissionDenied:
            # This backend says to stop in our tracks - this user should not be allowed in at all.
            break
        if user is None:
            continue
        # Annotate the user object with the path of the backend.
        user.backend = backend_path
        return user

    # The credentials supplied are invalid to all backends, fire signal
    user_login_failed.send(sender=__name__, credentials=_clean_credentials(credentials), request=request)


def login(request, user, backend=None):
    """
    将用户设为登录状态
    其实就是创建一个 django_session 数据表对应的映射类实例，并调用实例的 save 方法将自身存到数据表中
    最后返回给浏览器一个 Cookie 的 sessionid 字段

    :request: 请求对象
    :user: 用户映射类实例
    """
    print(f'【django.contrib.auth.__init__.login】{user.username} 用户登录')
    session_auth_hash = ''
    if user is None:
        user = request.user
    if hasattr(user, 'get_session_auth_hash'):
        # 此方法定义在 django.contrib.auth.base_user.AbstractBaseUser 类中
        # 返回值是密码的 64 位十六进制哈希值
        session_auth_hash = user.get_session_auth_hash()

    # SESSION_KEY 的值是字符串 '_auth_user_id'
    # 如果请求信息中的 Cookie 中有 sessionid 字段，执行 if 语句块，否则执行 else 语句块
    if SESSION_KEY in request.session:
        if _get_user_session_key(request) != user.pk or (
                session_auth_hash and
                not constant_time_compare(request.session.get(HASH_SESSION_KEY, ''), session_auth_hash)):
            # To avoid reusing another user's session, create a new, empty
            # session if the existing session corresponds to a different
            # authenticated user.
            # 此方法定义在 django.contrib.sessions.backends.base.SessionBase 类中
            # 其作用是在数据库的 django_session 表中移除请求的 session 
            request.session.flush()
    else:
        # 创建一个 django_session 数据表对应的映射类实例，并调用实例的 save 方法将自身存到数据表中
        # 该实例包含三个字段：
        # session_key: 返回给浏览器的 Cookie 的 sessionid 字段值
        # session_data: session_key 对应的值（该值还不完整，完整值在 session 中间件处理响应对象过程中创建）
        # expire_date: session 的有效截止时间（两周）
        request.session.cycle_key()

    try:
        backend = backend or user.backend
    except AttributeError:
        backends = _get_backends(return_tuples=True)
        if len(backends) == 1:
            _, backend = backends[0]
        else:
            raise ValueError(
                'You have multiple authentication backends configured and '
                'therefore must provide the `backend` argument or set the '
                '`backend` attribute on the user.'
            )
    else:
        if not isinstance(backend, str):
            raise TypeError('backend must be a dotted import path string (got %r).' % backend)

    # 下面 3 行给 request.session 增加 3 组键值对：
    # {   _auth_user_id: 2,
	#     _auth_user_backend: django.contrib.auth.backends.ModelBackend,
	#     _auth_user_hash: 64 位十六进制字符串
    # }
    request.session[SESSION_KEY] = user._meta.pk.value_to_string(user)
    request.session[BACKEND_SESSION_KEY] = backend
    request.session[HASH_SESSION_KEY] = session_auth_hash
    if hasattr(request, 'user'):
        request.user = user
    # 给 request.META 换个新的 csrftoken 字段值
    #rotate_token(request)
    user_logged_in.send(sender=user.__class__, request=request, user=user)


def logout(request):
    """
    根据 request.COOKIES 中的 sessionid 字段找到 django_session 数据表中对应的数据
    解析数据的 session_data 字段得到用户 id 
    根据用户 id 得到用户映射类实例并判断身份是否合法
    最后移除 django_session 数据表中对应的数据
    
    当前退出登录函数是在调用视图函数的过程中执行的，视图函数返回响应对象之后
    在 sessions 中间件处理响应对象时会移除 Cookie 中的 sessionid 字段，这样就彻底退出登录了
    """
    # user 是用户映射类实例
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', True):
        user = None
    user_logged_out.send(sender=user.__class__, request=request, user=user)
    # 移除 django_session 数据表中相应的数据
    request.session.flush()
    # 把 request.user 属性值设为匿名用户
    if hasattr(request, 'user'):
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()


def get_user_model():
    """
    Return the User model that is active in this project.
    """
    try:
        # settings.AUTH_USER_MODEL 是定义在项目的配置文件中的配置项
        # 此处返回的是用户映射类
        return django_apps.get_model(settings.AUTH_USER_MODEL, require_ready=False)
    except ValueError:
        raise ImproperlyConfigured("AUTH_USER_MODEL must be of the form 'app_label.model_name'")
    except LookupError:
        raise ImproperlyConfigured(
            "AUTH_USER_MODEL refers to model '%s' that has not been installed" % settings.AUTH_USER_MODEL
        )


def get_user(request):
    """
    Return the user model instance associated with the given request session.
    If no user is retrieved, return an instance of `AnonymousUser`.
    """
    from .models import AnonymousUser
    user = None
    try:
        # user_id 是用户类实例的 id 属性值
        user_id = _get_user_session_key(request)
        backend_path = request.session[BACKEND_SESSION_KEY]
    except KeyError:
        pass
    else:
        if backend_path in settings.AUTHENTICATION_BACKENDS:
            # backend 是 django.contrib.auth.backends.ModelBackend 类
            backend = load_backend(backend_path)
            # user 是根据 user_id 得到的用户类实例
            user = backend.get_user(user_id)
            # Verify the session
            if hasattr(user, 'get_session_auth_hash'):
                session_hash = request.session.get(HASH_SESSION_KEY)
                session_hash_verified = session_hash and constant_time_compare(
                    session_hash,
                    user.get_session_auth_hash()
                )
                if not session_hash_verified:
                    if not (
                        session_hash and
                        hasattr(user, '_legacy_get_session_auth_hash') and
                        constant_time_compare(session_hash, user._legacy_get_session_auth_hash())
                    ):
                        request.session.flush()
                        user = None

    return user or AnonymousUser()


def get_permission_codename(action, opts):
    """
    Return the codename of the permission for the specified action.
    """
    return '%s_%s' % (action, opts.model_name)


def update_session_auth_hash(request, user):
    """
    Updating a user's password logs out all sessions for the user.

    Take the current request and the updated user object from which the new
    session hash will be derived and update the session hash appropriately to
    prevent a password change from logging out the session from which the
    password was changed.
    """
    request.session.cycle_key()
    if hasattr(user, 'get_session_auth_hash') and request.user == user:
        request.session[HASH_SESSION_KEY] = user.get_session_auth_hash()


default_app_config = 'django.contrib.auth.apps.AuthConfig'
