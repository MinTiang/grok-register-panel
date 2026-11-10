# -*- coding: utf-8 -*-
"""验证码等待：批量路径换邮箱重试 + 可配置超时。"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")


def test_code_timeout_is_configurable():
    assert "def get_outlook_pool_code_timeout" in SRC
    assert "outlook_pool_code_timeout" in SRC
    # provider 分支必须使用该 helper，而不是硬编码 240
    # 在 get_oai_code 内部（含 wait_for_code 调用的那个分支）
    assert "timeout=max(int(timeout or 0), get_outlook_pool_code_timeout())" in SRC


def test_timeout_helper_bounds():
    import grok_register_ttk as g

    old = dict(getattr(g, "config", {}) or {})
    try:
        g.config = {}
        assert g.get_outlook_pool_code_timeout() == 150
        g.config = {"outlook_pool_code_timeout": 10}
        assert g.get_outlook_pool_code_timeout() == 60          # 下限
        g.config = {"outlook_pool_code_timeout": 99999}
        assert g.get_outlook_pool_code_timeout() == 600         # 上限
        g.config = {"outlook_pool_code_timeout": "bad"}
        assert g.get_outlook_pool_code_timeout() == 150         # 非法值回落
    finally:
        g.config = old


def test_batch_worker_retries_with_new_mailbox():
    """批量 worker 必须含换邮箱重试（生产曾缺失，86 次超时全部直接失败）。"""
    assert "mail_code_retry" in SRC
    assert "_mail_retry_limit" in SRC
    # 重试分支：换新邮箱前重启浏览器并记录日志
    segment = SRC.split("_mail_retry_limit", 1)[1]
    assert "换新邮箱重试" in segment
    assert "restart_browser(" in segment
    # 三层循环结构：while i < n -> while True(换邮箱) -> try fill_code_and_submit
    tree = ast.parse(SRC)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "worker":
            for inner in ast.walk(node):
                if isinstance(inner, ast.While) and isinstance(inner.test, ast.Constant) and inner.test.value is True:
                    body = ast.dump(inner)
                    if "fill_code_and_submit" in body and "restart_browser" in body:
                        found = True
    assert found, "未找到批量 worker 的换邮箱重试循环"


def test_retry_does_not_consume_slot_prematurely():
    """_mail_try 只在验证码失败时递增，且受上限约束。"""
    lines = SRC.splitlines()
    start = next(i for i, line in enumerate(lines) if "_mail_try = 0" in line)
    segment = chr(10).join(lines[start:start + 120])
    assert "_mail_try < _mail_retry_limit" in segment
    assert "_mail_try += 1" in segment
    assert "code_failed" in segment


if __name__ == "__main__":
    test_code_timeout_is_configurable()
    test_timeout_helper_bounds()
    test_batch_worker_retries_with_new_mailbox()
    test_retry_does_not_consume_slot_prematurely()
    print("OK code wait retry")
