    function stopProfileLoginPolling() {
      if (profileLoginState.pollTimer) {
        clearInterval(profileLoginState.pollTimer);
        profileLoginState.pollTimer = null;
      }
    }

    function renderProfileLoginModal(flow) {
      const statusBox = $('profile-login-status-box');
      const statusText = $('profile-login-status-text');
      const spinner = $('profile-login-spinner');
      const urlBox = $('profile-login-url-box');
      const urlLink = $('profile-login-url-link');
      const deviceBox = $('profile-login-device-box');
      const deviceCodeEl = $('profile-login-device-code');
      const codeBox = $('profile-login-code-box');
      const submitBtn = $('btn-profile-login-submit-code');
      const retryBtn = $('btn-profile-login-retry');
      const cancelBtn = $('btn-profile-login-cancel');
      if (!statusBox) return;

      profileLoginState.status = flow.status || '';
      profileLoginState.authUrl = flow.auth_url || '';
      profileLoginState.userCode = flow.user_code || '';

      statusBox.classList.remove('hidden');
      statusText.textContent = flow.error || flow.message || '处理中...';
      const inProgress = ['starting', 'awaiting_user', 'submitting', 'reloading'].includes(flow.status);
      spinner.classList.toggle('hidden', !inProgress);

      statusBox.className = `rounded-xl border px-3 py-2.5 flex items-start gap-2 ${
        flow.status === 'succeeded'
          ? 'border-emerald-500/45 bg-emerald-500/10 text-emerald-200'
          : flow.status === 'failed'
          ? 'border-rose-500/45 bg-rose-500/10 text-rose-200'
          : 'border-border/80 bg-bg/90 text-slate-200'
      }`;

      if (flow.auth_url) {
        urlBox.classList.remove('hidden');
        urlLink.href = flow.auth_url;
      } else {
        urlBox.classList.add('hidden');
      }

      if (flow.backend === 'codex' && flow.user_code) {
        deviceBox.classList.remove('hidden');
        deviceCodeEl.textContent = flow.user_code;
      } else {
        deviceBox.classList.add('hidden');
      }

      if (flow.backend === 'agy' && flow.auth_url && flow.status === 'awaiting_user') {
        codeBox.classList.remove('hidden');
        if (submitBtn) submitBtn.disabled = false;
      } else if (flow.backend === 'agy' && flow.status === 'submitting') {
        codeBox.classList.remove('hidden');
        if (submitBtn) submitBtn.disabled = true;
      } else {
        codeBox.classList.add('hidden');
      }

      if (retryBtn) {
        retryBtn.classList.toggle('hidden', flow.status !== 'failed' && flow.status !== 'cancelled');
      }
      if (cancelBtn) {
        cancelBtn.textContent = flow.status === 'succeeded' || flow.status === 'failed' || flow.status === 'cancelled'
          ? '关闭'
          : '取消';
      }
    }

    async function pollProfileLoginStatus() {
      if (!profileLoginState.flowId) return;
      try {
        const res = await fetch(`/api/profile/login/${encodeURIComponent(profileLoginState.flowId)}`, {
          credentials: 'same-origin',
        });
        if (!res.ok) return;
        const flow = await res.json();
        renderProfileLoginModal(flow);
        if (['succeeded', 'failed', 'cancelled'].includes(flow.status)) {
          stopProfileLoginPolling();
          if (flow.status === 'succeeded') {
            await loadProfiles();
            showToast(flow.message || `账号 ${flow.profile} 登录成功`);
          }
        }
      } catch (_) {}
    }

    async function startProfileLoginFlow(profile, { backend = '', createIfMissing = false, logoutFirst = false } = {}) {
      stopProfileLoginPolling();
      const resolvedBackend = backend || getProfileBackend(profile);
      profileLoginState.profile = profile;
      profileLoginState.backend = resolvedBackend;
      profileLoginState.createIfMissing = createIfMissing;
      profileLoginState.logoutFirst = logoutFirst;
      profileLoginState.flowId = '';

      const createBox = $('profile-login-create-box');
      if (createBox) createBox.classList.add('hidden');
      const codeInput = $('profile-login-code-input');
      if (codeInput) codeInput.value = '';

      renderProfileLoginModal({
        backend: resolvedBackend,
        status: 'starting',
        message: resolvedBackend === 'codex' ? '正在启动 Codex 设备码登录...' : '正在生成 Google OAuth 登录链接...',
      });

      try {
        const res = await fetch('/api/profile/login/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            profile,
            backend: resolvedBackend,
            create_if_missing: createIfMissing,
            logout_first: logoutFirst,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        profileLoginState.flowId = data.flow_id || '';
        profileLoginState.backend = data.backend || resolvedBackend;
        renderProfileLoginModal(data);
        if (!['succeeded', 'failed', 'cancelled'].includes(data.status)) {
          profileLoginState.pollTimer = setInterval(pollProfileLoginStatus, 1200);
        } else if (data.status === 'succeeded') {
          await loadProfiles();
        }
      } catch (err) {
        renderProfileLoginModal({
          backend: resolvedBackend,
          status: 'failed',
          error: err.message || String(err),
        });
      }
    }

    function openProfileLoginModal({ profile = '', backend = 'agy', createMode = false, logoutFirst = false } = {}) {
      const modal = $('profile-login-modal');
      if (!modal) return;
      stopProfileLoginPolling();
      profileLoginState.flowId = '';
      profileLoginState.profile = profile;
      profileLoginState.backend = backend;

      const titleEl = $('profile-login-title');
      const subEl = $('profile-login-subtitle');
      const createBox = $('profile-login-create-box');
      const createLabel = $('profile-login-create-label');
      const createHint = $('profile-login-create-hint');
      const newNameInput = $('profile-login-new-name');

      modal.classList.remove('hidden');
      if (createMode) {
        const isCodex = backend === 'codex';
        if (titleEl) titleEl.textContent = isCodex ? '新增 Codex 账号' : '新增 AGY 账号';
        if (subEl) {
          subEl.textContent = isCodex
            ? '创建独立 CODEX_HOME 目录并完成设备码登录'
            : '创建独立配置目录并完成 Google OAuth 登录';
        }
        if (createLabel) {
          createLabel.textContent = isCodex
            ? '新 Codex 账号配置名（例如 codex2、codex-work）'
            : '新 AGY 账号配置名（例如 agy3、work）';
        }
        if (createHint) {
          createHint.innerHTML = isCodex
            ? '将在 <code class="text-slate-300">~/.config/codex/profiles/&lt;名称&gt;</code> 创建独立目录并自动复用已有 skills，再启动设备码登录。'
            : '将在 <code class="text-slate-300">~/.config/agy/profiles/&lt;名称&gt;</code> 创建独立目录并启动 Google OAuth 登录。';
        }
        if (createBox) createBox.classList.remove('hidden');
        $('profile-login-status-box')?.classList.add('hidden');
        $('profile-login-url-box')?.classList.add('hidden');
        $('profile-login-device-box')?.classList.add('hidden');
        $('profile-login-code-box')?.classList.add('hidden');
        $('btn-profile-login-retry')?.classList.add('hidden');
        if ($('btn-profile-login-cancel')) $('btn-profile-login-cancel').textContent = '取消';
        if (newNameInput) {
          newNameInput.value = '';
          setTimeout(() => newNameInput.focus(), 50);
        }
        return;
      }

      if (titleEl) titleEl.textContent = `账号登录 · ${profile}`;
      if (subEl) {
        subEl.textContent = backend === 'codex'
          ? 'Codex 设备码授权（完成后自动热重载）'
          : 'AGY Google OAuth 授权（完成后自动热重载）';
      }
      startProfileLoginFlow(profile, { backend, createIfMissing: false, logoutFirst });
    }

    async function closeProfileLoginModal() {
      const modal = $('profile-login-modal');
      stopProfileLoginPolling();
      const fid = profileLoginState.flowId;
      const st = profileLoginState.status;
      profileLoginState.flowId = '';
      if (modal) modal.classList.add('hidden');
      if (fid && !['succeeded', 'failed', 'cancelled'].includes(st)) {
        try {
          await fetch(`/api/profile/login/${encodeURIComponent(fid)}/cancel`, {
            method: 'POST',
            credentials: 'same-origin',
          });
        } catch (_) {}
      }
    }

    $('btn-profile-login-close')?.addEventListener('click', closeProfileLoginModal);
    $('btn-profile-login-cancel')?.addEventListener('click', closeProfileLoginModal);
    $('profile-login-modal')?.addEventListener('click', (e) => {
      if (e.target === $('profile-login-modal')) closeProfileLoginModal();
    });

    $('btn-profile-login-create-start')?.addEventListener('click', () => {
      const raw = ($('profile-login-new-name')?.value || '').trim();
      const targetBackend = profileLoginState.backend === 'codex' ? 'codex' : 'agy';
      const forbidden = targetBackend === 'codex' ? ['agy', 'default', 'codex'] : ['codex', 'default'];
      if (!raw || !/^[a-zA-Z0-9_-]+$/.test(raw) || forbidden.includes(raw)) {
        showToast(
          targetBackend === 'codex'
            ? '请输入合法的 Codex 账号名（字母、数字、_ 或 -，不能为 codex/agy/default）'
            : '请输入合法的 AGY 账号名（字母、数字、_ 或 -，不能为 codex/default）',
          true
        );
        return;
      }
      if ($('profile-login-title')) $('profile-login-title').textContent = `账号登录 · ${raw}`;
      startProfileLoginFlow(raw, { backend: targetBackend, createIfMissing: true, logoutFirst: false });
    });

    $('btn-profile-login-copy-url')?.addEventListener('click', async () => {
      if (!profileLoginState.authUrl) return;
      try {
        await navigator.clipboard.writeText(profileLoginState.authUrl);
        showToast('已复制授权链接');
      } catch (_) {
        showToast('复制失败，请直接点击「打开授权页面」', true);
      }
    });

    $('btn-profile-login-copy-code')?.addEventListener('click', async () => {
      if (!profileLoginState.userCode) return;
      try {
        await navigator.clipboard.writeText(profileLoginState.userCode);
        showToast(`已复制设备码：${profileLoginState.userCode}`);
      } catch (_) {
        showToast('复制失败，请手动选中复制', true);
      }
    });

    $('btn-profile-login-submit-code')?.addEventListener('click', async () => {
      if (!profileLoginState.flowId) return;
      const code = ($('profile-login-code-input')?.value || '').trim();
      if (!code) {
        showToast('请先粘贴授权码或完整回调链接', true);
        return;
      }
      const btn = $('btn-profile-login-submit-code');
      if (btn) btn.disabled = true;
      try {
        const res = await fetch(`/api/profile/login/${encodeURIComponent(profileLoginState.flowId)}/submit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ code }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        renderProfileLoginModal(data);
        if (data.status === 'succeeded') {
          stopProfileLoginPolling();
          await loadProfiles();
          showToast(data.message || `账号 ${data.profile} 登录成功`);
        }
      } catch (err) {
        if (btn) btn.disabled = false;
        showToast(err.message || String(err), true);
      }
    });

    $('btn-profile-login-retry')?.addEventListener('click', () => {
      if (!profileLoginState.profile) return;
      startProfileLoginFlow(profileLoginState.profile, {
        backend: profileLoginState.backend,
        createIfMissing: profileLoginState.createIfMissing,
        logoutFirst: profileLoginState.logoutFirst,
      });
    });

    $('btn-profile-toggle')?.addEventListener('click', async () => {
      state.profilePickerOpen = !state.profilePickerOpen;
      const listEl = $('profile-list');
      const chevEl = $('profile-chevron');
      if (listEl) listEl.classList.toggle('hidden', !state.profilePickerOpen);
      if (chevEl) chevEl.classList.toggle('rotate-180', state.profilePickerOpen);
      if (state.profilePickerOpen) await loadProfiles();
    });

    $('profile-list')?.addEventListener('click', (e) => {
      const actionBtn = e.target.closest('button[data-profile-action]');
      if (actionBtn) {
        const action = actionBtn.dataset.profileAction;
        if (action === 'create-agy') {
          openProfileLoginModal({ createMode: true, backend: 'agy' });
          return;
        }
        if (action === 'create-codex') {
          openProfileLoginModal({ createMode: true, backend: 'codex' });
          return;
        }
        const name = actionBtn.dataset.profileName;
        if (action === 'reload' && name) {
          reloadProfile(name);
          return;
        }
        if (action === 'login' && name) {
          const backend = actionBtn.dataset.profileBackend || getProfileBackend(name);
          const wasLogged = actionBtn.dataset.loggedIn === '1';
          openProfileLoginModal({
            profile: name,
            backend,
            logoutFirst: backend === 'codex' && wasLogged,
          });
          return;
        }
      }
      const btn = e.target.closest('button[data-profile-name]');
      if (!btn) return;
      const name = btn.dataset.profileName;
      const backend = btn.dataset.profileBackend || getProfileBackend(name);
      if (btn.dataset.loggedIn !== '1') {
        openProfileLoginModal({ profile: name, backend, logoutFirst: false });
        return;
      }
      switchProfile(name);
    });

    els.chatList.addEventListener('click', (e) => {
      const summary = e.target.closest('details.process-group > summary, details.tool-card > summary');
      if (summary) {
        const details = summary.parentElement;
        if (details && details.tagName === 'DETAILS') {
          e.preventDefault();
          if (details.classList.contains('process-group')) {
            details.dataset.userToggled = '1';
          }
          withScrollAnchor(summary, () => {
            details.open = !details.open;
            if (details.open && details.dataset.bodyLazy === '1') {
              const item = state.itemsById.get(details.dataset.itemId);
              if (details.dataset.itemType === 'thought') hydrateThoughtBody(details, item);
              else if (details.dataset.itemType === 'tool') hydrateToolBody(details, item);
            }
          });
          return;
        }
      }
      const btn = e.target.closest('button[data-switch-profile]');
      if (!btn) return;
      switchProfile(btn.dataset.switchProfile);
    });

