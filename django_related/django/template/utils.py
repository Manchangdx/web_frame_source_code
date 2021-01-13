import functools
from collections import Counter
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import cached_property
from django.utils.module_loading import import_string


class InvalidTemplateEngineError(ImproperlyConfigured):
    pass


class EngineHandler:
    def __init__(self, templates=None):
        """
        templates is an optional list of template engine definitions
        (structured like settings.TEMPLATES).
        """
        self._templates = templates
        self._engines = {}

    @cached_property
    def templates(self):
        if self._templates is None:
            self._templates = settings.TEMPLATES

        templates = {}
        backend_names = []
        for tpl in self._templates:
            try:
                # This will raise an exception if 'BACKEND' doesn't exist or
                # isn't a string containing at least one dot.
                default_name = tpl['BACKEND'].rsplit('.', 2)[-2]
                #print('【django.template.utils.EngineHandler.templates】default_name:', default_name)
            except Exception:
                invalid_backend = tpl.get('BACKEND', '<not defined>')
                raise ImproperlyConfigured(
                    "Invalid BACKEND for a template engine: {}. Check "
                    "your TEMPLATES setting.".format(invalid_backend))

            tpl = {
                'NAME': default_name,
                'DIRS': [],
                'APP_DIRS': False,
                'OPTIONS': {},
                **tpl,
            }

            templates[tpl['NAME']] = tpl
            backend_names.append(tpl['NAME'])

        counts = Counter(backend_names)
        duplicates = [alias for alias, count in counts.most_common() if count > 1]
        if duplicates:
            raise ImproperlyConfigured(
                "Template engine aliases aren't unique, duplicates: {}. "
                "Set a unique NAME for each engine in settings.TEMPLATES."
                .format(", ".join(duplicates)))

        return templates

    def __getitem__(self, alias):
        print('【django.template.utils.EngineHandler.__getitem__】alias:', alias)
        try:
            return self._engines[alias]
        except KeyError:
            try:
                params = self.templates[alias]
            except KeyError:
                raise InvalidTemplateEngineError(
                    "Could not find config for '{}' "
                    "in settings.TEMPLATES".format(alias))

            # If importing or initializing the backend raises an exception,
            # self._engines[alias] isn't set and this code may get executed
            # again, so we must preserve the original params. See #24265.
            params = params.copy()
            # 默认情况下，backend 的值是 'django.template.backends.django.DjangoTemplates'
            backend = params.pop('BACKEND')
            # 默认情况下，engine_cls 是 backend 对应的类
            engine_cls = import_string(backend)
            # 默认情况下，engine 就是 django.template.backends.django.DjangoTemplates 类的实例
            engine = engine_cls(params)

            self._engines[alias] = engine
            return engine

    def __iter__(self):
        return iter(self.templates)

    def all(self):
        # 返回列表
        # 默认情况下，列表中只有一个 django.template.backends.django.DjangoTemplates 类的实例
        # 该实例被称为「模板引擎对象」
        return [self[alias] for alias in self]


# 此装饰器用于实现函数缓存功能
# 当函数被调用时，会将参数作为 key 返回值作为 value 存到函数的缓存区域
# 下次以同样的参数调用函数时，直接在函数的缓存区域找到对应的 value 并返回
@functools.lru_cache()
def get_app_template_dirs(dirname):
    """
    Return an iterable of paths of directories to load app templates from.
    翻译：返回目录的可迭代路径，以从中加载应用程序模板。
    dirname is the name of the subdirectory containing templates inside
    installed applications.
    翻译：dirname 是包含已安装的应用程序中的模板的子目录的名称。
    """
    # apps 是定义在 django.apps.registry.Apps 类的实例
    # 其 get_app_configs 方法的返回值是类列表对象：
    # [
    #  <AdminConfig: admin>,                其 path 属性值：'.../site-packages/django/contrib/admin'
    #  <AuthConfig: auth>,                  其 path 属性值：'.../site-packages/django/contrib/auth'
    #  <ContentTypesConfig: contenttypes>,  其 path 属性值：'.../site-packages/django/contrib/contenttypes' 
    #  <SessionsConfig: sessions>,          其 path 属性值：'.../site-packages/django/contrib/sessions'
    #  <MessagesConfig: messages>,          其 path 属性值：'.../site-packages/django/contrib/messages'
    #  <StaticFilesConfig: staticfiles>,    其 path 属性值：'.../site-packages/django/contrib/staticfiles'
    #  <AppConfig: home>                    其 path 属性值：'.../项目主目录/应用目录'
    # ]
    # 下面这个列表里面是上面列表中各个对象的 path 属性值 + dirname 后的 PosixPath 类的实例
    # 类似这样：PosixPath('/.../site-packages/django/contrib/admin/templates')
    # 关于其中斜线操作符的用法参见 https://docs.python.org/zh-cn/3/library/pathlib.html
    template_dirs = [
        Path(app_config.path) / dirname
        for app_config in apps.get_app_configs()
        if app_config.path and (Path(app_config.path) / dirname).is_dir()
    ]
    # Immutable return value because it will be cached and shared by callers.
    return tuple(template_dirs)
