# -*- coding: utf-8 -*-
"""Celery Application."""
from __future__ import absolute_import, print_function, unicode_literals

from celery import _state
from celery._state import (app_or_default, disable_trace, enable_trace,
                           pop_current_task, push_current_task)
from celery.local import Proxy

from .base import Celery
from .utils import AppPickler

__all__ = (
    'Celery', 'AppPickler', 'app_or_default', 'default_app',
    'bugreport', 'enable_trace', 'disable_trace', 'shared_task',
    'push_current_task', 'pop_current_task',
)

#: Proxy always returning the app set as default.
default_app = Proxy(lambda: _state.default_app)


def bugreport(app=None):
    """Return information useful in bug reports."""
    return (app or _state.get_current_app()).bugreport()


def shared_task(*args, **kwargs):
    """任务装饰器，用于创建定时任务

    有两种方式：
        1. 当前函数本身作为装饰器新建函数
        2. 当前函数的调用返回值作为装饰器新建函数，这种情况的话调用当前函数时所有的参数必须是关键字参数
    """
    def create_shared_task(**options):

        def __inner(fun):
            name = options.get('name')

            # 下面的代码仅仅是创建一个匿名函数，将其作为参数添加到 celery._state._on_app_finalizers 集合中
            #
            # 在需要的时候，首次调用「任务控制器」的 tasks 属性，会调用「任务控制器」的 finalize 方法
            # 在这个 finalize 方法中通过一系列调用
            # 最后将「任务控制器」自身作为参数依次调用 celery._state._on_app_finalizers 集合中的各个匿名函数
            # 其实就是调用「任务控制器」自身的 _task_from_func 方法将 func 放到「任务控制器」的 _task 字典中
            #
            # 例如 fun 是 shiyanlou.apps.service.tasks 模块中的定时任务函数 haha
            #    key 就是字符串 'shiyanlou.apps.service.tasks.haha'
            #    value 就是 celery.app.task.Task 类的实例，其 run 属性就是 fun
            _state.connect_on_app_finalize(
                lambda app: app._task_from_fun(fun, **options)
            )

            for app in _state._get_active_apps():
                # 每个「任务控制器」都有一个 finalized 属性，初始值是 False
                # 当「任务控制器」调用了 tasks 属性获取其任务列表时，就会将自身的 finalized 属性设为 True
                # 如果该属性值是 False 则不需要做任何事，否则就直接把任务加到「任务控制器」的任务列表里
                if app.finalized:
                    with app._finalize_mutex:
                        app._task_from_fun(fun, **options)

            def task_by_cons():
                app = _state.get_current_app()
                return app.tasks[
                    # app.gen_task_name 生成 fun 的绝对路径
                    # 例如 fun 是 shiyanlou.apps.service.tasks 模块中的定时任务函数 haha
                    # 那么返回值就是字符串 'shiyanlou.apps.service.tasks.haha'
                    name or app.gen_task_name(fun.__name__, fun.__module__)
                ]

            # 被 shared_task 装饰的函数变成了这个，celery.local.Proxy 类的实例，这是一个代理对象
            return Proxy(task_by_cons)
        return __inner

    if len(args) == 1 and callable(args[0]):
        return create_shared_task(**kwargs)(args[0])
    return create_shared_task(*args, **kwargs)
