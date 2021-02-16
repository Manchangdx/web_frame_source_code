import asyncio
import logging
import sys
from functools import wraps

from asgiref.sync import sync_to_async

from django.conf import settings
from django.core import signals
from django.core.exceptions import (
    PermissionDenied, RequestDataTooBig, SuspiciousOperation,
    TooManyFieldsSent,
)
from django.http import Http404
from django.http.multipartparser import MultiPartParserError
from django.urls import get_resolver, get_urlconf
from django.utils.log import log_response
from django.views import debug


def convert_exception_to_response(get_response):
    """
    原注释翻译：
    将给定的参数 get_response 函数包装在异常到响应转换中。所有异常将被转换。 
    所有已知的 4xx 异常（Http404，PermissionDenied，MultiPartParserError，SuspiciousOperation）将转换为适当的响应。
    而所有其它异常将转换为 5xx 响应。
    该装饰器自动应用于所有中间件，以确保没有中间件泄漏异常，并且堆栈中的下一个中间件可以依赖于获取响应而不是异常。
    """
    if asyncio.iscoroutinefunction(get_response):
        @wraps(get_response)
        async def inner(request):
            try:
                response = await get_response(request)
            except Exception as exc:
                response = await sync_to_async(response_for_exception, thread_sensitive=False)(request, exc)
            return response
        return inner
    else:
        @wraps(get_response)
        def inner(request):
            try:
                # get_response 可能是中间件对象
                # 或者 django.core.handler.base.BaseHandler._get_response 方法
                response = get_response(request)
            except Exception as exc:
                response = response_for_exception(request, exc)
            return response
        inner.get_response = get_response
        return inner


def response_for_exception(request, exc):
    if isinstance(exc, Http404):
        if settings.DEBUG:
            response = debug.technical_404_response(request, exc)
        else:
            response = get_exception_response(request, get_resolver(get_urlconf()), 404, exc)

    elif isinstance(exc, PermissionDenied):
        response = get_exception_response(request, get_resolver(get_urlconf()), 403, exc)
        log_response(
            'Forbidden (Permission denied): %s', request.path,
            response=response,
            request=request,
            exc_info=sys.exc_info(),
        )

    elif isinstance(exc, MultiPartParserError):
        response = get_exception_response(request, get_resolver(get_urlconf()), 400, exc)
        log_response(
            'Bad request (Unable to parse request body): %s', request.path,
            response=response,
            request=request,
            exc_info=sys.exc_info(),
        )

    elif isinstance(exc, SuspiciousOperation):
        if isinstance(exc, (RequestDataTooBig, TooManyFieldsSent)):
            # POST data can't be accessed again, otherwise the original
            # exception would be raised.
            request._mark_post_parse_error()

        # The request logger receives events for any problematic request
        # The security logger receives events for all SuspiciousOperations
        security_logger = logging.getLogger('django.security.%s' % exc.__class__.__name__)
        security_logger.error(
            str(exc),
            extra={'status_code': 400, 'request': request},
        )
        if settings.DEBUG:
            response = debug.technical_500_response(request, *sys.exc_info(), status_code=400)
        else:
            response = get_exception_response(request, get_resolver(get_urlconf()), 400, exc)

    elif isinstance(exc, SystemExit):
        # Allow sys.exit() to actually exit. See tickets #1023 and #4701
        raise

    else:
        signals.got_request_exception.send(sender=None, request=request)
        response = handle_uncaught_exception(request, get_resolver(get_urlconf()), sys.exc_info())
        log_response(
            '%s: %s', response.reason_phrase, request.path,
            response=response,
            request=request,
            exc_info=sys.exc_info(),
        )

    # Force a TemplateResponse to be rendered.
    if not getattr(response, 'is_rendered', True) and callable(getattr(response, 'render', None)):
        response = response.render()

    return response


def get_exception_response(request, resolver, status_code, exception):
    try:
        callback, param_dict = resolver.resolve_error_handler(status_code)
        response = callback(request, **{**param_dict, 'exception': exception})
    except Exception:
        signals.got_request_exception.send(sender=None, request=request)
        response = handle_uncaught_exception(request, resolver, sys.exc_info())

    return response


def handle_uncaught_exception(request, resolver, exc_info):
    """
    Processing for any otherwise uncaught exceptions (those that will
    generate HTTP 500 responses).
    """
    if settings.DEBUG_PROPAGATE_EXCEPTIONS:
        raise

    if settings.DEBUG:
        return debug.technical_500_response(request, *exc_info)

    # Return an HttpResponse that displays a friendly error message.
    callback, param_dict = resolver.resolve_error_handler(500)
    return callback(request, **param_dict)
