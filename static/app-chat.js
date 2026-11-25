    // =========================================================================
    // Markdown Renderer with Highlight.js & Code Copy Buttons
    // =========================================================================
    const SAFE_MARKDOWN_URI_RE =
      /^(?:(?:(?:f|ht)tps?|file|mailto|tel|callto|sms|cid|xmpp):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i;

    function parseLineRangeFragment(frag) {
      if (!frag) return { lineStart: null, lineEnd: null };
      const cleaned = String(frag).replace(/^#/, '').trim();
      const m = cleaned.match(/^L?(\d+)(?:C\d+)?(?:-L?(\d+)(?:C\d+)?)?$/i);
      if (!m) return { lineStart: null, lineEnd: null };
      const start = parseInt(m[1], 10);
      const end = m[2] ? parseInt(m[2], 10) : start;
      if (!Number.isFinite(start) || start < 1) return { lineStart: null, lineEnd: null };
      return {
        lineStart: start,
        lineEnd: Number.isFinite(end) && end >= start ? end : start,
      };
    }

    function parseLocalFileHref(rawHref) {
      const href = String(rawHref || '').trim();
      if (!href || href.startsWith('#') || href.startsWith('//')) return null;

      // Ignore external web URLs and non-file URI schemes
      if (/^(https?|mailto|tel|sms|data|javascript|vbscript|blob):/i.test(href)) {
        return null;
      }

      let isFileUri = false;
      let rest = href;
      if (/^file:\/\//i.test(href)) {
        isFileUri = true;
        rest = href.replace(/^file:\/\/localhost(?=\/)/i, '').replace(/^file:\/\//i, '');
      } else if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
        // Other custom schemes like conversation://, vscode://, etc.
        return null;
      }

      // Split hash (#L10-L20) and query (?...)
      let hash = '';
      const hashIdx = rest.indexOf('#');
      if (hashIdx >= 0) {
        hash = rest.slice(hashIdx + 1);
        rest = rest.slice(0, hashIdx);
      }
      const queryIdx = rest.indexOf('?');
      if (queryIdx >= 0) {
        rest = rest.slice(0, queryIdx);
      }

      let decodedPath = rest;
      try {
        decodedPath = decodeURIComponent(rest);
      } catch (_) {}

      let { lineStart, lineEnd } = parseLineRangeFragment(hash);

      // Also support trailing :line, :line:col, :line-end suffixes on path
      if (!lineStart) {
        const colonRange = decodedPath.match(/^(.*):(\d+)(?::\d+)?(?:-(\d+))?$/);
        if (colonRange && colonRange[1] && !colonRange[1].endsWith('/')) {
          const s = parseInt(colonRange[2], 10);
          const e = colonRange[3] ? parseInt(colonRange[3], 10) : s;
          if (Number.isFinite(s) && s >= 1) {
            decodedPath = colonRange[1];
            lineStart = s;
            lineEnd = Number.isFinite(e) && e >= s ? e : s;
          }
        }
      }

      decodedPath = decodedPath.trim();
      if (!decodedPath) return null;

      // Do not hijack VibeOne's own HTTP routes when written as root-relative paths
      if (
        !isFileUri &&
        /^\/(?:api|uploads|static|vendor|ws|sw\.js|manifest\.json|favicon|icon|apple-touch-icon)(?:\/|$)/i.test(decodedPath)
      ) {
        return null;
      }

      return { path: decodedPath, lineStart, lineEnd };
    }

    function decorateMarkdownLinks(container) {
      container.querySelectorAll('a[href]').forEach((a) => {
        const rawHref = (a.getAttribute('href') || '').trim();
        if (!rawHref) return;
        if (/^https?:\/\//i.test(rawHref)) {
          a.setAttribute('target', '_blank');
          a.setAttribute('rel', 'noopener noreferrer');
          return;
        }
        const parsed = parseLocalFileHref(rawHref);
        if (!parsed) return;
        a.dataset.filePreviewPath = parsed.path;
        if (parsed.lineStart) a.dataset.fileLineStart = String(parsed.lineStart);
        if (parsed.lineEnd) a.dataset.fileLineEnd = String(parsed.lineEnd);
        a.classList.add('file-preview-link');
        a.setAttribute('role', 'button');
        a.setAttribute('href', '#file-preview');
        const rangeLabel = parsed.lineStart
          ? (parsed.lineEnd && parsed.lineEnd !== parsed.lineStart
              ? ` (L${parsed.lineStart}-L${parsed.lineEnd})`
              : ` (L${parsed.lineStart})`)
          : '';
        a.setAttribute('title', `预览文件: ${parsed.path}${rangeLabel}`);
      });
    }

    function renderMarkdownInto(container, markdownText) {
      const rawHtml = marked.parse(markdownText || '');
      const safeHtml = window.DOMPurify
        ? window.DOMPurify.sanitize(rawHtml, {
            USE_PROFILES: { html: true },
            ALLOWED_URI_REGEXP: SAFE_MARKDOWN_URI_RE,
            FORBID_TAGS: ['style', 'form', 'input', 'textarea', 'select', 'button', 'iframe', 'object', 'embed'],
          })
        : escapeHtml(markdownText || '');
      container.innerHTML = safeHtml;
      decorateMarkdownLinks(container);

      container.querySelectorAll('pre').forEach((pre) => {
        if (pre.parentElement && pre.parentElement.classList.contains('code-wrap')) return;
        const codeEl = pre.querySelector('code');
        let lang = '';
        if (codeEl) {
          const m = (codeEl.className || '').match(/language-([\w-]+)/);
          if (m) lang = m[1];
          try { hljs.highlightElement(codeEl); } catch (_) {}
        }

        const rawCode = codeEl ? codeEl.textContent : pre.textContent;
        const wrap = document.createElement('div');
        wrap.className = 'code-wrap';

        const header = document.createElement('div');
        header.className = 'code-header';
        header.innerHTML = `<span class="uppercase tracking-wider font-mono">${escapeHtml(lang || 'code')}</span>`;

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] active:scale-95';
        copyBtn.textContent = '复制';
        copyBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          copyText(rawCode, copyBtn);
        });
        header.appendChild(copyBtn);

        pre.parentNode.insertBefore(wrap, pre);
        wrap.appendChild(header);
        wrap.appendChild(pre);
      });
    }

    // =========================================================================
    // Scrolling Helper (column-reverse: scrollTop=0 is BOTTOM, negative goes UP)
    // =========================================================================
    function getDistanceFromBottom() {
      return Math.abs(els.chatScroll.scrollTop);
    }

    function getDistanceFromTop() {
      const el = els.chatScroll;
      const scrollMax = Math.max(0, el.scrollHeight - el.clientHeight);
      return Math.max(0, scrollMax - Math.abs(el.scrollTop));
    }

    function isNearBottom() {
      return getDistanceFromBottom() < 90;
    }

    function scrollToBottom(force = false) {
      if (force || state.autoScroll) {
        els.chatScroll.scrollTop = 0;
        state.lastScrollTop = 0;
      }
    }

    function withScrollAnchor(anchorEl, mutateFn) {
      if (!anchorEl || !anchorEl.isConnected) {
        mutateFn();
        return;
      }
      const beforeTop = anchorEl.getBoundingClientRect().top;
      mutateFn();
      const afterTop = anchorEl.getBoundingClientRect().top;
      const delta = afterTop - beforeTop;
      if (Math.abs(delta) > 0.5) {
        state.suppressAutoLoadUntil = Math.max(state.suppressAutoLoadUntil || 0, Date.now() + 300);
        els.chatScroll.scrollTop += delta;
        state.lastScrollTop = getDistanceFromBottom();
        const near = state.lastScrollTop < 90;
        state.autoScroll = near;
        els.btnScrollBottom.classList.toggle('hidden', near);
      }
    }

    function findVisibleChatAnchor() {
      const topEdge = els.chatScroll.getBoundingClientRect().top;
      for (const child of els.chatList.children) {
        const r = child.getBoundingClientRect();
        if (r.bottom > topEdge + 4) return child;
      }
      return null;
    }

    els.chatScroll.addEventListener('scroll', () => {
      const distBottom = getDistanceFromBottom();
      const scrollingUp = distBottom > state.lastScrollTop + 2;
      state.lastScrollTop = distBottom;
      const near = distBottom < 90;
      state.autoScroll = near;
      els.btnScrollBottom.classList.toggle('hidden', near);
      const now = Date.now();
      const distTop = getDistanceFromTop();
      const prefetchThreshold = Math.max(700, Math.round(els.chatScroll.clientHeight * 1.1));
      if (
        scrollingUp &&
        distTop <= prefetchThreshold &&
        now >= state.suppressAutoLoadUntil &&
        now - state.lastOlderLoadAt >= 260 &&
        state.hasMoreItems &&
        !state.loadingOlderItems &&
        state.items.length > 0
      ) {
        loadOlderItems();
      }
    });

    els.btnScrollBottom.addEventListener('click', () => {
      state.autoScroll = true;
      scrollToBottom(true);
      els.btnScrollBottom.classList.add('hidden');
    });

    // =========================================================================
    // Item DOM Rendering
    // =========================================================================
    function statusBadgeHtml(status) {
      if (status === 'completed') return '<span class="w-2 h-2 rounded-full bg-emerald-400 shrink-0"></span>';
      if (status === 'failed' || status === 'error') return '<span class="w-2 h-2 rounded-full bg-rose-400 shrink-0"></span>';
      if (status === 'pending') return '<span class="w-2 h-2 rounded-full bg-amber-400 animate-soft-pulse shrink-0"></span>';
      return '<span class="w-2 h-2 rounded-full bg-blue-400 animate-soft-pulse shrink-0"></span>';
    }

    function toolKindLabel(item) {
      const map = { read: '读文件', edit: '编辑', execute: '执行命令', search: '搜索', think: '思考' };
      return map[item.kind] || item.name || '工具';
    }

    function getOrCreateProcessGroup(isLive = false, container = els.chatList) {
      const target = container || els.chatList;
      const last = target.lastElementChild;
      if (last && last.classList && last.classList.contains('process-group')) {
        if (isLive && last.dataset.live !== '1') {
          last.dataset.live = '1';
          if (last.dataset.userToggled !== '1') last.open = true;
        }
        return last;
      }
      const group = document.createElement('details');
      group.className = 'process-group';
      group.dataset.live = isLive ? '1' : '0';
      group.open = Boolean(isLive);
      group.innerHTML = `
        <summary>
          <svg class="pg-chevron w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
          <span class="pg-icon shrink-0 flex items-center"></span>
          <span class="pg-title font-medium text-slate-300 shrink-0"></span>
          <span class="pg-preview text-[11px] text-slate-500 truncate flex-1"></span>
        </summary>
        <div class="process-group-body"></div>`;
      target.appendChild(group);
      return group;
    }

    function updateProcessGroupSummary(group) {
      if (!group) return;
      const body = group.querySelector('.process-group-body');
      if (!body) return;
      const children = Array.from(body.children);
      let thoughtCount = 0;
      let toolCount = 0;
      let hasError = false;
      let latestPreview = '';

      children.forEach((el) => {
        const t = el.dataset.itemType;
        const item = state.itemsById.get(el.dataset.itemId);
        if (t === 'thought') {
          thoughtCount += 1;
          if (item?.text) latestPreview = item.text.replace(/\s+/g, ' ').slice(0, 60);
        } else if (t === 'tool') {
          toolCount += 1;
          if (item?.status === 'failed' || item?.status === 'error') hasError = true;
          if (item) latestPreview = item.title || item.name || toolKindLabel(item);
        }
      });

      const isLive = group.dataset.live === '1';
      const iconEl = group.querySelector('.pg-icon');
      const titleEl = group.querySelector('.pg-title');
      const previewEl = group.querySelector('.pg-preview');

      if (isLive) {
        iconEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-blue-400 animate-soft-pulse"></span>';
      } else if (hasError) {
        iconEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span>';
      } else {
        iconEl.innerHTML = '<svg class="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2.4" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/></svg>';
      }

      const parts = [];
      if (thoughtCount > 0) parts.push(`${thoughtCount} 步思考`);
      if (toolCount > 0) parts.push(`${toolCount} 次工具`);
      const statsStr = parts.join(' · ') || '处理中';

      if (isLive) {
        titleEl.textContent = `正在执行 · ${statsStr}`;
        titleEl.className = 'pg-title font-medium text-blue-300 shrink-0';
        previewEl.textContent = latestPreview ? `· ${latestPreview}` : '';
      } else {
        const label =
          thoughtCount > 0 && toolCount === 0
            ? `思考过程 · ${thoughtCount} 步`
            : toolCount > 0 && thoughtCount === 0
            ? `工具执行 · ${toolCount} 次`
            : `执行过程 · ${statsStr}`;
        titleEl.textContent = label;
        titleEl.className = 'pg-title font-medium text-slate-300 shrink-0';
        previewEl.textContent = latestPreview ? `· ${latestPreview}` : '';
      }
    }

    function collapseCompletedProcessGroups() {
      const anchor = !isNearBottom() ? findVisibleChatAnchor() : null;
      withScrollAnchor(anchor, () => {
        els.chatList.querySelectorAll('details.process-group').forEach((grp) => {
          const wasLive = grp.dataset.live === '1';
          grp.dataset.live = '0';
          if (wasLive && grp.dataset.userToggled !== '1') {
            grp.open = false;
          }
          updateProcessGroupSummary(grp);
        });
      });
    }

    function formatClockTime(tsSec) {
      if (!tsSec) return '';
      const d = new Date(Number(tsSec) * 1000);
      if (Number.isNaN(d.getTime())) return '';
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      return `${hh}:${mm}`;
    }

    function findPrecedingUserItem(node) {
      let cur = node ? node.previousElementSibling : null;
      while (cur) {
        if (cur.dataset && cur.dataset.itemType === 'user') {
          return state.itemsById.get(cur.dataset.itemId) || null;
        }
        cur = cur.previousElementSibling;
      }
      return null;
    }

    function resendUserItem(userItem) {
      if (!userItem) {
        showToast('未找到对应的提问内容', true);
        return;
      }
      const txt = String(userItem.text || '').trim();
      const imgs = Array.isArray(userItem.images) ? userItem.images : [];
      if (!txt && !imgs.length) return;
      if (
        sendWs({
          type: 'prompt',
          text: txt,
          display_text: txt,
          images: imgs,
        })
      ) {
        state.autoScroll = true;
        scrollToBottom(true);
      }
    }

    function stripMarkdownForQuote(rawText) {
      let s = String(rawText || '');
      // Strip fenced code block markers while keeping concise content
      s = s.replace(/```[\w-]*\n?/g, ' ');
      // Replace markdown links [label](url) -> label
      s = s.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
      s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
      // Strip headings, blockquote markers, horizontal rules, list bullets
      s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '');
      s = s.replace(/^\s{0,3}>\s?/gm, '');
      s = s.replace(/^\s{0,3}(?:[-*+]|\d+\.)\s+/gm, '');
      // Strip inline formatting markers (bold/italic/code/strike)
      s = s.replace(/(\*\*|__|~~|`)/g, '');
      s = s.replace(/(^|\s)\*([^*\n]+)\*(?=\s|$|[.,!?;:，。！？；：])/g, '$1$2');
      return s.replace(/\s+/g, ' ').trim();
    }

    function parseUserMessageQuote(rawText) {
      const text = String(rawText || '').replace(/\r\n/g, '\n');
      const m = text.match(/^(?:>[ \t]?[^\n]*(?:\n|$))+/);
      if (!m) {
        return { quote: '', body: text };
      }
      const quoteLines = m[0]
        .split('\n')
        .map((line) => line.replace(/^>[ \t]?/, '').trim())
        .filter(Boolean);
      const quote = quoteLines.join('\n').trim();
      const body = text.slice(m[0].length).replace(/^\n+/, '');
      if (!quote) {
        return { quote: '', body: text };
      }
      return { quote, body };
    }

    function renderQuotePreview() {
      const q = state.quotedReply;
      if (!q || !q.excerpt) {
        els.quotePreviewBar.classList.add('hidden');
        els.quotePreviewText.textContent = '';
        updateComposerActionButton();
        return;
      }
      els.quotePreviewLabel.textContent = q.label || '引用回复';
      els.quotePreviewText.textContent = q.excerpt;
      els.quotePreviewJump.classList.toggle('cursor-pointer', Boolean(q.itemId));
      els.quotePreviewBar.classList.remove('hidden');
      updateComposerActionButton();
    }

    function clearQuotedReply() {
      if (!state.quotedReply) return;
      state.quotedReply = null;
      renderQuotePreview();
    }

    function flashMessageNode(itemId) {
      if (!itemId) return;
      const target = els.chatList.querySelector(`[data-item-id="${CSS.escape(itemId)}"]`);
      if (!target) return;
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.remove('msg-highlight-flash');
      void target.offsetWidth;
      target.classList.add('msg-highlight-flash');
    }

    function editUserItemInComposer(userItem) {
      if (!userItem) return;
      const raw = String(userItem.text || '').trim();
      const parsed = parseUserMessageQuote(raw);
      if (parsed.quote) {
        state.quotedReply = {
          excerpt: stripMarkdownForQuote(parsed.quote) || parsed.quote,
          itemId: '',
          label: '引用回复',
        };
        renderQuotePreview();
        els.promptInput.value = parsed.body.trim();
      } else {
        clearQuotedReply();
        els.promptInput.value = raw;
      }
      autoResizeInput();
      const imgs = Array.isArray(userItem.images) ? userItem.images : [];
      if (imgs.length) {
        state.uploadedImages = imgs.map((im) => ({ ...im }));
        renderImagePreviews();
      }
      els.promptInput.focus();
      els.promptInput.setSelectionRange(els.promptInput.value.length, els.promptInput.value.length);
    }

    function quoteTextInComposer(rawText, opts = {}) {
      const cleaned = stripMarkdownForQuote(rawText);
      if (!cleaned) return;
      const maxLen = opts.maxLen || 220;
      const excerpt = cleaned.length > maxLen ? `${cleaned.slice(0, maxLen).trim()}…` : cleaned;
      state.quotedReply = {
        excerpt,
        itemId: opts.itemId || '',
        label: opts.label || '引用回复',
      };
      renderQuotePreview();
      els.promptInput.focus();
      els.promptInput.setSelectionRange(els.promptInput.value.length, els.promptInput.value.length);
    }

    const MSG_ACTION_BTN_CLS =
      'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] text-slate-400 hover:text-slate-100 hover:bg-card active:scale-95 transition';

    async function fetchDeferredItemDetail(item) {
      const sid = state.currentSessionId;
      if (!sid || !item?.id) return item;
      const qs = state.currentProfile ? `?profile=${encodeURIComponent(state.currentProfile)}` : '';
      const res = await fetch(
        `/api/sessions/${encodeURIComponent(sid)}/items/${encodeURIComponent(item.id)}${qs}`,
        { credentials: 'same-origin' }
      );
      if (res.status === 401) {
        showAuthOverlay();
        return item;
      }
      if (!res.ok) return item;
      const data = await res.json().catch(() => ({}));
      const full = data.item;
      if (full && full.id === item.id) {
        state.itemsById.set(full.id, full);
        const idx = state.items.findIndex((x) => x.id === full.id);
        if (idx >= 0) state.items[idx] = full;
        return full;
      }
      return item;
    }

    function hydrateThoughtBody(node, item) {
      if (!node) return;
      const bodyEl = node.querySelector('.tool-body');
      if (!bodyEl) return;
      if (item?.detail_deferred) {
        if (node.dataset.fetchingDetail === '1') return;
        node.dataset.fetchingDetail = '1';
        bodyEl.textContent = '正在加载思考详情...';
        fetchDeferredItemDetail(item)
          .then((full) => {
            if (node.isConnected) {
              withScrollAnchor(node.querySelector('summary'), () => {
                bodyEl.textContent = full?.text || '';
                node.dataset.bodyLazy = '0';
              });
            }
          })
          .catch(() => {
            if (node.isConnected) bodyEl.textContent = item?.text || '加载详情失败';
          })
          .finally(() => {
            node.dataset.fetchingDetail = '0';
          });
        return;
      }
      bodyEl.textContent = item?.text || '';
      node.dataset.bodyLazy = '0';
    }

    function renderToolDetailText(item) {
      const argsStr =
        item?.args && Object.keys(item.args).length
          ? JSON.stringify(item.args, null, 2)
          : '';
      const outStr = item?.output || '';
      const detailParts = [];
      if (argsStr) detailParts.push(`// 输入参数\n${argsStr}`);
      if (outStr) detailParts.push(`// 执行输出\n${outStr}`);
      return detailParts.join('\n\n') || '(等待输出...)';
    }

    function hydrateToolBody(node, item) {
      if (!node) return;
      const bodyEl = node.querySelector('.tool-body');
      if (!bodyEl) return;
      if (item?.detail_deferred) {
        if (node.dataset.fetchingDetail === '1') return;
        node.dataset.fetchingDetail = '1';
        bodyEl.textContent = '正在加载工具详情...';
        fetchDeferredItemDetail(item)
          .then((full) => {
            if (node.isConnected) {
              withScrollAnchor(node.querySelector('summary'), () => {
                bodyEl.textContent = renderToolDetailText(full);
                node.dataset.bodyLazy = '0';
              });
            }
          })
          .catch(() => {
            if (node.isConnected) bodyEl.textContent = '加载详情失败';
          })
          .finally(() => {
            node.dataset.fetchingDetail = '0';
          });
        return;
      }
      bodyEl.textContent = renderToolDetailText(item);
      node.dataset.bodyLazy = '0';
    }

    function createOrUpdateItemDom(item, opts = {}) {
      const bulk = Boolean(opts.bulk);
      const fresh = Boolean(opts.fresh);
      const container = opts.container || els.chatList;
      if (!bulk) els.emptyState.classList.add('hidden');
      let node = fresh ? null : document.querySelector(`[data-item-id="${CSS.escape(item.id)}"]`);

      if (item.type === 'user') {
        if (!node) {
          node = document.createElement('div');
          node.dataset.itemId = item.id;
          node.dataset.itemType = 'user';
          node.className = 'flex flex-col items-end gap-1';
          container.appendChild(node);
        }
        const imgList = item.images || [];
        const imgs = imgList
          .map(
            (im, idx) =>
              `<button type="button" data-img-idx="${idx}" class="block rounded-lg overflow-hidden active:opacity-80 focus:outline-none"><img src="${escapeHtml(im.url)}" alt="image" class="max-h-40 rounded-lg border border-blue-400/30 object-cover" /></button>`
          )
          .join('');
        const textTrimmed = String(item.text || '').trim();
        const parsedMsg = parseUserMessageQuote(textTrimmed);
        const quoteDisplay = parsedMsg.quote ? (stripMarkdownForQuote(parsedMsg.quote) || parsedMsg.quote) : '';
        const bodyDisplay = parsedMsg.quote ? parsedMsg.body.trim() : (item.text || '');
        const quoteExpandable = quoteDisplay.length > 72;
        const quoteHtml = quoteDisplay
          ? `<div class="user-quote-card${quoteExpandable ? ' is-expandable' : ''}"${quoteExpandable ? ' title="点击展开/收起完整引用"' : ''}>
              <span class="user-quote-bar"></span>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-1 text-[10.5px] font-medium text-blue-100/75 mb-0.5 select-none">
                  <svg class="w-2.5 h-2.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3"/></svg>
                  <span>引用</span>
                </div>
                <div class="user-quote-text">${escapeHtml(quoteDisplay)}</div>
              </div>
            </div>`
          : '';
        const clockStr = formatClockTime(item.ts);
        node.innerHTML = `
          <div class="max-w-[86%] rounded-2xl rounded-br-md bg-blue-600 text-white px-3.5 py-2.5 shadow-sm selectable-text space-y-2">
            ${imgs ? `<div class="flex flex-wrap gap-1.5">${imgs}</div>` : ''}
            ${quoteHtml}
            ${bodyDisplay ? `<div class="text-[15px] leading-relaxed whitespace-pre-wrap break-words">${escapeHtml(bodyDisplay)}</div>` : ''}
          </div>
          <div class="flex items-center gap-1 pr-0.5 select-none">
            ${clockStr ? `<span class="text-[10px] text-slate-500 mr-1">${escapeHtml(clockStr)}</span>` : ''}
            ${textTrimmed ? `<button type="button" class="btn-msg-copy ${MSG_ACTION_BTN_CLS}" title="复制消息">复制</button>` : ''}
            <button type="button" class="btn-msg-edit ${MSG_ACTION_BTN_CLS}" title="回填到输入框编辑">编辑</button>
            <button type="button" class="btn-msg-retry ${MSG_ACTION_BTN_CLS}" title="重新发送">重试</button>
          </div>`;
        const quoteCard = node.querySelector('.user-quote-card.is-expandable');
        if (quoteCard) {
          quoteCard.addEventListener('click', () => {
            const sel = window.getSelection ? String(window.getSelection()).trim() : '';
            if (sel) return;
            quoteCard.classList.toggle('is-expanded');
          });
        }
        if (imgList.length) {
          const urls = imgList.map((im) => im.url).filter(Boolean);
          node.querySelectorAll('[data-img-idx]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
              e.preventDefault();
              e.stopPropagation();
              openImageModal(urls, Number(btn.dataset.imgIdx) || 0);
            });
          });
        }
        const copyBtn = node.querySelector('.btn-msg-copy');
        if (copyBtn) {
          copyBtn.addEventListener('click', () => {
            const cur = state.itemsById.get(item.id) || item;
            copyText(cur.text || '', copyBtn);
          });
        }
        const editBtn = node.querySelector('.btn-msg-edit');
        if (editBtn) {
          editBtn.addEventListener('click', () => {
            editUserItemInComposer(state.itemsById.get(item.id) || item);
          });
        }
        const retryBtn = node.querySelector('.btn-msg-retry');
        if (retryBtn) {
          retryBtn.addEventListener('click', () => {
            resendUserItem(state.itemsById.get(item.id) || item);
          });
        }
      } else if (item.type === 'assistant') {
        if (!node) {
          node = document.createElement('div');
          node.dataset.itemId = item.id;
          node.dataset.itemType = 'assistant';
          node.className = 'rounded-2xl border border-border/80 bg-surface px-3.5 py-3 shadow-sm';
          const clockStr = formatClockTime(item.ts);
          node.innerHTML = `
            <div class="md-body selectable-text"></div>
            <div class="mt-2.5 pt-2 border-t border-border/50 flex items-center justify-between gap-2 select-none">
              <span class="text-[10px] text-slate-500">${escapeHtml(clockStr)}</span>
              <div class="flex items-center gap-1">
                <button type="button" class="btn-msg-copy ${MSG_ACTION_BTN_CLS}" title="复制回复全文">复制</button>
                <button type="button" class="btn-msg-quote ${MSG_ACTION_BTN_CLS}" title="引用此回复继续提问">引用</button>
                <button type="button" class="btn-msg-retry ${MSG_ACTION_BTN_CLS}" title="重新生成此回复">重试</button>
              </div>
            </div>`;
          const copyBtn = node.querySelector('.btn-msg-copy');
          copyBtn.addEventListener('click', () => {
            const cur = state.itemsById.get(item.id) || item;
            copyText(cur.text || '', copyBtn);
          });
          let capturedSelection = '';
          const quoteBtn = node.querySelector('.btn-msg-quote');
          quoteBtn.addEventListener('pointerdown', () => {
            capturedSelection = '';
            const sel = window.getSelection ? window.getSelection() : null;
            if (!sel || sel.isCollapsed) return;
            const selectedText = String(sel).trim();
            if (!selectedText) return;
            const anchorInNode = sel.anchorNode && node.contains(sel.anchorNode);
            const focusInNode = sel.focusNode && node.contains(sel.focusNode);
            if (anchorInNode || focusInNode) {
              capturedSelection = selectedText;
            }
          });
          quoteBtn.addEventListener('click', () => {
            const cur = state.itemsById.get(item.id) || item;
            if (capturedSelection) {
              quoteTextInComposer(capturedSelection, {
                itemId: item.id,
                label: '引用选中内容',
                maxLen: 260,
              });
              capturedSelection = '';
            } else {
              quoteTextInComposer(cur.text || '', {
                itemId: item.id,
                label: '引用回复',
              });
            }
          });
          node.querySelector('.btn-msg-retry').addEventListener('click', () => {
            const prevUser = findPrecedingUserItem(node);
            resendUserItem(prevUser);
          });
          container.appendChild(node);
        }
        const body = node.querySelector('.md-body');
        if (bulk) {
          renderMarkdownInto(body, item.text || '');
        } else {
          clearTimeout(state.renderTimers.get(item.id));
          state.renderTimers.set(
            item.id,
            setTimeout(() => {
              renderMarkdownInto(body, item.text || '');
              scrollToBottom();
            }, 35)
          );
        }
      } else if (item.type === 'thought') {
        let group = node ? node.closest('details.process-group') : null;
        if (!node) {
          group = getOrCreateProcessGroup(!bulk, container);
          node = document.createElement('details');
          node.dataset.itemId = item.id;
          node.dataset.itemType = 'thought';
          node.dataset.bodyLazy = '1';
          node.className = 'tool-card';
          node.innerHTML = `
            <summary>
              <svg class="chevron w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
              <span class="w-2 h-2 rounded-full bg-purple-400 shrink-0"></span>
              <span class="font-medium text-purple-200">思考过程</span>
              <span class="thought-preview text-xs text-slate-400 truncate flex-1"></span>
            </summary>
            <div class="tool-body selectable-text"></div>`;
          node.addEventListener('toggle', () => {
            if (node.open && node.dataset.bodyLazy === '1') {
              hydrateThoughtBody(node, state.itemsById.get(item.id) || item);
            }
          });
          group.querySelector('.process-group-body').appendChild(node);
        }
        const preview = (item.text || '').replace(/\s+/g, ' ').slice(0, 70);
        node.querySelector('.thought-preview').textContent = preview;
        if (node.open) {
          hydrateThoughtBody(node, item);
        } else {
          node.dataset.bodyLazy = '1';
        }
        if (!bulk) updateProcessGroupSummary(group);
      } else if (item.type === 'tool') {
        let group = node ? node.closest('details.process-group') : null;
        if (!node) {
          group = getOrCreateProcessGroup(!bulk, container);
          node = document.createElement('details');
          node.dataset.itemId = item.id;
          node.dataset.itemType = 'tool';
          node.dataset.bodyLazy = '1';
          node.className = 'tool-card';
          node.innerHTML = `
            <summary></summary>
            <div class="tool-body selectable-text"></div>`;
          node.addEventListener('toggle', () => {
            if (node.open && node.dataset.bodyLazy === '1') {
              hydrateToolBody(node, state.itemsById.get(item.id) || item);
            }
          });
          group.querySelector('.process-group-body').appendChild(node);
        }
        const title = item.title || item.name || '工具调用';
        const summaryEl = node.querySelector('summary');
        if (summaryEl) {
          summaryEl.innerHTML = `
            <svg class="chevron w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
            ${statusBadgeHtml(item.status)}
            <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 shrink-0">${escapeHtml(toolKindLabel(item))}</span>
            <span class="truncate text-slate-200 font-mono text-xs flex-1">${escapeHtml(title)}</span>`;
        }
        if (node.open) {
          hydrateToolBody(node, item);
        } else {
          node.dataset.bodyLazy = '1';
        }
        if (!bulk) updateProcessGroupSummary(group);
      } else if (item.type === 'error') {
        const isEarlyStop = item.kind === 'early_stop';
        if (!node) {
          node = document.createElement('div');
          node.dataset.itemId = item.id;
          node.dataset.itemType = 'error';
          node.className = isEarlyStop
            ? 'rounded-xl border border-amber-500/45 bg-amber-950/35 px-3.5 py-2.5 text-xs text-amber-200 selectable-text flex items-center justify-between gap-2.5'
            : 'rounded-xl border border-rose-500/40 bg-rose-950/35 px-3.5 py-2.5 text-xs text-rose-200 selectable-text';
          container.appendChild(node);
        }
        if (isEarlyStop) {
          node.innerHTML = `
            <span class="min-w-0 leading-relaxed">⚠ ${escapeHtml(item.text || '')}</span>
            <button type="button" class="btn-continue-turn shrink-0 px-2.5 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/35 border border-amber-400/40 text-amber-100 font-medium active:scale-95 transition">继续</button>
          `;
          node.querySelector('.btn-continue-turn').addEventListener('click', () => {
            sendWs({ type: 'prompt', text: '继续', display_text: '继续', images: [] });
          });
        } else {
          node.textContent = `错误: ${item.text || ''}`;
        }
      }

      if (!bulk) scrollToBottom();
    }

    function createLoadOlderBar() {
      const bar = document.createElement('div');
      bar.id = 'load-older-bar';
      bar.className = 'flex justify-center py-1 select-none';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className =
        'px-3.5 py-1.5 rounded-full border border-border bg-surface hover:bg-card text-xs text-slate-300 active:scale-95 transition flex items-center gap-1.5';
      btn.addEventListener('click', () => loadOlderItems());
      bar.appendChild(btn);
      updateLoadOlderBarText(bar);
      return bar;
    }

    function updateLoadOlderBarText(barEl) {
      const bar = barEl || document.getElementById('load-older-bar');
      if (!bar) return;
      const btn = bar.querySelector('button');
      if (!btn) return;
      if (state.loadingOlderItems) {
        btn.disabled = true;
        btn.innerHTML =
          '<span class="inline-block w-3 h-3 rounded-full border-[1.5px] border-slate-500 border-t-blue-400 animate-spin"></span><span>正在加载更早记录...</span>';
      } else {
        btn.disabled = false;
        const remain = Math.max(0, Number(state.itemsOffset) || 0);
        btn.textContent = remain > 0 ? `加载更早记录（还有 ${remain} 条）` : '加载更早记录';
      }
    }

    async function loadOlderItems() {
      if (state.loadingOlderItems || !state.hasMoreItems || !state.currentSessionId || !state.items.length) {
        return;
      }
      const targetSid = state.currentSessionId;
      const firstItem = state.items[0];
      state.loadingOlderItems = true;
      updateLoadOlderBarText();
      try {
        const qs = new URLSearchParams({
          before_id: firstItem?.id || '',
          before_offset: String(state.itemsOffset || 0),
          limit: '200',
        });
        if (state.currentProfile) qs.set('profile', state.currentProfile);
        const res = await fetch(
          `/api/sessions/${encodeURIComponent(targetSid)}/items?${qs.toString()}`,
          { credentials: 'same-origin' }
        );
        if (res.status === 401) {
          showAuthOverlay();
          return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.detail || `加载失败 (${res.status})`);
        }
        if (state.currentSessionId !== targetSid) return;
        const older = (Array.isArray(data.items) ? data.items : []).filter((it) => it && !state.itemsById.has(it.id));
        state.totalItems = Number(data.total_items ?? state.totalItems);
        state.itemsOffset = Number(data.items_offset ?? 0);
        state.hasMoreItems = Boolean(data.has_more_items) && older.length > 0;

        const bar = document.getElementById('load-older-bar');
        if (older.length) {
          const frag = document.createDocumentFragment();
          for (const it of older) {
            state.itemsById.set(it.id, it);
            createOrUpdateItemDom(it, { bulk: true, fresh: true, container: frag });
          }
          frag.querySelectorAll('details.process-group').forEach((grp) => {
            grp.dataset.live = '0';
            grp.open = false;
            updateProcessGroupSummary(grp);
          });

          state.items = older.concat(state.items);
          state.suppressAutoLoadUntil = Date.now() + 260;
          // column-reverse pins scroll origin to BOTTOM: prepending at top needs ZERO scrollTop mutation!
          if (bar && bar.parentNode === els.chatList) {
            els.chatList.insertBefore(frag, bar.nextSibling);
            if (!state.hasMoreItems) bar.remove();
          } else {
            els.chatList.insertBefore(frag, els.chatList.firstChild);
          }
          state.lastScrollTop = getDistanceFromBottom();
          state.lastOlderLoadAt = Date.now();
        } else if (!state.hasMoreItems && bar) {
          bar.remove();
        }
      } catch (err) {
        showToast(err.message || '加载历史记录失败', true);
      } finally {
        state.loadingOlderItems = false;
        updateLoadOlderBarText();
      }
    }

    function renderAllItems(items) {
      state.suppressAutoLoadUntil = Date.now() + 600;
      state.items = items || [];
      state.itemsById.clear();
      els.chatList.querySelectorAll('[data-item-id], details.process-group, #load-older-bar').forEach((el) => el.remove());

      if (!state.items.length) {
        els.emptyState.classList.remove('hidden');
        els.chatScroll.scrollTop = 0;
        state.lastScrollTop = 0;
        return;
      }
      els.emptyState.classList.add('hidden');
      const frag = document.createDocumentFragment();
      if (state.hasMoreItems) {
        frag.appendChild(createLoadOlderBar());
      }
      for (const it of state.items) {
        state.itemsById.set(it.id, it);
        createOrUpdateItemDom(it, { bulk: true, fresh: true, container: frag });
      }

      // Collapse all historical execution groups; expand ONLY the trailing one if currently busy
      const allGroups = Array.from(frag.querySelectorAll('details.process-group'));
      const lastChild = frag.lastElementChild;
      allGroups.forEach((grp) => {
        const isActiveTrailing = state.isBusy && grp === lastChild;
        grp.dataset.live = isActiveTrailing ? '1' : '0';
        grp.open = isActiveTrailing;
        updateProcessGroupSummary(grp);
      });

      els.chatList.appendChild(frag);
      // With column-reverse, scrollTop=0 is natively the bottom — zero jump, zero flash
      els.chatScroll.scrollTop = 0;
      state.lastScrollTop = 0;
      state.autoScroll = true;
      els.btnScrollBottom.classList.add('hidden');
    }

    // =========================================================================
    // Permission Bar Rendering
    // =========================================================================
    function renderPermissions() {
      const list = Array.from(state.pendingPermissions.values());
      if (!list.length) {
        els.permissionBar.classList.add('hidden');
        els.permissionList.innerHTML = '';
        return;
      }
      els.permissionBar.classList.remove('hidden');
      els.permissionList.innerHTML = '';

      list.forEach((perm) => {
        const tc = perm.tool_call || {};
        const cmdPreview =
          tc.args?.CommandLine ||
          tc.args?.Path ||
          tc.title ||
          JSON.stringify(tc.args || {});

        const card = document.createElement('div');
        card.className = 'rounded-xl border border-amber-500/40 bg-[#231c12] p-3 space-y-2.5';
        card.innerHTML = `
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <div class="text-xs font-semibold text-amber-300 flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-amber-400 animate-soft-pulse"></span>
                <span>请求权限 · ${escapeHtml(tc.name || '工具调用')}</span>
              </div>
              <div class="mt-1 text-xs font-mono text-amber-100/90 break-all max-h-20 overflow-y-auto selectable-text">${escapeHtml(cmdPreview)}</div>
            </div>
          </div>
          <div class="flex flex-wrap gap-2 perm-actions"></div>
        `;

        const actionsEl = card.querySelector('.perm-actions');
        // Always place Reject/Deny on the LEFT and Allow on the RIGHT (prevents right-thumb accidental denies)
        const sortedOptions = (perm.options || []).slice().sort((a, b) => {
          const rank = (o) => {
            const k = String(o?.kind || '');
            const id = String(o?.optionId || '');
            if (k.startsWith('reject') || id === 'deny') return 0;
            if (k.startsWith('allow') || id === 'allow') return 2;
            return 1;
          };
          return rank(a) - rank(b);
        });
        sortedOptions.forEach((opt) => {
          const isAllow = (opt.kind || '').startsWith('allow') || opt.optionId === 'allow';
          const isReject = (opt.kind || '').startsWith('reject') || opt.optionId === 'deny';
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = `flex-1 min-w-[90px] py-2 px-3 rounded-lg text-xs font-semibold active:scale-95 transition ${
            isAllow
              ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
              : isReject
              ? 'bg-rose-600/90 hover:bg-rose-500 text-white'
              : 'bg-slate-700 hover:bg-slate-600 text-slate-100'
          }`;
          const zhLabel =
            opt.optionId === 'allow'
              ? '允许'
              : opt.optionId === 'deny'
              ? '拒绝'
              : opt.name || opt.optionId;
          btn.textContent = zhLabel;
          btn.addEventListener('click', () => {
            sendWs({
              type: 'permission_response',
              key: perm.key,
              option_id: opt.optionId,
              cancelled: false,
            });
          });
          actionsEl.appendChild(btn);
        });

        els.permissionList.appendChild(card);
      });
    }

    // =========================================================================
    // Queued Messages Bar (Per-Session FIFO Message Queue)
    // =========================================================================
    function renderQueueBar() {
      const list = state.queuedPrompts || [];
      if (!list.length) {
        els.queueBar.classList.add('hidden');
        els.queueList.innerHTML = '';
        return;
      }
      els.queueBar.classList.remove('hidden');
      els.queueCount.textContent = state.isBusy
        ? `待发送队列 (${list.length}) · 当前回合结束后自动执行`
        : `待发送队列 (${list.length}) · 已暂停`;
      els.btnQueueRun.classList.toggle('hidden', state.isBusy);

      els.queueList.innerHTML = '';
      list.forEach((entry, idx) => {
        const row = document.createElement('div');
        row.className = 'px-3 py-2 flex items-center gap-2 hover:bg-card/60 transition';
        const imgs = Array.isArray(entry.images) ? entry.images : [];
        const thumbHtml = imgs.length
          ? `<img src="${escapeHtml(imgs[0].url || '')}" class="w-7 h-7 rounded-md object-cover border border-border shrink-0" />`
          : '';
        const rawQueueText = (entry.display_text || entry.text || '').trim();
        const parsedQueue = parseUserMessageQuote(rawQueueText);
        const textPreview = parsedQueue.quote
          ? (parsedQueue.body.trim() ? `「引用」${parsedQueue.body.trim()}` : `「引用」${stripMarkdownForQuote(parsedQueue.quote)}`)
          : (rawQueueText || (imgs.length ? `[${imgs.length} 张图片]` : '(空消息)'));
        row.innerHTML = `
          <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 shrink-0">#${idx + 1}</span>
          ${thumbHtml}
          <button type="button" class="btn-queue-edit flex-1 min-w-0 text-left text-xs text-slate-200 truncate hover:text-white" title="点击撤回到输入框编辑">${escapeHtml(textPreview)}</button>
          ${imgs.length > 1 ? `<span class="text-[10px] text-slate-400 shrink-0">+${imgs.length - 1}图</span>` : ''}
          <button type="button" class="btn-queue-steer px-1.5 py-0.5 rounded-md bg-amber-500/15 hover:bg-amber-500/30 border border-amber-400/35 text-[10px] text-amber-200 font-medium active:scale-95 transition shrink-0" title="${state.isBusy ? '打断当前回合并立即插队发送' : '立即发送此消息'}">${state.isBusy ? '插队' : '发送'}</button>
          <button type="button" class="btn-queue-del p-1 rounded-md text-slate-400 hover:text-rose-300 hover:bg-rose-950/60 active:scale-95 transition shrink-0" title="从队列移除">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        `;
        row.querySelector('.btn-queue-edit').addEventListener('click', () => {
          // Pull item back into composer for editing and remove from queue
          const existingText = els.promptInput.value.trim();
          const pulledText = parsedQueue.quote ? parsedQueue.body.trim() : rawQueueText;
          if (parsedQueue.quote) {
            state.quotedReply = {
              excerpt: stripMarkdownForQuote(parsedQueue.quote) || parsedQueue.quote,
              itemId: '',
              label: '引用回复',
            };
            renderQuotePreview();
          }
          if (pulledText) {
            els.promptInput.value = existingText ? `${pulledText}\n${existingText}` : pulledText;
          }
          autoResizeInput();
          if (imgs.length) {
            state.uploadedImages.push(...imgs);
            renderImagePreviews();
          }
          sendWs({ type: 'queue_remove', queue_id: entry.id });
          els.promptInput.focus();
        });
        row.querySelector('.btn-queue-steer').addEventListener('click', () => {
          sendWs({ type: 'queue_steer', queue_id: entry.id });
        });
        row.querySelector('.btn-queue-del').addEventListener('click', () => {
          sendWs({ type: 'queue_remove', queue_id: entry.id });
        });
        els.queueList.appendChild(row);
      });
    }

    els.btnQueueRun.addEventListener('click', () => {
      sendWs({ type: 'queue_run_next' });
    });
    els.btnQueueClear.addEventListener('click', () => {
      sendWs({ type: 'queue_clear' });
    });

