from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management.commands.runserver import (
    Command as RunserverCommand,
)


class Command(RunserverCommand):
    help = "Starts a lightweight Web server for development and also serves static files."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--nostatic', action="store_false", dest='use_static_handler',
            help='Tells Django to NOT automatically serve static files at STATIC_URL.',
        )
        parser.add_argument(
            '--insecure', action="store_true", dest='insecure_serving',
            help='Allows serving static files even if DEBUG is False.',
        )

    def get_handler(self, *args, **options):
        """重要方法，创建并返回 django.core.handlers.wsgi.WSGIHandler 类的实例，相当于 Flask 中的 app 应用对象
        """
        handler = super().get_handler(*args, **options)

        # MCDXSIGN → 那什么这块儿不再使用这个静态文件处理类了
        # use_static_handler = options['use_static_handler']
        # insecure_serving = options['insecure_serving']
        # if use_static_handler and (settings.DEBUG or insecure_serving):
        #     return StaticFilesHandler(handler)
        return handler
