[RELEASE.md](https://github.com/user-attachments/files/28292606/RELEASE.md)
# Hosts管理器 v1.0.61

> 飞牛NAS（fnOS）`/etc/hosts` 文件可视化管理工具 —— 纯 Python + 原生前端，无框架依赖

---

##  应用简介

Hosts管理器 是一款专为飞牛NAS打造的 hosts 文件管理应用。通过直观的 Web 界面对 `/etc/hosts` 进行查看、新增、编辑、删除、批量导入、搜索过滤、备份还原，并支持一键刷新 DNS 缓存，修改后即时生效。

- **作者**：豪子
- **社区**：[https://club.fnnas.com/forum.php?mod=viewthread&tid=64553&extra=page%3D1](https://club.fnnas.com/forum.php?mod=viewthread&tid=64553&extra=page%3D1)

---

## ⚙️ 技术公开

### 技术栈

| 层级 | 技术方案 |
|------|----------|
| 后端 | Python 3.11 + Flask 3.1.2 |
| 前端 | 原生 HTML / CSS / JavaScript（无任何框架） |
| 通信 | Unix Socket 代理 → Flask HTTP (127.0.0.1:5080) |
| 权限 | 以 root 运行，飞牛网关身份透传 + 管理员校验 |

### 部署架构

```
浏览器 → nginx (fnOS 端口5001)
       → /app/fnnas-hosts → Unix Socket (app.sock)
       → Python Flask (127.0.0.1:5080)
       → /etc/hosts 文件读写
```

### 项目结构

```
fn-hosts/
├── app/
│   ├── app.py             # Flask 后端服务（核心逻辑）
│   ├── index.html          # 前端单页面（完整 UI + JS）
│   ├── requirements.txt    # Flask + Werkzeug
│   └── ui/                 # 桌面图标配置
├── cmd/                    # 应用生命周期脚本（安装/卸载/升级）
├── config/                 # 权限与资源声明
├── manifest                # fnOS 应用清单
└── LICENSE                 # MIT 开源协议
```

---

## ✨ 功能特性

- **查看记录** — 表格形式展示 `/etc/hosts` 所有条目（行号、IP、主机名、注释）
- **新增 / 编辑** — 单条添加或批量导入，支持 IPv4 和 IPv6
- **删除** — 行内删除 + 二次确认弹窗，安全防误触
- **搜索过滤** — 按 IP 或域名实时过滤表格
- **备份与还原** — 手动/自动备份到 `/etc/hosts_backups/`，随时从历史备份恢复
- **刷新 DNS 缓存** — 支持 systemd-resolved / nscd / dnsmasq，修改后即时生效
- **管理员权限检测** — 非管理员用户 403 + 完整友好提示
- **响应式布局** — `@media` 适配移动端
- **键盘快捷键** — Escape 关闭弹窗，Enter 提交表单

---

## 📦 安装要求

| 项目 | 说明 |
|------|------|
| 系统 | 飞牛 NAS（fnOS ≥ 0.9.27） |
| 运行时 | Python 3.11 |
| 权限 | root 安装，以 root 运行 |
| 端口 | 5080 |

---

## 📥 安装方法

1. 下载 `fnnas.hosts.fpk`
2. 飞牛 NAS → 应用中心 → 手动安装 → 选择 fpk 文件
3. 安装完成后在桌面打开「Hosts管理器」

---

## 📝 使用说明

- **新增记录**：点击「新增记录」，填写 IP 和主机名（多个主机名空格分隔）
- **批量导入**：点击「批量模式」，粘贴 `IP 主机名 #注释` 格式的多行记录
- **编辑**：点击对应行的「编辑」按钮，修改后保存
- **备份**：点击「备份」手动备份，每次修改操作也会自动备份
- **还原**：点击「还原」，从历史备份列表中选择并恢复
- **刷新缓存**：修改 hosts 后点击「刷新缓存」使 DNS 立即生效

---

##  更新日志

### v1.0.61
- 增加系统保留 IP 保护机制
- 禁止修改和删除以下系统默认记录：`127.0.0.1`、`127.0.1.1`、`::1`、`ff02::1`、`ff02::2`
- 前端表格对受保护记录隐藏编辑/删除按钮，显示"系统保留"
- 后端接口增加受保护 IP 校验，返回 403 拒绝

### v1.0.60
- LICENSE 更新为全中文 Apache 2.0 协议
- LICENSE 附录填入应用详细信息（名称、版本、作者、技术栈、功能特性）

### v1.0.59
- 新增 `_themeSource` 主题来源追踪，防止 OS 级 matchMedia 变化覆盖父窗口检测到的主题
- 新增父窗口 `html[theme-mode]` 属性检测（部分实现放在 html 上）
- 新增父窗口 `html.theme-dark` / `.theme-light` class 兼容
- 新增父窗口 CSS 变量 `--semi-color-bg-0` 亮度检测兜底（最可靠方案）
- 父窗口 MutationObserver 同步增强为多属性监听

### v1.0.58
- 修复 CSS `:root` 语法（之前误写为 `::root`）
- 修复 Cookie 主题检测正则表达式双转义问题（`\\s` → `\s`）
- 修复 MutationObserver 在 `<head>` 中执行时 `document.body` 为 null 导致无法安装自身主题监听

### v1.0.57
- 主题检测全面重写：新增 URL 参数 `?theme=dark|light` 和 Cookie `fnos_theme` 检测（fnOS 官方论坛建议方案）
- 优化多级优先级策略：URL参数 → Cookie → 父窗口DOM → 系统matchMedia，确保正确识别系统主题
- 新增主题缓存机制，避免重复设置相同主题

### v1.0.56
- 修复亮色主题 bug：`::root` CSS 语法错误改为 `:root`（导致亮色 CSS 变量全部失效）
- 主题检测 JS 重写：优先检测 fnOS 的 `body[theme-mode=dark]` 机制，兼容 `.dark` class / data-theme / 背景亮度等多种方案
- 修复 MutationObserver 同时监听 `body[theme-mode]` 和 `html class` 变化

### v1.0.55
- 移动端表格响应式适配：≤600px 屏幕自动切换为 card 卡片布局，带行号和字段标签
- PC 端布局完全不受影响

### v1.0.54
- 彻底放弃 venv（fnOS 缺少 math 等 C 扩展），改用 `pip --target vendor/` 本地隔离 + 系统 Python 直接运行

### v1.0.46
- 修复 `body:not(.dark)` 选择器错误（`.dark` 在 `html` 上不在 `body` 上），亮模式样式彻底生效

### v1.0.45
- 同上（选择器修复的迭代版本）

### v1.0.44
- 亮模式主背景改为纯白（`#fff`），更加干净明亮
- 重新适配亮模式文字、边框、悬停色层次
- 白底卡片增加微弱阴影区分卡片与背景
- 滚动条颜色跟随主题

### v1.0.43
- 亮模式恢复原始配色方案（`#f0f2f5` 体系），暗模式保持不变

### v1.0.41 - v1.0.42
- 亮/暗模式颜色全面适配 fnOS Tailwind 体系
- 修复硬编码颜色，scrollbar / placeholder / 毛玻璃等细节优化

### v1.0.39 - v1.0.40
- 深色模式改为 `.dark` class 方案，与 fnOS 主题系统对齐
- 新增 MutationObserver 实时跟随父窗口主题切换

---

##  反馈 & 社区

> 技术讨论 & 问题反馈：[https://club.fnnas.com/forum.php?mod=viewthread&tid=64553&extra=page%3D1](https://club.fnnas.com/forum.php?mod=viewthread&tid=64553&extra=page%3D1)  
> 作者：豪子  
> 协议：MIT License
