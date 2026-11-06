"""依据OA录制实现登录、列表查找、主表查重/新建/回查、附件增删下载和导出准备。

列表转业务对象等尚未录制的操作仍显式阻断；归集PDF不上传，只更新单据状态。
不点击不明遮罩，不使用force=True，不循环点击全局“确定”。
"""
import re
from dataclasses import replace
from pathlib import Path
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, expect
from core.errors import BusinessPending, ConfigError, ImplementationRequired, SystemFailure
from core.retry import Verification
from log.loggingConfig import logger
from web.qx0003.adapters import OaAdapter
from web.oa.attachment_operations import AttachmentOperations
from web.oa.collection_api import CollectionApi
from web.oa.detail_tables import DETAIL_TABLES, column_mapping
from web.oa.rules_api import RulesApi
from workflows.qx0003_archive.models import (
    Attachment, Declaration, PdfReceipt, Snapshot, parse_datetime,
)

EXPORT_FIELDS = (
    "[主表] 状态", "[主表] 报关单号", "[主表] 合同编号",
    "[主表] 报关单附件是否存在", "[主表] 放行单附件是否存在",
    "[主表] 合同附件是否存在", "[主表] 提单附件是否存在",
    "[主表] 销售发票附件是否存在", "[主表] 归集的PDF路径",
    "[主表] 创建时间", "[主表] 修改时间", "[主表] 创建人",
)

# OA“附件来源”选项；共享盘文件按既有规则标注电子口岸。
OA_SOURCE_LABELS = {"共享盘": "电子口岸", "一卡通": "一卡通", "BIP": "BIP", "单一窗口": "电子口岸"}

# 按当前状态值定位状态下拉图标，返回它在 .CAP.suffix_icon 中的序号；不依赖动态字段号。
STATUS_TRIGGER_JS = """(root, status) => {
  const inputs = Array.from(root.querySelectorAll('input'));
  const hit = inputs.find(i => (i.value || '').trim() === status);
  if (!hit) return -1;
  const all = Array.from(root.querySelectorAll('.CAP.suffix_icon'));
  let box = hit.parentElement;
  for (let i = 0; i < 6 && box; i++, box = box.parentElement) {
    const icon = Array.from(box.querySelectorAll('.CAP.suffix_icon'))[0];
    if (icon) return all.indexOf(icon);
  }
  return -1;
}"""

# 只读取当前列表DOM，不点击、不调用框架内部接口。
LIST_ROWS = """body => Array.from(body.querySelectorAll('tr')).map((row, index) => {
  const cells = Array.from(row.children).filter(e => ['TD','TH'].includes(e.tagName));
  return {index, visible: !!row.getClientRects().length,
    merged: cells.some(c => c.colSpan !== 1 || c.rowSpan !== 1),
    values: cells.map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim())};
})"""

class ArchiveOaAdapter(AttachmentOperations, OaAdapter):
    BUSINESS_ID = '000_/cap4/busi_-7866127308096582419'
    FORM_FIELDS = {"export_date": "#field0080_id", "declaration_no": "#field0079_id",
                   "contract_no": "#field0087_id"}  # 用户已确认
    # 2026-11-06录制：新建后先通过主表选择器确定公司/组织。
    ORGANIZATION_PICKER = ".cap4-text__cnt.is-relatinbrowse"
    ORGANIZATION_TRIGGER = ".icon.CAP.cap-icon-zhubiaoxuanzeqi"
    LIST_HEADERS = ("状态", "报关单号", "合同编号")
    DETAIL_TITLES = {
        "报关单": "报关单信息", "合同": "合同信息", "放行通知单": "放行通知单信息",
        "提单": "提单信息", "销售发票": "销售发票信息", "电子委托协议": "电子委托协议信息",
        "发票": "发票信息", "装箱单": "装箱单信息", "保税核注清单": "报税核注清单信息",
        "代理报关发票": "代理报关发票信息", "CIF保险单": "CIF保险单信息",
    }

    def __init__(self, browser=None, timeout=30000, config=None):
        super().__init__(browser)
        self.archive_page = None
        self.timeout = timeout
        self.config = config
        self.rules_api = RulesApi(config) if config is not None else None
        self.collection_api = CollectionApi(config) if config is not None else None
        self._rules_cache = None

    def read_all_rules(self):
        """读取配置表(DZSJDYSX00)，供判定必要附件与打印顺序；只读接口，不写回OA。"""
        if self.rules_api is None:
            raise ConfigError("OA适配器未注入配置，无法读取打印顺序配置表")
        if self._rules_cache is not None:
            return self._rules_cache
        self._rules_cache = self.rules_api.fetch()
        return self._rules_cache

    def list_collection_records(self):
        """登录OA前读取历史单张收集单，用于比对新增/补齐状态。"""
        if self.collection_api is None:
            raise ConfigError("OA适配器未注入配置，无法读取单张收集单据")
        return self.collection_api.fetch()

    def list_collection_history(self):
        """单张收集历史行（状态+各类型附件更新时间），供与结果表比对。"""
        if self.collection_api is None:
            raise ConfigError("OA适配器未注入配置，无法读取单张收集历史")
        return self.collection_api.history()

    def export_collection_excel(self, target):
        """导出单张收集(DZSJ001)为临时xlsx，供与历史结果表比对。"""
        if self.collection_api is None:
            raise ConfigError("OA适配器未注入配置，无法导出单张收集")
        return self.collection_api.save_excel(target)

    def export_rules_excel(self, target):
        """导出打印顺序配置表(DZSJDYSX00)为临时xlsx，供与规则表比对。"""
        if self.rules_api is None:
            raise ConfigError("OA适配器未注入配置，无法导出配置表")
        return self.rules_api.save_excel(target)

    def read_rules(self, declaration):
        """按公司名优先、组织编码兜底读取配置；多条配置不判定时阻断。"""
        if self.rules_api is None:
            raise ConfigError("OA适配器未注入配置，无法读取打印顺序配置表")
        company = str(declaration.company or "").strip()
        org = str(declaration.organization_code or "").strip()
        keys = list(dict.fromkeys(key for key in (company, org) if key))
        if not keys:
            raise BusinessPending("报关单缺少公司名/组织编码，不能查询打印顺序配置")
        rules_map = self.read_all_rules()
        for key in keys:
            candidates = rules_map.get(key) or []
            if not candidates:
                continue
            if len(candidates) > 1:
                raise BusinessPending("OA打印顺序配置存在多条且无法判定: " + key)
            rules = candidates[0]
            if not rules.pdf_order:
                raise BusinessPending("OA打印顺序为空: " + key)
            return rules
        raise BusinessPending("OA打印顺序配置表没有该公司/组织: " + "、".join(keys))

    @property
    def portal(self):
        if self.browser is None:
            raise BusinessPending("OA浏览器尚未启动")
        return self.browser.get_page()

    def _unique(self, locator, description):
        locator.first.wait_for(state="visible", timeout=self.timeout)
        if locator.count() != 1:
            raise ImplementationRequired(description + "匹配不唯一，请补充容器定位")
        return locator

    def _click(self, locator, description):
        target = self._unique(locator, description)
        target.scroll_into_view_if_needed(timeout=self.timeout)
        # 普通点击自动等待跨iframe遮挡消失；失败交系统重试，不强制穿透遮罩。
        target.click(timeout=self.timeout)

    def login(self, credential):
        try:
            page = self.portal
            target = str(self.config.get("OA配置.网址") or "").strip() if self.config else ""
            if target:
                page.goto(target, wait_until="domcontentloaded", timeout=self.timeout)
            if page.locator("#login_username").is_visible():
                page.locator("#login_username").fill(credential.username)
                page.locator("#login_password1").fill(credential.password)
                self._click(page.get_by_role("button", name=re.compile(r"^登\s*录$")), "登录按钮")
            # 业务子菜单可能尚未展开，不能要求子菜单已插入DOM才算登录成功。
            page.get_by_text('销售管理', exact=True).wait_for(state='visible', timeout=self.timeout)
            return True
        except PlaywrightError as exc:
            raise SystemFailure(f"OA登录或主页加载失败；请检查网址、会话和账号: {exc}") from None

    def open_archive_list(self):
        try:
            if self.archive_page is not None and not self.archive_page.is_closed():
                return self.archive_page
            page = self.portal
            entry = page.locator('[id="' + self.BUSINESS_ID + '"] > .navText')
            if not entry.is_visible():
                # 替代录制li:nth-child(9)，使用已知菜单显示文字。
                self._click(page.get_by_text("销售管理", exact=True), "销售管理菜单")
            with page.expect_popup(timeout=self.timeout) as popup:
                self._click(entry, "单证业务入口")
            self.archive_page = popup.value
            self.archive_page.locator("#cap4UnflowList").wait_for(state="visible", timeout=self.timeout)
            return self.archive_page
        except PlaywrightError as exc:
            self.archive_page = None
            raise SystemFailure(f"OA业务弹窗或单据列表未加载完成: {exc}") from None

    def list_frame(self):
        return self.open_archive_list().frame_locator("#cap4UnflowList")

    def form_frame(self):
        return self.list_frame().frame_locator("#targetFrame").frame_locator("#cap4FormContent")

    def _refresh_list(self):
        """回到列表并等待列表应用加载完成；不整页刷新，避免重复加载。"""
        page = self.open_archive_list()
        self.list_frame().get_by_role("textbox", name="搜索关键字").first.wait_for(
            state="visible", timeout=self.timeout)
        return page

    def _list_snapshot(self):
        """读取列表表头和数据行；表头不唯一时阻断，不猜列。"""
        rows = self.list_frame().locator("body").evaluate(LIST_ROWS)
        for row in rows:
            row["values"] = [re.sub(r"^\[[^\]]+\]\s*", "", value) for value in row["values"]]
        headers = [row for row in rows if all(name in row["values"] for name in self.LIST_HEADERS)]
        if len(headers) != 1:
            raise ImplementationRequired("单据列表表头不唯一或缺少状态/报关单号/合同编号/创建人，需补充列表结构")
        header = headers[0]
        if header["merged"]:
            raise ImplementationRequired("单据列表表头含合并单元格，列索引不可靠，需补充列表结构")
        records = [row for row in rows if row["visible"] and row["index"] > header["index"]
                   and len(row["values"]) == len(header["values"]) and not row["merged"]]
        return header["values"], records

    def _list_total(self, frame):
        matched = re.search(r"共\s*(\d+)\s*条", frame.locator("body").inner_text(timeout=self.timeout))
        return int(matched.group(1)) if matched else None

    def open_new_form(self):
        try:
            buttons = self.list_frame().locator(
                "button:visible", has_text=re.compile(r"^\s*新建\s*$"))
            buttons.first.wait_for(state="visible", timeout=self.timeout)
            # 列表上下各有一套工具栏，录制使用第二个；只有一个时使用第一个。
            target = buttons.nth(1) if buttons.count() > 1 else buttons.first
            self._click(target, "列表新建按钮")
            self.form_frame().locator(self.FORM_FIELDS["declaration_no"]).wait_for(
                state="visible", timeout=self.timeout)
        except PlaywrightError:
            raise SystemFailure("OA新建表单不可操作或被遮挡") from None

    def fill_new_form(self, declaration):
        """填写已打开表单；不等于保存成功，不设置机器人创建来源。"""
        if not declaration.export_date or not declaration.declaration_no or not declaration.contract_no:
            raise BusinessPending("填单缺少出口日期、报关单号或合同编号")
        values = {"export_date": declaration.export_date.isoformat(),
                  "declaration_no": declaration.declaration_no, "contract_no": declaration.contract_no}
        try:
            frame = self.form_frame()
            for field, value in values.items():
                box = self._unique(frame.locator(self.FORM_FIELDS[field]).get_by_role("textbox"), field)
                box.scroll_into_view_if_needed(timeout=self.timeout)
                box.fill(str(value), timeout=self.timeout)
                box.press("Tab")
                expect(box).to_have_value(str(value), timeout=self.timeout)
        except PlaywrightError:
            raise SystemFailure("OA字段填入或回显校验失败") from None

    def _confirm_layer(self, page, description):
        """只点击可见layui弹窗内的确定；不点遮罩，也不循环点击全局确定。"""
        button = page.locator(".layui-layer:visible").get_by_text("确定", exact=True)
        if button.count() != 1:
            raise ImplementationRequired(description + "的确定按钮不唯一，需补充弹窗结构")
        self._click(button, description + "确定")

    def select_organization(self, company):
        """按2026-11-06录制用主表选择器确定公司；搜索词使用单据公司名。"""
        company = str(company or "").strip()
        if not company:
            raise BusinessPending("新建主表缺少公司名，不能选择组织")
        stage = "定位公司字段"
        try:
            page = self.open_archive_list()
            frame = self.form_frame()
            # 按最新录制：直接点击组织字段右侧的主表选择图标，不先点文本。
            stage = "点击主表选择器"
            self._click(self._unique(frame.locator(self.ORGANIZATION_TRIGGER), "主表选择器"), "主表选择器")
            stage = "等待主表选择弹窗"
            layer = 'iframe[name="layui-layer-iframe1"]'
            if page.locator(layer).count() == 0:
                layer = 'iframe[name^="layui-layer-iframe"]:visible'
            self._unique(page.locator(layer), "主表选择弹窗")
            picker = page.frame_locator(layer)
            stage = "读取选择器搜索框"
            search = picker.get_by_role("textbox", name="搜索关键字")
            search.first.wait_for(state="visible", timeout=self.timeout)
            if search.count() < 1:
                raise ImplementationRequired("主表选择器缺少搜索框，需补充定位")
            # 最新录制使用第一个搜索框。
            box = search.first
            stage = "填写公司搜索词"
            box.fill(company, timeout=self.timeout)
            stage = "点击选择器筛选"
            self._click(picker.get_by_role("button", name="筛选", exact=True), "筛选按钮")
            stage = "选择公司搜索结果"
            self._click(picker.get_by_text(company, exact=True).first, "公司搜索结果")
            stage = "确认主表选择"
            self._confirm_layer(page, "主表选择")
            remaining = page.locator(layer)
            try:
                expect(remaining).to_have_count(0, timeout=5000)
            except AssertionError:
                # 兼容部分环境一次确定后仍有二次确认。
                stage = "二次确认主表选择"
                self._confirm_layer(page, "主表选择二次确认")
                expect(remaining).to_have_count(0, timeout=self.timeout)
        except PlaywrightError as exc:
            raise SystemFailure(f"OA主表选择器不可操作或被遮挡（{stage}）: {exc}") from None

    def save_form(self):
        """按录制点击保存并关闭；表单未关闭即视为结果待核验，不重复保存。"""
        try:
            button = self._unique(self.form_frame().get_by_text("保存并关闭", exact=True), "保存并关闭按钮")
            self._click(button, "保存并关闭")
            expect(button).to_have_count(0, timeout=self.timeout)
            logger.info("OA保存并关闭完成")
        except (PlaywrightError, AssertionError):
            raise SystemFailure("OA保存并关闭后表单未关闭，保存结果待核验；禁止重试保存") from None

    def expose_detail_row(self, row_locator):
        """调用方传入已按单据键限定的行/控件；滚动后检查是否真正可点击。"""
        try:
            row = self._unique(row_locator, "目标明细行")
            row.scroll_into_view_if_needed(timeout=self.timeout)
            row.click(trial=True, timeout=self.timeout)
            return row
        except PlaywrightError:
            raise BusinessPending("目标单据行仍被遮挡；需关闭已确认的弹窗后重试，不穿透遮罩点击") from None

    def focus_detail(self, kind):
        if kind == "transport_invoice":
            raise ImplementationRequired("两种运输发票属于同类；OA存在两个明细区，需确认使用哪个，不能同时上传")
        title = self.DETAIL_TITLES.get(kind)
        if not title:
            raise ImplementationRequired("该类型明细区尚未确定: " + kind)
        marker = self.form_frame().get_by_text(re.compile(r"^\s*" + re.escape(title) + r"\s*$"))
        return self.expose_detail_row(marker)

    def open_export_wizard(self):
        try:
            page = self.open_archive_list()
            frame = self.list_frame()
            header = self._unique(frame.get_by_role(
                "row", name=re.compile("状态.*报关单号.*合同编号.*归集的PDF")), "列表表头行")
            header.get_by_role("checkbox").check(timeout=self.timeout)
            # 表头全选不代表全量导出，最终数据范围必须另外确认。
            buttons = frame.get_by_role("button", name="导入导出", exact=True)
            buttons.nth(1).wait_for(state="visible", timeout=self.timeout)
            if buttons.count() != 2:
                raise ImplementationRequired("导入导出按钮结构变化，需补充工具栏定位")
            self._click(buttons.nth(1), "导入导出按钮")
            items = frame.get_by_role("listitem", name="导出", exact=True)
            items.nth(1).wait_for(state="visible", timeout=self.timeout)
            if items.count() != 2:
                raise ImplementationRequired("导出菜单结构变化，需补充菜单容器")
            self._click(items.nth(1), "导出菜单")
            dialogs = page.locator('iframe[name^="layui-layer-iframe"]:visible')
            self._unique(dialogs, "可见导出弹窗iframe")
            return page.frame_locator('iframe[name^="layui-layer-iframe"]:visible')
        except PlaywrightError:
            raise SystemFailure("OA导出向导不可操作或被其他弹窗遮挡") from None

    def configure_export_fields(self, dialog, fields=EXPORT_FIELDS):
        """双列表目标区必须能确认新增字段；不把点击了箭头当作配置成功。"""
        lists = dialog.get_by_role("listbox")
        if lists.count() != 2:
            raise ImplementationRequired("请补录导出字段左右列表结构，当前不能核验已选字段")
        available, chosen = lists.nth(0), lists.nth(1)
        for field in fields:
            existing = chosen.locator("option").all_text_contents()
            if field in existing:
                continue
            available.select_option(field)
            # 唯一位置定位来自原录制；仅在双列表结构下使用并逐项验证目标变化。
            arrow = dialog.locator("i").nth(5)
            self._click(arrow, "字段添加箭头（录制第6个图标）")
            expect(chosen.locator("option")).to_contain_text(list(existing) + [field], timeout=self.timeout)
        actual = chosen.locator("option").all_text_contents()
        if actual != list(fields):
            raise BusinessPending("已选导出字段或顺序与预期不一致，请核验向导")

    def export_document_list(self, destination, *, dialog=None, configure_fields=True,
                             scope_label=None, format_label=None, confirm_selector=None):
        """按更新录制选值1并确认；代码捕获文件响应，不依赖录制生成保存动作。"""
        if Path(destination).suffix.lower() != '.xlsx':
            raise BusinessPending('导出目标文件必须为.xlsx')
        try:
            if dialog is None:
                dialog = self.open_export_wizard()
            if configure_fields:
                self.configure_export_fields(dialog)
            # 本次录制未点击范围单选项，保持当前选择，不能宣称是全量导出。
            if scope_label:
                self._unique(dialog.get_by_role("radio", name=scope_label, exact=True), "导出数据范围").check()
            combo = self._unique(dialog.get_by_role("combobox"), "导出格式")
            if format_label:
                combo.select_option(label=format_label)
            else:
                combo.select_option('1')
            page = self.open_archive_list()
            confirm = page.locator(confirm_selector or ':text-is("确定"):visible')
            from web.oa.export_capture import ExportCapture
            with ExportCapture(page, timeout=self.timeout) as capture:
                self._click(confirm, "最终下载按钮")
                return capture.save(destination)
        except PlaywrightError:
            raise SystemFailure("OA列表导出未确认完成；下轮需先处理已有弹窗，不连续点击确定") from None

    def create_form(self, declaration, run_id, attachments=()):
        """按2026-11-06录制新建：填完主表后先在当前表单上传附件，再保存一次。"""
        logger.info("OA新建表单: 单据=%s，公司=%s，附件%d个",
                    declaration.key, declaration.company, len(attachments))
        self.open_new_form()
        self.select_organization(declaration.company)
        self.fill_new_form(declaration)
        for item in attachments:
            self.fill_attachment_detail(declaration, item.kind, item.path,
                                        source_label=self.attachment_source_label(item),
                                        source_id=item.source_id, mode="new")
        self.save_form()
        return None

    def _search_list(self, number):
        """刷新列表并按报关单号筛选；使用录制的第一个搜索框和筛选按钮。"""
        self._refresh_list()
        frame = self.list_frame()
        box = self._unique(frame.get_by_role("textbox", name="搜索关键字").first, "列表搜索框")
        box.fill(str(number), timeout=self.timeout)
        self._click(frame.get_by_role("button", name="筛选", exact=True), "列表筛选按钮")
        # 列表为异步查询，点击筛选后需等待结果回写，再读取总条数与数据行。
        self.open_archive_list().wait_for_timeout(2000)
        logger.info("OA列表按报关单号筛选: %s", number)

    def _find_rows(self, declaration):
        """按报关单号筛选后定位行；返回表头、命中行、可见行和总条数。"""
        number = str(declaration.declaration_no or "").strip()
        contract = str(declaration.contract_no or "").strip()
        if not number:
            raise BusinessPending("缺少报关单号，不能在列表查重")
        self._search_list(number)
        total = self._list_total(self.list_frame())
        try:
            headers, records = self._list_snapshot()
        except ImplementationRequired:
            # 筛选后无记录时列表可能不渲染表头；只有确认0条才按无记录处理。
            if total == 0:
                return [], [], [], 0
            raise
        no_index, contract_index = headers.index("报关单号"), headers.index("合同编号")
        matches = [row for row in records
                   if row["values"][no_index] == number
                   and (not contract or row["values"][contract_index] == contract)]
        return headers, matches, records, total

    @staticmethod
    def _row_value(headers, row, name):
        return row["values"][headers.index(name)].strip()

    def _declaration_from_row(self, declaration, headers, row):
        created_at = None
        modified_at = None
        if "创建时间" in headers:
            created_at = parse_datetime(self._row_value(headers, row, "创建时间"))
        if "修改时间" in headers:
            modified_at = parse_datetime(self._row_value(headers, row, "修改时间"))
        contract_no = str(declaration.contract_no or "").strip()
        if not contract_no and "合同编号" in headers:
            contract_no = self._row_value(headers, row, "合同编号")
        collected_pdf_path = declaration.collected_pdf_path
        if "归集的PDF路径" in headers:
            collected_pdf_path = self._row_value(headers, row, "归集的PDF路径")
        collected_pdf_time = declaration.collected_pdf_time
        if "归集的PDF时间" in headers:
            collected_pdf_time = parse_datetime(self._row_value(headers, row, "归集的PDF时间"))
        return Declaration(
            company=declaration.company,
            declaration_no=str(declaration.declaration_no or "").strip(),
            export_date=declaration.export_date,
            contract_no=contract_no,
            organization_code=declaration.organization_code,
            created_by=(self._row_value(headers, row, "创建人")
                        if "创建人" in headers else ""),
            status=self._row_value(headers, row, "状态"),
            created_at=created_at,
            modified_at=modified_at,
            collected_pdf_path=collected_pdf_path,
            collected_pdf_time=collected_pdf_time,
        )

    def find_form(self, declaration):
        """按报关单号筛选回查主表是否存在；筛选结果分页不完整时禁止新建。"""
        try:
            headers, matches, records, total = self._find_rows(declaration)
        except PlaywrightError as exc:
            raise SystemFailure(f"OA单据列表查重失败，不能确认主表是否存在: {exc}") from None
        if not matches:
            if total is not None and total > len(records):
                raise BusinessPending("按报关单号筛选后仍分页，无法确认主表是否已存在；禁止新建")
            return None
        if len(matches) != 1:
            raise BusinessPending("同一报关单号+合同编号在列表命中多行，需人工确认")
        return self._declaration_from_row(declaration, headers, matches[0])

    def verify_form(self, declaration, run_id):
        """保存后回到列表按报关单号回查创建人；结果不完整时返回未知。"""
        try:
            headers, matches, records, total = self._find_rows(declaration)
        except PlaywrightError as exc:
            raise SystemFailure(f"OA单据列表回查失败，不能据此判定单据是否存在: {exc}") from None
        if not matches:
            if total is not None and total > len(records):
                return Verification.UNKNOWN, None
            return Verification.ABSENT, None
        if len(matches) != 1:
            return Verification.UNKNOWN, None
        found = self._declaration_from_row(declaration, headers, matches[0])
        # 列表可能不显示创建人；存在且单号/合同匹配即可认为保存成功，
        # 是否机器人创建由 OaSync 结合本轮创建标记和附件归集人判定。
        return Verification.COMPLETED, found

    def open_record(self, declaration):
        """按列表录制搜索报关单号并打开单据，核验打开的是目标单号和组织。"""
        number = str(declaration.declaration_no or "").strip()
        contract = str(declaration.contract_no or "").strip()
        if not number:
            raise BusinessPending("缺少报关单号，不能按列表打开单据")
        try:
            self._search_list(number)
            frame = self.list_frame()
            cell = frame.locator("div").filter(
                has_text=re.compile(r"^\s*" + re.escape(number) + r"\s*$")).first
            try:
                cell.wait_for(state="visible", timeout=self.timeout)
            except PlaywrightTimeoutError:
                raise BusinessPending("OA列表按报关单号未查到记录，无法打开单据") from None
            page = self.open_archive_list()
            try:
                # 录制中点击单号会额外打开一个窗口；单据仍加载在列表的targetFrame。
                with page.expect_popup(timeout=3000):
                    self._click(cell, "报关单号单元格")
            except PlaywrightTimeoutError:
                pass
            form = self.form_frame()
            no_box = form.locator(self.FORM_FIELDS["declaration_no"]).get_by_role("textbox")
            no_box.wait_for(state="visible", timeout=self.timeout)
            if str(no_box.input_value(timeout=self.timeout)).strip() != number:
                raise BusinessPending("打开的单据报关单号与目标不一致")
            if contract:
                contract_box = form.locator(self.FORM_FIELDS["contract_no"]).get_by_role("textbox")
                if str(contract_box.input_value(timeout=self.timeout)).strip() != contract:
                    raise BusinessPending("打开的单据合同编号与目标不一致")
            logger.info("OA已打开单据: %s", number)
            return form
        except PlaywrightError:
            raise SystemFailure("OA按报关单号打开单据失败") from None

    @staticmethod
    def _column_index(headers, name, kind):
        indices = [index for index, text in enumerate(headers) if text == name]
        if len(indices) != 1:
            raise ImplementationRequired(kind + "明细缺少唯一列: " + str(name))
        return indices[0]

    def read_attachments(self, declaration):
        """打开单据读取各明细区附件；无法确认分页完整时complete为False。"""
        self.open_record(declaration)
        items, complete = [], True
        for kind, schema in DETAIL_TABLES.items():
            table, _, headers, records = self._detail_snapshot(kind)
            attachment_index = self._column_index(headers, schema[1], kind)
            source_index = self._column_index(headers, "附件来源", kind)
            operator_index = self._column_index(headers, "归集人", kind)
            id_index = (self._column_index(headers, schema[2], kind)
                        if schema[2] and schema[2] in headers else None)
            updated_index = next((index for index, name in enumerate(headers)
                                  if name in ("附件更新时间", "BIP附件更新时间")), None)
            total = re.search(r"共\s*(\d+)\s*条", table.inner_text(timeout=self.timeout))
            if total and int(total.group(1)) > len(records):
                complete = False
            for row in records:
                values = row["values"]
                if not values[attachment_index]:
                    continue
                if id_index is None:
                    raise BusinessPending("销售发票附件未提供附件ID号列，无法生成权威快照")
                source_id, source = values[id_index], values[source_index]
                updated = values[updated_index] if updated_index is not None else ""
                if not source_id or not source or not updated:
                    complete = False
                    continue
                items.append(Attachment(kind, source, source_id, updated,
                                        robot_managed=values[operator_index] == "RPA"))
        logger.info("OA附件读取完成: %s，附件%d个，完整=%s", declaration.key, len(items), complete)
        return Snapshot(items, complete=complete, scope=declaration.key, source="OA")

    def fetch_pdf_inputs(self, declaration, snapshot, temp_dir):
        """按快照逐行点击附件下载图标，保存文件并保留源ID/版本。"""
        self.open_record(declaration)
        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        page = self.open_archive_list()
        files, editing = [], False
        for item in snapshot.items:
            if item.kind == "transport_invoice":
                raise ImplementationRequired("运输发票有两个明细区，需确认下载哪个区后再取PDF输入")
            controls = self._header_attachment_controls(item.kind, item.source_id)
            icons = controls["attachment"].locator(".icon.CAP.cap-icon-download")
            if icons.count() != 1 and not editing:
                # 录制在“修改”态下载；只读态没有图标时才进入修改，不重复点击修改。
                self.begin_attachment_edit(declaration)
                editing = True
                controls = self._header_attachment_controls(item.kind, item.source_id)
                icons = controls["attachment"].locator(".icon.CAP.cap-icon-download")
            if icons.count() != 1:
                raise ImplementationRequired("附件行下载图标不唯一: " + item.kind + "/" + item.source_id)
            with page.expect_download(timeout=self.timeout) as info:
                self._click(icons, "附件下载图标")
            download = info.value
            suffix = Path(download.suggested_filename or "").suffix
            target = temp_dir / (re.sub(r"[^\w.-]+", "_", item.kind) + "_"
                                 + re.sub(r"[^\w.-]+", "_", item.source_id) + suffix)
            download.save_as(str(target))
            if not target.is_file() or target.stat().st_size == 0:
                raise SystemFailure("OA附件下载为空: " + item.source_id)
            logger.info("OA附件下载完成: %s/%s -> %s", item.kind, item.source_id, target.name)
            files.append(replace(item, path=target))
        return files

    def assert_pdf_snapshot_current(self, declaration, snapshot):
        """提交PDF前回查状态与附件源ID/更新时间是否与快照一致。"""
        found = self.find_form(declaration)
        if not found or found.status != "确认已完成归集":
            return False
        current = self.read_attachments(declaration)
        if not current.complete:
            raise BusinessPending("提交PDF前OA附件未完整加载，不能确认快照")
        return ({item.key: item.updated_at for item in current.items}
                == {item.key: item.updated_at for item in snapshot.items})

    @staticmethod
    def attachment_source_label(attachment):
        """把收集来源映射为OA附件来源选项；未确认来源时不猜测。"""
        label = OA_SOURCE_LABELS.get(str(attachment.source or ""))
        if label is None:
            raise ImplementationRequired("附件来源未确认，无法选择OA附件来源: " + str(attachment.source))
        return label

    def upload_attachment(self, declaration, attachment):
        """打开单据进入修改，填写/上传一行后保存；保存结果由verify_attachment回查。"""
        label = self.attachment_source_label(attachment)
        if not attachment.path:
            raise BusinessPending("上传附件缺少本地文件")
        self.open_record(declaration)
        self.begin_attachment_edit(declaration)
        self.fill_attachment_detail(declaration, attachment.kind, attachment.path,
                                    source_label=label, source_id=attachment.source_id,
                                    mode="edit")
        self.save_form()
        logger.info("OA附件上传完成: %s，%s/%s", declaration.key, attachment.kind, attachment.source_id)
        return {"saved": True, "source_id": attachment.source_id}

    def verify_attachment(self, declaration, attachment):
        """打开单据按源ID回查附件行；存在即已上传，缺失为未上传。"""
        self.open_record(declaration)
        _, schema, headers, records = self._detail_snapshot(attachment.kind)
        if schema[2] is None:
            raise ImplementationRequired("销售发票附件缺少附件ID号列，无法核验上传")
        columns = column_mapping(headers, schema[1], schema[2])
        found = [row for row in records
                 if row["values"][columns["source_id"]] == str(attachment.source_id)
                 and row["values"][columns["attachment"]]]
        if not found:
            return Verification.ABSENT, None
        return (Verification.COMPLETED if len(found) == 1 else Verification.UNKNOWN), None

    def _confirm_delete(self):
        page = self.open_archive_list()
        message = page.get_by_text(re.compile(r"确认要删除")).filter(visible=True)
        if message.count() != 1:
            raise ImplementationRequired("删除确认弹窗结构与录制不符（未唯一出现“确认要删除”）")
        button = page.get_by_text("确定", exact=True).filter(visible=True)
        if button.count() != 1:
            raise ImplementationRequired("删除确认的确定按钮不唯一，需补充弹窗容器")
        self._click(button, "删除确认")

    def delete_attachment(self, declaration, attachment):
        """按录制在修改态删除机器人附件行，确认行已移除后保存并关闭。"""
        if not attachment.robot_managed:
            raise BusinessPending("只删除归集人为RPA的附件")
        self.open_record(declaration)
        self.begin_attachment_edit(declaration)
        controls = self._header_attachment_controls(attachment.kind, attachment.source_id)
        icons = controls["attachment"].locator(".icon.CAP.cap-icon-shanchu")
        if icons.count() != 1:
            raise ImplementationRequired("附件行删除图标不唯一: " + attachment.kind + "/" + attachment.source_id)
        self._click(icons, "删除附件图标")
        self._confirm_delete()
        _, schema, headers, records = self._detail_snapshot(attachment.kind)
        columns = column_mapping(headers, schema[1], schema[2])
        if any(row["values"][columns["source_id"]] == str(attachment.source_id)
               and row["values"][columns["attachment"]] for row in records):
            raise BusinessPending("删除后当前表单仍显示该附件，不能保存为已删除")
        self.save_form()
        logger.info("OA附件删除完成: %s，%s/%s", declaration.key, attachment.kind, attachment.source_id)
        return {"deleted": True, "source_id": attachment.source_id}

    def verify_deletion(self, declaration, attachment):
        """重新打开单据按源ID回查：仍存在为ABSENT，已消失为COMPLETED。"""
        try:
            self.open_record(declaration)
            _, schema, headers, records = self._detail_snapshot(attachment.kind)
        except PlaywrightError:
            raise SystemFailure("OA附件删除核验失败") from None
        if schema[2] is None:
            raise ImplementationRequired("销售发票附件缺少附件ID号列，无法核验删除")
        columns = column_mapping(headers, schema[1], schema[2])
        exists = any(row["values"][columns["source_id"]] == str(attachment.source_id)
                     and row["values"][columns["attachment"]] for row in records)
        return (Verification.ABSENT if exists else Verification.COMPLETED), None

    def _enter_modify(self, declaration):
        """按录制只点击“修改”进入编辑态，先核对打开的单据与目标一致。"""
        frame = self.form_frame()
        for key in ("declaration_no", "contract_no"):
            value = str(getattr(declaration, key) or "").strip()
            if not value:
                raise BusinessPending("修改单据缺少报关单号或合同编号")
            box = frame.locator(self.FORM_FIELDS[key]).get_by_role("textbox")
            if str(box.input_value(timeout=self.timeout)).strip() != value:
                raise BusinessPending("当前打开的单据与目标不一致，不能修改状态")
        self._click(self._unique(frame.get_by_text(re.compile(r"^\s*修改\s*$")).filter(visible=True),
                                 "修改"), "修改")

    def _set_status(self, current, target):
        """按当前状态值定位状态下拉再选新状态，避免误点第一个后缀图标。"""
        frame = self.form_frame()
        index = frame.locator("body").evaluate(STATUS_TRIGGER_JS, str(current or "").strip())
        if not isinstance(index, int) or index < 0:
            raise ImplementationRequired("未按当前状态定位到状态下拉图标，需补充状态字段定位")
        self._click(frame.locator(".CAP.suffix_icon").nth(index), "状态下拉图标")
        option = frame.get_by_text(target, exact=True).filter(visible=True)
        if option.count() != 1:
            raise ImplementationRequired("状态下拉未唯一出现选项: " + target)
        self._click(option, "状态选项: " + target)

    def upload_collected_pdf(self, declaration, pdf_path, manifest):
        """上传归集 PDF 到 OA 主表“归集的PDF”字段。

        录制尚未覆盖该字段的附件入口，未确认前不允许猜测定位。
        可通过 `OA配置.上传归集PDF` 控制是否调用本方法。
        """
        raise ImplementationRequired(
            "OA“归集的PDF”字段上传入口尚未录制，请先补充定位；"
            "或将 OA配置.上传归集PDF 设为否，只回写状态")

    def upload_pdf(self, declaration, pdf_path, merged_at, manifest):
        """按录制不上传合并PDF，只把单据状态改为“已归集为PDF”。"""
        if not Path(pdf_path).is_file():
            raise BusinessPending("合并后的PDF不存在，不能更新归集状态")
        self.open_record(declaration)
        self._enter_modify(declaration)
        if str(declaration.status or "").strip() != "已归集为PDF":
            self._set_status(declaration.status, "已归集为PDF")
        self.save_form()
        logger.info("OA单据状态已更新: %s -> 已归集为PDF（未上传PDF）", declaration.key)
        return {"status": "已归集为PDF", "path": str(pdf_path)}

    def verify_pdf(self, declaration, pdf_path, merged_at, manifest):
        """回查列表状态；PDF不上传OA，回执路径使用本地合并文件。"""
        found = self.find_form(declaration)
        if not found or found.status != "已归集为PDF":
            return Verification.ABSENT, None
        return Verification.COMPLETED, PdfReceipt(str(pdf_path), merged_at, found.status)

    def download_collected_pdf(self, declaration, destination):
        if not declaration.historical or declaration.status != '已归集为PDF':
            raise BusinessPending('历史PDF下载仅限非RPA创建且已归集为PDF的记录')
        raw = str(declaration.collected_pdf_path or '').strip()
        if not raw:
            raise BusinessPending('已归集记录的PDF路径为空；需补录OA附件入口，不能猜测下载地址')
        from urllib.parse import urljoin, urlsplit
        from utils.atomic import atomic_target
        from pypdf import PdfReader
        from io import BytesIO
        try:
            if raw.startswith(('http://', 'https://', '/')) and not raw.startswith('//'):
                page = self.open_archive_list()
                url = urljoin(page.url, raw)
                if urlsplit(url).netloc != urlsplit(page.url).netloc:
                    raise BusinessPending('归集PDF不是OA同源地址，需确认外部下载权限或共享路径')
                response = page.context.request.get(url, timeout=self.timeout, max_redirects=0)
                try:
                    if not response.ok:
                        raise BusinessPending('归集PDF下载失败，可能会话过期或需要确认跳转入口')
                    data = response.body()
                finally:
                    response.dispose()
            else:
                file = Path(raw)
                if not file.is_file():
                    raise BusinessPending('共享盘归集PDF路径不可读取')
                data = file.read_bytes()
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted or not reader.pages:
                raise BusinessPending('下载的归集PDF加密或无页面')
        except BusinessPending:
            raise
        except PlaywrightError:
            raise SystemFailure('OA历史PDF请求失败，请检查会话') from None
        except (OSError, PermissionError):
            raise SystemFailure('历史PDF共享路径读取失败') from None
        except Exception:
            raise BusinessPending('下载内容不是有效PDF，不能把登录页当附件保存') from None
        target = Path(destination)
        with atomic_target(target) as pending:
            pending.write_bytes(data)
        return target

    def list_declarations(self, params):
        raise ImplementationRequired(
            "OA导出及创建人RPA分流已接入；全量范围及公司/出口日期/主表ID映射尚未确认")


