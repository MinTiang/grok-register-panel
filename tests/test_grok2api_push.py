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
        "event: created\ndata: {\"id\": 1}\n"
        "\n"
        "event: updated\ndata: {\"id\": 2}\n"
        "\n"
        "event: skipped\ndata: {}\n"
        "event: failed\ndata: {\"reason\": \"x\"}\n"
        "event: done\ndata: {\"message\": \"ok\"}\n"
    )
    result = grok2api_push.parse_import_sse(sse)
    assert result["created"] == 1 and result["updated"] == 1
    assert result["skipped"] == 1 and result["failed"] == 1
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
            "event: created\ndata: {}\n"
            "event: done\ndata: {}\n"
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
    files = import_call["kwargs"]["files"]
    assert list(files) == ["files"]
    filename, payload, ctype = files["files"]
    assert filename.endswith(".json") and ctype == "application/json"
    doc = __import__("json").loads(payload.decode("utf-8"))
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


if __name__ == "__main__":
    test_build_import_document()
    test_parse_import_sse()
    test_push_sso_end_to_end()
    test_push_settings_store_roundtrip()
    test_registration_wiring_present()
    print("OK grok2api push")
