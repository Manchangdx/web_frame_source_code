"""AMQPStorm Connection.Heartbeat."""

import logging
import threading

from amqpstorm.exception import AMQPConnectionError

logger = logging.getLogger(__name__)


class Heartbeat(object):
    """心跳控制器 💓
    """

    def __init__(self, interval, send_heartbeat_impl, timer=threading.Timer):
        """初始化心跳控制器

        Args:
            interval: 心跳时间间隔
            send_heartbeat_impl: 发送心跳帧的方法
        """
        self.send_heartbeat_impl = send_heartbeat_impl
        self.timer_impl = timer
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._timer = None
        self._exceptions = None
        self._reads_since_check = 0
        self._writes_since_check = 0
        self._interval = interval
        self._threshold = 0

    def register_read(self):
        """从服务器读取数据次数 +1
        """
        self._reads_since_check += 1

    def register_write(self):
        """向服务器写入次数 +1
        """
        self._writes_since_check += 1

    def start(self, exceptions):
        """启动心跳检查
        """
        if not self._interval:
            return False
        logger.info('[amqpstorm.heartbeat.Heartbeat.start] 启动心跳检查')
        # 把自己的 “线程事件” 设为 “已设置” 状态
        self._running.set()
        with self._lock:
            self._threshold = 0
            self._reads_since_check = 0
            self._writes_since_check = 0
        self._exceptions = exceptions
        logger.debug('Heartbeat Checker Started')
        # 启动心跳检查
        return self._start_new_timer()

    def stop(self):
        """Stop the Heartbeat Checker.

        :return:
        """
        self._running.clear()
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = None

    def _check_for_life_signs(self):
        """线程计时器要执行的用于检查连接状态的函数

        首先检查是否有任何数据被发送，如果没有，向服务器发送一个心跳信号。
        如果在两个时间间隔内都没有接收到任何数据，抛出一个异常，以便关闭连接。
        """
        # 如果自身的 “线程事件” 处于 “未设置” 状态，直接返回
        if not self._running.is_set():
            return False

        # 如果从上次检查心跳到现在一直没有向 RabbitMQ 服务器发送过数据
        # 向 RabbitMQ 服务器发送一个心跳帧
        if self._writes_since_check == 0:
            self.send_heartbeat_impl()

        self._lock.acquire()
        try:
            # 如果从上次检查心跳到现在一直没收到 RabbitMQ 服务器发来的数据
            # 记它一下，达到两下就给它抛异常
            if self._reads_since_check == 0:
                self._threshold += 1
                if self._threshold >= 2:
                    self._running.clear()
                    self._raise_or_append_exception()
                    return False
            else:
                self._threshold = 0
        finally:
            self._reads_since_check = 0
            self._writes_since_check = 0
            self._lock.release()

        # 无限循环
        return self._start_new_timer()

    def _raise_or_append_exception(self):
        """The connection is presumably dead and we need to raise or
        append an exception.

            If we have a list for exceptions, append the exception and let
            the connection handle it, if not raise the exception here.

        :return:
        """
        message = (
            'Connection dead, no heartbeat or data received in >= '
            '%ds' % (
                self._interval * 2
            )
        )
        why = AMQPConnectionError(message)
        if self._exceptions is None:
            raise why
        self._exceptions.append(why)

    def _start_new_timer(self):
        """创建一个用于定期检查连接的心跳的计时器，并且启动心跳检查
        """
        if not self._running.is_set():
            return False
        self._timer = self.timer_impl(
            interval=self._interval,
            function=self._check_for_life_signs
        )
        self._timer.daemon = True
        self._timer.start()
        return True
