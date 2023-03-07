import os
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

MODELS_MODULE_NAME = 'models'


class AppConfig:
    """Class representing a Django application and its configuration."""

    def __init__(self, app_name, app_module):
        # Full Python path to the application e.g. 'django.contrib.admin'.
        self.name = app_name

        # Root module for the application e.g. <module 'django.contrib.admin'
        # from 'django/contrib/admin/__init__.py'>.
        self.module = app_module

        # Reference to the Apps registry that holds this AppConfig. Set by the
        # registry when it registers the AppConfig instance.
        self.apps = None

        # The following attributes could be defined at the class level in a
        # subclass, hence the test-and-set pattern.

        # Last component of the Python path to the application e.g. 'admin'.
        # This value must be unique across a Django project.
        if not hasattr(self, 'label'):
            self.label = app_name.rpartition(".")[2]

        # Human-readable name for the application e.g. "Admin".
        if not hasattr(self, 'verbose_name'):
            self.verbose_name = self.label.title()

        # Filesystem path to the application directory e.g.
        # '/path/to/django/contrib/admin'.
        if not hasattr(self, 'path'):
            self.path = self._path_from_module(app_module)

        # Module containing models e.g. <module 'django.contrib.admin.models'
        # from 'django/contrib/admin/models.py'>. Set by import_models().
        # None if the application doesn't have a models module.
        self.models_module = None

        # Mapping of lowercase model names to model classes. Initially set to
        # None to prevent accidental access before import_models() runs.
        self.models = None

    def __repr__(self):
        return '<%s: %s>' % (self.__class__.__name__, self.label)

    def _path_from_module(self, module):
        """Attempt to determine app's filesystem path from its module."""
        # See #21874 for extended discussion of the behavior of this method in
        # various cases.
        # Convert paths to list because Python's _NamespacePath doesn't support
        # indexing.
        paths = list(getattr(module, '__path__', []))
        if len(paths) != 1:
            filename = getattr(module, '__file__', None)
            if filename is not None:
                paths = [os.path.dirname(filename)]
            else:
                # For unknown reasons, sometimes the list returned by __path__
                # contains duplicates that must be removed (#25246).
                paths = list(set(paths))
        if len(paths) > 1:
            raise ImproperlyConfigured(
                "The app module %r has multiple filesystem locations (%r); "
                "you must configure this app with an AppConfig subclass "
                "with a 'path' class attribute." % (module, paths))
        elif not paths:
            raise ImproperlyConfigured(
                "The app module %r has no filesystem location, "
                "you must configure this app with an AppConfig subclass "
                "with a 'path' class attribute." % (module,))
        return paths[0]

    @classmethod
    def create(cls, entry):
        """工厂函数，根据应用的路径字符串创建应用
        """
        # 现假设 entry = 'shiyanlou.apps.ide_plugin.apps.PluginConfig'

        try:
            # 下面这个会失败
            module = import_module(entry)

        except ImportError:
            module = None

            # mod_path = 'shiyanlou.apps.ide_plugin.apps'
            # cls_name = 'PluginConfig'
            mod_path, _, cls_name = entry.rpartition('.')

            if not mod_path:
                raise

        else:
            try:
                entry = module.default_app_config
            except AttributeError:
                conf = cls(entry, module)
                return conf
            else:
                mod_path, _, cls_name = entry.rpartition('.')

        # 应用的 apps 模块
        mod = import_module(mod_path)
        try:
            # 应用的 apps 模块中的配置类
            cls = getattr(mod, cls_name)
        except AttributeError:
            if module is None:
                candidates = sorted(
                    repr(name) for name, candidate in mod.__dict__.items()
                    if isinstance(candidate, type) and
                    issubclass(candidate, AppConfig) and
                    candidate is not AppConfig
                )
                if candidates:
                    raise ImproperlyConfigured(
                        "'%s' does not contain a class '%s'. Choices are: %s."
                        % (mod_path, cls_name, ', '.join(candidates))
                    )
                import_module(entry)
            else:
                raise

        if not issubclass(cls, AppConfig):
            raise ImproperlyConfigured(
                "'%s' isn't a subclass of AppConfig." % entry)

        try:
            # 应用的 apps 模块中的配置类的 name 属性值
            app_name = cls.name
        except AttributeError:
            raise ImproperlyConfigured(
                "'%s' must supply a name attribute." % entry)

        try:
            # 应用
            app_module = import_module(app_name)
        except ImportError:
            raise ImproperlyConfigured(
                "Cannot import '%s'. Check that '%s.%s.name' is correct." % (
                    app_name, mod_path, cls_name,
                )
            )

        # cls 就是当前所在类 AppConfig
        return cls(app_name, app_module)

    def get_model(self, model_name, require_ready=True):
        """
        Return the model with the given case-insensitive model_name.

        Raise LookupError if no model exists with this name.
        """
        if require_ready:
            self.apps.check_models_ready()
        else:
            self.apps.check_apps_ready()
        try:
            return self.models[model_name.lower()]
        except KeyError:
            raise LookupError(
                "App '%s' doesn't have a '%s' model." % (self.label, model_name))

    def get_models(self, include_auto_created=False, include_swapped=False):
        """
        Return an iterable of models.

        By default, the following models aren't included:

        - auto-created models for many-to-many relations without
          an explicit intermediate table,
        - models that have been swapped out.

        Set the corresponding keyword argument to True to include such models.
        Keyword arguments aren't documented; they're a private API.
        """
        self.apps.check_models_ready()
        for model in self.models.values():
            if model._meta.auto_created and not include_auto_created:
                continue
            if model._meta.swapped and not include_swapped:
                continue
            yield model

    def import_models(self):
        # Dictionary of models for this app, primarily maintained in the
        # 'all_models' attribute of the Apps this AppConfig is attached to.
        self.models = self.apps.all_models[self.label]

        if module_has_submodule(self.module, MODELS_MODULE_NAME):
            models_module_name = '%s.%s' % (self.name, MODELS_MODULE_NAME)
            self.models_module = import_module(models_module_name)

    def ready(self):
        """
        Override this method in subclasses to run code when Django starts.
        """
