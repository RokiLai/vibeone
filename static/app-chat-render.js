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
          renderMarkdownInto(body, item.text || '', { deferHighlight: true });
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

