"""一卡通（合同附件）通过HTTP接口获取文件，无页面操作。

list_records 按合同号 + 配置的时间下限查询接口；download 按返回的 url 落盘；
snapshot 重新查询生成权威源快照，供 M08 对账增删。
"""
import hashlib
import re
from pathlib import Path

from core.errors import BusinessPending
from web.qx0003.adapters import SourceAdapter
from web.yikatong import api
from workflows.qx0003_archive.models import Attachment, Snapshot, normalize_type


def _safe_part(value):
    """把接口字段清洗为可作文件名的一段：路径非法字符与空白统一替换为下划线。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(value or "").strip()).strip("_")


def _safe_filename(record):
    """下载文件名：no + filetype + 时间戳，保留原扩展名。"""
    name = "_".join(p for p in (
        _safe_part(record.get("no")),
        _safe_part(record.get("filetype")),
        _safe_part(record.get("time")),
    ) if p)
    ext = str(record.get("ext") or "").strip().lstrip(".")
    if not ext:
        m = re.search(r"\.([A-Za-z0-9]+)$", str(record.get("name") or "").strip())
        ext = m.group(1) if m else ""
    if not name:
        name = "附件"
    if ext and not name.lower().endswith("." + ext.lower()):
        name = f"{name}.{ext}"
    return name


class YikatongAdapter(SourceAdapter):
    name = "一卡通"
    api_based = True  # 纯接口，不启动浏览器

    def __init__(self, config=None, browser=None):
        super().__init__(browser)
        self.config = config

    def _time_lower(self):
        return self.config.require("一卡通配置.时间下限")

    def _api(self):
        return self.config.require("一卡通配置.接口地址"), self.config.require("一卡通配置.密钥")

    def _to_attachment(self, record, path=None, content_hash=""):
        source_id = str(record.get("id") or "").strip()
        if not source_id:
            raise BusinessPending("一卡通附件缺少源ID")
        return Attachment(
            kind=normalize_type(str(record.get("filetype") or "")),
            source=self.name,
            source_id=source_id,
            updated_at=str(record.get("time") or ""),
            path=path,
            robot_managed=True,
            content_hash=content_hash,
        )

    def list_records(self, declaration, time_lower=None):
        contract_no = str(declaration.contract_no or "").strip()
        if not contract_no:
            raise BusinessPending("一卡通查询缺少合同编号，不能按合同号定位附件")
        base_url, key = self._api()
        return api.query(contract_no, time_lower or self._time_lower(),
                         base_url=base_url, key=key)

    def download(self, declaration, records, temp_dir):
        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for record in records:
            data = api.download_bytes(record.get("url"))
            if not data:
                raise BusinessPending("一卡通下载附件为空: " + str(record.get("id")))
            path = temp_dir / _safe_filename(record)
            path.write_bytes(data)
            files.append(self._to_attachment(record, path, hashlib.sha256(data).hexdigest()))
        return files

    def snapshot(self, declaration):
        items = [self._to_attachment(r) for r in self.list_records(declaration)]
        return Snapshot(items, complete=True, scope=declaration.key, source=self.name)