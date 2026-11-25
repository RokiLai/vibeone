    // =========================================================================
    // Event Bindings: Mode, Model, Directory Picker, Diff, Logout
    // =========================================================================
    els.modeSelect.addEventListener('change', (e) => {
      sendWs({ type: 'set_mode', mode_id: e.target.value });
    });

    els.btnModel.addEventListener('click', openModelModal);
    els.btnModelBack.addEventListener('click', () => {
      state.selectedModelGroupKey = null;
      renderModelList();
      els.modelList.scrollTop = 0;
    });
    $('btn-model-close').addEventListener('click', closeModelModal);
    els.modelModal.addEventListener('click', (e) => {
      if (e.target === els.modelModal) closeModelModal();
    });
    els.modelSearch.addEventListener('input', renderModelList);

    $('btn-sidebar-new').addEventListener('click', () => openDirModal(state.currentWorkdir));
    $('btn-header-new').addEventListener('click', () => {
      if (state.currentWorkdir) {
        requestNewSession(state.currentWorkdir);
      } else {
        openDirModal('');
      }
    });
    $('btn-dir-close').addEventListener('click', closeDirModal);
    els.dirModal.addEventListener('click', (e) => {
      if (e.target === els.dirModal) closeDirModal();
    });
    els.dirPathForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const target = els.dirPathInput.value.trim();
      if (target) loadDirectory(target);
    });
    els.btnDirUp.addEventListener('click', () => {
      if (state.browseParent) loadDirectory(state.browseParent);
    });
    els.btnDirHidden.addEventListener('click', () => {
      state.browseShowHidden = !state.browseShowHidden;
      els.btnDirHidden.className = `px-2 py-1 rounded-lg border text-[11px] ${
        state.browseShowHidden
          ? 'border-blue-500/50 bg-blue-500/15 text-blue-300'
          : 'border-border bg-bg hover:bg-card text-slate-400'
      }`;
      loadDirectory(state.browsePath || state.currentWorkdir);
    });
    els.btnDirMkdir.addEventListener('click', async () => {
      const parentPath = state.browsePath || state.currentWorkdir;
      if (!parentPath) return;
      const name = prompt(`在 ${parentPath} 下新建文件夹名称：`);
      if (!name || !name.trim()) return;
      try {
        const res = await fetch('/api/fs/mkdir', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ parent_path: parentPath, name: name.trim() }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast(data.detail || '创建目录失败', true);
          return;
        }
        showToast(`已创建目录: ${data.name}`);
        loadDirectory(data.path);
      } catch (_) {
        showToast('创建目录失败', true);
      }
    });
    els.btnDirCreateSession.addEventListener('click', () => {
      createSessionInDir(state.browsePath || state.currentWorkdir);
    });

