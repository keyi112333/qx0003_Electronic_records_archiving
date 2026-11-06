#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行结果通知：企微机器人与 SMTP 邮件。"""
import json
import mimetypes
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib import request

from config.credentials import get_secret
from core.errors import ConfigError
from log.loggingConfig import logger
from workflows.qx0003_archive.models import STATUS_PDF

PROJECT_NAME = "电子档案管理（单证归集与打印）"


def parse_recipients(value) -> Dict[str, List[str]]:
    """解析 组织=邮箱1;邮箱2|组织2=... 格式。"""
    text = str(value or "").strip()
    if not text:
        return {}
    result: Dict[str, List[str]] = {}
    for block in re.split(r"[|\n]+", text):
        block = block.strip()
        if not block or "=" not in block:
            continue
        org, emails = block.split("=", 1)
        org = org.strip()
        addresses = [item.strip() for item in re.split(r"[;,，；\s]+", emails) if item.strip()]
        if org and addresses:
            result[org] = addresses
    return result


class WeComNotifier:
    """企微群机器人。"""

    def __init__(self, config):
        self.config = config

    def _webhook(self) -> Optional[str]:
        reference = self.config.get("通知配置.企微Webhook凭据引用")
        if not reference:
            return None
        try:
            secret = get_secret(str(reference)).strip()
        except ConfigError as exc:
            logger.warning("企微凭据不可用，跳过通知: %s", exc)
            return None
        if not secret:
            return None
        if secret.startswith("http://") or secret.startswith("https://"):
            return secret
        return f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={secret}"

    def send(self, content: str) -> bool:
        webhook = self._webhook()
        if not webhook:
            logger.info("未配置企微Webhook，跳过通知")
            return False
        payload = json.dumps({"msgtype": "text", "text": {"content": content}},
                             ensure_ascii=False).encode("utf-8")
        req = request.Request(webhook, data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=15) as response:
                body = response.read().decode("utf-8", "replace")
            logger.info("企微通知已发送: %s", body[:200])
            return True
        except Exception as exc:
            logger.warning("企微通知发送失败: %s", exc)
            return False


class EmailNotifier:
    """SMTP 邮件通知。"""

    def __init__(self, config):
        self.config = config
        self.recipients = parse_recipients(config.get("通知配置.组织收件人映射"))

    def _connection(self):
        host = self.config.get("通知配置.SMTP主机")
        if not host:
            return None
        port = self.config.get_int("通知配置.SMTP端口", 25)
        mode = str(self.config.get("通知配置.SMTP加密方式") or "").strip().upper()
        if "SSL" in mode:
            server = smtplib.SMTP_SSL(str(host), port, timeout=20)
        else:
            server = smtplib.SMTP(str(host), port, timeout=20)
            if "TLS" in mode or "STARTTLS" in mode:
                server.starttls()
        username = self.config.get("通知配置.SMTP用户名")
        reference = self.config.get("通知配置.SMTP凭据引用")
        if username and reference:
            password = get_secret(str(reference))
            server.login(str(username), password)
        return server

    def send(self, subject: str, body: str, attachments: Iterable[Path],
             recipients: Iterable[str]) -> bool:
        addresses = [item for item in recipients if item]
        if not addresses:
            return False
        sender = self.config.get("通知配置.发件人") or self.config.get("通知配置.SMTP用户名")
        if not sender:
            logger.warning("未配置发件人，跳过邮件")
            return False
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = str(sender)
        message["To"] = ", ".join(addresses)
        message.set_content(body)
        for path in attachments:
            path = Path(path)
            if not path.is_file():
                continue
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            main_type, sub_type = content_type.split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=main_type,
                                   subtype=sub_type, filename=path.name)
        try:
            server = self._connection()
            if server is None:
                logger.info("未配置SMTP主机，跳过邮件")
                return False
            try:
                server.send_message(message)
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
            logger.info("邮件已发送: %s -> %s", subject, addresses)
            return True
        except Exception as exc:
            logger.warning("邮件发送失败: %s", exc)
            return False


class Notifier:
    """通知入口，任何通知失败都不影响业务结果。"""

    def __init__(self, config, project_name: str = PROJECT_NAME):
        self.config = config
        self.project_name = project_name
        self.wecom = WeComNotifier(config)
        self.email = EmailNotifier(config)

    def notify_start(self, run_id: str, params_summary: str) -> None:
        self.wecom.send(f"QX0003 {self.project_name}开始运行，run_id={run_id}，{params_summary}")

    def notify_error(self, stage: str, declaration: str, error: Exception,
                     screenshot: str = "") -> None:
        content = f"运行异常：{stage} {declaration} {type(error).__name__} {error}"
        if screenshot:
            content += f"；截图：{screenshot}"
        self.wecom.send(content)

    def notify_end(self, items, workbooks_by_org: Dict[str, List[Path]]) -> None:
        total = len(items)
        pdf_count = sum(1 for item in items if self._has_pdf(item))
        failed = sum(1 for item in items if item.status == "失败")
        manual = sum(1 for item in items if item.status == "待人工")
        summary = (f"本次共处理{total}个报关单的附件；{pdf_count}个报关单附件已归集成PDF，"
                   f"已可以打印；失败{failed}条，待人工{manual}条。")
        self.wecom.send(summary)

        for org, paths in workbooks_by_org.items():
            recipients = self.email.recipients.get(org) or []
            if not recipients:
                logger.info("组织未配置收件人，跳过邮件: %s", org)
                continue
            org_total = sum(1 for item in items if item.declaration.company == org)
            org_pdf = sum(1 for item in items
                          if item.declaration.company == org and self._has_pdf(item))
            body_lines = [
                "各位好，请查收电子档案管理（单证归集与打印）运行结果，谢谢！",
                f"本次共处理{org}{org_total}个报关单的附件；{org_pdf}个报关单附件已归集成PDF，"
                f"已可以打印，具体请查看《电子档案管理（单证归集与打印）运行结果表》。",
            ]
            years = sorted({path.parent.name for path in paths if path.parent.name})
            if len(years) > 1:
                body_lines.append(
                    f"本次涉及{'、'.join(years)}共{len(years)}个年度，{len(paths)}份结果表，请分别查收。")
            subject = f"{org}{self.project_name}运行结果-{datetime.now():%Y%m%d}"
            self.email.send(subject, "\n".join(body_lines), paths, recipients)

    @staticmethod
    def _has_pdf(item) -> bool:
        if item.pdf:
            return True
        declaration = item.declaration
        return (str(declaration.status or "").strip() == STATUS_PDF
                and bool(str(declaration.collected_pdf_path or "").strip()))
