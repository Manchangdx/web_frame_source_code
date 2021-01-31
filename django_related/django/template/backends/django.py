from importlib import import_module
from pkgutil import walk_packages

from django.apps import apps
from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.context import make_context
from django.template.engine import Engine
from django.template.library import InvalidTemplateLibrary

from .base import BaseEngine


class DjangoTemplates(BaseEngine):

    app_dirname = 'templates'

    def __init__(self, params):
        params = params.copy()
        options = params.pop('OPTIONS').copy()
        options.setdefault('autoescape', True)
        options.setdefault('debug', settings.DEBUG)
        options.setdefault('file_charset', 'utf-8')
        libraries = options.get('libraries', {})
        # 下面这个值是字典对象
        # {'cache': 'django.templatetags.cache', 
        #  'i18n': 'django.templatetags.i18n', 
        #  'l10n': 'django.templatetags.l10n', 
        #  'static': 'django.templatetags.static', 
        #  'tz': 'django.templatetags.tz', 
        #  'admin_list': 'django.contrib.admin.templatetags.admin_list', 
        #  'admin_modify': 'django.contrib.admin.templatetags.admin_modify', 
        #  'admin_urls': 'django.contrib.admin.templatetags.admin_urls', 
        #  'log': 'django.contrib.admin.templatetags.log'
        # }
        options['libraries'] = self.get_templatetag_libraries(libraries)
        super().__init__(params)
        self.engine = Engine(self.dirs, self.app_dirs, **options)

    def from_string(self, template_code):
        return Template(self.engine.from_string(template_code), self)

    def get_template(self, template_name):
        # self 是「模板引擎对象」
        try:
            # self.engine 是 django.template.engine.Engine 类的实例，叫做「引擎对象」
            # self.engine.get_template 的返回值是「模板对象」，django.template.base.Template 类的实例
            # 下面这个 Template 是定义在当前模块中的类，该类的实例被称为「最终模板对象」
            return Template(self.engine.get_template(template_name), self)
        except TemplateDoesNotExist as exc:
            reraise(exc, self)

    def get_templatetag_libraries(self, custom_libraries):
        """
        Return a collation of template tag libraries from installed
        applications and the supplied custom_libraries argument.
        """
        libraries = get_installed_libraries()
        libraries.update(custom_libraries)
        return libraries


class Template:

    def __init__(self, template, backend):
        # self 是「最终模板对象」
        # 参数 template 是「模板对象」，django.template.base.Template 类的实例
        # 参数 backend 是「模板引擎对象」，当前模块中的 DjangoTemplates 类的实例
        self.template = template
        self.backend = backend

    @property
    def origin(self):
        return self.template.origin     

    def render(self, context=None, request=None):
        # self 是「最终模板对象」
        # 参数 context 是字典对象：{'form': 表单类实例, 'view': 视图类实例}
        # 参数 request 是「请求对象」，django.core.handlers.wsgi.WSGIRequest 类的实例

        # django.template.context.RequestContext 类的实例，叫做「请求上下文对象」
        context = make_context(context, request, autoescape=self.backend.engine.autoescape)
        try:
            # 此处调用「模板对象」的 render 方法返回携带渲染完毕的模板文件内容字符串的「响应体字符串对象」
            # 该对象是 django.utils.safestring.SafeString 类的实例
            return self.template.render(context)
        except TemplateDoesNotExist as exc:
            reraise(exc, self.backend)


def copy_exception(exc, backend=None):
    """
    Create a new TemplateDoesNotExist. Preserve its declared attributes and
    template debug data but discard __traceback__, __context__, and __cause__
    to make this object suitable for keeping around (in a cache, for example).
    """
    backend = backend or exc.backend
    new = exc.__class__(*exc.args, tried=exc.tried, backend=backend, chain=exc.chain)
    if hasattr(exc, 'template_debug'):
        new.template_debug = exc.template_debug
    return new


def reraise(exc, backend):
    """
    Reraise TemplateDoesNotExist while maintaining template debug information.
    """
    new = copy_exception(exc, backend)
    raise new from exc


def get_installed_libraries():
    """
    Return the built-in template tag libraries and those from installed
    applications. Libraries are stored in a dictionary where keys are the
    individual module names, not the full module paths. Example:
    django.templatetags.i18n is stored as i18n.
    """
    libraries = {}
    candidates = ['django.templatetags']
    candidates.extend(
        '%s.templatetags' % app_config.name
        for app_config in apps.get_app_configs())

    for candidate in candidates:
        try:
            pkg = import_module(candidate)
        except ImportError:
            # No templatetags package defined. This is safe to ignore.
            continue

        if hasattr(pkg, '__path__'):
            for name in get_package_libraries(pkg):
                libraries[name[len(candidate) + 1:]] = name

    return libraries


def get_package_libraries(pkg):
    """
    Recursively yield template tag libraries defined in submodules of a
    package.
    """
    for entry in walk_packages(pkg.__path__, pkg.__name__ + '.'):
        try:
            module = import_module(entry[1])
        except ImportError as e:
            raise InvalidTemplateLibrary(
                "Invalid template library specified. ImportError raised when "
                "trying to load '%s': %s" % (entry[1], e)
            )

        if hasattr(module, 'register'):
            yield entry[1]
