import asyncio
import functools
import sys
import typing
from typing import Any, AsyncGenerator, Iterator

try:
    import contextvars  # Python 3.7+ only or via contextvars backport.
except ImportError:  # pragma: no cover
    contextvars = None  # type: ignore

if sys.version_info >= (3, 7):  # pragma: no cover
    from asyncio import create_task
else:  # pragma: no cover
    from asyncio import ensure_future as create_task

T = typing.TypeVar("T")


async def run_until_first_complete(*args: typing.Tuple[typing.Callable, dict]) -> None:
    tasks = [create_task(handler(**kwargs)) for handler, kwargs in args]
    (done, pending) = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    [task.cancel() for task in pending]
    [task.result() for task in done]


async def run_in_threadpool(
    func: typing.Callable[..., T], *args: typing.Any, **kwargs: typing.Any
) -> T:
    loop = asyncio.get_event_loop()
    if contextvars is not None:  # pragma: no cover
        # Ensure we run in the same context
        child = functools.partial(func, *args, **kwargs)
        context = contextvars.copy_context()
        func = context.run
        args = (child,)
    elif kwargs:  # pragma: no cover
        # loop.run_in_executor 不接受关键字参数，在这里使用偏函数绑定关键字参数
        func = functools.partial(func, **kwargs)

    # 参考 https://segmentfault.com/q/1010000007863971
    # 协程从属于线程，事件循环对象在运行时会阻塞当前线程
    # 这里调用线程池中的另一个线程执行视图函数，并将执行结果返回给运行中的事件循环对象
    # 视图函数 func 的返回值即为 loop.run_in_exector 协程的返回值
    return await loop.run_in_executor(None, func, *args)


class _StopIteration(Exception):
    pass


def _next(iterator: Iterator) -> Any:
    # We can't raise `StopIteration` from within the threadpool iterator
    # and catch it outside that context, so we coerce them into a different
    # exception type.
    try:
        return next(iterator)
    except StopIteration:
        raise _StopIteration


async def iterate_in_threadpool(iterator: Iterator) -> AsyncGenerator:
    while True:
        try:
            yield await run_in_threadpool(_next, iterator)
        except _StopIteration:
            break
