"""登录OA前通过REST读取“单张收集”单据（默认模板DZSJDYSX00）。

该模板同时承载单张收集记录与打印顺序配置字段，用于比对历史状态、
按公司名取得必要附件和PDF顺序。字段以根目录“单证收集.xlsx”为准。
字段映射缺失时抛缺口，由收集阶段记录并继续处理结果表可覆盖的部分。
"""
import re
from datetime import date, datetime

from core.errors import BusinessPending
from web.oa.rules_api import RulesApi, cell_text
from workflows.qx0003_archive.models import Declaration

DEFAULT_TEMPLATE = "DZSJDYSX00"
ALIASES = {
    "组织": "company", "公司": "company", "组织名称": "company", "公司名称": "company",
    "组织编码": "organization_code",
    "报关单号": "declaration_no",
    "合同号": "contract_no", "合同编号": "contract_no",
    "出口日期": "export_date",
    "状态": "status", "单据状态": "status", "收集状态": "status",
    "创建人": "created_by", "发起人": "created_by",
    "归集的PDF路径": "collected_pdf_path", "归集PDF路径": "collected_pdf_path",
}


def _display(text):
    return re.sub(r"^\[[^\]]+\]\s*", "", str(text or "")).strip()


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    matched = re.match(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", str(value or "").strip())
    if not matched:
        return None
    try:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    except ValueError:
        return None


def parse_collection(payload):
    """解析导出JSON，返回单张收集单据列表。"""
    body = payload.get("data") or {}
    if str(body.get("success")) != "1":
        raise BusinessPending("OA单张收集导出未成功: " + str(body.get("message") or body.get("code") or ""))
    inner = body.get("data") or {}
    fields = (inner.get("definition") or {}).get("fields") or []
    if not fields:
        raise BusinessPending("OA单张收集导出缺少字段定义")
    headers = {}
    for field in fields:
        key = ALIASES.get(_display(field.get("display")))
        if key and key not in headers:
            headers[key] = field.get("name")
    if "declaration_no" not in headers:
        raise BusinessPending("OA单张收集导出缺少报关单号字段")
    if "company" not in headers and "organization_code" not in headers:
        raise BusinessPending("OA单张收集导出缺少组织字段")
    result, seen = [], set()
    for row in inner.get("data") or []:
        master = row.get("masterData") or {}
        number = cell_text(master.get(headers["declaration_no"]))
        if not number:
            continue
        company = cell_text(master.get(headers["company"])) if "company" in headers else ""
        organization = (cell_text(master.get(headers["organization_code"]))
                        if "organization_code" in headers else "") or company
        if not company and not organization:
            # 无公司/组织编码的行无法与共享目录公司匹配，不当作历史记录。
            continue
        key = (organization or company) + ":" + number
        if key in seen:
            continue
        seen.add(key)
        created_by = cell_text(master.get(headers["created_by"])) if "created_by" in headers else ""
        declaration = Declaration(
            company=company or organization,
            declaration_no=number,
            export_date=_as_date(cell_text(master.get(headers["export_date"]))
                                 if "export_date" in headers else ""),
            contract_no=cell_text(master.get(headers["contract_no"])) if "contract_no" in headers else "",
            organization_code=organization,
            origin="robot_created" if created_by == "RPA" else "manual_or_history",
            status=cell_text(master.get(headers["status"])) if "status" in headers else "",
            created_by=created_by,
            collected_pdf_path=(cell_text(master.get(headers["collected_pdf_path"]))
                                if "collected_pdf_path" in headers else ""),
            oa_form_id=str(row.get("id") or ""),
        )
        result.append(declaration)
    return result


class CollectionApi:
    """读取并缓存本轮单张收集单据；与配置表共用OA REST导出接口。"""

    def __init__(self, config, timeout=30):
        self.config = config
        self.rules_api = RulesApi(config, timeout)
        self._payload = None
        self._cache = None

    def fetch_json(self):
        """返回原始导出JSON；同一轮运行内只请求一次。"""
        if self._payload is None:
            template = (self.config.get("OA配置.收集模板编号")
                        or self.config.get("OA配置.单张收集模板编号") or DEFAULT_TEMPLATE)
            right_id = self.config.get("OA配置.收集权限ID") or None
            self._payload = self.rules_api.fetch_json(template, right_id)
        return self._payload

    def fetch(self):
        if self._cache is not None:
            return self._cache
        self._cache = parse_collection(self.fetch_json())
        return self._cache
