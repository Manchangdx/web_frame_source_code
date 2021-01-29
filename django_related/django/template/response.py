from django.http import HttpResponse

from .loader import get_template, select_template


class ContentNotRenderedError(Exception):
    pass


class SimpleTemplateResponse(HttpResponse):
    rendering_attrs = ['template_name', 'context_data', '_post_render_callbacks']

    def __init__(self, template, context=None, content_type=None, status=None,
                 charset=None, using=None):
        # It would seem obvious to call these next two members 'template' and
        # 'context', but those names are reserved as part of the test Client
        # API. To avoid the name collision, we use different names.
        print('【django.template.response.SimpleTemplateResponse.__init__】')
        self.template_name = template
        # context 是字典对象：{'form': 表单类实例, 'view': 视图类实例，也就是 self}
        self.context_data = context

        self.using = using

        self._post_render_callbacks = []

        # _request stores the current request object in subclasses that know
        # about requests, like TemplateResponse. It's defined in the base class
        # to minimize code duplication.
        # It's called self._request because self.request gets overwritten by
        # django.test.client.Client. Unlike template_name and context_data,
        # _request should not be considered part of the public API.
        self._request = None

        # content argument doesn't make sense here because it will be replaced
        # with rendered template so we always pass empty string in order to
        # prevent errors and provide shorter signature.
        super().__init__('', content_type, status, charset=charset)

        # _is_rendered tracks whether the template and context has been baked
        # into a final response.
        # Super __init__ doesn't know any better than to set self.content to
        # the empty string we just gave it, which wrongly sets _is_rendered
        # True, so we initialize it to False after the call to super __init__.
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
        """Return the freshly rendered content for the template and context
        described by the TemplateResponse.

        This *does not* set the final content of the response. To set the
        response content, you must either call render(), or set the
        content explicitly using the value of this property.
        """
        # 该对象是 django.template.backends.django.Template 类的实例，叫做「最终模板对象」
        template = self.resolve_template(self.template_name)
        # self.context_data 是字典对象：{'form': 表单类实例, 'view': 视图类实例，也就是 self}
        # self.resolve_context 的返回值就是参数
        context = self.resolve_context(self.context_data)
        
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
        if not self._is_rendered:
            # self 是「响应对象」，其 rendered_content 方法定义在当前类中
            self.content = self.rendered_content
            for post_callback in self._post_render_callbacks:
                newretval = post_callback(retval)
                if newretval is not None:
                    retval = newretval
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
        print('【django.template.response.TemplateResponse.__init__】')
        super().__init__(template, context, content_type, status, charset, using)
        self._request = request
        #print('------')
        #print([i for i in dir(self) if not i.startswith('__')])
        #print('------')