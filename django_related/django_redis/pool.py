import logging

from django.conf import settings
from django.utils.module_loading import import_string
from redis.connection import DefaultParser

logger = logging.getLogger(__name__)


class ConnectionFactory:
    """Redis 连接工厂
    """

    _pools = {}

    def __init__(self, options):
        logger.info(f'初始化 Redis 连接工厂 {options=}')
        pool_cls_path = options.get("CONNECTION_POOL_CLASS", "redis.connection.ConnectionPool")
        self.pool_cls = import_string(pool_cls_path)
        self.pool_cls_kwargs = options.get("CONNECTION_POOL_KWARGS", {})

        redis_client_cls_path = options.get("REDIS_CLIENT_CLASS", "redis.client.StrictRedis")
        self.redis_client_cls = import_string(redis_client_cls_path)
        self.redis_client_cls_kwargs = options.get("REDIS_CLIENT_KWARGS", {})

        self.options = options

    def make_connection_params(self, url):
        """
        Given a main connection parameters, build a complete
        dict of connection parameters.
        """

        kwargs = {
            "url": url,
            "parser_class": self.get_parser_cls(),
        }

        password = self.options.get("PASSWORD", None)
        if password:
            kwargs["password"] = password

        socket_timeout = self.options.get("SOCKET_TIMEOUT", None)
        if socket_timeout:
            assert isinstance(socket_timeout, (int, float)), \
                "Socket timeout should be float or integer"
            kwargs["socket_timeout"] = socket_timeout

        socket_connect_timeout = self.options.get("SOCKET_CONNECT_TIMEOUT", None)
        if socket_connect_timeout:
            assert isinstance(socket_connect_timeout, (int, float)), \
                "Socket connect timeout should be float or integer"
            kwargs["socket_connect_timeout"] = socket_connect_timeout

        return kwargs

    def connect(self, url):
        """创建 Redis 客户端并返回，参数 url 是 Redis 服务器连接串
        """
        params = self.make_connection_params(url)
        logger.info(f'Redis 连接工厂创建连接 {params=}')
        connection = self.get_connection(params)
        return connection

    def get_connection(self, params):
        """
        Given a now preformated params, return a
        new connection.

        The default implementation uses a cached pools
        for create new connection.
        """
        pool = self.get_or_create_connection_pool(params)
        return self.redis_client_cls(connection_pool=pool, **self.redis_client_cls_kwargs)

    def get_parser_cls(self):
        cls = self.options.get("PARSER_CLASS", None)
        if cls is None:
            return DefaultParser
        return import_string(cls)

    def get_or_create_connection_pool(self, params):
        """根据 Redis 服务器连接串获取 Redis 连接池
        """
        key = params["url"]
        if key not in self._pools:
            self._pools[key] = self.get_connection_pool(params)
        return self._pools[key]

    def get_connection_pool(self, params):
        """创建 Redis 连接池

        给定连接参数，返回一个新的连接池。
        如果您想要自定义创建连接池的行为，可以重写此方法。
        """
        cp_params = dict(params)
        cp_params.update(self.pool_cls_kwargs)
        # self.pool_cls 是 redis.connection.ConnectionPool 类
        pool = self.pool_cls.from_url(**cp_params)

        if pool.connection_kwargs.get("password", None) is None:
            pool.connection_kwargs["password"] = params.get("password", None)
            pool.reset()

        return pool


def get_connection_factory(path=None, options=None):
    if path is None:
        path = getattr(settings, "DJANGO_REDIS_CONNECTION_FACTORY", "django_redis.pool.ConnectionFactory")

    cls = import_string(path)
    logger.info(f'创建 Redis 连接工厂 {cls=}')
    # django_redis.pool.ConnectionFactory（当前模块中）
    return cls(options or {})
