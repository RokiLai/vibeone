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

