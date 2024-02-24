"""AMQPStorm Message."""

import json
import uuid
from datetime import datetime

from amqpstorm.base import BaseMessage
from amqpstorm.compatibility import try_utf8_decode
from amqpstorm.exception import AMQPMessageError


class Message(BaseMessage):
    """RabbitMQ Message.
    """
    __slots__ = [
        '_decode_cache'
    ]

    def __init__(self, channel, body=None, method=None, properties=None, auto_decode=True):
        super(Message, self).__init__(channel, body, method, properties, auto_decode)
        self._decode_cache = dict()

    @staticmethod
    def create(channel, body, properties=None):
        """创建消息对象并返回
        """
        properties = dict(properties or {})
        if 'correlation_id' not in properties:
            properties['correlation_id'] = str(uuid.uuid4())
        if 'message_id' not in properties:
            properties['message_id'] = str(uuid.uuid4())
        if 'timestamp' not in properties:
            properties['timestamp'] = datetime.utcnow()

        return Message(channel, auto_decode=False, body=body, properties=properties)

    @property
    def body(self):
        """返回消息体（如果 auto_decode=True 就对消息体进行编码生成字节序列）
        """
        if not self._auto_decode:
            return self._body
        if 'body' in self._decode_cache:
            return self._decode_cache['body']
        body = try_utf8_decode(self._body)
        self._decode_cache['body'] = body
        return body

    @property
    def channel(self):
        return self._channel

    @property
    def method(self):
        """Return the Message Method.

            If auto_decode is enabled, all strings will automatically be
            decoded using decode('utf-8') if possible.

        :rtype: dict
        """
        return self._try_decode_utf8_content(self._method, 'method')

    @property
    def properties(self):
        """Returns the Message Properties.

            If auto_decode is enabled, all strings will automatically be
            decoded using decode('utf-8') if possible.

        :rtype: dict
        """
        return self._try_decode_utf8_content(self._properties, 'properties')

    def ack(self):
        """向服务器发送消息的 “消费确认” 数据帧
        """
        if not self._method:
            raise AMQPMessageError('Message.ack only available on incoming messages')
        # 每次发送消费确认数据帧，都会带回来 1 条【消息】（至少 3 个数据帧）
        self._channel.basic.ack(delivery_tag=self.delivery_tag)

    def nack(self, requeue=True):
        """Negative Acknowledgement.

        :raises AMQPInvalidArgument: Invalid Parameters
        :raises AMQPChannelError: Raises if the channel encountered an error.
        :raises AMQPConnectionError: Raises if the connection
                                     encountered an error.

        :param bool requeue: Re-queue the message
        """
        if not self._method:
            raise AMQPMessageError(
                'Message.nack only available on incoming messages'
            )
        self._channel.basic.nack(delivery_tag=self.delivery_tag,
                                 requeue=requeue)

    def reject(self, requeue=True):
        """拒收消息

        Args:
            requeue: 是否重新放回队列，设为 “否” 时，消息就会变成死信
        """
        if not self._method:
            raise AMQPMessageError('Message.reject only available on incoming messages')
        self._channel.basic.reject(delivery_tag=self.delivery_tag, requeue=requeue)

    def publish(self, routing_key, exchange='', mandatory=False, immediate=False):
        """向消息队列发送消息

        Args:
            routing_key : 消息路由键，交换机据此判断将消息转发到哪些队列
            exchange    : 交换机
            mandatory   : 服务器收到无法被转发到任何队列的消息时，是否返回一个 Basic.Return 数据帧
            immediate   : 服务器转发消息到队列，如果队列未绑定接受者，是否返回一个 Basic.Return 数据帧
        """
        return self._channel.basic.publish(
            body=self._body,
            routing_key=routing_key,
            exchange=exchange,
            properties=self._properties,
            mandatory=mandatory,
            immediate=immediate
        )

    @property
    def app_id(self):
        """Get AMQP Message attribute: app_id.

        :return:
        """
        return self.properties.get('app_id')

    @app_id.setter
    def app_id(self, value):
        """Set AMQP Message attribute: app_id.

        :return:
        """
        self._update_properties('app_id', value)

    @property
    def message_id(self):
        """Get AMQP Message attribute: message_id.

        :return:
        """
        return self.properties.get('message_id')

    @message_id.setter
    def message_id(self, value):
        """Set AMQP Message attribute: message_id.

        :return:
        """
        self._update_properties('message_id', value)

    @property
    def content_encoding(self):
        """Get AMQP Message attribute: content_encoding.

        :return:
        """
        return self.properties.get('content_encoding')

    @content_encoding.setter
    def content_encoding(self, value):
        """Set AMQP Message attribute: content_encoding.

        :return:
        """
        self._update_properties('content_encoding', value)

    @property
    def content_type(self):
        """Get AMQP Message attribute: content_type.

        :return:
        """
        return self.properties.get('content_type')

    @content_type.setter
    def content_type(self, value):
        """Set AMQP Message attribute: content_type.

        :return:
        """
        self._update_properties('content_type', value)

    @property
    def correlation_id(self):
        """Get AMQP Message attribute: correlation_id.

        :return:
        """
        return self.properties.get('correlation_id')

    @correlation_id.setter
    def correlation_id(self, value):
        """Set AMQP Message attribute: correlation_id.

        :return:
        """
        self._update_properties('correlation_id', value)

    @property
    def delivery_mode(self):
        """Get AMQP Message attribute: delivery_mode.

        :return:
        """
        return self.properties.get('delivery_mode')

    @delivery_mode.setter
    def delivery_mode(self, value):
        """Set AMQP Message attribute: delivery_mode.

        :return:
        """
        self._update_properties('delivery_mode', value)

    @property
    def timestamp(self):
        """Get AMQP Message attribute: timestamp.

        :return:
        """
        return self.properties.get('timestamp')

    @timestamp.setter
    def timestamp(self, value):
        """Set AMQP Message attribute: timestamp.

        :return:
        """
        self._update_properties('timestamp', value)

    @property
    def priority(self):
        """Get AMQP Message attribute: priority.

        :return:
        """
        return self.properties.get('priority')

    @priority.setter
    def priority(self, value):
        """Set AMQP Message attribute: priority.

        :return:
        """
        self._update_properties('priority', value)

    @property
    def reply_to(self):
        """Get AMQP Message attribute: reply_to.

        :return:
        """
        return self.properties.get('reply_to')

    @reply_to.setter
    def reply_to(self, value):
        """Set AMQP Message attribute: reply_to.

        :return:
        """
        self._update_properties('reply_to', value)

    @property
    def message_type(self):
        """Get AMQP Message attribute: message_type.

        :return:
        """
        return self.properties.get('message_type')

    @message_type.setter
    def message_type(self, value):
        """Set AMQP Message attribute: message_type.

        :return:
        """
        self._update_properties('message_type', value)

    @property
    def expiration(self):
        """Get AMQP Message attribute: expiration.

        :return:
        """
        return self.properties.get('expiration')

    @expiration.setter
    def expiration(self, value):
        """Set AMQP Message attribute: expiration.

        :return:
        """
        self._update_properties('expiration', value)

    @property
    def user_id(self):
        """Get AMQP Message attribute: user_id.

        :return:
        """
        return self.properties.get('user_id')

    @user_id.setter
    def user_id(self, value):
        """Set AMQP Message attribute: user_id.

        :return:
        """
        self._update_properties('user_id', value)

    @property
    def redelivered(self):
        """Indicates if this message may have been delivered before (but not
        acknowledged).

        :rtype: bool,None
        """
        if not self._method:
            return None
        return self._method.get('redelivered')

    @property
    def delivery_tag(self):
        """Server-assigned delivery tag.

        :rtype: int,None
        """
        if not self._method:
            return None
        return self._method.get('delivery_tag')

    def json(self):
        """Deserialize the message body, if it is JSON.

        :return:
        """
        return json.loads(self.body)

    def _update_properties(self, name, value):
        """Update properties, and keep cache up-to-date if auto decode is
        enabled.

        :param str name: Key
        :param obj value: Value
        :return:
        """
        if self._auto_decode and 'properties' in self._decode_cache:
            self._decode_cache['properties'][name] = value
        self._properties[name] = value

    def _try_decode_utf8_content(self, content, content_type):
        """Generic function to decode content.

        :param object content:
        :return:
        """
        if not self._auto_decode or not content:
            return content
        if content_type in self._decode_cache:
            return self._decode_cache[content_type]
        if isinstance(content, dict):
            content = self._try_decode_dict(content)
        else:
            content = try_utf8_decode(content)
        self._decode_cache[content_type] = content
        return content

    def _try_decode_dict(self, content):
        """Decode content of a dictionary.

        :param dict content:
        :return:
        """
        result = dict()
        for key, value in content.items():
            key = try_utf8_decode(key)
            if isinstance(value, dict):
                result[key] = self._try_decode_dict(value)
            elif isinstance(value, list):
                result[key] = self._try_decode_list(value)
            elif isinstance(value, tuple):
                result[key] = self._try_decode_tuple(value)
            else:
                result[key] = try_utf8_decode(value)
        return result

    @staticmethod
    def _try_decode_list(content):
        """Decode content of a list.

        :param list,tuple content:
        :return:
        """
        result = list()
        for value in content:
            result.append(try_utf8_decode(value))
        return result

    @staticmethod
    def _try_decode_tuple(content):
        """Decode content of a tuple.

        :param tuple content:
        :return:
        """
        return tuple(Message._try_decode_list(content))
