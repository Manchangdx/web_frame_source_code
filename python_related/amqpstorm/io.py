"""AMQPStorm Connection.IO."""

import logging
import select
import socket
import threading
from errno import EAGAIN
from errno import EINTR
from errno import EWOULDBLOCK

from amqpstorm import compatibility
from amqpstorm.base import MAX_FRAME_SIZE
from amqpstorm.compatibility import ssl
from amqpstorm.exception import AMQPConnectionError

EMPTY_BUFFER = bytes()
LOGGER = logging.getLogger(__name__)
POLL_TIMEOUT = 1.0


class Poller(object):
    """Socket Read Poller."""

    def __init__(self, fileno, exceptions, timeout=5):
        self.select = select
        self._fileno = fileno
        self._exceptions = exceptions
        self.timeout = timeout

    @property
    def fileno(self):
        """Socket Fileno.

        :return:
        """
        return self._fileno

    @property
    def is_ready(self) -> bool:
        """判断连接 RabbitMQ 服务器的客户端套接字是否 “读就绪”
        """
        try:
            ready, _, _ = self.select.select([self.fileno], [], [], POLL_TIMEOUT)
            return bool(ready)
        except self.select.error as why:
            if why.args[0] != EINTR:
                self._exceptions.append(AMQPConnectionError(why))
        return False


class IO(object):
    """Internal Input/Output handler."""

    def __init__(self, parameters, exceptions=None, on_read_impl=None):
        self._exceptions = exceptions
        self._wr_lock = threading.Lock()
        self._rd_lock = threading.Lock()
        self._inbound_thread = None
        self._on_read_impl = on_read_impl
        self._running = threading.Event()
        self._parameters = parameters
        self.data_in = EMPTY_BUFFER
        self.poller = None
        self.socket = None
        self.use_ssl = self._parameters['ssl']

    def close(self):
        """Close Socket.

        :return:
        """
        self._wr_lock.acquire()
        self._rd_lock.acquire()
        try:
            self._running.clear()
            self._close_socket()
        finally:
            self._wr_lock.release()
            self._rd_lock.release()

        if self._inbound_thread:
            self._inbound_thread.join(timeout=self._parameters['timeout'])

        self.socket = None
        self.poller = None
        self._inbound_thread = None

    def open(self):
        """在线程安全的情况下启动 IO 相关活动

        1. 将 “协程事件” 设为 “已设置” 状态
        2. 创建 TCP 套接字，并与 RabbitMQ 服务器建立连接
        3. 创建 poller 对象，其本质就是 select 多路复用对象，用于判断客户端套接字是否 “读就绪”，也就是有没有收到服务器发来的消息
        4. 创建一个子线程并启动运行，该子线程会维持一个无限循环，在循环中利用 poller 对象判断客户端套接字是否 “读就绪”
        """
        self._wr_lock.acquire()
        self._rd_lock.acquire()
        try:
            self.data_in = EMPTY_BUFFER
            print('【amqpstorm.io.IO.open】将 “协程事件” 设为 “已设置” 状态')
            self._running.set()
            print('【amqpstorm.io.IO.open】创建 TCP 套接字，并与 RabbitMQ 服务器建立连接')
            sock_addresses = self._get_socket_addresses()
            self.socket = self._find_address_and_connect(sock_addresses)
            print('【amqpstorm.io.IO.open】创建 poller 对象，即 select 多路复用对象')
            self.poller = Poller(self.socket.fileno(), self._exceptions, timeout=self._parameters['timeout'])
            print('【amqpstorm.io.IO.open】创建一个子线程并启动运行，该子线程会维持一个无限循环，在循环中利用 poller 对象判断客户端套接字是否 “读就绪”')
            self._inbound_thread = self._create_inbound_thread()
        finally:
            self._wr_lock.release()
            self._rd_lock.release()

    def write_to_socket(self, frame_data):
        """套接字发送数据

        Python 套接字的 send 方法可以发送任意字节数的数据，此方法只是把数据从用户空间拷贝到内核空间的网络缓冲区。
        操作系统一次只能发送一个 TCP 报文，并且报文段有字节量限制，即 MTU 最大传输单元，通常为 1500 字节（不包括 IP 和 TCP 头部）。
        所以调用一次 socket.send 方法发送 2000 字节，操作系统只会发送 1500 ，剩下那 500 会被舍弃。
        最后 socket.send 方法返回发送成功的字节数 1500。
        """
        self._wr_lock.acquire()
        try:
            total_bytes_written = 0
            bytes_to_send = len(frame_data)
            while total_bytes_written < bytes_to_send:
                try:
                    if not self.socket:
                        raise socket.error('connection/socket error')
                    bytes_written = (
                        self.socket.send(frame_data[total_bytes_written:])
                    )
                    if bytes_written == 0:
                        raise socket.error('connection/socket error')
                    total_bytes_written += bytes_written
                except socket.timeout:
                    pass
                except socket.error as why:
                    if why.args[0] in (EWOULDBLOCK, EAGAIN):
                        continue
                    self._exceptions.append(AMQPConnectionError(why))
                    return
        finally:
            self._wr_lock.release()

    def _close_socket(self):
        """Shutdown and close the Socket.

        :return:
        """
        if not self.socket:
            return
        try:
            if self.use_ssl:
                self.socket.unwrap()
            self.socket.shutdown(socket.SHUT_RDWR)
        except (OSError, socket.error, ValueError):
            pass

        self.socket.close()

    def _get_socket_addresses(self):
        """Get Socket address information.

        :rtype: list
        """
        family = socket.AF_UNSPEC
        if not socket.has_ipv6:
            family = socket.AF_INET
        try:
            addresses = socket.getaddrinfo(self._parameters['hostname'],
                                           self._parameters['port'], family,
                                           socket.SOCK_STREAM)
        except socket.gaierror as why:
            raise AMQPConnectionError(why)
        return addresses

    def _find_address_and_connect(self, addresses):
        """根据参数地址创建 socket 套接字并与 RabbitMQ 服务器建立连接，最后返回套接字
        """
        error_message = None
        for address in addresses:
            sock = self._create_socket(socket_family=address[0])
            try:
                print(f'【amqpstorm.io.IO._find_address_and_connect】RabbitMQ 客户端套接字连接服务器，服务器 IP 地址和端口号: {address[4]}')
                sock.connect(address[4])
            except (IOError, OSError) as why:
                error_message = why.strerror
                continue
            return sock
        raise AMQPConnectionError(
            'Could not connect to %s:%d error: %s' % (
                self._parameters['hostname'], self._parameters['port'], error_message
            )
        )

    def _create_socket(self, socket_family):
        """Create Socket.

        :param int socket_family:
        :rtype: socket.socket
        """
        sock = socket.socket(socket_family, socket.SOCK_STREAM, 0)
        sock.settimeout(self._parameters['timeout'] or None)
        if self.use_ssl:
            if not compatibility.SSL_SUPPORTED:
                raise AMQPConnectionError(
                    'Python not compiled with support for TLSv1 or higher'
                )
            sock = self._ssl_wrap_socket(sock)
        return sock

    def _ssl_wrap_socket(self, sock):
        """Wrap SSLSocket around the Socket.

        :param socket.socket sock:
        :rtype: SSLSocket
        """
        context = self._parameters['ssl_options'].get('context')
        if context is not None:
            hostname = self._parameters['ssl_options'].get('server_hostname')
            return context.wrap_socket(
                sock, do_handshake_on_connect=True,
                server_hostname=hostname
            )
        hostname = self._parameters['hostname']
        context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        mode = self._parameters['ssl_options'].get('verify_mode', 'none')
        if mode.lower() == 'required':
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            context.verify_mode = ssl.CERT_NONE
        check = self._parameters['ssl_options'].get('check_hostname', False)
        context.check_hostname = check
        context.load_default_certs()
        return context.wrap_socket(sock, do_handshake_on_connect=True,
                                   server_hostname=hostname)

    def _create_inbound_thread(self):
        """创建一个子线程并启动

        该子线程会维持一个无限循环，在循环中判断客户端套接字是否 “读就绪” 也就是是否收到了 RabbitMQ 的消息:
            1. 如果收到就处理
            2. 否则继续循环
        """
        # 创建子线程
        inbound_thread = threading.Thread(target=self._process_incoming_data, name=__name__)
        # 将子线程设为守护线程，目的是跟随主线程一起停止运行
        inbound_thread.daemon = True
        # 启动子线程
        inbound_thread.start()
        return inbound_thread

    def _process_incoming_data(self):
        """处理 RabbitMQ 服务器发来的消息
        """
        # 如果 “线程事件” 处于 “已设置” 状态
        while self._running.is_set():
            # 如果 RabbitMQ 服务器发来了消息
            if self.poller.is_ready:
                # 从套接字的 recv 方法读取收到的二进制数据
                self.data_in += self._receive()
                # 根据 AMQP 解析二进制数据，得到 channel 编号和消息内容，再利用 channel.on_frame 方法解析消息内容
                self.data_in = self._on_read_impl(self.data_in)

    def _receive(self):
        """调用套接字的 recv 方法获取服务器发来的二进制数据并返回
        """
        data_in = EMPTY_BUFFER
        try:
            data_in = self._read_from_socket()
            if len(data_in) == 0:
                raise socket.error("connection closed by server")
        except socket.timeout:
            pass
        except compatibility.SSLWantReadError:
            # NOTE(visobet): Retry if the non-blocking socket does not have any meaningful data ready.
            pass
        except (IOError, OSError, ValueError) as why:
            if why.args[0] not in (EWOULDBLOCK, EAGAIN):
                self._exceptions.append(AMQPConnectionError(why))
                if self._running.is_set():
                    LOGGER.warning("Stopping inbound thread due to %s", why, exc_info=True)
                    self._running.clear()
        return data_in

    def _read_from_socket(self):
        """Read data from the socket.

        :rtype: bytes
        """
        if not self.use_ssl:
            if not self.socket:
                raise socket.error('connection/socket error')
            return self.socket.recv(MAX_FRAME_SIZE)

        with self._rd_lock:
            if not self.socket:
                raise socket.error('connection/socket error')
            return self.socket.read(MAX_FRAME_SIZE)
