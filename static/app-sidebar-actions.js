    function renderSidebarRunningSection(groups, dirNameCounts, getSessionActivity, switchToSidebarSession) {
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

    function renderSidebarBatchToolbar(grp, section) {
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
    let _sessionsLoadAbort = null;
    let _sessionsLoadProfile = '';
    let _sessionsLoadQueued = false;
    let _sessionsLoadQueuedRefresh = false;
    let _sessionsLoadTimer = null;

    async function _doLoadSidebarSessions({ forceRefresh = false } = {}) {
      const reqProfile = state.currentProfile || '';
      if (_sessionsLoadInFlight) {
        if (_sessionsLoadProfile !== reqProfile || forceRefresh) {
          try { _sessionsLoadAbort?.abort(); } catch (_) {}
          _sessionsLoadAbort = null;
          _sessionsLoadInFlight = null;
        } else {
          _sessionsLoadQueued = true;
          return _sessionsLoadInFlight;
        }
      }
      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      _sessionsLoadAbort = controller;
      _sessionsLoadProfile = reqProfile;
      const run = (async () => {
        try {
          const qs = new URLSearchParams();
          if (reqProfile) qs.set('profile', reqProfile);
          if (state.currentSessionId) qs.set('session_id', state.currentSessionId);
          if (forceRefresh) qs.set('refresh', '1');
          const url = qs.toString() ? `/api/sessions?${qs.toString()}` : '/api/sessions';
          const res = await fetch(url, {
            credentials: 'same-origin',
            signal: controller ? controller.signal : undefined,
          });
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
          if (_sessionsLoadInFlight === run) {
            _sessionsLoadInFlight = null;
            _sessionsLoadAbort = null;
            if (_sessionsLoadQueued) {
              const nextRefresh = _sessionsLoadQueuedRefresh;
              _sessionsLoadQueued = false;
              _sessionsLoadQueuedRefresh = false;
              _doLoadSidebarSessions({ forceRefresh: nextRefresh });
            }
          }
        }
      })();
      _sessionsLoadInFlight = run;
      return run;
    }

    function loadSidebarSessions(immediate = false, opts = {}) {
      const forceRefresh = Boolean(opts && opts.forceRefresh);
      if (immediate) {
        clearTimeout(_sessionsLoadTimer);
        _sessionsLoadTimer = null;
        return _doLoadSidebarSessions({ forceRefresh });
      }
      if (forceRefresh) {
        _sessionsLoadQueuedRefresh = true;
      }
      if (_sessionsLoadTimer) return;
      _sessionsLoadTimer = setTimeout(() => {
        _sessionsLoadTimer = null;
        const nextRefresh = _sessionsLoadQueuedRefresh;
        _sessionsLoadQueuedRefresh = false;
        _doLoadSidebarSessions({ forceRefresh: nextRefresh });
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
      loadSidebarSessions(true, { forceRefresh: true });
      syncCurrentSession({ silent: true });
      showToast('已刷新会话列表');
    });

