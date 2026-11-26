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

    // Mobile sidebar search keyboard ergonomics: collapse non-essential chrome
    // while typing so the session list gets maximum height, and dismiss keyboard on scroll.
    if (els.sidebarSearch && els.sidebar) {
      els.sidebarSearch.addEventListener('focus', () => {
        els.sidebar.classList.add('sidebar-search-focused');
      });
      els.sidebarSearch.addEventListener('blur', () => {
        els.sidebar.classList.remove('sidebar-search-focused');
      });
      els.sidebarSearch.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.isComposing) {
          els.sidebarSearch.blur();
        }
      });
    }

    if (els.sidebarSessions && els.sidebarSearch) {
      let _searchScrollTouchY = null;
      els.sidebarSessions.addEventListener('touchstart', (e) => {
        if (document.activeElement === els.sidebarSearch && e.touches.length === 1) {
          _searchScrollTouchY = e.touches[0].clientY;
        } else {
          _searchScrollTouchY = null;
        }
      }, { passive: true });
      els.sidebarSessions.addEventListener('touchmove', (e) => {
        if (_searchScrollTouchY === null || e.touches.length !== 1) return;
        if (Math.abs(e.touches[0].clientY - _searchScrollTouchY) > 8) {
          _searchScrollTouchY = null;
          if (document.activeElement === els.sidebarSearch) {
            els.sidebarSearch.blur();
          }
        }
      }, { passive: true });
      els.sidebarSessions.addEventListener('touchend', () => {
        _searchScrollTouchY = null;
      }, { passive: true });
      els.sidebarSessions.addEventListener('touchcancel', () => {
        _searchScrollTouchY = null;
      }, { passive: true });
    }

