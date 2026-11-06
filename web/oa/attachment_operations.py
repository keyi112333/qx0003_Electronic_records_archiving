"""新增/修改共用的附件明细编辑；只准备表单，不代表主表已保存。"""
import re
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, expect
from core.errors import BusinessPending, ImplementationRequired
from workflows.qx0003_archive.models import normalize_type
from web.oa.detail_tables import DetailTables, DETAIL_TABLES


class AttachmentOperations(DetailTables):
    def _attachment_picker(self, control):
        """format可能只包展示值，上传图标在同一td内的相邻节点中。"""
        selector = '.cap-icon-fujian:visible, .cap4-attach__picker > .icon:visible'
        container = control
        icons = container.locator(selector)
        if icons.count() == 0:
            container = control.locator('xpath=ancestor::td[1]')
            if container.count() != 1:
                raise ImplementationRequired('附件字段内无上传图标，且无法唯一确定所属单元格')
            icons = container.locator(selector)
        if icons.count() != 1:
            raise ImplementationRequired('目标附件单元格中可见上传图标数量为' + str(icons.count())
                                         + '，请补充当前行DOM；不能改用页面第一个图标')
        return container, icons

    def begin_attachment_edit(self, declaration):
        """目标单据已打开时进入修改；搜索关键字不能证明选中了唯一目标。"""
        if not declaration.robot_owned:
            raise BusinessPending('仅允许修改RPA创建单据的源附件')
        self._check_attachment_form(declaration)
        frame = self.form_frame()
        self._click(frame.get_by_text(re.compile(r'^\s*基本信息\s*$')), '基本信息')
        self._click(frame.get_by_text(re.compile(r'^\s*修改\s*$')), '修改单据')

    def _check_attachment_form(self, declaration):
        if not declaration.declaration_no or not declaration.contract_no:
            raise BusinessPending('附件操作缺少报关单号或合同编号')
        for key in ('declaration_no', 'contract_no'):
            box = self._unique(self.form_frame().locator(self.FORM_FIELDS[key]).get_by_role('textbox'), key)
            expect(box).to_have_value(str(getattr(declaration, key)), timeout=self.timeout)

    def fill_attachment_detail(self, declaration, kind, path, *, source_label, source_id,
                               mode='new', detail_row=None, extra_fields=None, progress=None):
        """在已打开且可编辑的表单中上传并填写一行，不保存主表。

        mode=new用于未保存的新建表单；edit要求RPA归属并先进入修改。
        默认按表头确定列，按源附件ID确定行；无匹配时只使用唯一空行。
        source_label为OA选项（共享盘读取的文件仍可标注原业务来源电子口岸）。
        source_id对应各区“附件ID号”列，不自动映射文件hash。
        """
        if kind not in DETAIL_TABLES:
            kind = normalize_type(kind)
        if kind not in DETAIL_TABLES:
            raise ImplementationRequired('请补充该附件类型的明细区：' + kind)
        if mode not in ('new', 'edit'):
            raise BusinessPending('附件编辑模式必须为new或edit')
        if mode == 'edit' and not declaration.robot_owned:
            raise BusinessPending('仅处理机器人新增单据的源附件')
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            raise BusinessPending('待上传附件不存在或为空')
        if not source_label:
            raise BusinessPending('请选择附件来源')
        if mode == 'edit' and not source_id:
            raise BusinessPending('修改已有附件必须提供附件ID；新建表单可留空')
        extra_fields = extra_fields or {}
        allowed_extras = set()
        if kind == '合同':
            allowed_extras.add('field0088')
        if kind == '销售发票':
            allowed_extras.update({'报关单号', '出库单号', '出口单号', '发票单号', 'BIP附件创建时间'})
        if set(extra_fields) - allowed_extras:
            raise ImplementationRequired('存在未确认的额外字段，不能猜测列映射')
        stage = '核对报关单号和合同编号'
        def report(message):
            nonlocal stage
            stage = message
            if progress:
                progress('附件操作：' + message)

        upload_started = False
        try:
            report(stage)
            self._check_attachment_form(declaration)
            report('定位' + kind + '明细区域')
            controls = self._header_attachment_controls(kind, source_id, detail_row)
            extras = {}
            if kind == '合同':
                value = extra_fields.get('field0088', declaration.contract_no)
                if str(value) != str(declaration.contract_no):
                    raise BusinessPending('合同明细合同编号须与主表一致')
                extras['合同编号'] = (controls['contract_no'], value)
            elif kind == '销售发票':
                for label, value in extra_fields.items():
                    if label not in controls:
                        raise ImplementationRequired('销售发票明细缺少列: ' + label)
                    extras[label] = (controls[label], value)
            # 空单元格可能没有可点击面积，不能对整格执行trial点击来判断字段可用。
            # 实际点击时由_click滚动并等待具体控件可操作。
            # 不覆盖已有附件；替换/删除必须走独立的来源和版本核验流程。
            report('定位附件上传图标')
            attachment_container, picker = self._attachment_picker(controls['attachment'])
            if attachment_container.get_by_text(path.name, exact=True).count():
                raise BusinessPending('该行已有同名附件，请先核验上传状态，避免重复上传')
            report('点击附件上传图标')
            self._click(picker, kind + '附件选择')
            page = self.open_archive_list()
            selector = 'iframe[name^="layui-layer-iframe"]:visible'
            report('等待附件上传弹窗iframe')
            self._unique(page.locator(selector), '附件上传弹窗')
            dialog = page.frame_locator(selector)
            report('设置上传文件')
            file_input = dialog.locator('input[type="file"]')
            # 原生文件input经常被OA样式隐藏，set_input_files只要求元素已挂载。
            file_input.first.wait_for(state='attached', timeout=self.timeout)
            if file_input.count() != 1:
                raise ImplementationRequired('上传文件输入框匹配不唯一')
            upload_started = True
            file_input.set_input_files(
                str(path.resolve()), timeout=self.timeout)
            report('确认附件上传')
            self._click(dialog.get_by_text('确定', exact=True), '附件弹窗确定')
            expect(page.locator(selector)).to_have_count(0, timeout=self.timeout)
            report('核验附件文件名回显')
            expect(attachment_container).to_contain_text(path.name, timeout=self.timeout)
            report('选择附件来源')
            self._click(controls['source'], '附件来源单元格')
            self._click(self.form_frame().get_by_text(str(source_label), exact=True).filter(visible=True), '附件来源选项')
            self._expect_cell_value(controls['source'], str(source_label))
            text_fields = [('归集人', controls['operator'], 'RPA')]
            if source_id:
                text_fields.insert(0, ('附件ID', controls['source_id'], source_id))
            text_fields.extend((key, control, value) for key, (control, value) in extras.items())
            for label, control, value in text_fields:
                report('填写' + label)
                self._fill_attachment_text(control, str(value), label)
        except (PlaywrightError, AssertionError) as exc:
            # 上传可能已发生，不能作为可直接重放的系统错误。
            reason = str(exc).split('Call log:')[0].strip().splitlines()
            detail = reason[0][:240] if reason else type(exc).__name__
            state = '文件可能已上传，请核验当前行，勿重复上传' if upload_started else '尚未设置上传文件'
            raise BusinessPending('附件操作失败步骤：' + stage + '；' + state + '；' + detail) from None
        return {'kind': kind, 'filename': path.name, 'saved': False}

    def _fill_attachment_text(self, control, value, label):
        # CAP浮动textarea与单元格input可能同时存在。先结束前一格的编辑，
        # 否则覆盖层会拦截下一格点击，或把RPA写回上一格。
        floating = self.form_frame().locator('textarea:visible')
        floating.evaluate_all('(elements) => elements.forEach(el => el.blur())')
        control.scroll_into_view_if_needed(timeout=self.timeout)
        inputs = control.locator('input:visible:not([type="hidden"]), textarea:visible')
        # 按原录制先点input激活CAP编辑，不把镜像input的editable误认为已激活。
        target = inputs if inputs.count() == 1 else control.locator('.td-content:visible')
        if target.count() != 1:
            target = control
        self._click(target, '附件' + label + '输入控件')
        floating = self.form_frame().locator('textarea:visible')
        if floating.count() == 1:
            editor = floating
        else:
            editor = control.locator('input:visible:not([readonly]), textarea:visible')
        editor = self._unique(editor, '附件' + label + '编辑框')
        editor.fill(str(value), timeout=self.timeout)
        editor.press('Tab')
        # Tab可能只移动焦点；显式触发正常blur以提交CAP数据模型，随后核验原单元格。
        control.evaluate('cell => { const active = cell.ownerDocument.activeElement; '
                         'if (active && (cell.contains(active) || active.tagName === "TEXTAREA")) active.blur(); }')
        self._expect_cell_value(control, str(value))

    def fill_existing_attachment_information(self, declaration, kind, *, source_id, progress=None):
        """按已有附件ID只补填文本信息；不打开附件弹窗，不新增文件。"""
        if declaration.historical:
            raise BusinessPending('不维护人工历史单据的源附件')
        if not source_id:
            raise BusinessPending('补填信息必须提供已有附件ID')
        self._check_attachment_form(declaration)
        from web.oa.detail_tables import column_mapping
        table, schema, headers, records = self._detail_snapshot(kind)
        if schema[2] is None:
            raise ImplementationRequired('销售发票需单独确认填写规则')
        columns = column_mapping(headers, schema[1], schema[2])
        matches = [r for r in records if r['values'][columns['source_id']] == str(source_id)]
        if len(matches) != 1 or not matches[0]['values'][columns['attachment']]:
            raise BusinessPending('未按附件ID找到唯一已有附件行；不能补填空行或猜测目标')
        if matches[0]['values'][columns['operator']] not in ('', 'RPA'):
            raise BusinessPending('目标附件由非RPA归集，不自动修改')
        row = table.locator('tr').nth(matches[0]['index'])
        controls = self._header_attachment_controls(kind, source_id, row)
        for label, control, value in [
            ('归集人', controls['operator'], 'RPA'),
            *(([('合同编号', controls['contract_no'], declaration.contract_no)]) if kind == '合同' else []),
        ]:
            if progress:
                progress('附件信息补填：' + label + '（不上传文件）')
            try:
                self._fill_attachment_text(control, value, label)
            except (PlaywrightError, AssertionError) as exc:
                reason = str(exc).split('Call log:')[0].strip().splitlines()
                raise BusinessPending('补填' + label + '未完成；未上传文件；' + (reason[0][:240] if reason else type(exc).__name__)) from None
        return {'saved': False, 'uploaded': False}

    def _expect_cell_value(self, control, value):
        inputs = control.locator('input:visible:not([type="hidden"]), textarea:visible')
        if inputs.count() == 1:
            expect(inputs).to_have_value(value, timeout=self.timeout)
        else:
            expect(control).to_contain_text(value, timeout=self.timeout)
