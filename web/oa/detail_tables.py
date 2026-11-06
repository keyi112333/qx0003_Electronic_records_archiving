"""按录制确认的明细区容器和表头定位；不使用动态字段号或动态行ID。"""
from core.errors import BusinessPending, ImplementationRequired


# 容器ID来自2026-10-20录制；运行时还会检查该区特有的附件列名。
DETAIL_TABLES = {
    '报关单': (1, '报关单附件', '报关单附件ID号'),
    '合同': (2, '合同附件', '合同附件ID号'),
    '放行通知单': (3, '放行通知单附件', '放行单附件ID号'),
    '提单': (4, '提单附件', '提单附件ID号'),
    '销售发票': (6, '销售发票附件', '发票附件ID'),
    '电子委托协议': (15, '电子委托协议', '电子委托协议附件ID号'),
    '发票': (14, '发票', '发票附件ID号'),
    '装箱单': (13, '装箱单', '发票附件ID号'),
    '保税核注清单': (12, '报税核注清单', '报税核注清单附件ID号'),
    '代理报关发票': (11, '代理报关发票', '代理报关发票附件ID号'),
    '国内运输发票': (10, '国内运输发票', '国内运输发票附件ID号'),
    'CIF保险单': (9, 'CIF保险单', 'CIF保险单附件ID号'),
    '国内运费形式发票': (8, '国内运费形式发票', '国内运费形式发票附件ID号'),
    '一卡通其他附件': (5, '一卡通其他附件', '一卡通其他附件ID号'),
    '其他附件': (7, '其他附件', '其他附件ID号'),
}


def column_mapping(headers, attachment, source_id):
    names = {'attachment': attachment, 'source': '附件来源', 'operator': '归集人'}
    if source_id:
        names['source_id'] = source_id
    result = {}
    for key, name in names.items():
        indices = [i for i, text in enumerate(headers) if text == name]
        if len(indices) != 1:
            raise ImplementationRequired('明细列不唯一或不存在：' + name)
        result[key] = indices[0]
    return result


def select_detail_record(records, columns, source_id):
    matches = [r for r in records if r['values'][columns['source_id']] == str(source_id)] if source_id else []
    if len(matches) > 1:
        raise BusinessPending('同一附件ID命中多行，禁止自动选择')
    if matches:
        return matches[0]
    empty = [r for r in records if all(not r['values'][index] for index in columns.values())]
    if len(empty) != 1:
        raise BusinessPending('未找到唯一空附件行；请在对应明细区手动新增一行后再试')
    return empty[0]


# 只读取当前DOM；不触发点击，不读取框架内部API。保留每个单元格的实际列索引。
TABLE_SNAPSHOT = """root => Array.from(root.querySelectorAll('tr')).map((row, index) => {
  const cells = Array.from(row.children).filter(e => ['TD','TH'].includes(e.tagName));
  return {index, visible: !!row.getClientRects().length,
    merged: cells.some(c => c.colSpan !== 1 || c.rowSpan !== 1),
    values: cells.map(c => {
      const inputs = Array.from(c.querySelectorAll('input:not([type=hidden]),textarea,select'));
      const input = inputs.find(e => e.tagName !== 'INPUT' || !['checkbox','radio','file','button'].includes(e.type));
      const text = input ? (input.tagName === 'SELECT' ? Array.from(input.selectedOptions).map(o=>o.text).join(' ') : input.value) : c.innerText;
      return (text || '').replace(/\\s+/g,' ').trim();
    })};
})"""


class DetailTables:
    def _detail_snapshot(self, kind):
        if kind == '报税核注清单':
            kind = '保税核注清单'
        if kind == 'transport_invoice':
            raise ImplementationRequired('请明确选择国内运输发票或国内运费形式发票所在区，不能同时上传')
        schema = DETAIL_TABLES.get(kind)
        if not schema:
            raise ImplementationRequired('未知明细区：' + kind)
        table = self._unique(self.form_frame().locator('#tableName-front_formson_' + str(schema[0])), kind + '明细区')
        table.scroll_into_view_if_needed(timeout=self.timeout)
        rows = table.evaluate(TABLE_SNAPSHOT)
        candidates = [r for r in rows if r['visible'] and schema[1] in r['values']]
        # 表头必须包含至少两个录制列，避免将数据中的同名附件视作表头。
        candidates = [r for r in candidates
                      if '附件更新时间' in r['values']
                      or 'BIP附件更新时间' in r['values']
                      or 'BIP附件创建时间' in r['values']]
        if len(candidates) != 1 or candidates[0]['merged']:
            raise ImplementationRequired('明细表头不唯一或包含跨行跨列，需补充当前表格结构：' + kind)
        header = candidates[0]
        records = [r for r in rows if r['visible'] and r['index'] > header['index']
                   and len(r['values']) == len(header['values']) and not r['merged']]
        return table, schema, header['values'], records

    def read_attachment_details(self, kind):
        """读取对应标题区当前已加载行，包括只读详情；不声称分页范围完整。"""
        _, _, headers, records = self._detail_snapshot(kind)
        if len(set(headers)) != len(headers):
            raise ImplementationRequired('明细表头重复，不能可靠读取列值')
        return {'kind': kind, 'headers': headers,
                'rows': [dict(zip(headers, row['values'])) for row in records],
                'complete': False, 'scope': '当前打开单据对应明细区的已加载可见数据'}

    def _header_attachment_controls(self, kind, source_id, detail_row=None):
        table, schema, headers, records = self._detail_snapshot(kind)
        if schema[2] is None:
            raise ImplementationRequired('销售发票的BIP创建/更新时间及业务字段与通用附件不同，需补充填写规则；可先读取')
        columns = column_mapping(headers, schema[1], schema[2])
        if detail_row is None:
            record = select_detail_record(records, columns, source_id)
            operator = record['values'][columns['operator']]
            if operator and operator != 'RPA':
                raise BusinessPending('目标附件行由非RPA归集，不自动维护')
            row = table.locator('tr').nth(record['index'])
        else:
            row = self._unique(detail_row, '指定附件行')
        cells = row.locator(':scope > td, :scope > th')
        if cells.count() != len(headers):
            raise BusinessPending('附件行列数与表头不一致，请重新读取')
        controls = {key: cells.nth(index) for key, index in columns.items()}
        # 保留表头到单元格的完整映射，供销售发票等特殊字段补填使用。
        for name in headers:
            if name and name not in controls:
                controls[name] = cells.nth(headers.index(name))
        if kind == '合同':
            if headers.count('合同编号') != 1:
                raise ImplementationRequired('合同明细缺少唯一合同编号列')
            controls['contract_no'] = cells.nth(headers.index('合同编号'))
        return controls
