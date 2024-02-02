"""AMQPStorm Connection."""

import logging
import threading
import time
from time import sleep

from pamqp import exceptions as pamqp_exception
from pamqp import frame as pamqp_frame
from pamqp import header as pamqp_header
from pamqp import specification

from amqpstorm import compatibility
from amqpstorm.base import IDLE_WAIT
from amqpstorm.base import Stateful
from amqpstorm.channel import Channel
from amqpstorm.channel0 import Channel0
from amqpstorm.exception import AMQPConnectionError
from amqpstorm.exception import AMQPInvalidArgument
from amqpstorm.heartbeat import Heartbeat
from amqpstorm.io import EMPTY_BUFFER
from amqpstorm.io import IO

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_INTERVAL = 60
DEFAULT_SOCKET_TIMEOUT = 10
DEFAULT_VIRTUAL_HOST = '/'


class Connection(Stateful):
    """RabbitMQ 连接类
    """
    __slots__ = [
        'heartbeat', 'parameters', '_channel0', '_channels', '_io'
    ]

    def __init__(self, hostname, username, password, port=5672, **kwargs):
        super(Connection, self).__init__()
        self.lock = threading.RLock()
        self.buffer_lock = threading.Lock()
        self.parameters = {
            'hostname': hostname,
            'username': username,
            'password': password,
            'port': port,
            'virtual_host': kwargs.get('virtual_host', DEFAULT_VIRTUAL_HOST),
            'heartbeat': kwargs.get('heartbeat', DEFAULT_HEARTBEAT_INTERVAL),
            'timeout': kwargs.get('timeout', DEFAULT_SOCKET_TIMEOUT),
            'ssl': kwargs.get('ssl', False),
            'ssl_options': kwargs.get('ssl_options', {}),
            'client_properties': kwargs.get('client_properties', {})
        }
        self._validate_parameters()
        self._io = IO(self.parameters, exceptions=self._exceptions, on_read_impl=self._read_buffer)
        self._channel0 = Channel0(self, self.parameters['client_properties'])
        self._channels = {}
        self._last_channel_id = None
        self.heartbeat = Heartbeat(self.parameters['heartbeat'], self._channel0.send_heartbeat)
        if not kwargs.get('lazy', False):
            self.open()

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, _):
        if exception_type:
            message = 'Closing connection due to an unhandled exception: %s'
            logger.warning(message, exception_value)
        self.close()

    @property
    def channels(self):
        """信道字典 {channel_id: channel}
        """
        return self._channels

    @property
    def fileno(self):
        """客户端套接字文件描述符
        """
        if not self._io.socket:
            return None
        return self._io.socket.fileno()

    @property
    def is_blocked(self):
        """连接是否处于被远程服务器阻塞的状态
        """
        return self._channel0.is_blocked

    @property
    def max_allowed_channels(self):
        """单个连接允许创建的信道数量上限，默认是 2047 个
        """
        return self._channel0.max_allowed_channels

    @property
    def max_frame_size(self):
        """数据帧的最大字节数，默认是 131072 Bytes
        """
        return self._channel0.max_frame_size

    @property
    def server_properties(self):
        """服务器属性字典
        """
        return self._channel0.server_properties

    @property
    def socket(self):
        return self._io.socket

    def channel(self, rpc_timeout=60, lazy=False):
        """创建一个信道
        """
        if not compatibility.is_integer(rpc_timeout):
            raise AMQPInvalidArgument('rpc_timeout should be an integer')
        elif self.is_closed:
            raise AMQPConnectionError('connection closed')

        with self.lock:
            channel_id = self._get_next_available_channel_id()
            logger.info(f'[amqpstorm.connection.Connection.channel] 新建一个信道 {channel_id=}')
            channel = Channel(channel_id, self, rpc_timeout)
            self._channels[channel_id] = channel
            if not lazy:
                channel.open()

        logger.info(f'[amqpstorm.connection.Connection.channel] 现有信道: {list(self._channels)}')
        return self._channels[channel_id]

    def check_for_errors(self):
        """检查连接异常

        1. 如果没有异常并且连接处于未关闭模式：什么也不做
        2. 如果没有异常并且连接处于关闭模式：创建异常，断开信道和连接，将连接设为关闭模式，抛出异常
        3. 如果有异常：断开信道和连接，将连接设为关闭模式，抛出异常
        """
        if not self.exceptions:
            if not self.is_closed:
                return
            why = AMQPConnectionError('connection closed')
            self.exceptions.append(why)
        self.set_state(self.CLOSED)
        self.close()
        raise self.exceptions[0]

    def close(self):
        """断开与 RabbitMQ 服务器的 TCP 连接
        """
        logger.debug('Connection Closing')
        if not self.is_closed:
            self.set_state(self.CLOSING)
        self.heartbeat.stop()
        try:
            if not self.is_closed and self.socket:
                self._channel0.send_close_connection()
                self._wait_for_connection_state(state=Stateful.CLOSED)
        except AMQPConnectionError:
            pass
        finally:
            self._close_remaining_channels()
            self._io.close()
            self.set_state(self.CLOSED)
        logger.debug('Connection Closed')

    def open(self):
        """启动 AMQP 连接

        连接有四个状态（即 self._state 属性值）：已关闭、正在关闭、已开启、正在开启，新建的连接处于 “已关闭” 状态
        当前方法的主要工作流程是：
            1. 将连接状态即 self._state 属性设为 “正在开启”
            2. 创建 TCP 客户端并与 RabbitMQ 服务器建立连接
            3. 向 RabbitMQ 服务器发送 AMQP 握手请求，把所有准备工作做好，包括约定各种版本号、登录的账号密码、Vhost 等
            4. 这个过程中客户端会收发一系列数据帧，直到最后收到 OpenOk 数据帧，然后将连接状态也就是 self._state 属性设为 “已开启”
            6. 启动心跳检查
            7. 返回 self，这样就可以使用 with 语句来保证连接处于 “已开启” 状态
        """
        logger.info('[amqpstorm.connection.Connection.open] 启动连接')

        # 将 self._state 设为 “正在开启” 状态
        self.set_state(self.OPENING)

        self._exceptions = []
        self._channels = {}
        self._last_channel_id = None

        # 创建 TCP 客户端套接字，与 RabbitMQ 服务器建立 TCP 连接，等待接收服务端发来的消息
        self._io.open()

        # 此时 TCP 连接已就绪，向 RabbitMQ 服务器发送握手请求，把所有准备工作做好
        # 包括与服务器约定 AMQP 版本号、Python 版本号、登录的账号密码、心跳时间间隔、连接的 Vhost 等
        self._send_handshake()
        self._wait_for_connection_state(state=Stateful.OPEN)

        # 启动心跳检查
        self.heartbeat.start(self._exceptions)

        logger.info('[amqpstorm.connection.Connection.open] 连接成功')

    def write_frame(self, channel_id, frame_out):
        """将一条数据帧通过指定信道发送给 RabbitMQ 服务器

        所有发向服务器的 TCP 消息（除了建立 TCP 连接后发送的第一条握手消息）都经过此方法或下面的方法发送
        所有要发出去的消息都是 Frame 实例，即数据帧对象，数据帧会被转换成二进制数据，以 b'\xce' 结尾

        Args:
            channel_id: 信道 ID
            frame_out: pamqp.specification.Frame 类的实例
        """
        logger.info(f'[amqpstorm.connection.Connection.write_frame] 客户端利用信道发送消息 {channel_id=} {frame_out=}')
        frame_data = pamqp_frame.marshal(frame_out, channel_id)
        self.heartbeat.register_write()
        self._io.write_to_socket(frame_data)

    def write_frames(self, channel_id, frames_out):
        """将多条数据帧通过指定信道发送给 RabbitMQ 服务器

        Args:
            channel_id: 信道编号
            frames_out: 要发送给服务器的数据帧对象列表
        """
        with self.lock:
            logger.info(
                f'[amqpstorm.connection.Connection.write_frames] 利用信道给服务器发送消息 {channel_id=} {frames_out=}'
            )
        data_out = EMPTY_BUFFER
        for single_frame in frames_out:
            data_out += pamqp_frame.marshal(single_frame, channel_id)
        self.heartbeat.register_write()
        self._io.write_to_socket(data_out)

    def _close_remaining_channels(self):
        """强制关闭全部处于开启状态的信道
        """
        for channel_id in list(self._channels):
            self._channels[channel_id].set_state(Channel.CLOSED)
            self._channels[channel_id].close()
            self._cleanup_channel(channel_id)

    def _get_next_available_channel_id(self):
        """返回一个未被使用的信道 ID
        """
        for channel_id in compatibility.RANGE(self._last_channel_id or 1, self.max_allowed_channels + 1):
            if channel_id in self._channels:
                channel = self._channels[channel_id]
                if channel.current_state != Channel.CLOSED:
                    continue
                del self._channels[channel_id]
            self._last_channel_id = channel_id
            return channel_id

        if self._last_channel_id:
            self._last_channel_id = None
            return self._get_next_available_channel_id()

        raise AMQPConnectionError(f'reached the maximum number of channels {self.max_allowed_channels}')

    def _handle_amqp_frame(self, data_in):
        """反序列化服务器发来的二进制数据，生成 Frame 对象

        返回值: (消息体, 信道编号, Frame 对象)
        """
        if not data_in:
            return data_in, None, None
        try:
            byte_count, channel_id, frame_in = pamqp_frame.unmarshal(data_in)
            return data_in[byte_count:], channel_id, frame_in
        except pamqp_exception.UnmarshalingException:
            pass
        except specification.AMQPFrameError as why:
            logger.error('AMQPFrameError: %r', why, exc_info=True)
        except ValueError as why:
            logger.error(why, exc_info=True)
            self.exceptions.append(AMQPConnectionError(why))
        return data_in, None, None

    def _read_buffer(self, data_in):
        """处理服务器发来的消息

        当 select 多路复用机制监听到 TCP 客户端套接字可读事件就绪时，就会调用此方法，此方法在 amqpstorm.io 线程中执行
        有时服务器发来的消息是多个数据帧连起来的字节序列，每个数据帧对应的字节序列以 b'\xce' 结尾
        这种情况下，下面的 while 循环就会循环多次，每次调用 self._handle_amqp_frame 处理 1 个排在最左边的数据帧
        直到最后一个数据帧处理完毕，data_in 就变成空字节序列 b'' 了
        """
        with self.buffer_lock:
            n = 0
            while data_in:
                data_in, channel_id, frame_in = self._handle_amqp_frame(data_in)
                n += 1
                logger.info(
                    f'[amqpstorm.connection.Connection._read_buffer] 处理从服务器接收的消息，'
                    f'第 {n} 次处理结果: {channel_id=} {frame_in=} {data_in=}'
                )

                if frame_in is None:
                    break

                # 心跳控制器的[服务器读取消息次数] +1
                self.heartbeat.register_read()

                # 发给零号信道的消息专门处理
                if channel_id == 0:
                    self._channel0.on_frame(frame_in)
                # 发给其它信道的消息先存起来
                elif channel_id in self._channels:
                    self._channels[channel_id].on_frame(frame_in)

        return data_in

    def _cleanup_channel(self, channel_id):
        """关闭全部信道
        """
        with self.lock:
            if channel_id not in self._channels:
                return
            del self._channels[channel_id]

    def _send_handshake(self):
        """通过零号信道发送一个 AMQP 握手请求

        此请求是建立 TCP 连接后客户端发出的第一个请求，该请求引发的连锁反应如下：
            1. 客户端向服务器发送第一个握手请求，包含 AMQP 版本号
            2. 服务器返回 Start 数据帧
            3. 客户端发送 StartOk 数据帧，包括 Python 版本、AMQPStorm 版本、用户账号密码等信息
            4. 服务器验证通过后返回 Tune 数据帧
            5. 客户端发送两个数据帧
                  TuneOk 数据帧，包括信道数量上限、数据帧的字节数上限、心跳间隔时间等
                  Open 数据帧，包含要连接的 vhost 虚拟主机
            6. 服务器收到俩帧后返回 OpenOk 数据帧
            7. 客户端收到这个数据帧后将连接的状态设为 “已开启”，意味着 AMQP 连接已建立
        """
        frame_data = pamqp_header.ProtocolHeader().marshal()
        logger.info(
            f'[amqpstorm.connection.Connection._send_handshake] 创建 TCP 连接后向服务器发送 AMQP 握手请求: {frame_data=}'
        )
        self._io.write_to_socket(frame_data)

    def _validate_parameters(self):
        """验证创建 Connection 的参数，仅验证各个参数的数据类型而已
        """
        if not compatibility.is_string(self.parameters['hostname']):
            raise AMQPInvalidArgument('hostname should be a string')
        elif not compatibility.is_integer(self.parameters['port']):
            raise AMQPInvalidArgument('port should be an integer')
        elif not compatibility.is_string(self.parameters['username']):
            raise AMQPInvalidArgument('username should be a string')
        elif not compatibility.is_string(self.parameters['password']):
            raise AMQPInvalidArgument('password should be a string')
        elif not compatibility.is_string(self.parameters['virtual_host']):
            raise AMQPInvalidArgument('virtual_host should be a string')
        elif not isinstance(self.parameters['timeout'], (int, float)):
            raise AMQPInvalidArgument('timeout should be an integer or float')
        elif not compatibility.is_integer(self.parameters['heartbeat']):
            raise AMQPInvalidArgument('heartbeat should be an integer')

    def _wait_for_connection_state(self, state=Stateful.OPEN, rpc_timeout=30):
        """确保连接处于参数 state 指定的状态
        """
        start_time = time.time()
        while self.current_state != state:
            self.check_for_errors()
            if time.time() - start_time > rpc_timeout:
                raise AMQPConnectionError('connection timed out')
            sleep(IDLE_WAIT)
