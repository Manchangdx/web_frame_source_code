"""
Wrapper for loading templates from the filesystem.
"""

from django.core.exceptions import SuspiciousFileOperation
from django.template import Origin, TemplateDoesNotExist
from django.utils._os import safe_join

from .base import Loader as BaseLoader


class Loader(BaseLoader):

    def __init__(self, engine, dirs=None):
        # self 是「模板加载对象」
        super().__init__(engine)
        self.dirs = dirs

    def get_dirs(self):
        return self.dirs if self.dirs is not None else self.engine.dirs

    def get_contents(self, origin):
        # self 是「模板加载对象」
        # origin 是 django.template.base.Origin 类的实例
        try:
            # 第一个参数是视图函数指定的模板文件的绝对路径
            # 这里打开模板文件，然后返回文件中的全部数据
            with open(origin.name, encoding=self.engine.file_charset) as fp:
                return fp.read()
        except FileNotFoundError:
            raise TemplateDoesNotExist(origin)

    def get_template_sources(self, template_name):
        """
        Return an Origin object pointing to an absolute path in each directory
        in template_dirs. For security reasons, if a path doesn't lie inside
        one of the template_dirs it is excluded from the result set.
        """
        # self 是「模板加载对象」
        # 其 get_dirs 方法的返回值是元组，元组里面可能是这样的：
        # (PosixPath('.../site-packages/django/contrib/admin/templates'), 
        #  PosixPath('.../site-packages/django/contrib/auth/templates'), 
        #  PosixPath('.../qa_community/home/templates'))
        for template_dir in self.get_dirs():
            try:
                # template_dir 的值是 pathlib.PosixPath 的实例
                # 例如第一个参数是 PosixPath('.../admin/templates') ，第二个参数是 'home/haha.html'
                # safe_join 函数的返回值就是字符串 '.../admin/templates/home/haha.html'
                name = safe_join(template_dir, template_name)
            except SuspiciousFileOperation:
                # The joined path was located outside of this template_dir
                # (it might be inside another one, so this isn't fatal).
                continue

            # 迭代 django.template.base.Origin 类的实例
            yield Origin(
                name=name,                      # 模板文件的绝对路径
                template_name=template_name,    # 模板文件的相对路径
                loader=self,                    # 模板加载对象
            )
