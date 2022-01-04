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


def _create_cache(alias_backend, **kwargs):
    """从配置文件中获取缓存配置信息，从中得到缓存类，对其进行实例化并返回
    """
    try:
        try:
            # 从配置项 CACHES 中获取配置信息，conf 是一个字典对象
            conf = settings.CACHES[alias_backend]
        except KeyError:
            try:
                import_string(alias_backend)
            except ImportError as e:
                raise InvalidCacheBackendError("Could not find backend '%s': %s" % (
                    alias_backend, e))
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
    print(f'【django.core.cache.__init__._create_cache】创建 Redis 缓存对象 {alias_backend:<8}{location}')
    return backend_cls(location, params)


class CacheHandler:
    """缓存处理器类，用于管理「缓存对象」

    通常该类只被实例化一次，其实例叫做「缓存处理器」
    该实例用于确保每个线程中每个别名只存在一个「缓存对象」
    """

    def __init__(self):
        # local 是 Python 标准库 _threading_local 模块中的类
        # 该类在初始化时不允许提供参数，该类的实例（线程缓存对象）按线程存储数据
        # 项目启动时就会创建这个实例，该实例的所有属性都是线程安全的
        self._caches = local()

        # local 实现线程安全的原理如下
        #
        # 1. 创建一个 impl 对象，该对象用来存储数据（该对象是实现线程安全的核心）
        #    存储的数据都在 impl.dicts 属性中，其值是一个字典对象
        #    字典的 key 是当前线程的内存地址，value 是内嵌的字典对象
        #    每次数据的增删改查都根据当前线程的内存地址在 impl.dicts 字典中找到内嵌字典对象再操作
        # 2. 在创建 local 实例时将 impl 对象赋值给 local._local__impl 属性
        # 3. local 内部重写了 __setattr__ 之类的操作属性的方法
        #    每次操作都会使用一个 _patch 上下文函数根据当前所在线程来获取 impl.dicts 中对应的内嵌字典对象
        #    然后再对这个字典对象进行操作，这样就达到了线程安全的效果

        # 在 self.__getitem__ 方法中用的是 self._caches.caches 这个属性，也就是 local 对象的 caches 属性
        #
        # 1. 定义 local.caches 属性为空字典，也就是调用 local.__setattr__ 方法设置属性
        #    首先在 _patch 这个上下文函数里找到 local._local_impl 属性值，即 impl 对象
        #    然后调用 impl.get_dict 方法根据当前线程的内存地址找到 impl.dicts 字典中对应的内嵌字典
        #    这时候肯定是没有的，那就调用 impl.create_dict 方法新建一组键值对放到 impl.dicts 里面
        #    其中 key 自然就是当前线程的内存地址，value 是空字典
        #    顺便在线程锁中把这个 value 赋值给 local.__dict__ 属性
        #    最后给 local 设置属性，也就是向 local.__dict__ 中加入一组键值对 {'caches': {}}
        # 2. 使用 local.caches 存储数据
        #    创建一个缓存对象，然后将其存储 local.cache 中，其中 key 是缓存对象别名，value 是缓存对象
        #    首先要查找 local 的 caches 属性，也就是调用 local.__getattribute__ 方法
        #    还是在 _patch 这个上下文函数里找到 local._local_impl 属性值，即 impl 对象
        #    然后调用 impl.get_dict 方法根据当前线程的内存地址找到 impl.dicts 字典中对应的内嵌字典
        #    内嵌字典就是 {'caches': {}} ，在线程锁中把这个内嵌字典赋值给 local.__dict__ 属性
        #    这样 local.caches 的值就是空字典了
        #    最后再给这个空字典添加一组键值对 {'default': 缓存对象}
        #    这样 impl.dicts 就变成了 {'caches': {'default': 缓存对象}}
        # 3. 每次请求进入，服务器的套接字对象都会委派一个线程来单独处理
        #    注意每次未必都会在 impl.dicts 里新建一组键值对 {当前线程的内存地址: local.__dict__}
        #    因为线程可能重用
        #    但同时处理的多个请求所使用的线程是单独的，它们对应的「缓存对象」也是单独的

    def __getitem__(self, alias):
        """获取「缓存处理器」中 alias 对应的值，参数 alias 的值通常是字符串 'default'

        0. 每次请求进入后，调用「缓存对象」cache 时都会执行当前所在方法
        1. 如果「线程缓存对象」的 caches 属性里有这个 alias ，直接返回其对应的值，也就是 Redis 缓存对象
        2. 如果没有这个 alias ，创建一个 Redis 缓存对象加到「线程缓存对象」的 caches 属性字典里并返回
        """
        try:
            # self._caches 的所有属性都是线程安全的
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
