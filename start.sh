#!/bin/bash
# 社区机器人一键启动（Linux / macOS）
# 用法：bash start.sh   或   双击运行

set -e
cd "$(dirname "$0")"

echo "=== 社区机器人一键启动 ==="
echo ""

# 1. 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[失败] 没找到 python3，请先安装 Python（见 docs/安装指南.md）"
    exit 1
fi
echo "[OK] Python: $(python3 --version)"

# 2. 安装依赖
echo ""
echo "正在安装依赖（pip install -r requirements.txt）..."
pip_out=$(python3 -m pip install -r requirements.txt 2>&1) || {
    if echo "$pip_out" | grep -q "externally-managed-environment"; then
        echo ""
        echo "[提示] 系统 Python 禁止直接 pip 安装（PEP 668，Ubuntu/Debian 常见）"
        echo "  解决办法（二选一）："
        echo "  ① 用虚拟环境（推荐）："
        echo "     python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
        echo "  ② 加参数强制安装：python3 -m pip install --break-system-packages -r requirements.txt"
        echo ""
        echo "选好后重新运行本脚本"
    else
        echo "[失败] 依赖安装失败，请检查网络后重试"
        echo "$pip_out" | tail -5
    fi
    exit 1
}
echo "[OK] 依赖已安装"

# 3. 检查配置文件
if [ ! -f "config/config.yaml" ]; then
    echo ""
    echo "没找到 config/config.yaml，正在从示例复制..."
    cp config/config.example.yaml config/config.yaml
    echo "[OK] 已复制，可先用 Web 界面配置（见第 5 步）"
else
    echo "[OK] 配置文件已存在"
fi

# 4. 管理密码
if [ -z "$WEB_PASSWORD" ]; then
    echo ""
    read -s -p "请设置 Web 管理密码（登录 http://127.0.0.1:52323 用）: " WEB_PASSWORD
    echo ""
    if [ -z "$WEB_PASSWORD" ]; then
        echo "[失败] 密码不能为空"
        exit 1
    fi
    export WEB_PASSWORD
fi
echo "[OK] 管理密码已设置"

# 5. 启动
echo ""
echo "正在启动：守护进程（后台） + Web 管理界面..."
echo "Web 地址：http://127.0.0.1:52323"
echo "按 Ctrl+C 停止"
echo ""

# 守护进程放后台，日志写到 logs/daemon.log
mkdir -p logs data
nohup python3 -m src.main > logs/daemon.log 2>&1 &
daemon_pid=$!
echo $daemon_pid > data/daemon.pid
# M4：等 2 秒确认进程还活着（配置错误会秒退）
sleep 2
if ! kill -0 $daemon_pid 2>/dev/null; then
    echo "[失败] 守护进程启动后立即退出，可能是配置有问题"
    echo "  日志尾部（logs/daemon.log）："
    tail -10 logs/daemon.log | sed 's/^/  /'
    exit 1
fi
echo "[OK] 守护进程已启动（日志：logs/daemon.log）"

# Web 放前台，Ctrl+C 能一起停掉
trap "kill $(cat data/daemon.pid) 2>/dev/null; echo '已停止'; exit 0" INT TERM
python3 -m src.web.app
