from django.utils.version import get_version

VERSION = (3, 1, 4, 'final', 0)

__version__ = get_version(VERSION)


def setup(set_prefix=True):
    """
    Configure the settings (this happens as a side effect of accessing the
    first setting), configure logging and populate the app registry.
    Set the thread-local urlresolvers script prefix if `set_prefix` is True.
    """
    import threading
    ct = threading.current_thread()
    print('【django.__init__.setup】当前线程：', ct.name, ct.ident)

    from django.apps import apps
    from django.conf import settings
    from django.urls import set_script_prefix
    from django.utils.log import configure_logging

    configure_logging(settings.LOGGING_CONFIG, settings.LOGGING)
    if set_prefix:
        set_script_prefix(
            '/' if settings.FORCE_SCRIPT_NAME is None else settings.FORCE_SCRIPT_NAME
        )
    # 这个 apps 是 django.apps.registry.Apps 类的实例
    # 这里调用 populate 方法将项目的配置文件中的 INSTALLED_APPS 中的
    # 应用程序都放到实例自身的 app_configs 属性字典中
    # key 就是 INSTALLED_APPS 列表里的字符串
    # value 是 django.apps.config.AppConfig 类的实例，此实例就是应用对象
    apps.populate(settings.INSTALLED_APPS)