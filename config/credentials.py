#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 凭据读取。

配置表只保存凭据引用名；实际账号密码从 Windows 凭据管理器读取。
开发环境可在凭据管理器不可用时通过环境变量覆盖：
    QX0003_CRED_<引用名>_USERNAME
    QX0003_CRED_<引用名>_PASSWORD
    QX0003_CRED_<引用名>            （单值密钥，如 Webhook）
"""
import os
import re
from dataclasses import dataclass
from typing import Optional

from core.errors import ConfigError
from log.loggingConfig import logger

try:
    import win32cred
    HAS_WIN32CRED = True
except ImportError:
    HAS_WIN32CRED = False


@dataclass
class Credential:
    username: str
    password: str


def _env_name(reference: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", str(reference or "")).strip("_").upper()


def _from_env(reference: str) -> Optional[Credential]:
    name = _env_name(reference)
    if not name:
        return None
    username = os.environ.get(f"QX0003_CRED_{name}_USERNAME", "")
    password = os.environ.get(f"QX0003_CRED_{name}_PASSWORD", "")
    single = os.environ.get(f"QX0003_CRED_{name}", "")
    if username or password:
        return Credential(username, password)
    if single:
        return Credential("", single)
    return None


def get_credential(reference: str) -> Credential:
    """按引用名读取账号密码。"""
    reference = str(reference or "").strip()
    if not reference:
        raise ConfigError("凭据引用名为空")
    if HAS_WIN32CRED:
        try:
            raw = win32cred.CredRead(reference, win32cred.CRED_TYPE_GENERIC)
            blob = raw.get("CredentialBlob") or b""
            if isinstance(blob, bytes):
                password = blob.decode("utf-16-le", "ignore")
            else:
                password = str(blob)
            credential = Credential(str(raw.get("UserName") or ""), password)
            if credential.username or credential.password:
                return credential
        except Exception as exc:
            logger.debug("Windows凭据读取失败，尝试环境变量: %s", exc)
    credential = _from_env(reference)
    if credential:
        return credential
    raise ConfigError(f"凭据不可用: {reference}（请在Windows凭据管理器创建通用凭据）")


def get_secret(reference: str) -> str:
    """按引用名读取单值密钥；优先密码字段，其次用户名。"""
    credential = get_credential(reference)
    return credential.password or credential.username
