import logging

from django.contrib.sessions.backends.base import (
    CreateError, SessionBase, UpdateError,
)
from django.core.exceptions import SuspiciousOperation
from django.db import DatabaseError, IntegrityError, router, transaction
from django.utils import timezone
from django.utils.functional import cached_property


class SessionStore(SessionBase):
    """
    Implement database session store.
    """
    def __init__(self, session_key=None):
        super().__init__(session_key)

    @classmethod
    def get_model_class(cls):
        # Avoids a circular import and allows importing SessionStore when
        # django.contrib.sessions is not in INSTALLED_APPS.
        from django.contrib.sessions.models import Session
        return Session

    @cached_property
    def model(self):
        return self.get_model_class()

    def _get_session_from_db(self):
        # 从数据库中找到 session_key 对应的那条数据，返回对应的映射类实例
        try:
            return self.model.objects.get(
                session_key=self.session_key,
                expire_date__gt=timezone.now()
            )
        except (self.model.DoesNotExist, SuspiciousOperation) as e:
            if isinstance(e, SuspiciousOperation):
                logger = logging.getLogger('django.security.%s' % e.__class__.__name__)
                logger.warning(str(e))
            self._session_key = None

    def load(self):
        # 从数据库中找到 session_key 对应的那条数据，返回对应的映射类实例
        s = self._get_session_from_db()
        # 解析映射类实例的 session_data 属性值，返回字典：
        # {'_auth_user_id': '2', 
        #  '_auth_user_backend': 'django.contrib.auth.backends.ModelBackend', 
        #  '_auth_user_hash': 64 位十六进制字符串
        # }
        d = self.decode(s.session_data) if s else {}
        return d

    def exists(self, session_key):
        return self.model.objects.filter(session_key=session_key).exists()

    def create(self):
        # self 是 reques.session 
        while True:
            # 这会儿 self._session_key 应该是 None，现在给该属性赋一个新值，32 位随机值
            self._session_key = self._get_new_session_key()
            try:
                # 创建一个 django_session 数据表对应的映射类实例，并调用实例的 save 方法将自身存到数据表中
                self.save(must_create=True)
            except CreateError:
                # Key wasn't unique. Try again.
                continue
            self.modified = True
            return

    def create_model_instance(self, data):
        """
        Return a new instance of the session model object, which represents the
        current session state. Intended to be used for saving the session data
        to the database.
        """
        return self.model(
            # 32 位随机字符串，作为客户端 Cookie 的 sessionid 字段值
            session_key=self._get_or_create_session_key(),
            # 这是一个随机的哈希值，有点儿复杂，待研究
            session_data=self.encode(data),
            # Datetime 对象，session 的过期时间，默认是此时的日期时间 + 两周
            expire_date=self.get_expiry_date(),
        )

    def save(self, must_create=False):
        """创建一个 django_session 数据表对应的映射类实例，并调用实例的 save 方法将自身存到数据表中。
        """
        # self 是 request.session
        
        if self.session_key is None:
            return self.create()
        # 在创建响应对象过程中，data 的值是空字典
        # 在调用 session 中间件处理响应对象过程中，data 的值是：
        # {'_auth_user_id': '2', 
        #  '_auth_user_backend': 'django.contrib.auth.backends.ModelBackend', 
        #  '_auth_user_hash': '3a17add86428df990b4080690bd8c102e9dee620ccc65865165130439c8988f7'
        # }
        # 这些字段是在 django.contrib.auth.__init__.login 登录函数中添加的
        data = self._get_session(no_load=must_create)
        # 创建一个 django_session 数据表对应的映射类实例
        obj = self.create_model_instance(data)
        using = router.db_for_write(self.model, instance=obj)
        try:
            # 将实例存入数据表中，注意是 with 上下文对象退出的时候才会存入，而不是 obj.save 存入
            with transaction.atomic(using=using):
                obj.save(force_insert=must_create, force_update=not must_create, using=using)
        except IntegrityError:
            if must_create:
                raise CreateError
            raise
        except DatabaseError:
            if not must_create:
                raise UpdateError
            raise

    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        try:
            self.model.objects.get(session_key=session_key).delete()
        except self.model.DoesNotExist:
            pass

    @classmethod
    def clear_expired(cls):
        cls.get_model_class().objects.filter(expire_date__lt=timezone.now()).delete()
