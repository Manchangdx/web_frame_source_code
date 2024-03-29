"""AMQPStorm Rpc."""

import threading
import time
from uuid import uuid4

from amqpstorm.base import IDLE_WAIT
from amqpstorm.exception import AMQPChannelError


class Rpc(object):
    """Internal RPC handler.

    :param object default_adapter: Connection or Channel.
    :param int,float timeout: Rpc timeout.
    """

    def __init__(self, default_adapter, timeout=360):
        self._lock = threading.Lock()
        self._default_connection_adapter = default_adapter  # channel.Channel()
        self._timeout = timeout
        self._response = {}
        self._request = {}

    @property
    def lock(self):
        return self._lock

    def on_frame(self, frame_in):
        """处理服务器发给信道的消息，参数 frame_in 是数据帧
        """
        # 只有 RPC 请求才会记录在 self._request 中
        if frame_in.name not in self._request:
            return False

        uuid = self._request[frame_in.name]
        if self._response[uuid]:
            self._response[uuid].append(frame_in)
        else:
            self._response[uuid] = [frame_in]
        return True

    def register_request(self, valid_responses):
        """记录一次 RPC 请求
        """
        uuid = str(uuid4())
        self._response[uuid] = []
        for action in valid_responses:
            self._request[action] = uuid
        return uuid

    def remove(self, uuid):
        """Remove any data related to a specific RPC request.

        :param str uuid: Rpc Identifier.
        :return:
        """
        self.remove_request(uuid)
        self.remove_response(uuid)

    def remove_request(self, uuid):
        """Remove any RPC request(s) using this uuid.

        :param str uuid: Rpc Identifier.
        :return:
        """
        for key in list(self._request):
            if self._request[key] == uuid:
                del self._request[key]

    def remove_response(self, uuid):
        """Remove a RPC Response using this uuid.

        :param str uuid: Rpc Identifier.
        :return:
        """
        if uuid in self._response:
            del self._response[uuid]

    def get_request(self, uuid, raw=False, multiple=False, connection_adapter=None):
        """获取服务器返回的数据帧，每次信道向服务器发出 RPC 请求后，都会调用此方法等待并返回响应
        """
        if uuid not in self._response:
            return

        # 阻塞运行，等待服务器响应
        self._wait_for_request(uuid, connection_adapter or self._default_connection_adapter)
        # 从 self._response[uuid] 列表中获取服务器返回的数据帧
        frame = self._get_response_frame(uuid)

        if not multiple:
            self.remove(uuid)
        result = None
        if raw:
            result = frame
        elif frame is not None:
            result = dict(frame)
        return result

    def _get_response_frame(self, uuid):
        frame = None
        frames = self._response.get(uuid, None)
        if frames:
            frame = frames.pop(0)
        return frame

    def _wait_for_request(self, uuid, connection_adapter=None):
        """等待服务器返回数据帧

        每次信道发出 RPC 请求给服务器后，都会调用此方法等待响应
        信道收到响应后，就会调用 self.on_frame 方法将响应数据帧放到 self._response[uuid] 的列表里面
        然后此方法就会结束运行（或者超时抛出异常）
        """
        start_time = time.time()
        while not self._response[uuid]:
            connection_adapter.check_for_errors()
            if time.time() - start_time > self._timeout:
                self._raise_rpc_timeout_error(uuid)
            time.sleep(IDLE_WAIT)

    def _raise_rpc_timeout_error(self, uuid):
        """Gather information and raise an Rpc exception.

        :param str uuid: Rpc Identifier.
        :return:
        """
        requests = []
        for key, value in self._request.items():
            if value == uuid:
                requests.append(key)
        self.remove(uuid)
        message = (
            'rpc requests %s (%s) took too long' %
            (
                uuid,
                ', '.join(requests)
            )
        )
        raise AMQPChannelError(message)
