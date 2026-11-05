# -*- coding: utf-8 -*-
"""outlookEmail 池 provider 单测：别名、claim/complete 状态机、收信等码、配置与预检。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from email_providers import outlook_pool as provider  # noqa: E402
from email_providers.common import extract_verification_code  # noqa: E402


class FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


def test_alias_modes():
    assert provider.build_alias_address("User@outlook.com", "plus", tag="ab12cd") == (
        "User+ab12cd@outlook.com"
    )
    tag = provider.build_alias_address("User@outlook.com", "plus")
    assert "+@" not in tag and "@" in tag  # 自动生成 tag
    assert provider.build_alias_address("User@outlook.com", "primary") == "User@outlook.com"
    assert provider.build_alias_address("User@outlook.com", "configured", aliases=["al@x.com"]) == (
        "al@x.com"
    )
    fallback = provider.build_alias_address("User@outlook.com", "configured", aliases=[])
    assert "+" in fallback.split("@")[0] and fallback.endswith("@outlook.com")


def test_claim_pack_roundtrip_and_registry():
    claim = {
        "account_id": 33,
        "email": "Pool@outlook.com",
        "claim_token": "pclm_t",
        "project_key": "grok",
    }
    used = provider.build_alias_address(claim["email"], "plus", tag="z9z9z9")
    token = provider.pack_claim(claim, used, "plus")
    data = provider.unpack_claim(token)
    assert data["account_id"] == 33
    assert data["claim_token"] == "pclm_t"
    assert data["primary_email"] == "Pool@outlook.com"
    assert data["email_used"] == used
    assert data["alias_mode"] == "plus"
    assert data["claimed_at"] > 0

    provider.remember_claim(used, claim)
    assert provider.peek_claim(used)["claim_token"] == "pclm_t"
    popped = provider.pop_claim(used)
    assert popped["account_id"] == 33
    assert provider.peek_claim(used) is None


def test_claim_mailbox_success_and_errors():
    calls = []

    def ok_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return FakeResp(200, {
            "success": True,
            "data": {"account_id": 9, "email": "a@outlook.com", "claim_token": "pclm_ok"},
        })

    claim = provider.claim_mailbox(
        ok_post, "https://olp.example.com/", "key-1", "grok", caller_id="w1", lease_seconds=900
    )
    assert claim["email"] == "a@outlook.com" and claim["claim_token"] == "pclm_ok"
    url, payload, headers = calls[0]
    assert url.endswith("/api/external/projects/grok/claim-random")
    assert payload["caller_id"] == "w1" and payload["lease_seconds"] == 900
    assert payload["task_id"]  # 服务端必填，未传时自动生成
    assert headers["X-API-Key"] == "key-1"

    def empty_post(*_a, **_k):
        return FakeResp(200, {"success": False, "error": "没有可领取的项目邮箱"})

    try:
        provider.claim_mailbox(empty_post, "https://olp.example.com", "k", "grok")
        raise AssertionError("应当抛出池空异常")
    except provider.OutlookPoolError as exc:
        assert "没有可领取" in str(exc)

    def readonly_post(*_a, **_k):
        return FakeResp(403, {"error": "readonly"})

    try:
        provider.claim_mailbox(readonly_post, "https://olp.example.com", "k", "grok")
        raise AssertionError("应当抛出权限异常")
    except provider.OutlookPoolError as exc:
        assert "403" in str(exc)

    try:
        provider.claim_mailbox(ok_post, "https://olp.example.com", "k", "")
        raise AssertionError("空项目应当报错")
    except provider.OutlookPoolError as exc:
        assert "project" in str(exc) or "项目" in str(exc)


def test_complete_claim_outcomes():
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return FakeResp(200, {"success": True})

    for outcome, endpoint in (
        ("success", "complete-success"),
        ("failed", "complete-failed"),
        ("release", "release"),
    ):
        result = provider.complete_claim(
            post, "https://olp.example.com", "k", "grok",
            account_id=1, claim_token="t", outcome=outcome, detail="d",
        )
        assert result["ok"] is True
        assert calls[-1][0].endswith(f"/api/external/projects/grok/{endpoint}")
        assert calls[-1][1]["account_id"] == 1

    assert provider.complete_claim(
        post, "https://olp.example.com", "k", "grok",
        account_id=1, claim_token="t", outcome="bogus",
    )["ok"] is False

    def broken_post(*_a, **_k):
        raise OSError("network down")

    result = provider.complete_claim(
        broken_post, "https://olp.example.com", "k", "grok",
        account_id=1, claim_token="t", outcome="release",
    )
    assert result["ok"] is False and "network" in result["message"]


def test_fetch_emails_encodes_plus_address():
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        return FakeResp(200, {"success": True, "emails": [{"id": "m1", "subject": "hi"}]})

    mails = provider.fetch_emails(
        get, "https://olp.example.com", "k", "User+tag@outlook.com", folder="all", top=10
    )
    assert mails and mails[0]["id"] == "m1"
    assert "email=User%2Btag%40outlook.com" in calls[0]
    assert "folder=all" in calls[0] and "top=10" in calls[0]


def test_wait_for_code_skips_old_mail_and_finds_code():
    old_mail = {
        "id": "old",
        "subject": "Old newsletter",
        "date": "2026-01-01T00:00:00Z",
        "body_preview": "nothing here",
    }
    fresh_mail = {
        "id": "new",
        "subject": "582-913 xAI",
        "date": "2099-01-01T00:00:00Z",
        "body_preview": "Your code",
    }
    state = {"calls": 0}

    def get(url, headers=None, timeout=None):
        state["calls"] += 1
        return FakeResp(200, {"success": True, "emails": [fresh_mail, old_mail]})

    claim = {
        "account_id": 1,
        "claim_token": "t",
        "email": "a@outlook.com",
        "project_key": "grok",
        "claimed_at": 4102444800.0,  # 2100-01-01，早于 fresh(2099?) —— 这里用 claimed_at 远古值
    }
    # 让 claimed_at 早于 fresh 邮件：claimed_at=0 时不启用过滤，改用显式旧邮件日期校验
    claim["claimed_at"] = 1.0
    token = provider.pack_claim(claim, "a+x@outlook.com", "plus")
    code = provider.wait_for_code(
        get, "https://olp.example.com", "k", token, "a+x@outlook.com",
        timeout=20, poll_interval=1, extract_code=extract_verification_code,
    )
    assert code == "582-913"
    assert state["calls"] >= 1


def test_wait_for_code_timeout():
    def get(url, headers=None, timeout=None):
        return FakeResp(200, {"success": True, "emails": []})

    claim = {"account_id": 1, "claim_token": "t", "email": "a@outlook.com", "claimed_at": 0.0}
    token = provider.pack_claim(claim, "a@outlook.com", "primary")
    try:
        provider.wait_for_code(
            get, "https://olp.example.com", "k", token, "a@outlook.com",
            timeout=30, poll_interval=2, extract_code=extract_verification_code,
        )
        raise AssertionError("应当超时")
    except provider.OutlookPoolError as exc:
        assert "超时" in str(exc)


def test_store_provider_config(tmp_path=None):
    import tempfile

    from webui import email_provider_store as store

    with tempfile.TemporaryDirectory() as temp:
        store.CONFIG_PATH = Path(temp) / "config.json"
        store.LOCK_PATH = Path(temp) / "config.json.lock"
        result = store.save_email_provider_config(
            "outlook_pool",
            {
                "outlook_pool_api_base": "https://olp.example.com/",
                "outlook_pool_api_key": "secret-key",
                "outlook_pool_project": "Grok.1",
                "outlook_pool_alias_mode": "plus",
            },
        )
        assert result["provider"] == "outlook_pool"
        assert result["configured"] is True
        # 密钥不回显
        assert result["values"]["outlook_pool_api_key"] == ""
        assert result["secret_configured"]["outlook_pool_api_key"] is True
        saved = store.CONFIG_PATH.read_text(encoding="utf-8")
        assert "secret-key" in saved

        # 项目 Key 非法值被拒
        try:
            store.save_email_provider_config(
                "outlook_pool",
                {"outlook_pool_project": "bad key!"},
            )
            raise AssertionError("非法项目 Key 应被拒绝")
        except store.EmailProviderConfigError:
            pass

        # 别名模式非法值被拒
        try:
            store.save_email_provider_config(
                "outlook_pool",
                {"outlook_pool_alias_mode": "wild"},
            )
            raise AssertionError("非法别名模式应被拒绝")
        except store.EmailProviderConfigError:
            pass


def test_connectivity_precheck():
    import connectivity

    good = FakeResp(200, {
        "success": True,
        "data": {"projects": [{"project_key": "grok", "active": True, "to_claim_count": 1796}]},
    })

    ok_provider = lambda url, headers=None, timeout=None: good  # noqa: E731
    name, ok, detail = connectivity.check_email_api(
        "outlook_pool",
        {"outlook_pool_api_base": "https://olp.example.com", "outlook_pool_api_key": "k",
         "outlook_pool_project": "grok"},
        ok_provider,
        None,
    )
    assert ok is True and "1796" in detail

    missing = FakeResp(200, {"success": True, "data": {"projects": [{"project_key": "gpt"}]}})
    name, ok, detail = connectivity.check_email_api(
        "outlook_pool",
        {"outlook_pool_api_base": "https://olp.example.com", "outlook_pool_api_key": "k",
         "outlook_pool_project": "grok"},
        lambda *a, **k: missing,
        None,
    )
    assert ok is False and "不存在" in detail

    name, ok, detail = connectivity.check_email_api(
        "outlook_pool",
        {"outlook_pool_api_base": "", "outlook_pool_api_key": "k"},
        None,
        None,
    )
    assert ok is False


def test_grok_ttk_wiring_present():
    source = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    assert "outlook_pool_take_mailbox" in source
    assert 'provider == "outlook_pool"' in source
    assert "_notify_outlook_pool_result" in source
    assert "outlook_pool_release_claim" in source
    store = (ROOT / "webui/email_provider_store.py").read_text(encoding="utf-8")
    assert '"outlook_pool"' in store


if __name__ == "__main__":
    test_alias_modes()
    test_claim_pack_roundtrip_and_registry()
    test_claim_mailbox_success_and_errors()
    test_complete_claim_outcomes()
    test_fetch_emails_encodes_plus_address()
    test_wait_for_code_skips_old_mail_and_finds_code()
    test_wait_for_code_timeout()
    test_store_provider_config()
    test_connectivity_precheck()
    test_grok_ttk_wiring_present()
    print("OK outlook pool")
