"""通过OA REST批量导出接口读取“单证收集打印顺序配置表”。

配置表按公司/组织维护附件是否必要及附件打印顺序。字段以根目录
“单证收集.xlsx”（DZSJDYSX00）为准。导出接口返回字段定义和表单数据，
本模块只做字段映射与结构校验，不推断业务条件；同一公司存在多条
不一致配置时原样返回，由调用方阻断，不任取第一条。
"""
import json
from datetime import datetime
from urllib import error, request
from urllib.parse import quote

from core.errors import BusinessPending, SystemFailure
from workflows.qx0003_archive.models import OrganizationRules

ORG_DISPLAYS = ("组织编码",)
COMPANY_DISPLAYS = ("公司", "组织", "组织名称", "公司名称")
ORDER_DISPLAYS = ("附件打印顺序", "附件打印顺序-文本")
REQUIRED_SUFFIX = "是否必要"
EXPORT_PATH = "/seeyon/rest/cap4/form/soap/export"
TOKEN_PATH = "/seeyon/rest/token/{username}/{password}"
DEFAULT_BEGIN = "2000-01-01 00:00:00"
# 单张收集打印顺序配置表模板编号；未配置时使用该默认值。
DEFAULT_TEMPLATE = "DZSJDYSX00"


def _first_field(display, names):
    """按显示名优先级返回唯一字段编码；字段编码为空时返回None。"""
    for name in names:
        for field, text in display.items():
            if text == name:
                return field
    return None


def cell_text(cell):
    """取单元格显示值；下拉字段的枚举ID仅作回退。"""
    if not isinstance(cell, dict):
        return ""
    return str(cell.get("showValue") or cell.get("value") or "").strip()


def split_order(text):
    parts = str(text or "").replace("，", "/").replace("、", "/").split("/")
    return [part.strip() for part in parts if part.strip()]


def parse_rules(payload):
    """解析导出JSON，返回 {公司名/组织编码: [OrganizationRules, ...]}。

    字段以根目录“单证收集.xlsx”的DZSJDYSX00表头为准：组织=公司名，组织编码=编码，
    附件打印顺序决定PDF合并顺序，各“xx是否必要”决定需要在OA上传的附件类别。
    公司名与组织编码同时作为索引，便于共享目录按公司名匹配；同一公司重复的
    相同配置去重，配置不一致时保留多条由调用方阻断。
    """
    body = payload.get("data") or {}
    if str(body.get("success")) != "1":
        raise BusinessPending("OA配置表导出未成功: " + str(body.get("message") or body.get("code") or ""))
    inner = body.get("data") or {}
    fields = (inner.get("definition") or {}).get("fields") or []
    if not fields:
        raise BusinessPending("OA配置表导出缺少字段定义")
    display = {f.get("name"): str(f.get("display") or "").strip() for f in fields}
    org_field = _first_field(display, ORG_DISPLAYS)
    company_field = _first_field(display, COMPANY_DISPLAYS)
    order_field = _first_field(display, ORDER_DISPLAYS)
    required_fields = [(name, text[:-len(REQUIRED_SUFFIX)])
                       for name, text in display.items() if text.endswith(REQUIRED_SUFFIX)]
    if not org_field and not company_field:
        raise BusinessPending("OA配置表缺少公司/组织编码字段")
    if not order_field:
        raise BusinessPending("OA配置表缺少附件打印顺序字段")
    result = {}
    for row in inner.get("data") or []:
        master = row.get("masterData") or {}
        org = cell_text(master.get(org_field)) if org_field else ""
        company = cell_text(master.get(company_field)) if company_field else ""
        if not org and not company:
            continue
        required = [kind for name, kind in required_fields
                    if cell_text(master.get(name)) == "是"]
        order = split_order(cell_text(master.get(order_field)))
        if not required and not order:
            # 单张收集记录未填写配置字段时不参与公司配置判定。
            continue
        rules = OrganizationRules(required, order)
        signature = (tuple(rules.required), tuple(rules.pdf_order))
        for key in (company, org):
            if not key:
                continue
            bucket = result.setdefault(key, [])
            if not any((tuple(item.required), tuple(item.pdf_order)) == signature for item in bucket):
                bucket.append(rules)
    if not result:
        raise BusinessPending("OA配置表在导出时间范围内没有配置记录")
    return result


class RulesApi:
    """读取并缓存一轮运行内的组织规则；不写回OA。"""

    def __init__(self, config, timeout=30):
        self.config = config
        self.timeout = timeout
        self._cache = None

    def _base(self):
        return str(self.config.require("OA配置.网址")).rstrip("/")

    def token(self):
        username = self.config.require("OA配置.REST账号")
        password = self.config.require("OA配置.REST密码")
        url = self._base() + TOKEN_PATH.format(
            username=quote(str(username), safe=""), password=quote(str(password), safe=""))
        try:
            with request.urlopen(request.Request(url, method="GET"), timeout=self.timeout) as resp:
                token = resp.read().decode("utf-8", "replace").strip()
        except (error.HTTPError, error.URLError) as exc:
            raise SystemFailure("OA REST token获取失败") from exc
        if not token or token.startswith("<!") or "<html" in token.lower():
            raise SystemFailure("OA REST token返回内容异常")
        return token

    def fetch_json(self, template_code, right_id=None):
        """调用OA REST批量导出接口，返回解析后的JSON；模板编号由调用方指定。"""
        payload = {
            "templateCode": str(template_code),
            "beginDateTime": str(self.config.get("OA配置.规则起始时间") or DEFAULT_BEGIN),
            "endDateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rightId": str(right_id or self.config.require("OA配置.规则权限ID")),
        }
        headers = {"Accept": "*/*", "Content-Type": "application/json;charset=UTF-8",
                   "token": self.token()}
        req = request.Request(self._base() + EXPORT_PATH,
                              data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            raise SystemFailure(f"OA导出HTTP错误: {exc.code}") from exc
        except error.URLError as exc:
            raise SystemFailure("OA导出网络错误") from exc
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise BusinessPending("OA导出返回不是JSON") from None

    def fetch(self):
        """返回 {组织编码: [OrganizationRules, ...]}；本轮运行内只请求一次。"""
        if self._cache is not None:
            return self._cache
        self._cache = parse_rules(self.fetch_json(self.template_code()))
        return self._cache

    def template_code(self):
        """配置表模板编号，未配置时使用 DZSJDYSX00。"""
        return str(self.config.get("OA配置.规则模板编号") or DEFAULT_TEMPLATE)

    def save_excel(self, target):
        """把配置表导出写成临时xlsx，供与规则表比对。"""
        from data_processing.oa_export import export_payload_to_xlsx
        return export_payload_to_xlsx(self.fetch_json(self.template_code()), target)
