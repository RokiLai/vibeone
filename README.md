# VibeOne

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

**Continue your desktop AI coding sessions from your phone.**

VibeOne is a self-hosted web console for **OpenAI Codex CLI** and **Antigravity CLI** running on your own machine. Kick off a bug fix or refactor before stepping away from your desk, then monitor streaming progress, review Git diffs, approve permissions, and send follow-up prompts right from your phone. Your code, session history, and CLI credentials stay strictly on your local computer.

<p align="center">
  <img src="docs/demo/mobile-workflow.gif" width="250" alt="VibeOne mobile demo: open a running session, inspect Git diff, and send a follow-up prompt">
  <img src="docs/demo/sidebar.png" width="250" alt="VibeOne mobile sidebar: workspace groups, active runs, and pending approvals">
  <img src="docs/demo/ios-push.jpg" width="250" alt="VibeOne Web Push notification on iPhone Lock Screen">
</p>

> Demo captures use simulated task data. See also the [static session screenshot](docs/demo/mobile.png).

---

## Highlights

- **Keep coding away from your desk**: Stream live output, inspect Git diffs, approve tool permissions, and send follow-up instructions from any mobile browser. Add VibeOne to your iPhone Home Screen to receive native iOS Web Push notifications when tasks finish or need approval.
- **Manage multiple projects in parallel**: Switch between workspaces and sessions from one mobile-first sidebar, monitor background runs, and persist full session history locally in SQLite.
- **Unified multi-account & dual-engine control**: Switch seamlessly between multiple Codex and Antigravity accounts with background prewarming and per-account model/reasoning preferences.
- **Direct local official CLI integration**: Talks directly to `codex app-server --stdio` and `agy acp --stdio` over stdio ([Codex integration guide](https://developers.openai.com/blog/codex-as-a-platform) · [Antigravity ACP docs](https://antigravity.google/docs/cli/acp/)). Uses your own local CLI logins with zero third-party relay servers.

---

## Quick Start

### Prerequisites

- **Python 3.9+** and **Git**
- At least one of the following CLIs installed and signed in:
  - **OpenAI Codex CLI (`codex`)**: signed in via terminal (`~/.codex` or `OPENAI_API_KEY`)
  - **Antigravity CLI (`agy`)**: signed in via terminal (multi-account profiles in `~/.config/agy/profiles/<name>`)

### Install & Run

```bash
git clone https://github.com/RokiLai/vibeone.git
cd vibeone

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# Edit .env and set VIBEONE_WEB_TOKEN to a strong random secret

./start.sh
```

Open `http://127.0.0.1:8765` in your browser and sign in with `VIBEONE_WEB_TOKEN`.

---

## Mobile Remote Access & iPhone Push

> **Security note**: VibeOne is designed for single-user self-hosting. Once signed in, it has access to your local workspaces and logged-in agents. Keep VibeOne bound to loopback (`127.0.0.1:8765`) and expose it only over a private VPN or an access-controlled HTTPS reverse proxy. Codex `auto` mode can write to your workspace and access the network; `full-access` disables the file sandbox.

### Option 1: Tailscale (Recommended, zero domain setup)

1. Sign in to the same Tailscale network on both your computer and phone, and enable MagicDNS + HTTPS certificates in the Tailscale admin console.
2. Start VibeOne locally on `127.0.0.1:8765`, then run in another terminal:

   ```bash
   tailscale serve --bg http://127.0.0.1:8765
   ```

3. Open the `https://…ts.net` URL from your phone while connected to Tailscale and sign in with your token ([Tailscale Serve docs](https://tailscale.com/docs/reference/tailscale-cli/serve)).

### Option 2: Custom Domain + Caddy

1. Point your domain's DNS (`A`/`AAAA`) to your public host and ensure TCP 80/443 reach Caddy (if your ISP uses CGNAT without public IPv6, use Tailscale above).
2. Copy [Caddyfile.example](Caddyfile.example) to `Caddyfile`, replace the domain, and add your own ingress access control.
3. Run VibeOne and Caddy:

   ```bash
   ./start.sh
   caddy run --config Caddyfile
   ```

   Caddy automatically provisions and renews TLS certificates ([Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https)).

### Enable iPhone Push Notifications (iOS PWA)

1. Set `VIBEONE_VAPID_SUBJECT=mailto:your@domain.com` in `.env` (must use a real domain, never `@localhost`) and restart VibeOne.
2. Open your trusted HTTPS URL in iPhone Safari and tap **Share → Add to Home Screen**.
3. Launch VibeOne from the Home Screen icon, open Settings, and enable Web Push notifications.

---

## Environment Variables

Common settings (see [.env.example](.env.example) for a starter template):

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `VIBEONE_WEB_TOKEN` | Yes | None | Auth token for web login, HTTP API, and WebSocket |
| `VIBEONE_HOST` | No | `127.0.0.1` | Bind host |
| `VIBEONE_PORT` | No | `8765` | Bind port |
| `VIBEONE_WORKDIR` | No | Repo root | Default workspace directory |
| `VIBEONE_VAPID_SUBJECT` | No | `mailto:admin@example.com` | Contact email for Web Push VAPID (valid domain required) |

<details>
<summary>Show advanced configuration (modes, prewarming, CLI paths, LaunchAgent)</summary>

| Variable | Default | Description |
| --- | --- | --- |
| `VIBEONE_DEFAULT_MODE` | `auto` | Default permission mode when creating/resuming sessions |
| `VIBEONE_PREWARM_PROFILES` | `all` | Profiles to prewarm asynchronously on startup (`all`, comma-separated names, or `none`) |
| `VIBEONE_MAX_UPLOAD_BYTES` | `26214400` (25MB) | Max upload size per image |
| `VIBEONE_LAUNCHD_LABEL` | `local.vibeone` | macOS LaunchAgent label for `.restart_after_turn` deferred restarts |
| `AGY_PROFILE` | Auto-detected (prefers logged-in profile) | Default profile selected on first launch |
| `AGY_PROFILES_DIR` | `~/.config/agy/profiles` | Antigravity multi-account profiles directory |
| `AGY_BIN` / `AGY_REAL_BIN` | Auto-discovered from `~/.local/bin` and `PATH` | Custom `agy` binary path |
| `CODEX_BIN` | Auto-discovered from `PATH` / Homebrew | Custom `codex` binary path |
| `CODEX_HOME` | `~/.codex` | Custom Codex config directory |

> Binary discovery checks environment variables first, then `PATH` and common install locations—no code changes needed across machines.

</details>

---

## Development & Testing

```bash
# Backend syntax check
.venv/bin/python -m py_compile server.py vibeone/*.py vibeone/engines/*.py

# Run unit tests
.venv/bin/python -m unittest discover -s tests -v
```

- Frontend edits (`index.html`, `static/*`) take effect on browser refresh without restarting the server.
- Backend Python edits require a service restart; under a macOS LaunchAgent, creating `.restart_after_turn` triggers a clean restart after active turns and push notifications finish.

<details>
<summary>Project Structure</summary>

```text
vibeone/
├── server.py               # FastAPI entrypoint & HTTP / WebSocket API
├── start.sh                # Startup script (loads .env & PATH)
├── .env.example            # Environment variable template
├── index.html              # Frontend entrypoint
├── manifest.json           # PWA manifest
├── sw.js                   # Service Worker (offline shell cache & Web Push)
├── Caddyfile.example       # Caddy HTTPS reverse proxy example
├── requirements.txt        # Python dependencies
├── vibeone/
│   ├── config.py           # Env vars, binary discovery & profile resolution
│   ├── store.py            # SQLite persistence for sessions, items & metadata
│   ├── passkey.py          # WebAuthn / Passkey biometric login
│   ├── auth_flow.py        # In-browser CLI login flow manager
│   ├── push.py             # VAPID Web Push manager
│   ├── quota.py            # Account quota queries
│   └── engines/
│       ├── acp.py          # Antigravity ACP engine wrapper
│       ├── codex.py        # OpenAI Codex App-Server engine wrapper
│       └── pool.py         # Multi-account hot-switch pool & prewarming
├── static/                 # Modular frontend scripts & styles (app-*.js, app.css)
├── vendor/                 # Bundled frontend libraries (Tailwind CSS, marked, highlight.js, DOMPurify)
├── icons/                  # PWA icons
└── tests/                  # Unit tests
```

</details>

---

## License

VibeOne is released under the [MIT License](LICENSE). Bundled third-party frontend assets are distributed under [their respective licenses](vendor/README.md).
