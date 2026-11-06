"""捕获本次确认产生的文件，不猜测OA接口、不重发未知POST。"""
import time
from pathlib import Path
from urllib.parse import urlsplit
from core.errors import BusinessPending, SystemFailure
from utils.atomic import atomic_target
from data_processing.oa_export import normalize_export_bytes


class ExportCapture:
    def __init__(self, page, timeout=30000):
        self.page, self.timeout = page, timeout
        self.downloads, self.bodies, self.trace = [], [], []
        self.pages = [page]
        self.origin = urlsplit(page.url).netloc

    def _download(self, item):
        self.downloads.append(item)

    def _popup(self, page):
        self.pages.append(page)
        page.on('download', self._download)

    def _response(self, response):
        url = urlsplit(response.url)
        if url.netloc != self.origin:
            return
        try:
            if response.frame.page not in self.pages:
                return
            headers = response.headers
            mime = headers.get('content-type', '').lower()
            disposition = headers.get('content-disposition', '').lower()
            self.trace.append({'method': response.request.method, 'path': url.path,
                               'status': response.status, 'content_type': mime,
                               'attachment': bool(disposition)})
            file_response = ('attachment' in disposition or 'filename=' in disposition
                             or 'spreadsheet' in mime or 'ms-excel' in mime
                             or 'application/octet-stream' in mime
                             or (response.request.method == 'POST' and ('text/html' in mime or 'text/plain' in mime)))
            if response.ok and file_response:
                data = response.body()
                if data:
                    self.bodies.append(data)
        except Exception:
            # 下载导航的body有时不可读，继续等download事件；不输出URL参数或认证头。
            return

    def __enter__(self):
        self.page.on('download', self._download)
        self.page.context.on('page', self._popup)
        self.page.context.on('response', self._response)
        return self

    def __exit__(self, *args):
        self.page.context.remove_listener('response', self._response)
        self.page.context.remove_listener('page', self._popup)
        for page in self.pages:
            page.remove_listener('download', self._download)

    def save(self, destination):
        target = Path(destination)
        if target.suffix.lower() != '.xlsx':
            raise BusinessPending('OA导出保存路径必须为.xlsx')
        deadline = time.monotonic() + self.timeout / 1000
        last_problem = None
        while True:
            while self.downloads:
                item = self.downloads.pop(0)
                if item.failure():
                    last_problem = BusinessPending('OA浏览器下载失败')
                    continue
                try:
                    with atomic_target(target) as pending:
                        item.save_as(str(pending))
                        normalize_export_bytes(pending.read_bytes(), pending)
                except BusinessPending as exc:
                    last_problem = exc
                    continue
                return target
            while self.bodies:
                data = self.bodies.pop(0)
                try:
                    with atomic_target(target) as pending:
                        normalize_export_bytes(data, pending)
                    return target
                except BusinessPending as exc:
                    last_problem = exc
            if time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(100)
        if last_problem:
            raise BusinessPending(str(last_problem))
        raise SystemFailure('确定后未收到文件下载或Excel响应，请检查OA导出任务是否完成')
