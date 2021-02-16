import asyncio
import inspect
import warnings

from asgiref.sync import sync_to_async


class RemovedInNextVersionWarning(DeprecationWarning):
    pass


class RemovedInDjango40Warning(PendingDeprecationWarning):
    pass


class warn_about_renamed_method:
    def __init__(self, class_name, old_method_name, new_method_name, deprecation_warning):
        self.class_name = class_name
        self.old_method_name = old_method_name
        self.new_method_name = new_method_name
        self.deprecation_warning = deprecation_warning

    def __call__(self, f):
        def wrapped(*args, **kwargs):
            warnings.warn(
                "`%s.%s` is deprecated, use `%s` instead." %
                (self.class_name, self.old_method_name, self.new_method_name),
                self.deprecation_warning, 2)
            return f(*args, **kwargs)
        return wrapped


class RenameMethodsBase(type):
    """
    Handles the deprecation paths when renaming a method.

    It does the following:
        1) Define the new method if missing and complain about it.
        2) Define the old method if missing.
        3) Complain whenever an old method is called.

    See #15363 for more details.
    """

    renamed_methods = ()

    def __new__(cls, name, bases, attrs):
        new_class = super().__new__(cls, name, bases, attrs)

        for base in inspect.getmro(new_class):
            class_name = base.__name__
            for renamed_method in cls.renamed_methods:
                old_method_name = renamed_method[0]
                old_method = base.__dict__.get(old_method_name)
                new_method_name = renamed_method[1]
                new_method = base.__dict__.get(new_method_name)
                deprecation_warning = renamed_method[2]
                wrapper = warn_about_renamed_method(class_name, *renamed_method)

                # Define the new method if missing and complain about it
                if not new_method and old_method:
                    warnings.warn(
                        "`%s.%s` method should be renamed `%s`." %
                        (class_name, old_method_name, new_method_name),
                        deprecation_warning, 2)
                    setattr(base, new_method_name, old_method)
                    setattr(base, old_method_name, wrapper(old_method))

                # Define the old method as a wrapped call to the new method.
                if not old_method and new_method:
                    setattr(base, old_method_name, wrapper(new_method))

        return new_class


class DeprecationInstanceCheck(type):
    def __instancecheck__(self, instance):
        warnings.warn(
            "`%s` is deprecated, use `%s` instead." % (self.__name__, self.alternative),
            self.deprecation_warning, 2
        )
        return super().__instancecheck__(instance)


class MiddlewareMixin:
    """中间件基类，所有的中间件类都需要继承此类，所有的中间件实例的 __call__ 方法都定义在此类中
    """
    sync_capable = True
    async_capable = True

    # RemovedInDjango40Warning: when the deprecation ends, replace with:
    #   def __init__(self, get_response):
    def __init__(self, get_response=None):
        #print('id(get_response):', id(get_response), get_response)
        self._get_response_none_deprecation(get_response)
        self.get_response = get_response
        self._async_check()
        super().__init__()

    def _async_check(self):
        """
        If get_response is a coroutine function, turns us into async mode so
        a thread is not consumed during a whole request.
        """
        if asyncio.iscoroutinefunction(self.get_response):
            # Mark the class as async-capable, but do the actual switch
            # inside __call__ to avoid swapping out dunder methods
            self._is_coroutine = asyncio.coroutines._is_coroutine

    def __call__(self, request):
        # self 是中间件
        # 在「响应处理对象」的处理过程中，会链式调用当前方法多次
        # 最终调用「应用对象」的 _get_response 方法获取请求所对应的视图函数及其参数


        if asyncio.iscoroutinefunction(self.get_response):
            return self.__acall__(request)
        response = None

        # 中间件对象处理请求
        print(f'\t【djang.utils.deprecation.MiddlewareMixin.__call__】{str(self.__class__):<70s}', end='')
        if hasattr(self, 'process_request'):
            print('process_request')
            response = self.process_request(request)
        else:
            print()

        # 下面这行代码创建「响应对象」，这行代码算是分界线，此前中间件处理请求，此后中间件处理响应
        # 最后一个中间件对象是没有 process_request 方法的，就调用 self.get_response 方法
        # 也就是 djang.core.handlers.base.BaseHandler._get_response 方法
        response = response or self.get_response(request)

        # 中间件对象处理响应
        print(f'\t【djang.utils.deprecation.MiddlewareMixin.__call__】{str(self.__class__):<70s}', end='')
        if hasattr(self, 'process_response'):
            print('process_response')
            response = self.process_response(request, response)
        else:
            print()

        #print('【django.utils.deprecation.MiddlewareMixin】response:', response)
        return response

    async def __acall__(self, request):
        """
        Async version of __call__ that is swapped in when an async request
        is running.
        """
        response = None
        if hasattr(self, 'process_request'):
            response = await sync_to_async(
                self.process_request,
                thread_sensitive=True,
            )(request)
        response = response or await self.get_response(request)
        if hasattr(self, 'process_response'):
            response = await sync_to_async(
                self.process_response,
                thread_sensitive=True,
            )(request, response)
        return response

    def _get_response_none_deprecation(self, get_response):
        if get_response is None:
            warnings.warn(
                'Passing None for the middleware get_response argument is '
                'deprecated.',
                RemovedInDjango40Warning, stacklevel=3,
            )
