VERSION = (4, 11, 0)
__version__ = '.'.join(map(str, VERSION))


def get_redis_connection(alias='default', write=True):
    """获得一个原生 Redis 客户端对象
    """

    from django.core.cache import caches

    cache = caches[alias]

    if not hasattr(cache, "client"):
        raise NotImplementedError("This backend does not support this feature")

    if not hasattr(cache.client, "get_client"):
        raise NotImplementedError("This backend does not support this feature")

    # cache 是 django_redis.cache.RedisCache 类的实例
    # cache.client 是 django_redis.client.default.DefaultClient 类的实例
    # cache.client.get_client(1) 是 redis.client.Redis 类的实例
    return cache.client.get_client(write)
