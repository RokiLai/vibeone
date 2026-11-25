    // =========================================================================
    // PWA Web Push Notifications (iOS 16.4+ Home Screen PWA / Android / Desktop)
    // =========================================================================
    const pushState = {
      swReg: null,
      publicKey: '',
      subscribed: false,
      backendSupported: false,
      pendingDeepLinkSid: new URLSearchParams(location.search).get('session') || '',
      pendingDeepLinkProfile: new URLSearchParams(location.search).get('profile') || '',
    };

    async function handleOpenSessionDeepLink(targetSid, targetProfile) {
      if (targetProfile && targetProfile !== state.currentProfile) {
        await switchProfile(targetProfile);
      }
      if (targetSid && targetSid !== state.currentSessionId) {
        sendWs({ type: 'switch_session', session_id: targetSid });
      } else {
        requestSnapshotSync(true);
      }
    }

    function urlBase64ToUint8Array(base64String) {
      const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const rawData = atob(base64);
      const outputArray = new Uint8Array(rawData.length);
      for (let i = 0; i < rawData.length; i += 1) {
        outputArray[i] = rawData.charCodeAt(i);
      }
      return outputArray;
    }

    function updatePushUi() {
      const supportedBrowser = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
      if (!supportedBrowser) {
        els.pushToggleLabel.textContent = '仅主屏幕PWA支持推送';
        els.btnPushToggle.className = 'flex-1 min-w-0 px-3 py-2 rounded-xl border border-border/60 bg-bg text-slate-500 flex items-center justify-center gap-1.5 transition';
        els.btnPushTest.classList.add('hidden');
        return;
      }
      if (pushState.subscribed) {
        els.pushToggleLabel.textContent = '推送已开启';
        els.btnPushToggle.className = 'flex-1 min-w-0 px-3 py-2 rounded-xl border border-emerald-500/45 bg-emerald-500/15 hover:bg-emerald-500/20 text-emerald-300 flex items-center justify-center gap-1.5 transition';
        els.btnPushTest.classList.remove('hidden');
      } else {
        els.pushToggleLabel.textContent = Notification.permission === 'denied' ? '推送已被系统禁用' : '开启推送通知';
        els.btnPushToggle.className = 'flex-1 min-w-0 px-3 py-2 rounded-xl border border-border bg-bg hover:bg-card active:scale-[0.99] text-slate-300 flex items-center justify-center gap-1.5 transition';
        els.btnPushTest.classList.add('hidden');
      }
    }

    async function initWebPush() {
      updatePushUi();
      if (!('serviceWorker' in navigator)) return;
      try {
        pushState.swReg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        navigator.serviceWorker.addEventListener('message', (ev) => {
          const data = ev.data || {};
          if (data.type === 'open_session') {
            handleOpenSessionDeepLink(data.session_id || '', data.profile || '');
          }
        });
      } catch (_) {
        return;
      }

      try {
        const res = await fetch('/api/push/config', { credentials: 'same-origin' });
        if (res.ok) {
          const cfg = await res.json();
          pushState.backendSupported = Boolean(cfg.supported);
          pushState.publicKey = cfg.public_key || '';
        }
      } catch (_) {}

      if (pushState.swReg && 'PushManager' in window) {
        try {
          const existingSub = await pushState.swReg.pushManager.getSubscription();
          pushState.subscribed = Boolean(existingSub);
          if (existingSub && pushState.backendSupported) {
            // Re-sync subscription with server silently on boot
            const subJson = existingSub.toJSON();
            if (subJson?.endpoint && subJson?.keys) {
              fetch('/api/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ endpoint: subJson.endpoint, keys: subJson.keys }),
              }).catch(() => {});
            }
          }
        } catch (_) {}
      }
      updatePushUi();
    }

    async function togglePushSubscription() {
      const supportedBrowser = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
      if (!supportedBrowser) {
        showToast('iOS 需先将本页「添加到主屏幕」，再从桌面图标打开后开启推送', true);
        return;
      }
      if (!pushState.backendSupported || !pushState.publicKey) {
        showToast('后端推送服务未就绪，请先重启后端服务', true);
        return;
      }
      if (!pushState.swReg) {
        pushState.swReg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      }
      await navigator.serviceWorker.ready;

      if (pushState.subscribed) {
        try {
          const sub = await pushState.swReg.pushManager.getSubscription();
          if (sub) {
            const endpoint = sub.endpoint;
            await sub.unsubscribe();
            await fetch('/api/push/unsubscribe', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              credentials: 'same-origin',
              body: JSON.stringify({ endpoint }),
            });
          }
          pushState.subscribed = false;
          updatePushUi();
          showToast('已关闭推送通知');
        } catch (_) {
          showToast('关闭推送失败', true);
        }
        return;
      }

      try {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
          updatePushUi();
          showToast('未授予通知权限，请在系统设置中允许通知', true);
          return;
        }
        const sub = await pushState.swReg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(pushState.publicKey),
        });
        const subJson = sub.toJSON();
        const res = await fetch('/api/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ endpoint: subJson.endpoint, keys: subJson.keys }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showToast(err.detail || '订阅推送失败', true);
          return;
        }
        pushState.subscribed = true;
        updatePushUi();
        showToast('推送通知已开启！可点「测试」验证');
      } catch (err) {
        showToast(`开启推送失败: ${err?.message || '请确认使用 HTTPS 访问'}`, true);
      }
    }

    els.btnPushToggle.addEventListener('click', togglePushSubscription);
    els.btnPushTest.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/push/test', { method: 'POST', credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.sent > 0) {
          showToast('测试通知已发送，请查看系统通知栏');
        } else {
          showToast(data.detail || '发送失败，请重新开启推送订阅', true);
        }
      } catch (_) {
        showToast('测试推送请求失败', true);
      }
    });

    // =========================================================================
    // Auth Flow & Passkey (WebAuthn / iCloud Keychain)
    // =========================================================================
    function b64urlToBuf(b64url) {
      const pad = '='.repeat((4 - (b64url.length % 4)) % 4);
      const b64 = (b64url + pad).replace(/-/g, '+').replace(/_/g, '/');
      const raw = atob(b64);
      const buf = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
      return buf.buffer;
    }

    function bufToB64url(buf) {
      const bytes = new Uint8Array(buf);
      let str = '';
      for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
      return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function supportsPasskey() {
      return Boolean(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
    }

    async function refreshPasskeyLoginAvailability() {
      if (!els.btnPasskeyLogin) return;
      if (!supportsPasskey()) {
        els.btnPasskeyLogin.classList.add('hidden');
        return;
      }
      try {
        const res = await fetch('/api/auth/passkey/status', { credentials: 'same-origin' });
        const data = res.ok ? await res.json() : { enabled: false };
        if (data.enabled) {
          els.btnPasskeyLogin.classList.remove('hidden');
        } else {
          els.btnPasskeyLogin.classList.add('hidden');
        }
      } catch (_) {
        els.btnPasskeyLogin.classList.add('hidden');
      }
    }

    async function refreshPasskeyBindStatus() {
      if (!els.btnPasskeyBind || !els.passkeyBindLabel) return;
      if (!supportsPasskey()) {
        els.btnPasskeyBind.classList.add('hidden');
        if (els.settingsPasskeySection) els.settingsPasskeySection.classList.add('hidden');
        return;
      }
      els.btnPasskeyBind.classList.remove('hidden');
      if (els.settingsPasskeySection) els.settingsPasskeySection.classList.remove('hidden');
      try {
        const res = await fetch('/api/auth/passkey/status', { credentials: 'same-origin' });
        const data = res.ok ? await res.json() : { count: 0 };
        const count = Number(data.count || 0);
        els.passkeyBindLabel.textContent = count > 0
          ? `Passkey 已绑定 (${count}) · 新增`
          : '绑定 Passkey（面容/iCloud）';
      } catch (_) {
        els.passkeyBindLabel.textContent = '绑定 Passkey（面容/iCloud）';
      }
    }

    async function loginWithPasskey() {
      if (!supportsPasskey()) return;
      try {
        const optRes = await fetch('/api/auth/passkey/login/options', {
          method: 'POST',
          credentials: 'same-origin',
        });
        if (!optRes.ok) {
          showAuthOverlay('尚未绑定 Passkey，请先用密钥解锁');
          return;
        }
        const opts = await optRes.json();
        opts.challenge = b64urlToBuf(opts.challenge);
        if (Array.isArray(opts.allowCredentials)) {
          opts.allowCredentials = opts.allowCredentials.map((c) => ({
            ...c,
            id: b64urlToBuf(c.id),
          }));
        }
        const assertion = await navigator.credentials.get({ publicKey: opts });
        if (!assertion) return;

        const verifyRes = await fetch('/api/auth/passkey/login/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            id: assertion.id,
            authenticatorData: bufToB64url(assertion.response.authenticatorData),
            clientDataJSON: bufToB64url(assertion.response.clientDataJSON),
            signature: bufToB64url(assertion.response.signature),
          }),
        });
        if (!verifyRes.ok) {
          showAuthOverlay('Passkey 验证失败，请重试或使用密钥解锁');
          return;
        }
        els.authInput.value = '';
        state.manualClose = false;
        els.authOverlay.classList.add('hidden');
        connectWs();
        initWebPush();
        refreshPasskeyBindStatus();
      } catch (err) {
        if (err && err.name === 'NotAllowedError') return;
        showAuthOverlay('Passkey 解锁已取消或失败');
      }
    }

    async function registerPasskey() {
      if (!supportsPasskey()) {
        showToast('当前环境不支持 Passkey（需 HTTPS 或 localhost）', true);
        return;
      }
      try {
        const optRes = await fetch('/api/auth/passkey/register/options', {
          method: 'POST',
          credentials: 'same-origin',
        });
        if (!optRes.ok) {
          showToast('获取 Passkey 注册参数失败', true);
          return;
        }
        const opts = await optRes.json();
        opts.challenge = b64urlToBuf(opts.challenge);
        opts.user.id = b64urlToBuf(opts.user.id);
        if (Array.isArray(opts.excludeCredentials)) {
          opts.excludeCredentials = opts.excludeCredentials.map((c) => ({
            ...c,
            id: b64urlToBuf(c.id),
          }));
        }
        const cred = await navigator.credentials.create({ publicKey: opts });
        if (!cred) return;

        const verifyRes = await fetch('/api/auth/passkey/register/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            id: cred.id,
            attestationObject: bufToB64url(cred.response.attestationObject),
            clientDataJSON: bufToB64url(cred.response.clientDataJSON),
            label: navigator.platform || 'Device Passkey',
          }),
        });
        if (!verifyRes.ok) {
          showToast('Passkey 绑定失败，请重试', true);
          return;
        }
        showToast('Passkey 绑定成功，下次可直接面容/钥匙串解锁');
        refreshPasskeyBindStatus();
      } catch (err) {
        if (err && err.name === 'NotAllowedError') return;
        if (err && err.name === 'InvalidStateError') {
          showToast('当前设备已绑定过此 Passkey');
          return;
        }
        showToast('Passkey 绑定失败', true);
      }
    }

    function showOfflineOverlay(detail = '') {
      els.authOverlay.classList.add('hidden');
      if (els.offlineOverlay) {
        if (els.offlineDetail) {
          els.offlineDetail.textContent = detail || (
            navigator.onLine === false
              ? '当前设备已断网，恢复网络连接后将自动重连。'
              : '无法连接到 VibeOne 服务器，请检查网络或服务状态后重试。'
          );
        }
        els.offlineOverlay.classList.remove('hidden');
      }
      if (els.connDot) {
        els.connDot.className = 'w-2 h-2 rounded-full bg-rose-500 shrink-0';
      }
    }

    function hideOfflineOverlay() {
      if (els.offlineOverlay) els.offlineOverlay.classList.add('hidden');
    }

    function showAuthOverlay(errorMsg = '') {
      hideOfflineOverlay();
      els.authOverlay.classList.remove('hidden');
      if (errorMsg) {
        els.authError.textContent = errorMsg;
        els.authError.classList.remove('hidden');
      } else {
        els.authError.classList.add('hidden');
      }
      refreshPasskeyLoginAvailability();
      setTimeout(() => els.authInput.focus(), 100);
    }

    let _bootingAuth = false;
    async function checkAuthAndBoot() {
      if (_bootingAuth) return;
      _bootingAuth = true;
      if (els.btnOfflineRetry) {
        els.btnOfflineRetry.disabled = true;
        els.btnOfflineRetry.textContent = '正在连接...';
      }
      try {
        const res = await fetch('/api/auth/check', { credentials: 'same-origin', cache: 'no-store' });
        if (res.ok) {
          state.booted = true;
          state.manualClose = false;
          hideOfflineOverlay();
          els.authOverlay.classList.add('hidden');
          connectWs();
          initWebPush();
          refreshPasskeyBindStatus();
          return;
        }
        if (res.status === 401 || res.status === 403) {
          showAuthOverlay();
          return;
        }
        showOfflineOverlay(`服务器暂时不可用（HTTP ${res.status}），请稍后重试。`);
      } catch (_) {
        showOfflineOverlay();
      } finally {
        _bootingAuth = false;
        if (els.btnOfflineRetry) {
          els.btnOfflineRetry.disabled = false;
          els.btnOfflineRetry.textContent = '重新连接';
        }
      }
    }

    if (els.btnOfflineRetry) {
      els.btnOfflineRetry.addEventListener('click', () => checkAuthAndBoot());
    }

    if (els.btnPasskeyLogin) {
      els.btnPasskeyLogin.addEventListener('click', loginWithPasskey);
    }
    if (els.btnPasskeyBind) {
      els.btnPasskeyBind.addEventListener('click', registerPasskey);
    }

    els.authForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const token = els.authInput.value.trim();
      if (!token) return;
      try {
        const res = await fetch('/api/auth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ token }),
        });
        if (!res.ok) {
          showAuthOverlay('密钥错误，请重试');
          return;
        }
        els.authInput.value = '';
        state.manualClose = false;
        els.authOverlay.classList.add('hidden');
        connectWs();
        initWebPush();
        refreshPasskeyBindStatus();
      } catch (_) {
        showAuthOverlay('网络错误，无法连接服务器');
      }
    });

