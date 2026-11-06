#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子写入工具。"""
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def atomic_target(target) -> Iterator[Path]:
    """先写同目录临时文件，成功后原子替换目标文件。"""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    pending = target_path.with_name(
        f".{target_path.name}.{os.getpid()}.tmp"
    )
    try:
        yield pending
        os.replace(pending, target_path)
    finally:
        if pending.exists():
            try:
                pending.unlink()
            except OSError:
                pass
