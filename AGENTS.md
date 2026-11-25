# VibeOne — Project Rules

## Service Restart
- Frontend edits take effect on browser refresh. Restart the service after backend edits.
- For a LaunchAgent deployment, set `VIBEONE_LAUNCHD_LABEL` to the installed service label. The `.restart_after_turn` marker requests a restart after active turns and push notifications finish.

## Verification & Environment
- Python runtime is `.venv/bin/python` (Python 3.9); run `.venv/bin/python -m py_compile server.py` after backend edits.
- Validate JS syntax in `index.html` and `sw.js` using `/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc`.
- Supported agent engines: Antigravity CLI (`agy acp --stdio`) and OpenAI Codex (`codex app-server --stdio`).

## Platform & Safety Invariants
- **Sidebar removal safety**: Removing a workspace directory or session from the sidebar must NEVER delete local filesystem files or directories—only update SQLite/sidebar tracking state.
- **History preservation**: Prefer `session/resume` / `thread/resume(excludeTurns=True)` whenever SQLite already stores items for a session; never let history replay overwrite existing local user messages/images.
- **iOS Standalone PWA layout**: Keep `<meta name="apple-mobile-web-app-status-bar-style" content="black">` (do NOT change to `black-translucent`, which causes a 59pt bottom dead zone in iOS WKWebView).
- **iOS Web Push**:
  - In `sw.js`, ALWAYS call `self.registration.showNotification(...)` on `push` events (foreground anti-disturb suppression must be enforced server-side so Apple WebKit does not revoke subscriptions).
  - In `pywebpush`, pass a `Vapid` object (`Vapid.from_pem(...)`) instead of a raw PEM string, and use a valid domain for `VIBEONE_VAPID_SUBJECT` (never `@localhost`).
