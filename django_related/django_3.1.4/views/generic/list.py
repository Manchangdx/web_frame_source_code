from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import InvalidPage, Paginator
from django.db.models import QuerySet
from django.http import Http404
from django.utils.translation import gettext as _
from django.views.generic.base import ContextMixin, TemplateResponseMixin, View


class MultipleObjectMixin(ContextMixin):
    """A mixin for views manipulating multiple objects."""
    allow_empty = True
    queryset = None
    model = None
    paginate_by = None
    paginate_orphans = 0
    context_object_name = None
    paginator_class = Paginator
    page_kwarg = 'page'
    ordering = None

    def get_queryset(self):
        """此方法所在类是 ListView 类的 Mixin 类，此方法的作用是提供被展示的映射类实例的列表。
        """
        if self.queryset is not None:
            # 此属性可能定义在项目中的视图类中
            queryset = self.queryset
            if isinstance(queryset, QuerySet):
                queryset = queryset.all()
        elif self.model is not None:
            # 如果项目中的视图类里没有定义 queryset 属性
            # 则调用项目中的视图类里的 model 属性作为映射类查询相应数据表中的全部数据
            # 并返回「查询集对象」（QuerySet 类的实例）
            queryset = self.model._default_manager.all()
        else:
            raise ImproperlyConfigured(
                "%(cls)s is missing a QuerySet. Define "
                "%(cls)s.model, %(cls)s.queryset, or override "
                "%(cls)s.get_queryset()." % {
                    'cls': self.__class__.__name__
                }
            )
        # 定义在项目中的视图类里的 ordering 属性，属性值通常是元组，里面是排序属性字符串
        ordering = self.get_ordering()
        # 调用「查询集对象」的 order_by 方法对查询结果进行排序
        if ordering:
            if isinstance(ordering, str):
                ordering = (ordering,)
            queryset = queryset.order_by(*ordering)

        # 返回「查询集对象」
        return queryset

    def get_ordering(self):
        """Return the field or fields to use for ordering the queryset."""
        return self.ordering

    def paginate_queryset(self, queryset, page_size):
        """Paginate the queryset, if needed."""
        paginator = self.get_paginator(
            queryset, page_size, orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty())
        page_kwarg = self.page_kwarg
        page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        try:
            page_number = int(page)
        except ValueError:
            if page == 'last':
                page_number = paginator.num_pages
            else:
                raise Http404(_('Page is not “last”, nor can it be converted to an int.'))
        try:
            page = paginator.page(page_number)
            return (paginator, page, page.object_list, page.has_other_pages())
        except InvalidPage as e:
            raise Http404(_('Invalid page (%(page_number)s): %(message)s') % {
                'page_number': page_number,
                'message': str(e)
            })

    def get_paginate_by(self, queryset):
        """
        Get the number of items to paginate by, or ``None`` for no pagination.
        """
        return self.paginate_by

    def get_paginator(self, queryset, per_page, orphans=0,
                      allow_empty_first_page=True, **kwargs):
        """Return an instance of the paginator for this view."""
        return self.paginator_class(
            queryset, per_page, orphans=orphans,
            allow_empty_first_page=allow_empty_first_page, **kwargs)

    def get_paginate_orphans(self):
        """
        Return the maximum number of orphans extend the last page by when
        paginating.
        """
        return self.paginate_orphans

    def get_allow_empty(self):
        """
        Return ``True`` if the view should display empty lists and ``False``
        if a 404 should be raised instead.
        """
        return self.allow_empty

    def get_context_object_name(self, object_list):
        """Get the name of the item to be used in the context."""
        if self.context_object_name:
            return self.context_object_name
        elif hasattr(object_list, 'model'):
            return '%s_list' % object_list.model._meta.model_name
        else:
            return None

    def get_context_data(self, *, object_list=None, **kwargs):
        """获取 context 字典对象并返回
        """
        # 映射类查询数据表返回的「查询集对象」（QuerySet 类的实例）
        queryset = object_list if object_list is not None else self.object_list
        # 定义在项目的视图类中的 paginate_by 属性值，每页展示对象的数量
        page_size = self.get_paginate_by(queryset)
        # 定义在项目的视图类中的 context_object_name 属性值
        context_object_name = self.get_context_object_name(queryset)
        if page_size:
            # 四个变量分别是：分页对象，页数，当前页的查询集对象，是否有下一页
            paginator, page, queryset, is_paginated = self.paginate_queryset(queryset, page_size)
            context = {
                'paginator': paginator,
                'page_obj': page,
                'is_paginated': is_paginated,
                'object_list': queryset
            }
        else:
            context = {
                'paginator': None,
                'page_obj': None,
                'is_paginated': False,
                'object_list': queryset
            }
        if context_object_name is not None:
            # 添加一组键值对 {'questions': 完整的查询集对象}
            context[context_object_name] = queryset
        context.update(kwargs)
        return super().get_context_data(**context)


class BaseListView(MultipleObjectMixin, View):
    """A base view for displaying a list of objects."""
    def get(self, request, *args, **kwargs):
        # 查询数据表的结果「查询集对象」
        self.object_list = self.get_queryset()
        # 定义在当前模块中的 MultipleObjectMixin 类中的属性，布尔值，默认是 True
        allow_empty = self.get_allow_empty()

        if not allow_empty:
            # When pagination is enabled and object_list is a queryset,
            # it's better to do a cheap query than to load the unpaginated
            # queryset in memory.
            if self.get_paginate_by(self.object_list) is not None and hasattr(self.object_list, 'exists'):
                is_empty = not self.object_list.exists()
            else:
                is_empty = not self.object_list
            if is_empty:
                raise Http404(_('Empty list and “%(class_name)s.allow_empty” is False.') % {
                    'class_name': self.__class__.__name__,
                })
        context = self.get_context_data()
        return self.render_to_response(context)


class MultipleObjectTemplateResponseMixin(TemplateResponseMixin):
    """Mixin for responding with a template and list of objects."""
    template_name_suffix = '_list'

    def get_template_names(self):
        """
        Return a list of template names to be used for the request. Must return
        a list. May not be called if render_to_response is overridden.
        """
        try:
            names = super().get_template_names()
        except ImproperlyConfigured:
            # If template_name isn't specified, it's not a problem --
            # we just start with an empty list.
            names = []

        # If the list is a queryset, we'll invent a template name based on the
        # app and model name. This name gets put at the end of the template
        # name list so that user-supplied names override the automatically-
        # generated ones.
        if hasattr(self.object_list, 'model'):
            opts = self.object_list.model._meta
            names.append("%s/%s%s.html" % (opts.app_label, opts.model_name, self.template_name_suffix))
        elif not names:
            raise ImproperlyConfigured(
                "%(cls)s requires either a 'template_name' attribute "
                "or a get_queryset() method that returns a QuerySet." % {
                    'cls': self.__class__.__name__,
                }
            )
        return names


class ListView(MultipleObjectTemplateResponseMixin, BaseListView):
    """
    Render some list of objects, set by `self.model` or `self.queryset`.
    `self.queryset` can actually be any iterable of items, not just a queryset.
    """
