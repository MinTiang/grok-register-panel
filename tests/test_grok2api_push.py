# -*- coding: utf-8 -*-
"""grok2api 推送模块单测：导入文档、SSE 解析、multipart 推送、设置存储。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webui import grok2api_push  # noqa: E402
from webui import push_settings_store as store  # noqa: E402


class FakeResp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body


def test_build_import_document():
    doc = grok2api_push.build_import_document("user@outlook.com", "sso=eyJabc.def.ghi")
    assert doc["provider"] == "grok_web"
    entry = doc["accounts"][0]
    assert entry["email"] == "user@outlook.com"
    assert entry["sso_token"] == "eyJabc.def.ghi"  # sso= 前缀剥离
    assert entry["tier"] == "auto"
    try:
        grok2api_push.build_import_document("user@outlook.com", "  ")
        raise AssertionError("空 sso 应报错")
    except grok2api_push.Grok2ApiPushError:
        pass


def test_parse_import_sse():
    sse = (
        ": connected\n"
        "event: progress\ndata: {\"completed\":0,\"total\":1,\"phase\":\"importing\"}\n"
        "\n"
        "event: skipped\ndata: {}\n"
        "event: failed\ndata: {\"reason\": \"x\"}\n"
        "event: complete\ndata: {\"created\":1,\"updated\":0,\"skipped\":1,\"failed\":1,\"synced\":0,\"syncFailed\":1}\n"
    )
    result = grok2api_push.parse_import_sse(sse)
    assert result["created"] == 1 and result["skipped"] == 1
    assert result["failed"] == 1 and result["syncFailed"] == 1
    assert result["ok"] is True

    err = grok2api_push.parse_import_sse(
        "event: error\ndata: {\"code\":\"authImportFailed\",\"message\":\"导入账号失败\"}\n"
    )
    assert err["ok"] is False and "导入账号失败" in err["message"]


def test_push_sso_end_to_end():
    calls = []

    def post(url, headers=None, timeout=None, proxies=None, impersonate=None, **kwargs):
        calls.append({"url": url, "headers": headers or {}, "kwargs": kwargs})
        if url.endswith("/auth/login"):
            return FakeResp(200, {"data": {"tokens": {"accessToken": "eyJlogin.jwt.token"}}})
        return FakeResp(200, text=(
            "event: progress\ndata: {\"completed\":1,\"total\":1,\"phase\":\"syncing\"}\n"
            "event: complete\ndata: {\"created\":1,\"updated\":0,\"skipped\":0,\"failed\":0,\"synced\":0,\"syncFailed\":0}\n"
        ))

    result = grok2api_push.push_sso(
        post, "https://grok2api.example.com/", "admin", "pw",
        "user@outlook.com", "sso=eyJabc.def.ghi", proxy="",
    )
    assert result["ok"] is True and result["created"] == 1
    login_call, import_call = calls[0], calls[1]
    assert login_call["url"].endswith("/api/admin/v1/auth/login")
    assert import_call["url"].endswith("/api/admin/v1/accounts/web/import")
    assert import_call["headers"]["Authorization"] == "Bearer eyJlogin.jwt.token"
    ctype = import_call["headers"]["Content-Type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=", 1)[1]
    body = import_call["kwargs"]["data"]
    assert f'name="files"; filename="grok-web-sso.json"'.encode() in body
    import json as _json
    part = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
    doc = _json.loads(part.decode("utf-8"))
    assert doc["provider"] == "grok_web"
    assert doc["accounts"][0]["sso_token"] == "eyJabc.def.ghi"

    def readonly_post(url, **kwargs):
        return FakeResp(403, {"error": "readonly"})

    try:
        grok2api_push.push_sso(
            readonly_post, "https://grok2api.example.com", "admin", "pw",
            "u@o.com", "tok",
        )
        raise AssertionError("403 应当抛错")
    except grok2api_push.Grok2ApiPushError as exc:
        assert "403" in str(exc)


def test_push_settings_store_roundtrip():
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        store.CONFIG_PATH = Path(temp) / "config.json"
        store.LOCK_PATH = Path(temp) / "config.json.lock"
        result = store.save_push_settings({
            "grok2api_push_enabled": True,
            "grok2api_push_url": "https://grok2api.example.com/",
            "grok2api_push_username": "admin",
            "grok2api_push_password": "secret-pw",
            "grok2api_push_only_clean": True,
        })
        assert result["values"]["grok2api_push_enabled"] is True
        assert result["values"]["grok2api_push_password"] == ""  # 不回显
        assert result["password_configured"] is True
        assert result["enabled"] is True

        # 留空密码 = 保留
        store.save_push_settings({"grok2api_push_password": ""})
        assert store.read_push_settings()["password_configured"] is True

        # 布尔字符串解析
        store.save_push_settings({"grok2api_push_enabled": "off"})
        assert store.read_push_settings()["values"]["grok2api_push_enabled"] is False
        assert store.is_push_enabled() is False

        # 非法 URL 被拒
        try:
            store.save_push_settings({"grok2api_push_url": "ftp://x"})
            raise AssertionError("非法 URL 应被拒")
        except store.PushSettingsError:
            pass

        # 未知字段被拒
        try:
            store.save_push_settings({"unknown_field": 1})
            raise AssertionError("未知字段应被拒")
        except store.PushSettingsError:
            pass


def test_login_token_cache_reuse():
    calls = []

    def post(url, headers=None, timeout=None, proxies=None, impersonate=None, **kwargs):
        calls.append(url)
        if url.endswith("/auth/login"):
            return FakeResp(200, {"data": {"tokens": {"accessToken": "eyJcached.jwt.token"}}})
        return FakeResp(200, text=(
            "event: complete\ndata: {\"created\":1,\"updated\":0,\"skipped\":0,\"failed\":0,\"synced\":0,\"syncFailed\":0}\n"
        ))

    for _ in range(3):
        result = grok2api_push.push_sso(
            post, "https://cache.example.com", "admin", "pw",
            "u1@outlook.com", "tok1", proxy="",
        )
        assert result["ok"] is True
    logins = [c for c in calls if c.endswith("/auth/login")]
    imports = [c for c in calls if c.endswith("/accounts/web/import")]
    assert len(logins) == 1 and len(imports) == 3  # 一次登录服务三个号

    # 换服务地址/凭据则重新登录
    grok2api_push.push_sso(
        post, "https://other.example.com", "admin", "pw", "u2@outlook.com", "tok2", proxy="",
    )
    logins = [c for c in calls if c.endswith("/auth/login")]
    assert len(logins) == 2


def test_registration_wiring_present():
    ttk = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    assert "_grok2api_push_if_enabled" in ttk
    assert "grok2api_push_enabled" in ttk
    recovery = (ROOT / "sso_to_auth_json.py").read_text(encoding="utf-8")
    assert "is_push_enabled(config)" in recovery
    monitor = (ROOT / "webui/monitor.py").read_text(encoding="utf-8")
    assert "/api/push-settings" in monitor
    example = (ROOT / "config.example.json").read_text(encoding="utf-8")
    assert "grok2api_push_enabled" in example



def test_push_decoupled_from_cpa_conversion():
    """推送必须在 CPA token 转换之前触发，且不再挂在 add_sso_to_cpa 内部。"""
    ttk = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    assert "def push_sso_to_grok2api(" in ttk
    # 三处注册成功点都独立调用
    assert ttk.count("push_sso_to_grok2api(sso, email=email") >= 3
    # add_sso_to_cpa 内部不应再有旧触发（避免重复推送）
    body = ttk.split("def add_sso_to_cpa(", 1)[1].split("def ", 1)[0]
    assert "_grok2api_push_if_enabled(" not in body
    # 推送台账
    assert "_g2a_push_ledger_mark(" in ttk


def test_recovery_push_independent_of_token():
    src = (ROOT / "sso_to_auth_json.py").read_text(encoding="utf-8")
    assert "_push_sso_independent(" in src
    loop = src.split("for i, record in enumerate(records, 1):", 1)[1]
    first_push = loop.find("_push_sso_independent(")
    first_state = loop.find("inspect_sso_account_state(", first_push)
    assert first_push >= 0 and first_state > first_push, "推送应在风控/token 转换之前"


def test_backfill_script_exists():
    path = ROOT / "scripts/push_existing_sso.py"
    assert path.is_file()
    src = path.read_text(encoding="utf-8")
    assert "load_sso_accounts" in src and "--dry-run" in src


if __name__ == "__main__":
    test_build_import_document()
    test_parse_import_sse()
    test_push_sso_end_to_end()
    test_push_settings_store_roundtrip()
    test_login_token_cache_reuse()
    test_registration_wiring_present()
    test_push_decoupled_from_cpa_conversion()
    test_recovery_push_independent_of_token()
    test_backfill_script_exists()
    print("OK grok2api push")
