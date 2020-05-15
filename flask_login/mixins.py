# -*- coding: utf-8 -*-
'''
    flask_login.mixins
    ------------------
    This module provides mixin objects.
'''


from ._compat import PY2, text_type


class UserMixin(object):
    '''
    This provides default implementations for the methods that Flask-Login
    expects user objects to have.
    '''

    if not PY2:  # pragma: no cover
        # Python 3 implicitly set __hash__ to None if we override __eq__
        # We set it back to its default implementation
        __hash__ = object.__hash__

    @property
    def is_active(self):
        '''是否为活跃用户'''
        return True

    @property
    def is_authenticated(self):
        '''是否为已注册用户'''
        return True

    @property
    def is_anonymous(self):
        '''是否为匿名用户'''
        return False

    # 获取用户的 id 属性值的字符串，text_type 就是 str 
    def get_id(self):
        try:
            return text_type(self.id)
        except AttributeError:
            raise NotImplementedError('No `id` attribute - override `get_id`')

    def __eq__(self, other):
        '''
        Checks the equality of two `UserMixin` objects using `get_id`.
        '''
        if isinstance(other, UserMixin):
            return self.get_id() == other.get_id()
        return NotImplemented

    def __ne__(self, other):
        '''
        Checks the inequality of two `UserMixin` objects using `get_id`.
        '''
        equal = self.__eq__(other)
        if equal is NotImplemented:
            return NotImplemented
        return not equal


class AnonymousUserMixin(object):
    '''
    This is the default object for representing an anonymous user.
    '''
    @property
    def is_authenticated(self):
        return False

    @property
    def is_active(self):
        return False

    @property
    def is_anonymous(self):
        return True

    def get_id(self):
        return
