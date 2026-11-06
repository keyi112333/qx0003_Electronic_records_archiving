#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务重试与结果核验枚举。"""
import time
from enum import Enum
from typing import Callable, Iterable, Type

from core.errors import SystemFailure
from log.loggingConfig import logger


class Verification(Enum):
    """OA 写入后的回查结果。"""

    COMPLETED = "completed"
    ABSENT = "absent"
    UNKNOWN = "unknown"


def call_with_retry(
    func: Callable,
    *,
    attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    retry_exceptions: Iterable[Type[BaseException]] = (SystemFailure,),
    description: str = "",
):
    """执行可重试操作。

    只对 retry_exceptions 中的异常重试；BusinessPending、
    ImplementationRequired 与 ConfigError 由调用方直接处理。
    """
    attempts = max(1, int(attempts))
    wait = max(0.0, float(delay))
    last_error = None
    for index in range(attempts):
        try:
            return func()
        except tuple(retry_exceptions) as exc:
            last_error = exc
            if index >= attempts - 1:
                break
            logger.warning("%s失败，%.1f秒后重试(%d/%d): %s",
                           description or func.__name__, wait, index + 1, attempts, exc)
            time.sleep(wait)
            wait = min(max_delay, wait * backoff)
    raise last_error
