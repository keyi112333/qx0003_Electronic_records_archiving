#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心模块。

为避免 log/config/core.errors 之间形成导入环，本包不在 __init__ 中
提前导入任务类。请按需使用：
    from core.task_base import TaskBase
    from core.context import TaskContext
"""
