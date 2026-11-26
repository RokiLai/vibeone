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
            ? '<span class="mr-1.5 w-[36px] text-center py-[1px] rounded bg-emerald-500/20 text-emerald-300 text-[9px] border border-emerald-500/30 shrink-0">Codex</span>'
            : '<span class="mr-1.5 w-[36px] text-center py-[1px] rounded bg-blue-500/20 text-blue-300 text-[9px] border border-blue-500/30 shrink-0">Agy</span>';
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
            rightLabel = '切换';
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
                  ${engineTag}
                  <span class="font-medium shrink-0">${escapeHtml(p.name)}</span>
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
        showToast(`已切换账号: ${state.currentProfile}`);
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

