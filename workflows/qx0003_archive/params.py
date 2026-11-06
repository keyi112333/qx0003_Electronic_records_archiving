#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行入参解析。

优先级：环境变量/框架入参 > config.xlsx「入参」sheet > 默认值。
"""
import os
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from core.errors import ConfigError
from workflows.qx0003_archive.models import parse_date

VALID_STAGES = ("all", "collect", "sync", "pdf")


def _split(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        text = str(value)
        for separator in ("，", "、", ";", "；", "|"):
            text = text.replace(separator, ",")
        items = text.split(",")
    return [item.strip() for item in items if item.strip()]


def _period(value) -> tuple[Optional[date], Optional[date]]:
    text = str(value or "").strip()
    if not text:
        return None, None
    for separator in ("~", "～", "至"):
        text = text.replace(separator, "-")
    # 形如 2026-01-01-2026-01-31，按第 11 位拆分，避免日期中的连字符干扰。
    if len(text) > 11 and text.count("-") >= 5:
        start_text, end_text = text[:10], text[11:]
    elif "," in text:
        start_text, end_text = text.split(",", 1)
    else:
        start_text, end_text = text, ""
    start = parse_date(start_text)
    end = parse_date(end_text) if str(end_text).strip() else start
    if not start:
        raise ConfigError(f"报关单日期期间起始值无效: {value}")
    return start, end


@dataclass
class RunParams:
    """单次运行的入参。"""

    companies: List[str] = field(default_factory=list)
    declarations: List[str] = field(default_factory=list)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    stage: str = "all"

    def __post_init__(self):
        if self.stage not in VALID_STAGES:
            raise ConfigError(f"stage 必须为 {VALID_STAGES} 之一: {self.stage}")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ConfigError("报关单日期期间起始日期晚于结束日期")

    @classmethod
    def from_config(cls, config) -> "RunParams":
        raw = config.get_input_params() or {}
        env = os.environ

        companies = _split(env.get("QX0003_COMPANIES")) or _split(raw.get("公司"))
        declarations = _split(env.get("QX0003_DECLARATIONS")) or _split(raw.get("报关单"))

        period_value = env.get("QX0003_PERIOD")
        period_start = period_end = None
        if period_value:
            period_start, period_end = _period(period_value)
        else:
            raw_period = raw.get("报关单日期期间")
            if raw_period:
                period_start, period_end = _period(raw_period)
            else:
                period_start = parse_date(env.get("QX0003_PERIOD_START"))
                period_end = parse_date(env.get("QX0003_PERIOD_END"))
                if period_start and not period_end:
                    period_end = period_start

        stage = str(env.get("QX0003_STAGE") or "all").strip().lower()
        return cls(companies, declarations, period_start, period_end, stage)

    def in_period(self, value: Optional[date]) -> bool:
        if not self.period_start and not self.period_end:
            return True
        if not value:
            return False
        if self.period_start and value < self.period_start:
            return False
        if self.period_end and value > self.period_end:
            return False
        return True

    def summary(self) -> str:
        companies = "、".join(self.companies) if self.companies else "全部"
        declarations = "、".join(self.declarations) if self.declarations else "共享目录全部"
        if self.period_start:
            period = self.period_start.isoformat()
            if self.period_end and self.period_end != self.period_start:
                period += f"~{self.period_end.isoformat()}"
        else:
            period = "不限"
        return f"公司={companies}；报关单={declarations}；期间={period}；stage={self.stage}"
