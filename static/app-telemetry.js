    // =========================================================================
    // Lightweight Frontend Telemetry, Behavior & UI Jank Trace Chain
    // =========================================================================
    const VibeTelemetry = (() => {
      const CID_KEY = 'vibeone_cid_v1';
      const MAX_RING = 200;
      const MAX_QUEUE = 120;
      const FLUSH_BATCH_SIZE = 20;
      const FLUSH_INTERVAL_MS = 5000;
      const TRACE_TTL_MS = 5000;
      const ACTION_TTL_MS = 10000;
      const JANK_WARN_MS = 120;
      const JANK_FREEZE_MS = 300;
      const JANK_MIN_GAP_MS = 400;
      const LONGTASK_MIN_GAP_MS = 500;
      const SLOW_API_MS = 500;

      function randId(len) {
        const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
        let out = '';
        for (let i = 0; i < len; i++) out += chars[Math.floor(Math.random() * chars.length)];
        return out;
      }

      function loadOrCreateCid() {
        try {
          if (typeof window !== 'undefined' && window.localStorage) {
            const existing = window.localStorage.getItem(CID_KEY);
            if (existing && /^[A-Za-z0-9_.-]{4,32}$/.test(existing)) return existing;
            const created = `c_${randId(8)}`;
            window.localStorage.setItem(CID_KEY, created);
            return created;
          }
        } catch (_) {}
        return `c_${randId(8)}`;
      }

      const cid = loadOrCreateCid();
      const pageId = `p_${randId(6)}`;
      const ring = [];
      const queue = [];
      let flushTimer = null;
      let flushInFlight = false;
      let authBackoffUntil = 0;

      let activeTrace = { id: '', label: '', ts: 0 };
      let lastClickSig = '';
      let lastClickTs = 0;

      const wsMsgTimestamps = [];
      let lastJankReportAt = 0;
      let suppressedJankCount = 0;
      let suppressedMaxGap = 0;
      let lastLongTaskAt = 0;
      let suppressedLongTasks = 0;

      const origFetch =
        typeof window !== 'undefined' && typeof window.fetch === 'function'
          ? window.fetch.bind(window)
          : null;

      const nowMs = () => Date.now();
      const perfNow = () =>
        typeof performance !== 'undefined' && typeof performance.now === 'function'
          ? performance.now()
          : Date.now();

      function currentSessionContext() {
        const st = typeof state === 'object' && state ? state : null;
        return {
          session_id: st && st.currentSessionId ? String(st.currentSessionId) : '',
          profile: st && st.currentProfile ? String(st.currentProfile) : '',
        };
      }

      function startTrace(label = '') {
        const id = `t_${Date.now().toString(36)}_${randId(4)}`;
        activeTrace = { id, label: String(label || '').slice(0, 64), ts: nowMs() };
        return id;
      }

      function getActiveTraceId(maxAgeMs = TRACE_TTL_MS) {
        return activeTrace.id && nowMs() - activeTrace.ts <= maxAgeMs ? activeTrace.id : '';
      }

      function getLastAction(maxAgeMs = ACTION_TTL_MS) {
        return activeTrace.label && nowMs() - activeTrace.ts <= maxAgeMs ? activeTrace.label : '';
      }

      function noteWsMessage() {
        const t = nowMs();
        wsMsgTimestamps.push(t);
        const cutoff = t - 1000;
        while (wsMsgTimestamps.length > 0 && wsMsgTimestamps[0] < cutoff) wsMsgTimestamps.shift();
        if (wsMsgTimestamps.length > 400) wsMsgTimestamps.splice(0, wsMsgTimestamps.length - 400);
      }

      function getWsMsgRate1s() {
        const cutoff = nowMs() - 1000;
        while (wsMsgTimestamps.length > 0 && wsMsgTimestamps[0] < cutoff) wsMsgTimestamps.shift();
        return wsMsgTimestamps.length;
      }

      function getBootMeta() {
        const nav = typeof navigator !== 'undefined' ? navigator : {};
        const win = typeof window !== 'undefined' ? window : {};
        const standalone = Boolean(
          (win.matchMedia && win.matchMedia('(display-mode: standalone)').matches) || nav.standalone === true
        );
        return {
          vw: win.innerWidth || 0,
          vh: win.innerHeight || 0,
          dpr: Number(win.devicePixelRatio || 1),
          standalone,
          online: nav.onLine !== false,
          lang: nav.language || '',
          platform: nav.platform || '',
        };
      }

      function track(event, data = null, opts = {}) {
        if (!event) return null;
        const ctx = currentSessionContext();
        const traceId = opts.traceId !== undefined ? String(opts.traceId || '') : getActiveTraceId();
        const rec = {
          ts: nowMs(),
          event: String(event).slice(0, 64),
          cat: String(opts.cat || 'behavior').slice(0, 32),
          level: String(opts.level || 'info').slice(0, 16),
          trace_id: traceId,
          session_id: opts.sessionId !== undefined ? String(opts.sessionId || '') : ctx.session_id,
          profile: opts.profile !== undefined ? String(opts.profile || '') : ctx.profile,
        };
        if (typeof opts.durMs === 'number' && Number.isFinite(opts.durMs)) {
          rec.dur_ms = Math.round(opts.durMs * 100) / 100;
        }
        if (data && typeof data === 'object') rec.data = data;

        ring.push(rec);
        if (ring.length > MAX_RING) ring.shift();
        queue.push(rec);
        if (queue.length > MAX_QUEUE) queue.shift();

        if (queue.length >= FLUSH_BATCH_SIZE) {
          flush(false);
        } else if (!flushTimer && typeof setTimeout === 'function') {
          flushTimer = setTimeout(() => {
            flushTimer = null;
            flush(false);
          }, FLUSH_INTERVAL_MS);
        }
        return rec;
      }

      function recordSpan(event, durMs, data = null, opts = {}) {
        const warnThreshold = Number(opts.warnMs ?? 50);
        const isSlow = durMs >= warnThreshold;
        const level = isSlow ? 'warn' : (opts.level || 'info');
        const evName = isSlow && opts.slowEvent ? opts.slowEvent : event;
        return track(evName, data, { ...opts, cat: opts.cat || 'perf', level, durMs });
      }

      function canSendToServer() {
        if (nowMs() < authBackoffUntil) return false;
        const st = typeof state === 'object' && state ? state : null;
        if (st && st.manualClose) return false;
        if (typeof els === 'object' && els && els.authOverlay && els.authOverlay.classList) {
          if (!els.authOverlay.classList.contains('hidden')) return false;
        }
        return true;
      }

      function flush(useBeacon = false) {
        if (!queue.length || !canSendToServer()) return;
        if (flushInFlight && !useBeacon) return;
        const batch = queue.splice(0, 40);
        const payload = JSON.stringify({
          cid,
          page_id: pageId,
          meta: batch.some((e) => e.event === 'app_boot') ? getBootMeta() : undefined,
          events: batch,
        });

        if (useBeacon && typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
          try {
            if (navigator.sendBeacon('/api/telemetry', new Blob([payload], { type: 'application/json' }))) return;
          } catch (_) {}
        }

        if (!origFetch) return;
        flushInFlight = true;
        origFetch('/api/telemetry', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-VibeOne-Client': cid },
          credentials: 'same-origin',
          keepalive: Boolean(useBeacon),
          body: payload,
        })
          .then((res) => {
            if (res && (res.status === 401 || res.status === 403)) authBackoffUntil = nowMs() + 30000;
          })
          .catch(() => {})
          .finally(() => {
            flushInFlight = false;
            if (queue.length >= FLUSH_BATCH_SIZE) flush(false);
          });
      }

      function safePathInfo(rawUrl) {
        try {
          const base = typeof location !== 'undefined' ? location.origin : 'http://localhost';
          const u = new URL(String(rawUrl), base);
          if (typeof location !== 'undefined' && u.origin !== location.origin) return null;
          const keys = Array.from(new Set(Array.from(u.searchParams.keys()))).slice(0, 8);
          return { path: u.pathname, qkeys: keys.join(',') };
        } catch (_) {
          return null;
        }
      }

      function shouldLogApiPath(path, method) {
        if (method !== 'GET') return true;
        return (
          path === '/api/sessions' ||
          path.startsWith('/api/sessions/') ||
          path.startsWith('/api/git/') ||
          path.startsWith('/api/file') ||
          path.startsWith('/api/profiles') ||
          path === '/api/auth/check'
        );
      }

      function installFetchInstrumentation() {
        if (!origFetch || typeof window === 'undefined') return;
        window.fetch = function instrumentedFetch(input, init) {
          const rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
          const info = safePathInfo(rawUrl);
          if (!info || !info.path.startsWith('/api/') || info.path.startsWith('/api/telemetry')) {
            return origFetch(input, init);
          }

          const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
          const traceId = getActiveTraceId() || (method !== 'GET' ? startTrace(`api:${info.path}`) : '');
          const nextInit = init ? { ...init } : {};
          try {
            const headers = new Headers(
              nextInit.headers || (typeof Request !== 'undefined' && input instanceof Request ? input.headers : undefined)
            );
            if (cid && !headers.has('X-VibeOne-Client')) headers.set('X-VibeOne-Client', cid);
            if (traceId && !headers.has('X-VibeOne-Trace')) headers.set('X-VibeOne-Trace', traceId);
            nextInit.headers = headers;
          } catch (_) {}

          const started = perfNow();
          return origFetch(input, nextInit).then(
            (res) => {
              const durMs = perfNow() - started;
              if (!res.ok) {
                track('api_error', { method, path: info.path, status: res.status, q: info.qkeys }, {
                  cat: 'network',
                  level: res.status >= 500 ? 'error' : 'warn',
                  durMs,
                  traceId,
                });
              } else if (durMs >= SLOW_API_MS) {
                track('api_slow', { method, path: info.path, status: res.status, q: info.qkeys }, {
                  cat: 'network',
                  level: 'warn',
                  durMs,
                  traceId,
                });
              } else if (shouldLogApiPath(info.path, method)) {
                track('api_req', { method, path: info.path, status: res.status }, {
                  cat: 'network',
                  level: 'info',
                  durMs,
                  traceId,
                });
              }
              return res;
            },
            (err) => {
              const durMs = perfNow() - started;
              if (!err || err.name !== 'AbortError') {
                track('api_error', { method, path: info.path, err: String(err && err.message || err || 'fetch_failed').slice(0, 120) }, {
                  cat: 'network',
                  level: 'error',
                  durMs,
                  traceId,
                });
              }
              throw err;
            }
          );
        };
      }

      function describeClickTarget(target) {
        if (!target || typeof target.closest !== 'function') return null;
        const el = target.closest('button, a, summary, [role="button"], select, [data-telemetry]');
        if (!el) return null;
        const explicit = el.getAttribute && el.getAttribute('data-telemetry');
        const tag = (el.tagName || '').toLowerCase();
        const role = explicit || (el.id ? `#${el.id}` : tag);
        let label = explicit || (el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label'))) || '';
        if (!label && tag !== 'select') label = String(el.textContent || '').replace(/\s+/g, ' ').trim();
        return { target: role.slice(0, 48), label: label.slice(0, 32) };
      }

      function installClickTracking() {
        if (typeof document === 'undefined' || typeof document.addEventListener !== 'function') return;
        document.addEventListener('click', (ev) => {
          const desc = describeClickTarget(ev.target);
          if (!desc) return;
          const sig = `${desc.target}:${desc.label}`;
          const t = nowMs();
          if (sig === lastClickSig && t - lastClickTs < 250) return;
          lastClickSig = sig;
          lastClickTs = t;
          const traceId = startTrace(desc.target);
          track('ui_click', desc, { cat: 'behavior', level: 'info', traceId });
        }, { capture: true, passive: true });
      }

      function installErrorTracking() {
        if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
        window.addEventListener('error', (ev) => {
          track('js_error', {
            msg: String(ev.message || 'script_error').slice(0, 200),
            src: String(ev.filename || '').split('/').slice(-2).join('/'),
            line: ev.lineno || 0,
            col: ev.colno || 0,
            action: getLastAction(),
          }, { cat: 'error', level: 'error' });
          flush(false);
        });
        window.addEventListener('unhandledrejection', (ev) => {
          const r = ev && ev.reason;
          const msg = r && r.message ? `${r.name || 'Error'}: ${r.message}` : String(r || 'unhandled_rejection');
          track('unhandled_rejection', {
            msg: msg.slice(0, 220),
            stack: r && r.stack ? String(r.stack).slice(0, 300) : '',
            action: getLastAction(),
          }, { cat: 'error', level: 'error' });
          flush(false);
        });
      }

      function installJankMonitor() {
        if (typeof window === 'undefined') return;
        if (typeof PerformanceObserver === 'function') {
          try {
            const obs = new PerformanceObserver((list) => {
              for (const entry of (list.getEntries ? list.getEntries() : [])) {
                const dur = Number(entry.duration || 0);
                if (dur < 50) continue;
                const t = nowMs();
                if (t - lastLongTaskAt < LONGTASK_MIN_GAP_MS) {
                  suppressedLongTasks += 1;
                  continue;
                }
                const extra = suppressedLongTasks;
                suppressedLongTasks = 0;
                lastLongTaskAt = t;
                track(dur >= JANK_FREEZE_MS ? 'jank_freeze' : 'long_task', {
                  src: 'longtask',
                  action: getLastAction(),
                  ws_1s: getWsMsgRate1s(),
                  suppressed: extra,
                  items: typeof state === 'object' && state && state.items ? state.items.length : 0,
                }, { cat: 'jank', level: dur >= JANK_FREEZE_MS ? 'error' : 'warn', durMs: dur });
              }
            });
            obs.observe({ entryTypes: ['longtask'] });
          } catch (_) {}
        }

        if (typeof window.requestAnimationFrame !== 'function') return;
        let rafId = null;
        let lastFrameTs = 0;
        let warmupFrames = 0;

        function step(ts) {
          rafId = null;
          if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
            lastFrameTs = 0;
            return;
          }
          if (lastFrameTs > 0 && warmupFrames <= 0) {
            const gap = ts - lastFrameTs;
            if (gap >= JANK_WARN_MS && gap < 15000) {
              const t = nowMs();
              if (t - lastJankReportAt >= JANK_MIN_GAP_MS) {
                const mergedMax = Math.max(gap, suppressedMaxGap);
                const mergedCnt = suppressedJankCount;
                suppressedJankCount = 0;
                suppressedMaxGap = 0;
                lastJankReportAt = t;
                const isFreeze = mergedMax >= JANK_FREEZE_MS;
                track(isFreeze ? 'jank_freeze' : 'jank_frame', {
                  src: 'raf',
                  action: getLastAction(),
                  ws_1s: getWsMsgRate1s(),
                  busy: Boolean(typeof state === 'object' && state && state.isBusy),
                  items: typeof state === 'object' && state && state.items ? state.items.length : 0,
                  suppressed: mergedCnt,
                }, { cat: 'jank', level: isFreeze ? 'error' : 'warn', durMs: mergedMax });
              } else {
                suppressedJankCount += 1;
                if (gap > suppressedMaxGap) suppressedMaxGap = gap;
              }
            }
          } else if (warmupFrames > 0) {
            warmupFrames -= 1;
          }
          lastFrameTs = ts;
          rafId = window.requestAnimationFrame(step);
        }

        function startRaf() {
          if (rafId !== null) return;
          lastFrameTs = 0;
          warmupFrames = 2;
          rafId = window.requestAnimationFrame(step);
        }
        function stopRaf() {
          if (rafId !== null && typeof window.cancelAnimationFrame === 'function') window.cancelAnimationFrame(rafId);
          rafId = null;
          lastFrameTs = 0;
        }

        let hiddenAt = 0;
        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
          if (document.visibilityState !== 'hidden') startRaf();
          document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') {
              hiddenAt = nowMs();
              stopRaf();
              track('page_hidden', null, { cat: 'lifecycle', level: 'info' });
              flush(true);
            } else {
              const hiddenMs = hiddenAt ? nowMs() - hiddenAt : 0;
              hiddenAt = 0;
              startRaf();
              track('page_visible', { hidden_ms: hiddenMs }, { cat: 'lifecycle', level: 'info' });
            }
          });
        }
        if (typeof window.addEventListener === 'function') {
          window.addEventListener('pagehide', () => flush(true));
        }
      }

      installFetchInstrumentation();
      installClickTracking();
      installErrorTracking();
      installJankMonitor();
      track('app_boot', getBootMeta(), { cat: 'lifecycle', level: 'info' });

      return {
        getClientId: () => cid,
        getPageId: () => pageId,
        startTrace,
        getActiveTraceId,
        getLastAction,
        noteWsMessage,
        track,
        recordSpan,
        flush,
        getRecent: (limit = 50) => ring.slice(-Math.max(1, Number(limit) || 50)),
      };
    })();

    if (typeof window !== 'undefined') window.VibeTelemetry = VibeTelemetry;
