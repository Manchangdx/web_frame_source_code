"""
Caching framework.

This package defines set of cache backends that all conform to a simple API.
In a nutshell, a cache is a set of values -- which can be any object that
may be pickled -- identified by string keys.  For the complete API, see
the abstract BaseCache class in django.core.cache.backends.base.

Client code should use the `cache` variable defined here to access the default
cache backend and look up non-default cache backends in the `caches` dict-like
object.

See docs/topics/cache.txt for information on the public API.
"""
from threading import local

from django.conf import settings
from django.core import signals
from django.core.cache.backends.base import (
    BaseCache, CacheKeyWarning, InvalidCacheBackendError,
)
from django.utils.module_loading import import_string

__all__ = [
    'cache', 'caches', 'DEFAULT_CACHE_ALIAS', 'InvalidCacheBackendError',
    'CacheKeyWarning', 'BaseCache',
]

DEFAULT_CACHE_ALIAS = 'default'


def _create_cache(backend, **kwargs):
    """从配置文件中获取缓存配置信息，从中得到缓存类，对其进行实例化并返回
    """
    try:
        try:
            # 从配置项 CACHES 中获取配置信息，conf 是一个字典对象
            conf = settings.CACHES[backend]
        except KeyError:
            try:
                import_string(backend)
            except ImportError as e:
                raise InvalidCacheBackendError("Could not find backend '%s': %s" % (
                    backend, e))
            location = kwargs.pop('LOCATION', '')
            params = kwargs
        else:
            params = {**conf, **kwargs}
            backend = params.pop('BACKEND')
            location = params.pop('LOCATION', '')
        # 获取 Redis 缓存类
        backend_cls = import_string(backend)
    except ImportError as e:
        raise InvalidCacheBackendError(
            "Could not find backend '%s': %s" % (backend, e))
    # 对 Redis 缓存类进行实例化（其实就是 Redis 客户端对象）并返回
    return backend_cls(location, params)


class CacheHandler:
    """
    A Cache Handler to manage access to Cache instances.

    Ensure only one instance of each alias exists per thread.
    """
    def __init__(self):
        # local 是 Python 标准库 _threading_local 模块中的类
        # 该类在初始化时不允许提供参数，该类的实例（线程缓存对象）按线程存储数据
        self._caches = local()

    def __getitem__(self, alias):
        """获取「线程缓存对象」中 alias 对应的值，变量 alias 的值通常是字符串 'default'

        1.「线程缓存对象」的 caches 属性里有这个 alias ，直接返回其对应的值，也就是 Redis 缓存对象
        2. 如果没有这个 alias ，创建一个 Redis 缓存对象加到「线程缓存对象」的 caches 属性里并返回
        """
        try:
            return self._caches.caches[alias]
        except AttributeError:
            self._caches.caches = {}
        except KeyError:
            pass

        if alias not in settings.CACHES:
            raise InvalidCacheBackendError(
                "Could not find config for '%s' in settings.CACHES" % alias
            )

        # 创建一个 Redis 缓存对象
        cache = _create_cache(alias)
        self._caches.caches[alias] = cache
        return cache

    def all(self):
        return getattr(self._caches, 'caches', {}).values()


caches = CacheHandler()


class DefaultCacheProxy:
    """
    Proxy access to the default Cache object's attributes.

    This allows the legacy `cache` object to be thread-safe using the new
    ``caches`` API.
    """
    def __getattr__(self, name):
        return getattr(caches[DEFAULT_CACHE_ALIAS], name)

    def __setattr__(self, name, value):
        return setattr(caches[DEFAULT_CACHE_ALIAS], name, value)

    def __delattr__(self, name):
        return delattr(caches[DEFAULT_CACHE_ALIAS], name)

    def __contains__(self, key):
        return key in caches[DEFAULT_CACHE_ALIAS]

    def __eq__(self, other):
        return caches[DEFAULT_CACHE_ALIAS] == other


cache = DefaultCacheProxy()


def close_caches(**kwargs):
    # Some caches -- python-memcached in particular -- need to do a cleanup at the
    # end of a request cycle. If not implemented in a particular backend
    # cache.close is a no-op
    for cache in caches.all():
        cache.close()


signals.request_finished.connect(close_caches)
