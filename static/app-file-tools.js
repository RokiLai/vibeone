    // Git Diff Modal
    function stripGitDiffPrefix(rawPath) {
      let p = String(rawPath || '').trim();
      if (p.startsWith('"') && p.endsWith('"') && p.length >= 2) {
        p = p.slice(1, -1);
      }
      if (p.startsWith('a/') || p.startsWith('b/')) {
        p = p.slice(2);
      }
      return p;
    }

    function parseDiffHeaderPath(headerLine) {
      const mQuoted = headerLine.match(/^diff --git "a\/(.+?)" "b\/(.+?)"$/);
      if (mQuoted) return mQuoted[2] || mQuoted[1];
      const mPlain = headerLine.match(/^diff --git a\/(.+?) b\/(.+)$/);
      if (mPlain) return mPlain[2] || mPlain[1];
      return headerLine.replace(/^diff --git\s+/, '').trim() || '未知文件';
    }

    function parseDiffFiles(diffText) {
      if (!diffText || !diffText.trim()) return [];
      const rawLines = diffText.split('\n');
      const files = [];
      let current = null;

      for (const line of rawLines) {
        if (line.startsWith('diff --git ')) {
          if (current) files.push(current);
          current = {
            path: parseDiffHeaderPath(line),
            status: 'M',
            renameFrom: '',
            renameTo: '',
            added: 0,
            deleted: 0,
            isBinary: false,
            lines: [line],
          };
          continue;
        }
        if (!current) {
          if (!line.trim()) continue;
          current = {
            path: 'Diff',
            status: 'M',
            renameFrom: '',
            renameTo: '',
            added: 0,
            deleted: 0,
            isBinary: false,
            lines: [],
          };
        }
        current.lines.push(line);
        if (line.startsWith('new file mode ')) {
          current.status = 'A';
        } else if (line.startsWith('deleted file mode ')) {
          current.status = 'D';
        } else if (line.startsWith('rename from ')) {
          current.status = 'R';
          current.renameFrom = line.slice('rename from '.length).trim();
        } else if (line.startsWith('rename to ')) {
          current.status = 'R';
          current.renameTo = line.slice('rename to '.length).trim();
        } else if (line.startsWith('Binary files ') || line === 'GIT binary patch') {
          current.isBinary = true;
        } else if (line.startsWith('+++ ')) {
          const p = line.slice(4).trim();
          if (p !== '/dev/null') current.path = stripGitDiffPrefix(p);
        } else if (line.startsWith('--- ')) {
          const p = line.slice(4).trim();
          if (current.status === 'D' && p !== '/dev/null') {
            current.path = stripGitDiffPrefix(p);
          }
        } else if (line.startsWith('+') && !line.startsWith('+++')) {
          current.added += 1;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          current.deleted += 1;
        }
      }
      if (current) files.push(current);

      for (const f of files) {
        if (f.status === 'R' && f.renameFrom && f.renameTo) {
          f.path = `${f.renameFrom} → ${f.renameTo}`;
        }
        while (f.lines.length && f.lines[f.lines.length - 1] === '') {
          f.lines.pop();
        }
      }
      return files;
    }

    function formatDiffLinesHtml(lines) {
      let oldLine = null;
      let newLine = null;
      return lines
        .map((line) => {
          let cls = 'text-slate-300';
          let oldNum = '';
          let newNum = '';

          if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) {
            cls = 'diff-line-file';
            oldLine = null;
            newLine = null;
          } else if (line.startsWith('@@')) {
            cls = 'diff-line-hunk';
            const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
            if (m) {
              oldLine = parseInt(m[1], 10);
              newLine = parseInt(m[2], 10);
            }
            oldNum = '···';
            newNum = '···';
          } else if (oldLine !== null && newLine !== null) {
            if (line.startsWith('+')) {
              cls = 'diff-line-add';
              newNum = String(newLine++);
            } else if (line.startsWith('-')) {
              cls = 'diff-line-del';
              oldNum = String(oldLine++);
            } else if (line.startsWith('\\')) {
              cls = 'text-slate-500';
            } else {
              oldNum = String(oldLine++);
              newNum = String(newLine++);
            }
          } else if (line.startsWith('+')) {
            cls = 'diff-line-add';
          } else if (line.startsWith('-')) {
            cls = 'diff-line-del';
          }

          return `<div class="diff-row ${cls}"><span class="diff-ln">${oldNum}</span><span class="diff-ln">${newNum}</span><span class="diff-code">${escapeHtml(line || ' ')}</span></div>`;
        })
        .join('');
    }

    const DIFF_BADGE_STYLES = {
      A: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/35',
      D: 'bg-rose-500/20 text-rose-300 border-rose-500/35',
      R: 'bg-amber-500/20 text-amber-300 border-amber-500/35',
      M: 'bg-blue-500/20 text-blue-300 border-blue-500/35',
    };

    const DIFF_EXPAND_ALL_LIMIT = 30;
    const DIFF_LOAD_CONCURRENCY = 4;

    const diffModalState = {
      workdir: '',
      profile: '',
      sessionId: '',
      requestSeq: 0,
    };

    function formatDiffHtml(diffText) {
      if (!diffText || !diffText.trim()) {
        return '<div class="rounded-xl border border-border bg-bg text-slate-400 py-6 text-center">暂无未提交代码改动</div>';
      }
      const files = parseDiffFiles(diffText);
      if (!files.length) {
        return '<div class="rounded-xl border border-border bg-bg text-slate-400 py-6 text-center">暂无未提交代码改动</div>';
      }
      return `<div class="space-y-2">${files
        .map((file) => {
          const badgeCls = DIFF_BADGE_STYLES[file.status] || DIFF_BADGE_STYLES.M;
          const statsHtml = file.isBinary
            ? '<span class="text-[10px] text-slate-400 shrink-0">Binary</span>'
            : `<span class="flex items-center gap-1.5 text-[11px] shrink-0">${
                file.added > 0 ? `<span class="text-emerald-400">+${file.added}</span>` : ''
              }${
                file.deleted > 0 ? `<span class="text-rose-400">-${file.deleted}</span>` : ''
              }</span>`;
          return `
            <details class="diff-file-card">
              <summary title="${escapeHtml(file.path)}">
                <div class="flex items-center gap-2 min-w-0 flex-1">
                  <svg class="diff-chevron w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>
                  <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border ${badgeCls} shrink-0">${escapeHtml(file.status)}</span>
                  <span class="text-xs text-slate-100 font-medium truncate">${escapeHtml(file.path)}</span>
                </div>
                ${statsHtml}
              </summary>
              <div class="diff-file-body">${formatDiffLinesHtml(file.lines)}</div>
            </details>
          `;
        })
        .join('')}</div>`;
    }

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

    // =========================================================================
    // File Preview Modal (In-Page Code / Markdown / Image Preview)
    // =========================================================================
    const filePreviewState = {
      path: '',
      lineStart: null,
      lineEnd: null,
      data: null,
      mode: 'code', // 'code' | 'markdown'
      wrap: false,
      requestSeq: 0,
    };

    function formatFileSize(bytes) {
      const n = Number(bytes || 0);
      if (!Number.isFinite(n) || n < 0) return '';
      if (n < 1024) return `${n} B`;
      if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
      return `${(n / (1024 * 1024)).toFixed(2)} MB`;
    }

    function splitHighlightedHtmlLines(html) {
      const rawLines = String(html || '').split('\n');
      const openTags = [];
      return rawLines.map((line) => {
        const prefix = openTags.join('');
        const tagRe = /<\/?span(?:\s+[^>]*)?>/gi;
        let match;
        while ((match = tagRe.exec(line)) !== null) {
          const tag = match[0];
          if (tag.startsWith('</')) {
            if (openTags.length) openTags.pop();
          } else {
            openTags.push(tag);
          }
        }
        const suffix = openTags.length ? '</span>'.repeat(openTags.length) : '';
        return prefix + line + suffix;
      });
    }

    function closeFilePreview() {
      if (els.filePreviewModal) els.filePreviewModal.classList.add('hidden');
    }

    function renderFilePreviewBody() {
      const data = filePreviewState.data;
      if (!data) return;

      const isMarkdown = data.kind === 'text' && data.language === 'markdown';
      els.btnFilePreviewMdToggle.classList.toggle('hidden', !isMarkdown);
      if (isMarkdown) {
        els.btnFilePreviewMdToggle.textContent = filePreviewState.mode === 'markdown' ? '源码' : '渲染';
      }

      const showCodeControls = data.kind === 'text' && filePreviewState.mode === 'code';
      els.btnFilePreviewWrap.classList.toggle('hidden', !showCodeControls);
      els.btnFilePreviewWrap.classList.toggle('border-blue-500/60', showCodeControls && filePreviewState.wrap);
      els.btnFilePreviewWrap.classList.toggle('text-blue-300', showCodeControls && filePreviewState.wrap);
      els.btnFilePreviewCopy.classList.toggle('hidden', data.kind !== 'text');

      if (data.truncated) {
        els.filePreviewBanner.textContent = `文件较大（共 ${formatFileSize(data.size)}），仅预览前 ${formatFileSize(data.shown_bytes || 524288)} 内容。`;
        els.filePreviewBanner.classList.remove('hidden');
      } else {
        els.filePreviewBanner.classList.add('hidden');
      }

      if (data.kind === 'image') {
        const qs = new URLSearchParams({ path: data.path });
        if (state.currentWorkdir) qs.set('workdir', state.currentWorkdir);
        if (state.currentProfile) qs.set('profile', state.currentProfile);
        if (state.currentSessionId) qs.set('session_id', state.currentSessionId);
        const rawUrl = `/api/fs/file/raw?${qs.toString()}`;
        els.filePreviewContent.innerHTML = `
          <div class="h-full min-h-[260px] flex flex-col items-center justify-center p-6 gap-3">
            <img
              src="${escapeHtml(rawUrl)}"
              alt="${escapeHtml(data.name || '预览图片')}"
              class="max-w-full max-h-[65dvh] object-contain rounded-xl border border-border/70 bg-black/30 shadow-lg cursor-zoom-in"
              data-fp-image-zoom="1"
            />
            <div class="text-[11px] text-slate-400">点击图片可全屏缩放查看 · ${escapeHtml(formatFileSize(data.size))}</div>
          </div>
        `;
        const imgEl = els.filePreviewContent.querySelector('[data-fp-image-zoom]');
        if (imgEl) {
          imgEl.addEventListener('click', () => openImageModal([rawUrl], 0));
        }
        return;
      }

      if (data.kind === 'binary') {
        els.filePreviewContent.innerHTML = `
          <div class="py-16 px-6 text-center space-y-2">
            <div class="text-sm font-medium text-slate-200">二进制文件，暂不支持文本预览</div>
            <div class="text-xs text-slate-400 font-mono break-all">${escapeHtml(data.path || '')}</div>
            <div class="text-[11px] text-slate-500">${escapeHtml(data.mime_type || 'application/octet-stream')} · ${escapeHtml(formatFileSize(data.size))}</div>
          </div>
        `;
        return;
      }

      if (isMarkdown && filePreviewState.mode === 'markdown') {
        const wrap = document.createElement('div');
        wrap.className = 'p-4 sm:p-6 max-w-3xl mx-auto md-body selectable-text';
        renderMarkdownInto(wrap, data.content || '');
        els.filePreviewContent.innerHTML = '';
        els.filePreviewContent.appendChild(wrap);
        els.filePreviewContent.scrollTop = 0;
        return;
      }

      // Syntax-highlighted code view with line numbers
      const content = data.content || '';
      if (!content) {
        els.filePreviewContent.innerHTML = '<div class="py-14 text-center text-xs text-slate-500">空文件</div>';
        return;
      }

      let highlightedHtml = '';
      const lang = data.language && data.language !== 'plaintext' ? data.language : '';
      try {
        if (window.hljs && lang && window.hljs.getLanguage(lang)) {
          highlightedHtml = window.hljs.highlight(content, { language: lang, ignoreIllegals: true }).value;
        } else {
          highlightedHtml = escapeHtml(content);
        }
      } catch (_) {
        highlightedHtml = escapeHtml(content);
      }

      const lines = splitHighlightedHtmlLines(highlightedHtml);
      const hlStart = filePreviewState.lineStart;
      const hlEnd = filePreviewState.lineEnd || hlStart;
      const rowsHtml = new Array(lines.length);
      for (let i = 0; i < lines.length; i++) {
        const ln = i + 1;
        const isHl = hlStart && ln >= hlStart && ln <= hlEnd;
        rowsHtml[i] = `<div class="fp-row${isHl ? ' fp-row-hl' : ''}" data-fp-line="${ln}"><span class="fp-ln">${ln}</span><span class="fp-code">${lines[i] || ' '}</span></div>`;
      }

      els.filePreviewContent.innerHTML = `<div class="fp-code-wrap${filePreviewState.wrap ? ' fp-wrap-lines' : ''}">${rowsHtml.join('')}</div>`;

      if (hlStart) {
        requestAnimationFrame(() => {
          const targetRow = els.filePreviewContent.querySelector(`[data-fp-line="${hlStart}"]`);
          if (targetRow) {
            targetRow.scrollIntoView({ block: 'center', behavior: 'auto' });
          }
        });
      } else {
        els.filePreviewContent.scrollTop = 0;
      }
    }

    async function loadFilePreview(path, opts = {}) {
      const targetPath = String(path || '').trim();
      if (!targetPath) return;

      const lineStart = opts.lineStart ? Number(opts.lineStart) : null;
      const lineEnd = opts.lineEnd ? Number(opts.lineEnd) : lineStart;
      const seq = ++filePreviewState.requestSeq;

      filePreviewState.path = targetPath;
      filePreviewState.lineStart = Number.isFinite(lineStart) && lineStart > 0 ? lineStart : null;
      filePreviewState.lineEnd = Number.isFinite(lineEnd) && lineEnd >= (filePreviewState.lineStart || 1)
        ? lineEnd
        : filePreviewState.lineStart;
      filePreviewState.data = null;

      els.filePreviewName.textContent = basenamePath(targetPath) || targetPath;
      els.filePreviewPath.textContent = targetPath;
      els.filePreviewMeta.textContent = '';
      els.filePreviewLang.classList.add('hidden');
      if (filePreviewState.lineStart) {
        els.filePreviewRange.textContent =
          filePreviewState.lineEnd && filePreviewState.lineEnd !== filePreviewState.lineStart
            ? `L${filePreviewState.lineStart}-L${filePreviewState.lineEnd}`
            : `L${filePreviewState.lineStart}`;
        els.filePreviewRange.classList.remove('hidden');
      } else {
        els.filePreviewRange.classList.add('hidden');
      }
      els.filePreviewBanner.classList.add('hidden');
      els.btnFilePreviewMdToggle.classList.add('hidden');
      els.filePreviewContent.innerHTML = '<div class="text-slate-400 py-12 text-center text-xs">正在加载文件内容...</div>';
      els.filePreviewModal.classList.remove('hidden');

      try {
        const qs = new URLSearchParams({ path: targetPath });
        const effectiveWorkdir = opts.workdir || state.currentWorkdir;
        if (effectiveWorkdir) qs.set('workdir', effectiveWorkdir);
        if (state.currentProfile) qs.set('profile', state.currentProfile);
        if (state.currentSessionId) qs.set('session_id', state.currentSessionId);
        const res = await fetch(`/api/fs/file?${qs.toString()}`, { credentials: 'same-origin' });
        if (seq !== filePreviewState.requestSeq) return;
        if (res.status === 401) {
          closeFilePreview();
          showAuthOverlay();
          return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = data.detail || `无法读取文件 (HTTP ${res.status})`;
          els.filePreviewContent.innerHTML = `
            <div class="py-14 px-6 text-center space-y-2">
              <div class="text-sm font-medium text-rose-300">无法预览文件</div>
              <div class="text-xs text-slate-400 break-all">${escapeHtml(detail)}</div>
            </div>
          `;
          return;
        }

        filePreviewState.data = data;
        // Default markdown files to rendered view unless user clicked a specific line range
        if (data.kind === 'text' && data.language === 'markdown' && !filePreviewState.lineStart) {
          filePreviewState.mode = 'markdown';
        } else {
          filePreviewState.mode = 'code';
        }

        els.filePreviewName.textContent = data.name || basenamePath(data.path) || targetPath;
        els.filePreviewPath.textContent = data.display_path || data.path || targetPath;
        const metaParts = [];
        if (data.kind === 'text' && data.line_count) metaParts.push(`${data.line_count} 行`);
        if (data.size != null) metaParts.push(formatFileSize(data.size));
        els.filePreviewMeta.textContent = metaParts.join(' · ');

        if (data.language && data.language !== 'plaintext') {
          els.filePreviewLang.textContent = data.language;
          els.filePreviewLang.classList.remove('hidden');
        } else if (data.ext) {
          els.filePreviewLang.textContent = data.ext.replace(/^\./, '');
          els.filePreviewLang.classList.remove('hidden');
        } else {
          els.filePreviewLang.classList.add('hidden');
        }

        renderFilePreviewBody();
      } catch (err) {
        if (seq !== filePreviewState.requestSeq) return;
        els.filePreviewContent.innerHTML = `
          <div class="py-14 px-6 text-center space-y-2">
            <div class="text-sm font-medium text-rose-300">加载文件失败</div>
            <div class="text-xs text-slate-400">${escapeHtml((err && err.message) || '网络异常')}</div>
          </div>
        `;
      }
    }

    document.addEventListener('click', (e) => {
      const link = e.target && e.target.closest ? e.target.closest('a.file-preview-link[data-file-preview-path]') : null;
      if (!link) return;
      e.preventDefault();
      e.stopPropagation();
      const targetPath = link.dataset.filePreviewPath || '';
      const lineStart = link.dataset.fileLineStart ? Number(link.dataset.fileLineStart) : null;
      const lineEnd = link.dataset.fileLineEnd ? Number(link.dataset.fileLineEnd) : null;
      if (targetPath) {
        let baseWorkdir = null;
        if (
          els.filePreviewContent &&
          els.filePreviewContent.contains(link) &&
          filePreviewState.data &&
          filePreviewState.data.path &&
          !targetPath.startsWith('/') &&
          !targetPath.startsWith('~')
        ) {
          const curPath = String(filePreviewState.data.path);
          const slashIdx = curPath.lastIndexOf('/');
          if (slashIdx > 0) baseWorkdir = curPath.slice(0, slashIdx);
        }
        loadFilePreview(targetPath, { lineStart, lineEnd, workdir: baseWorkdir });
      }
    });

    els.btnFilePreviewClose.addEventListener('click', closeFilePreview);
    els.filePreviewModal.addEventListener('click', (e) => {
      if (e.target === els.filePreviewModal) closeFilePreview();
    });
    els.btnFilePreviewRefresh.addEventListener('click', () => {
      if (filePreviewState.path) {
        loadFilePreview(filePreviewState.path, {
          lineStart: filePreviewState.lineStart,
          lineEnd: filePreviewState.lineEnd,
        });
      }
    });
    els.btnFilePreviewMdToggle.addEventListener('click', () => {
      filePreviewState.mode = filePreviewState.mode === 'markdown' ? 'code' : 'markdown';
      renderFilePreviewBody();
    });
    els.btnFilePreviewWrap.addEventListener('click', () => {
      filePreviewState.wrap = !filePreviewState.wrap;
      renderFilePreviewBody();
    });
    els.btnFilePreviewCopy.addEventListener('click', () => {
      if (filePreviewState.data && filePreviewState.data.kind === 'text') {
        copyText(filePreviewState.data.content || '', els.btnFilePreviewCopy);
      }
    });
    els.filePreviewPath.addEventListener('click', () => {
      const full = (filePreviewState.data && filePreviewState.data.path) || filePreviewState.path;
      if (full) {
        copyText(full);
        showToast('已复制文件路径');
      }
    });

