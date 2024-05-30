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
import logging
from threading import local

from django.conf import settings
from django.core import signals
from django.core.cache.backends.base import (
    BaseCache, CacheKeyWarning, InvalidCacheBackendError,
)
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


__all__ = [
    'cache', 'caches', 'DEFAULT_CACHE_ALIAS', 'InvalidCacheBackendError',
    'CacheKeyWarning', 'BaseCache',
]

DEFAULT_CACHE_ALIAS = 'default'


def _create_cache(alias_backend, **kwargs):
    """从配置文件中获取缓存配置信息，从中得到缓存类，对其进行实例化生成「缓存对象」并返回
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
        # 获取缓存类
        backend_cls = import_string(backend)
    except ImportError as e:
        raise InvalidCacheBackendError("Could not find backend '%s': %s" % (backend, e))

    logger.info(f'创建「缓存对象」 {alias_backend:<8}{location}')
    # 对缓存类（例如 django_redis.cache.RedisCache）进行实例化生成「缓存对象」并返回
    obj = backend_cls(location, params)

    return obj


class CacheHandler:
    """缓存处理器类，用于管理「缓存对象」

    Django 项目启动后，创建一个子线程作为 Django 应用的主线程
    在 Django 应用的主线程中执行 django.core.managements.runserver.Command.inner_run 方法
    在此方法中进行系统检查、数据库迁移文件检查、创建「应用对象」等操作，这些都在 Django 应用的主线程中进行
    在此方法的执行过程中，当前缓存处理器类会被实例化一次，其实例叫做「缓存处理器」，这是一个全局对象
    该实例用于确保每个线程中每个别名只存在一个「缓存对象」
    """

    def __init__(self):
        # local 是 Python 标准库 _threading_local 模块中的类
        # 该类在初始化时不允许提供参数，该类的实例我们称为「线程管理对象」，用于按线程存储数据
        # 项目启动时就会创建这个实例，该实例的所有属性都是线程安全的
        self._caches = local()

        # local 操作流程如下，它使用递归线程锁实现线程安全
        #
        # 1. 创建一个 impl 对象，该对象用来存储数据
        #    存储的数据都在 impl.dicts 属性中，其值是一个字典对象
        #    字典的 key 是当前线程的内存地址，value 是内嵌的字典对象
        #    每次数据的增删改查都根据当前线程的内存地址在 impl.dicts 字典中找到内嵌字典对象再操作
        # 2. 在创建 local 实例时将 impl 对象赋值给 local._local__impl 属性
        # 3. local 内部重写了 __setattr__ 之类的操作属性的方法
        #    每次操作都会使用一个 _patch 上下文函数根据当前所在线程来获取 impl.dicts 中对应的内嵌字典对象
        #    然后在线程锁内对这个字典对象进行操作，这样就达到了线程安全的效果

        # 在 self.__getitem__ 方法中用的是 self._caches.caches 这个属性，也就是 local 对象的 caches 属性
        #
        # 1. 定义 local.caches 属性
        #    首先在 _patch 这个上下文函数里找到 local._local_impl 属性值，即 impl 对象
        #    然后调用 impl.get_dict 方法根据当前线程的内存地址找到 impl.dicts 字典中对应的内嵌字典
        #    这时候肯定是没有的，那就调用 impl.create_dict 方法新建一组键值对放到 impl.dicts 里面
        #    其中 key 自然就是当前线程的内存地址，value 是空字典
        #    然后在线程锁内进行操作:
        #       a. 把这个 value 赋值给 local.__dict__ 属性
        #       b. 调用 object.__setattr__ 方法设置属性
        #          也就是向 local.__dict__ 中加入一组键值对 {'caches': {}}
        #    此时 impl.dicts = {
        #        当前线程的内存地址: {
        #            'caches': {}
        #        }
        #    }
        # 2. 使用 local.caches 属性存储数据
        #    创建一个「缓存对象」并将其存储在 local.cache 中，其中 key 是缓存对象别名，value 是「缓存对象」
        #    首先要查找 local 的 caches 属性，也就是调用 local.__getattribute__ 方法
        #    还是在 _patch 这个上下文函数里找到 local._local_impl 属性值，即 impl 对象
        #    然后调用 impl.get_dict 方法根据当前线程的内存地址找到 impl.dicts 字典中对应的内嵌字典
        #    此时的内嵌字典就是 {'caches': {}}
        #    然后在线程锁内进行操作:
        #        a. 把这个内嵌字典赋值给 local.__dict__ 属性，这样 local.caches 的值就是空字典
        #        b. 给这个空字典添加一组键值对 {'default': 缓存对象}
        #    此时 impl.dicts = {
        #        当前线程的内存地址: {
        #            'caches': {
        #                'default': 缓存对象
        #            }
        #        }
        #    }
        # 3. 每次请求进入，服务器的套接字对象都会委派一个线程来单独处理
        #    线程可能重用，所以未必每次都在 impl.dicts 里新建一组键值对 {当前线程的内存地址: local.__dict__}
        #    但同时处理的多个请求所使用的线程是隔绝的，它们对应的「缓存对象」也是隔绝的

    def __getitem__(self, alias):
        """获取「缓存处理器」中 alias 对应的值，参数 alias 的值通常是字符串 'default'

        0. 每次请求进入后，调用「缓存对象」cache 时都会执行当前所在方法
        1. 如果「线程管理对象」的 caches 属性里有这个 alias ，直接返回其对应的值，也就是 Redis 缓存对象
        2. 如果没有这个 alias ，创建一个 Redis 缓存对象加到「线程管理对象」的 caches 属性字典里并返回
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

        # 创建一个「缓存对象」
        cache = _create_cache(alias)
        self._caches.caches[alias] = cache
        return cache

    def all(self):
        return getattr(self._caches, 'caches', {}).values()


caches = CacheHandler()


class DefaultCacheProxy:
    """缓存代理类

    caches[DEFAULT_CACHE_ALIAS] 就是「缓存对象」，是 django_redis.cache.RedisCache 类的实例
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

# 下面的 cache 对象可以看作是利用当前线程获取到的「缓存对象」，即 django_redis.cache.RedisCache 类的实例
#「缓存对象」的 client 属性是 django_redis.client.default.DefaultClient 类的实例，叫做「Redis 客户端对象」
#「Redis 客户端对象」的 get_client 方法返回 redis.client.Redis 类的实例，取名为「Redis 对象」
#「Redis 对象」有个 connection_pool 属性，属性值是 redis.connection.ConnectionPool 类的实例，叫做「连接池」
#「连接池」有个 _available_connections 列表，里面是 redis.connection.Connection 类的实例，叫做「连接对象」
#
# 首次调用「缓存对象」的方法时，例如调用 cache.get('xxx') 流程如下：
# 1. 创建一个「缓存对象」，即 django_redis.cache.RedisCache 类的实例，这个就是 cache 了
# 2. 调用 cache.get 方法，就会调用 cache.client.get 方法，其实就是对应的「Redis 客户端对象」的 get 方法
# 3. 在「Redis 客户端对象」的 get 方法内，首先调用自身的 get_client 方法在「连接池」里找一个「连接对象」
#    然后调用「连接对象」的 TODO
cache = DefaultCacheProxy()


def close_caches(**kwargs):
    # Some caches -- python-memcached in particular -- need to do a cleanup at the
    # end of a request cycle. If not implemented in a particular backend
    # cache.close is a no-op
    for cache in caches.all():
        cache.close()


signals.request_finished.connect(close_caches)
