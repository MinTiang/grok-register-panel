#!/bin/sh
# 容器入口脚本,做两件事:
#   1. 把运行时目录集中到挂载点 /app/data(容器内仍按项目根目录布局访问,
#      数据实际落在宿主机 ./data/ 下);
#   2. config.json 首次启动自动初始化,启动时载入持久化副本,停止时写回。
set -eu

DATA_DIR=/app/data
mkdir -p "$DATA_DIR"

# 运行时目录 → /app/data/<目录>(符号链接;目录内容持久化到宿主机)
for dir in accounts cpa_auth grok2api_auth log keys auth_out .browser-profiles; do
    mkdir -p "$DATA_DIR/$dir"
    if [ -e "/app/$dir" ] && [ ! -L "/app/$dir" ]; then
        rm -rf "/app/$dir"  # 镜像内置的空目录让位给持久化目录
    fi
    ln -sfn "$DATA_DIR/$dir" "/app/$dir"
done

# config.json:首次启动从示例配置初始化;之后每次启动载入持久化副本
if [ ! -f "$DATA_DIR/config.json" ]; then
    cp /app/config.example.json "$DATA_DIR/config.json"
    echo "[entrypoint] 已生成初始配置 /app/data/config.json,编辑后重启容器生效"
fi
cp "$DATA_DIR/config.json" /app/config.json

# 面板运行期间可能通过 UI 保存 config.json(如邮箱 provider 配置),退出前写回
sync_config() {
    cp /app/config.json "$DATA_DIR/config.json" 2>/dev/null || true
}

shutdown() {
    status=$?
    if [ -n "${child_pid:-}" ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    sync_config
    exit "$status"
}
trap shutdown TERM INT EXIT

# 保持 shell 为 PID 1 以转发信号并负责收尾;"$@" 来自 CMD
"$@" &
child_pid=$!
wait "$child_pid"
