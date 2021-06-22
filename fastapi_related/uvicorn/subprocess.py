"""
Some light wrappers around Python's multiprocessing, to deal with cleanly
starting child processes.
"""
import multiprocessing
import os
import sys
from multiprocessing.context import SpawnProcess
from socket import socket
from typing import Callable, List, Optional

from uvicorn.config import Config

multiprocessing.allow_connection_pickling()
# 这是一个用于启动子进程的上下文对象，详见 https://docs.python.org/zh-cn/3/library/multiprocessing.html
spawn = multiprocessing.get_context("spawn")


def get_subprocess(
    config: Config,
    target: Callable[..., None],
    sockets: List[socket],
) -> SpawnProcess:
    """
    Called in the parent process, to instantiate a new child process instance.
    The child is not yet started at this point.

    * config - The Uvicorn configuration instance.
    * target - A callable that accepts a list of sockets. In practice this will
               be the `Server.run()` method.
    * sockets - A list of sockets to pass to the server. Sockets are bound once
                by the parent process, and then passed to the child processes.
    """
    # We pass across the stdin fileno, and reopen it in the child process.
    # This is required for some debugging environments.
    stdin_fileno: Optional[int]
    try:
        stdin_fileno = sys.stdin.fileno()
    except OSError:
        stdin_fileno = None

    kwargs = {
        "config": config,
        "target": target,
        "sockets": sockets,
        "stdin_fileno": stdin_fileno,
    }

    # spawn 是启动子进程的上下文对象，它设置了 multiprocessing.Process 的启动方式为 spawn
    # 这种方式会创建一个新的 Python 解释器运行在子进程中，该上下文对象拥有与 multiprocessing 完全相同的 API
    return spawn.Process(target=subprocess_started, kwargs=kwargs)


def subprocess_started(
    config: Config,
    target: Callable[..., None],
    sockets: List[socket],
    stdin_fileno: Optional[int],
) -> None:
    """
    Called when the child process starts.

    * config - The Uvicorn configuration instance.
    * target - A callable that accepts a list of sockets. In practice this will
               be the `Server.run()` method.
    * sockets - A list of sockets to pass to the server. Sockets are bound once
                by the parent process, and then passed to the child processes.
    * stdin_fileno - The file number of sys.stdin, so that it can be reattached
                     to the child process.
    """
    # Re-open stdin.
    if stdin_fileno is not None:
        sys.stdin = os.fdopen(stdin_fileno)

    # Logging needs to be setup again for each child.
    config.configure_logging()

    # Now we can call into `Server.run(sockets=sockets)`
    print('【uvicorn.subprocess.subprocess_started】在子线程中执行的函数:', target)
    target(sockets=sockets)
