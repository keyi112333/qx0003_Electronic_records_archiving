"""登录OA前通过REST读取“单张收集”单据（模板DZSJ001）。

单张收集记录用于比对历史状态；打印顺序配置表另取DZSJDYSX00，
两者都可导出为临时xlsx，分别与历史结果表和规则表比对。
字段映射缺失时抛缺口，由收集阶段记录并继续处理结果表可覆盖的部分。
"""
import re
from datetime import date, datetime

from core.errors import BusinessPending
from web.oa.rules_api import RulesApi, cell_text
from workflows.qx0003_archive.models import (
    CollectionHistory,
    Declaration,
    normalize_type,
    parse_datetime,
)

DEFAULT_TEMPLATE = "DZSJ001"
TIME_SUFFIX = "附件更新时间"
ALIASES = {
    "组织": "company", "公司": "company", "组织名称": "company", "公司名称": "company",
    "组织编码": "organization_code",
    "报关单号": "declaration_no",
    "合同号": "contract_no", "合同编号": "contract_no",
    "出口日期": "export_date",
    "状态": "status", "单据状态": "status", "收集状态": "status",
    "创建人": "created_by", "发起人": "created_by", "申请人": "created_by",
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


def parse_collection_history(payload):
    """解析单张收集导出，返回按(报关单号,合同编号)去重的历史行。

    每行同时带“状态”和各区“XXX附件更新时间”，用于与结果表比对。
    """
    body = payload.get("data") or {}
    if str(body.get("success")) != "1":
        raise BusinessPending("OA单张收集导出未成功: " + str(body.get("message") or body.get("code") or ""))
    inner = body.get("data") or {}
    fields = (inner.get("definition") or {}).get("fields") or []
    if not fields:
        raise BusinessPending("OA单张收集导出缺少字段定义")
    display = {field.get("name"): str(field.get("display") or "").strip() for field in fields}
    by_name = {}
    for name, text in display.items():
        by_name.setdefault(text, name)
    number_field = by_name.get("报关单号")
    if not number_field:
        raise BusinessPending("OA单张收集导出缺少报关单号字段")
    contract_field = by_name.get("合同编号") or by_name.get("合同号")
    company_field = by_name.get("组织") or by_name.get("公司") or by_name.get("组织名称")
    org_field = by_name.get("组织编码")
    status_field = by_name.get("状态")
    date_field = by_name.get("出口日期")
    user_field = by_name.get("申请人") or by_name.get("创建人")
    pdf_field = by_name.get("归集的PDF路径")
    pdf_time_field = by_name.get("归集为PDF时间")
    time_fields = {}
    for name, text in display.items():
        if not text.endswith(TIME_SUFFIX):
            continue
        kind = normalize_type(text[:-len(TIME_SUFFIX)])
        if kind:
            time_fields[name] = kind
    result, index = [], {}
    for row in inner.get("data") or []:
        master = row.get("masterData") or {}
        number = cell_text(master.get(number_field)).strip()
        if not number:
            continue
        contract = cell_text(master.get(contract_field)).strip() if contract_field else ""
        times = {kind: cell_text(master.get(name))
                 for name, kind in time_fields.items()
                 if cell_text(master.get(name))}
        key = (number, contract)
        if key in index:
            # 同一单据多行时合并附件更新时间，取较新值。
            current = index[key]
            for kind, value in times.items():
                if value > current.attachment_times.get(kind, ""):
                    current.attachment_times[kind] = value
            continue
        history = CollectionHistory(
            company=(cell_text(master.get(company_field)) if company_field else "")
            or (cell_text(master.get(org_field)) if org_field else ""),
            declaration_no=number,
            contract_no=contract,
            export_date=_as_date(cell_text(master.get(date_field)) if date_field else ""),
            status=cell_text(master.get(status_field)) if status_field else "",
            created_by=cell_text(master.get(user_field)) if user_field else "",
            collected_pdf_path=cell_text(master.get(pdf_field)) if pdf_field else "",
            collected_pdf_time=(parse_datetime(cell_text(master.get(pdf_time_field)))
                                if pdf_time_field else None),
            attachment_times=times,
        )
        index[key] = history
        result.append(history)
    return result


class CollectionApi:
    """读取并缓存本轮单张收集单据；与配置表共用OA REST导出接口。"""

    def __init__(self, config, timeout=30):
        self.config = config
        self.rules_api = RulesApi(config, timeout)
        self._payload = None
        self._cache = None
        self._history = None

    def template_code(self):
        """单张收集模板编号，未配置时使用 DZSJ001。"""
        return str(self.config.get("OA配置.收集模板编号")
                   or self.config.get("OA配置.单张收集模板编号") or DEFAULT_TEMPLATE)

    def fetch_json(self):
        """返回原始导出JSON；同一轮运行内只请求一次。"""
        if self._payload is None:
            right_id = self.config.get("OA配置.收集权限ID") or None
            self._payload = self.rules_api.fetch_json(self.template_code(), right_id)
        return self._payload

    def fetch(self):
        if self._cache is not None:
            return self._cache
        self._cache = parse_collection(self.fetch_json())
        return self._cache

    def history(self):
        """单张收集历史行（状态+各类型附件更新时间）；本轮只解析一次。"""
        if self._history is None:
            self._history = parse_collection_history(self.fetch_json())
        return self._history

    def save_excel(self, target):
        """把单张收集导出写成临时xlsx，供与历史结果表比对。"""
        from data_processing.oa_export import export_payload_to_xlsx
        return export_payload_to_xlsx(self.fetch_json(), target)
