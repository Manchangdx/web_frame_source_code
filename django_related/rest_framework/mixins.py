"""
Basic building blocks for generic class based views.

We don't bind behaviour to http method handlers yet,
which allows mixin classes to be composed in interesting ways.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.settings import api_settings


class CreateModelMixin:
    """
    Create a model instance.
    """
    def create(self, request, *args, **kwargs):
        """根据请求体反序列化生成映射类实例并保存到数据表中
        """
        print('【rest_framework.mixins.CreateModelMixin.create】这是 POST【创建】操作')
        print('【rest_framework.mixins.CreateModelMixin.create】请求体:', request.data)
        # 此方法定义在 rest_framework.generics.GenericAPIView 类中，根据请求体数据创建序列化类的实例
        serializer = self.get_serializer(data=request.data)
        print('【rest_framework.mixins.CreateModelMixin.create】创建序列化实例，调用其 is_valid 方法验证数据')
        # 此方法定义在 rest_framework.serializers.BaseSerializer 类中，验证请求体数据，如果有异常则抛出异常
        serializer.is_valid(raise_exception=True)
        # 此方法定义在 rest_framework.serializers.BaseSerializer 类中，创建映射类实例并保存到数据表中
        self.perform_create(serializer)
        print('【rest_framework.mixins.CreateModelMixin.create】创建映射类实例成功，响应体:')
        print('\t', serializer.data)
        headers = self.get_success_headers(serializer.data)
        # 序列化实例的 data 属性定义在 rest_framework.serializers.BaseSerializer 类中，属性值是字典对象
        resp = Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        print('【rest_framework.mixins.CreateModelMixin.create】返回响应对象:', resp)
        return resp

    def perform_create(self, serializer):
        # 此方法定义在 rest_framework.serializers.BaseSerializer 类中
        # 调用序列化实例的 create 方法创建映射类实例或 update 方法更新映射类实例（此处为创建之用）
        serializer.save()

    def get_success_headers(self, data):
        try:
            return {'Location': str(data[api_settings.URL_FIELD_NAME])}
        except (TypeError, KeyError):
            return {}


class ListModelMixin:
    """
    List a queryset.
    """
    def list(self, request, *args, **kwargs):
        """查询数据表中的数据生成映射类实例的列表
        """
        print('【rest_framework.mixins.ListModelMixin.list】这是 GET【查询列表】操作')

        # 这里涉及分页，自定义视图类中需要定义 get_queryset 方法或 queryset 属性
        # filter_queryset 定义在 rest_framework.generics.GenericAPIView 类中，进行一些额外的过滤操作
        queryset = self.filter_queryset(self.get_queryset())

        #print('【rest_framework.mixins.ListModelMixin.list】查询参数:', request.query_params)
        print('【rest_framework.mixins.ListModelMixin.list】处理分页，获得分页中的映射类实例列表')
        # 此方法定义在 rest_framework.generics.GenericAPIView 类中
        page = self.paginate_queryset(queryset)
        if page is not None:
            print('【rest_framework.mixins.ListModelMixin.list】将列表中的映射类实例进行序列化作为响应体')
            serializer = self.get_serializer(page, many=True)
            print('【rest_framework.mixins.ListModelMixin.list】创建分页响应体并返回，Django 框架内的后续操作会构建响应对象')
            # 此方法通常在项目内自定义，可能定义在 shiyanlou.contrib.pagination.page_number.CurrentPageNumberPagination 类中
            return self.get_paginated_response(serializer.data)

        print('【rest_framework.mixins.ListModelMixin.list】没有分页，将查询集中的全部映射类实例序列化作为响应体')
        serializer = self.get_serializer(queryset, many=True)
        resp = Response(serializer.data)
        print('【rest_framework.mixins.ListModelMixin.list】创建响应对象并返回:', resp)
        return resp


class RetrieveModelMixin:
    """
    Retrieve a model instance.
    """
    def retrieve(self, request, *args, **kwargs):
        print('【rest_framework.mixins.RetrieveModelMixin.retrieve】这是 GET【查询】操作')

        print('【rest_framework.mixins.RetrieveModelMixin.retrieve】调用「视图类实例」自身的 get_object 方法获取映射类实例')
        # 此方法定义在 rest_framework.generics.GenericAPIView 类中，也可能在自定义视图类中重写
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        print('【rest_framework.mixins.RetrieveModelMixin.retrieve】序列化映射类实例生成响应体')
        resp = Response(serializer.data)
        print('【rest_framework.mixins.RetrieveModelMixin.retrieve】创建响应对象并返回:', resp)
        return resp


class UpdateModelMixin:
    """
    Update a model instance.
    """
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        s = 'PATCH【部分更新】' if partial else 'PUT【更新】'
        print(f'【rest_framework.mixins.UpdateModelMixin.update】这是 {s}操作')
        # 此方法定义在 rest_framework.generics.GenericAPIView 类中，也可能在自定义视图类中重写
        instance = self.get_object()
        print('【rest_framework.mixins.UpdateModelMixin.update】获取将要被修改的映射类实例:', instance)
        # 参数 instance 将赋值给「序列化对象」的 instance 属性
        # 参数 data 通过各种验证器后用于修改 instance 
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        # 调用「序列化对象」的 save 方法修改映射类实例并保存修改到数据表
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def perform_update(self, serializer):
        serializer.save()

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)


class DestroyModelMixin:
    """
    Destroy a model instance.
    """
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        instance.delete()
