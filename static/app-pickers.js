    // =========================================================================
    // 2-Level Model Picker (Model -> Level) & Status UI
    // =========================================================================
    const LEVEL_ORDER = {
      none: 0, minimal: 1, low: 2, medium: 3, high: 4, xhigh: 5, extra: 5, max: 6,
    };
    const LEVEL_META = {
      none: { label: 'None', desc: '关闭思考' },
      minimal: { label: 'Minimal', desc: '极简思考' },
      low: { label: 'Low', desc: '低 · 响应更快' },
      medium: { label: 'Medium', desc: '中 · 均衡模式' },
      high: { label: 'High', desc: '高 · 深度推理' },
      xhigh: { label: 'XHigh', desc: '极高 · 最强推理' },
      extra: { label: 'Extra', desc: '极高 · 最强推理' },
      max: { label: 'Max', desc: '最高强度' },
    };

    function getModelId(m) {
      return m?.modelId || m?.id || '';
    }

    function getModelLabel(modelId) {
      if (!modelId) return '';
      const found = state.availableModels.find((m) => getModelId(m) === modelId);
      return found?.name || modelId;
    }

    function parseModelEntry(m) {
      const id = getModelId(m);
      const name = String(m?.name || id);
      const match = name.match(/^(.*?)\s*\(([^()]+)\)\s*$/);
      if (match) {
        const base = match[1].trim();
        const tag = match[2].trim();
        const tagKey = tag.toLowerCase();
        if (Object.prototype.hasOwnProperty.call(LEVEL_ORDER, tagKey)) {
          return { id, name, base, level: tag, levelKey: tagKey };
        }
      }
      return { id, name, base: name, level: '', levelKey: '' };
    }

    function buildModelGroups(models) {
      const groups = [];
      const byKey = new Map();
      (models || []).forEach((m) => {
        const parsed = parseModelEntry(m);
        const key = parsed.base;
        let grp = byKey.get(key);
        if (!grp) {
          grp = { key, name: parsed.base, variants: [] };
          byKey.set(key, grp);
          groups.push(grp);
        }
        grp.variants.push(parsed);
      });
      groups.forEach((grp) => {
        if (grp.variants.length > 1) {
          grp.variants.sort((a, b) => (LEVEL_ORDER[a.levelKey] ?? 99) - (LEVEL_ORDER[b.levelKey] ?? 99));
        }
      });
      return groups;
    }

    function modelTagClass(tag) {
      const t = String(tag || '').toLowerCase();
      if (t === 'xhigh' || t === 'extra' || t === 'max') return 'bg-purple-500/20 text-purple-300 border-purple-500/35';
      if (t === 'high') return 'bg-blue-500/20 text-blue-300 border-blue-500/35';
      if (t === 'medium') return 'bg-emerald-500/20 text-emerald-300 border-emerald-500/35';
      if (t === 'low' || t === 'minimal' || t === 'none') return 'bg-slate-700/70 text-slate-300 border-slate-600/60';
      return 'bg-amber-500/15 text-amber-300 border-amber-500/30';
    }

    function selectModelAndClose(modelId) {
      closeModelModal();
      if (modelId && modelId !== state.currentModel) {
        sendWs({ type: 'set_model', model_id: modelId });
      }
    }

    function renderModelList() {
      const groups = buildModelGroups(state.availableModels || []);

      if (state.selectedModelGroupKey) {
        const grp = groups.find((g) => g.key === state.selectedModelGroupKey);
        if (!grp || grp.variants.length <= 1) {
          state.selectedModelGroupKey = null;
        } else {
          els.btnModelBack.classList.remove('hidden');
          els.modelModalTitle.textContent = grp.name;
          els.modelCount.textContent = `${grp.variants.length} 档`;
          els.modelSearchWrap.classList.add('hidden');
          els.modelList.innerHTML = '<div class="px-1 pb-1 text-[11px] text-slate-400">选择推理档位 (Level)</div>';

          grp.variants.forEach((v) => {
            const isActive = v.id === state.currentModel;
            const meta = LEVEL_META[v.levelKey] || { label: v.level || v.name, desc: '' };
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `w-full text-left px-3 py-2.5 rounded-xl border transition active:scale-[0.99] flex items-center justify-between gap-2.5 ${
              isActive ? 'border-blue-500/70 bg-blue-500/15' : 'border-border/70 bg-bg hover:bg-card'
            }`;
            btn.innerHTML = `
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 min-w-0">
                  <span class="text-xs font-semibold px-2 py-0.5 rounded-md border shrink-0 ${modelTagClass(v.levelKey)}">${escapeHtml(v.level || meta.label)}</span>
                  ${meta.desc ? `<span class="text-xs ${isActive ? 'text-slate-100' : 'text-slate-300'} truncate">${escapeHtml(meta.desc)}</span>` : ''}
                </div>
                <div class="text-[11px] font-mono text-slate-400 truncate mt-1">${escapeHtml(v.id)}</div>
              </div>
              ${isActive ? '<svg class="w-4 h-4 text-blue-400 shrink-0" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/></svg>' : ''}
            `;
            btn.addEventListener('click', () => selectModelAndClose(v.id));
            els.modelList.appendChild(btn);
          });
          return;
        }
      }

      els.btnModelBack.classList.add('hidden');
      els.modelModalTitle.textContent = '选择模型';
      els.modelSearchWrap.classList.remove('hidden');

      const q = (els.modelSearch.value || '').trim().toLowerCase();
      const filtered = q
        ? groups.filter(
            (g) =>
              g.name.toLowerCase().includes(q) ||
              g.variants.some((v) => v.id.toLowerCase().includes(q) || v.name.toLowerCase().includes(q))
          )
        : groups;

      els.modelCount.textContent = String(filtered.length);
      if (!filtered.length) {
        els.modelList.innerHTML = `<div class="text-slate-400 text-xs py-8 text-center">${groups.length ? '未找到匹配的模型' : '暂无可用模型'}</div>`;
        return;
      }

      els.modelList.innerHTML = '';
      filtered.forEach((grp) => {
        const activeVariant = grp.variants.find((v) => v.id === state.currentModel) || null;
        const isMulti = grp.variants.length > 1;
        const isActive = Boolean(activeVariant);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `w-full text-left px-3 py-2.5 rounded-xl border transition active:scale-[0.99] flex items-center justify-between gap-2.5 ${
          isActive ? 'border-blue-500/70 bg-blue-500/15' : 'border-border/70 bg-bg hover:bg-card'
        }`;

        if (isMulti) {
          const levelsSummary = grp.variants.map((v) => v.level || v.id).join(' · ');
          btn.innerHTML = `
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="text-sm font-medium ${isActive ? 'text-white' : 'text-slate-200'} truncate">${escapeHtml(grp.name)}</span>
                ${activeVariant?.level ? `<span class="text-[10px] px-1.5 py-0.5 rounded-md border shrink-0 ${modelTagClass(activeVariant.levelKey)}">${escapeHtml(activeVariant.level)}</span>` : ''}
              </div>
              <div class="text-[11px] text-slate-400 truncate mt-0.5">${escapeHtml(levelsSummary)}</div>
            </div>
            <div class="flex items-center gap-1 text-slate-400 shrink-0">
              ${isActive ? '<svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/></svg>' : `<span class="text-[11px]">${grp.variants.length} 档</span>`}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>
            </div>
          `;
          btn.addEventListener('click', () => {
            state.selectedModelGroupKey = grp.key;
            renderModelList();
            els.modelList.scrollTop = 0;
          });
        } else {
          const only = grp.variants[0];
          btn.innerHTML = `
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium ${isActive ? 'text-white' : 'text-slate-200'} truncate">${escapeHtml(only.name)}</div>
              <div class="text-[11px] font-mono text-slate-400 truncate mt-0.5">${escapeHtml(only.id)}</div>
            </div>
            ${isActive ? '<svg class="w-4 h-4 text-blue-400 shrink-0" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/></svg>' : ''}
          `;
          btn.addEventListener('click', () => selectModelAndClose(only.id));
        }

        els.modelList.appendChild(btn);
      });
    }

    function openModelModal() {
      if (!state.availableModels.length) {
        showToast('模型列表尚未加载', true);
        return;
      }
      state.selectedModelGroupKey = null;
      els.modelSearch.value = '';
      renderModelList();
      els.modelModal.classList.remove('hidden');
      els.modelList.scrollTop = 0;
    }

    function closeModelModal() {
      state.selectedModelGroupKey = null;
      els.modelModal.classList.add('hidden');
    }

    function applyStatus(s) {
      if (!s) return;
      const prevSid = state.currentSessionId;
      const prevTitle = els.sessionTitle.textContent;
      const prevBusy = state.isBusy;
      const prevBgBusy = state.bgBusyCount;
      const prevBgPerm = state.bgPermCount;

      state.isBusy = Boolean(s.is_busy);
      els.busyBadge.classList.toggle('hidden', !state.isBusy);
      updateComposerActionButton();
      renderQueueBar();

      const bgBusy = Number(s.bg_busy_count || 0);
      const bgPerm = Number(s.bg_perm_count || 0);
      state.bgBusyCount = bgBusy;
      state.bgPermCount = bgPerm;
      if (bgPerm > 0) {
        els.bgTasksBadge.className = 'text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/35 shrink-0 inline-flex items-center gap-1 active:scale-95 transition';
        els.bgTasksBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-soft-pulse"></span><span>后台待确认 ${bgPerm}</span>`;
      } else if (bgBusy > 0) {
        els.bgTasksBadge.className = 'text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/35 shrink-0 inline-flex items-center gap-1 active:scale-95 transition';
        els.bgTasksBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-soft-pulse"></span><span>后台 ${bgBusy} 运行中</span>`;
      } else {
        els.bgTasksBadge.className = 'hidden';
        els.bgTasksBadge.innerHTML = '';
      }

      els.connDot.className = `w-2 h-2 rounded-full shrink-0 ${
        s.connected ? 'bg-emerald-400' : 'bg-rose-500'
      }`;
      els.sessionTitle.textContent = s.title || 'VibeOne';

      if (typeof s.current_profile === 'string' && s.current_profile) {
        const changed = state.currentProfile && state.currentProfile !== s.current_profile;
        state.currentProfile = s.current_profile;
        state.currentProfileEmail = s.current_profile_email || '';
        renderProfileHeader();
        if (changed) {
          resetFullTextSearchState({ retrigger: true });
          loadProfiles();
        }
      }
      if (typeof s.backend === 'string' && s.backend) {
        state.currentBackend = s.backend;
      } else if (state.currentProfile) {
        state.currentBackend = getProfileBackend(state.currentProfile);
      }

      if (typeof s.session_id === 'string' && s.session_id) {
        if (state.currentSessionId && state.currentSessionId !== s.session_id) {
          clearQuotedReply();
        }
        state.currentSessionId = s.session_id;
      }
      if (typeof s.workdir === 'string' && s.workdir) {
        state.currentWorkdir = s.workdir;
        const dirName = basenamePath(s.workdir);
        els.headerDirLabel.textContent = dirName;
        els.headerDir.title = `当前目录: ${s.workdir}`;
        $('btn-header-new').title = `在 ${dirName} 新建会话`;
      }

      if (s.last_error) {
        els.headerError.textContent = `· ${s.last_error}`;
        els.headerError.classList.remove('hidden');
      } else {
        els.headerError.classList.add('hidden');
      }

      if (Array.isArray(s.available_models)) {
        state.availableModels = s.available_models;
      }
      if (Array.isArray(s.available_commands)) {
        state.availableCommands = s.available_commands;
      }
      if (typeof s.current_model === 'string' && s.current_model) {
        state.currentModel = s.current_model;
      }

      const modelLabel = getModelLabel(state.currentModel);
      els.btnModelLabel.textContent = modelLabel || '选择模型';

      if (!els.modelModal.classList.contains('hidden')) {
        renderModelList();
      }

      if (Array.isArray(s.available_modes) && s.available_modes.length) {
        const prev = els.modeSelect.value;
        els.modeSelect.innerHTML = s.available_modes
          .map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`)
          .join('');
        els.modeSelect.value = s.current_mode || prev || 'auto';
      }

      // Keep sidebar active indicator / titles fresh
      if (
        prevSid !== state.currentSessionId ||
        prevTitle !== els.sessionTitle.textContent ||
        prevBusy !== state.isBusy
      ) {
        renderSidebarSessions();
      }
      if (prevBgBusy !== bgBusy || prevBgPerm !== bgPerm) {
        loadSidebarSessions();
      }
      if (prevBusy && !state.isBusy) {
        collapseCompletedProcessGroups();
      }
    }

    function applyUsage(u) {
      if (!u || !u.size) {
        state.usage = { used: 0, size: 0 };
        els.sidebarUsage.textContent = '上下文: -';
        return;
      }
      state.usage = { used: Number(u.used || 0), size: Number(u.size || 0) };
      const pct = Math.min(100, Math.round((u.used / u.size) * 100));
      els.sidebarUsage.textContent = `上下文: ${(u.used / 1000).toFixed(1)}k / ${(u.size / 1000).toFixed(0)}k (${pct}%)`;
    }

    // =========================================================================
    // Directory Picker Modal
    // =========================================================================
    function openDirModal(initialPath = '') {
      closeSidebar();
      els.dirModal.classList.remove('hidden');
      loadDirectory(initialPath || state.browsePath || state.currentWorkdir || '');
    }

    function closeDirModal() {
      els.dirModal.classList.add('hidden');
    }

    let _newSessionReqAt = 0;
    function requestNewSession(targetPath) {
      const now = Date.now();
      if (now - _newSessionReqAt < 600) return;
      _newSessionReqAt = now;
      const dirLabel = targetPath ? String(targetPath).split('/').filter(Boolean).pop() || targetPath : '';
      showToast(dirLabel ? `正在 ${dirLabel} 创建新会话...` : '正在创建新会话...');
      sendWs({ type: 'new_session', workdir: targetPath });
    }

    function createSessionInDir(targetPath) {
      if (!targetPath) return;
      closeDirModal();
      requestNewSession(targetPath);
    }

    async function loadDirectory(targetPath) {
      els.dirList.innerHTML = '<div class="text-slate-400 text-xs py-8 text-center">正在读取目录...</div>';
      try {
        const params = new URLSearchParams();
        if (targetPath) params.set('path', targetPath);
        if (state.browseShowHidden) params.set('show_hidden', 'true');
        if (state.currentProfile) params.set('profile', state.currentProfile);
        const res = await fetch(`/api/fs/dirs?${params.toString()}`, { credentials: 'same-origin' });
        if (res.status === 401) {
          closeDirModal();
          showAuthOverlay('登录已过期');
          return;
        }
        const data = await res.json();
        if (!res.ok) {
          els.dirList.innerHTML = `<div class="text-rose-400 text-xs py-8 text-center">${escapeHtml(data.detail || '读取目录失败')}</div>`;
          return;
        }

        state.browsePath = data.current;
        state.browseParent = data.parent;
        els.dirPathInput.value = data.current;
        els.btnDirUp.disabled = !data.parent;
        els.dirCurrentBadge.textContent = data.is_git ? `${data.current_name} (git)` : data.current_name;
        els.dirCtaPath.textContent = data.current;
        els.btnDirCreateSession.innerHTML = `<span>＋ 在「${escapeHtml(data.current_name)}」创建新会话</span>`;

        els.dirShortcuts.innerHTML = '';
        (data.shortcuts || []).forEach((sc) => {
          const isCurrentBrowse = sc.path === data.current;
          const chip = document.createElement('button');
          chip.type = 'button';
          chip.className = `px-2.5 py-1 rounded-full border text-[11px] shrink-0 transition active:scale-95 ${
            isCurrentBrowse
              ? 'border-emerald-500/60 bg-emerald-500/15 text-emerald-200'
              : 'border-border bg-bg hover:bg-card text-slate-300'
          }`;
          chip.textContent = sc.label;
          chip.title = sc.path;
          chip.addEventListener('click', () => loadDirectory(sc.path));
          els.dirShortcuts.appendChild(chip);
        });

        const entries = data.entries || [];
        if (!entries.length) {
          els.dirList.innerHTML = '<div class="text-slate-400 text-xs py-8 text-center">此目录下暂无子文件夹，可直接点击下方按钮在此创建会话</div>';
          return;
        }

        els.dirList.innerHTML = '';
        entries.forEach((entry) => {
          const isActiveDir = entry.path === state.currentWorkdir;
          const row = document.createElement('div');
          row.className = `rounded-xl border transition flex items-center justify-between gap-2 px-2.5 py-2 ${
            isActiveDir ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-border/70 bg-bg hover:bg-card'
          }`;

          const navBtn = document.createElement('button');
          navBtn.type = 'button';
          navBtn.className = 'flex-1 min-w-0 flex items-center gap-2 text-left active:opacity-80';
          navBtn.innerHTML = `
            <svg class="w-4 h-4 shrink-0 ${entry.is_git ? 'text-emerald-400' : 'text-blue-400/80'}" fill="none" stroke="currentColor" stroke-width="1.9" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"/></svg>
            <span class="text-sm text-slate-200 truncate">${escapeHtml(entry.name)}</span>
            ${entry.is_git ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 shrink-0">git</span>' : ''}
            ${isActiveDir ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 border border-blue-500/30 text-blue-300 shrink-0">当前</span>' : ''}
          `;
          navBtn.addEventListener('click', () => loadDirectory(entry.path));

          const startBtn = document.createElement('button');
          startBtn.type = 'button';
          startBtn.className = 'px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-blue-600 text-[11px] text-slate-200 hover:text-white active:scale-95 transition shrink-0';
          startBtn.textContent = '新建会话';
          startBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            createSessionInDir(entry.path);
          });

          row.appendChild(navBtn);
          row.appendChild(startBtn);
          els.dirList.appendChild(row);
        });
      } catch (_) {
        els.dirList.innerHTML = '<div class="text-rose-400 text-xs py-8 text-center">获取目录失败，请重试</div>';
      }
    }

