from django.http import HttpResponse

from .loader import get_template, select_template


class ContentNotRenderedError(Exception):
    pass


class SimpleTemplateResponse(HttpResponse):
    rendering_attrs = ['template_name', 'context_data', '_post_render_callbacks']

    def __init__(self, template, context=None, content_type=None, status=None,
                 charset=None, using=None):
        #print('【django.template.response.SimpleTemplateResponse.__init__】')
        # 定义在项目中的视图类中的 template_name 属性值，字符串类型
        self.template_name = template
        # context 是字典对象：{'form': 表单类实例, 'view': 视图类实例，也就是 self}
        self.context_data = context

        self.using = using

        self._post_render_callbacks = []

        # 原注释翻译：
        # _request 将当前请求对象存储在知道请求的子类中，例如 TemplateResponse。  
        # 该属性在基类中定义，以最大程度地减少代码重复。  
        # 之所以称为 self._request，是因为 self.request 被 django.test.client.Client 覆盖。  
        # 与 template_name 和 context_data 不同，_request 不应被视为公共 API 的一部分。
        self._request = None

        # 调用父类 django.http.response.HttpResponse 的初始化方法，通常后面仨参数都是 None
        # 第一个参数 content 是空字符串，在这里只作占位之用，不写也行，因为它将被呈现的模板替换
        super().__init__('', content_type, status, charset=charset)

        # 原注释翻译：
        # _is_rendered 跟踪模板和上下文是否已准备好并赋值到最终响应中。  
        # 超级 __init__ 没有什么比将 self.content 设置为我们刚刚给它的空字符串更好的了，
        # 它错误地将 _is_rendered 设置为 True，因此在调用超级 __init__ 之后将其初始化为 False。
        # 我的注释：
        # 此属性定义在当前类中，用于判断 self.content 属性是否已经被赋值
        # 为 self.content 赋值的操作定义在当前类中，它是一个由 content.setter 装饰的方法
        # 调用此方法会将 self._is_render 属性变成 True 
        self._is_rendered = False

    def __getstate__(self):
        """
        Raise an exception if trying to pickle an unrendered response. Pickle
        only rendered data, not the data used to construct the response.
        """
        obj_dict = self.__dict__.copy()
        if not self._is_rendered:
            raise ContentNotRenderedError('The response content must be '
                                          'rendered before it can be pickled.')
        for attr in self.rendering_attrs:
            if attr in obj_dict:
                del obj_dict[attr]

        return obj_dict

    def resolve_template(self, template):
        # self 是「响应对象」，参数 template 是模板文件相对路径的字符串
        if isinstance(template, (list, tuple)):
            return select_template(template, using=self.using)
        elif isinstance(template, str):
            # 此函数定义在 django.template.loader 模块中，根据给定名称加载并返回「最终模板对象」
            # 该对象是 django.template.backends.django.Template 类的实例
            return get_template(template, using=self.using)
        else:
            return template

    def resolve_context(self, context):
        return context

    @property
    def rendered_content(self):
        """
        原注释翻译：
        返回 TemplateResponse 描述的模板和上下文的最新渲染内容。
        这并没有设置响应的最终内容。 
        要设置响应内容，必须调用 render 或使用此属性的值显式设置内容。
        """
        # self 是「响应对象」
        # 该 template 对象是 django.template.backends.django.Template 类的实例，叫做「最终模板对象」
        template = self.resolve_template(self.template_name)
        # self.context_data 是字典对象
        # self.resolve_context 的返回值就是参数 self.context_data
        context = self.resolve_context(self.context_data)
        
        # self._request 是「请求对象」，django.core.handlers.wsgi.WSGIRequest 类的实例
        # template 是「最终模板对象」
        # template.render 定义在 django.template.backends.django.Template 类中
        # 返回值是携带渲染完毕的模板文件内容字符串的「响应体字符串对象」
        # 该对象是 django.utils.safestring.SafeString 类的实例
        return template.render(context, self._request)

    def add_post_render_callback(self, callback):
        """Add a new post-rendering callback.

        If the response has already been rendered,
        invoke the callback immediately.
        """
        if self._is_rendered:
            callback(self)
        else:
            self._post_render_callbacks.append(callback)

    # 此方法在 django.core.handlers.base.BaseHandler._get_response 方法中被调用
    def render(self):
        """Render (thereby finalizing) the content of the response.

        If the content has already been rendered, this is a no-op.

        Return the baked response instance.
        """
        retval = self
        # 下面的 _is_rendered 属性定义在当前类的 __init__ 方法中，默认值是 False
        # 如果 self.content 被赋值过，那么该属性值就是 True ；否则下面的代码块将对其进行赋值操作
        if not self._is_rendered:
            # self 是「响应对象」，其 rendered_content 方法定义在当前类中
            # self.content 是携带渲染完毕的模板文件内容字符串的「响应体字符串对象」
            # 该对象是 django.utils.safestring.SafeString 类的实例
            self.content = self.rendered_content
            for post_callback in self._post_render_callbacks:
                newretval = post_callback(retval)
                if newretval is not None:
                    retval = newretval

        print('【django.template.response.SimpleTemplateResponse.renderd_content】'
              '获取渲染完毕的模板文件内容并赋值给「响应对象」的 content 属性')

        # 返回值是自身，也就是「响应对象」
        return retval

    @property
    def is_rendered(self):
        return self._is_rendered

    def __iter__(self):
        if not self._is_rendered:
            raise ContentNotRenderedError(
                'The response content must be rendered before it can be iterated over.'
            )
        return super().__iter__()

    @property
    def content(self):
        if not self._is_rendered:
            raise ContentNotRenderedError(
                'The response content must be rendered before it can be accessed.'
            )
        return super().content

    @content.setter
    def content(self, value):
        """Set the content for the response."""
        HttpResponse.content.fset(self, value)
        self._is_rendered = True


class TemplateResponse(SimpleTemplateResponse):
    # 当前类的父类是定义在当前模块的 SimpleTemplateResponse 类
    # 后者的父类是 django.http.response.HttpResponse 类
    # 后者的父类是 django.http.response.HttpResponseBase 类
    rendering_attrs = SimpleTemplateResponse.rendering_attrs + ['_request']

    def __init__(self, request, template, context=None, content_type=None,
                 status=None, charset=None, using=None):
        """
        关键参数说明：

        request  : 请求对象
        template : 字符串，前端模板文件名
        context  : 上下文字典对象
        """
        #print('【django.template.response.TemplateResponse.__init__】')
        super().__init__(template, context, content_type, status, charset, using)
        # 把「请求对象」赋值给「响应对象」的 _request 属性
        # 前者是 django.core.handlers.wsgi.WSGIRequest 类的实例
        self._request = request