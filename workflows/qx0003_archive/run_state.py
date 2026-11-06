#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行基线与审计记录。"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from log.loggingConfig import logger
from utils.atomic import atomic_target


class RunState:
    """保存上一次成功运行时间，供附件变更比对使用。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def load(cls, log_dir) -> "RunState":
        return cls(Path(log_dir) / "run_state.json")

    def read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("运行状态文件读取失败，按空状态处理: %s", exc)
            return {}

    @property
    def last_success_end(self) -> Optional[datetime]:
        value = self.read().get("last_success_end")
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def save(self, run_id: str, per_org: Dict[str, str], success: bool = True) -> None:
        if not success:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "last_success_end": now,
            "last_run_id": run_id,
            "per_org": per_org,
        }
        with atomic_target(self.path) as pending:
            pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AuditLog:
    """按运行ID记录审计明细与 PDF 清单。"""

    def __init__(self, log_dir, run_id: str):
        self.root = Path(log_dir) / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "audit.jsonl"

    def write(self, stage: str, declaration_key: str = "", action: str = "",
              result: str = "", detail: Optional[Dict[str, Any]] = None) -> None:
        record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": stage,
            "declaration": declaration_key,
            "action": action,
            "result": result,
            "detail": detail or {},
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_pdf_manifest(self, declaration_no: str, payload: Dict[str, Any]) -> Path:
        target = self.root / f"pdf_{declaration_no}.json"
        with atomic_target(target) as pending:
            pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
