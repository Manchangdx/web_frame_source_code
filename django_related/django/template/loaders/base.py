from django.template import Template, TemplateDoesNotExist


class Loader:

    def __init__(self, engine):
        # self 是「模板加载对象」
        # engine 是「引擎对象」，django.template.engine.Engine 类的实例
        self.engine = engine

    def get_template(self, template_name, skip=None):
        """
        Call self.get_template_sources() and return a Template object for
        the first template matching template_name. If skip is provided, ignore
        template origins in skip. This is used to avoid recursion during
        template extending.
        """
        tried = []

        for origin in self.get_template_sources(template_name):
            # origin 是 django.template.base.Origin 类的实例
            if skip is not None and origin in skip:
                tried.append((origin, 'Skipped'))
                continue

            try:
                # 此方法定义在 django.template.loaders.filesystem.Loader 类中
                # 其返回值是视图函数指定的模板文件中的全部内容
                contents = self.get_contents(origin)
            except TemplateDoesNotExist:
                tried.append((origin, 'Source does not exist'))
                continue
            else:
                # 最终返回 django.template.base.Template 类的实例，这就是「模板对象」
                # 参数说明：
                # contents              : 视图函数中指定的模板文件内容字符串
                # origin                : django.template.base.Origin 类的实例
                # origin.template_name  : 视图函数中指定的模板文件的相对路径
                # self.engine           : 参数 engine
                return Template(
                    contents, origin, origin.template_name, self.engine,
                )

        raise TemplateDoesNotExist(template_name, tried=tried)

    def get_template_sources(self, template_name):
        """
        An iterator that yields possible matching template paths for a
        template name.
        """
        raise NotImplementedError(
            'subclasses of Loader must provide a get_template_sources() method'
        )

    def reset(self):
        """
        Reset any state maintained by the loader instance (e.g. cached
        templates or cached loader modules).
        """
        pass
