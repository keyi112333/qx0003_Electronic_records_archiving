#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QX0003 数据模型。

字段与开发文档第 4 章一致；同时兼容 web/oa 与 web/yikatong 中
已有的构造方式（Attachment 前四个字段为位置参数）。
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATUS_COLLECTING = "附件归集中"
STATUS_WAIT_USER = "待用户确认"
STATUS_CONFIRMED = "确认已完成归集"
STATUS_PDF = "已归集为PDF"

NEXT_WAIT_USER = "wait_user"
NEXT_MERGE_PDF = "merge_pdf"
NEXT_DONE = "done"
NEXT_MANUAL = "manual"

TYPE_ALIASES = {
    "报税核注清单": "保税核注清单",
    "保税核注清单": "保税核注清单",
    "放行单": "放行通知单",
    "放行条": "放行通知单",
    "放行通知单": "放行通知单",
    "一卡通上的其它附件": "一卡通其他附件",
    "一卡通其他附件": "一卡通其他附件",
    "BIP上的其他附件": "BIP其他附件",
    "BIP其他附件": "BIP其他附件",
    "其他附件": "其他附件",
}

KNOWN_TYPES = (
    "报关单", "放行通知单", "电子委托协议", "发票", "装箱单", "保税核注清单",
    "合同", "提单", "代理报关发票", "国内运输发票", "CIF保险单",
    "国内运费形式发票", "销售发票", "一卡通其他附件", "BIP其他附件", "其他附件",
)


def normalize_type(value: Any) -> str:
    """把 OA 明细区标题或文件名类型规范为内部类型名。"""
    text = str(value or "").strip()
    text = re.sub(r"信息$", "", text)
    text = text.replace("（", "(").replace("）", ")")
    if text in TYPE_ALIASES:
        return TYPE_ALIASES[text]
    for name in KNOWN_TYPES:
        if text == name:
            return name
    for name in KNOWN_TYPES:
        if name in text:
            return name
    return text


def parse_date(value: Any) -> Optional[date]:
    """解析 yyyy-mm-dd、yyyy/mm/dd、yyyyMMdd 或 datetime。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    matched = re.match(r"^(\d{4})[-/.]?(\d{1,2})[-/.]?(\d{1,2})$", text)
    if not matched:
        return None
    try:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    except ValueError:
        return None


def parse_datetime(value: Any) -> Optional[datetime]:
    """解析常见日期时间字符串；无法解析时返回 None。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_datetime(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else str(value or "").strip()


@dataclass
class Declaration:
    """报关单主记录。"""

    company: str
    declaration_no: str
    contract_no: str = ""
    export_date: Optional[date] = None
    organization_code: str = ""
    status: str = ""
    created_by: str = ""
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    oa_form_id: str = ""
    origin: str = "manual_or_history"
    collected_pdf_path: str = ""
    collected_pdf_time: Optional[datetime] = None
    run_status: str = "待处理"
    error_message: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    # 条件必要附件判定字段，来源为 OA 主表或人工补充。
    trade_terms: str = ""
    premium: str = ""
    freight: str = ""
    supervision_mode: str = ""
    exists_in_oa: bool = False

    @property
    def key(self) -> str:
        return f"{self.company}:{self.declaration_no}"

    @property
    def robot_owned(self) -> bool:
        # origin 由 OA 同步阶段按配置的机器人创建人判定，避免账号显示名变化。
        return self.origin == "robot_created"

    @property
    def historical(self) -> bool:
        return not self.robot_owned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company": self.company,
            "declaration_no": self.declaration_no,
            "contract_no": self.contract_no,
            "export_date": self.export_date.isoformat() if self.export_date else "",
            "organization_code": self.organization_code,
            "status": self.status,
            "created_by": self.created_by,
            "origin": self.origin,
            "exists_in_oa": self.exists_in_oa,
        }


def next_action(declaration: Declaration) -> str:
    """按 OA 状态与归集 PDF 路径给出下一步动作；历史单据同样适用。"""
    status = str(declaration.status or "").strip()
    if status == STATUS_CONFIRMED:
        return NEXT_MERGE_PDF
    if status == STATUS_PDF:
        path = str(declaration.collected_pdf_path or "").strip()
        if not path:
            return NEXT_MERGE_PDF
        # 本地绝对路径但文件已丢失时重新生成；OA URL 视为已存在。
        if (":" in path[:3] or path.startswith("\\\\")) and not Path(path).is_file():
            return NEXT_MERGE_PDF
        return NEXT_DONE
    return NEXT_WAIT_USER


@dataclass
class CollectionHistory:
    """OA单张收集(DZSJ001)历史行，用于状态与附件更新时间比对。"""

    company: str
    declaration_no: str
    contract_no: str = ""
    export_date: Optional[date] = None
    status: str = ""
    created_by: str = ""
    collected_pdf_path: str = ""
    collected_pdf_time: Optional[datetime] = None
    attachment_times: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self):
        return (str(self.declaration_no or "").strip(), str(self.contract_no or "").strip())


@dataclass
class Attachment:
    """附件项。"""

    kind: str
    source: str
    source_id: str
    updated_at: str = ""
    path: Optional[Path] = None
    robot_managed: bool = False
    content_hash: str = ""
    oa_updated_at: str = ""

    @property
    def key(self):
        return self.kind, self.source_id

    @property
    def effective_updated_at(self) -> str:
        return self.oa_updated_at or self.updated_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "source_id": self.source_id,
            "updated_at": self.updated_at,
            "path": str(self.path) if self.path else "",
            "robot_managed": self.robot_managed,
            "content_hash": self.content_hash,
            "oa_updated_at": self.oa_updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attachment":
        path = data.get("path") or None
        return cls(
            kind=str(data.get("kind") or ""),
            source=str(data.get("source") or ""),
            source_id=str(data.get("source_id") or ""),
            updated_at=str(data.get("updated_at") or ""),
            path=Path(path) if path else None,
            robot_managed=bool(data.get("robot_managed")),
            content_hash=str(data.get("content_hash") or ""),
            oa_updated_at=str(data.get("oa_updated_at") or ""),
        )


@dataclass
class Snapshot:
    """某一来源在某一时点的附件快照。"""

    items: List[Attachment] = field(default_factory=list)
    complete: bool = False
    scope: str = ""
    source: str = ""

    @property
    def kinds(self):
        return {item.kind for item in self.items}


@dataclass
class OrganizationRules:
    """组织必要附件与 PDF 合并顺序。"""

    required: List[str] = field(default_factory=list)
    pdf_order: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.required = [normalize_type(item) for item in self.required]
        self.pdf_order = [normalize_type(item) for item in self.pdf_order]


@dataclass
class PdfReceipt:
    """OA 归集 PDF 回执。"""

    path: str
    merged_at: datetime
    status: str


@dataclass
class CollectionPackage:
    """模块一产物。"""

    declaration_key: str
    items: List[Attachment] = field(default_factory=list)
    missing: List[Dict[str, str]] = field(default_factory=list)
    complete: bool = False
    ambiguous: bool = False
    fetched_at: datetime = field(default_factory=datetime.now)
    manifest_path: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def kinds(self):
        return {item.kind for item in self.items}

    def missing_kinds(self):
        return [item.get("kind", "") for item in self.missing]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "declaration_key": self.declaration_key,
            "items": [item.to_dict() for item in self.items],
            "missing": list(self.missing),
            "complete": self.complete,
            "ambiguous": self.ambiguous,
            "fetched_at": self.fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
            "manifest_path": self.manifest_path,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CollectionPackage":
        return cls(
            declaration_key=str(data.get("declaration_key") or ""),
            items=[Attachment.from_dict(item) for item in data.get("items") or []],
            missing=list(data.get("missing") or []),
            complete=bool(data.get("complete")),
            ambiguous=bool(data.get("ambiguous")),
            fetched_at=parse_datetime(data.get("fetched_at")) or datetime.now(),
            manifest_path=str(data.get("manifest_path") or ""),
            notes=list(data.get("notes") or []),
        )


@dataclass
class OaSyncResult:
    """模块二产物。"""

    declaration: Declaration
    exists: bool = False
    robot_owned: bool = False
    status: str = ""
    added: List[Attachment] = field(default_factory=list)
    updated: List[Attachment] = field(default_factory=list)
    deleted: List[Attachment] = field(default_factory=list)
    # 同类型但源文件更新：先删除OA旧行再上传新文件。
    replaced: List[Tuple[Attachment, List[Attachment]]] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)
    next_action: str = NEXT_WAIT_USER
    snapshot: Optional[Snapshot] = None
    error_message: str = ""


@dataclass
class PdfResult:
    """模块三产物。"""

    path: str
    page_count: int = 0
    merged_at: Optional[datetime] = None
    uploaded: bool = False
    status_verified: bool = False


@dataclass
class RunItem:
    """单张单据运行结果，供结果表与通知汇总。"""

    declaration: Declaration
    package: Optional[CollectionPackage] = None
    sync: Optional[OaSyncResult] = None
    pdf: Optional[PdfResult] = None
    status: str = "待处理"
    error_message: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
