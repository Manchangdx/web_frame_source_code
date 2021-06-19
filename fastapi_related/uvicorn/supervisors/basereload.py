import logging
import os
import signal
import threading
from socket import socket
from types import FrameType
from typing import Callable, Dict, List, Optional

import click

from uvicorn.config import Config
from uvicorn.subprocess import get_subprocess

HANDLED_SIGNALS = (
    signal.SIGINT,  # Unix signal 2. Sent by Ctrl+C.
    signal.SIGTERM,  # Unix signal 15. Sent by `kill <pid>`.
)

logger = logging.getLogger("uvicorn.error")


class BaseReload:
    def __init__(
        self,
        config: Config,
        target: Callable[[Optional[List[socket]]], None],
        sockets: List[socket],
    ) -> None:
        self.config = config
        self.target = target
        self.sockets = sockets
        self.should_exit = threading.Event()
        self.pid = os.getpid()
        self.reloader_name: Optional[str] = None

    def signal_handler(self, sig: signal.Signals, frame: FrameType) -> None:
        """
        A signal handler that is registered with the parent process.
        """
        self.should_exit.set()

    def run(self) -> None:
        self.startup()
        while not self.should_exit.wait(self.config.reload_delay):
            if self.should_restart():
                self.restart()

        self.shutdown()

    def startup(self) -> None:
        message = f"Started reloader process [{self.pid}] using {self.reloader_name}"
        color_message = "Started reloader process [{}] using {}".format(
            click.style(str(self.pid), fg="cyan", bold=True),
            click.style(str(self.reloader_name), fg="cyan", bold=True),
        )
        # logger.info(message, extra={"color_message": color_message})
        cs = f'当前为主进程，进程 ID : {click.style(f"[{self.pid}]", fg="cyan")}'
        print(f'【(logger.info) uvicorn.supervisors.basereload.BaseReload.startup】{cs}')

        for sig in HANDLED_SIGNALS:
            signal.signal(sig, self.signal_handler)

        # 创建当前进程的子进程，三个参数分别是配置对象、服务对象的 run 方法、套接字
        self.process = get_subprocess(
            config=self.config, target=self.target, sockets=self.sockets
        )
        # 启动子进程，其实就是启动服务对象的 run 方法
        self.process.start()

    def restart(self) -> None:
        self.mtimes: Dict[str, float] = {}

        self.process.terminate()
        self.process.join()

        self.process = get_subprocess(
            config=self.config, target=self.target, sockets=self.sockets
        )
        self.process.start()

    def shutdown(self) -> None:
        self.process.join()
        message = "Stopping reloader process [{}]".format(str(self.pid))
        color_message = "Stopping reloader process [{}]".format(
            click.style(str(self.pid), fg="cyan", bold=True)
        )
        logger.info(message, extra={"color_message": color_message})

    def should_restart(self) -> bool:
        raise NotImplementedError("Reload strategies should override should_restart()")
