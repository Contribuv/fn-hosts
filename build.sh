#!/bin/bash
# ======================================================
#  Hosts管理器 - FPK 打包脚本
#  用法: bash build.sh
#  或在项目的 fn-hosts 目录中运行: fnpack build
# ======================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  飞牛NAS Hosts管理器 - FPK 打包"
echo "=========================================="

# 检查 fnpack 工具
if ! command -v fnpack &> /dev/null; then
    echo "[错误] 未找到 fnpack 工具"
    echo "请从 https://developer.fnnas.com/ 下载安装:"
    echo "  chmod +x fnpack-*-linux-amd64"
    echo "  sudo mv fnpack-*-linux-amd64 /usr/local/bin/fnpack"
    exit 1
fi

echo "[✓] fnpack 工具已就绪"

# 打包
echo ""
echo "[*] 正在打包..."
fnpack build --directory "$SCRIPT_DIR"

FPK_FILE=$(ls *.fpk 2>/dev/null | head -1)
if [ -n "$FPK_FILE" ]; then
    echo ""
    echo "=========================================="
    echo "  打包成功!"
    echo "  输出文件: $SCRIPT_DIR/$FPK_FILE"
    echo ""
    echo "  安装方式:"
    echo "  1. 飞牛OS应用中心 → 手动安装 → 选择 $FPK_FILE"
    echo "  2. 命令行: appcenter-cli install-fpk $FPK_FILE"
    echo "=========================================="
else
    echo "[错误] 打包失败，未生成 .fpk 文件"
    exit 1
fi
