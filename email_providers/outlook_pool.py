"""outlookEmail 池 provider（对接 https://github.com/assast/outlookEmail）。

自建 outlookEmail 服务维护 Outlook 账号池与项目（project）状态机：
claim-random 领取 → 注册 → complete-success / complete-failed / release 回报。
注册用 Outlook 别名地址（plus 地址 `local+tag@domain`），服务端收信接口
自动把别名解析回主邮箱，无需预先在服务端登记别名。
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import string
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

DEFAULT_LEASE_SECONDS = 1800
DEFAULT_POLL_INTERVAL = 4
DEFAULT_RESEND_INTERVAL = 60
_ALIAS_TAG_ALPHABET = string.ascii_lowercase + string.digits
ALIAS_MODES = ("plus", "configured", "primary")
_SINCE_SKEW_SECONDS = 120  # 服务端/本地时钟偏移容忍

HttpGet = Callable[..., Any]
HttpPost = Callable[..., Any]


class OutlookPoolError(RuntimeError):
    pass


def normalize_base(base_url: str = "") -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise OutlookPoolError("outlook_pool_api_base 未配置")
    if "://" not in base:
        raise OutlookPoolError("outlook_pool_api_base 必须是 http(s) URL")
    return base


def build_headers(api_key: str = "") -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    key = str(api_key or "").strip()
    if key:
        headers["X-API-Key"] = key
    return headers


def _safe_message(message: object) -> str:
    """响应报错可能回显请求头/密钥，统一脱敏。"""
    text = str(message or "")
    text = re.sub(r"(X-API-Key\s*[:=]\s*)\S+", r"\1<redacted>", text, flags=re.IGNORECASE)
    return text[:240]


def _extract_error(body: Any, status: int) -> str:
    if isinstance(body, dict):
        for key in ("error", "message"):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("code") or value
            if value:
                return _safe_message(value)
    return f"HTTP {status}"


def random_tag(length: int = 10) -> str:
    return "".join(secrets.choice(_ALIAS_TAG_ALPHABET) for _ in range(length))


def build_alias_address(
    email: str,
    mode: str = "plus",
    tag: str = "",
    aliases: Optional[List[str]] = None,
) -> str:
    """按模式生成注册用地址。

    plus:       local+tag@domain（Outlook 原生投递到主邮箱）
    configured: 使用服务端登记的别名；没有可用别名时回退 plus
    primary:    原地址
    """
    addr = str(email or "").strip()
    if not addr or "@" not in addr:
        return addr
    resolved_mode = str(mode or "plus").strip().lower()
    if resolved_mode == "primary":
        return addr
    if resolved_mode == "configured":
        for alias in aliases or []:
            alias = str(alias or "").strip()
            if alias and "@" in alias:
                return alias
    local, _, domain = addr.partition("@")
    return f"{local}+{tag or random_tag()}@{domain}"


def claim_mailbox(
    http_post: HttpPost,
    base_url: str,
    api_key: str,
    project_key: str,
    *,
    caller_id: str = "grok-worker",
    task_id: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Dict[str, Any]:
    """从项目邮箱池随机领取一个待注册邮箱（toClaim → claiming）。"""
    base = normalize_base(base_url)
    project = str(project_key or "").strip()
    if not project:
        raise OutlookPoolError("outlook_pool_project 未配置")
    url = f"{base}/api/external/projects/{quote(project, safe='')}/claim-random"
    payload: Dict[str, Any] = {
        "caller_id": str(caller_id or "grok-worker"),
        # 部署版服务强制要求 task_id；未提供时自动生成
        "task_id": str(task_id or "").strip()
        or f"task-{int(time.time())}-{secrets.token_hex(3)}",
        "lease_seconds": max(60, int(lease_seconds or DEFAULT_LEASE_SECONDS)),
    }
    resp = None
    body = None
    # 服务偶发 502/超时（单 worker Gunicorn），退避重试；确定性错误（404/403/池空）不重试
    last_error = ""
    for attempt in range(3):
        try:
            resp = http_post(url, json=payload, headers=build_headers(api_key), timeout=25)
        except Exception as exc:
            last_error = f"outlookEmail 服务请求失败: {_safe_message(exc)}"
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise OutlookPoolError(last_error) from exc
        try:
            body = resp.json()
        except Exception:
            body = None
        if resp.status_code == 404:
            raise OutlookPoolError(f"outlookEmail 项目不存在: {project}")
        if resp.status_code in (401, 403):
            raise OutlookPoolError(
                f"outlookEmail API Key 无权限（HTTP {resp.status_code}，领取需要 readwrite 权限）"
            )
        if resp.status_code >= 500 and attempt < 2:
            last_error = f"outlookEmail 领取失败 HTTP {resp.status_code}"
            time.sleep(3 * (attempt + 1))
            continue
        break
    if resp is None:
        raise OutlookPoolError(last_error or "outlookEmail 领取失败")
    if resp.status_code >= 400:
        raise OutlookPoolError(f"outlookEmail 领取失败: {_extract_error(body, resp.status_code)}")
    if not isinstance(body, dict) or not body.get("success"):
        raise OutlookPoolError(
            f"outlookEmail 池子没有可领取邮箱: {_extract_error(body, resp.status_code)}"
        )
    data = body.get("data") or {}
    claim = {
        "account_id": data.get("account_id"),
        "email": str(data.get("email") or "").strip(),
        "claim_token": str(data.get("claim_token") or ""),
        "lease_expires_at": str(data.get("lease_expires_at") or ""),
        "project_key": project,
    }
    if not claim["email"] or not claim["account_id"]:
        raise OutlookPoolError("outlookEmail 领取响应缺少 account_id/email")
    return claim


def complete_claim(
    http_post: HttpPost,
    base_url: str,
    api_key: str,
    project_key: str,
    *,
    account_id: Any,
    claim_token: str,
    outcome: str,
    caller_id: str = "grok-worker",
    detail: str = "",
) -> Dict[str, Any]:
    """回报项目状态机：success / failed / release。

    网络或状态冲突（如租约已过期）不抛异常，返回 {ok, message} 由调用方记日志。
    """
    base = normalize_base(base_url)
    project = str(project_key or "").strip()
    endpoint = {
        "success": "complete-success",
        "failed": "complete-failed",
        "release": "release",
    }.get(str(outcome or "").strip().lower())
    if not endpoint:
        return {"ok": False, "message": f"未知回报类型: {outcome}"}
    url = f"{base}/api/external/projects/{quote(project, safe='')}/{endpoint}"
    payload: Dict[str, Any] = {
        "account_id": account_id,
        "claim_token": str(claim_token or ""),
        "caller_id": str(caller_id or "grok-worker"),
    }
    if detail:
        payload["detail"] = str(detail)[:300]
    message = ""
    for attempt in range(3):
        try:
            resp = http_post(url, json=payload, headers=build_headers(api_key), timeout=20)
        except Exception as exc:
            message = f"outlookEmail 回报失败: {_safe_message(exc)}"
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            return {"ok": False, "message": message}
        try:
            body = resp.json()
        except Exception:
            body = None
        if resp.status_code >= 500 and attempt < 2:
            message = f"outlookEmail 回报 HTTP {resp.status_code}"
            time.sleep(3 * (attempt + 1))
            continue
        if resp.status_code >= 400 or not (isinstance(body, dict) and body.get("success")):
            return {"ok": False, "message": _extract_error(body, resp.status_code)}
        return {"ok": True, "message": ""}
    return {"ok": False, "message": message}


def fetch_emails(
    http_get: HttpGet,
    base_url: str,
    api_key: str,
    email: str,
    *,
    folder: str = "all",
    top: int = 20,
) -> List[Dict[str, Any]]:
    """按主邮箱或别名拉邮件（服务端自动解析别名与 + 回退）。"""
    base = normalize_base(base_url)
    addr = quote(str(email or "").strip(), safe="")  # `+`/`@` 都编码，文档建议 %2B
    if not addr:
        return []
    url = (
        f"{base}/api/external/emails?email={addr}"
        f"&folder={quote(str(folder or 'all'), safe='')}&top={max(1, min(int(top or 20), 50))}"
    )
    resp = http_get(url, headers=build_headers(api_key), timeout=25)
    if resp.status_code in (401, 403):
        raise OutlookPoolError(f"outlookEmail API Key 无权限（HTTP {resp.status_code}）")
    if resp.status_code >= 400:
        raise OutlookPoolError(f"outlookEmail 拉取邮件失败 HTTP {resp.status_code}")
    body = resp.json() if hasattr(resp, "json") else {}
    emails = body.get("emails") if isinstance(body, dict) else None
    return [item for item in (emails or []) if isinstance(item, dict)]


def _parse_email_date(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def fetch_account_aliases(http_get: HttpGet, base_url: str, api_key: str) -> Dict[str, List[str]]:
    """全量账号的主邮箱 → 别名映射（configured 模式用；服务端最多几千条，一次拉完）。"""
    base = normalize_base(base_url)
    url = f"{base}/api/external/accounts?limit=10000"
    resp = http_get(url, headers=build_headers(api_key), timeout=30)
    if resp.status_code >= 400:
        raise OutlookPoolError(f"outlookEmail 账号列表拉取失败 HTTP {resp.status_code}")
    body = resp.json() if hasattr(resp, "json") else {}
    mapping: Dict[str, List[str]] = {}
    for account in (body.get("accounts") or []) if isinstance(body, dict) else []:
        email = str(account.get("email") or "").strip().lower()
        aliases = [str(a or "").strip() for a in (account.get("aliases") or []) if str(a or "").strip()]
        if email and aliases:
            mapping[email] = aliases
    return mapping


# ---------------------------------------------------------------------------
# 领取状态登记：email_used（注册用地址）→ claim 上下文。
# dev_token 里也带同样信息，用于跨函数传递；这里的映射供 release/结果回报钩子使用。
# ---------------------------------------------------------------------------
_CLAIMS_LOCK = threading.Lock()
_CLAIMS: Dict[str, Dict[str, Any]] = {}


def remember_claim(email_used: str, claim: Dict[str, Any]) -> None:
    addr = str(email_used or "").strip().lower()
    if not addr:
        return
    with _CLAIMS_LOCK:
        _CLAIMS[addr] = dict(claim or {})


def pop_claim(email_used: str) -> Optional[Dict[str, Any]]:
    addr = str(email_used or "").strip().lower()
    with _CLAIMS_LOCK:
        return _CLAIMS.pop(addr, None) or None


def peek_claim(email_used: str) -> Optional[Dict[str, Any]]:
    addr = str(email_used or "").strip().lower()
    with _CLAIMS_LOCK:
        claim = _CLAIMS.get(addr)
        return dict(claim) if claim else None


def pack_claim(claim: Dict[str, Any], email_used: str, alias_mode: str) -> str:
    payload = {
        "account_id": claim.get("account_id"),
        "claim_token": claim.get("claim_token") or "",
        "primary_email": claim.get("email") or "",
        "project_key": claim.get("project_key") or "",
        "email_used": str(email_used or ""),
        "alias_mode": str(alias_mode or "plus"),
        "claimed_at": time.time(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def unpack_claim(dev_token: Any) -> Optional[Dict[str, Any]]:
    try:
        raw = base64.urlsafe_b64decode(str(dev_token or "").encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def wait_for_code(
    http_get: HttpGet,
    base_url: str,
    api_key: str,
    dev_token: str,
    email: str = "",
    *,
    timeout: int = 240,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    extract_code: Optional[Callable[[str, str], Optional[str]]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    raise_if_cancelled: Optional[Callable[[Any], None]] = None,
    sleep_with_cancel: Optional[Callable[[float, Any], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    resend_callback: Optional[Callable[[], None]] = None,
    resend_interval: int = DEFAULT_RESEND_INTERVAL,
) -> str:
    """轮询 outlookEmail 服务收信接口，提取 xAI 验证码。

    只认领取之后到达的邮件（claimed_at - 120s），避免把池内旧邮件当验证码。
    """
    claim = unpack_claim(dev_token) or {}
    target_email = str(email or claim.get("email_used") or claim.get("primary_email") or "")
    if not target_email:
        raise OutlookPoolError("缺少收信地址，无法拉取验证码")
    base = normalize_base(base_url)
    key = str(api_key or "").strip()
    since_ts = float(claim.get("claimed_at") or 0.0) - _SINCE_SKEW_SECONDS
    deadline = time.time() + max(30, int(timeout or 0))
    interval = max(2, int(poll_interval or DEFAULT_POLL_INTERVAL))
    next_resend_at = time.time() + max(20, int(resend_interval or DEFAULT_RESEND_INTERVAL))
    seen_ids: set = set()

    def _log(message: str) -> None:
        if log_callback:
            log_callback(message)

    while time.time() < deadline:
        if raise_if_cancelled:
            raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                _log("[*] 已触发重新发送验证码")
            except Exception as exc:
                _log(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + max(20, int(resend_interval or DEFAULT_RESEND_INTERVAL))
        try:
            mails = fetch_emails(http_get, base, key, target_email, folder="all", top=20)
        except Exception as exc:
            _log(f"[Debug] outlookEmail 拉取邮件失败: {_safe_message(exc)}")
            if sleep_with_cancel:
                sleep_with_cancel(interval, cancel_callback)
            continue
        fresh = 0
        for mail in mails:
            mail_id = str(mail.get("id") or f"{mail.get('date')}-{mail.get('subject')}")
            if mail_id in seen_ids:
                continue
            seen_ids.add(mail_id)
            mail_ts = _parse_email_date(mail.get("date"))
            if since_ts and mail_ts and mail_ts < since_ts:
                continue  # 领取前的旧邮件
            fresh += 1
            subject = str(mail.get("subject") or "")
            body = str(mail.get("body_preview") or mail.get("body") or "")
            code = extract_code(subject, body) if extract_code else None
            if code:
                _log(f"[*] 收到验证码邮件: {subject[:60]}")
                return code
        if fresh and log_callback:
            _log(f"[Debug] outlookEmail 本轮新邮件 {fresh} 封，尚未匹配到验证码")
        if sleep_with_cancel:
            sleep_with_cancel(interval, cancel_callback)
    raise OutlookPoolError(f"outlookEmail 验证码等待超时（{int(max(30, int(timeout or 0)))}s）")
