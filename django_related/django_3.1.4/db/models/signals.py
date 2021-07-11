from functools import partial

from django.db.models.utils import make_model_tuple
from django.dispatch import Signal

class_prepared = Signal()


class ModelSignal(Signal):
    """
    Signal subclass that allows the sender to be lazily specified as a string
    of the `app_label.ModelName` form.
    """
    def _lazy_method(self, method, apps, receiver, sender, **kwargs):
        """
        参数说明：

        method   : 父类中定义的 django.db.models.signals.ModelSignal.connect 方法
        apps     : None
        receiver : 信号接收者，通常是一个可调用对象
        sender   : 信号发送者，可能是一个映射类
        kwargs   : 字典 {'weak': True, 'dispatch_uid': None}
        """
        from django.db.models.options import Options

        # 下面这个偏函数是来自父类 django.dispatch.dispatcher.Signal 的 connect 方法
        # 要调用此偏函数，只需要提供 sender 参数就行了
        partial_method = partial(method, receiver, **kwargs)

        if isinstance(sender, str):
            apps = apps or Options.default_apps
            apps.lazy_model_operation(partial_method, make_model_tuple(sender))
        else:
            # 返回偏函数的调用，也就是调用父类的 connect 方法
            partial_method(sender)

    def connect(self, receiver, sender=None, weak=True, dispatch_uid=None, apps=None):
        self._lazy_method(
            super().connect, apps, receiver, sender,
            weak=weak, dispatch_uid=dispatch_uid,
        )

    def disconnect(self, receiver=None, sender=None, dispatch_uid=None, apps=None):
        return self._lazy_method(
            super().disconnect, apps, receiver, sender, dispatch_uid=dispatch_uid
        )


pre_init = ModelSignal(use_caching=True)
post_init = ModelSignal(use_caching=True)

pre_save = ModelSignal(use_caching=True)
post_save = ModelSignal(use_caching=True)

pre_delete = ModelSignal(use_caching=True)
post_delete = ModelSignal(use_caching=True)

m2m_changed = ModelSignal(use_caching=True)

pre_migrate = Signal()
post_migrate = Signal()
