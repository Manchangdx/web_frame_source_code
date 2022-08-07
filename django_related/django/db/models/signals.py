from functools import partial

from django.db.models.utils import make_model_tuple
from django.dispatch import Signal

class_prepared = Signal(providing_args=["class"])


class ModelSignal(Signal):
    """
    Signal subclass that allows the sender to be lazily specified as a string
    of the `app_label.ModelName` form.
    """
    def _lazy_method(self, method, apps, receiver, sender, **kwargs):
        """此方法的作用是支持 sender 参数是 “应用名字.映射类名字” 字符串
        """
        from django.db.models.options import Options

        # 该偏函数把「信号对象」注册关联关系的 connect 方法除 sender 之外的全部参数都给固定了
        partial_method = partial(method, receiver, **kwargs)
        # 如果 sender 是字符串，例如 'user.User'
        if isinstance(sender, str):
            # django.apps.registry.App 类的实例
            apps = apps or Options.default_apps
            # 第二个参数是元组：(应用字符串, 映射类字符串)，例如 ('user', 'User')
            # 此方法会根据 sender 字符串生成的元组找到对应的映射类对象
            # 然后将其作为 partial_method 的 sender 参数值生成没有参数的新偏函数，最后调用新偏函数
            apps.lazy_model_operation(partial_method, make_model_tuple(sender))
        else:
            return partial_method(sender)

    def connect(self, receiver, sender=None, weak=True, dispatch_uid=None, apps=None):
        self._lazy_method(
            super().connect, apps, receiver, sender,
            weak=weak, dispatch_uid=dispatch_uid,
        )

    def disconnect(self, receiver=None, sender=None, dispatch_uid=None, apps=None):
        return self._lazy_method(
            super().disconnect, apps, receiver, sender, dispatch_uid=dispatch_uid
        )


pre_init = ModelSignal(providing_args=["instance", "args", "kwargs"], use_caching=True)
post_init = ModelSignal(providing_args=["instance"], use_caching=True)

pre_save = ModelSignal(providing_args=["instance", "raw", "using", "update_fields"],
                       use_caching=True)
post_save = ModelSignal(providing_args=["instance", "raw", "created", "using", "update_fields"], use_caching=True)

pre_delete = ModelSignal(providing_args=["instance", "using"], use_caching=True)
post_delete = ModelSignal(providing_args=["instance", "using"], use_caching=True)

m2m_changed = ModelSignal(
    providing_args=["action", "instance", "reverse", "model", "pk_set", "using"],
    use_caching=True,
)

pre_migrate = Signal(providing_args=["app_config", "verbosity", "interactive", "using", "apps", "plan"])
post_migrate = Signal(providing_args=["app_config", "verbosity", "interactive", "using", "apps", "plan"])
