# -*- encoding: utf-8 -*-
"""Manage the marshaling and unmarshaling of AMQP frames

unmarshal will turn a raw AMQP byte stream into the appropriate AMQP objects
from the specification file.

marshal will take an object created from the specification file and turn it
into a raw byte stream.

"""
import logging
import struct

from pamqp import (body, decode, exceptions, header, heartbeat, PYTHON3,
                   specification)

AMQP = b'AMQP'
FRAME_HEADER_SIZE = 7
FRAME_END_CHAR = chr(specification.FRAME_END)
DECODE_FRAME_END_CHAR = FRAME_END_CHAR
if PYTHON3:
    FRAME_END_CHAR = bytes((specification.FRAME_END, ))
    DECODE_FRAME_END_CHAR = specification.FRAME_END
LOGGER = logging.getLogger(__name__)
UNMARSHAL_FAILURE = 0, 0, None


def unmarshal(data_in):
    """对二进制数据流进行反序列化，生成（解析的字节数，信道编号，Frame 对象）

    Frame 对象就是数据帧，包括 5 种类型:
        header.ProtocolHeader   协议头帧
        heartbeat.Heartbeat     心跳帧
        specification.Frame     方法帧
        header.ContentHeader    消息头帧
        body.ContentBody        消息体帧

    参数 data_in 可能是很多条数据帧合起来的字节序列，从左向右依次读取即可
    首先前 7 个字节肯定是数据帧的头部，分析头部信息然后进一步处理
    该函数每次只解析 1 个数据帧
    """

    # 如果是协议头帧对应的字节序列
    #   字节数量是 8 个：头部 7；载荷 0；结束符 1
    #   零号信道
    #   数据帧是 header.ProtocolHeader
    try:
        frame_value = _unmarshal_protocol_header_frame(data_in)
        if frame_value:
            return 8, 0, frame_value
    except ValueError as error:
        raise exceptions.UnmarshalingException(header.ProtocolHeader, error)

    # 解析头部，也就是前 7 个字节，得到三个整数：数据帧类型编号、信道编号、载荷字节数
    frame_type, channel_id, frame_size = frame_parts(data_in)

    # 如果是心跳帧，并且载荷为空
    if frame_type == specification.FRAME_HEARTBEAT and frame_size == 0:
        return 8, channel_id, heartbeat.Heartbeat()

    # 除了协议头帧和心跳帧，载荷不能为空
    if not frame_size:
        raise exceptions.UnmarshalingException('Unknown', 'No frame size')

    # 整个数据帧的字节数 = 7 头部 + 载荷字节数 + 1 结束符
    byte_count = FRAME_HEADER_SIZE + frame_size + 1
    if byte_count > len(data_in):
        raise exceptions.UnmarshalingException('Unknown', 'Not all data received')
    if data_in[byte_count - 1] != DECODE_FRAME_END_CHAR:
        raise exceptions.UnmarshalingException('Unknown', 'Last byte error')

    # 整个数据帧的载荷部分的字节序列
    frame_data = data_in[FRAME_HEADER_SIZE: byte_count - 1]

    # 如果是方法帧
    if frame_type == specification.FRAME_METHOD:
        return byte_count, channel_id, _unmarshal_method_frame(frame_data)

    # 如果是消息头帧
    elif frame_type == specification.FRAME_HEADER:
        return byte_count, channel_id, _unmarshal_header_frame(frame_data)

    # 如果是消息体帧
    elif frame_type == specification.FRAME_BODY:
        return byte_count, channel_id, _unmarshal_body_frame(frame_data)

    raise exceptions.UnmarshalingException('Unknown', 'Unknown frame type: {}'.format(frame_type))


def marshal(frame_value, channel_id):
    """frame + channel_id → 数据帧字节序列
    """

    #【协议头帧】
    # 在创建 TCP 连接后发出的第一次 AMQP 握手数据帧就是协议头帧，它有专门的发送路径，不会经过此处
    if isinstance(frame_value, header.ProtocolHeader):
        return frame_value.marshal()

    #【方法帧】
    # 例如 AMQP 握手期间用到的 Connection.Open、声明交换机时用到的 Exchange.Declare、发送消息时用到的 Basic.Publish 等
    # 以最后一个为例，其关键信息是 exchange 和 routing_key
    # 此外在方法帧对象序列化时还需要将 frame 对象的类标识放进去，占 4 个字节
    if isinstance(frame_value, specification.Frame):
        return _marshal_method_frame(frame_value, channel_id)

    #【消息头帧】
    # 关键信息是消息体长度
    if isinstance(frame_value, header.ContentHeader):
        return _marshal_content_header_frame(frame_value, channel_id)

    #【消息体帧】
    # 关键信息是二进制消息本身
    if isinstance(frame_value, body.ContentBody):
        return _marshal_content_body_frame(frame_value, channel_id)

    #【心跳帧】
    # 没有关键信息，只是隔段时间告诉服务器客户端还在
    if isinstance(frame_value, heartbeat.Heartbeat):
        return frame_value.marshal()

    raise ValueError('Could not determine frame type: {}'.format(frame_value))


def _unmarshal_protocol_header_frame(data_in):
    """Attempt to unmarshal a protocol header frame

    The ProtocolHeader is abbreviated in size and functionality compared to
    the rest of the frame types, so return UNMARSHAL_ERROR doesn't apply
    as cleanly since we don't have all of the attributes to return even
    regardless of success or failure.

    :param bytes data_in: Raw byte stream data
    :rtype: header.ProtocolHeader
    :raises: ValueError

    """
    # Do the first four bytes match?
    if data_in[0:4] == AMQP:
        frame = header.ProtocolHeader()
        frame.unmarshal(data_in)
        return frame


def _unmarshal_method_frame(frame_data):
    """将「方法帧」的字节序列转换成 Frame 对象
    """
    # Get the Method Index from the class data
    bytes_used, method_index = decode.long_int(frame_data[0:4])

    # Create an instance of the method object we're going to unmarshal
    try:
        method = specification.INDEX_MAPPING[method_index]()
    except KeyError:
        raise exceptions.UnmarshalingException(
            'Unknown', 'Unknown method index: {}'.format(str(method_index)))

    # Unmarshal the data
    try:
        method.unmarshal(frame_data[bytes_used:])
    except struct.error as error:
        raise exceptions.UnmarshalingException(method, error)

    #  Unmarshal the data in the object and return it
    return method


def _unmarshal_header_frame(frame_data):
    """Attempt to unmarshal a header frame

    :param bytes frame_data: Raw frame data to assign to our header frame
    :return tuple: Amount of data consumed and the frame object

    """
    content_header = header.ContentHeader()
    try:
        content_header.unmarshal(frame_data)
    except struct.error as error:
        raise exceptions.UnmarshalingException('ContentHeader', error)
    return content_header


def _unmarshal_body_frame(frame_data):
    """Attempt to unmarshal a body frame

    :param bytes frame_data: Raw frame data to assign to our body frame
    :return tuple: Amount of data consumed and the frame object

    """
    content_body = body.ContentBody()
    content_body.unmarshal(frame_data)
    return content_body


def frame_parts(data_in):
    try:
        # 这块儿是固定的，前 7 个字节: 1 数据帧类型编号 2 信道编号 3 载荷字节数
        return struct.unpack('>BHI', data_in[0:FRAME_HEADER_SIZE])
    except struct.error:
        return UNMARSHAL_FAILURE


def _marshal(frame_type, channel_id, payload):
    """数据帧 → 字节序列

    Args:
        frame_type  : 数据帧编号（1 方法帧；2 消息头帧；3 消息体帧）
        channel_id  : 信道编号
        payload     : 载荷
    """
    return b''.join([
        struct.pack('>BHI', frame_type, channel_id, len(payload)),  # 头部 7 字节（1 数据帧编号；2 信道编号；4 载荷字节数）
        payload,                                                    # 载荷
        FRAME_END_CHAR                                              # 结束符 1 字节
    ])


def _marshal_content_body_frame(frame_value, channel_id):
    """消息体帧 → 字节序列
    """
    return _marshal(specification.FRAME_BODY, channel_id, frame_value.marshal())


def _marshal_content_header_frame(frame_value, channel_id):
    """消息头帧 → 字节序列
    """
    return _marshal(specification.FRAME_HEADER, channel_id, frame_value.marshal())


def _marshal_method_frame(frame_value, channel_id):
    """方法帧 → 字节序列
    """
    return _marshal(
        specification.FRAME_METHOD,
        channel_id,
        struct.pack('>I', frame_value.index) + frame_value.marshal()
    )
