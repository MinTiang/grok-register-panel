# -*- coding: utf-8 -*-
"""Docker 部署资产结构校验：workflow、Dockerfile、compose 与入口脚本。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/docker-build.yml'
DOCKERFILE = ROOT / 'Dockerfile'
COMPOSE = ROOT / 'docker-compose.yaml'
ENTRYPOINT = ROOT / 'docker/entrypoint.sh'


def test_workflow_builds_on_version_tags():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert re.search(r'tags:\s*\n\s*-\s*[\'"]v\*[\'"]', text)
    assert 'ghcr.io' in text


def test_workflow_has_parallel_platform_jobs_and_merge():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'linux/amd64' in text
    assert 'linux/arm64' in text
    assert re.search(r'\bbuild-amd64\b', text)
    assert re.search(r'\bbuild-arm64\b', text)
    # merge 汇聚两个平台 digest 后统一打标签
    assert re.search(r'needs:\s*\[\s*build-amd64,\s*build-arm64\s*\]', text)
    assert 'imagetools create' in text
    assert 'type=semver' in text


def test_dockerfile_bakes_browser_and_headless_stack():
    text = DOCKERFILE.read_text(encoding='utf-8')
    assert 'python -m camoufox fetch' in text
    assert 'playwright install-deps' in text
    assert 'xvfb' in text
    assert 'EXPOSE 8787' in text
    assert 'MONITOR_HOST=0.0.0.0' in text
    assert 'PANEL_INCLUDE_TAIL=0' in text


def test_compose_uses_defaults_without_env_file():
    text = COMPOSE.read_text(encoding='utf-8')
    assert 'ghcr.io/mintiang/grok-register-panel' in text
    assert './data:/app/data' in text
    assert 'MONITOR_TOKEN' in text
    assert 'MONITOR_HOST' in text
    assert 'PANEL_INCLUDE_TAIL' in text
    assert 'GROK_USE_XVFB' in text
    # 不允许依赖 .env 变量替换或 env_file
    assert 'env_file' not in text
    assert '${' not in text


def test_compose_reflects_all_runtime_env_knobs():
    text = COMPOSE.read_text(encoding='utf-8')
    required = (
        # 面板
        'MONITOR_TOKEN', 'MONITOR_HOST', 'MONITOR_PORT', 'MONITOR_CORS_ORIGIN',
        'PANEL_INCLUDE_TAIL', 'TZ',
        # 浏览器 / 指纹
        'GROK_USE_XVFB', 'GROK_BROWSER_OS', 'GROK_HEADLESS', 'GROK_HEADED',
        'GROK_CLIENT_VERSION',
        # 重试与恢复
        'GROK_BATCH_IDLE_TIMEOUT', 'GROK_BATCH_MAX_RESTARTS',
        'GROK_BROWSER_START_ATTEMPTS', 'GROK_PROXY_BOOT_ROTATIONS',
        'GROK_SLOT_RETRIES', 'GROK_ORCH_MAX_CONSECUTIVE_FAILURES',
        # 代理池
        'PROXY_POOL_STATE_FILE', 'PROXY_POOL_LEGACY_FILE',
        'PROXY_NETWORK_COOLDOWN_SECONDS', 'PROXY_RISK_COOLDOWN_SECONDS',
        'PROXY_IP_FRESH_SECONDS', 'PROXY_HOME_PORTS', 'PROXY_BLOCKED_PORTS',
        # 邮箱 / 账号
        'EMAIL_PROVIDER_CONFIG_FILE', 'EMAIL_DOMAIN_POOL_STATE_FILE',
        'BLACKLIST_STATE_FILE', 'INBUCKET_API_BASE',
        'OUTLOOK_RT_CODE_WAIT_CONCURRENCY', 'SSO_WAIT_TIMEOUT',
        # 静态缓存 / 日志 / 目录
        'GROK_STATIC_ASSET_CACHE', 'GROK_STATIC_CACHE_DIR',
        'GROK_STATIC_CACHE_MAX_MB', 'BATCH_LOG',
        'CPA_AUTH_DIR', 'GROK2API_AUTH_DIR',
        # 容器内自动解析 / 自动注入,需在 compose 中说明
        'GROK_PYTHON_BIN', 'PLAYWRIGHT_NODEJS_PATH', 'GROK_COMPAT_PROCESS_ROOTS',
        'GROK_BATCH_PROGRESS_FILE', 'GROK_BATCH_TRAFFIC_FILE',
    )
    for name in required:
        assert name in text, f'{name} 未在 docker-compose.yaml 中说明'
    # 敏感凭据不得作为生效配置写进 compose(会出现在 docker inspect / 进程环境);
    # 注释里的说明文字允许提及变量名
    active = '\n'.join(
        line for line in text.splitlines() if not line.lstrip().startswith('#')
    )
    for secret in ('DUCKMAIL', 'CLOUDMAIL', 'MOEMAIL_API_KEY', 'OUTLOOK_RT_INVENTORY'):
        assert secret not in active, f'{secret} 不应作为配置出现在 docker-compose.yaml'


def test_entrypoint_persists_runtime_data():
    text = ENTRYPOINT.read_text(encoding='utf-8')
    assert '/app/data' in text
    assert 'config.example.json' in text
    for name in ('accounts', 'cpa_auth', 'grok2api_auth', 'log'):
        assert name in text


def test_old_ci_and_star_history_removed():
    for stale in ('ci.yml', 'update-star-history.yml'):
        assert not (ROOT / '.github/workflows' / stale).exists()
    assert not (ROOT / 'scripts/update_star_history.py').exists()
    assert not (ROOT / 'tests/test_star_history.py').exists()
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert 'star-history' not in readme
    run_tests = (ROOT / 'scripts/run_tests.sh').read_text(encoding='utf-8')
    assert 'test_star_history' not in run_tests
    assert 'test_docker_deploy' in run_tests


if __name__ == '__main__':
    test_workflow_builds_on_version_tags()
    test_workflow_has_parallel_platform_jobs_and_merge()
    test_dockerfile_bakes_browser_and_headless_stack()
    test_compose_uses_defaults_without_env_file()
    test_compose_reflects_all_runtime_env_knobs()
    test_entrypoint_persists_runtime_data()
    test_old_ci_and_star_history_removed()
    print('OK docker deploy')
