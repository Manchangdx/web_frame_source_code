"""AMQPStorm Connection.Channel."""

import logging
import threading
import time

from pamqp import specification
from pamqp.header import ContentHeader

from amqpstorm import compatibility
from amqpstorm.base import BaseChannel
from amqpstorm.base import BaseMessage
from amqpstorm.base import IDLE_WAIT
from amqpstorm.basic import Basic
from amqpstorm.compatibility import try_utf8_decode
from amqpstorm.exception import AMQPError
from amqpstorm.exception import AMQPChannelError
from amqpstorm.exception import AMQPConnectionError
from amqpstorm.exception import AMQPInvalidArgument
from amqpstorm.exception import AMQPMessageError
from amqpstorm.exchange import Exchange
from amqpstorm.message import Message
from amqpstorm.queue import Queue
from amqpstorm.rpc import Rpc
from amqpstorm.tx import Tx

logger = logging.getLogger(__name__)
CONTENT_FRAME = ['Basic.Deliver', 'ContentHeader', 'ContentBody']


class Channel(BaseChannel):
    """RabbitMQ Channel
    """
    __slots__ = [
        '_consumer_callbacks', 'rpc', '_basic', '_confirming_deliveries',
        '_connection', '_exchange', '_inbound', '_queue', '_tx'
    ]

    def __init__(self, channel_id, connection, rpc_timeout):
        logger.info(f'[amqpstorm.channel.Channel.__init__] 信道初始化 {channel_id=}')
        super(Channel, self).__init__(channel_id)
        self.lock = threading.Lock()
        self.rpc = Rpc(self, timeout=rpc_timeout)
        self._consumer_callbacks = {}
        self._confirming_deliveries = False
        self._connection = connection
        self._inbound = []
        self._basic = Basic(self, connection.max_frame_size)
        self._exchange = Exchange(self)
        self._tx = Tx(self)
        self._queue = Queue(self)

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, _):
        if exception_type:
            logger.warning(f'Closing channel due to an unhandled exception: {exception_value}')
        if not self.is_open:
            return
        self.close()

    def __int__(self):
        return self._channel_id

    @property
    def basic(self):
        return self._basic

    @property
    def exchange(self):
        return self._exchange

    @property
    def queue(self):
        return self._queue

    @property
    def tx(self):
        return self._tx

    def build_inbound_messages(self, break_on_empty=False, to_tuple=False, auto_decode=True, message_impl=None):
        """根据 self._inbound 列表中的 Frame 对象构造【消息】并弹出

        Args:
            break_on_empty : 没有消息时停止循环
            to_tuple       : 返回【消息】的关键属性元组
            auto_decode    : 返回解码后的消息体
            message_impl   : 消息类，默认是 message.Message
        """
        self.check_for_errors()
        if message_impl:
            if not issubclass(message_impl, BaseMessage):
                raise AMQPInvalidArgument('message_impl must derive from BaseMessage')
        else:
            message_impl = Message

        while not self.is_closed:
            message = self._build_message(auto_decode=auto_decode, message_impl=message_impl)
            if not message:
                self.check_for_errors()
                time.sleep(IDLE_WAIT)
                if break_on_empty and not self._inbound:
                    break
                continue
            if to_tuple:
                yield message.to_tuple()
                continue
            yield message

    def close(self, reply_code=200, reply_text=''):
        """关闭信道
        """
        if not compatibility.is_integer(reply_code):
            raise AMQPInvalidArgument('reply_code should be an integer')
        elif not compatibility.is_string(reply_text):
            raise AMQPInvalidArgument('reply_text should be a string')
        try:
            if self._connection.is_closed or not self.is_open:
                self.stop_consuming()
                logger.debug('Channel #%d forcefully Closed', self.channel_id)
                return
            self.set_state(self.CLOSING)
            logger.debug('Channel #%d Closing', self.channel_id)
            try:
                self.stop_consuming()
            except AMQPChannelError:
                self.remove_consumer_tag()
            self.rpc_request(specification.Channel.Close(
                reply_code=reply_code,
                reply_text=reply_text),
                connection_adapter=self._connection
            )
        finally:
            if self._inbound:
                del self._inbound[:]
            self.set_state(self.CLOSED)
        logger.debug('Channel #%d Closed', self.channel_id)

    def check_for_errors(self,):
        """Check connection and channel for errors.

        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.
        :return:
        """
        try:
            self._connection.check_for_errors()
        except AMQPConnectionError:
            self.set_state(self.CLOSED)
            raise

        self.check_for_exceptions()

        if self.is_closed:
            raise AMQPChannelError('channel closed')

    def check_for_exceptions(self):
        """Check channel for exceptions.

        :raises AMQPChannelError: Raises if the channel encountered an error.

        :return:
        """
        if self.exceptions:
            exception = self.exceptions[0]
            if self.is_open:
                self.exceptions.pop(0)
            raise exception

    def confirm_deliveries(self):
        """将信道设置为 “确认每条消息是否已经成功发送”
        """
        self._confirming_deliveries = True
        confirm_frame = specification.Confirm.Select()
        return self.rpc_request(confirm_frame)

    @property
    def confirming_deliveries(self):
        return self._confirming_deliveries

    def on_frame(self, frame_in):
        """处理服务器发来的消息
        """

        # 有一些方法例如 channel.open 和 channel.basic.qos 等发送数据帧后，需要阻塞等待响应来确认是否成功
        # 以 channel.open 方法为例，它给服务器发送的是 Channel.Open 数据帧，告知服务器要创建信道，此方法阻塞运行，直到服务器回复
        # 下面这个方法就是用来处理上述情况中服务器回复的 Frame 对象，又叫做「即时响应 Frame 对象」
        if self.rpc.on_frame(frame_in):
            return

        # 如果是 “队列消息” Frame 对象，包括 Basic.Deliver, ContentHeader, ContentBody
        # 放到信道的「消息暂存列表」里面
        if frame_in.name in CONTENT_FRAME:
            self._inbound.append(frame_in)
        elif frame_in.name == 'Basic.Cancel':
            self._basic_cancel(frame_in)
        elif frame_in.name == 'Basic.CancelOk':
            self.remove_consumer_tag(frame_in.consumer_tag)
        elif frame_in.name == 'Basic.ConsumeOk':
            self.add_consumer_tag(frame_in['consumer_tag'])
        elif frame_in.name == 'Basic.Return':
            self._basic_return(frame_in)
        elif frame_in.name == 'Channel.Close':
            self._close_channel(frame_in)
        elif frame_in.name == 'Channel.Flow':
            self.write_frame(specification.Channel.FlowOk(frame_in.active))
        else:
            logger.error(f'[Channel {self.channel_id}] Unhandled Frame: {frame_in.name} -- {dict(frame_in)}')

    def open(self):
        """启动信道，给服务器发送一个 Channel.Open 数据帧知会一声
        """
        self._inbound = []
        self._exceptions = []
        self._confirming_deliveries = False
        self.set_state(self.OPENING)
        self.rpc_request(specification.Channel.Open())
        self.set_state(self.OPEN)

    def process_data_events(self, to_tuple=False, auto_decode=True):
        """调用回调函数处理消息队列发来的【消息】
        """
        # 调用 channel.basic.consume 方法绑定消息队列后，此属性必有值
        if not self._consumer_callbacks:
            raise AMQPChannelError('no consumer callback defined')
        for message in self.build_inbound_messages(break_on_empty=True, auto_decode=auto_decode):
            print(f'【amqpstorm.channel.Channel.process_data_events】回调函数处理消息: {message._body=}')
            consumer_tag = message._method.get('consumer_tag')
            callback = self._consumer_callbacks[consumer_tag]
            if to_tuple:
                callback(*message.to_tuple())
            else:
                callback(message)

    def rpc_request(self, frame_out, connection_adapter=None):
        """给服务器发送一个 RPC 请求，等待服务器响应结果并返回

        RPC 请求的数据帧都是有特定作用的实例，相关类定义在 pamqp.specification 模块中
        每次请求发出后，都会在 channel.rpc._requests 字典中记录 {frame_out.valid_responses: uuid}

        Args:
            frame_out: pamqp.specification.Frame 子类的实例
        """
        with self.rpc.lock:
            logger.info(f'[amqpstorm.channel.Channel.rpc_request] 信道发送消息 channel_id={self.channel_id} {frame_out=}')
            # 利用信道所属连接发送消息给服务器
            self._connection.write_frame(self.channel_id, frame_out)
            # 把数据帧的名字记下，并随机生成唯一标识符
            uuid = self.rpc.register_request(frame_out.valid_responses)
            # 等待并返回 “服务器返回的数据帧”
            result = self.rpc.get_request(uuid, connection_adapter=connection_adapter)
            logger.info(f'[amqpstorm.channel.Channel.rpc_request] 信道收到响应 channel_id={self.channel_id} {result=}')
            return result

    def start_consuming(self, to_tuple=False, auto_decode=True):
        """将信道绑定到指定的消息队列上之后，调用此方法启动消费消息
        """
        while not self.is_closed:
            self.process_data_events(
                to_tuple=to_tuple,
                auto_decode=auto_decode
            )
            if self.consumer_tags:
                time.sleep(IDLE_WAIT)
                continue
            break

    def stop_consuming(self):
        """Stop consuming messages.

        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.

        :return:
        """
        if not self.consumer_tags:
            return
        if not self.is_closed:
            for tag in self.consumer_tags:
                self.basic.cancel(tag)
        self.remove_consumer_tag()

    def write_frame(self, frame_out):
        """利用当前信道向服务器发送一个数据帧
        """
        self.check_for_errors()
        self._connection.write_frame(self.channel_id, frame_out)

    def write_frames(self, frames_out):
        """利用当前信道向服务器发送多个数据帧
        """
        self.check_for_errors()
        self._connection.write_frames(self.channel_id, frames_out)

    def _basic_cancel(self, frame_in):
        """Handle a Basic Cancel frame.

        :param specification.Basic.Cancel frame_in: Amqp frame.

        :return:
        """
        logger.warning(
            'Received Basic.Cancel on consumer_tag: %s',
            try_utf8_decode(frame_in.consumer_tag)
        )
        self.remove_consumer_tag(frame_in.consumer_tag)

    def _basic_return(self, frame_in):
        """Handle a Basic Return Frame and treat it as an error.

        :param specification.Basic.Return frame_in: Amqp frame.

        :return:
        """
        reply_text = try_utf8_decode(frame_in.reply_text)
        message = (
            "Message not delivered: %s (%s) to queue '%s' from exchange '%s'" %
            (
                reply_text,
                frame_in.reply_code,
                frame_in.routing_key,
                frame_in.exchange
            )
        )
        exception = AMQPMessageError(message,
                                     reply_code=frame_in.reply_code)
        self.exceptions.append(exception)

    def _build_message(self, auto_decode, message_impl):
        """从信道的 channel._inbound 列表中读取 Frame 对象并构造【消息】
        """
        with self.lock:
            # 一条数据至少包含 specification.Basic.Deliver、header.ContentHeader 这两个数据帧
            if len(self._inbound) < 2:
                return None
            headers = self._build_message_headers()
            if not headers:
                return None
            basic_deliver, content_header = headers
            # 消息内容二进制值
            body = self._build_message_body(content_header.body_size)

        # 默认 auto_decode=True, 消息体会被解码
        message = message_impl(channel=self,
                               body=body,
                               method=dict(basic_deliver),
                               properties=dict(content_header.properties),
                               auto_decode=auto_decode)
        return message

    def _build_message_headers(self):
        """获取「投递 Frame 对象」和「消息头 Frame 对象」
        """
        basic_deliver = self._inbound.pop(0)
        if not isinstance(basic_deliver, specification.Basic.Deliver):
            logger.warning(
                'Received an out-of-order frame: %s was '
                'expecting a Basic.Deliver frame',
                type(basic_deliver)
            )
            return None
        content_header = self._inbound.pop(0)
        if not isinstance(content_header, ContentHeader):
            logger.warning(
                'Received an out-of-order frame: %s was '
                'expecting a ContentHeader frame',
                type(content_header)
            )
            return None

        return basic_deliver, content_header

    def _build_message_body(self, body_size):
        """根据「消息头 Frame 对象」中存储的消息字节数获取消息本身（二进制）
        """
        body = bytes()
        while len(body) < body_size:
            if not self._inbound:
                self.check_for_errors()
                time.sleep(IDLE_WAIT)
                continue
            body_piece = self._inbound.pop(0)
            if not body_piece.value:
                break
            body += body_piece.value
        return body

    def _close_channel(self, frame_in):
        """告知服务器信道已关闭，并将当前信道设为已关闭状态
        """
        self.set_state(self.CLOSING)
        if not self._connection.is_closed:
            try:
                self.write_frame(specification.Channel.CloseOk())
            except AMQPError:
                pass
        self.remove_consumer_tag()
        if self._inbound:
            del self._inbound[:]
        self.exceptions.append(AMQPChannelError(
            'Channel %d was closed by remote server: %s' %
            (
                self._channel_id,
                try_utf8_decode(frame_in.reply_text)
            ),
            reply_code=frame_in.reply_code
        ))
        self.set_state(self.CLOSED)
