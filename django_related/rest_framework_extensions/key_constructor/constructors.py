import hashlib
import json

from rest_framework_extensions.key_constructor import bits
from rest_framework_extensions.settings import extensions_api_settings


class KeyConstructor:
    """缓存 key 构造类
    
    整个扩展的主要功能是缓存视图函数返回的响应体
    该类的实例叫做「缓存 key 构造器」，用于构造缓存的 key 值
    """

    def __init__(self, memoize_for_request=None, params=None):
        """初始化「缓存 key 构造器」

        Args:
            memoize_for_request(bool): 
                是否把计算好的 key 让请求对象 request 记住（计算 key 挺耗 CPU 的）（默认为否）
                这个操作是给 request 请求对象加个属性字典
                然后把 JSON 字符串作为 key ，计算好的 key 作为 value 放到字典里
                这需要在视图函数里对 request 的这个属性字典作进一步处理，使之加到响应体中
                当浏览器再次调用同样的请求时，请求体中携带上这个数据，这样就不需要再次计算 key 了
            params(dict): 
                调用 hashlib.md5 计算 key 首先要构造字典
                字典是由各个 Bit 对象根据请求体、视图函数的 queryset 方法调用结果等信息计算出来的
                这些 Bit 对象在「缓存 key 构造类」中已经设置好了
                在对「缓存 key 构造类」进行实例化时提供该字典参数，就会在构造字典时替换设置好的 Bit 对象
        """
        if memoize_for_request is None:
            self.memoize_for_request = extensions_api_settings.DEFAULT_KEY_CONSTRUCTOR_MEMOIZE_FOR_REQUEST
        else:
            self.memoize_for_request = memoize_for_request
        if params is None:
            self.params = {}
        else:
            self.params = params
        self.bits = self.get_bits()

    def get_bits(self):
        """获取计算缓存 key 的各种规则对象
        """
        _bits = {}
        for attr in dir(self.__class__):
            attr_value = getattr(self.__class__, attr)
            if isinstance(attr_value, bits.KeyBitBase):
                _bits[attr] = attr_value
        return _bits

    def __call__(self, **kwargs):
        return self.get_key(**kwargs)

    def get_key(self, view_instance, view_method, request, args, kwargs):
        """计算缓存 key

        Args:
            view_instance : 视图类实例
            view_method   : 视图函数
            request       : 请求对象
            args          : 调用视图函数时传的可变参数
            kwargs        : 调用视图函数时传的关键字参数
        """
        if self.memoize_for_request:
            memoization_key = self._get_memoization_key(
                view_instance=view_instance,
                view_method=view_method,
                args=args,
                kwargs=kwargs
            )
            print(f'【rest_framework_extensions.key_constructor.constructors.KeyConstructor.get_key】{memoization_key=}')
            if not hasattr(request, '_key_constructor_cache'):
                request._key_constructor_cache = {}
        if self.memoize_for_request and memoization_key in request._key_constructor_cache:
            return request._key_constructor_cache.get(memoization_key)
        else:
            value = self._get_key(
                view_instance=view_instance,    # 视图类实例
                view_method=view_method,        # 视图函数
                request=request,                # 请求对象
                args=args,                      # 调用视图函数时传的可变参数
                kwargs=kwargs                   # 调用视图函数时传的关键字参数（通常是路径中的变量）
            )
            if self.memoize_for_request:
                request._key_constructor_cache[memoization_key] = value
            return value

    def _get_memoization_key(self, view_instance, view_method, args, kwargs):
        from rest_framework_extensions.utils import get_unique_method_id
        return json.dumps({
            'unique_method_id': get_unique_method_id(view_instance=view_instance, view_method=view_method),
            'args': args,
            'kwargs': kwargs,
            'instance_id': id(self)
        })

    def _get_key(self, view_instance, view_method, request, args, kwargs):
        _kwargs = {
            'view_instance': view_instance,
            'view_method': view_method,
            'request': request,
            'args': args,
            'kwargs': kwargs,
        }
        return self.prepare_key(
            self.get_data_from_bits(**_kwargs)
        )

    def prepare_key(self, key_dict):
        return hashlib.md5(json.dumps(key_dict, sort_keys=True).encode('utf-8')).hexdigest()

    def get_data_from_bits(self, **kwargs):
        """根据参数构造字典并返回，这个字典将被哈希成缓存 key

        Args:
            self:「缓存 key 构造器」
            kwargs: 构造字典所需的对象（视图类实例、视图函数、请求对象等）
        Return:
            构造字典有哪些键值对，这个由 self.bits 来决定
            所以对于「缓存 key 构造器」来说，有哪些 keybit 才是核心
        """
        result_dict = {}
        for bit_name, bit_instance in self.bits.items():
            #print('=='*22, bit_name, bit_instance, bit_instance.params)
            if bit_name in self.params:
                params = self.params[bit_name]
            else:
                try:
                    params = bit_instance.params
                except AttributeError:
                    params = None
            result_dict[bit_name] = bit_instance.get_data(params=params, **kwargs)
        print('【rest_framework_extensions.key_constructor.KeyConstructor.get_data_from_bits】result_dict:', result_dict)
        return result_dict


class DefaultKeyConstructor(KeyConstructor):
    unique_method_id = bits.UniqueMethodIdKeyBit()
    format = bits.FormatKeyBit()
    language = bits.LanguageKeyBit()


class DefaultObjectKeyConstructor(DefaultKeyConstructor):
    retrieve_sql_query = bits.RetrieveSqlQueryKeyBit()


class DefaultListKeyConstructor(DefaultKeyConstructor):
    list_sql_query = bits.ListSqlQueryKeyBit()
    pagination = bits.PaginationKeyBit()


class DefaultAPIModelInstanceKeyConstructor(KeyConstructor):
    """
    Use this constructor when the values of the model instance are required
    to identify the resource.
    """
    retrieve_model_values = bits.RetrieveModelKeyBit()


class DefaultAPIModelListKeyConstructor(KeyConstructor):
    """
    Use this constructor when the values of the model instance are required
    to identify many resources.
    """
    list_model_values = bits.ListModelKeyBit()
