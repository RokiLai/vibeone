    function renderDiffSectionHtml(title, titleCls, diffText) {
      const files = parseDiffFiles(diffText);
      const countText = files.length ? ` · ${files.length} 个文件` : '';
      const toggleBtnHtml = files.length > 0
        ? '<button type="button" data-diff-toggle-all="1" class="px-2 py-0.5 rounded-md border border-border bg-card text-[11px] text-slate-300 hover:bg-slate-800 hover:text-white transition">全部展开</button>'
        : '';
      return `
        <div class="diff-section mb-3">
          <div class="flex items-center justify-between gap-2 mb-1.5">
            <div class="text-[11px] ${titleCls} font-semibold">${escapeHtml(title)}${escapeHtml(countText)}</div>
            ${toggleBtnHtml}
          </div>
          ${formatDiffHtml(diffText)}
        </div>
      `;
    }

    function renderDiffLazyFileCardHtml(file) {
      const status = file.status || 'M';
      const badgeCls = DIFF_BADGE_STYLES[status] || DIFF_BADGE_STYLES.M;
      const displayPath = file.display_path || file.path || '未知文件';
      let statsHtml = '';
      if (file.is_binary) {
        statsHtml = '<span class="text-[10px] text-slate-400 shrink-0">Binary</span>';
      } else if (file.scope === 'untracked' && !file.added && !file.deleted) {
        const sizeLabel = Number.isFinite(Number(file.size)) ? formatFileSize(file.size) : '';
        statsHtml = sizeLabel
          ? `<span class="text-[10px] text-slate-400 shrink-0">${escapeHtml(sizeLabel)}</span>`
          : '';
      } else {
        statsHtml = `<span class="flex items-center gap-1.5 text-[11px] shrink-0">${
          file.added > 0 ? `<span class="text-emerald-400">+${file.added}</span>` : ''
        }${
          file.deleted > 0 ? `<span class="text-rose-400">-${file.deleted}</span>` : ''
        }</span>`;
      }
      return `
        <details
          class="diff-file-card"
          data-diff-lazy="1"
          data-diff-state="idle"
          data-diff-path="${escapeHtml(file.path || '')}"
          data-diff-scope="${escapeHtml(file.scope || 'working')}"
          data-diff-rename-from="${escapeHtml(file.rename_from || '')}"
        >
          <summary title="${escapeHtml(displayPath)}">
            <div class="flex items-center gap-2 min-w-0 flex-1">
              <svg class="diff-chevron w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>
              <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border ${badgeCls} shrink-0">${escapeHtml(status)}</span>
              <span class="text-xs text-slate-100 font-medium truncate">${escapeHtml(displayPath)}</span>
            </div>
            ${statsHtml}
          </summary>
          <div class="diff-file-body">
            <div class="px-3 py-3 text-slate-400">点击展开加载代码改动...</div>
          </div>
        </details>
      `;
    }

    function renderDiffLazySectionHtml(title, titleCls, files) {
      const list = Array.isArray(files) ? files : [];
      const countText = list.length ? ` · ${list.length} 个文件` : '';
      const toggleBtnHtml = list.length > 0
        ? '<button type="button" data-diff-toggle-all="1" class="px-2 py-0.5 rounded-md border border-border bg-card text-[11px] text-slate-300 hover:bg-slate-800 hover:text-white transition">全部展开</button>'
        : '';
      const bodyHtml = list.length
        ? `<div class="space-y-2">${list.map(renderDiffLazyFileCardHtml).join('')}</div>`
        : '<div class="rounded-xl border border-border bg-bg text-slate-400 py-6 text-center">暂无未提交代码改动</div>';
      return `
        <div class="diff-section mb-3">
          <div class="flex items-center justify-between gap-2 mb-1.5">
            <div class="text-[11px] ${titleCls} font-semibold">${escapeHtml(title)}${escapeHtml(countText)}</div>
            ${toggleBtnHtml}
          </div>
          ${bodyHtml}
        </div>
      `;
    }

    async function readFetchErrorDetail(res) {
      try {
        const text = await res.text();
        if (!text) return `HTTP ${res.status}`;
        try {
          const parsed = JSON.parse(text);
          if (parsed && parsed.detail) return `HTTP ${res.status}: ${parsed.detail}`;
        } catch (_) {}
        return `HTTP ${res.status}: ${text.slice(0, 160)}`;
      } catch (_) {
        return `HTTP ${res.status}`;
      }
    }

    async function loadSingleDiffFile(card) {
      if (!card || card.dataset.diffLazy !== '1') return;
      if (card.dataset.diffState === 'loading' || card.dataset.diffState === 'loaded') return;
      const body = card.querySelector('.diff-file-body');
      if (!body) return;

      const reqSeq = diffModalState.requestSeq;
      card.dataset.diffState = 'loading';
      body.innerHTML = '<div class="px-3 py-3 text-slate-400">正在加载文件改动...</div>';

      try {
        const qs = new URLSearchParams();
        qs.set('path', card.dataset.diffPath || '');
        qs.set('scope', card.dataset.diffScope || 'working');
        if (card.dataset.diffRenameFrom) qs.set('rename_from', card.dataset.diffRenameFrom);
        if (diffModalState.workdir) qs.set('workdir', diffModalState.workdir);
        if (diffModalState.profile) qs.set('profile', diffModalState.profile);
        if (diffModalState.sessionId) qs.set('session_id', diffModalState.sessionId);

        const res = await fetch(`/api/diff/file?${qs.toString()}`, { credentials: 'same-origin' });
        if (res.status === 401) {
          els.diffModal.classList.add('hidden');
          showAuthOverlay();
          return;
        }
        if (!res.ok) {
          throw new Error(await readFetchErrorDetail(res));
        }
        const data = await res.json();
        if (reqSeq !== diffModalState.requestSeq) return;

        const diffText = String(data.diff || '');
        if (!diffText.trim()) {
          body.innerHTML = '<div class="px-3 py-3 text-slate-400">无文本改动</div>';
        } else {
          const lines = diffText.split('\n');
          while (lines.length && lines[lines.length - 1] === '') lines.pop();
          const noticeHtml = data.truncated
            ? '<div class="px-3 py-2 text-[11px] text-amber-300 bg-amber-500/10 border-t border-amber-500/25">内容过大，已截断显示</div>'
            : '';
          body.innerHTML = formatDiffLinesHtml(lines) + noticeHtml;
        }
        card.dataset.diffState = 'loaded';
      } catch (err) {
        if (reqSeq !== diffModalState.requestSeq) return;
        card.dataset.diffState = 'error';
        const reason = err && err.message ? err.message : '';
        body.innerHTML = `
          <div class="px-3 py-3 flex items-center justify-between gap-2 text-rose-400">
            <span class="truncate">加载文件改动失败${reason ? ` (${escapeHtml(reason)})` : ''}</span>
            <button type="button" data-diff-retry="1" class="px-2 py-0.5 rounded border border-rose-500/40 bg-rose-500/10 text-[11px] text-rose-200 hover:bg-rose-500/20 shrink-0">重试</button>
          </div>
        `;
      }
    }

    async function loadDiffCardsWithConcurrency(cards, concurrency) {
      let idx = 0;
      const reqSeq = diffModalState.requestSeq;
      async function worker() {
        while (idx < cards.length) {
          if (reqSeq !== diffModalState.requestSeq) return;
          const card = cards[idx++];
          if (card && card.open) {
            await loadSingleDiffFile(card);
          }
        }
      }
      const workers = [];
      const count = Math.min(concurrency, cards.length);
      for (let i = 0; i < count; i++) workers.push(worker());
      await Promise.all(workers);
    }

    function syncDiffSectionToggleLabel(sectionEl) {
      if (!sectionEl) return;
      const btn = sectionEl.querySelector('[data-diff-toggle-all]');
      if (!btn) return;
      const cards = Array.from(sectionEl.querySelectorAll('details.diff-file-card'));
      if (!cards.length) return;
      const anyOpen = cards.some((d) => d.open);
      btn.textContent = anyOpen ? '全部折叠' : '全部展开';
    }

    async function loadDiff() {
      const reqSeq = ++diffModalState.requestSeq;
      diffModalState.workdir = state.currentWorkdir || '';
      diffModalState.profile = state.currentProfile || '';
      diffModalState.sessionId = state.currentSessionId || '';
      els.diffContent.innerHTML = '<div class="text-slate-400 py-8 text-center">正在加载 Git Diff...</div>';
      try {
        const qs = new URLSearchParams();
        if (diffModalState.workdir) qs.set('workdir', diffModalState.workdir);
        if (diffModalState.profile) qs.set('profile', diffModalState.profile);
        if (diffModalState.sessionId) qs.set('session_id', diffModalState.sessionId);
        const url = qs.toString() ? `/api/diff?${qs.toString()}` : '/api/diff';
        const res = await fetch(url, { credentials: 'same-origin' });
        if (res.status === 401) {
          els.diffModal.classList.add('hidden');
          showAuthOverlay();
          return;
        }
        if (!res.ok) {
          throw new Error(await readFetchErrorDetail(res));
        }
        const data = await res.json();
        if (reqSeq !== diffModalState.requestSeq) return;

        if (data.workdir) diffModalState.workdir = data.workdir;
        els.diffBranch.textContent = data.branch || (data.is_git_repo ? 'HEAD' : '非 Git 仓库');
        els.diffWorkdir.textContent = data.workdir || '';

        if (!data.is_git_repo) {
          els.diffContent.innerHTML = `<div class="text-slate-400 py-8 text-center">${escapeHtml(data.message || '不是 Git 仓库')}</div>`;
          return;
        }

        const blocks = [];
        if (data.status && data.status.trim()) {
          const statusLines = data.status.trim().split('\n');
          const statusPreview = statusLines.length > 80
            ? `${statusLines.slice(0, 80).join('\n')}\n... (共 ${statusLines.length} 条)`
            : data.status.trim();
          blocks.push(
            `<details class="mb-3 rounded-xl border border-border bg-bg p-2.5"><summary class="cursor-pointer text-[11px] text-slate-400 select-none">git status --short · ${statusLines.length} 项</summary><pre class="mt-1.5 whitespace-pre overflow-x-auto text-slate-200">${escapeHtml(statusPreview)}</pre></details>`
          );
        }

        if (data.lazy && Array.isArray(data.working_files)) {
          if (Array.isArray(data.staged_files) && data.staged_files.length) {
            blocks.push(
              renderDiffLazySectionHtml('Staged Changes (git diff --cached)', 'text-emerald-400', data.staged_files)
            );
          }
          blocks.push(
            renderDiffLazySectionHtml('Working Tree Changes (git diff)', 'text-blue-400', data.working_files)
          );
        } else {
          if (data.staged_diff && data.staged_diff.trim()) {
            blocks.push(
              renderDiffSectionHtml('Staged Changes (git diff --cached)', 'text-emerald-400', data.staged_diff)
            );
          }
          blocks.push(
            renderDiffSectionHtml('Working Tree Changes (git diff)', 'text-blue-400', data.diff)
          );
        }
        els.diffContent.innerHTML = blocks.join('');
      } catch (err) {
        if (reqSeq !== diffModalState.requestSeq) return;
        const reason = err && err.message ? err.message : '';
        els.diffContent.innerHTML = `<div class="text-rose-400 py-8 text-center"><div>获取 Git Diff 失败</div>${
          reason ? `<div class="mt-1.5 text-[11px] text-rose-300/80 break-all px-4">${escapeHtml(reason)}</div>` : ''
        }</div>`;
      }
    }

    els.diffContent.addEventListener('click', (e) => {
      const retryBtn = e.target.closest('[data-diff-retry]');
      if (retryBtn) {
        const card = retryBtn.closest('details.diff-file-card');
        if (card) loadSingleDiffFile(card);
        return;
      }
      const btn = e.target.closest('[data-diff-toggle-all]');
      if (!btn) return;
      const section = btn.closest('.diff-section');
      if (!section) return;
      const cards = Array.from(section.querySelectorAll('details.diff-file-card'));
      if (!cards.length) return;
      const shouldOpen = !cards.some((d) => d.open);
      if (!shouldOpen) {
        cards.forEach((d) => {
          d.open = false;
        });
        syncDiffSectionToggleLabel(section);
        return;
      }
      const toExpand = cards.slice(0, DIFF_EXPAND_ALL_LIMIT);
      toExpand.forEach((d) => {
        d.open = true;
      });
      if (cards.length > DIFF_EXPAND_ALL_LIMIT && typeof showToast === 'function') {
        showToast(`仅展开前 ${DIFF_EXPAND_ALL_LIMIT} 个文件，其余请按需点开`);
      }
      syncDiffSectionToggleLabel(section);
      const lazyCards = toExpand.filter(
        (d) => d.dataset.diffLazy === '1' && d.dataset.diffState !== 'loaded'
      );
      if (lazyCards.length) {
        loadDiffCardsWithConcurrency(lazyCards, DIFF_LOAD_CONCURRENCY);
      }
    });
    els.diffContent.addEventListener(
      'toggle',
      (e) => {
        const card = e.target;
        if (card && card.matches && card.matches('details.diff-file-card')) {
          if (
            card.open &&
            card.dataset.diffLazy === '1' &&
            (card.dataset.diffState === 'idle' || card.dataset.diffState === 'error')
          ) {
            loadSingleDiffFile(card);
          }
          syncDiffSectionToggleLabel(card.closest('.diff-section'));
        }
      },
      true
    );

    $('btn-diff').addEventListener('click', () => {
      els.diffModal.classList.remove('hidden');
      loadDiff();
    });
    $('btn-diff-refresh').addEventListener('click', loadDiff);
    $('btn-diff-close').addEventListener('click', () => els.diffModal.classList.add('hidden'));
    els.diffModal.addEventListener('click', (e) => {
      if (e.target === els.diffModal) els.diffModal.classList.add('hidden');
    });

