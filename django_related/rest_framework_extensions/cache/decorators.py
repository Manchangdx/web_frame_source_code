from functools import wraps, WRAPPER_ASSIGNMENTS

from django.http.response import HttpResponse

from rest_framework_extensions.settings import extensions_api_settings


def get_cache(alias):
    from django.core.cache import caches
    return caches[alias]


class CacheResponse:
    """缓存类，可以用于存储和返回 Django 响应对象（使用 Redis 服务器缓存）
    
    该类的实例可作为视图函数的装饰器，这样做之后，视图函数就是 self.__call__ 方法的返回值 inner
    调用视图函数就是调用 inner 函数
    也就是说，每个需要设置缓存的视图类的方法（视图函数）都要配置一个单独的 “该类的实例”

    原注释:
        这个装饰器会渲染并丢弃原始的 DRF 响应，转而使用 Django 的 “HttpResponse”
        这允许缓存保留更小的内存占用，并消除了对每个请求重新呈现响应的需要
        此外，它还消除了用户在不知情的情况下缓存整个序列化器和 queryset 的风险
    """

    def __init__(self,
                 timeout=None,
                 key_func=None,
                 cache=None,
                 cache_errors=None):
        """初始化缓存类实例

        在初始化 “该类的实例” 作为装饰器定义视图函数时，需设定 key_func 和 timeout 两个关键属性
        它们分别是 “用于创建缓存 key 的可调用对象” 和 “超时时间”
        """
        if timeout is None:
            self.timeout = extensions_api_settings.DEFAULT_CACHE_RESPONSE_TIMEOUT
        else:
            self.timeout = timeout

        if key_func is None:
            self.key_func = extensions_api_settings.DEFAULT_CACHE_KEY_FUNC
        else:
            self.key_func = key_func

        if cache_errors is None:
            self.cache_errors = extensions_api_settings.DEFAULT_CACHE_ERRORS
        else:
            self.cache_errors = cache_errors

        self.cache = get_cache(cache or extensions_api_settings.DEFAULT_USE_CACHE)

    def __call__(self, func):
        """创建视图函数
        
        该类的实例就是用来创建视图函数的装饰器，创建视图函数时就会调用这个 __call__ 方法
        参数 self 就是当前类的实例，参数 func 就是被装饰的视图函数
        """
        this = self

        # 调用视图函数就是调用这个 inner 函数
        @wraps(func, assigned=WRAPPER_ASSIGNMENTS)
        def inner(self, request, *args, **kwargs):
            # this 是缓存类实例；self 是视图类实例
            return this.process_cache_response(
                view_instance=self,
                view_method=func,
                request=request,
                args=args,
                kwargs=kwargs,
            )
        return inner

    def process_cache_response(self,
                               view_instance,   # 视图类实例
                               view_method,     # 原本的视图函数
                               request,         # 请求对象
                               args,
                               kwargs):
        # 缓存肯定是以 key - value 形式存放，因为用的是 Redis 数据库的缓存功能
        # 这里是获取 key ，它是一个 32 位的 MD5 哈希字符串
        key = self.calculate_key(
            view_instance=view_instance,
            view_method=view_method,
            request=request,
            args=args,
            kwargs=kwargs
        )
        print('【rest_framework_extensions.cache.decorators.CacheResponse.process_cache_response】key:', key)
        # 查询缓存的超时时间，这个是预设好的
        timeout = self.calculate_timeout(view_instance=view_instance)

        # 根据 key 从 Redis 数据库里查询缓存数据
        response_triple = self.cache.get(key)
        #print('【rest_framework_extensions.cache.decorators.CacheResponse.process_cache_response】response_triple:', response_triple)
        if not response_triple:
            # render response to create and cache the content byte string
            response = view_method(view_instance, request, *args, **kwargs)
            response = view_instance.finalize_response(request, response, *args, **kwargs)
            response.render()

            if not response.status_code >= 400 or self.cache_errors:
                # django 3.0 has not .items() method, django 3.2 has not ._headers
                if hasattr(response, '_headers'):
                    headers = response._headers.copy()
                else:
                    headers = {k: (k, v) for k, v in response.items()}
                response_triple = (
                    response.rendered_content,
                    response.status_code,
                    headers
                )
                self.cache.set(key, response_triple, timeout)
        else:
            # build smaller Django HttpResponse
            content, status, headers = response_triple
            response = HttpResponse(content=content, status=status)
            for k, v in headers.values():
                response[k] = v
        if not hasattr(response, '_closable_objects'):
            response._closable_objects = []

        return response

    def calculate_key(self,
                      view_instance,
                      view_method,
                      request,
                      args,
                      kwargs):
        #print('【rest_framework_extensions.cache.decorators.CacheResponse.calculate_key】view_instance:', view_instance)
        if isinstance(self.key_func, str):
            key_func = getattr(view_instance, self.key_func)
        else:
            key_func = self.key_func
        print('【rest_framework_extensions.cache.decorators.CacheResponse.calculate_key】key_func:', key_func)
        return key_func(
            view_instance=view_instance,
            view_method=view_method,
            request=request,
            args=args,
            kwargs=kwargs,
        )

    def calculate_timeout(self, view_instance, **_):
        """计算缓存的超时时间并返回

        参数 view_instance 是视图类实例，在项目启动时已经创建好
        同时创建好的还有视图函数，而视图函数是将当前类的实例作为装饰器创建的，所以 self 也在项目启动时创建好了
        调用视图函数时就会调用 self.__call__ 内嵌的 inner 函数
        调用链会涉及查询缓存是否过期，也就是会调用当前方法了
        """
        # 超时时间可能是字符串，参见 rest_framework_extensions.cache.mixins 模块中的 cache_response 装饰器
        if isinstance(self.timeout, str):
            self.timeout = getattr(view_instance, self.timeout)
        return self.timeout


cache_response = CacheResponse
