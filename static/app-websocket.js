    // =========================================================================
    // WebSocket Lifecycle (Heartbeat + Pong Timeout + Mobile/Network Recovery)
    // =========================================================================
    const WS_HEARTBEAT_INTERVAL = 20000;
    const WS_PONG_TIMEOUT = 4000;
    const WS_WAKE_PONG_TIMEOUT = 2500;
    const WS_CONNECT_TIMEOUT = 8000;

    function clearWsTimers() {
      if (state.heartbeatTimer) {
        clearInterval(state.heartbeatTimer);
        state.heartbeatTimer = null;
      }
      if (state.pongTimer) {
        clearTimeout(state.pongTimer);
        state.pongTimer = null;
      }
      if (state.connectTimer) {
        clearTimeout(state.connectTimer);
        state.connectTimer = null;
      }
    }

    function isAuthRequired() {
      return state.manualClose || !els.authOverlay.classList.contains('hidden');
    }

    function forceReconnectWs() {
      if (isAuthRequired()) return;
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
      clearWsTimers();
      state.reconnectDelay = 1000;

      const oldWs = state.ws;
      state.ws = null;
      if (oldWs) {
        oldWs.onopen = null;
        oldWs.onmessage = null;
        oldWs.onerror = null;
        oldWs.onclose = null;
        try { oldWs.close(); } catch (_) {}
      }
      connectWs();
    }

    function probeWsHealth(timeoutMs = WS_PONG_TIMEOUT) {
      if (isAuthRequired()) return;
      const ws = state.ws;
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        forceReconnectWs();
        return;
      }
      if (ws.readyState === WebSocket.CONNECTING) {
        return;
      }
      try {
        ws.send(JSON.stringify({ type: 'ping' }));
      } catch (_) {
        forceReconnectWs();
        return;
      }
      if (state.pongTimer) clearTimeout(state.pongTimer);
      state.pongTimer = setTimeout(() => {
        state.pongTimer = null;
        if (state.ws === ws) {
          forceReconnectWs();
        }
      }, timeoutMs);
    }

    function ensureWsAlive(strictProbe = false) {
      if (isAuthRequired()) return;
      const ws = state.ws;
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = null;
        state.reconnectDelay = 1000;
        connectWs();
        return;
      }
      if (ws.readyState === WebSocket.OPEN && strictProbe) {
        probeWsHealth(WS_WAKE_PONG_TIMEOUT);
      }
    }

    function sendWs(obj) {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        try {
          state.ws.send(JSON.stringify(obj));
          return true;
        } catch (_) {
          forceReconnectWs();
        }
      } else {
        ensureWsAlive(false);
      }
      showToast('连接已断开，正在重连...', true);
      return false;
    }

    const VIEW_STORAGE_KEY = 'vibeone_client_view_v1';

    function readSavedView() {
      for (const store of [window.sessionStorage, window.localStorage]) {
        try {
          const raw = store?.getItem(VIEW_STORAGE_KEY);
          if (!raw) continue;
          const obj = JSON.parse(raw);
          if (obj && typeof obj === 'object' && (obj.profile || obj.session_id)) {
            return {
              profile: typeof obj.profile === 'string' ? obj.profile : '',
              session_id: typeof obj.session_id === 'string' ? obj.session_id : '',
            };
          }
        } catch (_) {}
      }
      return { profile: '', session_id: '' };
    }

    function persistCurrentView() {
      if (!state.currentProfile && !state.currentSessionId) return;
      const payload = JSON.stringify({
        profile: state.currentProfile || '',
        session_id: state.currentSessionId || '',
      });
      try { window.sessionStorage?.setItem(VIEW_STORAGE_KEY, payload); } catch (_) {}
      try { window.localStorage?.setItem(VIEW_STORAGE_KEY, payload); } catch (_) {}
    }

    function connectWs() {
      if (isAuthRequired()) return;
      if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
      clearWsTimers();

      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const saved = readSavedView();
      const wsParams = new URLSearchParams();
      const targetProfile = state.currentProfile || saved.profile;
      const targetSession = state.currentSessionId || saved.session_id;
      if (targetProfile) wsParams.set('profile', targetProfile);
      if (targetSession) wsParams.set('session', targetSession);
      const qs = wsParams.toString();
      const url = `${proto}//${location.host}/ws${qs ? `?${qs}` : ''}`;
      els.connDot.className = 'w-2 h-2 rounded-full bg-amber-400 animate-soft-pulse shrink-0';

      const ws = new WebSocket(url);
      state.ws = ws;

      // Guard against hung CONNECTING sockets on flaky mobile networks
      state.connectTimer = setTimeout(() => {
        state.connectTimer = null;
        if (state.ws === ws && ws.readyState === WebSocket.CONNECTING) {
          forceReconnectWs();
        }
      }, WS_CONNECT_TIMEOUT);

      ws.onopen = () => {
        if (state.ws !== ws) return;
        clearWsTimers();
        state.reconnectDelay = 1000;
        state.lastWsMessageAt = Date.now();
        try {
          ws.send(JSON.stringify({ type: 'client_visibility', visible: document.visibilityState === 'visible' }));
        } catch (_) {}
        state.heartbeatTimer = setInterval(() => {
          if (document.visibilityState === 'visible' && state.ws === ws) {
            probeWsHealth(WS_PONG_TIMEOUT);
          }
        }, WS_HEARTBEAT_INTERVAL);
      };

      ws.onmessage = (ev) => {
        if (state.ws !== ws) return;
        state.lastWsMessageAt = Date.now();
        if (state.pongTimer) {
          clearTimeout(state.pongTimer);
          state.pongTimer = null;
        }

        let msg;
        try { msg = JSON.parse(ev.data); } catch (_) { return; }

        if (msg.type === 'pong') {
          return;
        } else if (msg.type === 'snapshot') {
          const snap = msg.data || {};
          const prevSid = state.currentSessionId;
          const prevProfile = state.currentProfile;
          applyStatus(snap);
          persistCurrentView();
          applyUsage(snap.usage);
          const nextItems = Array.isArray(snap.items) ? snap.items : [];
          const nextTotal = Number(snap.total_items ?? nextItems.length);
          const nextOffset = Number(snap.items_offset ?? 0);
          const nextHasMore = Boolean(snap.has_more_items);
          const sameSession =
            prevSid &&
            snap.session_id === prevSid &&
            (!snap.current_profile || snap.current_profile === prevProfile);
          const hasSwitchPlaceholder = Boolean(document.getElementById('session-switch-placeholder'));
          let unchanged =
            sameSession &&
            !hasSwitchPlaceholder &&
            nextTotal === state.totalItems &&
            state.items.length >= nextItems.length;
          if (unchanged) {
            for (let i = Math.max(0, nextItems.length - 6); i < nextItems.length; i++) {
              const ni = nextItems[i];
              const oi = state.itemsById.get(ni.id);
              if (!oi || String(oi.status || '') !== String(ni.status || '')) {
                unchanged = false;
                break;
              }
              if (!ni.detail_deferred) {
                if (
                  String(oi.text || '').length !== String(ni.text || '').length ||
                  String(oi.output || '').length !== String(ni.output || '').length
                ) {
                  unchanged = false;
                  break;
                }
              }
            }
          }
          if (!unchanged) {
            // If same session and the user has cached/loaded history overlapping the new snapshot, merge tail without wiping DOM
            const canMergeTail =
              sameSession &&
              !hasSwitchPlaceholder &&
              state.items.length > 0 &&
              nextItems.length > 0 &&
              state.itemsById.has(nextItems[0].id);
            if (canMergeTail) {
              state.totalItems = nextTotal;
              for (const ni of nextItems) {
                const oi = state.itemsById.get(ni.id);
                const merged = ni.detail_deferred && oi && !oi.detail_deferred ? oi : ni;
                state.itemsById.set(merged.id, merged);
                if (!oi) state.items.push(merged);
                createOrUpdateItemDom(merged);
              }
            } else {
              if (!sameSession && prevSid && typeof cacheCurrentSessionView === 'function') {
                const curSidAfter = state.currentSessionId;
                const curProfAfter = state.currentProfile;
                state.currentSessionId = prevSid;
                state.currentProfile = prevProfile;
                cacheCurrentSessionView();
                state.currentSessionId = curSidAfter;
                state.currentProfile = curProfAfter;
              }
              state.totalItems = nextTotal;
              state.itemsOffset = nextOffset;
              state.hasMoreItems = nextHasMore;
              state.loadingOlderItems = false;
              renderAllItems(nextItems);
            }
          }
          state.pendingPermissions.clear();
          (snap.pending_permissions || []).forEach((p) => state.pendingPermissions.set(p.key, p));
          renderPermissions();
          state.queuedPrompts = Array.isArray(snap.queued_prompts) ? snap.queued_prompts : [];
          renderQueueBar();
          loadSidebarSessions();
          if (pushState.pendingDeepLinkSid || pushState.pendingDeepLinkProfile) {
            const targetSid = pushState.pendingDeepLinkSid;
            const targetProfile = pushState.pendingDeepLinkProfile;
            pushState.pendingDeepLinkSid = '';
            pushState.pendingDeepLinkProfile = '';
            try { history.replaceState(null, '', '/'); } catch (_) {}
            handleOpenSessionDeepLink(targetSid, targetProfile);
          }
        } else if (msg.type === 'queue_update') {
          state.queuedPrompts = Array.isArray(msg.data) ? msg.data : [];
          renderQueueBar();
        } else if (msg.type === 'status') {
          applyStatus(msg.data);
        } else if (msg.type === 'sessions_changed') {
          loadSidebarSessions();
        } else if (msg.type === 'usage') {
          applyUsage(msg.data);
        } else if (msg.type === 'item_upsert') {
          const it = msg.data;
          const existed = state.itemsById.has(it.id);
          state.itemsById.set(it.id, it);
          if (!existed) {
            state.items.push(it);
            state.totalItems += 1;
          }
          createOrUpdateItemDom(it);
        } else if (msg.type === 'text_delta') {
          const { item_id, delta } = msg.data;
          let it = state.itemsById.get(item_id);
          if (!it) {
            it = { id: item_id, type: 'assistant', text: delta };
            state.itemsById.set(item_id, it);
            state.items.push(it);
            state.totalItems += 1;
          } else {
            it.text = (it.text || '') + delta;
          }
          createOrUpdateItemDom(it);
        } else if (msg.type === 'thought_delta') {
          const { item_id, delta } = msg.data;
          let it = state.itemsById.get(item_id);
          if (!it) {
            it = { id: item_id, type: 'thought', text: delta };
            state.itemsById.set(item_id, it);
            state.items.push(it);
            state.totalItems += 1;
          } else {
            it.text = (it.text || '') + delta;
          }
          createOrUpdateItemDom(it);
        } else if (msg.type === 'permission_request') {
          state.pendingPermissions.set(msg.data.key, msg.data);
          renderPermissions();
          scrollToBottom(true);
        } else if (msg.type === 'permission_resolved') {
          state.pendingPermissions.delete(msg.data.key);
          renderPermissions();
        } else if (msg.type === 'turn_end') {
          collapseCompletedProcessGroups();
          const sr = String(msg.data?.stopReason || '').toLowerCase();
          if (msg.data?.early_stop_message) {
            showToast(msg.data.early_stop_message, true);
          } else if (sr === 'cancelled') {
            showToast('已中断当前任务');
          }
        } else if (msg.type === 'btw_delta') {
          updateBtwCardDelta(msg.data?.btw_id, msg.data?.delta || '');
        } else if (msg.type === 'btw_done') {
          finishBtwCard(msg.data?.btw_id, msg.data?.text || '', msg.data?.error || '');
        } else if (msg.type === 'toast') {
          showToast(msg.data?.message || '', msg.data?.level === 'error');
        }
      };

      ws.onerror = () => {
        // onclose will follow and trigger backoff reconnect
      };

      ws.onclose = (ev) => {
        if (state.ws !== ws) return;
        clearWsTimers();
        state.ws = null;
        els.connDot.className = 'w-2 h-2 rounded-full bg-rose-500 shrink-0';
        if (state.manualClose) {
          return;
        }
        if (ev.code === 4401) {
          showAuthOverlay();
          return;
        }
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = setTimeout(() => {
          state.reconnectDelay = Math.min(8000, Math.round(state.reconnectDelay * 1.5));
          connectWs();
        }, state.reconnectDelay);
      };
    }

    // Wake / foreground / network-switch recovery (handles iOS PWA suspension & half-open sockets)
    let _lastSnapshotSyncAt = 0;
    function requestSnapshotSync(force = false) {
      if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
      const now = Date.now();
      if (!force && now - _lastSnapshotSyncAt < 800) return;
      _lastSnapshotSyncAt = now;
      try {
        state.ws.send(JSON.stringify({ type: 'request_snapshot' }));
      } catch (_) {}
    }

    function reportClientVisibility() {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        try {
          state.ws.send(JSON.stringify({ type: 'client_visibility', visible: document.visibilityState === 'visible' }));
        } catch (_) {}
      }
    }

    document.addEventListener('visibilitychange', () => {
      reportClientVisibility();
      if (document.visibilityState === 'visible') {
        if (!state.booted && els.offlineOverlay && !els.offlineOverlay.classList.contains('hidden')) {
          checkAuthAndBoot();
          return;
        }
        ensureWsAlive(true);
        requestSnapshotSync(false);
        if (!isAuthRequired()) loadSidebarSessions(false, { onlyIfStale: true });
      }
    });

    window.addEventListener('pageshow', (ev) => {
      reportClientVisibility();
      if (ev.persisted || document.visibilityState === 'visible') {
        if (!state.booted && els.offlineOverlay && !els.offlineOverlay.classList.contains('hidden')) {
          checkAuthAndBoot();
          return;
        }
        ensureWsAlive(true);
        requestSnapshotSync(false);
      }
    });

    window.addEventListener('online', () => {
      if (!state.booted && els.offlineOverlay && !els.offlineOverlay.classList.contains('hidden')) {
        checkAuthAndBoot();
        return;
      }
      ensureWsAlive(true);
      requestSnapshotSync(false);
    });

    window.addEventListener('offline', () => {
      els.connDot.className = 'w-2 h-2 rounded-full bg-rose-500 shrink-0';
    });

    window.addEventListener('focus', () => {
      reportClientVisibility();
      ensureWsAlive(false);
      requestSnapshotSync(false);
    });

