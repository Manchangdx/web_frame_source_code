from . import engines
from .exceptions import TemplateDoesNotExist


def get_template(template_name, using=None):
    """
    Load and return a template for the given name.
    Raise TemplateDoesNotExist if no such template exists.
    翻译：根据给定名称加载并返回模板对象。如果不存在对应的模板，抛出 TemplateDoesNotExist 异常。

    :template_name: 模板文件相对路径字符串
    """
    print('【django.template.loader.get_template】获取「最终模板对象」')
    chain = []
    # 此函数定义在当前模块最下面，返回值是「模板引擎列表对象」
    engines = _engine_list(using)

    for engine in engines:
        try:
            # engine 是 django.template.backends.django.DjangoTemplates 类的实例，叫做「模板引擎对象」
            # 此处返回 django.template.backends.django.Template 类的实例，即「最终模板对象」
            return engine.get_template(template_name)
        except TemplateDoesNotExist as e:
            chain.append(e)

    raise TemplateDoesNotExist(template_name, chain=chain)


def select_template(template_name_list, using=None):
    """
    Load and return a template for one of the given names.

    Try names in order and return the first template found.

    Raise TemplateDoesNotExist if no such template exists.
    """
    if isinstance(template_name_list, str):
        raise TypeError(
            'select_template() takes an iterable of template names but got a '
            'string: %r. Use get_template() if you want to load a single '
            'template by name.' % template_name_list
        )

    chain = []
    engines = _engine_list(using)
    for template_name in template_name_list:
        for engine in engines:
            try:
                return engine.get_template(template_name)
            except TemplateDoesNotExist as e:
                chain.append(e)

    if template_name_list:
        raise TemplateDoesNotExist(', '.join(template_name_list), chain=chain)
    else:
        raise TemplateDoesNotExist("No template names provided")


def render_to_string(template_name, context=None, request=None, using=None):
    """
    Load a template and render it with a context. Return a string.
    翻译：加载模板并使用上下文呈现它。返回一个字符串。

    :template_name: 可能是模板文件的相对路径字符串或者是一个字符串列表
    :request: 请求对象
    """
    if isinstance(template_name, (list, tuple)):
        template = select_template(template_name, using=using)
    else:
        # 此函数定义在当前模块中，返回「最终模板对象」
        # 该对象是 django.template.backends.django.DjangoTemplates 类的实例
        template = get_template(template_name, using=using)
    # 此处调用「最终模板对象」的 render 方法返回携带渲染完毕的模板文件内容字符串的「响应体字符串对象」
    # 该对象是 django.utils.safestring.SafeString 类的实例
    return template.render(context, request)


def _engine_list(using=None):
    # engines 是 django.template.utils.EngineHandler 类的实例
    # 下面的返回值是列表，列表里面是「模板引擎对象」
    # 即 django.template.backends.django.DjangoTemplates 类的实例
    return engines.all() if using is None else [engines[using]]
