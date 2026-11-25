    function setSettingsView(view) {
      const next = view === 'archived' ? 'archived' : 'main';
      state.settingsView = next;
      const isArchived = next === 'archived';
      if (els.btnSettingsBack) els.btnSettingsBack.classList.toggle('hidden', !isArchived);
      if (els.settingsMainView) els.settingsMainView.classList.toggle('hidden', isArchived);
      if (els.settingsArchivedView) els.settingsArchivedView.classList.toggle('hidden', !isArchived);
      if (els.settingsModalTitle) {
        els.settingsModalTitle.textContent = isArchived ? '已归档会话' : '设置';
      }
      if (els.settingsModalSubtitle) {
        els.settingsModalSubtitle.textContent = isArchived
          ? '点击可查看会话，或恢复到左侧列表'
          : '会话管理、推送通知与账号安全';
      }
      if (isArchived) {
        if (els.archivedSearch) els.archivedSearch.value = '';
        renderArchivedSessionsList();
      }
    }

    let _revealedAccessToken = '';
    let _tokenActionResetTimer = null;

    function resetAccessTokenDisplay() {
      _revealedAccessToken = '';
      clearTimeout(_tokenActionResetTimer);
      _tokenActionResetTimer = null;
      if (els.settingsTokenValue) {
        els.settingsTokenValue.textContent = '••••••••••••••••••••';
        els.settingsTokenValue.classList.remove('text-slate-100');
        els.settingsTokenValue.classList.add('text-slate-300');
      }
      if (els.settingsTokenAction) {
        els.settingsTokenAction.textContent = '显示并复制';
      }
      if (els.btnTokenHide) {
        els.btnTokenHide.classList.add('hidden');
      }
    }

    async function handleRevealAndCopyToken() {
      if (!els.btnTokenRevealCopy) return;
      let token = _revealedAccessToken;
      const wasHidden = !token;
      if (!token) {
        if (els.settingsTokenAction) els.settingsTokenAction.textContent = '获取中...';
        try {
          const res = await fetch('/api/auth/token', {
            credentials: 'same-origin',
            cache: 'no-store',
          });
          if (res.status === 401) {
            closeSettingsModal();
            showAuthOverlay();
            return;
          }
          if (res.status === 404) {
            resetAccessTokenDisplay();
            showToast('请先重启后端服务以启用密钥查看', true);
            return;
          }
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.token) {
            resetAccessTokenDisplay();
            showToast(data.detail || '获取访问密钥失败', true);
            return;
          }
          token = String(data.token);
          _revealedAccessToken = token;
        } catch (_) {
          resetAccessTokenDisplay();
          showToast('获取访问密钥失败', true);
          return;
        }
      }

      if (els.settingsTokenValue) {
        els.settingsTokenValue.textContent = token;
        els.settingsTokenValue.classList.remove('text-slate-300');
        els.settingsTokenValue.classList.add('text-slate-100');
      }
      if (els.btnTokenHide) {
        els.btnTokenHide.classList.remove('hidden');
      }

      let copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(token);
          copied = true;
        }
      } catch (_) {}
      if (!copied) {
        const ta = document.createElement('textarea');
        ta.value = token;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { copied = document.execCommand('copy'); } catch (_) {}
        document.body.removeChild(ta);
      }

      clearTimeout(_tokenActionResetTimer);
      if (els.settingsTokenAction) {
        els.settingsTokenAction.textContent = copied ? '已复制 ✓' : '点击复制';
        _tokenActionResetTimer = setTimeout(() => {
          if (els.settingsTokenAction && _revealedAccessToken) {
            els.settingsTokenAction.textContent = '复制';
          }
        }, 1600);
      }
      if (copied) {
        showToast(wasHidden ? '已显示并复制访问密钥' : '已复制访问密钥');
      } else {
        showToast(wasHidden ? '已显示密钥，请再次点击复制' : '复制失败，请手动选中复制', true);
      }
    }

    function openSettingsModal(view = 'main') {
      if (!els.settingsModal) return;
      resetAccessTokenDisplay();
      updateArchivedBadge();
      updatePushUi();
      refreshPasskeyBindStatus();
      setSettingsView(view);
      els.settingsModal.classList.remove('hidden');
    }

    function closeSettingsModal() {
      if (!els.settingsModal) return;
      els.settingsModal.classList.add('hidden');
      state.settingsView = 'main';
      resetAccessTokenDisplay();
    }

    els.btnSidebarSettings?.addEventListener('click', () => openSettingsModal('main'));
    els.btnSettingsArchived?.addEventListener('click', () => setSettingsView('archived'));
    els.btnSettingsBack?.addEventListener('click', () => setSettingsView('main'));
    els.btnTokenRevealCopy?.addEventListener('click', handleRevealAndCopyToken);
    els.btnTokenHide?.addEventListener('click', resetAccessTokenDisplay);
    els.archivedSearch?.addEventListener('input', scheduleArchivedSearch);
    $('btn-settings-close')?.addEventListener('click', closeSettingsModal);
    els.settingsModal?.addEventListener('click', (e) => {
      if (e.target === els.settingsModal) closeSettingsModal();
    });

    $('btn-logout').addEventListener('click', async () => {
      closeSettingsModal();
      closeSidebar();
      state.manualClose = true;
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
      clearWsTimers();
      await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
      if (state.ws) {
        try { state.ws.close(); } catch (_) {}
        state.ws = null;
      }
      showAuthOverlay();
    });

