#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块一：附件收集。

职责：在进入 OA 前，把单张报关单需要的附件从 QX0002 共享目录、
一卡通接口、BIP 结果文件/附件目录收集到本地，输出 CollectionPackage。
本模块不读写 OA。
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.errors import BusinessPending, ConfigError, SystemFailure
from core.task_base import TaskBase
from log.loggingConfig import logger
from utils.atomic import atomic_target
from web.bip.adapter import BipAdapter
from web.yikatong.pages import YikatongAdapter
from workflows.qx0003_archive.models import (
    Attachment,
    CollectionPackage,
    Declaration,
    OrganizationRules,
    normalize_type,
    parse_date,
)

ATTACHMENT_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
FILE_NAME_RE = re.compile(
    r"^(?P<no>[^-]+)-(?P<contract>.+)-(?P<date>\d{8})-(?P<kind>[^-]+)"
    r"\.(?P<ext>pdf|jpg|jpeg|png|tif|tiff)$",
    re.IGNORECASE,
)
FOLDER_RE = re.compile(r"^(?P<no>[^-]+)-(?P<contract>.+)$")
DATE_IN_TEXT_RE = re.compile(r"(20\d{6})")

# 一卡通「合同文件类型」字典；值为OA明细区使用的附件类型名。
YIKATONG_FILETYPES = (
    ("合同", "合同"),
    ("提单", "提单"),
    ("报关单", "报关单"),
    ("放行条", "放行通知单"),
)

# 顺序很重要：先匹配更长、更具体的名称，再匹配“发票”“报关单”。
KIND_PATTERNS = (
    ("国内运费形式发票", ("国内运费形式发票", "运费形式发票")),
    ("国内运输发票", ("国内运输发票",)),
    ("代理报关发票", ("代理报关发票",)),
    ("电子委托协议", ("电子委托协议", "委托协议")),
    ("放行通知单", ("放行通知单", "放行单")),
    ("保税核注清单", ("保税核注清单", "报税核注清单", "核注清单")),
    ("CIF保险单", ("CIF保险单", "保险单")),
    ("装箱单", ("装箱单",)),
    ("销售发票", ("销售发票",)),
    ("发票", ("发票",)),
    ("合同", ("合同",)),
    ("提单", ("提单",)),
    ("报关单", ("报关单",)),
)


def kind_from_text(text: str) -> str:
    """按文件名关键字识别附件类型。"""
    value = str(text or "").upper()
    for kind, keywords in KIND_PATTERNS:
        if any(keyword.upper() in value for keyword in keywords):
            return kind
    return ""


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def conditional_kinds(declaration: Declaration) -> List[str]:
    """按成交方式、保费、运费、监管方式追加条件必要附件。"""
    kinds = []
    trade_terms = str(declaration.trade_terms or "").strip().upper()
    if trade_terms == "CIF" and str(declaration.premium or "").strip():
        kinds.append("CIF保险单")
    if trade_terms == "EXW" and str(declaration.freight or "").strip():
        kinds.append("国内运费形式发票")
    if "进料" in str(declaration.supervision_mode or ""):
        kinds.append("保税核注清单")
    return kinds


def required_kinds(rules: Optional[OrganizationRules], declaration: Declaration) -> List[str]:
    required = list(rules.required) if rules else []
    required.extend(conditional_kinds(declaration))
    return list(dict.fromkeys(required))


@dataclass
class FileRecord:
    company: str
    declaration_no: str
    contract_no: str
    export_date: Optional[date]
    kind: str
    path: Path
    source_id: str
    updated_at: str


class SharedFolderSource:
    """QX0002 共享目录附件来源。"""

    name = "共享盘"

    def __init__(self, root):
        self.root = Path(root)
        self._cache: Dict[str, List[FileRecord]] = {}

    def _org_dir(self, company: str) -> Path:
        direct = self.root / company
        if direct.is_dir():
            return direct
        if self.root.name == company:
            return self.root
        return direct

    def _parse(self, path: Path, company: str) -> Optional[FileRecord]:
        matched = FILE_NAME_RE.match(path.name)
        if matched:
            number = matched.group("no").strip()
            contract = matched.group("contract").strip()
            export_date = parse_date(matched.group("date"))
            kind = kind_from_text(matched.group("kind")) or normalize_type(matched.group("kind"))
        else:
            folder = FOLDER_RE.match(path.parent.name)
            if not folder:
                return None
            number = folder.group("no").strip()
            contract = folder.group("contract").strip()
            kind = kind_from_text(path.stem)
            date_match = DATE_IN_TEXT_RE.search(path.stem) or DATE_IN_TEXT_RE.search(path.parent.name)
            export_date = parse_date(date_match.group(1)) if date_match else None
        if not number or not kind:
            return None
        stat = path.stat()
        return FileRecord(
            company=company,
            declaration_no=number,
            contract_no=contract,
            export_date=export_date,
            kind=normalize_type(kind),
            path=path,
            source_id=hash_file(path),
            updated_at=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        )

    def index(self, company: str) -> List[FileRecord]:
        if company in self._cache:
            return self._cache[company]
        records: List[FileRecord] = []
        org_dir = self._org_dir(company)
        if org_dir.is_dir():
            for path in org_dir.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in ATTACHMENT_SUFFIXES:
                    continue
                try:
                    record = self._parse(path, company)
                except OSError as exc:
                    logger.warning("读取共享盘文件失败，已跳过 %s: %s", path, exc)
                    continue
                if record:
                    records.append(record)
        self._cache[company] = records
        logger.info("共享盘扫描完成: %s，附件%d个", company, len(records))
        return records

    def discover(self, companies: List[str], params) -> List[Declaration]:
        """扫描共享目录生成待处理报关单。"""
        groups: Dict[Tuple[str, str, str], Declaration] = {}
        for company in companies:
            for record in self.index(company):
                if params.declarations and record.declaration_no not in params.declarations:
                    continue
                if not params.in_period(record.export_date):
                    continue
                key = (company, record.declaration_no, record.contract_no)
                declaration = groups.get(key)
                if declaration is None:
                    declaration = Declaration(
                        company=company,
                        declaration_no=record.declaration_no,
                        contract_no=record.contract_no,
                        export_date=record.export_date,
                    )
                    groups[key] = declaration
                elif declaration.export_date is None and record.export_date:
                    declaration.export_date = record.export_date
        result = sorted(groups.values(),
                        key=lambda item: (item.company, item.export_date or date.min, item.declaration_no))
        logger.info("待处理单据解析完成: %d条", len(result))
        return result

    def files_for(self, declaration: Declaration) -> List[FileRecord]:
        records = self.index(declaration.company)
        if declaration.contract_no:
            return [item for item in records
                    if item.declaration_no == declaration.declaration_no
                    and item.contract_no == declaration.contract_no]
        return [item for item in records if item.declaration_no == declaration.declaration_no]

    def collect(self, declaration: Declaration):
        """返回 (附件列表, 提示列表, 是否多候选)。"""
        by_kind: Dict[str, List[FileRecord]] = {}
        for record in self.files_for(declaration):
            by_kind.setdefault(record.kind, []).append(record)
        items, notes = [], []
        ambiguous = False
        for kind, records in by_kind.items():
            records.sort(key=lambda item: item.path.stat().st_mtime, reverse=True)
            if len(records) > 1:
                ambiguous = True
                notes.append(f"{kind}存在{len(records)}个候选文件，已取最新: {records[0].path.name}")
            record = records[0]
            items.append(Attachment(
                kind=kind,
                source=self.name,
                source_id=record.source_id,
                updated_at=record.updated_at,
                path=record.path,
                robot_managed=True,
                content_hash=record.source_id,
            ))
        return items, notes, ambiguous


class CollectSubtask(TaskBase):
    """子任务：附件收集（共享盘 + 一卡通 + BIP）。"""

    def __init__(self, context, declaration: Optional[Declaration] = None,
                 rules: Optional[OrganizationRules] = None, *,
                 yikatong=None, bip=None, shared=None):
        super().__init__("附件收集", context)
        self.config = context.config
        self.declaration = declaration
        self.rules = rules
        self.temp_dir = Path(self.config.get_path("temp"))
        self.run_id = str(context.get("run_id") or "")
        self.audit = context.get("audit")
        self.input_root = Path(self.config.get_path("input"))
        root = self.config.get("路径配置.报关单证存放路径")
        shared_source = shared or context.get("shared_source")
        self.shared = shared_source or (SharedFolderSource(root) if root else None)
        self.yikatong = yikatong or YikatongAdapter(config=self.config)
        self.bip = bip or BipAdapter(config=self.config)
        self.package: Optional[CollectionPackage] = None

    def execute(self) -> bool:
        if self.declaration is None:
            raise ValueError("附件收集子任务缺少报关单")
        self.package = self.collect(self.declaration, self.rules)
        self.context.set(f"package:{self.declaration.key}", self.package)
        return True

    def discover(self, params, companies: List[str]) -> List[Declaration]:
        if not self.shared:
            raise ConfigError("缺少配置: 路径配置.报关单证存放路径")
        declarations = self.shared.discover(companies, params)
        found_numbers = {item.declaration_no for item in declarations}
        default_company = companies[0] if companies else ""
        for number in params.declarations:
            if number not in found_numbers:
                # 指定了报关单但共享目录没有文件，生成占位记录交由 OA 同步判定。
                declarations.append(Declaration(
                    company=default_company, declaration_no=number,
                    contract_no="", export_date=None))
        return declarations

    def _manifest_path(self, declaration: Declaration) -> Path:
        safe = re.sub(r"[^\w.-]+", "_", declaration.declaration_no)
        return self.temp_dir / "collect" / f"{declaration.company}_{safe}" / "manifest.json"

    def _merge(self, items: List[Attachment]) -> List[Attachment]:
        result, seen = [], set()
        for item in items:
            key = (item.kind, item.source_id)
            if not item.kind or not item.source_id or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _latest_invoices(self, items: List[Attachment],
                         notes: List[str]) -> List[Attachment]:
        """销售发票/运费发票存在多份时，只保留最后更新时间的一份。"""
        targets = ("销售发票", "国内运费形式发票")
        latest = {}
        result = []
        for item in items:
            if item.kind not in targets:
                result.append(item)
                continue
            current = latest.get(item.kind)
            if current is None or str(item.updated_at) > str(current.updated_at):
                latest[item.kind] = item
        for kind, item in latest.items():
            count = sum(1 for candidate in items if candidate.kind == kind)
            if count > 1:
                notes.append(f"{kind}存在{count}个附件，已取最后更新时间的一份")
            result.append(item)
        return result

    def _single_window(self, declaration: Declaration,
                       present: set) -> List[Attachment]:
        """共享盘缺失时，读取02_输入/单一窗口下的人工导出文件。"""
        roots = [self.input_root / "单一窗口" / declaration.declaration_no,
                 self.input_root / "单一窗口"]
        folder = next((path for path in roots if path.is_dir()), None)
        if folder is None:
            return []
        result = []
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in ATTACHMENT_SUFFIXES:
                continue
            if path.parent.name != declaration.declaration_no and declaration.declaration_no not in path.name:
                continue
            kind = kind_from_text(path.stem)
            normalized = normalize_type(kind) if kind else ""
            if not normalized or normalized in present:
                continue
            stat = path.stat()
            source_id = hash_file(path)
            result.append(Attachment(
                kind=normalized,
                source="单一窗口",
                source_id=source_id,
                updated_at=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                path=path,
                robot_managed=True,
                content_hash=source_id,
            ))
            present.add(normalized)
        return result

    def collect(self, declaration: Declaration, rules: Optional[OrganizationRules]) -> CollectionPackage:
        items: List[Attachment] = []
        notes: List[str] = []
        ambiguous = False

        if self.shared:
            shared_items, shared_notes, shared_ambiguous = self.shared.collect(declaration)
            items.extend(shared_items)
            notes.extend(shared_notes)
            ambiguous = ambiguous or shared_ambiguous

        # 一卡通：按合同号 + 文件类型查询并下载；共享盘已有的类型不重复取。
        if declaration.contract_no:
            try:
                shared_kinds = {item.kind for item in items}
                filetypes = [name for name, kind in YIKATONG_FILETYPES
                             if kind not in shared_kinds]
                records = (self.yikatong.list_records(declaration, filetypes=filetypes)
                           if filetypes else [])
                logger.info("一卡通查询完成: %s，合同号=%s，类型=%s，返回%d条",
                            declaration.key, declaration.contract_no,
                            "、".join(filetypes) or "无需查询", len(records))
                folder = self.temp_dir / "collect" / f"{declaration.company}_{declaration.declaration_no}" / "yikatong"
                ykt_items = self.yikatong.download(declaration, records, folder)
                for item in ykt_items:
                    if not item.kind and item.path:
                        item.kind = normalize_type(item.path.stem)
                items.extend(ykt_items)
                if not records:
                    notes.append("一卡通无该合同附件")
            except (BusinessPending, SystemFailure, ConfigError) as exc:
                notes.append(f"一卡通待补: {exc}")
                logger.warning("一卡通收集失败 %s: %s", declaration.key, exc)
        else:
            notes.append("合同编号为空，未查询一卡通")

        # BIP：销售发票/BIP其他附件。
        try:
            snapshot = self.bip.snapshot(declaration)
            items.extend(snapshot.items)
            if not snapshot.items:
                notes.append("BIP无销售发票附件")
        except (BusinessPending, SystemFailure, ConfigError) as exc:
            notes.append(f"BIP待补: {exc}")
            logger.warning("BIP收集失败 %s: %s", declaration.key, exc)

        items = self._merge(items)
        items = self._latest_invoices(items, notes)
        items.extend(self._single_window(declaration, {item.kind for item in items}))
        items = self._merge(items)
        required = required_kinds(rules, declaration)
        present = {item.kind for item in items}
        missing = [{"kind": kind, "reason": "未收集到"} for kind in required if kind not in present]
        complete = not missing and not ambiguous and rules is not None
        if rules is None:
            notes.append("未读取组织规则，必要附件完整性待OA阶段判定")

        package = CollectionPackage(
            declaration_key=declaration.key,
            items=items,
            missing=missing,
            complete=complete,
            ambiguous=ambiguous,
            notes=notes,
        )
        self._write_manifest(declaration, package, required)
        if self.audit:
            self.audit.write("collect", declaration.key, "collect", complete and "成功" or "部分收集",
                             {"items": len(items), "missing": missing, "notes": notes})
        logger.info("附件收集完成: %s，附件%d个，完整=%s，缺失=%s",
                    declaration.key, len(items), complete, [item["kind"] for item in missing])
        return package

    def _write_manifest(self, declaration: Declaration,
                        package: CollectionPackage, required: List[str]) -> None:
        manifest = self._manifest_path(declaration)
        package.manifest_path = str(manifest)
        payload = package.to_dict()
        payload["declaration"] = declaration.to_dict()
        payload["required"] = required
        with atomic_target(manifest) as pending:
            pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def apply_rules(self, package: CollectionPackage, declaration: Declaration,
                    rules: Optional[OrganizationRules]) -> CollectionPackage:
        """OA阶段拿到组织规则后补全缺失与完整标记，并回写清单。"""
        required = required_kinds(rules, declaration)
        present = package.kinds
        package.missing = [{"kind": kind, "reason": "未收集到"}
                           for kind in required if kind not in present]
        package.complete = rules is not None and not package.missing and not package.ambiguous
        self._write_manifest(declaration, package, required)
        if self.audit:
            self.audit.write("collect", declaration.key, "rules",
                             package.complete and "成功" or "部分收集",
                             {"required": required, "missing": package.missing})
        logger.info("组织规则应用完成: %s，必要%d类，缺失=%s",
                    declaration.key, len(required), package.missing)
        return package

    def load_package(self, declaration: Declaration) -> Optional[CollectionPackage]:
        """按 manifest 复用上一轮收集结果。"""
        path = self._manifest_path(declaration)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CollectionPackage.from_dict(data)
        except (OSError, ValueError) as exc:
            logger.warning("收集清单读取失败，将重新收集 %s: %s", declaration.key, exc)
            return None
