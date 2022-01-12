import threading
import weakref

from django.utils.inspect import func_accepts_kwargs


def _make_id(target):
    if hasattr(target, '__func__'):
        return (id(target.__self__), id(target.__func__))
    return id(target)


NONE_ID = _make_id(None)

# A marker for caching
NO_RECEIVERS = object()


class Signal:
    """信号基类

    使用说明:
        1. 对当前类的子类进行实例化，创建「信号对象」
           通常需要在配置文件的 INSTALLED_APPS 配置项中配置这个「信号对象」，也就是引用「信号对象」所在模块
           其目的就是在项目启动时创建「信号对象」
        2. 调用 self.connect 建立「信号接收者」与「信号发送者」之间的连接，其实就是二元元组
           当前模块下有个 receiver 函数，它是一个装饰器，用于调用 self.connect 方法
           通常「信号接收者」也是一个函数，在功能上相当于回调函数，创建该函数时使用 receiver 装饰器就可以了
        3. 在需要的地方调用「信号对象」的 send 方法发送信号
           调用 self.send 方法发送信号的时候提供「信号发送者」和调用「信号接收者」所需参数
           这样就可以根据「信号发送者」找到对应的一系列「信号接收者」并依次调用
    """

    def __init__(self, providing_args=None, use_caching=False):
        """初始化「信号对象」

        Arguments:
            :providing_args: 发送信号时传递参数的列表，此列表仅为程序编写者作展示之用
        """
        self.receivers = []
        if providing_args is None:
            providing_args = []
        self.providing_args = set(providing_args)
        self.lock = threading.Lock()
        self.use_caching = use_caching
        # For convenience we create empty caches even if they are not used.
        # A note about caching: if use_caching is defined, then for each
        # distinct sender we cache the receivers that sender has in
        # 'sender_receivers_cache'. The cache is cleaned when .connect() or
        # .disconnect() is called and populated on send().
        self.sender_receivers_cache = weakref.WeakKeyDictionary() if use_caching else {}
        self._dead_receivers = False

    def connect(self, receiver, sender=None, weak=True, dispatch_uid=None):
        """针对当前信号对象 self 建立「信号接收者」与「信号发送者」之间的连接，简言之就是建立连接

        所谓「信号接收者」其实就是一个可调用对象，通常是函数，可以称之为回调函数
        所谓“建立连接”其实就是把「信号接收者」和「信号发送者」的内存地址组成二元元组，设为 a
        然后「信号接收者」这个函数本身设为 b ，这样构成一个元组 (a, b) ，再把它放到 self.receivers 列表里备用
        当 self.send 执行时，循环 self.receivers 列表，找到可用的回调函数并调用之

        Arguments:
            :receiver:「信号接收者」，必须是可调用对象，调用时必须可以接受关键字参数
            :sender:「信号发送者」，可以是任意 Python 对象，如果是 None ，表示全部「信号发送者」
            :weak: 弱引用，待补充
            :dispatch_uid: 待补充
        """
        from django.conf import settings

        if settings.configured and settings.DEBUG:
            #「信号接收者」即回调函数必须是可调用对象
            assert callable(receiver), "Signal receivers must be callable."

            #「信号接收者」即回调函数必须可以接受关键字参数
            if not func_accepts_kwargs(receiver):
                raise ValueError("Signal receivers must accept keyword arguments (**kwargs).")

        # 这个 lookup_key 查询键是可散列对象，通常是一个由「信号接收者」和「信号发送者」的内存地址组成的二元元组
        if dispatch_uid:
            lookup_key = (dispatch_uid, _make_id(sender))
        else:
            lookup_key = (_make_id(receiver), _make_id(sender))

        if weak:
            ref = weakref.ref
            receiver_object = receiver
            # Check for bound methods
            if hasattr(receiver, '__self__') and hasattr(receiver, '__func__'):
                ref = weakref.WeakMethod
                receiver_object = receiver.__self__
            receiver = ref(receiver)
            weakref.finalize(receiver_object, self._remove_receiver)

        with self.lock:
            self._clear_dead_receivers()
            if not any(r_key == lookup_key for r_key, _ in self.receivers):
                self.receivers.append((lookup_key, receiver))
            self.sender_receivers_cache.clear()

    def disconnect(self, receiver=None, sender=None, dispatch_uid=None):
        """
        Disconnect receiver from sender for signal.

        If weak references are used, disconnect need not be called. The receiver
        will be removed from dispatch automatically.

        Arguments:

            receiver
                The registered receiver to disconnect. May be none if
                dispatch_uid is specified.

            sender
                The registered sender to disconnect

            dispatch_uid
                the unique identifier of the receiver to disconnect
        """
        if dispatch_uid:
            lookup_key = (dispatch_uid, _make_id(sender))
        else:
            lookup_key = (_make_id(receiver), _make_id(sender))

        disconnected = False
        with self.lock:
            self._clear_dead_receivers()
            for index in range(len(self.receivers)):
                (r_key, _) = self.receivers[index]
                if r_key == lookup_key:
                    disconnected = True
                    del self.receivers[index]
                    break
            self.sender_receivers_cache.clear()
        return disconnected

    def has_listeners(self, sender=None):
        return bool(self._live_receivers(sender))

    def send(self, sender, **named):
        """发送信号给「信号接收者」（其实就是调用函数）

        在此之前，「信号接收者」已经与「信号发送者」建立连接
        当前方法会将 named 字典作为关键字参数依次调用各个「信号接收者（可调用对象）」
        任意「信号接收者」被调用时如果出现异常，立刻抛出并终止当前函数

        Arguments:
            :sender:「信号发送者」可以是任意 Python 对象，如果是 None ，表示全部「信号发送者」
            :named: 调用「信号接收者」时提供的关键字参数

        Return: 列表，列表里面是二元元组 (「信号接收者」, 调用「信号接收者」的返回值)
        """
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return []

        return [
            (receiver, receiver(signal=self, sender=sender, **named))
            for receiver in self._live_receivers(sender)
        ]

    def send_robust(self, sender, **named):
        """发送信号给「信号接收者」（其实就是调用函数）

        在此之前，「信号接收者」已经与「信号发送者」建立连接
        当前方法会将 named 字典作为参数依次调用各个「信号接收者（可调用对象）」
        任意「信号接收者」被调用时如果出现异常，不抛出异常，而是将异常对象作为返回值二元元组的第二个值返回

        Arguments:
            :sender:「信号发送者」可以是任意 Python 对象，如果是 None ，表示全部「信号发送者」
            :named: 调用「信号接收者」时提供的参数

        Return: 列表，列表里面是二元元组 (「信号接收者」, 调用「信号接收者」的返回值或异常对象)
        """
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return []

        # Call each receiver with whatever arguments it can accept.
        # Return a list of tuple pairs [(receiver, response), ... ].
        responses = []
        for receiver in self._live_receivers(sender):
            try:
                response = receiver(signal=self, sender=sender, **named)
            except Exception as err:
                responses.append((receiver, err))
            else:
                responses.append((receiver, response))
        return responses

    def _clear_dead_receivers(self):
        # Note: caller is assumed to hold self.lock.
        if self._dead_receivers:
            self._dead_receivers = False
            self.receivers = [
                r for r in self.receivers
                if not(isinstance(r[1], weakref.ReferenceType) and r[1]() is None)
            ]

    def _live_receivers(self, sender):
        """
        Filter sequence of receivers to get resolved, live receivers.

        This checks for weak references and resolves them, then returning only
        live receivers.
        """
        receivers = None
        if self.use_caching and not self._dead_receivers:
            receivers = self.sender_receivers_cache.get(sender)
            # We could end up here with NO_RECEIVERS even if we do check this case in
            # .send() prior to calling _live_receivers() due to concurrent .send() call.
            if receivers is NO_RECEIVERS:
                return []
        if receivers is None:
            with self.lock:
                self._clear_dead_receivers()
                senderkey = _make_id(sender)
                receivers = []
                for (receiverkey, r_senderkey), receiver in self.receivers:
                    if r_senderkey == NONE_ID or r_senderkey == senderkey:
                        receivers.append(receiver)
                if self.use_caching:
                    if not receivers:
                        self.sender_receivers_cache[sender] = NO_RECEIVERS
                    else:
                        # Note, we must cache the weakref versions.
                        self.sender_receivers_cache[sender] = receivers
        non_weak_receivers = []
        for receiver in receivers:
            if isinstance(receiver, weakref.ReferenceType):
                # Dereference the weak reference.
                receiver = receiver()
                if receiver is not None:
                    non_weak_receivers.append(receiver)
            else:
                non_weak_receivers.append(receiver)
        return non_weak_receivers

    def _remove_receiver(self, receiver=None):
        # Mark that the self.receivers list has dead weakrefs. If so, we will
        # clean those up in connect, disconnect and _live_receivers while
        # holding self.lock. Note that doing the cleanup here isn't a good
        # idea, _remove_receiver() will be called as side effect of garbage
        # collection, and so the call can happen while we are already holding
        # self.lock.
        self._dead_receivers = True


def receiver(signal, **kwargs):
    """为「信号发送者」和「信号接收者」建立连接的装饰器

    调用此装饰器时须提供「信号对象」或其列表作为参数，也可以选择性提供「信号发送者」作为参数
    通常在定义「信号接收者」这个函数的时候使用此装饰器，两个示例如下:

        @receiver(post_save, sender=MyModel)
        def signal_receiver(sender, **kwargs):
            ...

        @receiver([post_save, post_delete], sender=MyModel)
        def signals_receiver(sender, **kwargs):
            ...
    """
    def _decorator(func):
        if isinstance(signal, (list, tuple)):
            for s in signal:
                s.connect(func, **kwargs)
        else:
            signal.connect(func, **kwargs)
        return func
    return _decorator
