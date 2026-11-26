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
      // Pinned "Running / Pending Permission" Quick-Jump Section at the top of sidebar
      if (!q) {
        renderSidebarRunningSection(groups, dirNameCounts, getSessionActivity, switchToSidebarSession);
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
          renderSidebarBatchToolbar(grp, section);
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

