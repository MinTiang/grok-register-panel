"""grok2api SSO 推送（可开关）。

对接 chenyme/grok2api（含 grok2api-egress-enhancements fork）管理 API，
把注册/补录拿到的 grok.com SSO cookie 以 Grok Web 账号形式推送：

  POST {base}/api/admin/v1/auth/login            {username,password} → accessToken
  POST {base}/api/admin/v1/accounts/web/import   multipart `files` 字段（SSE 响应）

导入文档格式（服务端 backend/internal/infra/provider/web/import.go）：
  {"provider": "grok_web", "accounts": [{"email": ..., "sso_token": ..., "tier": "auto"}]}
SSE 事件：created / updated / skipped / failed / error / done。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Dict, Optional

DEFAULT_TIMEOUT = 30
IMPORT_TIMEOUT = 60  # web/import 是 SSE 流，syncing 阶段可达 30s+
_TOKEN_TTL_SECONDS = 14 * 60  # accessToken 有效期 15 分钟，提前 1 分钟复用


class Grok2ApiPushError(RuntimeError):
    pass


def normalize_base(base_url: str = "") -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise Grok2ApiPushError("grok2api_push_url 为空")
    if "://" not in base:
        raise Grok2ApiPushError("grok2api_push_url 必须是 http(s) URL")
    return base


def _redact(text: Any) -> str:
    """日志脱敏：吞掉 JWT / 密码形态的片段。"""
    value = str(text or "")
    value = re.sub(r"(Authorization\s*[:=]\s*Bearer\s+)\S+", r"\1<redacted>", value, flags=re.IGNORECASE)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b", "<jwt>", value)
    return value[:300]


def build_import_document(email: str, sso_token: str) -> Dict[str, Any]:
    addr = str(email or "").strip()
    token = str(sso_token or "").strip().removeprefix("sso=")
    if not token:
        raise Grok2ApiPushError("sso cookie 为空，无法推送")
    return {
        "provider": "grok_web",
        "accounts": [
            {
                "email": addr,
                "sso_token": token,
                "tier": "auto",
            }
        ],
    }


def login(http_post: Any, base_url: str, username: str, password: str, *, proxy: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    base = normalize_base(base_url)
    user = str(username or "").strip()
    pwd = str(password or "")
    if not user or not pwd:
        raise Grok2ApiPushError("grok2api_push_username / password 未配置")
    token = _cached_token(base, user, pwd)
    if token:
        return token
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last_error = ""
    for attempt in range(3):
        try:
            resp = http_post(
                f"{base}/api/admin/v1/auth/login",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"username": user, "password": pwd}).encode("utf-8"),
                timeout=timeout,
                proxies=proxies,
                impersonate="chrome",
            )
        except Exception as exc:
            last_error = f"grok2api 管理登录请求失败: {_redact(exc)}"
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise Grok2ApiPushError(last_error) from exc
        if resp.status_code in (401, 403):
            raise Grok2ApiPushError(f"grok2api 管理登录被拒 HTTP {resp.status_code}")
        if resp.status_code >= 500 and attempt < 2:
            last_error = f"grok2api 管理登录 HTTP {resp.status_code}"
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            raise Grok2ApiPushError(f"grok2api 管理登录失败 HTTP {resp.status_code}")
        try:
            body = resp.json()
        except Exception as exc:
            raise Grok2ApiPushError(f"grok2api 登录响应无法解析: {_redact(exc)}") from exc
        token = str(((body.get("data") or {}).get("tokens") or {}).get("accessToken") or "")
        if not token:
            raise Grok2ApiPushError("grok2api 登录响应缺少 accessToken")
        _store_token(base, user, pwd, token)
        return token
    raise Grok2ApiPushError(last_error or "grok2api 管理登录失败")


# accessToken（15 分钟有效）跨号复用：同一批次多个成功号共享一次登录
_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE: Dict[str, Any] = {"key": "", "token": "", "expires": 0.0}


def _token_cache_key(base: str, user: str, pwd: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{base}|{user}|{pwd}".encode("utf-8")).hexdigest()[:24]
    return digest


def _cached_token(base: str, user: str, pwd: str) -> str:
    key = _token_cache_key(base, user, pwd)
    with _TOKEN_CACHE_LOCK:
        if (
            _TOKEN_CACHE.get("key") == key
            and time.time() < float(_TOKEN_CACHE.get("expires") or 0)
        ):
            return str(_TOKEN_CACHE.get("token") or "")
    return ""


def _store_token(base: str, user: str, pwd: str, token: str) -> None:
    key = _token_cache_key(base, user, pwd)
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE["key"] = key
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires"] = time.time() + _TOKEN_TTL_SECONDS


def parse_import_sse(text: str) -> Dict[str, Any]:
    """解析 web/import 的 SSE 响应。

    服务端事件流：progress（阶段进度）→ complete（data 内含
    created/updated/skipped/failed/synced/syncFailed 汇总）→ 或 error。
    """
    result = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "synced": 0,
        "syncFailed": 0,
        "ok": False,
        "message": "",
    }
    event = ""
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        try:
            payload = json.loads(data) if data else {}
        except Exception:
            payload = {}
        if event == "error":
            result["ok"] = False
            result["message"] = _redact(
                (payload.get("message") or payload.get("error") or payload.get("code") or "导入失败")
            )
        elif event == "complete":
            for key in ("created", "updated", "skipped", "failed", "synced", "syncFailed"):
                result[key] = int(payload.get(key) or 0)
            result["ok"] = True
        elif event == "done":
            message = payload.get("message") or payload
            if message and not result["message"]:
                result["message"] = _redact(message)
    return result


def push_sso(
    http_post: Any,
    base_url: str,
    username: str,
    password: str,
    email: str,
    sso_token: str,
    *,
    proxy: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """推送单个号的 SSO 到 grok2api 的 Grok Web 账号池。

    返回 {ok, created, updated, skipped, failed, message}；网络/认证异常抛
    Grok2ApiPushError（调用方决定是否重试/记日志）。
    """
    import uuid

    base = normalize_base(base_url)
    document = build_import_document(email, sso_token)
    token = login(http_post, base, username, password, proxy=proxy, timeout=timeout)
    filename = "grok-web-sso.json"
    payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    # 手工构造 multipart/form-data：requests 与 curl_cffi 的上传参数名不同
    # （files= / multipart=），标准编码对两者都兼容
    boundary = "----grokpanel" + uuid.uuid4().hex
    body = b"".join(
        [
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\n"
                "Content-Type: application/json\r\n\r\n"
            ).encode("utf-8"),
            payload,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    result: Optional[Dict[str, Any]] = None
    last_error = ""
    # 导入是幂等的（服务端按 sso token 哈希去重），超时/网络错误可安全重试一次
    for attempt in range(2):
        try:
            resp = http_post(
                f"{base}/api/admin/v1/accounts/web/import",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                data=body,
                timeout=max(timeout, IMPORT_TIMEOUT),
                proxies={"http": proxy, "https": proxy} if proxy else None,
                impersonate="chrome",
            )
        except Exception as exc:
            last_error = f"grok2api 导入请求失败: {_redact(exc)}"
            if attempt < 1:
                time.sleep(3)
                continue
            raise Grok2ApiPushError(last_error) from exc
        if resp.status_code in (401, 403):
            raise Grok2ApiPushError(f"grok2api 管理会话无效 HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise Grok2ApiPushError(f"grok2api 导入请求失败 HTTP {resp.status_code}: {_redact(resp.text)}")
        result = parse_import_sse(resp.text or "")
        break
    if result is None:
        raise Grok2ApiPushError(last_error or "grok2api 导入失败")
    if result["ok"] and not result["message"] and result.get("syncFailed"):
        result["message"] = f"web 池已入池，console 同步失败 {result['syncFailed']}（不影响使用）"
    return result
