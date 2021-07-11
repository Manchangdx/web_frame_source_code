"""
Wrapper for loading templates from "templates" directories in INSTALLED_APPS
packages.
"""

from django.template.utils import get_app_template_dirs

from .filesystem import Loader as FilesystemLoader


class Loader(FilesystemLoader):

    def get_dirs(self):
        # TODO 此返回值是元组，里面都是绝对路径：
        # (PosixPath('.../site-packages/django/contrib/admin/templates'), 
        #  PosixPath('.../site-packages/django/contrib/auth/templates'), 
        #  PosixPath('.../qa_community/home/templates'))
        return get_app_template_dirs('templates')
