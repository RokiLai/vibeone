    function renderProfileHeader() {
      const lbl = $('profile-current-label');
      if (!lbl) return;
      const p = state.currentProfile || 'default';
      lbl.textContent = state.currentProfileEmail ? `${p} · ${state.currentProfileEmail}` : `账号: ${p}`;
    }

    function renderProfileList() {
      const box = $('profile-list');
      if (!box) return;
      const list = Array.isArray(state.profiles) ? state.profiles : [];
      const rows = list
        .map((p) => {
          const isCur = Boolean(p.is_current || p.name === state.currentProfile);
          const logged = Boolean(p.logged_in);
          const isSwitching = state.switchingProfile === p.name;
          const isReloading = state.reloadingProfile === p.name;
          const sub = logged ? (p.email || '已登录') : '未登录（点击登录）';
          const engineTag = p.backend === 'codex'
            ? '<span class="ml-1 px-1 rounded bg-emerald-500/20 text-emerald-300 text-[9px] border border-emerald-500/30 shrink-0">Codex</span>'
            : '<span class="ml-1 px-1 rounded bg-blue-500/20 text-blue-300 text-[9px] border border-blue-500/30 shrink-0">Agy</span>';
          const busyTag = p.busy_count > 0 ? '<span class="ml-1 px-1 rounded bg-indigo-500/25 text-indigo-300 text-[9px]">运行中</span>' : '';
          let rightLabel = '去登录';
          let rightClass = 'text-amber-300';
          if (isReloading) {
            rightLabel = '重载中...';
            rightClass = 'text-blue-300 animate-pulse';
          } else if (isSwitching) {
            rightLabel = '切换中...';
            rightClass = 'text-amber-300 animate-pulse';
          } else if (isCur) {
            rightLabel = '当前';
            rightClass = 'text-emerald-300';
          } else if (logged) {
            rightLabel = p.warm ? '⚡ 秒切' : '切换';
            rightClass = p.warm ? 'text-emerald-300' : 'text-slate-400';
          }
          const disableRow = Boolean(state.switchingProfile || state.reloadingProfile);
          return `
            <div class="flex items-center gap-1">
              <button
                type="button"
                data-profile-name="${escapeHtml(p.name)}"
                data-profile-backend="${escapeHtml(p.backend || 'agy')}"
                data-logged-in="${logged ? '1' : '0'}"
                ${disableRow ? 'disabled' : ''}
                class="flex-1 min-w-0 flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-left text-[11px] transition ${
                  isCur
                    ? 'bg-blue-600/25 border border-blue-500/45 text-blue-100'
                    : logged
                    ? 'bg-bg/80 hover:bg-card border border-border/50 text-slate-200 disabled:opacity-60'
                    : 'bg-bg/50 hover:bg-card/70 border border-amber-500/30 text-slate-300'
                }"
              >
                <span class="min-w-0 truncate flex items-center">
                  <span class="font-medium shrink-0">${escapeHtml(p.name)}</span>
                  ${engineTag}
                  <span class="text-[10px] opacity-80 ml-1 truncate">${escapeHtml(sub)}</span>
                  ${busyTag}
                </span>
                <span class="text-[10px] shrink-0 ${rightClass}">${rightLabel}</span>
              </button>
              <button
                type="button"
                data-profile-action="login"
                data-profile-name="${escapeHtml(p.name)}"
                data-profile-backend="${escapeHtml(p.backend || 'agy')}"
                data-logged-in="${logged ? '1' : '0'}"
                ${disableRow ? 'disabled' : ''}
                title="${logged ? '重新登录 / 刷新凭证' : '立即登录'}"
                class="p-1.5 rounded-lg border border-border/50 bg-bg/80 hover:bg-card text-slate-400 hover:text-amber-300 active:scale-95 transition shrink-0 disabled:opacity-50"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"/></svg>
              </button>
              ${
                logged
                  ? `<button
                      type="button"
                      data-profile-action="reload"
                      data-profile-name="${escapeHtml(p.name)}"
                      ${disableRow ? 'disabled' : ''}
                      title="单账号热重载（不重启服务）"
                      class="p-1.5 rounded-lg border border-border/50 bg-bg/80 hover:bg-card text-slate-400 hover:text-blue-300 active:scale-95 transition shrink-0 disabled:opacity-50"
                    >
                      <svg class="w-3.5 h-3.5 ${isReloading ? 'animate-spin text-blue-300' : ''}" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg>
                    </button>`
                  : ''
              }
            </div>`;
        })
        .join('');

      const addBtn = `
        <div class="grid grid-cols-2 gap-1.5 mt-1">
          <button
            type="button"
            data-profile-action="create-agy"
            class="py-1.5 px-2 rounded-lg border border-dashed border-blue-500/40 bg-bg/40 hover:bg-card/70 text-[11px] text-blue-300/90 hover:text-blue-200 flex items-center justify-center gap-1 transition active:scale-[0.99]"
          >
            <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
            <span>新增 AGY</span>
          </button>
          <button
            type="button"
            data-profile-action="create-codex"
            class="py-1.5 px-2 rounded-lg border border-dashed border-emerald-500/40 bg-bg/40 hover:bg-card/70 text-[11px] text-emerald-300/90 hover:text-emerald-200 flex items-center justify-center gap-1 transition active:scale-[0.99]"
          >
            <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
            <span>新增 Codex</span>
          </button>
        </div>`;

      box.innerHTML = (rows || '<div class="text-[11px] text-slate-500 px-2 py-1">未检测到可用账号配置</div>') + addBtn;
    }

    let _profilesLoadInFlight = null;
    async function loadProfiles() {
      if (_profilesLoadInFlight) return _profilesLoadInFlight;
      _profilesLoadInFlight = (async () => {
        try {
          const qs = state.currentProfile ? `?profile=${encodeURIComponent(state.currentProfile)}` : '';
          const res = await fetch(`/api/profiles${qs}`, { credentials: 'same-origin' });
          if (!res.ok) return;
          const data = await res.json();
          state.profiles = Array.isArray(data.profiles) ? data.profiles : [];
          if (!state.currentProfile && data.current_profile) state.currentProfile = data.current_profile;
          const curObj = state.profiles.find((p) => p.name === state.currentProfile);
          if (curObj) state.currentProfileEmail = curObj.email || '';
          renderProfileHeader();
          renderProfileList();
        } catch {}
        finally {
          _profilesLoadInFlight = null;
        }
      })();
      return _profilesLoadInFlight;
    }

    async function switchProfile(name, force = false) {
      if (!name || name === state.currentProfile || state.switchingProfile) return;
      state.switchingProfile = name;
      renderProfileList();
      const targetInfo = (state.profiles || []).find((p) => p.name === name);
      if (!targetInfo || !targetInfo.warm) {
        showToast(`首次启动账号 ${name}，请稍候...`);
      }
      try {
        const res = await fetch('/api/profile/switch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ profile: name, force, scope: 'client' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        state.currentProfile = data.profile || name;
        state.currentProfileEmail = data.email || '';
        state.currentSessionId = data.session_id || '';
        state.switchingProfile = '';
        resetFullTextSearchState({ retrigger: true });
        persistCurrentView();
        renderProfileHeader();
        sendWs({ type: 'switch_profile', profile: state.currentProfile });
        await loadProfiles();
        showToast(data.hot ? `⚡ 已秒切到账号: ${state.currentProfile}` : `已切换账号: ${state.currentProfile}`);
      } catch (err) {
        state.switchingProfile = '';
        renderProfileList();
        showToast(err.message || String(err));
      }
    }

    async function reloadProfile(name, force = false) {
      if (!name || state.reloadingProfile) return;
      state.reloadingProfile = name;
      renderProfileList();
      showToast(`正在热重载账号 ${name}...`);
      try {
        const res = await fetch('/api/profile/reload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ profile: name, force }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 409 && data.needs_confirm && !force) {
          state.reloadingProfile = '';
          renderProfileList();
          const ok = window.confirm(data.detail || `账号 ${name} 有任务正在运行，确定强制重载吗？`);
          if (ok) await reloadProfile(name, true);
          return;
        }
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        state.reloadingProfile = '';
        await loadProfiles();
        showToast(`账号 ${name} 热重载成功`);
      } catch (err) {
        state.reloadingProfile = '';
        renderProfileList();
        showToast(err.message || String(err), true);
      }
    }

    const profileLoginState = {
      flowId: '',
      profile: '',
      backend: 'agy',
      createIfMissing: false,
      logoutFirst: false,
      pollTimer: null,
      authUrl: '',
      userCode: '',
      status: '',
    };

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

