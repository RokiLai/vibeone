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

