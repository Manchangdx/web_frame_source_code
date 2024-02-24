"""AMQPStorm Channel.Tx."""

import logging

from pamqp import specification

from amqpstorm.base import Handler

LOGGER = logging.getLogger(__name__)


class Tx(Handler):
    """RabbitMQ 事务
    """
    __slots__ = ['_tx_active']

    def __init__(self, channel):
        self._tx_active = True
        super(Tx, self).__init__(channel)

    def __enter__(self):
        self.select()
        return self

    def __exit__(self, exception_type, exception_value, _):
        if exception_type:
            LOGGER.warning(f'Leaving Transaction on exception: {exception_type}')
            if self._tx_active:
                self.rollback()
            return
        if self._tx_active:
            self.commit()

    def select(self):
        """在信道上启动事务

        事务内，客户端发送的消息会放在服务器的缓存区，等待客户端提交或回滚
        每个信道都可以启动事务，也就是说事务是跟随信道的
        注意：信道不能设置消息发送确认机制，因为消息到服务器那里要延迟发送到队列的，客户端如果阻塞等待就没完了
        """
        # 将信道设为事务开启状态
        self._tx_active = True
        # 发送一个「启动事务」数据帧给服务器
        return self._channel.rpc_request(specification.Tx.Select())

    def commit(self):
        """通知服务器 “提交” 当前事务期间缓存的所有消息，并立刻开启新的事务
        """
        self._tx_active = False
        return self._channel.rpc_request(specification.Tx.Commit())

    def rollback(self):
        """通知服务器 “清除” 当前事务期间缓存的所有消息，并立刻开启新的事务
        """
        self._tx_active = False
        return self._channel.rpc_request(specification.Tx.Rollback())
