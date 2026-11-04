# syntax=docker/dockerfile:1

# Grok Register + Live Panel 运行镜像
#
# 内置:Python 3.11、Camoufox 浏览器(构建期预下载)、Xvfb、Playwright 系统依赖、
#       系统 Node(scripts/playwright-node 包装脚本优先使用)。
# 架构:linux/amd64 + linux/arm64(Camoufox 官方均提供构建)。
# 用法:见 docker-compose.yaml 与 DEPLOYMENT.md 的 Docker 章节。
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 系统依赖:
#   xvfb / xauth  无显示环境下面板自动经 xvfb-run 启动批次(GROK_USE_XVFB=auto)
#   nodejs        scripts/playwright-node 包装脚本优先使用系统 Node 18/20/22
#   fonts-liberation / libdbus-glib-1-2  浏览器字体与 Firefox 运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb xauth nodejs fonts-liberation libdbus-glib-1-2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单:代码改动不会使依赖层缓存失效
COPY requirements.txt requirements.lock.txt ./

# Python 依赖 → Playwright/Firefox 系统库(清单由 Playwright 维护,自动匹配 amd64/arm64)
# → 构建期预下载 Camoufox 浏览器(约数百 MB),容器首启无需再联网下载
RUN python -m pip install -r requirements.txt \
    && apt-get update \
    && python -m playwright install-deps firefox \
    && rm -rf /var/lib/apt/lists/* \
    && python -m camoufox fetch

# 项目代码(.dockerignore 已排除 .git、docs、tests 与本地运行时数据)
COPY . .
RUN chmod +x scripts/playwright-node

# 面板默认绑定 0.0.0.0:8787(容器内必须监听非回环地址才能映射到宿主机)
ENV MONITOR_HOST=0.0.0.0 \
    MONITOR_PORT=8787 \
    PANEL_INCLUDE_TAIL=0 \
    GROK_USE_XVFB=auto

EXPOSE 8787

COPY docker/entrypoint.sh /usr/local/bin/grok-entrypoint
RUN chmod 0755 /usr/local/bin/grok-entrypoint

ENTRYPOINT ["grok-entrypoint"]
# 默认启动面板。MONITOR_TOKEN 必须由 compose/运行环境提供,否则面板拒绝启动
# (非回环绑定无令牌不安全,这是应用自身的安全约束)。
CMD ["python", "-u", "webui/monitor.py"]
