# VibeOne

Mobile-first 多 AI 编程远程控制台，通过 Web / iOS PWA 远程操控本机的 **Antigravity CLI (`agy`)** 与 **OpenAI Codex (`codex`)**，让你在手机或任意浏览器上随时查看进度、审批权限、切换账号和继续编程。

## 核心特性

- **双引擎支持 & 后台预热秒切**
  - 同时支持 **Antigravity CLI**（`agy acp --stdio`，含多账号 profile 切换）与 **OpenAI Codex**（`codex app-server --stdio`）。
  - 服务启动时后台预热已登录的账号，可通过 `VIBEONE_PREWARM_PROFILES` 限定范围。切换账号时可保留后台任务。
- **模型与推理强度独立记忆**
  - 按 Agent / 账号（如 `agy2`、`agy_r`、`codex`）分别持久化最近一次选择的模型与推理档位（如 `Gemini 4 Neon (Medium)`、`GPT-5.4 (xhigh)`），重启服务、重新打开前端或切换会话时自动恢复。
- **多工作目录 & 多会话并发**
  - 支持在多个项目目录间自由切换、浏览文件与新建目录，各会话独立运行互不阻塞。
  - SQLite 本地持久化聊天记录、上传图片预览、Token 用量与待发送队列；从侧边栏移除目录或会话仅更新本地记录，绝不删除磁盘文件。
- **移动端优先的交互体验**
  - 实时 WebSocket 流式输出、Markdown 渲染与代码高亮、可折叠带行号的 Diff 视图、消息引用回复、图片上传、工具调用生命周期展示与权限审批弹窗。
  - 支持 **iOS Standalone PWA**（添加到主屏幕全屏使用）与 **Web Push** 系统通知（任务完成、等待审批或异常中断时推送，前台自动免打扰）。
- **余量查询与安全认证**
  - 内置 Antigravity 多账号余量与 Codex 额度查询。
  - Token 登录签发 HttpOnly 会话 Cookie，内置登录失败速率限制与静态资源白名单。

## 目录结构

```text
vibeone/
├── server.py               # FastAPI 入口与 HTTP / WebSocket API
├── start.sh                # 一键启动脚本（自动加载 .env 与 PATH）
├── index.html              # 前端入口页面
├── manifest.json           # PWA Manifest
├── sw.js                   # Service Worker（Web Push 通知）
├── Caddyfile.example       # Caddy HTTPS 反向代理配置示例
├── requirements.txt        # Python 依赖
├── vibeone/
│   ├── config.py           # 环境变量、二进制路径与多账号配置解析
│   ├── store.py            # SQLite 会话、消息与元数据持久化
│   ├── push.py             # VAPID Web Push 推送管理
│   ├── quota.py            # 账号余量查询
│   └── engines/
│       ├── acp.py          # Antigravity ACP 引擎封装
│       ├── codex.py        # OpenAI Codex App-Server 引擎封装
│       └── pool.py         # 多账号热切换连接池与后台预热
├── static/                 # 前端逻辑与样式 (app.js, app.css)
├── vendor/                 # 本地前端依赖 (marked, highlight.js, DOMPurify)
├── icons/                  # PWA 图标资源
└── tests/                  # 单元测试
```

## 快速开始

### 1. 环境要求

- Python 3.9+
- 至少安装并登录以下 CLI 之一：
  - **Antigravity CLI (`agy`)**：确保已在终端完成登录，多账号配置位于 `~/.config/agy/profiles/<name>`
  - **OpenAI Codex CLI (`codex`)**：确保已在终端完成登录（`~/.codex` 或 `OPENAI_API_KEY`）

### 2. 安装依赖

```bash
git clone https://github.com/RokiLai/vibeone.git
cd vibeone

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. 配置 `.env`

在项目根目录创建 `.env`（已被 `.gitignore` 忽略）：

```dotenv
# 必填：登录访问密钥（建议使用高强度随机字符串）
VIBEONE_WEB_TOKEN=your-strong-random-token

# 可选：监听地址与端口（默认 127.0.0.1:8765）
VIBEONE_HOST=127.0.0.1
VIBEONE_PORT=8765

# 可选：默认工作目录（默认当前项目目录）
VIBEONE_WORKDIR=/path/to/your/workspace

# 可选：启用 Web Push 时，填写部署者的联系邮箱
# VIBEONE_VAPID_SUBJECT=mailto:admin@example.com
```

### 4. 启动服务

```bash
./start.sh
```

启动后在浏览器访问 `http://127.0.0.1:8765`（或你配置的端口），输入 `VIBEONE_WEB_TOKEN` 即可使用。

## 环境变量说明

| 变量名 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `VIBEONE_WEB_TOKEN` | 是 | 无 | Web 登录与 API/WS 鉴权令牌 |
| `VIBEONE_HOST` | 否 | `127.0.0.1` | 监听地址 |
| `VIBEONE_PORT` | 否 | `8765` | 监听端口 |
| `VIBEONE_WORKDIR` | 否 | 项目根目录 | 默认打开的工作目录 |
| `VIBEONE_DEFAULT_MODE` | 否 | `auto` | 新建/恢复会话时的默认权限模式 |
| `VIBEONE_PREWARM_PROFILES` | 否 | `all`（扫描全部已登录账号） | 启动时后台异步启动的账号列表（默认扫描全部已登录账号并并发异步启动，不分主次；也可逗号分隔指定账号，或设为 `none` / `off` 关闭自动启动） |
| `VIBEONE_VAPID_SUBJECT` | 否 | `mailto:admin@example.com` | Web Push VAPID 联系邮箱（需使用合法域名，不能用 `@localhost`） |
| `VIBEONE_MAX_UPLOAD_BYTES` | 否 | `26214400` (25MB) | 单张图片上传大小上限 |
| `VIBEONE_LAUNCHD_LABEL` | 否 | `local.vibeone` | macOS LaunchAgent 服务标识（用于 `.restart_after_turn` 自动重启） |
| `AGY_PROFILE` | 否 | 自动探测（优先 `agy2`） | 首次启动默认选中的账号 |
| `AGY_PROFILES_DIR` | 否 | `~/.config/agy/profiles` | Antigravity 多账号目录 |
| `AGY_BIN` / `AGY_REAL_BIN` | 否 | 自动从 `~/.local/bin` 和 `PATH` 查找 | 指定 `agy` 可执行文件路径 |
| `CODEX_BIN` | 否 | 自动从 `PATH` / Homebrew 查找 | 指定 `codex` 可执行文件路径 |
| `CODEX_HOME` | 否 | `~/.codex` | 指定 Codex 配置目录 |

> 路径查找顺序为「环境变量优先 → `PATH` 与常见目录自动发现」，换机部署无需修改源码。

## HTTPS 远程访问与 iOS PWA

VibeOne 面向单用户自托管。Web 登录后可以访问本机工作区、会话和已登录的 Agent。保留服务的回环监听，通过 VPN 或带访问控制的 HTTPS 代理提供远程访问，仅向受信任的用户和设备开放。Codex 的 `auto` 模式允许写入工作区和联网，无需逐次审批。`full-access` 会关闭 Codex 的文件沙箱。

iOS Web Push 需要 HTTPS。将 `VIBEONE_VAPID_SUBJECT` 设置为部署者的实际联系邮箱，然后按以下步骤配置。

1. 参考 [Caddyfile.example](Caddyfile.example) 复制为 `Caddyfile`，替换为你的域名与本地端口。
2. 启动 VibeOne 与 Caddy：
   ```bash
   ./start.sh
   caddy run --config Caddyfile
   ```
3. 在 iPhone Safari 中打开你的 HTTPS 域名 → 点击分享按钮 → **添加到主屏幕**。
4. 从主屏幕打开 VibeOne，在设置中开启推送通知即可在后台接收任务完成与权限审批提醒。

## 开发与测试

```bash
# 后端语法检查
.venv/bin/python -m py_compile server.py

# 运行单元测试
.venv/bin/python -m unittest discover -s tests -v
```

- 修改前端文件（`index.html`、`static/app.js`、`static/app.css`）刷新浏览器即可生效，无需重启后端。
- 修改后端 Python 代码后需重启服务生效；如果通过 macOS LaunchAgent 托管运行，可在项目根目录创建 `.restart_after_turn` 触发当前回合结束后的无损自动重启。

## 许可证

VibeOne 采用 [MIT 许可证](LICENSE)。随项目分发的第三方依赖遵循[各自的许可证](vendor/README.md)。
