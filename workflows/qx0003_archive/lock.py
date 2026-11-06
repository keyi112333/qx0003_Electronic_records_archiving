#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单实例锁。"""
import os
import re
import time
from datetime import datetime
from pathlib import Path

import psutil

from log.loggingConfig import logger

STALE_SECONDS = 12 * 60 * 60


class SingleInstance:
    """基于独占文件锁，防止两个机器人同时运行。"""

    def __init__(self, path):
        self.path = Path(path)

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(str(self.path),
                                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(f"pid={os.getpid()}\n")
                    stream.write(f"time={datetime.now():%Y-%m-%d %H:%M:%S}\n")
                return True
            except FileExistsError:
                if not self._owner_alive() or self._is_stale():
                    logger.warning("发现已退出实例的运行锁，自动清理: %s", self.path)
                    try:
                        self.path.unlink()
                    except OSError:
                        return False
                    continue
                return False
        return False

    def _owner_alive(self) -> bool:
        """锁文件中的 PID 仍存在时视为有人在运行。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return True
        matched = re.search(r"pid=(\d+)", text)
        if not matched:
            return True
        try:
            return psutil.pid_exists(int(matched.group(1)))
        except Exception:
            return True

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > STALE_SECONDS
        except OSError:
            return False

    def release(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
