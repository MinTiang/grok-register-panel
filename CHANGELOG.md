# Changelog

## Unreleased

## 0.10.1 - 2026-11-09

### Fixed

- **修复生产环境「验证码等待超时」导致的大面积失败**（outlookEmail 占 failed 记录 54%）：收信轮询用本机 `time.time()` 减 120s 作为「只看新邮件」的阈值，而邮件 `date` 来自微软服务器；生产 VPS 时钟偏差超过 2 分钟时，新到的验证码邮件全部被误判为旧邮件丢弃，240s 后超时，实际邮件已正常送达。
  - 阈值改以**服务端租约**（`lease_expires_at - lease_seconds`）反推领取时刻，不再依赖本机时钟；
  - 时钟偏移容忍从 120s 放宽到 600s；
  - 兜底：若一轮检查中所有邮件都被时间过滤挡下，自动放宽过滤并清空去重缓存重试，避免误杀。

## 0.10.0 - 2026-11-07

### Fixed

- **修复 grok2api 漏推**：推送原先只在 CPA token 转换成功后触发，导致 SSO 换 token 失败、`bfs_skip_cpa` 过滤、`cpa_auto_add=false`、风控拒绝等分支的号全部漏推。现在推送与 CPA/Grok2API 本地写入**完全解耦**：注册主流程拿到 SSO 保存账号文件后立即推送（三个成功点），补录流程在风控检查与 token 转换之前推送。
- 推送新增本地台账 `accounts/g2a_pushed.json`（`email → status/ts/detail`），成功推送的号自动去重，避免重复推送与漏推无法审计。

### Added

- `scripts/push_existing_sso.py`：把本地 `accounts/*.txt` 中未推送的 SSO 批量补推到 grok2api，支持 `--dry-run` / `--limit`，自动跳过服务端已存在的号。

## 0.9.0 - 2026-11-06

### Added

- 新增 grok2api SSO 推送（可开关）：注册/账号补录成功后把 grok.com SSO 以 Grok Web 账号推送到 chenyme/grok2api 的账号池（管理登录 + `accounts/web/import` multipart 导入，SSE 结果统计 created/updated/skipped）。面板新增「grok2api 推送」设置卡（地址 / 管理员凭据 / 开关），凭据不回显、留空保留。
- 推送降智过滤：`只推不降智的号` 开启后，推送前做实时降智短测（复用入库短测标注，无标注才实时探测），降智/风控的号不推送；推送失败不影响注册与入库结果。

## 0.8.0 - 2026-11-05

### Added

- 新增 `outlook_pool` 邮箱 provider：对接自建 [outlookEmail](https://github.com/assast/outlookEmail) 服务，从项目邮箱池 `claim-random` 领取 Outlook 账号，用 Outlook plus 别名地址（`local+tag@域`，服务端自动解析收信，无需预登记别名）注册；注册成败自动回报 `complete-success` / `complete-failed`，填邮箱前失败自动 `release` 回池，浏览器崩溃/卡住按可重试放回并由租约兜底。
- 面板「邮箱服务」新增 provider 卡片：服务地址 / readwrite API Key / 项目 Key / 别名模式（plus · configured · primary）；连接测试校验服务可达、Key 权限与项目存在性并报待领取数量。密钥保持不回显、留空保留、显式清除语义。

## 0.7.1 - 2026-11-05

### Changed

- `docker-compose.yaml` 的 `environment` 按「面板服务 / 浏览器指纹 / 重试恢复 / 代理池 / 邮箱账号 / 静态缓存 / 容器内不建议修改」七组补齐全部可调环境变量（代码中 39 个环境变量全覆盖，逐个标注默认值与取值范围）；明确业务配置（邮箱 provider、代理、并发、CPA/Grok2API、BFS、降智）在 `./data/config.json`，API key / 密码不得写进 compose（会出现在 `docker inspect`）。
- Docker 文档补充 Grok2API auth 目录说明：输出目录由 `./data/config.json` 的 `grok2api_auth_dir` 决定（SSO→auth 转换脚本按 config 写入），`GROK2API_AUTH_DIR` 仅影响面板统计、BFS 检测与降智扫描目录。

### Added

- `tests/test_docker_deploy.py` 新增全量环境变量覆盖断言（36 个变量必须在 compose 中说明）与凭据泄露断言（DUCKMAIL / CLOUDMAIL / MOEMAIL_API_KEY / OUTLOOK_RT_INVENTORY 不得作为生效配置出现）。

## 0.7.0 - 2026-11-05

### Added

- 新增 Docker 部署方案：`Dockerfile`（内置 Camoufox 浏览器、Xvfb 与 Playwright 系统依赖，支持 `linux/amd64` + `linux/arm64`）、`docker-compose.yaml`（配置全部集中在 environment 段，带默认值与注释，无需 `.env`）和 `docker/entrypoint.sh`（运行数据统一持久化到宿主机 `./data/`，`config.json` 首启自动初始化）。
- 新增 GitHub Actions「Build Docker Image」：推送 `v*` 标签时自动构建多架构镜像并发布到 GHCR；amd64 / arm64 两个 job 并行构建，由 merge job 合并 manifest 并打 `vx.y.z` / `x.y.z` / `x.y` / `latest` 标签。
- 新增 `tests/test_docker_deploy.py`，校验 Docker 相关资产结构与旧 workflow 清理状态。

### Removed

- 移除旧 CI workflow 与 Star History 自更新图表：删除 `ci.yml`、`update-star-history.yml`、`scripts/update_star_history.py`、`tests/test_star_history.py`、`docs/star-history-*.svg` 及 README Star History 章节。

## 0.6.1 - 2026-10-25

### Fixed

- 静态资源缓存不再拦截 JavaScript，也不再拦截整段 `/_next/static/`。注册页脚本留在网络上加载。只缓存 CSS、字体和图片。
- 发码成功后的 “Verify your email” 页面按验证码已发送处理，不再当成空白页刷新掉。

### Verified

- home-serve 上 `GROK_STATIC_ASSET_CACHE=1` 的 6 号批次：`成功 6 | 失败 0`。六个邮箱都收到验证码、拿到 SSO，并写成 `status=ok`。空白页失败为 0，缓存命中 52、未命中 0。

## 0.6.0 - 2026-10-24

### Added

- 新增 Outlook/Hotmail 本地卡池 provider：支持 `email----password----client_id----refresh_token` 格式、IMAP XOAUTH2 收信，以及桌面 GUI / CLI 配置。
- 新增 20 项离线回归测试，覆盖跨平台导入、旧编码控制台、线程/进程并发取号、共享已用文件、写盘异常、旧邮件过滤和注册阶段回收边界；接入现有 Windows 测试入口。

### Changed

- 页面导航默认等待 `domcontentloaded`，并放宽注册页与邮箱表单等待时间。
- CPA chat 请求头默认使用客户端版本 `1.0.13`，可通过 `GROK_CLIENT_VERSION` 覆盖；token 交换链路继续使用独立版本常量。
- 降智测试只按有没有 thinking 判定：有 thinking 为正常，没有为降智。不再用 Token/s / `burst` / `soft` 分档。

### Fixed

- Outlook 卡池复用跨平台文件锁和私有原子写入，修复 Windows 无法导入 `fcntl` 的启动错误及旧编码控制台中文日志异常。
- 取号时先保存已用记录，再更新待用池；写盘与回滚异常保留账号记录，已用路径留空使用默认值，多个卡池共用已用文件时共同加锁。
- 仅读取基线 UID 之后的新邮件；基线探测失败时归还邮箱并终止该次取号，防止误用旧验证码。
- 并发 worker 进入建号资料提交阶段后保留邮箱为已用状态，防止 SSO 超时、建号结果不明或本地保存失败导致重复注册。

## 0.5.0 - 2026-09-06

### Added

- Add a panel **降智测试** that batch-probes CPA / Grok2API accounts with a real streamed reply over configured 家宽 proxies. Missing thinking or inflated Token/s is degraded; 401/403 / permission-denied is risk. CLI: `scripts/check_quality.py`.
- Optional register-time short probe via `quality_probe_on_register` (default **off**). When enabled, SSO→OAuth write stamps `quality_*` onto CPA / Grok2API auth so a later panel scan is not required for new accounts.
- Add an `inbucket` email provider for self-hosted Inbucket instances: generate addresses under a configured receive domain and poll the v1 mailbox API for the xAI verification code. Root domains accept a comma-separated rotation list, and `inbucket_random_levels` can stack 1-3 random subdomain labels per address (wildcard MX required).
- Treat Windows as a first-class runtime: PowerShell setup/batch/panel scripts, Playwright `node.exe` + EPIPE guard (no bash wrapper), default headless batches, and SOCKS5 `PySocks` as a direct dependency so the panel can import remote residential URLs without Linux mixed ports.
- Add a GitHub Pages landing page and link Discussions from the README.

### Changed

- Stop using grok.com `botFlagSource` / `policy=deny` as a registration risk gate or operational verdict. SSO scan remains as a deprecated diagnostic only.
- Recommend residential (家宽) exits and Outlook-class mailboxes; domain emails are no longer the suggested default.
- 降智测试改为短题（时钟夹角 `3:27` + `QUALITY_OK`）、`max_tokens=48`，见到 thinking 约 800ms 后掐流。面板批量扫描用于存量复测。

### Fixed

- Stop assigning `scripts/playwright-node` (a POSIX shell wrapper) to `PLAYWRIGHT_NODEJS_PATH` on Windows, which previously made Camoufox fail to spawn.
- Keep POSIX `GROK_PLAYWRIGHT_NODE` on a real node binary so the wrapper cannot `exec` itself. Quote Windows `NODE_OPTIONS --require` paths that contain spaces.
- Treat a mid-stream proxy/upstream disconnect as a transport error (or keep a partial sample) instead of crashing the quality scan.

## 0.4.4 - 2026-08-16

### Added

- Persist owner-only batch traffic history and show rolling average traffic per batch and per successful account in the live panel.
- Add an SSO risk panel and CLI that check `botFlagSource` / `policy=deny` from existing SSO cookies without exchanging tokens; panel exports contain redacted state only, while reusable clean SSO remains host-local.
- Pin Camoufox/Playwright to system Node 22 plus an EPIPE socket guard so a dead browser pipe no longer kills the whole batch.
- Default browser fingerprints to Windows (UA / WebGL / Segoe) even when the host is Linux Xvfb.

### Fixed

- After x.ai shows the Next.js “error loading this page” overlay, keep waiting for the SSO redirect instead of hard-reloading `/sign-up` (that reload was aborting createAccount).
- Treat Playwright `EPIPE` / `TargetClosed` as a retryable browser failure and rotate the sticky exit.

## 0.4.2 - 2026-08-11

### Fixed

- Validate managed proxies against the actual xAI registration page, immediately retire proxies that fail the batch precheck, and require an explicit retest after network/TLS cooldowns instead of reviving stale health from an old exit IP.

## 0.4.1 - 2026-08-11

### Fixed

- Fix Windows state, progress, proxy, email, panel, orchestrator, and recovery file writes when `os.fchmod` is unavailable; use `msvcrt` byte-range locks for cross-process state protection.
- Add `GROK_HEADLESS` / `GROK_HEADED` controls and software-rendering preferences for Windows sessions where headed Camoufox GPU processes fail.
- Preserve configured virtual-environment Python symlink paths so panel-launched jobs keep their installed dependencies.
- Ship `tzdata` in both direct and locked dependency manifests for Windows Beijing-time support.

## 0.4.0 - 2026-08-11

### Added

- Add post-registration sign-in recovery so workers can rebuild an SSO session with the account credentials when the normal redirect loses the cookie.
- Add bounded Cloudflare address-collision retries and a configurable random-subdomain mode for wildcard mail routing.
- Add per-batch HTTP/HTTPS proxy byte metering with authenticated upstream forwarding and a live upload/download KPI in the panel.
- Add Windows-safe supervisor pipe streaming, recursive Camoufox process-tree shutdown, and user-local browser profiles.

### Changed

- Display panel, proxy, blacklist, and worker timestamps in Beijing time regardless of the server timezone.
- Keep up to 80 recent success/failure records and paginate them in the panel while refreshing full statistics every 30 seconds.
- Enable the guarded static-asset cache for panel-launched batches and reduce browser, proxy-rotation, slot, and supervisor retry defaults.
- Stop the batch and orchestrator without retrying when the xAI registration-page precheck fails.

### Fixed

- Preserve BFS `unknown` handling and `bfs_skip_cpa` behavior instead of treating undecodable tokens as clean.
- Redact Cloudflare fallback errors, avoid retrying unrelated HTTP 400 responses, and fall back to fixed UTC+8 when system tzdata is unavailable.
- Remove the narrow-layout horizontal overflow in the panel's proxy and email views.
- Bound Windows proxy exit-IP probes by a total timeout and reject malformed or stale cached IP candidates.

## 0.3.0 - 2026-08-09

### Added

- Add a managed external proxy pool with single/bulk HTTP proxy import, enable/disable controls, health tests, cooldown state, and panel APIs.
- Add configurable email providers, including MoeMail, plus an authenticated provider editor and connection test in the panel.
- Add a managed email-domain pool with rotation rules, failure thresholds, reset controls, and worker integration.
- Add **JWT `bfs` claim detection** to registration and OAuth output. Flagged accounts are recorded in `accounts/sso_bfs_flagged.txt`, and CPA records receive `bfs` metadata.
- Add the panel **BFS 检测** card, `/api/bfs` endpoints, `scripts/check_bfs.py`, and the `bfs_check`, `bfs_skip_cpa`, and `bfs_disable_cpa` settings.
- Add an opt-in shared browser static-asset cache for scripts, stylesheets, fonts, and public images, with size limits, TTL handling, and private-response safeguards.
- Add a self-updating GitHub Star History chart to the project documentation.

### Changed

- Supervise headless batches and atomically persist completed slots so Playwright/Camoufox driver crashes or stalls resume only the remaining work.
- Make process discovery and batch launch platform-aware with `psutil`, Linux auto-Xvfb, macOS direct launch, Windows virtualenv paths, and actionable missing-procfs errors.
- Support external runtime roots while keeping process control scoped to the configured project.
- Move GitHub Actions to Node.js 24-compatible action versions and expand release checks for proxy, email, BFS, cache, platform, and supervisor behavior.
- Document deployment guidance, the LINUX DO community link, and the related Grok2API egress project.

### Fixed

- Fix BFS unknown-token handling, stale metadata precedence, merged auth scanning, CLI config loading, and configured relative/absolute auth-directory resolution.
- Detect buffered Playwright crash markers immediately instead of waiting for the supervisor idle timeout.
- Avoid false Cloudflare email preflight failures when `/admin/new_address` uses `x-admin-auth` but `/api/domains` expects mailbox authentication.
- Preserve full success statistics and per-day jsonl results across the panel's two-second status polling.

## 0.2.0 - 2026-07-30

- Redesign the live panel with responsive light and dark themes.
- Add a dedicated usage and troubleshooting view.
- Add pending SSO and account-file recovery with success dequeue.
- Move learned ASN rules from Python source into locked JSON state.
- Scope process discovery and termination to one project root.
- Require monitor authentication for operational read and write APIs.
- Add security headers, bounded request bodies, and redacted log output.
- Create runtime credentials, account data, logs, state, and PID files owner-only.
- Add release tests, CI, a systemd service template, and deployment checks.
