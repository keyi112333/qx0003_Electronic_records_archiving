"""一卡通（合同附件）HTTP接口客户端。

一卡通文件全部通过接口获取，无页面操作。接口说明见
``docx/合同附件接口使用说明.docx``。

签名规则：``sign = md5(key + ts + rand + json)``，四段按顺序直接拼接，
不加分隔符；json/ts/rand 使用 URL 解码后的原文；MD5 取 UTF-8 字节，
结果为 32 位小写十六进制。必须先签名、再对参数做 URL 编码。

查询按 ``no``（合同号）+ ``time``（上传时间下限）过滤，返回 ``data`` 列表。
下载建议直接使用返回的 ``url``，不用 ``withfile``。
"""
import hashlib
import json
import random
import time as _time
from urllib import request, error
from urllib.parse import urlencode

from core.errors import ConfigError, SystemFailure

# 使用说明给出的默认接口地址与密钥（实际以「一卡通配置」为准）。
DEFAULT_API_URL = "http://a.uptron.tech/hetong/api"
DEFAULT_KEY = "iguwhzidv5"

_USER_AGENT = "yikatong-api/1.0"


def _dump(obj):
    """紧凑序列化中文不转义，保证签名与发送的json逐字节一致。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _ts():
    """14位时间戳（YYYYMMDDHHmmss）。"""
    return _time.strftime("%Y%m%d%H%M%S")


def _rand():
    return "%012x" % random.getrandbits(48)


def _sign(key, ts, rand, json_str):
    raw = f"{key}{ts}{rand}{json_str}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _get(base_url, payload, timeout):
    encoded = urlencode(payload)
    req = request.Request(f"{base_url}?{encoded}",
                          headers={"Accept": "*/*", "User-Agent": _USER_AGENT}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except error.HTTPError as exc:
        raise SystemFailure(f"一卡通接口HTTP错误 {exc.code}") from None
    except error.URLError as exc:
        raise SystemFailure(f"一卡通接口网络错误: {exc.reason}") from None


def query(contract_no, time_lower, base_url=None, key=None, timeout=30):
    """按合同号 + 时间下限查询附件，返回 ``data`` 原始列表（每项含 id/name/url/time/filetype/no）。"""
    if not str(time_lower or "").strip():
        raise ConfigError("一卡通配置.时间下限不能为空")
    base_url = (base_url or DEFAULT_API_URL).rstrip("/")
    key = key or DEFAULT_KEY
    json_str = _dump({
        "no": str(contract_no or ""),
        "filetype": "",
        "id": "",
        "time": str(time_lower),
        "state": "",
        "busystate": "",
        "dbname": "",
        "withfile": "0",
    })
    ts, rand = _ts(), _rand()
    payload = {"json": json_str, "ts": ts, "rand": rand,
               "sign": _sign(key, ts, rand, json_str)}
    body = _get(base_url, payload, timeout)
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise SystemFailure("一卡通接口返回非JSON内容") from None
    code = obj.get("code")
    if code == "ok":
        data = obj.get("data") or []
        if not isinstance(data, list):
            raise SystemFailure("一卡通接口data不是列表")
        return data
    if code == "signerr":
        raise ConfigError("一卡通接口签名校验失败，请核对密钥")
    if code == "paramerr":
        raise ConfigError("一卡通接口参数错误: " + str(obj.get("message", "")))
    raise SystemFailure("一卡通接口返回未知code: " + str(code))


def download_bytes(url, timeout=60):
    """按查询结果中的 url 下载文件字节。"""
    if not str(url or "").strip():
        raise SystemFailure("一卡通附件缺少下载地址")
    req = request.Request(str(url), headers={"Accept": "*/*", "User-Agent": _USER_AGENT}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except error.HTTPError as exc:
        raise SystemFailure(f"一卡通文件下载HTTP错误 {exc.code}") from None
    except error.URLError as exc:
        raise SystemFailure(f"一卡通文件下载网络错误: {exc.reason}") from None