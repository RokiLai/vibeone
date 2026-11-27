    const _streamRenderJobs = new Map();
    const STREAM_SMOOTH_MAX_LEN = 12000;

    function _nextSafeSliceLen(text, targetLen) {
      if (targetLen >= text.length) return text.length;
      const code = text.charCodeAt(targetLen - 1);
      if (code >= 0xd800 && code <= 0xdbff && targetLen < text.length) {
        return targetLen + 1;
      }
      return targetLen;
    }

    function clearStreamingMarkdownRenders() {
      _streamRenderJobs.forEach((job) => {
        if (job.timer) clearTimeout(job.timer);
        if (job.finalizeTimer) clearTimeout(job.finalizeTimer);
        if (job.rafId !== null) {
          if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(job.rafId);
          else clearTimeout(job.rafId);
        }
      });
      _streamRenderJobs.clear();
    }

    function flushStreamingMarkdownRenders(opts = {}) {
      const doHighlight = opts.highlight !== false;
      _streamRenderJobs.forEach((job, itemId) => {
        if (job.timer) {
          clearTimeout(job.timer);
          job.timer = null;
        }
        if (job.finalizeTimer) {
          clearTimeout(job.finalizeTimer);
          job.finalizeTimer = null;
        }
        if (job.rafId !== null) {
          if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(job.rafId);
          else clearTimeout(job.rafId);
          job.rafId = null;
        }
        if (!job.body || !job.body.isConnected) return;
        const cur = state.itemsById.get(itemId) || job.item;
        const fullText = String(cur?.text || '');
        if (job.dirty || job.displayedLen < fullText.length) {
          job.dirty = false;
          job.displayedLen = fullText.length;
          renderMarkdownInto(job.body, fullText, { deferHighlight: doHighlight, skipHighlight: !doHighlight });
          scrollToBottom();
        } else if (doHighlight && typeof highlightUnmarkedCodeIn === 'function') {
          highlightUnmarkedCodeIn(job.body);
        }
      });
      _streamRenderJobs.clear();
    }

    function finishStreamingMarkdownRenders() {
      _streamRenderJobs.forEach((job, itemId) => {
        job.turnEnded = true;
        if (job.finalizeTimer) {
          clearTimeout(job.finalizeTimer);
          job.finalizeTimer = null;
        }
        if (!job.body || !job.body.isConnected) {
          _streamRenderJobs.delete(itemId);
          return;
        }
        const cur = state.itemsById.get(itemId) || job.item;
        const fullText = String(cur?.text || '');
        if (job.displayedLen >= fullText.length && !job.dirty) {
          if (typeof highlightUnmarkedCodeIn === 'function') highlightUnmarkedCodeIn(job.body);
          _streamRenderJobs.delete(itemId);
        }
      });
    }

    function scheduleStreamingMarkdownRender(item, body) {
      if (!item || !body) return;
      const itemId = item.id;
      const initialText = String(item.text || '');
      let job = _streamRenderJobs.get(itemId);
      if (!job) {
        job = {
          item,
          body,
          dirty: false,
          turnEnded: false,
          displayedLen: initialText.length > 400 ? initialText.length : 0,
          charBudget: 0,
          lastTickAt: 0,
          lastRenderAt: 0,
          timer: null,
          rafId: null,
          finalizeTimer: null,
        };
        _streamRenderJobs.set(itemId, job);
      }
      job.item = item;
      job.body = body;
      job.dirty = true;

      if (job.finalizeTimer) clearTimeout(job.finalizeTimer);
      job.finalizeTimer = setTimeout(() => {
        job.finalizeTimer = null;
        if (!job.body || !job.body.isConnected) {
          _streamRenderJobs.delete(itemId);
          return;
        }
        const cur = state.itemsById.get(itemId) || job.item;
        const fullText = String(cur?.text || '');
        if (job.displayedLen < fullText.length && job.rafId !== null) {
          job.turnEnded = true;
          return;
        }
        if (job.dirty || job.displayedLen < fullText.length) {
          if (job.timer) { clearTimeout(job.timer); job.timer = null; }
          if (job.rafId !== null) {
            if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(job.rafId);
            else clearTimeout(job.rafId);
            job.rafId = null;
          }
          job.dirty = false;
          job.displayedLen = fullText.length;
          renderMarkdownInto(job.body, fullText, { deferHighlight: true });
          scrollToBottom();
        } else if (typeof highlightUnmarkedCodeIn === 'function') {
          highlightUnmarkedCodeIn(job.body);
        }
        _streamRenderJobs.delete(itemId);
      }, 900);

      if (job.timer || job.rafId !== null) return;

      const scheduleRaf = () => {
        if (typeof requestAnimationFrame === 'function') {
          job.rafId = requestAnimationFrame(runFrame);
        } else {
          job.rafId = setTimeout(runFrame, 16);
        }
      };

      const runFrame = () => {
        job.rafId = null;
        if (!job.body || !job.body.isConnected) {
          _streamRenderJobs.delete(itemId);
          return;
        }
        const cur = state.itemsById.get(itemId) || job.item;
        const fullText = String(cur?.text || '');
        if (!job.dirty && job.displayedLen >= fullText.length) {
          if (job.turnEnded) {
            if (typeof highlightUnmarkedCodeIn === 'function') highlightUnmarkedCodeIn(job.body);
            _streamRenderJobs.delete(itemId);
          }
          return;
        }

        const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
        const rawDt = job.lastTickAt ? now - job.lastTickAt : 16;
        const dtMs = rawDt > 0 ? Math.min(64, rawDt) : 16;
        job.lastTickAt = now;

        if (fullText.length > STREAM_SMOOTH_MAX_LEN) {
          const elapsed = now - job.lastRenderAt;
          if (job.lastRenderAt && elapsed < 36) {
            job.timer = setTimeout(() => {
              job.timer = null;
              scheduleRaf();
            }, 36 - elapsed);
            return;
          }
          job.displayedLen = fullText.length;
        } else if (job.displayedLen < fullText.length) {
          const backlog = fullText.length - job.displayedLen;
          // Time-based reading pace (consistent across 60Hz and 120Hz ProMotion displays)
          let cps = 55;
          if (backlog > 2500) cps = Math.min(260, 140 + (backlog - 2500) * 0.05);
          else if (backlog > 600) cps = Math.min(140, 55 + (backlog - 600) * 0.05);
          job.charBudget += (dtMs / 1000) * cps;
          const step = Math.floor(job.charBudget);
          if (step < 1) {
            scheduleRaf();
            return;
          }
          job.charBudget -= step;
          job.displayedLen = _nextSafeSliceLen(fullText, Math.min(fullText.length, job.displayedLen + step));
        } else {
          job.displayedLen = fullText.length;
        }

        job.dirty = false;
        const reachedEnd = job.displayedLen >= fullText.length;
        renderMarkdownInto(job.body, fullText.slice(0, job.displayedLen), {
          skipHighlight: !(reachedEnd && job.turnEnded),
          deferHighlight: Boolean(reachedEnd && job.turnEnded),
        });
        job.lastRenderAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
        scrollToBottom();

        if (!reachedEnd) {
          scheduleRaf();
        } else if (job.turnEnded) {
          _streamRenderJobs.delete(itemId);
        }
      };

      scheduleRaf();
    }

    let _capturedQuoteSelection = '';
    let _capturedQuoteItemId = '';
    if (els.chatList && typeof els.chatList.addEventListener === 'function') {
      els.chatList.addEventListener('pointerdown', (e) => {
        const quoteBtn = e.target && e.target.closest ? e.target.closest('.btn-msg-quote') : null;
        if (!quoteBtn) return;
        _capturedQuoteSelection = '';
        _capturedQuoteItemId = '';
        const node = quoteBtn.closest('[data-item-id]');
        if (!node) return;
        const sel = window.getSelection ? window.getSelection() : null;
        if (!sel || sel.isCollapsed) return;
        const selectedText = String(sel).trim();
        if (!selectedText) return;
        const anchorInNode = sel.anchorNode && node.contains(sel.anchorNode);
        const focusInNode = sel.focusNode && node.contains(sel.focusNode);
        if (anchorInNode || focusInNode) {
          _capturedQuoteSelection = selectedText;
          _capturedQuoteItemId = node.dataset.itemId || '';
        }
      });

      els.chatList.addEventListener('click', (e) => {
        const target = e.target;
        if (!target || !target.closest) return;

        const imgBtn = target.closest('[data-img-idx]');
        if (imgBtn && els.chatList.contains(imgBtn)) {
          const node = imgBtn.closest('[data-item-id]');
          const cur = node ? state.itemsById.get(node.dataset.itemId) : null;
          const urls = (cur?.images || []).map((im) => im.url).filter(Boolean);
          if (urls.length) {
            e.preventDefault();
            e.stopPropagation();
            openImageModal(urls, Number(imgBtn.dataset.imgIdx) || 0);
          }
          return;
        }

        const quoteCard = target.closest('.user-quote-card.is-expandable');
        if (quoteCard && els.chatList.contains(quoteCard)) {
          const sel = window.getSelection ? String(window.getSelection()).trim() : '';
          if (!sel) quoteCard.classList.toggle('is-expanded');
          return;
        }

        const actionBtn = target.closest('.btn-msg-copy, .btn-msg-edit, .btn-msg-retry, .btn-msg-quote, .btn-continue-turn');
        if (!actionBtn || !els.chatList.contains(actionBtn)) return;
        if (actionBtn.classList.contains('btn-continue-turn')) {
          sendWs({ type: 'prompt', text: '继续', display_text: '继续', images: [] });
          return;
        }
        const node = actionBtn.closest('[data-item-id]');
        if (!node) return;
        const itemId = node.dataset.itemId || '';
        const itemType = node.dataset.itemType || '';
        const cur = state.itemsById.get(itemId);
        if (actionBtn.classList.contains('btn-msg-copy')) {
          copyText(cur?.text || '', actionBtn);
        } else if (actionBtn.classList.contains('btn-msg-edit')) {
          editUserItemInComposer(cur);
        } else if (actionBtn.classList.contains('btn-msg-retry')) {
          if (itemType === 'assistant') {
            resendUserItem(findPrecedingUserItem(node));
          } else {
            resendUserItem(cur);
          }
        } else if (actionBtn.classList.contains('btn-msg-quote')) {
          if (_capturedQuoteSelection && _capturedQuoteItemId === itemId) {
            quoteTextInComposer(_capturedQuoteSelection, {
              itemId,
              label: '引用选中内容',
              maxLen: 260,
            });
          } else {
            quoteTextInComposer(cur?.text || '', {
              itemId,
              label: '引用回复',
            });
          }
          _capturedQuoteSelection = '';
          _capturedQuoteItemId = '';
        }
      });
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
              `<button type="button" data-img-idx="${idx}" class="block rounded-lg overflow-hidden active:opacity-80 focus:outline-none"><img src="${escapeHtml(im.url)}" alt="image" loading="lazy" decoding="async" class="max-h-40 rounded-lg border border-blue-400/30 object-cover" /></button>`
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
          container.appendChild(node);
        }
        const body = node.querySelector('.md-body');
        if (bulk) {
          renderMarkdownInto(body, item.text || '', { deferHighlight: true });
        } else {
          scheduleStreamingMarkdownRender(item, body);
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
        } else {
          node.textContent = `错误: ${item.text || ''}`;
        }
      }

      if (!bulk) scrollToBottom();
    }

