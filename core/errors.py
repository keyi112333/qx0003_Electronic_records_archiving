#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QX0003 流程异常分类。

异常类型与开发文档第 11 章一致，调用方按类型决定终止、重试或转人工。
"""


class Qx0003Error(Exception):
    """流程异常基类，可携带阶段与单据信息。"""

    def __init__(self, message: str, *, stage: str = "", declaration: str = ""):
        super().__init__(message)
        self.stage = stage
        self.declaration = declaration

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.stage:
            parts.append(f"阶段={self.stage}")
        if self.declaration:
            parts.append(f"单据={self.declaration}")
        return "；".join(parts)


class ConfigError(Qx0003Error):
    """配置缺失、格式错误或凭据不可用。终止本轮。"""


class SystemFailure(Qx0003Error):
    """网络、登录、页面加载、接口异常。允许按配置重试。"""


class BusinessPending(Qx0003Error):
    """业务前置不满足，例如重复、缺字段、待人工、快照不完整。不重试。"""


class ImplementationRequired(Qx0003Error):
    """页面结构变化、定位不唯一或规则未确认。不重试。"""
