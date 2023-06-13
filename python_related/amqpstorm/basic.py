"""AMQPStorm Channel.Basic."""

import logging
import math

from pamqp import body as pamqp_body
from pamqp import header as pamqp_header
from pamqp import specification

from amqpstorm import compatibility
from amqpstorm.base import BaseMessage
from amqpstorm.base import Handler
from amqpstorm.base import MAX_FRAME_SIZE
from amqpstorm.exception import AMQPChannelError
from amqpstorm.exception import AMQPInvalidArgument
from amqpstorm.message import Message

LOGGER = logging.getLogger(__name__)


class Basic(Handler):
    """信道管理器
    """
    __slots__ = ['_max_frame_size']

    def __init__(self, channel, max_frame_size=None):
        super(Basic, self).__init__(channel)
        self._max_frame_size = max_frame_size or MAX_FRAME_SIZE

    def qos(self, prefetch_count=0, prefetch_size=0, global_=False):
        """设置信道的服务质量，其实就是设置接收服务器队列中的消息的频率

        Args:
            prefetch_count : 预取的消息数量，默认无限制
            prefetch_size  : 预取的消息字节数，默认无限制，非零时屏蔽上个参数
            global_        : 是否将此设置应用到当前连接中的所有信道上

        Returns:
            dict: 返回服务设置结果
        """
        if not compatibility.is_integer(prefetch_count):
            raise AMQPInvalidArgument('prefetch_count should be an integer')
        elif not compatibility.is_integer(prefetch_size):
            raise AMQPInvalidArgument('prefetch_size should be an integer')
        elif not isinstance(global_, bool):
            raise AMQPInvalidArgument('global_ should be a boolean')
        qos_frame = specification.Basic.Qos(prefetch_count=prefetch_count, prefetch_size=prefetch_size, global_=global_)
        return self._channel.rpc_request(qos_frame)

    def get(self, queue='', no_ack=False, to_dict=False, auto_decode=True,
            message_impl=None):
        """Fetch a single message.

        :param str queue: Queue name
        :param bool no_ack: No acknowledgement needed
        :param bool to_dict: Should incoming messages be converted to a
                    dictionary before delivery.
        :param bool auto_decode: Auto-decode strings when possible.
        :param class message_impl: Message implementation based on BaseMessage
        :raises AMQPInvalidArgument: Invalid Parameters
        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.

        :returns: Returns a single message, as long as there is a message in
                  the queue. If no message is available, returns None.

        :rtype: amqpstorm.Message,dict,None
        """
        if not compatibility.is_string(queue):
            raise AMQPInvalidArgument('queue should be a string')
        elif not isinstance(no_ack, bool):
            raise AMQPInvalidArgument('no_ack should be a boolean')
        elif self._channel.consumer_tags:
            raise AMQPChannelError("Cannot call 'get' when channel is "
                                   "set to consume")
        if message_impl:
            if not issubclass(message_impl, BaseMessage):
                raise AMQPInvalidArgument(
                    'message_impl should be derived from BaseMessage'
                )
        else:
            message_impl = Message
        get_frame = specification.Basic.Get(queue=queue,
                                            no_ack=no_ack)
        with self._channel.lock and self._channel.rpc.lock:
            message = self._get_message(get_frame, auto_decode=auto_decode,
                                        message_impl=message_impl)
            if message and to_dict:
                return message.to_dict()
            return message

    def recover(self, requeue=False):
        """Redeliver unacknowledged messages.

        :param bool requeue: Re-queue the messages

        :raises AMQPInvalidArgument: Invalid Parameters
        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.

        :rtype: dict
        """
        if not isinstance(requeue, bool):
            raise AMQPInvalidArgument('requeue should be a boolean')
        recover_frame = specification.Basic.Recover(requeue=requeue)
        return self._channel.rpc_request(recover_frame)

    def consume(
        self, callback=None, queue='', consumer_tag='',
        exclusive=False, no_ack=False, no_local=False, arguments=None
    ):
        """将当前信道绑定到指定的消息队列上

        调用此方法产生的连锁事件:
            1. 发送 Basic.Consume 数据帧给服务器
            2. 服务器返回响应，响应中包含 Basic.ConsumeOk 数据帧
               此数据帧有个 consume_tag, 把这个标签放到 channel._consumer_callbacks 字典里 {标签: 回调函数}
            3. 如果消息队列中有消息，捎带返回指定数量的【消息】
               每条【消息】包含 3 个 pamqp 数据帧：specification.Basic.Deliver、header.ContentHeader、body.ContentBody
               所有的数据帧都被放置在 self._channel._inbound 列表里以待消费

        Args:
            callback:       回调函数
            queue:          当前信道要接收消息的消息队列
            consumer_tag:   消费者标签
            exclusive:      是否独占该消息队列
            no_ack:         是否手动确认消息
            no_local:       是否允许消费者接收自身发出的消息
        """
        if not compatibility.is_string(queue):
            raise AMQPInvalidArgument('queue should be a string')
        elif not compatibility.is_string(consumer_tag):
            raise AMQPInvalidArgument('consumer_tag should be a string')
        elif not isinstance(exclusive, bool):
            raise AMQPInvalidArgument('exclusive should be a boolean')
        elif not isinstance(no_ack, bool):
            raise AMQPInvalidArgument('no_ack should be a boolean')
        elif not isinstance(no_local, bool):
            raise AMQPInvalidArgument('no_local should be a boolean')
        elif arguments is not None and not isinstance(arguments, dict):
            raise AMQPInvalidArgument('arguments should be a dict or None')

        # 发送 Basic.Consume 数据帧，阻塞等待响应 Basic.ConsumeOk 数据帧
        consume_rpc_result = self._consume_rpc_request(arguments, consumer_tag, exclusive, no_ack, no_local, queue)

        tag = self._consume_add_and_get_tag(consume_rpc_result)
        self._channel._consumer_callbacks[tag] = callback
        return tag

    def cancel(self, consumer_tag=''):
        """Cancel a queue consumer.

        :param str consumer_tag: Consumer tag

        :raises AMQPInvalidArgument: Invalid Parameters
        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.

        :rtype: dict
        """
        if not compatibility.is_string(consumer_tag):
            raise AMQPInvalidArgument('consumer_tag should be a string')
        cancel_frame = specification.Basic.Cancel(consumer_tag=consumer_tag)
        result = self._channel.rpc_request(cancel_frame)
        self._channel.remove_consumer_tag(consumer_tag)
        return result

    def publish(self, body, routing_key, exchange='', properties=None, mandatory=False, immediate=False):
        """信道直接向交换机发送消息

        Args:
            routing_key : 路由键
            exchange    : 交换机
            properties  : 字典参数
            mandatory   : 服务器收到无法被转发到任何队列的消息时，是否返回一个 Basic.Return 数据帧
            immediate   : 服务器转发消息到队列，如果队列未绑定接受者，是否返回一个 Basic.Return 数据帧
        """
        # 验证参数的数据类型是否合规
        self._validate_publish_parameters(body, exchange, immediate, mandatory, properties, routing_key)
        properties = properties or {}
        # 获取消息体的字节序列
        body = self._handle_utf8_payload(body, properties)
        # 消息的其它属性
        properties = specification.Basic.Properties(**properties)

        # 方法 Frame 对象
        method_frame = specification.Basic.Publish(
            exchange=exchange, routing_key=routing_key, mandatory=mandatory, immediate=immediate
        )
        # 消息头 Frame 对象
        header_frame = pamqp_header.ContentHeader(
            body_size=len(body), properties=properties
        )

        frames_out = [method_frame, header_frame]

        # 根据 max_frame_size 切分消息体生成多个 Frame 对象
        for body_frame in self._create_content_body(body):
            frames_out.append(body_frame)

        # 如果发送 “设置了确认机制的消息”
        if self._channel.confirming_deliveries:
            # 就要在同步锁中执行专门的方法
            with self._channel.rpc.lock:
                return self._publish_confirm(frames_out, mandatory)

        self._channel.write_frames(frames_out)

    def ack(self, delivery_tag=0, multiple=False):
        """发送消息确认数据帧给服务器
        """
        if not compatibility.is_integer(delivery_tag):
            raise AMQPInvalidArgument('delivery_tag should be an integer')
        elif not isinstance(multiple, bool):
            raise AMQPInvalidArgument('multiple should be a boolean')
        ack_frame = specification.Basic.Ack(delivery_tag=delivery_tag, multiple=multiple)
        self._channel.write_frame(ack_frame)

    def nack(self, delivery_tag=0, multiple=False, requeue=True):
        """消息确认失败，将其重新放回消息队列
        """
        if not compatibility.is_integer(delivery_tag):
            raise AMQPInvalidArgument('delivery_tag should be an integer')
        elif not isinstance(multiple, bool):
            raise AMQPInvalidArgument('multiple should be a boolean')
        elif not isinstance(requeue, bool):
            raise AMQPInvalidArgument('requeue should be a boolean')
        nack_frame = specification.Basic.Nack(delivery_tag=delivery_tag, multiple=multiple, requeue=requeue)
        self._channel.write_frame(nack_frame)

    def reject(self, delivery_tag=0, requeue=True):
        """Reject Message.

        :param int/long delivery_tag: Server-assigned delivery tag
        :param bool requeue: Re-queue the message

        :raises AMQPInvalidArgument: Invalid Parameters
        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.

        :return:
        """
        if not compatibility.is_integer(delivery_tag):
            raise AMQPInvalidArgument('delivery_tag should be an integer')
        elif not isinstance(requeue, bool):
            raise AMQPInvalidArgument('requeue should be a boolean')
        reject_frame = specification.Basic.Reject(delivery_tag=delivery_tag,
                                                  requeue=requeue)
        self._channel.write_frame(reject_frame)

    def _consume_add_and_get_tag(self, consume_rpc_result):
        # 参数 consume_rpc_result 是 Basic.Consume 数据帧
        # 该数据帧携带了 consumer_tag 标识，该标识对应到处理消息的回调函数
        consumer_tag = consume_rpc_result['consumer_tag']
        self._channel.add_consumer_tag(consumer_tag)
        return consumer_tag

    def _consume_rpc_request(self, arguments, consumer_tag, exclusive, no_ack, no_local, queue):
        """发送 RPC 数据帧给服务器，将当前信道与指定的消息队列进行绑定
        """
        consume_frame = specification.Basic.Consume(
            queue=queue, consumer_tag=consumer_tag, exclusive=exclusive,
            no_local=no_local, no_ack=no_ack, arguments=arguments
        )
        return self._channel.rpc_request(consume_frame)

    @staticmethod
    def _validate_publish_parameters(body, exchange, immediate, mandatory, properties, routing_key):
        if not compatibility.is_string(body):
            raise AMQPInvalidArgument('body should be a string')
        elif not compatibility.is_string(routing_key):
            raise AMQPInvalidArgument('routing_key should be a string')
        elif not compatibility.is_string(exchange):
            raise AMQPInvalidArgument('exchange should be a string')
        elif properties is not None and not isinstance(properties, dict):
            raise AMQPInvalidArgument('properties should be a dict or None')
        elif not isinstance(mandatory, bool):
            raise AMQPInvalidArgument('mandatory should be a boolean')
        elif not isinstance(immediate, bool):
            raise AMQPInvalidArgument('immediate should be a boolean')

    @staticmethod
    def _handle_utf8_payload(body, properties):
        if 'content_encoding' not in properties:
            properties['content_encoding'] = 'utf-8'
        encoding = properties['content_encoding']
        if compatibility.is_unicode(body):
            body = body.encode(encoding)
        elif compatibility.PYTHON3 and isinstance(body, str):
            body = bytes(body, encoding=encoding)
        return body

    def _get_message(self, get_frame, auto_decode, message_impl):
        """Get and return a message using a Basic.Get frame.

        :param Basic.Get get_frame:
        :param bool auto_decode: Auto-decode strings when possible.
        :param class message_impl: Message implementation based on BaseMessage

        :rtype: Message
        """
        message_uuid = self._channel.rpc.register_request(
            get_frame.valid_responses + ['ContentHeader', 'ContentBody']
        )
        try:
            self._channel.write_frame(get_frame)
            get_ok_frame = self._channel.rpc.get_request(message_uuid,
                                                         raw=True,
                                                         multiple=True)
            if isinstance(get_ok_frame, specification.Basic.GetEmpty):
                return None
            content_header = self._channel.rpc.get_request(message_uuid,
                                                           raw=True,
                                                           multiple=True)
            body = self._get_content_body(message_uuid,
                                          content_header.body_size)
        finally:
            self._channel.rpc.remove(message_uuid)
        return message_impl(channel=self._channel,
                            body=body,
                            method=dict(get_ok_frame),
                            properties=dict(content_header.properties),
                            auto_decode=auto_decode)

    def _publish_confirm(self, frames_out, mandatory):
        """发送 “设置了确认机制的消息”

        此方法发送消息后，会阻塞当前信道，直到收到服务器返回的数据帧
        并且根据数据帧判断消息是否发送成功
        """
        # 生成随机编号
        confirm_uuid = self._channel.rpc.register_request(['Basic.Ack', 'Basic.Nack'])
        # 发送消息
        self._channel.write_frames(frames_out)
        # 阻塞等待并获取服务器返回的响应数据帧
        result = self._channel.rpc.get_request(confirm_uuid, raw=True)
        if mandatory:
            self._channel.check_for_exceptions()
        # 根据响应数据帧的类型判断消息是否发送成功
        if isinstance(result, specification.Basic.Ack):
            return True
        return False

    def _create_content_body(self, body):
        """根据 maximum frame size 将消息体切分成多个数据帧
        """
        frames = int(math.ceil(len(body) / float(self._max_frame_size)))
        for offset in compatibility.RANGE(0, frames):
            start_frame = self._max_frame_size * offset
            end_frame = start_frame + self._max_frame_size
            body_len = len(body)
            if end_frame > body_len:
                end_frame = body_len
            # 使用生成器以减少内存占用
            yield pamqp_body.ContentBody(body[start_frame:end_frame])

    def _get_content_body(self, message_uuid, body_size):
        """Get Content Body using RPC requests.

        :param str uuid_body: Rpc Identifier.
        :param int body_size: Content Size.

        :rtype: str
        """
        body = bytes()
        while len(body) < body_size:
            body_piece = self._channel.rpc.get_request(message_uuid, raw=True,
                                                       multiple=True)
            if not body_piece.value:
                break
            body += body_piece.value
        return body
