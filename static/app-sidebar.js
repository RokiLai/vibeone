    // =========================================================================
    // Sidebar Project-Grouped Sessions Tree (Remote Control Style)
    // =========================================================================
    let _sidebarSearchTimer = null;
    let _sidebarSearchAbort = null;
    let _archivedSearchTimer = null;
    let _archivedSearchAbort = null;

    function formatMatchTypeLabel(matchType) {
      const map = { user: '我', assistant: 'AI', tool: '工具', thought: '思考', error: '提示' };
      return map[matchType] || '内容';
    }

    function escapeRegExp(str) {
      return String(str || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function highlightSearchSnippet(snippet, rawQuery) {
      const text = String(snippet || '');
      if (!text) return '';
      const terms = Array.from(
        new Set(
          String(rawQuery || '')
            .trim()
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 6)
        )
      ).sort((a, b) => b.length - a.length);
      if (!terms.length) return escapeHtml(text);
      const re = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'gi');
      let out = '';
      let lastIdx = 0;
      let m;
      while ((m = re.exec(text)) !== null) {
        if (m.index > lastIdx) out += escapeHtml(text.slice(lastIdx, m.index));
        out += `<mark class="bg-amber-400/25 text-amber-200 rounded px-0.5">${escapeHtml(m[0])}</mark>`;
        lastIdx = m.index + m[0].length;
        if (m[0].length === 0) break;
      }
      if (lastIdx < text.length) out += escapeHtml(text.slice(lastIdx));
      return out;
    }

    function resetFullTextSearchState({ retrigger = false } = {}) {
      clearTimeout(_sidebarSearchTimer);
      _sidebarSearchTimer = null;
      if (_sidebarSearchAbort) {
        try { _sidebarSearchAbort.abort(); } catch (_) {}
        _sidebarSearchAbort = null;
      }
      state.sidebarSearchQuery = '';
      state.sidebarSearchProfile = '';
      state.sidebarSearchLoading = false;
      state.sidebarSearchHits = new Map();

      clearTimeout(_archivedSearchTimer);
      _archivedSearchTimer = null;
      if (_archivedSearchAbort) {
        try { _archivedSearchAbort.abort(); } catch (_) {}
        _archivedSearchAbort = null;
      }
      state.archivedSearchQuery = '';
      state.archivedSearchProfile = '';
      state.archivedSearchLoading = false;
      state.archivedSearchHits = new Map();

      if (retrigger) {
        if ((els.sidebarSearch?.value || '').trim()) scheduleSidebarSearch();
        if ((els.archivedSearch?.value || '').trim()) scheduleArchivedSearch();
      }
    }

    function scheduleSidebarSearch() {
      const rawQ = (els.sidebarSearch?.value || '').trim();
      clearTimeout(_sidebarSearchTimer);
      _sidebarSearchTimer = null;
      if (_sidebarSearchAbort) {
        try { _sidebarSearchAbort.abort(); } catch (_) {}
        _sidebarSearchAbort = null;
      }
      if (!rawQ) {
        state.sidebarSearchQuery = '';
        state.sidebarSearchProfile = '';
        state.sidebarSearchLoading = false;
        state.sidebarSearchHits = new Map();
        renderSidebarSessions();
        return;
      }
      const reqProfile = state.currentProfile || '';
      state.sidebarSearchLoading = true;
      renderSidebarSessions();
      _sidebarSearchTimer = setTimeout(async () => {
        _sidebarSearchTimer = null;
        const ctrl = new AbortController();
        _sidebarSearchAbort = ctrl;
        try {
          const qs = new URLSearchParams({ q: rawQ, scope: 'active', limit: '50' });
          if (reqProfile) qs.set('profile', reqProfile);
          if (state.currentSessionId) qs.set('session_id', state.currentSessionId);
          const res = await fetch(`/api/sessions/search?${qs.toString()}`, {
            credentials: 'same-origin',
            signal: ctrl.signal,
          });
          if (!res.ok) return;
          const data = await res.json();
          if (ctrl.signal.aborted) return;
          if ((els.sidebarSearch?.value || '').trim() !== rawQ) return;
          if (state.currentProfile && data.current_profile && data.current_profile !== state.currentProfile) {
            return;
          }
          const hitsMap = new Map();
          (data.results || []).forEach((r) => {
            if (r && r.session_id) hitsMap.set(r.session_id, r);
          });
          state.sidebarSearchQuery = rawQ;
          state.sidebarSearchProfile = data.current_profile || reqProfile;
          state.sidebarSearchHits = hitsMap;
        } catch (err) {
          if (err && err.name === 'AbortError') return;
        } finally {
          if (_sidebarSearchAbort === ctrl) {
            _sidebarSearchAbort = null;
            state.sidebarSearchLoading = false;
            renderSidebarSessions();
          }
        }
      }, 200);
    }

    function scheduleArchivedSearch() {
      const rawQ = (els.archivedSearch?.value || '').trim();
      clearTimeout(_archivedSearchTimer);
      _archivedSearchTimer = null;
      if (_archivedSearchAbort) {
        try { _archivedSearchAbort.abort(); } catch (_) {}
        _archivedSearchAbort = null;
      }
      if (!rawQ) {
        state.archivedSearchQuery = '';
        state.archivedSearchProfile = '';
        state.archivedSearchLoading = false;
        state.archivedSearchHits = new Map();
        renderArchivedSessionsList();
        return;
      }
      const reqProfile = state.currentProfile || '';
      state.archivedSearchLoading = true;
      renderArchivedSessionsList();
      _archivedSearchTimer = setTimeout(async () => {
        _archivedSearchTimer = null;
        const ctrl = new AbortController();
        _archivedSearchAbort = ctrl;
        try {
          const qs = new URLSearchParams({ q: rawQ, scope: 'archived', limit: '50' });
          if (reqProfile) qs.set('profile', reqProfile);
          const res = await fetch(`/api/sessions/search?${qs.toString()}`, {
            credentials: 'same-origin',
            signal: ctrl.signal,
          });
          if (!res.ok) return;
          const data = await res.json();
          if (ctrl.signal.aborted) return;
          if ((els.archivedSearch?.value || '').trim() !== rawQ) return;
          if (state.currentProfile && data.current_profile && data.current_profile !== state.currentProfile) {
            return;
          }
          const hitsMap = new Map();
          (data.results || []).forEach((r) => {
            if (r && r.session_id) hitsMap.set(r.session_id, r);
          });
          state.archivedSearchQuery = rawQ;
          state.archivedSearchProfile = data.current_profile || reqProfile;
          state.archivedSearchHits = hitsMap;
        } catch (err) {
          if (err && err.name === 'AbortError') return;
        } finally {
          if (_archivedSearchAbort === ctrl) {
            _archivedSearchAbort = null;
            state.archivedSearchLoading = false;
            renderArchivedSessionsList();
          }
        }
      }, 200);
    }

    function exitBatchArchiveMode() {
      state.batchArchiveDir = null;
      state.batchSelectedIds.clear();
    }

    function updateArchivedBadge() {
      const archivedCount = (state.archivedSessionsCache || []).length;
      if (els.settingsArchivedCount) {
        els.settingsArchivedCount.textContent = String(archivedCount);
        els.settingsArchivedCount.className = archivedCount > 0
          ? 'text-[11px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30'
          : 'text-[11px] px-1.5 py-0.5 rounded-full bg-card border border-border text-slate-400';
      }
    }

    function renderArchivedSessionsList() {
      updateArchivedBadge();
      if (!els.settingsArchivedList) return;
      const rawList = state.archivedSessionsCache || [];
      const rawQ = (els.archivedSearch?.value || '').trim();
      const q = rawQ.toLowerCase();
      const activeHits =
        rawQ &&
        state.archivedSearchQuery === rawQ &&
        (!state.currentProfile || !state.archivedSearchProfile || state.archivedSearchProfile === state.currentProfile)
          ? state.archivedSearchHits
          : new Map();

      const groupMap = new Map();
      const seenIds = new Set();
      const addArchived = (s) => {
        if (!s || !s.session_id || seenIds.has(s.session_id)) return;
        seenIds.add(s.session_id);
        const norm = normalizeDirKey(s.cwd) || '/';
        let grp = groupMap.get(norm);
        if (!grp) {
          grp = { cwd: norm, dirName: basenamePath(norm), sessions: [], latestTs: 0 };
          groupMap.set(norm, grp);
        }
        const tsVal = Number(s.archived_at || s.updated_at || 0);
        grp.latestTs = Math.max(grp.latestTs, tsVal);
        grp.sessions.push(s);
      };
      rawList.forEach(addArchived);
      if (q && activeHits.size > 0) {
        activeHits.forEach((hit) => addArchived(hit));
      }

      let groups = Array.from(groupMap.values());
      if (q) {
        groups = groups
          .map((g) => {
            const dirMatches = g.dirName.toLowerCase().includes(q) || g.cwd.toLowerCase().includes(q);
            const matching = dirMatches
              ? g.sessions
              : g.sessions.filter(
                  (s) => String(s.title || '').toLowerCase().includes(q) || activeHits.has(s.session_id)
                );
            if (!dirMatches && !matching.length) return null;
            return { ...g, sessions: matching };
          })
          .filter(Boolean);
      }

      groups.sort((a, b) => {
        const diff = (b.latestTs || 0) - (a.latestTs || 0);
        if (diff !== 0) return diff;
        return a.dirName.localeCompare(b.dirName);
      });

      if (!groups.length) {
        const emptyText =
          q && state.archivedSearchLoading
            ? '正在当前账号中全文搜索...'
            : rawList.length
            ? '未找到匹配的已归档会话'
            : '暂无已归档会话';
        els.settingsArchivedList.innerHTML = `<div class="text-slate-500 text-xs text-center py-8">${emptyText}</div>`;
        return;
      }

      const dirNameCounts = new Map();
      groups.forEach((g) => dirNameCounts.set(g.dirName, (dirNameCounts.get(g.dirName) || 0) + 1));

      els.settingsArchivedList.innerHTML = '';
      groups.forEach((grp) => {
        const duplicateName = (dirNameCounts.get(grp.dirName) || 0) > 1;
        const pathHint = duplicateName ? parentPathHint(grp.cwd) : '';
        const section = document.createElement('div');
        section.className = 'rounded-xl border border-border/70 bg-bg/60 overflow-hidden';

        const header = document.createElement('div');
        header.className = 'px-2.5 py-2 border-b border-border/50 flex items-center justify-between gap-2 bg-card/40';
        header.innerHTML = `
          <div class="flex items-center gap-1.5 min-w-0">
            <svg class="w-3.5 h-3.5 shrink-0 text-amber-400/80" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"/></svg>
            <div class="min-w-0">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="text-xs font-medium text-slate-200 truncate" title="${escapeHtml(grp.cwd)}">${escapeHtml(grp.dirName)}</span>
                <span class="text-[10px] text-slate-500 shrink-0">${grp.sessions.length}</span>
              </div>
              ${pathHint ? `<div class="text-[10px] text-slate-500 truncate">${escapeHtml(pathHint)}</div>` : ''}
            </div>
          </div>
        `;

        const restoreAllBtn = document.createElement('button');
        restoreAllBtn.type = 'button';
        restoreAllBtn.className = 'px-2 py-0.5 rounded-lg text-[11px] text-emerald-300 hover:bg-emerald-950/70 active:scale-95 transition shrink-0';
        restoreAllBtn.title = `恢复 ${grp.dirName} 下全部已归档会话`;
        restoreAllBtn.textContent = '全部恢复';
        restoreAllBtn.addEventListener('click', () => {
          const ids = grp.sessions.map((s) => s.session_id);
          unarchiveSidebarSessions(ids, `目录「${grp.dirName}」`);
        });
        header.appendChild(restoreAllBtn);
        section.appendChild(header);

        const listWrap = document.createElement('div');
        listWrap.className = 'divide-y divide-border/40';
        grp.sessions.forEach((s) => {
          const row = document.createElement('div');
          row.className = 'flex items-center gap-1 pr-1.5 hover:bg-card/60 transition';

          const itemBtn = document.createElement('button');
          itemBtn.type = 'button';
          itemBtn.className = 'flex-1 min-w-0 text-left px-2.5 py-2 active:scale-[0.99]';
          const rel = formatRelativeTime(s.archived_at || s.updated_at);
          const hit = activeHits.get(s.session_id);
          const snippetHtml =
            hit && hit.snippet
              ? `<div class="mt-1 text-[11px] leading-snug text-slate-400 line-clamp-2 break-words"><span class="inline-block text-[10px] px-1 rounded bg-slate-800/90 text-slate-300 border border-border/60 mr-1">${escapeHtml(
                  formatMatchTypeLabel(hit.match_type)
                )}</span>${highlightSearchSnippet(hit.snippet, rawQ)}${
                  hit.match_count > 1 ? `<span class="ml-1 text-[10px] text-slate-500">(${hit.match_count}处)</span>` : ''
                }</div>`
              : '';
          itemBtn.innerHTML = `
            <div class="flex items-center justify-between gap-2 min-w-0">
              <span class="text-xs text-slate-300 truncate">${escapeHtml(s.title || '新会话')}</span>
              ${rel ? `<span class="text-[10px] text-slate-500 shrink-0">${escapeHtml(rel)}</span>` : ''}
            </div>
            ${snippetHtml}
          `;
          itemBtn.addEventListener('click', () => {
            closeSettingsModal();
            closeSidebar();
            showToast(`正在加载「${s.title || '会话'}」...`);
            sendWs({
              type: 'switch_session',
              session_id: s.session_id,
              workdir: s.cwd,
              title: s.title,
            });
          });
          row.appendChild(itemBtn);

          const restoreBtn = document.createElement('button');
          restoreBtn.type = 'button';
          restoreBtn.className = 'p-1.5 rounded-lg text-slate-400 hover:text-emerald-300 hover:bg-emerald-950/60 active:scale-95 transition shrink-0';
          restoreBtn.title = '恢复此会话到列表';
          restoreBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3"/></svg>';
          restoreBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            unarchiveSidebarSessions([s.session_id], s.title || '新会话');
          });
          row.appendChild(restoreBtn);

          const delBtn = document.createElement('button');
          delBtn.type = 'button';
          delBtn.className = 'p-1.5 rounded-lg text-slate-500 hover:text-rose-300 hover:bg-rose-950/60 active:scale-95 transition shrink-0';
          delBtn.title = '永久移除此会话';
          delBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>';
          delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeSidebarSession(s.session_id, s.title || '新会话');
          });
          row.appendChild(delBtn);

          listWrap.appendChild(row);
        });
        section.appendChild(listWrap);
        els.settingsArchivedList.appendChild(section);
      });
    }

    function renderSidebarSessions() {
      updateArchivedBadge();
      if (els.settingsModal && !els.settingsModal.classList.contains('hidden') && state.settingsView === 'archived') {
        renderArchivedSessionsList();
      }
      const rawQ = (els.sidebarSearch.value || '').trim();
      const q = rawQ.toLowerCase();
      const rawList = state.sessionsCache || [];
      const activeHits =
        rawQ &&
        state.sidebarSearchQuery === rawQ &&
        (!state.currentProfile || !state.sidebarSearchProfile || state.sidebarSearchProfile === state.currentProfile)
          ? state.sidebarSearchHits
          : new Map();

      // Group sessions by normalized project directory (cwd)
      const currentWorkdirKey = normalizeDirKey(state.currentWorkdir);
      const groupMap = new Map();
      const seenSessionIds = new Set();
      const ensureGroup = (cwd) => {
        const norm = normalizeDirKey(cwd) || currentWorkdirKey || '/';
        let grp = groupMap.get(norm);
        if (!grp) {
          grp = {
            cwd: norm,
            dirName: basenamePath(norm),
            sessions: [],
            latestTs: 0,
            hasCurrent: norm === currentWorkdirKey,
          };
          groupMap.set(norm, grp);
        }
        return grp;
      };

      if (currentWorkdirKey) {
        ensureGroup(currentWorkdirKey);
      }

      const addSessionToGroup = (s) => {
        if (!s || !s.session_id || seenSessionIds.has(s.session_id)) return;
        seenSessionIds.add(s.session_id);
        const grp = ensureGroup(s.cwd);
        const isCurrent = s.is_current || s.session_id === state.currentSessionId;
        if (isCurrent) grp.hasCurrent = true;
        const tsVal = Number(s.updated_at || 0);
        grp.latestTs = Math.max(grp.latestTs, tsVal);
        grp.sessions.push({ ...s, isCurrent });
      };

      rawList.forEach(addSessionToGroup);
      if (q && activeHits.size > 0) {
        activeHits.forEach((hit) => addSessionToGroup(hit));
      }

      let groups = Array.from(groupMap.values());
      if (q) {
        groups = groups
          .map((g) => {
            const dirMatches =
              g.dirName.toLowerCase().includes(q) || g.cwd.toLowerCase().includes(q);
            const matchingSessions = dirMatches
              ? g.sessions
              : g.sessions.filter(
                  (s) =>
                    String(s.title || '').toLowerCase().includes(q) ||
                    activeHits.has(s.session_id)
                );
            if (!dirMatches && !matchingSessions.length) return null;
            return { ...g, sessions: matchingSessions };
          })
          .filter(Boolean);
      }

      groups.sort((a, b) => {
        const diff = (b.latestTs || 0) - (a.latestTs || 0);
        if (diff !== 0) return diff;
        return a.dirName.localeCompare(b.dirName);
      });

      if (state.batchArchiveDir && !groups.some((g) => g.cwd === state.batchArchiveDir && g.sessions.length > 0)) {
        exitBatchArchiveMode();
      }

      if (!groups.length) {
        const emptyText =
          q && state.sidebarSearchLoading
            ? '正在当前账号中全文搜索...'
            : rawList.length || q
            ? '未找到匹配的项目、会话或消息'
            : '暂无历史会话';
        els.sidebarSessions.innerHTML = `<div class="text-slate-500 text-xs text-center py-8">${emptyText}</div>`;
        return;
      }

      const dirNameCounts = new Map();
      groups.forEach((g) => {
        dirNameCounts.set(g.dirName, (dirNameCounts.get(g.dirName) || 0) + 1);
      });

      const getSessionActivity = (s) => {
        const isCurrent = Boolean(s.isCurrent || s.session_id === state.currentSessionId);
        const sessBusy = Boolean(s.is_busy || (isCurrent && state.isBusy));
        const sessPerm = Math.max(
          Number(s.pending_permissions || 0),
          isCurrent ? state.pendingPermissions.size : 0
        );
        return { isCurrent, sessBusy, sessPerm };
      };

      const switchToSidebarSession = (s, isCurrent) => {
        closeSidebar();
        if (!isCurrent) {
          showToast(`正在加载「${s.title || '会话'}」...`);
          sendWs({
            type: 'switch_session',
            session_id: s.session_id,
            workdir: s.cwd,
            title: s.title,
          });
        } else if (!state.isBusy) {
          syncCurrentSession({ silent: false });
        }
      };

      els.sidebarSessions.innerHTML = '';

      // Pinned "Running / Pending Permission" Quick-Jump Section at the top of sidebar
      if (!q) {
        const activeSessions = [];
        groups.forEach((grp) => {
          const duplicateName = (dirNameCounts.get(grp.dirName) || 0) > 1;
          const pathHint = duplicateName ? parentPathHint(grp.cwd) : '';
          grp.sessions.forEach((s) => {
            const { isCurrent, sessBusy, sessPerm } = getSessionActivity(s);
            if (sessPerm > 0 || sessBusy) {
              activeSessions.push({
                session: s,
                grp,
                pathHint,
                isCurrent,
                sessBusy,
                sessPerm,
              });
            }
          });
        });

        if (activeSessions.length > 0) {
          activeSessions.sort((a, b) => {
            if ((b.sessPerm > 0) !== (a.sessPerm > 0)) return b.sessPerm > 0 ? 1 : -1;
            return Number(b.session.updated_at || 0) - Number(a.session.updated_at || 0);
          });

          const totalPerm = activeSessions.filter((x) => x.sessPerm > 0).length;
          const totalRunningOnly = activeSessions.length - totalPerm;
          const hasAnyPerm = totalPerm > 0;

          const runningSec = document.createElement('div');
          runningSec.id = 'sidebar-running-section';
          runningSec.className = `rounded-xl border p-1.5 space-y-1 transition ${
            hasAnyPerm
              ? 'border-amber-500/35 bg-amber-950/20'
              : 'border-blue-500/30 bg-blue-950/20'
          }`;

          const runningHeader = document.createElement('button');
          runningHeader.type = 'button';
          runningHeader.className = 'w-full flex items-center justify-between gap-2 px-1.5 py-1 rounded-lg hover:bg-white/5 text-left transition';
          const summaryParts = [];
          if (totalPerm > 0) summaryParts.push(`${totalPerm} 待确认`);
          if (totalRunningOnly > 0) summaryParts.push(`${totalRunningOnly} 运行中`);
          runningHeader.innerHTML = `
            <span class="flex items-center gap-1.5 min-w-0">
              <span class="w-1.5 h-1.5 rounded-full shrink-0 animate-soft-pulse ${hasAnyPerm ? 'bg-amber-400' : 'bg-blue-400'}"></span>
              <span class="text-[11px] font-semibold ${hasAnyPerm ? 'text-amber-200' : 'text-blue-200'} truncate">运行中任务</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ${
                hasAnyPerm
                  ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                  : 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
              }">${escapeHtml(summaryParts.join(' · '))}</span>
            </span>
            <svg class="w-3 h-3 text-slate-400 shrink-0 transition-transform ${state.runningSectionCollapsed ? '-rotate-90' : ''}" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
          `;
          runningHeader.addEventListener('click', () => {
            state.runningSectionCollapsed = !state.runningSectionCollapsed;
            renderSidebarSessions();
          });
          runningSec.appendChild(runningHeader);

          if (!state.runningSectionCollapsed) {
            const runningList = document.createElement('div');
            runningList.className = 'space-y-0.5';
            activeSessions.forEach(({ session: s, grp, pathHint, isCurrent, sessPerm }) => {
              const btn = document.createElement('button');
              btn.type = 'button';
              btn.className = `w-full text-left px-2 py-1.5 rounded-lg transition active:scale-[0.99] flex items-center justify-between gap-2 ${
                isCurrent
                  ? 'bg-blue-600/25 border border-blue-500/45 text-white'
                  : 'border border-transparent hover:bg-card/80 text-slate-200'
              }`;
              const dirLabel = pathHint ? `${grp.dirName} · ${pathHint}` : grp.dirName;
              const badgeHtml =
                sessPerm > 0
                  ? '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/35 shrink-0">待确认</span>'
                  : '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/35 shrink-0">运行中</span>';
              btn.innerHTML = `
                <div class="flex items-start gap-2 min-w-0 flex-1">
                  <span class="w-1.5 h-1.5 rounded-full shrink-0 mt-1.5 animate-soft-pulse ${
                    sessPerm > 0 ? 'bg-amber-400' : 'bg-blue-400'
                  }"></span>
                  <div class="min-w-0 flex-1">
                    <div class="text-xs ${isCurrent ? 'font-medium text-white' : 'text-slate-200'} truncate">${escapeHtml(s.title || '新会话')}</div>
                    <div class="text-[10px] text-slate-400 truncate flex items-center gap-1 mt-0.5">
                      <svg class="w-2.5 h-2.5 shrink-0 text-slate-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"/></svg>
                      <span class="truncate font-mono">${escapeHtml(dirLabel)}</span>
                    </div>
                  </div>
                </div>
                ${badgeHtml}
              `;
              btn.addEventListener('click', () => {
                switchToSidebarSession(s, isCurrent);
              });
              runningList.appendChild(btn);
            });
            runningSec.appendChild(runningList);
          }

          els.sidebarSessions.appendChild(runningSec);
        }
      }

      groups.forEach((grp) => {
        const isBatchDir = state.batchArchiveDir === grp.cwd;
        const isCollapsed = !q && !isBatchDir && state.collapsedDirs.has(grp.cwd);
        const duplicateName = (dirNameCounts.get(grp.dirName) || 0) > 1;
        const pathHint = duplicateName ? parentPathHint(grp.cwd) : '';
        let grpPermCount = 0;
        let grpRunningOnlyCount = 0;
        grp.sessions.forEach((s) => {
          const { sessBusy, sessPerm } = getSessionActivity(s);
          if (sessPerm > 0) grpPermCount += 1;
          else if (sessBusy) grpRunningOnlyCount += 1;
        });
        const section = document.createElement('div');
        section.className = 'space-y-0.5';

        // Project Directory Header Row
        const headerRow = document.createElement('div');
        headerRow.className = `group flex items-center justify-between gap-1 px-2 py-1.5 rounded-lg transition ${
          isBatchDir
            ? 'bg-amber-500/10'
            : isCollapsed && grpPermCount > 0
            ? 'bg-amber-500/10 hover:bg-amber-500/15 border border-amber-500/25'
            : isCollapsed && grpRunningOnlyCount > 0
            ? 'bg-blue-500/10 hover:bg-blue-500/15 border border-blue-500/25'
            : 'hover:bg-card/70'
        }`;

        const folderIconColor =
          grpPermCount > 0
            ? 'text-amber-400'
            : grpRunningOnlyCount > 0
            ? 'text-blue-400'
            : grp.hasCurrent
            ? 'text-emerald-400'
            : 'text-slate-400';

        const dirStatusBadges = [
          grpPermCount > 0
            ? `<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-[1px] rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/35 shrink-0" title="${grpPermCount} 个会话待确认"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-soft-pulse"></span><span>${grpPermCount}</span></span>`
            : '',
          grpRunningOnlyCount > 0
            ? `<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-[1px] rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/35 shrink-0" title="${grpRunningOnlyCount} 个会话运行中"><span class="w-1.5 h-1.5 rounded-full bg-blue-400 animate-soft-pulse"></span><span>${grpRunningOnlyCount}</span></span>`
            : '',
        ].join('');

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'flex-1 min-w-0 flex items-center gap-1.5 text-left';
        toggleBtn.title = grp.cwd;
        toggleBtn.innerHTML = `
          <svg class="w-3 h-3 text-slate-500 shrink-0 transition-transform ${isCollapsed ? '-rotate-90' : ''}" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
          <svg class="w-3.5 h-3.5 shrink-0 ${folderIconColor}" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"/></svg>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5 min-w-0">
              <span class="text-xs font-semibold ${grp.hasCurrent || grpPermCount > 0 || grpRunningOnlyCount > 0 ? 'text-slate-100' : 'text-slate-300'} truncate">${escapeHtml(grp.dirName)}</span>
              ${grp.sessions.length > 0 ? `<span class="text-[10px] text-slate-500 shrink-0">${grp.sessions.length}</span>` : ''}
              ${dirStatusBadges}
            </div>
            ${pathHint ? `<div class="text-[10px] text-slate-500 truncate">${escapeHtml(pathHint)}</div>` : ''}
          </div>
        `;
        toggleBtn.addEventListener('click', () => {
          if (state.collapsedDirs.has(grp.cwd)) state.collapsedDirs.delete(grp.cwd);
          else state.collapsedDirs.add(grp.cwd);
          renderSidebarSessions();
        });

        const headerActions = document.createElement('div');
        headerActions.className = 'flex items-center gap-0.5 shrink-0';

        const addInDirBtn = document.createElement('button');
        addInDirBtn.type = 'button';
        addInDirBtn.className = 'p-1 rounded-md text-slate-400 hover:text-white hover:bg-blue-600/80 active:scale-95 transition';
        addInDirBtn.title = `在 ${grp.dirName} 新建会话`;
        addInDirBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>';
        addInDirBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          closeSidebar();
          requestNewSession(grp.cwd);
        });
        headerActions.appendChild(addInDirBtn);

        if (grp.sessions.length > 0) {
          const batchBtn = document.createElement('button');
          batchBtn.type = 'button';
          batchBtn.className = isBatchDir
            ? 'p-1 rounded-md text-amber-200 bg-amber-500/25 active:scale-95 transition'
            : 'p-1 rounded-md text-slate-500 hover:text-amber-300 hover:bg-amber-950/60 active:scale-95 transition';
          batchBtn.title = isBatchDir ? '退出批量归档' : `批量归档 ${grp.dirName} 下的会话`;
          batchBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/></svg>';
          batchBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (state.batchArchiveDir === grp.cwd) {
              exitBatchArchiveMode();
            } else {
              state.batchArchiveDir = grp.cwd;
              state.batchSelectedIds.clear();
              state.collapsedDirs.delete(grp.cwd);
            }
            renderSidebarSessions();
          });
          headerActions.appendChild(batchBtn);
        }

        const removeDirBtn = document.createElement('button');
        removeDirBtn.type = 'button';
        removeDirBtn.className = 'p-1 rounded-md text-slate-500 hover:text-rose-300 hover:bg-rose-950/60 active:scale-95 transition';
        removeDirBtn.title = `从侧边栏移除目录 ${grp.dirName}`;
        removeDirBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/></svg>';
        removeDirBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          removeSidebarWorkdir(grp.cwd, grp.dirName, grp.sessions.length);
        });
        headerActions.appendChild(removeDirBtn);

        headerRow.appendChild(toggleBtn);
        headerRow.appendChild(headerActions);
        section.appendChild(headerRow);

        if (isBatchDir) {
          const validIds = new Set(grp.sessions.map((s) => s.session_id));
          for (const sid of Array.from(state.batchSelectedIds)) {
            if (!validIds.has(sid)) state.batchSelectedIds.delete(sid);
          }
          const selCount = state.batchSelectedIds.size;
          const allSelected = grp.sessions.length > 0 && selCount === grp.sessions.length;
          const toolbar = document.createElement('div');
          toolbar.className = 'ml-3 mr-0.5 my-1 p-2 rounded-xl border border-amber-500/35 bg-amber-950/20 space-y-2';

          const chipsRow = document.createElement('div');
          chipsRow.className = 'flex flex-wrap items-center gap-1';

          const makeChip = (label, onClick) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'px-2 py-0.5 rounded-lg border border-border/80 bg-bg/90 hover:bg-card text-[10px] text-slate-300 hover:text-white active:scale-95 transition';
            btn.textContent = label;
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              onClick();
              renderSidebarSessions();
            });
            return btn;
          };

          chipsRow.appendChild(makeChip(allSelected ? '清空' : '全选', () => {
            state.batchSelectedIds.clear();
            if (!allSelected) {
              grp.sessions.forEach((s) => state.batchSelectedIds.add(s.session_id));
            }
          }));

          chipsRow.appendChild(makeChip('保留当前', () => {
            state.batchSelectedIds.clear();
            grp.sessions.forEach((s) => {
              const isCur = s.isCurrent || s.session_id === state.currentSessionId;
              const busy = Boolean(s.is_busy || (isCur && state.isBusy));
              if (!isCur && !busy) state.batchSelectedIds.add(s.session_id);
            });
          }));

          const sevenDaysAgo = Date.now() / 1000 - 7 * 86400;
          const olderIds = grp.sessions
            .filter((s) => {
              const isCur = s.isCurrent || s.session_id === state.currentSessionId;
              const busy = Boolean(s.is_busy || (isCur && state.isBusy));
              const ts = Number(s.updated_at || 0);
              return !isCur && !busy && ts > 0 && ts < sevenDaysAgo;
            })
            .map((s) => s.session_id);
          if (olderIds.length > 0) {
            chipsRow.appendChild(makeChip(`7天前(${olderIds.length})`, () => {
              state.batchSelectedIds.clear();
              olderIds.forEach((id) => state.batchSelectedIds.add(id));
            }));
          }

          if (grp.sessions.length > 3) {
            chipsRow.appendChild(makeChip('仅留3条', () => {
              state.batchSelectedIds.clear();
              grp.sessions.slice(3).forEach((s) => {
                const isCur = s.isCurrent || s.session_id === state.currentSessionId;
                const busy = Boolean(s.is_busy || (isCur && state.isBusy));
                if (!isCur && !busy) state.batchSelectedIds.add(s.session_id);
              });
            }));
          }

          const actionRow = document.createElement('div');
          actionRow.className = 'flex items-center justify-between gap-2 pt-0.5';

          const countInfo = document.createElement('span');
          countInfo.className = 'text-[10px] text-amber-200/90';
          countInfo.textContent = `已选 ${selCount} / ${grp.sessions.length}`;

          const btnGroup = document.createElement('div');
          btnGroup.className = 'flex items-center gap-1';

          const cancelBtn = document.createElement('button');
          cancelBtn.type = 'button';
          cancelBtn.className = 'px-2 py-1 rounded-lg border border-border/80 bg-bg hover:bg-card text-[11px] text-slate-300 active:scale-95 transition';
          cancelBtn.textContent = '取消';
          cancelBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            exitBatchArchiveMode();
            renderSidebarSessions();
          });

          const confirmBtn = document.createElement('button');
          confirmBtn.type = 'button';
          confirmBtn.disabled = selCount === 0 || state.batchBusy;
          confirmBtn.className = 'px-2.5 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-40 disabled:pointer-events-none text-[11px] font-medium text-white active:scale-95 transition';
          confirmBtn.textContent = state.batchBusy ? '处理中...' : `归档 (${selCount})`;
          confirmBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const ids = Array.from(state.batchSelectedIds);
            if (!ids.length) return;
            archiveSidebarSessions(ids, grp.dirName);
          });

          btnGroup.appendChild(cancelBtn);
          btnGroup.appendChild(confirmBtn);
          actionRow.appendChild(countInfo);
          actionRow.appendChild(btnGroup);
          toolbar.appendChild(chipsRow);
          toolbar.appendChild(actionRow);
          section.appendChild(toolbar);
        }

        if (!isCollapsed) {
          const listWrap = document.createElement('div');
          listWrap.className = 'pl-3 space-y-0.5';

          if (!grp.sessions.length) {
            const emptyHint = document.createElement('div');
            emptyHint.className = 'px-2.5 py-1 text-[11px] text-slate-500';
            emptyHint.textContent = '暂无会话，点击右侧 + 新建';
            listWrap.appendChild(emptyHint);
          }

          grp.sessions.forEach((s) => {
            const isCurrent = s.isCurrent || s.session_id === state.currentSessionId;
            const isSelected = isBatchDir && state.batchSelectedIds.has(s.session_id);
            const rowWrap = document.createElement('div');
            rowWrap.className = `group/sess rounded-xl transition flex items-center gap-1 pr-1 ${
              isSelected
                ? 'bg-amber-500/15 border border-amber-500/45 text-white'
                : isCurrent
                ? 'bg-blue-600/20 border border-blue-500/45 text-white'
                : 'border border-transparent hover:bg-card text-slate-300'
            }`;

            const itemBtn = document.createElement('button');
            itemBtn.type = 'button';
            itemBtn.className = 'flex-1 min-w-0 text-left px-2.5 py-2 active:scale-[0.99]';
            const rel = formatRelativeTime(s.updated_at);
            const { sessBusy, sessPerm } = getSessionActivity(s);
            const hit = activeHits.get(s.session_id);
            const leftIndicator = isBatchDir
              ? `<span class="w-4 h-4 rounded-md border flex items-center justify-center shrink-0 transition ${
                  isSelected
                    ? 'bg-amber-500 border-amber-400 text-slate-950'
                    : 'border-slate-600 bg-bg/80 text-transparent'
                }"><svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/></svg></span>`
              : sessPerm > 0
              ? '<span class="w-1.5 h-1.5 rounded-full shrink-0 bg-amber-400 animate-soft-pulse"></span>'
              : sessBusy
              ? '<span class="w-1.5 h-1.5 rounded-full shrink-0 bg-blue-400 animate-soft-pulse"></span>'
              : isCurrent
              ? '<span class="w-1.5 h-1.5 rounded-full shrink-0 bg-emerald-400"></span>'
              : '<span class="w-1.5 h-1.5 rounded-full shrink-0 bg-slate-600/70"></span>';
            const rightBadge = sessPerm > 0
              ? '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/35 shrink-0">待确认</span>'
              : sessBusy
              ? '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/35 shrink-0">运行中</span>'
              : (rel ? `<span class="text-[10px] text-slate-500 shrink-0">${escapeHtml(rel)}</span>` : '');
            const snippetHtml =
              !isBatchDir && hit && hit.snippet
                ? `<div class="mt-1 pl-3.5 text-[11px] leading-snug text-slate-400 line-clamp-2 break-words"><span class="inline-block text-[10px] px-1 rounded bg-slate-800/90 text-slate-300 border border-border/60 mr-1">${escapeHtml(
                    formatMatchTypeLabel(hit.match_type)
                  )}</span>${highlightSearchSnippet(hit.snippet, rawQ)}${
                    hit.match_count > 1 ? `<span class="ml-1 text-[10px] text-slate-500">(${hit.match_count}处)</span>` : ''
                  }</div>`
                : '';
            itemBtn.innerHTML = `
              <div class="flex items-center justify-between gap-2 min-w-0">
                <div class="flex items-center gap-2 min-w-0 flex-1">
                  ${leftIndicator}
                  <span class="text-xs ${isCurrent || isSelected ? 'font-medium text-white' : 'text-slate-300'} truncate">${escapeHtml(s.title || '新会话')}</span>
                </div>
                ${rightBadge}
              </div>
              ${snippetHtml}
            `;
            itemBtn.addEventListener('click', () => {
              if (isBatchDir) {
                if (state.batchSelectedIds.has(s.session_id)) state.batchSelectedIds.delete(s.session_id);
                else state.batchSelectedIds.add(s.session_id);
                renderSidebarSessions();
                return;
              }
              switchToSidebarSession(s, isCurrent);
            });

            rowWrap.appendChild(itemBtn);

            if (!isBatchDir) {
              const delBtn = document.createElement('button');
              delBtn.type = 'button';
              delBtn.className = 'p-1.5 rounded-lg text-slate-500 hover:text-rose-300 hover:bg-rose-950/60 active:scale-95 transition shrink-0';
              delBtn.title = '从侧边栏移除此会话';
              delBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>';
              delBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                removeSidebarSession(s.session_id, s.title || '新会话');
              });
              rowWrap.appendChild(delBtn);
            }

            listWrap.appendChild(rowWrap);
          });
          section.appendChild(listWrap);
        }

        els.sidebarSessions.appendChild(section);
      });
    }

    async function archiveSidebarSessions(sessionIds, dirName) {
      const ids = Array.from(new Set((sessionIds || []).filter(Boolean)));
      if (!ids.length || state.batchBusy) return;
      const includesCurrent = ids.includes(state.currentSessionId);
      const curHint = includesCurrent ? '（包含当前会话，将自动切换到其他会话）' : '';
      if (ids.length >= 5 || includesCurrent) {
        if (!confirm(`确定归档「${dirName}」下的 ${ids.length} 条会话吗？${curHint}\n（记录会完整保留，随时可在「设置 → 已归档会话」中查看或恢复）`)) {
          return;
        }
      }
      state.batchBusy = true;
      renderSidebarSessions();
      try {
        const res = await fetch('/api/sessions/archive', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ session_ids: ids, profile: state.currentProfile || undefined }),
        });
        if (res.status === 404) {
          showToast('请先重启后端服务以启用归档功能', true);
          return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast(data.detail || '归档会话失败', true);
          return;
        }
        exitBatchArchiveMode();
        await loadSidebarSessions(true);
        showToast(`已归档 ${data.archived_count || ids.length} 条会话，可在「设置 → 已归档会话」中恢复`);
      } catch (_) {
        showToast('归档会话失败', true);
      } finally {
        state.batchBusy = false;
        renderSidebarSessions();
      }
    }

    async function unarchiveSidebarSessions(sessionIds, label) {
      const ids = Array.from(new Set((sessionIds || []).filter(Boolean)));
      if (!ids.length || state.batchBusy) return;
      state.batchBusy = true;
      renderSidebarSessions();
      try {
        const res = await fetch('/api/sessions/unarchive', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ session_ids: ids, profile: state.currentProfile || undefined }),
        });
        if (res.status === 404) {
          showToast('请先重启后端服务以启用归档恢复功能', true);
          return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast(data.detail || '恢复会话失败', true);
          return;
        }
        exitBatchArchiveMode();
        await loadSidebarSessions(true);
        const count = data.unarchived_count || ids.length;
        showToast(ids.length === 1 ? `已恢复会话「${label}」` : `已恢复 ${count} 条会话`);
      } catch (_) {
        showToast('恢复会话失败', true);
      } finally {
        state.batchBusy = false;
        renderSidebarSessions();
      }
    }

    async function removeSidebarSession(sessionId, title) {
      if (!confirm(`确定从侧边栏移除会话「${title}」吗？`)) return;
      try {
        const res = await fetch('/api/session/remove', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ session_id: sessionId, profile: state.currentProfile || undefined }),
        });
        if (res.status === 404) {
          showToast('请先重启后端服务以启用移除功能', true);
          return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast(data.detail || '移除会话失败', true);
          return;
        }
        state.sessionsCache = (state.sessionsCache || []).filter((s) => s.session_id !== sessionId);
        state.archivedSessionsCache = (state.archivedSessionsCache || []).filter((s) => s.session_id !== sessionId);
        renderSidebarSessions();
        showToast(`已移除会话「${title}」`);
      } catch (_) {
        showToast('移除会话失败', true);
      }
    }

    async function removeSidebarWorkdir(cwd, dirName, count) {
      const hint = count > 0 ? `及其下 ${count} 条会话记录` : '';
      if (!confirm(`确定从侧边栏移除目录「${dirName}」${hint}吗？\n（仅从列表移除，不会删除本地磁盘文件）`)) return;
      try {
        const res = await fetch('/api/workspace/remove', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ workdir: cwd, profile: state.currentProfile || undefined }),
        });
        if (res.status === 404) {
          showToast('请先重启后端服务以启用移除功能', true);
          return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast(data.detail || '移除目录失败', true);
          return;
        }
        const targetKey = normalizeDirKey(cwd);
        state.sessionsCache = (state.sessionsCache || []).filter((s) => normalizeDirKey(s.cwd) !== targetKey);
        renderSidebarSessions();
        showToast(`已移除目录「${dirName}」`);
      } catch (_) {
        showToast('移除目录失败', true);
      }
    }

    let _sessionsLoadInFlight = null;
    let _sessionsLoadQueued = false;
    let _sessionsLoadTimer = null;

    async function _doLoadSidebarSessions() {
      if (_sessionsLoadInFlight) {
        _sessionsLoadQueued = true;
        return _sessionsLoadInFlight;
      }
      _sessionsLoadInFlight = (async () => {
        try {
          const qs = new URLSearchParams();
          if (state.currentProfile) qs.set('profile', state.currentProfile);
          if (state.currentSessionId) qs.set('session_id', state.currentSessionId);
          const url = qs.toString() ? `/api/sessions?${qs.toString()}` : '/api/sessions';
          const res = await fetch(url, { credentials: 'same-origin' });
          if (res.status === 401 || !res.ok) return;
          const data = await res.json();
          if (data.current_profile && state.currentProfile && data.current_profile !== state.currentProfile) {
            return;
          }
          state.sessionsCache = data.sessions || [];
          state.archivedSessionsCache = data.archived_sessions || [];
          renderSidebarSessions();
        } catch (_) {
        } finally {
          _sessionsLoadInFlight = null;
          if (_sessionsLoadQueued) {
            _sessionsLoadQueued = false;
            _doLoadSidebarSessions();
          }
        }
      })();
      return _sessionsLoadInFlight;
    }

    function loadSidebarSessions(immediate = false) {
      if (immediate) {
        clearTimeout(_sessionsLoadTimer);
        _sessionsLoadTimer = null;
        return _doLoadSidebarSessions();
      }
      if (_sessionsLoadTimer) return;
      _sessionsLoadTimer = setTimeout(() => {
        _sessionsLoadTimer = null;
        _doLoadSidebarSessions();
      }, 120);
    }

    let _sessionSyncInFlight = false;
    async function syncCurrentSession({ silent = true } = {}) {
      if (_sessionSyncInFlight || state.isBusy || !state.currentSessionId) return;
      _sessionSyncInFlight = true;
      try {
        const res = await fetch('/api/session/sync', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            session_id: state.currentSessionId,
            profile: state.currentProfile || undefined,
            force: true,
          }),
        });
        if (!res.ok) return;
        const data = await res.json();
        const added = Number(data.added || 0);
        if (added > 0) {
          showToast(`已同步 ${added} 条最新终端记录`);
        } else if (!silent) {
          showToast('当前会话已是最新');
        }
      } catch (_) {
      } finally {
        _sessionSyncInFlight = false;
      }
    }

    els.sidebarSearch.addEventListener('input', scheduleSidebarSearch);
    $('btn-sidebar-refresh').addEventListener('click', () => {
      loadSidebarSessions(true);
      syncCurrentSession({ silent: true });
      showToast('已刷新会话列表');
    });

