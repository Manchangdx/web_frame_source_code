import errno
import logging
import os
import re
import socket
import sys
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.servers.basehttp import (
    WSGIServer, get_internal_wsgi_application, run,
)
from django.utils import autoreload

logger = logging.getLogger(__name__)

naiveip_re = re.compile(r"""^(?:
(?P<addr>
    (?P<ipv4>\d{1,3}(?:\.\d{1,3}){3}) |         # IPv4 address
    (?P<ipv6>\[[a-fA-F0-9:]+\]) |               # IPv6 address
    (?P<fqdn>[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*) # FQDN
):)?(?P<port>\d+)$""", re.X)


class Command(BaseCommand):
    help = "Starts a lightweight Web server for development."

    # Validation is called explicitly each time the server is reloaded.
    requires_system_checks = False
    stealth_options = ('shutdown_message',)

    default_addr = '127.0.0.1'
    default_addr_ipv6 = '::1'
    default_port = '8000'
    protocol = 'http'
    # 此类定义在 django.core.servers.basehttp 模块中，是服务器类
    server_cls = WSGIServer

    def add_arguments(self, parser):
        parser.add_argument(
            'addrport', nargs='?',
            help='Optional port number, or ipaddr:port'
        )
        parser.add_argument(
            '--ipv6', '-6', action='store_true', dest='use_ipv6',
            help='Tells Django to use an IPv6 address.',
        )
        parser.add_argument(
            '--nothreading', action='store_false', dest='use_threading',
            help='Tells Django to NOT use threading.',
        )
        parser.add_argument(
            '--noreload', action='store_false', dest='use_reloader',
            help='Tells Django to NOT use the auto-reloader.',
        )

    def execute(self, *args, **options):
        if options['no_color']:
            # We rely on the environment because it's currently the only
            # way to reach WSGIRequestHandler. This seems an acceptable
            # compromise considering `runserver` runs indefinitely.
            os.environ["DJANGO_COLORS"] = "nocolor"
        super().execute(*args, **options)

    def get_handler(self, *args, **options):
        """Return the default WSGI handler for the runner."""
        return get_internal_wsgi_application()

    def handle(self, *args, **options):
        """这是 runserver 命令要执行的方法
        """
        if not settings.DEBUG and not settings.ALLOWED_HOSTS:
            raise CommandError('You must set settings.ALLOWED_HOSTS if DEBUG is False.')

        self.use_ipv6 = options['use_ipv6']
        if self.use_ipv6 and not socket.has_ipv6:
            raise CommandError('Your Python does not support IPv6.')
        self._raw_ipv6 = False
        if not options['addrport']:
            self.addr = ''
            self.port = self.default_port
        else:
            m = re.match(naiveip_re, options['addrport'])
            if m is None:
                raise CommandError('"%s" is not a valid port number '
                                   'or address:port pair.' % options['addrport'])
            self.addr, _ipv4, _ipv6, _fqdn, self.port = m.groups()
            if not self.port.isdigit():
                raise CommandError("%r is not a valid port number." % self.port)
            if self.addr:
                if _ipv6:
                    self.addr = self.addr[1:-1]
                    self.use_ipv6 = True
                    self._raw_ipv6 = True
                elif self.use_ipv6 and not _fqdn:
                    raise CommandError('"%s" is not a valid IPv6 address.' % self.addr)
        if not self.addr:
            self.addr = self.default_addr_ipv6 if self.use_ipv6 else self.default_addr
            self._raw_ipv6 = self.use_ipv6
        self.run(**options)

    def run(self, **options):
        """启动服务
        """
        use_reloader = options['use_reloader']

        # 代码变更重载功能默认是开启的
        if use_reloader:
            autoreload.run_with_reloader(self.inner_run, **options)
        else:
            self.inner_run(None, **options)

    def inner_run(self, *args, **options):
        """该方法在子线程中启动，这个子线程是项目的主线程 django-main-thread
        """
        logger.info('[django.core.management.commands.runserver.Command.inner_run] 进入应用主线程')

        # If an exception was silenced in ManagementUtility.execute in order
        # to be raised in the child process, raise it now.
        autoreload.raise_last_exception()

        threading = options['use_threading']
        # 'shutdown_message' is a stealth option.
        shutdown_message = options.get('shutdown_message', '')
        quit_command = 'CTRL-BREAK' if sys.platform == 'win32' else 'CONTROL-C'  # pylint: disable=unused-variable

        logger.info(f'[django.core.management.commands.runserver.Command.inner_run] 不执行系统检查，执行脚本迁移检查...')
        # self.check(display_num_errors=True)
        # 检查数据库版本迁移，需要在终端执行 python manage.py migrate 之类的命令
        self.check_migrations()

        now = datetime.now().strftime('%B %d, %Y - %X')
        addr = f'{self.protocol}://{self.addr if self._raw_ipv6 else self.addr}:{self.port}/'
        logger.info(
            f'[django.core.management.commands.runserver.Command.inner_run] '
            f'Django 版本: {self.get_version()}; 即将创建套接字服务器并监听该地址: {addr}'
        )

        try:
            # self 是「命令执行对象」
            # get_handler 方法定义在 django.contrib.staticfiles.management.commands.runserver.Command 类中
            # handler 是 django.core.handlers.wsgi.WSGIHandler 类的实例，相当于 Flask 中的 app 应用对象
            handler = self.get_handler(*args, **options)
            logger.info(f'[django.core.management.commands.runserver.Command.inner_run] 应用对象创建完成: {handler}')
            # 下面这个 run 是核心方法，定义在 django.core.servers.basehttp 模块中
            # 在方法内部会创建服务器对象并启动监听
            # 参数 handler 是应用对象，server_cls 是服务器类，其实例就是携带 TCP 套接字的对象
            run(
                self.addr, int(self.port), handler,
                ipv6=self.use_ipv6, threading=threading, server_cls=self.server_cls
            )
        except OSError as e:
            # Use helpful error messages instead of ugly tracebacks.
            ERRORS = {
                errno.EACCES: "You don't have permission to access that port.",
                errno.EADDRINUSE: "That port is already in use.",
                errno.EADDRNOTAVAIL: "That IP address can't be assigned to.",
            }
            try:
                error_text = ERRORS[e.errno]
            except KeyError:
                error_text = e
            self.stderr.write("Error: %s" % error_text)
            # Need to use an OS exit because sys.exit doesn't work in a thread
            os._exit(1)
        except KeyboardInterrupt:
            if shutdown_message:
                self.stdout.write(shutdown_message)
            sys.exit(0)


# Kept for backward compatibility
BaseRunserverCommand = Command
