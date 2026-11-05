"""grok2api 推送设置的安全存储（写 config.json 的 grok2api_push_* 字段）。

与 email_provider_store 同一份 config.json / 同一把锁：
- GET 脱敏：password 不回显，只回 password_configured
- POST：secret 留空保留原值；布尔字段接受 true/false/1/0
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from secure_files import atomic_write_json, exclusive_file_lock
from webui.security_utils import redact_log_line


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(
    os.environ.get("EMAIL_PROVIDER_CONFIG_FILE", str(ROOT / "config.json"))
)
LOCK_PATH = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".lock")

SECRET_FIELD = "grok2api_push_password"
BOOL_FIELDS = ("grok2api_push_enabled", "grok2api_push_only_clean")
URL_FIELD = "grok2api_push_url"
ALL_FIELDS = (
    "grok2api_push_enabled",
    URL_FIELD,
    "grok2api_push_username",
    SECRET_FIELD,
    "grok2api_push_only_clean",
)
MAX_VALUE_LENGTH = 2048


class PushSettingsError(ValueError):
    pass


def _read_unlocked() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import json

        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise RuntimeError(f"config.json 无法读取: {redact_log_line(str(exc))[:200]}") from exc


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _normalize_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PushSettingsError("grok2api_push_url 必须是有效的 HTTP 或 HTTPS URL")
    if parsed.username or parsed.password:
        raise PushSettingsError("grok2api_push_url 中不能包含账号密码")
    if len(text) > MAX_VALUE_LENGTH:
        raise PushSettingsError("grok2api_push_url 过长")
    return text


def _string(value: object) -> str:
    text = str(value or "").strip()
    if any(ord(ch) < 32 and ch != "\t" for ch in text):
        raise PushSettingsError("配置值包含非法控制字符")
    if len(text) > MAX_VALUE_LENGTH:
        raise PushSettingsError("配置值过长")
    return text


def is_push_enabled(raw: dict | None = None) -> bool:
    """供注册/补录流程读取的开关判断。"""
    try:
        raw = raw if raw is not None else _read_unlocked()
    except Exception:
        return False
    if not _bool(raw.get("grok2api_push_enabled")):
        return False
    if not _normalize_url(raw.get("grok2api_push_url")):
        return False
    return bool(str(raw.get("grok2api_push_username") or "").strip()) and bool(
        str(raw.get("grok2api_push_password") or "").strip()
    )


def push_only_clean(raw: dict | None = None) -> bool:
    try:
        raw = raw if raw is not None else _read_unlocked()
    except Exception:
        return False
    return _bool(raw.get("grok2api_push_only_clean"))


def read_push_settings() -> dict:
    with exclusive_file_lock(LOCK_PATH):
        raw = _read_unlocked()
    values = {
        "grok2api_push_enabled": _bool(raw.get("grok2api_push_enabled")),
        "grok2api_push_url": str(raw.get("grok2api_push_url") or ""),
        "grok2api_push_username": str(raw.get("grok2api_push_username") or ""),
        "grok2api_push_password": "",
        "grok2api_push_only_clean": _bool(raw.get("grok2api_push_only_clean")),
    }
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = None
    return {
        "ok": True,
        "error": None,
        "values": values,
        "password_configured": bool(str(raw.get(SECRET_FIELD) or "").strip()),
        "enabled": is_push_enabled(raw),
        "config_exists": CONFIG_PATH.exists(),
        "mtime": mtime,
    }


def save_push_settings(settings: object) -> dict:
    if not isinstance(settings, dict):
        raise PushSettingsError("settings 必须是 JSON 对象")
    unknown = sorted(set(settings) - set(ALL_FIELDS))
    if unknown:
        raise PushSettingsError(f"包含不支持的配置字段: {unknown[0]}")
    with exclusive_file_lock(LOCK_PATH):
        raw = _read_unlocked()
        updated = dict(raw)
        for name in ALL_FIELDS:
            value = settings.get(name)
            if name == SECRET_FIELD:
                # 密码留空 = 保留原值；只有显式传字符串才会更新，传 null/缺省不动
                if value is None:
                    continue
                text = _string(value)
                if text:
                    updated[name] = text
                continue
            if name in BOOL_FIELDS:
                if value is None:
                    continue
                updated[name] = _bool(value)
                continue
            if value is None:
                continue
            if name == URL_FIELD:
                updated[name] = _normalize_url(value)
            else:
                updated[name] = _string(value)
        atomic_write_json(CONFIG_PATH, updated)
    result = read_push_settings()
    return result
