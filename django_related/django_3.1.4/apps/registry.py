import functools
import sys
import threading
import warnings
from collections import Counter, defaultdict
from functools import partial

from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

from .config import AppConfig


class Apps:
    """
    A registry that stores the configuration of installed applications.

    It also keeps track of models, e.g. to provide reverse relations.
    """

    def __init__(self, installed_apps=()):
        # self 是「应用收集对象」apps
        if installed_apps is None and hasattr(sys.modules[__name__], 'apps'):
            raise RuntimeError("You must supply an installed_apps argument.")

        # 通常调用字典对象的 key 获取对应的 value 时，如果 key 不存在，会报错
        # 这个 defaultdict 的返回值就是一个类字典对象，调用不存在的 key 时不会报错
        # 对应的 value 就是参数的调用
        # 例如参数是 dict ，value 就是空字典
        # 参数是 list ，value 就是空列表
        # 参数是 int ，value 就是 0
        self.all_models = defaultdict(dict)

        # 在这个 self.app_configs 属性字典里：
        # key 是项目配置文件中的 INSTALLED_APPS 列表里的字符串，它们指向的是一个个应用对象的包
        # value 是「应用对象」，也就是 django.apps.config.AppConfig 类或其子类的实例
        # 以 'django.contrib.messages' 为例说下 value 的属性：
        #     该实例的 module 属性值就是对应的应用的包对象，就是 ...django/contrib/messages/__init__.py 对应的模块
        #     该实例的 name 属性值就是一个字符串 'django.contrib.messages' 
        #     该实例的 label 属性值也是一个字符串 'messages'
        # 这个 self.app_configs 字典的键值对都是在 self.populate 方法中添加的
        self.app_configs = {}

        # Stack of app_configs. Used to store the current state in
        # set_available_apps and set_installed_apps.
        self.stored_app_configs = []

        # Whether the registry is populated.
        self.apps_ready = self.models_ready = self.ready = False
        # For the autoreloader.
        self.ready_event = threading.Event()

        # Lock for thread-safe population.
        self._lock = threading.RLock()
        self.loading = False

        # Maps ("app_label", "modelname") tuples to lists of functions to be
        # called when the corresponding model is ready. Used by this class's
        # `lazy_model_operation()` and `do_pending_operations()` methods.
        self._pending_operations = defaultdict(list)

        # Populate apps and models, unless it's the master registry.
        if installed_apps is not None:
            self.populate(installed_apps)

    def populate(self, installed_apps=None):
        """
        将项目配置项 INSTALLED_APPS 中的应用条件到 self.app_configs 字典中。
        它是线程安全和幂等的，但不可重复执行。
        """
        if self.ready:
            return

        # populate() might be called by two threads in parallel on servers
        # that create threads before initializing the WSGI callable.
        with self._lock:
            if self.ready:
                return

            # An RLock prevents other threads from entering this section. The
            # compare and set operation below is atomic.
            if self.loading:
                # Prevent reentrant calls to avoid running AppConfig.ready()
                # methods twice.
                raise RuntimeError("populate() isn't reentrant")
            self.loading = True

            import threading
            ct = threading.current_thread()
            #print('【django.apps.registry.Apps.populate】当前线程：', ct.name, ct.ident)

            # Phase 1: initialize app configs and import app modules.
            for entry in installed_apps:
                if isinstance(entry, AppConfig):
                    app_config = entry
                else:
                    # app_config 就是 django.apps.config.AppConfig 类或其子类的实例，称之为「应用对象」
                    app_config = AppConfig.create(entry)
                    #print('【django.apps.registry.Apps.populate】app_config.module:\n\t', app_config.module)
                    #print('\t app_config.name:', app_config.name, type(app_config.name))
                    #print('\t app_config.label:', app_config.label, type(app_config.label))
                if app_config.label in self.app_configs:
                    raise ImproperlyConfigured(
                        "Application labels aren't unique, "
                        "duplicates: %s" % app_config.label)

                self.app_configs[app_config.label] = app_config
                app_config.apps = self

            # Check for duplicate app names.
            counts = Counter(
                app_config.name for app_config in self.app_configs.values())
            duplicates = [
                name for name, count in counts.most_common() if count > 1]
            if duplicates:
                raise ImproperlyConfigured(
                    "Application names aren't unique, "
                    "duplicates: %s" % ", ".join(duplicates))

            self.apps_ready = True

            # Phase 2: import models modules.
            for app_config in self.app_configs.values():
                app_config.import_models()

            self.clear_cache()

            self.models_ready = True

            # 调用「应用对象」的 ready 方法
            # 把应用中的某些用于检查的函数添加到「“检查对象”收集器 registry」的 registered_checks 属性中
            # 该属性是一个集合，里面就是检查函数，函数名都是以 check 开头的
            for app_config in self.get_app_configs():
                app_config.ready()

            self.ready = True
            self.ready_event.set()

    def check_apps_ready(self):
        """此方法用于保证项目的配置模块中有 INSTALLED_APPS 这一配置项
        """
        if not self.apps_ready:
            from django.conf import settings

            # If "not ready" is due to unconfigured settings, accessing
            # INSTALLED_APPS raises a more helpful ImproperlyConfigured
            # exception.
            settings.INSTALLED_APPS
            raise AppRegistryNotReady("Apps aren't loaded yet.")

    def check_models_ready(self):
        """Raise an exception if all models haven't been imported yet."""
        if not self.models_ready:
            raise AppRegistryNotReady("Models aren't loaded yet.")

    def get_app_configs(self):
        """Import applications and return an iterable of app configs."""
        self.check_apps_ready()
        return self.app_configs.values()

    def get_app_config(self, app_label):
        """
        Import applications and returns an app config for the given label.

        Raise LookupError if no application exists with this label.
        """
        self.check_apps_ready()
        try:
            return self.app_configs[app_label]
        except KeyError:
            message = "No installed app with label '%s'." % app_label
            for app_config in self.get_app_configs():
                if app_config.name == app_label:
                    message += " Did you mean '%s'?" % app_config.label
                    break
            raise LookupError(message)

    # This method is performance-critical at least for Django's test suite.
    @functools.lru_cache(maxsize=None)
    def get_models(self, include_auto_created=False, include_swapped=False):
        """
        返回一个列表，里面是各个应用中定义的映射类。

        默认情况下，也就是两个默认参数使得返回值列表中不包括如下两种映射类（或者叫“模型类”）：
            1、自动建立的多对多关系模型，而没有明确的中间表；
            2、已经被 swapped out 的模型类。

        Set the corresponding keyword argument to True to include such models.
        """
        # self 是「应用收集对象」
        # 此方法用于保证 self.populate 一定被执行过，也就是收集过应用对象了
        self.check_models_ready()

        result = []
        # self 是「应用收集对象 apps」
        # self.app_configs 是字典，其 value 是「应用对象」，也就是 django.apps.config.AppConfig 类或其子类的实例
        for app_config in self.app_configs.values():
            # 此 get_models 方法定义在 django.apps.config.AppConfig 类中，其返回值是生成器
            # 生成器里面是各个应用中定义的映射类（也叫做“模型类”）
            g_models = app_config.get_models(include_auto_created, include_swapped)
            # 将生成器中的元素（也就是各个应用中定义的映射类）依次添加到列表里头
            result.extend(g_models)
        # 返回值是列表，列表里是各个应用中定义的映射类
        return result

    def get_model(self, app_label, model_name=None, require_ready=True):
        """
        Return the model matching the given app_label and model_name.

        As a shortcut, app_label may be in the form <app_label>.<model_name>.

        model_name is case-insensitive.

        Raise LookupError if no application exists with this label, or no
        model exists with this name in the application. Raise ValueError if
        called with a single argument that doesn't contain exactly one dot.
        """
        if require_ready:
            self.check_models_ready()
        else:
            self.check_apps_ready()

        if model_name is None:
            app_label, model_name = app_label.split('.')

        # 这个变量是「应用对象」，django.apps.config.AppConfig 类或其子类的实例
        app_config = self.get_app_config(app_label)

        if not require_ready and app_config.models is None:
            app_config.import_models()

        return app_config.get_model(model_name, require_ready=require_ready)

    def register_model(self, app_label, model):
        # Since this method is called when models are imported, it cannot
        # perform imports because of the risk of import loops. It mustn't
        # call get_app_config().
        model_name = model._meta.model_name
        app_models = self.all_models[app_label]
        if model_name in app_models:
            if (model.__name__ == app_models[model_name].__name__ and
                    model.__module__ == app_models[model_name].__module__):
                warnings.warn(
                    "Model '%s.%s' was already registered. "
                    "Reloading models is not advised as it can lead to inconsistencies, "
                    "most notably with related models." % (app_label, model_name),
                    RuntimeWarning, stacklevel=2)
            else:
                raise RuntimeError(
                    "Conflicting '%s' models in application '%s': %s and %s." %
                    (model_name, app_label, app_models[model_name], model))
        app_models[model_name] = model
        self.do_pending_operations(model)
        self.clear_cache()

    def is_installed(self, app_name):
        """
        Check whether an application with this name exists in the registry.

        app_name is the full name of the app e.g. 'django.contrib.admin'.
        """
        self.check_apps_ready()
        return any(ac.name == app_name for ac in self.app_configs.values())

    def get_containing_app_config(self, object_name):
        """
        Look for an app config containing a given object.

        object_name is the dotted Python path to the object.

        Return the app config for the inner application in case of nesting.
        Return None if the object isn't in any registered app config.
        """
        self.check_apps_ready()
        candidates = []
        for app_config in self.app_configs.values():
            if object_name.startswith(app_config.name):
                subpath = object_name[len(app_config.name):]
                if subpath == '' or subpath[0] == '.':
                    candidates.append(app_config)
        if candidates:
            return sorted(candidates, key=lambda ac: -len(ac.name))[0]

    def get_registered_model(self, app_label, model_name):
        """
        Similar to get_model(), but doesn't require that an app exists with
        the given app_label.

        It's safe to call this method at import time, even while the registry
        is being populated.
        """
        model = self.all_models[app_label].get(model_name.lower())
        if model is None:
            raise LookupError(
                "Model '%s.%s' not registered." % (app_label, model_name))
        return model

    @functools.lru_cache(maxsize=None)
    def get_swappable_settings_name(self, to_string):
        """
        For a given model string (e.g. "auth.User"), return the name of the
        corresponding settings name if it refers to a swappable model. If the
        referred model is not swappable, return None.

        This method is decorated with lru_cache because it's performance
        critical when it comes to migrations. Since the swappable settings don't
        change after Django has loaded the settings, there is no reason to get
        the respective settings attribute over and over again.
        """
        for model in self.get_models(include_swapped=True):
            swapped = model._meta.swapped
            # Is this model swapped out for the model given by to_string?
            if swapped and swapped == to_string:
                return model._meta.swappable
            # Is this model swappable and the one given by to_string?
            if model._meta.swappable and model._meta.label == to_string:
                return model._meta.swappable
        return None

    def set_available_apps(self, available):
        """
        Restrict the set of installed apps used by get_app_config[s].

        available must be an iterable of application names.

        set_available_apps() must be balanced with unset_available_apps().

        Primarily used for performance optimization in TransactionTestCase.

        This method is safe in the sense that it doesn't trigger any imports.
        """
        available = set(available)
        installed = {app_config.name for app_config in self.get_app_configs()}
        if not available.issubset(installed):
            raise ValueError(
                "Available apps isn't a subset of installed apps, extra apps: %s"
                % ", ".join(available - installed)
            )

        self.stored_app_configs.append(self.app_configs)
        self.app_configs = {
            label: app_config
            for label, app_config in self.app_configs.items()
            if app_config.name in available
        }
        self.clear_cache()

    def unset_available_apps(self):
        """Cancel a previous call to set_available_apps()."""
        self.app_configs = self.stored_app_configs.pop()
        self.clear_cache()

    def set_installed_apps(self, installed):
        """
        Enable a different set of installed apps for get_app_config[s].

        installed must be an iterable in the same format as INSTALLED_APPS.

        set_installed_apps() must be balanced with unset_installed_apps(),
        even if it exits with an exception.

        Primarily used as a receiver of the setting_changed signal in tests.

        This method may trigger new imports, which may add new models to the
        registry of all imported models. They will stay in the registry even
        after unset_installed_apps(). Since it isn't possible to replay
        imports safely (e.g. that could lead to registering listeners twice),
        models are registered when they're imported and never removed.
        """
        if not self.ready:
            raise AppRegistryNotReady("App registry isn't ready yet.")
        self.stored_app_configs.append(self.app_configs)
        self.app_configs = {}
        self.apps_ready = self.models_ready = self.loading = self.ready = False
        self.clear_cache()
        self.populate(installed)

    def unset_installed_apps(self):
        """Cancel a previous call to set_installed_apps()."""
        self.app_configs = self.stored_app_configs.pop()
        self.apps_ready = self.models_ready = self.ready = True
        self.clear_cache()

    def clear_cache(self):
        """
        Clear all internal caches, for methods that alter the app registry.

        This is mostly used in tests.
        """
        # Call expire cache on each model. This will purge
        # the relation tree and the fields cache.
        self.get_models.cache_clear()
        if self.ready:
            # Circumvent self.get_models() to prevent that the cache is refilled.
            # This particularly prevents that an empty value is cached while cloning.
            for app_config in self.app_configs.values():
                for model in app_config.get_models(include_auto_created=True):
                    model._meta._expire_cache()

    def lazy_model_operation(self, function, *model_keys):
        """
        Take a function and a number of ("app_label", "modelname") tuples, and
        when all the corresponding models have been imported and registered,
        call the function with the model classes as its arguments.

        The function passed to this method must accept exactly n models as
        arguments, where n=len(model_keys).
        """
        # Base case: no arguments, just execute the function.
        if not model_keys:
            function()
        # Recursive case: take the head of model_keys, wait for the
        # corresponding model class to be imported and registered, then apply
        # that argument to the supplied function. Pass the resulting partial
        # to lazy_model_operation() along with the remaining model args and
        # repeat until all models are loaded and all arguments are applied.
        else:
            next_model, *more_models = model_keys

            # This will be executed after the class corresponding to next_model
            # has been imported and registered. The `func` attribute provides
            # duck-type compatibility with partials.
            def apply_next_model(model):
                next_function = partial(apply_next_model.func, model)
                self.lazy_model_operation(next_function, *more_models)
            apply_next_model.func = function

            # If the model has already been imported and registered, partially
            # apply it to the function now. If not, add it to the list of
            # pending operations for the model, where it will be executed with
            # the model class as its sole argument once the model is ready.
            try:
                model_class = self.get_registered_model(*next_model)
            except LookupError:
                self._pending_operations[next_model].append(apply_next_model)
            else:
                apply_next_model(model_class)

    def do_pending_operations(self, model):
        """
        Take a newly-prepared model and pass it to each function waiting for
        it. This is called at the very end of Apps.register_model().
        """
        key = model._meta.app_label, model._meta.model_name
        for function in self._pending_operations.pop(key, []):
            function(model)


# 应用程序启动的时候就会创建该对象，并且只创建一次
# 这个姑且叫做「应用收集对象」
apps = Apps(installed_apps=None)
