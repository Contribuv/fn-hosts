#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞牛NAS - Hosts 文件管理应用 (FPK版)
====================================
由 fnOS 应用中心以 root 权限启动，负责读写 /etc/hosts 文件。
仅监听 127.0.0.1，安全性由 fnOS 网关 + 管理员校验双重保障。
"""

import sys as _sys
import os as _os

# 把 vendor/ 加入 sys.path（install_callback 用 pip --target 安装依赖到此）
_APP_DIR = _os.environ.get("TRIM_APPDEST", _os.path.dirname(_os.path.abspath(__file__)))
_VENDOR = _os.path.join(_APP_DIR, "vendor")
if _os.path.isdir(_VENDOR) and _VENDOR not in _sys.path:
    _sys.path.insert(0, _VENDOR)

_stderr = _sys.stderr
_stdout = _sys.stdout

def _early_log(msg):
    """最早期日志，在 logging 初始化之前使用，直接写 stderr 并 flush"""
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    _stderr.write(f"{ts} | EARLY    | hosts_manager | {msg}\n")
    _stderr.flush()

_early_log("app.py 开始执行，Python版本: " + _sys.version.split()[0])

import logging
import logging.handlers
import os
import re
import socket
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory

# ===================== 配置 =====================
HOSTS_FILE = "/etc/hosts"
BACKUP_DIR = "/etc/hosts_backups"
APP_HOST = "127.0.0.1"   # 仅监听本地回环，所有流量通过 Unix Socket 网关代理进入

# fnOS 环境变量（遵循官方规范）
#   APP_LOG_FILE 由 cmd/main 统一设置，确保脚本和 Python 写入同一日志
#   TRIM_PKGVAR  官方推荐变量（ = /var/apps/fnnas.hosts/var ）
#   TRIM_VARDIR  旧版兼容
#   TRIM_APPDEST 应用可执行文件目录
TRIM_APPDEST = os.environ.get("TRIM_APPDEST", os.path.dirname(os.path.abspath(__file__)))
_PKGVAR = os.environ.get("TRIM_PKGVAR", "") or os.environ.get("TRIM_VARDIR", "") or os.path.join(TRIM_APPDEST, "..", "var")
TRIM_TMPDIR = os.environ.get("TRIM_TMPDIR", "") or os.path.join(TRIM_APPDEST, "..", "tmp")
APP_PORT = int(os.environ.get("TRIM_SERVICE_PORT", "5080"))

_early_log(f"环境变量: TRIM_PKGVAR={_PKGVAR}, TRIM_APPDEST={TRIM_APPDEST}")

app = Flask(__name__, static_folder=None)

_early_log("Flask 实例已创建")


# ===================== 日志系统 =====================
# 遵循 fnOS 官方规范：
#   cmd/main 通过 APP_LOG_FILE 环境变量传递日志路径
#   日志写入 $TRIM_PKGVAR/info.log（即 /var/apps/fnnas.hosts/var/info.log）
#   fnOS 测试文档明确指出日志位于此路径
#
# 日志传递链：cmd/main → export APP_LOG_FILE → app.py 读取

_APP_LOG = os.environ.get("APP_LOG_FILE", "")
if _APP_LOG:
    LOG_FILE = _APP_LOG
    _early_log(f"日志路径(cmd/main传递): {LOG_FILE}")
else:
    # 回退：自行拼出与 cmd/main 一致的路径
    LOG_FILE = os.path.join(_PKGVAR, "info.log")
    _early_log(f"日志路径(自行推导): {LOG_FILE}")

try:
    _log_dir = os.path.dirname(LOG_FILE)
    if _log_dir:
        os.makedirs(_log_dir, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as _f:
        _f.write("")
    _early_log(f"日志文件可写: {LOG_FILE}")
except OSError as e:
    _early_log(f"!! 日志文件写入失败: {LOG_FILE} -- {e}")
    LOG_FILE = os.path.join(TRIM_APPDEST, "info.log")
    _early_log(f"终极回退: {LOG_FILE}")

log_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 文件日志 Handler → $PKG_VAR/info.log（DEBUG 级别，fnOS 应用中心可查看）
try:
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)
except OSError as e:
    file_handler = None
    _early_log(f"!! 无法创建文件日志 handler: {e}")

# 控制台日志 Handler = stderr（INFO 级别）
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.DEBUG)  # stderr 也输出DEBUG，方便排查

logger = logging.getLogger("hosts_manager")
logger.setLevel(logging.DEBUG)
# 先附上 console（必定可用）
logger.addHandler(console_handler)
if file_handler:
    logger.addHandler(file_handler)

logger.info("=" * 50)
logger.info("Hosts 管理器 日志系统初始化完成")
logger.info("  主日志文件: %s", LOG_FILE)
logger.info("  Python版本: %s", sys.version)
logger.info("  CWD: %s", os.getcwd())
logger.info("  PID: %s, EUID: %s", os.getpid(), os.geteuid())
_early_log("日志系统初始化完成，切换到 Python logging")


# ===================== 请求日志中间件 =====================
@app.before_request
def log_request():
    """记录所有 HTTP 请求（含完整 Header，用于诊断 fnOS 网关透传情况）"""
    # 记录关键 Header
    logger.info(
        "请求 | %s %s | 来源: %s | 用户: %s | 管理员: %s",
        request.method,
        request.path,
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("X-Trim-Username", "(无Header)"),
        request.headers.get("X-Trim-Isadmin", "(无Header)"),
    )
    # 记录所有 X-Trim-* 和 X-Forwarded-* Header（完整调试信息）
    trim_headers = {k: v for k, v in request.headers.items() if k.lower().startswith("x-trim") or k.lower().startswith("x-forwarded")}
    if trim_headers:
        logger.debug("fnOS透传Header: %s", trim_headers)
    else:
        logger.debug("未检测到 fnOS 网关 Header（可能为直接访问或代理未配置）")
    # 每次请求都记录全部 Header（便于排查）
    all_hdrs = dict(request.headers)
    logger.debug("全部HTTP Header (%d个): %s", len(all_hdrs), all_hdrs)


@app.after_request
def log_response(response):
    """记录所有 HTTP 响应"""
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    logger.log(
        level,
        "响应 | %s %s → %s | 大小: %s",
        request.method,
        request.path,
        response.status_code,
        response.content_length or "-",
    )
    return response


# ===================== 管理员认证中间件 =====================
def require_admin(f):
    """
    严格权限校验 - 基于 fnOS 统一网关认证。

    通过 Unix Socket Reverse Proxy 接入网关后，所有请求都经过网关代理，
    因此必定携带 X-Trim-* Header。未登录用户根本不会到达应用。

    校验规则：
    - 无 Header → 拒绝（异常情况）
    - X-Trim-Isadmin ≠ "true" → 拒绝（非管理员）
    - X-Trim-Isadmin = "true" → ✅ 通过

    参考文档：
    - developer.fnnas.com/docs/core-concepts/gateway-authentication
    - developer.fnnas.com/docs/core-concepts/gateway-registration
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        is_admin_hdr = request.headers.get("X-Trim-Isadmin", "")
        username = request.headers.get("X-Trim-Username", "")
        uid = request.headers.get("X-Trim-Userid", "") or request.headers.get("X-Trim-Uid", "")

        # 网关必须透传身份信息（经过 Unix Socket 代理的所有请求都会有）
        if not all([uid, is_admin_hdr, username]):
            logger.warning(
                "认证失败(无网关Header) | 来源:%s | UID=%s IsAdmin=%s Username=%s | %s",
                request.remote_addr, uid or "(无)", is_admin_hdr or "(无)",
                username or "(无)", request.path
            )
            if not request.path.startswith("/api/"):
                return NOT_AUTHENTICATED_HTML, 403, {"Content-Type": "text/html; charset=utf-8"}
            return jsonify({
                "success": False,
                "error": "未通过 fnOS 网关认证",
                "gateway_required": True
            }), 403

        # 必须是管理员
        is_admin_effective = is_admin_hdr.lower() in ("true", "1", "yes", "on")
        if not is_admin_effective:
            logger.warning(
                "权限拒绝(非管理员) | 用户:%s (UID:%s) IsAdmin=%s | %s",
                username, uid, is_admin_hdr, request.path
            )
            if not request.path.startswith("/api/"):
                return PERMISSION_DENIED_HTML.replace("{username}", username), 403, {"Content-Type": "text/html; charset=utf-8"}
            return jsonify({
                "success": False,
                "error": "权限不足：仅限 NAS 管理员使用",
                "admin_required": True, "current_user": username
            }), 403

        logger.debug("认证通过 | 管理员:%s (UID:%s) | %s", username, uid, request.path)
        return f(*args, **kwargs)
    return decorated


# 未经过网关认证时的 HTML 页面（直接端口访问等场景）
NOT_AUTHENTICATED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>需要登录 - Hosts管理器</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
       background:#1a1d23; color:#e1e3e6; display:flex; align-items:center;
       justify-content:center; min-height:100vh; }
.card { text-align:center; max-width:420px; padding:40px; }
.icon { font-size:64px; margin-bottom:20px; }
h2 { font-size:20px; margin-bottom:12px; font-weight:600; }
p { font-size:14px; color:#9a9ea8; line-height:1.8; }
strong.highlight { color:#4f8ff7; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">🛡️</div>
  <h2>需要登录认证</h2>
  <p>
    请从 <strong class="highlight">飞牛应用中心</strong> 打开本应用。<br/><br/>
    Hosts 管理器需要通过飞牛网关进行身份认证，<br/>不支持直接端口访问。
  </p>
</div>
</body>
</html>"""

# 权限不足时的 HTML 页面（已登录但非管理员）
PERMISSION_DENIED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>权限不足 - Hosts管理器</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
       background:#1a1d23; color:#e1e3e6; display:flex; align-items:center;
       justify-content:center; min-height:100vh; }
.card { text-align:center; max-width:420px; padding:40px; }
.icon { font-size:64px; margin-bottom:20px; }
h2 { font-size:20px; margin-bottom:12px; font-weight:600; }
p { font-size:14px; color:#9a9ea8; line-height:1.8; }
strong.admin { color:#4f8ff7; }
strong.tip { color:#f0a860; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">🔒</div>
  <h2>需要管理员权限</h2>
  <p>
    当前用户 <strong class="admin">{username}</strong> 不是 NAS 管理员，无权使用 Hosts 管理器。<br/><br/>
    请使用 <strong class="tip">管理员账号</strong> 登录飞牛NAS后重试。
  </p>
</div>
</body>
</html>"""


# ===================== 工具函数 =====================
def parse_hosts_file():
    """解析 hosts 文件，返回条目列表"""
    entries = []
    if not os.path.exists(HOSTS_FILE):
        return entries
    with open(HOSTS_FILE, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                entries.append({
                    "line": line_num, "type": "comment" if stripped.startswith("#") else "blank",
                    "content": stripped, "ip": "", "hostnames": "", "enabled": True
                })
            else:
                parts = re.split(r'\s+', stripped)
                if len(parts) >= 2:
                    entries.append({
                        "line": line_num, "type": "entry",
                        "ip": parts[0], "hostnames": " ".join(parts[1:]),
                        "content": stripped, "enabled": True
                    })
                else:
                    entries.append({
                        "line": line_num, "type": "unknown",
                        "content": stripped, "ip": "", "hostnames": "", "enabled": True
                    })
    return entries


def validate_ip(ip):
    """验证 IP 地址格式 (IPv4 和 IPv6)"""
    ipv4 = re.compile(r'^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$')
    ipv6 = re.compile(r'^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$')
    return bool(ipv4.match(ip) or ipv6.match(ip))


def validate_hostname(hostname):
    """验证主机名格式，支持单标签和完整域名(FQDN)如 api.themoviedb.org"""
    if not hostname or len(hostname) > 253:
        return False
    label_pattern = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?$')
    for label in hostname.split('.'):
        if not label or len(label) > 63 or not label_pattern.match(label):
            return False
    return True


def backup_hosts():
    """备份当前 hosts 文件"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"hosts.bak.{timestamp}")
    if os.path.exists(HOSTS_FILE):
        with open(HOSTS_FILE, "r", encoding="utf-8") as src:
            with open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
    logger.info("备份 | hosts 文件已备份至: %s", backup_path)
    return backup_path


def flush_dns_cache():
    """刷新 DNS 缓存，使 hosts 修改实时生效"""
    results = []
    for svc, cmd in [
        ("systemd-resolved", [["systemctl", "is-active", "systemd-resolved"], ["systemd-resolve", "--flush-caches"]]),
        ("nscd", [["systemctl", "is-active", "nscd"], ["nscd", "-i", "hosts"]]),
        ("dnsmasq", [["systemctl", "is-active", "dnsmasq"], ["systemctl", "restart", "dnsmasq"]]),
    ]:
        try:
            r = subprocess.run(cmd[0], capture_output=True, text=True, timeout=5)
            if r.stdout.strip() == "active":
                subprocess.run(cmd[1], capture_output=True, text=True, timeout=10)
                results.append((svc, "已刷新"))
                logger.debug("DNS 缓存 | %s 服务已刷新", svc)
        except Exception as e:
            logger.debug("DNS 缓存 | %s 服务检测/刷新跳过: %s", svc, e)
    if not results:
        logger.debug("DNS 缓存 | 未检测到缓存服务，hosts 直接生效")
        results.append(("direct", "hosts 文件直接生效 (无缓存服务)"))
    return results


# ===================== 路由 =====================

# ---------- 诊断端点（无需认证）----------
@app.route("/api/diag", methods=["GET"])
def api_diag():
    """诊断端点：返回应用运行状态和所有环境信息，无需登录验证"""
    import platform
    info = {
        "app": {
            "version": "1.0.29",
            "pid": os.getpid(),
            "euid": os.geteuid(),
            "is_root": os.geteuid() == 0,
            "cwd": os.getcwd(),
            "app_dest": TRIM_APPDEST,
        },
        "files": {
            "hosts_file": HOSTS_FILE,
            "hosts_exists": os.path.exists(HOSTS_FILE),
            "hosts_writable": os.access(HOSTS_FILE, os.W_OK) if os.path.exists(HOSTS_FILE) else False,
            "log_file": LOG_FILE,
            "log_exists": os.path.exists(LOG_FILE),
            "socket_file": UNIX_SOCKET_PATH,
            "socket_exists": os.path.exists(UNIX_SOCKET_PATH),
        },
        "env": {
            "TRIM_APPDEST": os.environ.get("TRIM_APPDEST", ""),
            "TRIM_PKGVAR": os.environ.get("TRIM_PKGVAR", ""),
            "TRIM_VARDIR": os.environ.get("TRIM_VARDIR", ""),
            "TRIM_TMPDIR": os.environ.get("TRIM_TMPDIR", ""),
            "APP_LOG_FILE": os.environ.get("APP_LOG_FILE", ""),
        },
        "network": {
            "remote_addr": request.remote_addr,
            "path": request.path,
            "script_name": request.environ.get("SCRIPT_NAME", ""),
        },
        "request_headers": dict(request.headers),
        "system": {
            "hostname": platform.node(),
            "python": sys.version,
            "platform": platform.platform(),
        }
    }
    logger.info("DIAG | 诊断查询 (来源: %s)", request.remote_addr)
    return jsonify({"success": True, "data": info})


# ---------- 网关身份端点（无需认证，供前端判断权限）----------
@app.route("/api/user", methods=["GET"])
def api_user():
    """返回网关透传的用户身份信息，前端据此决定 UI 行为"""
    uid = request.headers.get("X-Trim-Userid", "") or request.headers.get("X-Trim-Uid", "")
    username = request.headers.get("X-Trim-Username", "")
    is_admin = request.headers.get("X-Trim-Isadmin", "").lower() in ("true", "1", "yes")
    logger.info("USER查询 | UID=%s 用户=%s IsAdmin=%s | 来源:%s",
                uid or "(无)", username or "(无)", is_admin,
                request.headers.get("X-Forwarded-For", request.remote_addr))
    return jsonify({
        "success": True,
        "data": {
            "uid": uid,
            "username": username,
            "is_admin": is_admin,
            "authenticated": bool(uid),
            "gateway_headers_present": bool(uid and username),
        }
    })


@app.route("/")
def index():
    """返回前端页面（无需鉴权，前端通过 /api/user 获取身份信息后自行判断权限）"""
    return send_from_directory(TRIM_APPDEST, "index.html")


@app.route("/api/status", methods=["GET"])
@require_admin
def api_status():
    is_root = (os.geteuid() == 0)
    hosts_exists = os.path.exists(HOSTS_FILE)
    hosts_writable = os.access(HOSTS_FILE, os.W_OK) if hosts_exists else False
    logger.debug("状态查询 | root=%s, hosts存在=%s, hosts可写=%s", is_root, hosts_exists, hosts_writable)
    return jsonify({
        "success": True,
        "data": {
            "is_root": is_root,
            "hosts_path": HOSTS_FILE,
            "hosts_exists": hosts_exists,
            "hosts_writable": hosts_writable,
            "hostname": os.uname().nodename if hasattr(os, 'uname') else "unknown"
        }
    })


@app.route("/api/hosts", methods=["GET"])
@require_admin
def api_get_hosts():
    try:
        entries = parse_hosts_file()
        entry_count = len([e for e in entries if e["type"] == "entry"])
        logger.debug("读取 | 共 %d 条有效记录，%d 行总计", entry_count, len(entries))
        return jsonify({
            "success": True,
            "data": entries,
            "total": entry_count,
            "file_path": HOSTS_FILE
        })
    except Exception as e:
        logger.error("读取 hosts 失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/add", methods=["POST"])
@require_admin
def api_add_host():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    ip = data.get("ip", "").strip()
    hostnames = data.get("hostnames", "").strip()
    comment = data.get("comment", "").strip()

    if not ip:
        return jsonify({"success": False, "error": "IP 地址不能为空"}), 400
    if not hostnames:
        return jsonify({"success": False, "error": "主机名不能为空"}), 400
    if not validate_ip(ip):
        return jsonify({"success": False, "error": f"无效的 IP 地址: {ip}"}), 400
    for hostname in hostnames.split():
        if not validate_hostname(hostname):
            return jsonify({"success": False, "error": f"无效的主机名: {hostname}"}), 400

    # 查重
    for entry in parse_hosts_file():
        if entry["type"] == "entry" and entry["ip"] == ip:
            if all(h in entry["hostnames"].split() for h in hostnames.split()):
                return jsonify({"success": False, "error": f"该记录已存在: {ip} {hostnames}"}), 409

    backup_path = backup_hosts()
    try:
        new_line = f"{ip}\t{hostnames}"
        if comment:
            new_line += f"\t# {comment}"
        new_line += "\n"
        with open(HOSTS_FILE, "a", encoding="utf-8") as f:
            f.write(new_line)
        dns_result = flush_dns_cache()
        logger.info("添加 | %s %s (注释: %s)", ip, hostnames, comment or "无")
        return jsonify({
            "success": True,
            "message": "hosts 记录添加成功，已实时生效",
            "data": {"ip": ip, "hostnames": hostnames, "backup": backup_path, "dns_flush": dns_result}
        })
    except PermissionError:
        logger.error("添加失败 | 权限不足: %s %s", ip, hostnames)
        return jsonify({"success": False, "error": "权限不足！请确保以 root 权限运行。"}), 500
    except Exception as e:
        logger.error("添加失败 | %s %s: %s\n%s", ip, hostnames, e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/delete/<int:line_num>", methods=["DELETE"])
@require_admin
def api_delete_host(line_num):
    if not os.path.exists(HOSTS_FILE):
        return jsonify({"success": False, "error": "hosts 文件不存在"}), 404

    backup_path = backup_hosts()
    try:
        with open(HOSTS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if line_num < 1 or line_num > len(lines):
            logger.warning("删除失败 | 行号 %d 超出范围 (1-%d)", line_num, len(lines))
            return jsonify({"success": False, "error": f"行号超出范围: {line_num}"}), 400
        deleted_line = lines[line_num - 1].strip()
        del lines[line_num - 1]
        with open(HOSTS_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
        dns_result = flush_dns_cache()
        logger.info("删除 | 第 %d 行: %s", line_num, deleted_line)
        return jsonify({
            "success": True,
            "message": f"已删除第 {line_num} 行，实时生效",
            "data": {"deleted": deleted_line, "line": line_num, "backup": backup_path, "dns_flush": dns_result}
        })
    except PermissionError:
        logger.error("删除失败 | 权限不足 行号 %d", line_num)
        return jsonify({"success": False, "error": "权限不足！"}), 500
    except Exception as e:
        logger.error("删除失败 | 行号 %d: %s\n%s", line_num, e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/update/<int:line_num>", methods=["PUT"])
@require_admin
def api_update_host(line_num):
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    new_ip = data.get("ip", "").strip()
    new_hostnames = data.get("hostnames", "").strip()
    new_comment = data.get("comment", "").strip()

    if not new_ip or not new_hostnames:
        return jsonify({"success": False, "error": "IP 地址和主机名不能为空"}), 400
    if not validate_ip(new_ip):
        return jsonify({"success": False, "error": f"无效的 IP 地址: {new_ip}"}), 400
    for hostname in new_hostnames.split():
        if not validate_hostname(hostname):
            return jsonify({"success": False, "error": f"无效的主机名: {hostname}"}), 400

    backup_path = backup_hosts()
    try:
        with open(HOSTS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if line_num < 1 or line_num > len(lines):
            logger.warning("更新失败 | 行号 %d 超出范围 (1-%d)", line_num, len(lines))
            return jsonify({"success": False, "error": f"行号超出范围: {line_num}"}), 400
        new_line = f"{new_ip}\t{new_hostnames}"
        if new_comment:
            new_line += f"\t# {new_comment}"
        new_line += "\n"
        old_line = lines[line_num - 1].strip()
        lines[line_num - 1] = new_line
        with open(HOSTS_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
        dns_result = flush_dns_cache()
        logger.info("更新 | 第 %d 行: [%s] → [%s]", line_num, old_line, new_line.strip())
        return jsonify({
            "success": True,
            "message": "hosts 记录更新成功，已实时生效",
            "data": {"line": line_num, "old": old_line, "new": new_line.strip(), "backup": backup_path, "dns_flush": dns_result}
        })
    except PermissionError:
        logger.error("更新失败 | 权限不足 行号 %d", line_num)
        return jsonify({"success": False, "error": "权限不足！"}), 500
    except Exception as e:
        logger.error("更新失败 | 行号 %d: %s\n%s", line_num, e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/backup", methods=["POST"])
@require_admin
def api_backup_hosts():
    try:
        backup_path = backup_hosts()
        logger.info("手动备份 | 备份文件: %s", backup_path)
        return jsonify({"success": True, "message": "备份成功", "data": {"backup_path": backup_path}})
    except Exception as e:
        logger.error("手动备份失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/flush", methods=["POST"])
@require_admin
def api_flush_dns():
    try:
        dns_result = flush_dns_cache()
        logger.info("DNS 刷新 | 结果: %s", [(s, r) for s, r in dns_result])
        return jsonify({"success": True, "message": "DNS 缓存刷新完成", "data": {"results": dns_result}})
    except Exception as e:
        logger.error("DNS 刷新失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/backups", methods=["GET"])
@require_admin
def api_list_backups():
    """列出所有备份文件"""
    try:
        backups = []
        if os.path.exists(BACKUP_DIR):
            for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
                fpath = os.path.join(BACKUP_DIR, fname)
                if os.path.isfile(fpath):
                    stat = os.stat(fpath)
                    backups.append({
                        "filename": fname,
                        "path": fpath,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    })
        logger.info("备份列表 | 共 %d 个备份文件", len(backups))
        return jsonify({"success": True, "data": backups})
    except Exception as e:
        logger.error("列出备份失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hosts/restore", methods=["POST"])
@require_admin
def api_restore_hosts():
    """从指定备份文件还原 hosts"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    backup_filename = data.get("filename", "").strip()
    if not backup_filename:
        return jsonify({"success": False, "error": "未指定备份文件"}), 400

    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    # 安全检查：防止路径穿越
    if os.path.realpath(backup_path) != os.path.realpath(os.path.join(BACKUP_DIR, os.path.basename(backup_filename))):
        logger.warning("还原拒绝 | 非法路径: %s", backup_filename)
        return jsonify({"success": False, "error": "非法的备份文件名"}), 400

    if not os.path.isfile(backup_path):
        logger.warning("还原失败 | 备份文件不存在: %s", backup_path)
        return jsonify({"success": False, "error": f"备份文件不存在: {backup_filename}"}), 404

    try:
        # 还原前先自动备份当前 hosts
        current_backup = backup_hosts()
        logger.info("还原 | 当前 hosts 已备份至: %s", current_backup)

        # 读取备份内容写入 hosts
        with open(backup_path, "r", encoding="utf-8") as src:
            content = src.read()
        with open(HOSTS_FILE, "w", encoding="utf-8") as dst:
            dst.write(content)

        dns_result = flush_dns_cache()
        logger.info("还原 | 已从 %s 还原 hosts 文件 (%d 字节)，当前版本已备份至 %s",
                    backup_path, len(content), current_backup)
        return jsonify({
            "success": True,
            "message": f"已从备份 {backup_filename} 还原，已实时生效",
            "data": {
                "restored_from": backup_filename,
                "current_backup": current_backup,
                "dns_flush": dns_result
            }
        })
    except PermissionError:
        logger.error("还原失败 | 权限不足")
        return jsonify({"success": False, "error": "权限不足！请确保以 root 权限运行。"}), 500
    except Exception as e:
        logger.error("还原失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


# ===================== WSGI 路径前缀中间件 =====================
class PrefixMiddleware:
    """
    剥离 fnOS 网关 gatewayPrefix 路径前缀。

    网关通过 Unix Socket 反向代理转发请求时，URL 路径保持原样。
    例如：浏览器访问 https://NAS:5001/app/fnnas.hosts
          → nginx 转发到 unix:app.sock 时，PATH_INFO 仍是 /app/fnnas.hosts
          → Flask 路由 / 不匹配 → 404

    此中间件在请求到达 Flask 路由之前，去掉 /app/fnnas.hosts 前缀，
    让 Flask 的 @app.route("/") 等路由能正确匹配。
    """

    def __init__(self, app, prefix="/app/fnnas-hosts"):
        self.app = app
        self.prefix = prefix.rstrip("/")

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "")
        if path_info.startswith(self.prefix):
            # 剥离前缀，让 Flask 看到的是 /
            environ["PATH_INFO"] = path_info[len(self.prefix):] or "/"
            if environ.get("SCRIPT_NAME"):
                environ["SCRIPT_NAME"] += self.prefix
            else:
                environ["SCRIPT_NAME"] = self.prefix
        return self.app(environ, start_response)


# 应用路径前缀剥离（必须在路由注册之后、启动之前）
app.wsgi_app = PrefixMiddleware(app.wsgi_app)


# ===================== Unix Socket 网关代理 =====================
# 根据 fnOS 统一网关注册文档，应用通过 Unix Socket 接入网关。
# 网关会将 /app/fnnas.hosts 的请求转发到 target/app.sock，
# 并自动注入 X-Trim-* 身份 Header。
UNIX_SOCKET_PATH = os.path.join(TRIM_APPDEST, "app.sock")


def start_unix_socket_proxy():
    """在 target/app.sock 上监听 Unix Socket，透明代理到 Flask (127.0.0.1:5080)"""
    # 清理旧 socket 文件
    if os.path.exists(UNIX_SOCKET_PATH):
        os.unlink(UNIX_SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(UNIX_SOCKET_PATH)
    os.chmod(UNIX_SOCKET_PATH, 0o666)  # 允许网关进程读写
    server.listen(128)
    logger.info("Unix Socket 代理已启动 | %s → %s:%s", UNIX_SOCKET_PATH, APP_HOST, APP_PORT)

    def _forward(src, dst):
        """单向数据转发"""
        try:
            while True:
                data = src.recv(8192)
                if not data:
                    break
                dst.sendall(data)
        except (OSError, ConnectionError):
            pass

    def _handle_connection(client_sock):
        """处理单个 Unix Socket 客户端连接"""
        backend = None
        try:
            backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            backend.settimeout(30)
            backend.connect((APP_HOST, APP_PORT))
            backend.settimeout(None)

            t1 = threading.Thread(target=_forward, args=(client_sock, backend), daemon=True)
            t2 = threading.Thread(target=_forward, args=(backend, client_sock), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=60)
        except Exception as e:
            logger.debug("Socket代理错误: %s", e)
        finally:
            try:
                client_sock.close()
            except OSError:
                pass
            if backend:
                try:
                    backend.close()
                except OSError:
                    pass

    while True:
        try:
            client, _ = server.accept()
            threading.Thread(target=_handle_connection, args=(client,), daemon=True).start()
        except OSError as e:
            logger.error("Unix Socket 代理异常: %s", e)
            break


# ===================== 启动 =====================
_early_log("所有路由注册完成，准备启动服务")

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Hosts 管理器 v1.0.29 启动")
    logger.info("  HTTP:    %s:%s", APP_HOST, APP_PORT)
    logger.info("  Socket:  %s", UNIX_SOCKET_PATH)
    logger.info("  Dest:    %s", TRIM_APPDEST)
    logger.info("  TmpDir:  %s", TRIM_TMPDIR)
    logger.info("  VarDir:  %s", _PKGVAR)
    logger.info("  LogFile: %s", LOG_FILE)
    logger.info("  Hosts:   %s (存在=%s, 可写=%s)", 
                HOSTS_FILE, os.path.exists(HOSTS_FILE),
                os.access(HOSTS_FILE, os.W_OK) if os.path.exists(HOSTS_FILE) else "N/A")
    logger.info("  Root:    %s", os.geteuid() == 0)

    # fnOS 通过 privilege 配置以 root 身份运行
    if os.geteuid() != 0:
        logger.warning("非 root 权限运行，hosts 文件修改可能失败 (euid=%s)", os.geteuid())
    else:
        logger.info("以 root 权限运行 ✓")

    # 启动 Unix Socket 代理（后台线程）
    logger.info("启动 Unix Socket 代理线程...")
    proxy_thread = threading.Thread(target=start_unix_socket_proxy, daemon=True, name="UnixSocketProxy")
    proxy_thread.start()

    # 等待 socket 文件创建
    for _i in range(10):
        if os.path.exists(UNIX_SOCKET_PATH):
            logger.info("Socket 文件已创建: %s (权限:%s)", 
                        UNIX_SOCKET_PATH, oct(os.stat(UNIX_SOCKET_PATH).st_mode)[-3:])
            break
        import time as _time
        _time.sleep(0.1)

    logger.info("启动 Flask HTTP 服务 %s:%s ...", APP_HOST, APP_PORT)
    logger.info("=" * 50)
    logger.info(">> 启动完成！日志文件: %s", LOG_FILE)
    logger.info(">> 查看日志: cat %s", LOG_FILE)
    logger.info("=" * 50)
    app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)
