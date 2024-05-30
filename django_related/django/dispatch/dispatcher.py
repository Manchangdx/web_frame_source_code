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

    功能说明:
        Django 的信号机制采用了 Observer Pattern 观察者设计模式
        该设计模式有三个概念：事件（信号对象）、被观察者（信号发送者）、观察者（信号接收者）
        当被观察者发生事件变动时，观察者就会立刻做出反应

        举例说明，创建 User 类实例后，需要执行用户数量计数器 +1 操作
        常规做法:
            1. 编写计数器方法
            2. 在创建 User 实例的代码后面增加调用计数器方法的代码
        使用信号机制:
            1. 首先创建一个事件 create_obj（信号对象）
            2. 然后将 User 类作为被观察者（信号发送者）、计数器方法作为观察者（信号接收者）注册到 create_obj 上
            3. 最后在创建 User 实例的逻辑中写入 create_obj.send(User) 即可
            4. 调用信号对象的 send 方法时，根据参数 sender 查询信号接收者列表并依次调用它们

        有了信号对象作为事件，我们可以设置多组 [被观察者 - 事件 - 观察者] 的关联关系，例如:
            Course - create_obj - 发送课程上线通知
            Course - delete_obj - 设置用户学习课程数量 -1
            Lab    - create_obj - 发送章节变更通知
            Lab    - create_obj - 设置课程章节数量 +1

        综上所述，[被观察者 - 事件 - 观察者] 这三个概念在「观察者模式」中是多对多的关系
        而且 signal.send 与 callback_func 互相解耦，变更其中任何一个都不会影响对方

        使用信号机制的好处:
            1. 代码分离，信号接收者/回调函数（观察者）可以写到任意合理的位置
            2. 可扩展，对于一个信号发送者（被观察者）而言，可以随时增减信号接收者（观察者）
            3. 低耦合，上述两点使得代码耦合度降低

    使用说明:
        1. 对当前类或其子类进行实例化，创建「信号对象」
           通常需要在配置文件的 INSTALLED_APPS 配置项中配置这个「信号对象」，也就是引用「信号对象」所在模块
           其目的就是在项目启动时创建「信号对象」
        2. 调用 self.connect 建立「信号发送者」与「信号接收者」之间的关联，其实就是二元元组
              「信号发送者」可以是任意 Python 对象，也可以是 None
              「信号接收者」必须是可调用对象，通常是一个函数，在功能上相当于回调函数
           通常在创建「信号接收者」回调函数时捎带手给「信号发送者」和「信号接收者」建立关联
           当前模块下有个 receiver 函数，它是一个装饰器，用于调用 self.connect 方法，使用此装饰器创建「信号接收者」是常规操作
        3. 在需要的地方调用「信号对象」的 send 方法发送信号
           调用 self.send 方法发送信号时需提供
               1.「信号发送者」
               2. 调用「信号接收者」所需的参数
           这样就可以根据「信号发送者」找到对应的一系列「信号接收者」并依次调用
        4.「信号发送者」和「信号接收者」是零耦合的，「信号对象」的工作就是集纳二者的关联关系，在需要时根据 sender 找到 receiver 并调用
           所以理论上「信号发送者」与「信号接收者」可能存在于多个「信号对象」里面，不过实际上这种情况不多
    """

    def __init__(self, providing_args=None, use_caching=False):
        """初始化「信号对象」

        Arguments:
            :providing_args:
                发送信号时传递参数的列表，此列表仅为编程人员作展示之用
                因为在发送信号也就是调用 self.send 方法时需要传入指定的参数，然后拿这些参数调用各个「信号接收者」也就是回调函数
                在同一个「信号对象」中调用不同的「信号接收者」时传入的参数的数量和顺序都是相同的
                所以在定义「信号对象」时注明参数就是为了便于编写回调函数
            :use_caching:
                是否使用缓存，默认为否
                每次调用 self.send 方法都会计算 sender 对应的 receiver 列表，然后顺便把它们放到缓存字典里
                这个缓存功能就是使用一个字典存储计算好的 sender 与 receiver 列表的对应关系，避免重复计算
                每注册一个「信号接收者」都会清空缓存字典
        """
        #「信号发送者」与「信号接收者」的关联列表
        # 每个关联关系都是一个元组，可能是这样：((接收者标识, 发送者标识), 接收者)
        # [
        #     (
        #         (140690433683664, 140690892268032),
        #         <weakref at 0x7ff50b52a1d0; to 'function' at 0x7ff50b52b0d0 (on_report_published)>
        #     )
        # ]
        self.receivers = []
        if providing_args is None:
            providing_args = []
        self.providing_args = set(providing_args)
        self.lock = threading.Lock()
        self.use_caching = use_caching
        # 设置缓存字典
        # 调用 self.send 方法发送信号时
        #    如果不需要清理无效 receiver 且对应的缓存不存在，会添加缓存
        #    如果需要清理无效 receiver ，会重置缓存
        # 调用 self.connect 方法注册信号时会清除缓存
        self.sender_receivers_cache = weakref.WeakKeyDictionary() if use_caching else {}
        # 是否需要整理关联关系表，也就是清理 self.receivers 表中的无效数据
        # 当某个 receiver 被垃圾回收器清理时，该属性值会被设置为 True
        self._dead_receivers = False

    def connect(self, receiver, sender=None, weak=True, dispatch_uid=None):
        """注册回调函数

        针对当前「信号对象」self 建立「信号发送者」与「信号接收者」之间的关联，简言之就是建立关联
        所谓 “建立关联” 其实就是把「信号接收者」和「信号发送者」的内存地址组成二元元组，设为 a
        然后「信号接收者」这个函数本身设为 b ，这样构成一个嵌套元组 (a, b) ，再把它放到 self.receivers 列表里备用
        当 self.send 执行时，循环 self.receivers 列表，根据 sender 找到可用的回调函数 b 并调用之

        Arguments:
            :receiver:「信号接收者」，必须是可调用对象，调用时必须可以接受关键字参数
            :sender:「信号发送者」，可以是任意 Python 对象（如果是 None ，表示发送信号给关联关系里发送者是 None 的全部「信号发送者」）
            :weak: 是否使用弱引用，默认使用弱引用，即创建一个对「信号接收者」弱引用的对象注册到 self.receivers 列表里
            :dispatch_uid: 待补充
        """
        from django.conf import settings

        if settings.configured and settings.DEBUG:
            #「信号接收者」必须是可调用对象
            assert callable(receiver), "Signal receivers must be callable."

            #「信号接收者」必须可以接受关键字参数
            if not func_accepts_kwargs(receiver):
                raise ValueError("Signal receivers must accept keyword arguments (**kwargs).")

        # 这个 lookup_key 查询键是可散列对象，通常是一个由「信号接收者」和「信号发送者」的内存地址组成的二元元组
        if dispatch_uid:
            lookup_key = (dispatch_uid, _make_id(sender))
        else:
            lookup_key = (_make_id(receiver), _make_id(sender))

        # 如果使用弱引用，也就是在记录 signal.receiver 列表时，把「信号接收者」的弱引用对象记录进去
        # 这种做法不会增加「信号接收者」的引用计数，从而避免阻碍垃圾回收器清理「信号接收者」
        if weak:
            ref = weakref.ref
            #「信号接收者」所属对象
            receiver_object = receiver
            # 如果「信号接收者」是类内部定义的实例方法（也叫做 bound method 绑定方法）
            if hasattr(receiver, '__self__') and hasattr(receiver, '__func__'):
                # 就需要专门的方法来创建弱引用对象了
                ref = weakref.WeakMethod
                receiver_object = receiver.__self__
            #「信号接收者」的弱引用
            receiver = ref(receiver)
            # 指定第一个参数对象被 Garbage Collector 垃圾回收器清理时调用的回调函数为第二个参数
            # 也就是当「信号接收者」被 GC 回收时，将 signal.receiver 列表设为需要整理的状态
            weakref.finalize(receiver_object, self._remove_receiver)

        with self.lock:
            # 清理无效的 receiver
            self._clear_dead_receivers()
            if not any(r_key == lookup_key for r_key, _ in self.receivers):
                self.receivers.append((lookup_key, receiver))
            # FIXME 此处应该加一个判断 self.use_caching 属性值的逻辑
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
        """发送信号给「信号接收者」（其实就是调用函数）（出现异常时立刻抛出）

        在此之前，「信号接收者」已经与「信号发送者」建立关联，所以：
        1. 首先要根据「信号发送者」查询到对应的「信号接收者」列表
        2. 然后将 named 字典作为关键字参数依次调用各个「信号接收者」
        3. 任意「信号接收者」被调用时如果出现异常，立刻抛出并终止当前函数

        Arguments:
            :sender:「信号发送者」，可以是任意 Python 对象（如果是 None ，表示发送信号给关联关系里发送者是 None 的全部「信号发送者」）
            :named: 调用「信号接收者」时提供的关键字参数

        Return:
            列表，列表里面是二元元组 (「信号接收者」, 调用「信号接收者」的返回值)
        """
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return []

        return [
            (receiver, receiver(signal=self, sender=sender, **named))
            for receiver in self._live_receivers(sender)
        ]

    def send_robust(self, sender, **named):
        """发送信号给「信号接收者」（其实就是调用回调函数）（出现异常时不抛出）

        在此之前，「信号接收者」已经与「信号发送者」建立关联，所以：
        1. 首先要根据「信号发送者」查询到「信号接收者」列表
        2. 然后将 named 字典作为关键字参数依次调用各个「信号接收者」
        3. 任意「信号接收者」被调用时如果出现异常，不抛出异常，而是将异常对象作为返回值二元元组的第二个值返回

        Arguments:
            :sender:「信号发送者」，可以是任意 Python 对象（如果是 None ，表示发送信号给关联关系里发送者是 None 的全部「信号发送者」）
            :named: 调用「信号接收者」时提供的参数

        Return:
            列表，列表里面是二元元组 (「信号接收者」, 调用「信号接收者」的返回值或异常对象)
        """
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return []

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
        """整理信号对象的关联关系表，清除无效的关联关系

        该方法必须在线程锁内执行
        调用 self.connect 注册信号和调用 self.send 发送信号时都会调用该方法
        """
        # 当某个 receiver 被垃圾回收器清理后，该属性会被设为 True ，这样就可以执行 “整理信号对象关联关系表” 的流程了
        if self._dead_receivers:
            self._dead_receivers = False
            self.receivers = [
                r for r in self.receivers
                if not(isinstance(r[1], weakref.ReferenceType) and r[1]() is None)
            ]

    def _live_receivers(self, sender):
        """从 self.receivers 关联关系列表里找到 sender 对应的 receiver 并返回列表
        """
        receivers = None

        # 如果信号对象使用了缓存功能并且不存在需要清除的 receiver，就从缓存字典里找到 receiver 列表
        if self.use_caching and not self._dead_receivers:
            receivers = self.sender_receivers_cache.get(sender)
            if receivers is NO_RECEIVERS:
                return []

        if receivers is None:
            with self.lock:
                # 整理信号对象的关联关系表，清除无效的关联关系
                self._clear_dead_receivers()
                senderkey = _make_id(sender)
                receivers = []
                # 循环关联关系的列表，找到 sender 对应的 receiver
                for (receiverkey, r_senderkey), receiver in self.receivers:
                    # 如果关联关系里的发送者是 None 或者与指定发送者相同
                    if r_senderkey == NONE_ID or r_senderkey == senderkey:
                        receivers.append(receiver)

                # 如果信号对象使用了缓存功能，就把 sender 和 receiver 列表放到缓存字典里
                if self.use_caching:
                    if not receivers:
                        self.sender_receivers_cache[sender] = NO_RECEIVERS
                    else:
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
        """将信号对象设为：需要整理关联关系表，即需要清除 self.receivers 列表中的无效数据
        """
        self._dead_receivers = True


def receiver(signal, **kwargs):
    """创建「信号接收者」即回调函数时所使用的装饰器

    通常在定义「信号接收者」函数时使用此装饰器
    使用此装饰器时须提供「信号对象」或其列表作为参数，也可以选择性提供「信号发送者」作为参数

    Args:
        signal:「信号对象」或其列表
        kwargs: 
            sender:「信号发送者」

    Eg:
        @receiver(post_save, sender=MyModel)
        def signal_receiver(**kwargs):
            ...

        @receiver([post_save, post_delete], sender=MyModel)
        def signals_receiver(**kwargs):
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
