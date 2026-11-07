#!/usr/bin/env python3
"""把本地 accounts/*.txt 里未推送的 SSO 补推到 grok2api。

用法：
  python scripts/push_existing_sso.py --from-config config.json --dry-run
  python scripts/push_existing_sso.py --from-config config.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curl_cffi import requests  # noqa: E402

from webui import grok2api_push  # noqa: E402
from webui.push_settings_store import is_push_enabled, push_only_clean  # noqa: E402


def load_sso_accounts(accounts_dir: Path) -> list[dict]:
    out = []
    for path in sorted(accounts_dir.glob("*.txt")):
        try:
            parts = path.read_text(encoding="utf-8").strip().split("----")
        except OSError:
            continue
        if len(parts) >= 3 and parts[2].strip().startswith("eyJ"):
            out.append({"email": parts[0].strip(), "sso": parts[2].strip()})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="补推本地 SSO 到 grok2api")
    ap.add_argument("--from-config", default=str(ROOT / "config.json"))
    ap.add_argument("--accounts-dir", default=str(ROOT / "accounts"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    config = json.loads(Path(args.from_config).read_text(encoding="utf-8"))
    if not is_push_enabled(config):
        print("grok2api 推送未启用或配置不完整（grok2api_push_enabled/url/username/password）")
        return 2

    accounts = load_sso_accounts(Path(args.accounts_dir))
    if args.limit:
        accounts = accounts[: args.limit]
    print(f"本地 SSO：{len(accounts)} 个")

    # 已存在的 grok_web 号
    base = str(config.get("grok2api_push_url") or "").rstrip("/")
    user = str(config.get("grok2api_push_username") or "")
    pwd = str(config.get("grok2api_push_password") or "")
    existing = set()
    try:
        token = grok2api_push.login(requests.post, base, user, pwd)
        for page in range(1, 30):
            resp = requests.get(
                f"{base}/api/admin/v1/accounts?limit=100&page={page}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=40,
                impersonate="chrome",
            )
            items = (resp.json().get("data") or {}).get("items") or []
            for item in items:
                if item.get("provider") == "grok_web":
                    existing.add(str(item.get("email") or "").lower())
            if not items:
                break
    except Exception as exc:
        print(f"读取 grok2api 账号列表失败（将继续逐个推送）: {exc}")

    todo = [a for a in accounts if a["email"].lower() not in existing]
    print(f"已在 grok2api：{len(existing)} | 待推送：{len(todo)}")
    if args.dry_run:
        for a in todo[:20]:
            print("  dry-run:", a["email"])
        return 0

    only_clean = push_only_clean(config)
    ok = skipped = failed = 0
    for i, acct in enumerate(todo, 1):
        if only_clean:
            try:
                from quality_probe import probe_account

                probed = probe_account({}, proxy="")
                if str(probed.get("verdict") or "error") != "healthy":
                    skipped += 1
                    print(f"  [{i}/{len(todo)}] skip(降智) {acct['email'][:34]}")
                    continue
            except Exception:
                pass
        try:
            res = grok2api_push.push_sso(
                requests.post, base, user, pwd, acct["email"], acct["sso"], proxy=""
            )
            if res.get("ok"):
                ok += 1
                print(
                    f"  [{i}/{len(todo)}] OK {acct['email'][:34]} "
                    f"c={res.get('created')} u={res.get('updated')} s={res.get('skipped')}",
                    flush=True,
                )
            else:
                failed += 1
                print(f"  [{i}/{len(todo)}] FAIL {acct['email'][:34]}: {res.get('message')}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  [{i}/{len(todo)}] ERR {acct['email'][:34]}: {str(exc)[:80]}", flush=True)

    print(f"\n完成：成功 {ok} | 降智跳过 {skipped} | 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
