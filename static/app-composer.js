    // =========================================================================
    // Image Upload & Prompt Composer
    // =========================================================================
    // =========================================================================
    // Image Upload (Picker + Direct Clipboard Paste + Drag & Drop)
    // =========================================================================
    function renderImagePreviews() {
      if (!state.uploadedImages.length) {
        els.imagePreviewRow.classList.add('hidden');
        els.imagePreviewRow.innerHTML = '';
        if (typeof updateComposerActionButton === 'function') updateComposerActionButton();
        return;
      }
      els.imagePreviewRow.classList.remove('hidden');
      els.imagePreviewRow.innerHTML = '';
      const readyUrls = state.uploadedImages.map((x) => x.url || x.previewUrl).filter(Boolean);
      state.uploadedImages.forEach((img, idx) => {
        const src = img.url || img.previewUrl || '';
        const wrap = document.createElement('div');
        wrap.className = 'relative shrink-0';
        wrap.innerHTML = `
          <img src="${escapeHtml(src)}" class="w-14 h-14 rounded-xl object-cover border ${img.uploading ? 'border-blue-500/60 opacity-65' : 'border-border'} cursor-pointer" />
          ${img.uploading ? '<div class="absolute inset-0 rounded-xl bg-bg/45 flex items-center justify-center"><span class="w-3.5 h-3.5 rounded-full border-2 border-blue-400 border-t-transparent animate-spin"></span></div>' : ''}
          <button type="button" class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-slate-900 border border-border text-xs text-slate-200 flex items-center justify-center">×</button>
        `;
        wrap.querySelector('img').addEventListener('click', () => {
          if (readyUrls.length) openImageModal(readyUrls, idx);
        });
        wrap.querySelector('button').addEventListener('click', (e) => {
          e.stopPropagation();
          const removed = state.uploadedImages.splice(idx, 1)[0];
          if (removed?.previewUrl) {
            try { URL.revokeObjectURL(removed.previewUrl); } catch (_) {}
          }
          renderImagePreviews();
        });
        els.imagePreviewRow.appendChild(wrap);
      });
      if (typeof updateComposerActionButton === 'function') updateComposerActionButton();
    }

    async function uploadImageFiles(fileList) {
      const files = Array.from(fileList || []).filter((f) => {
        if (!f) return false;
        if (f.type && f.type.startsWith('image/')) return true;
        return /\.(png|jpe?g|gif|webp|heic|heif|bmp)$/i.test(f.name || '');
      });
      if (!files.length) return;

      for (const file of files) {
        const previewUrl = URL.createObjectURL(file);
        const placeholder = { uploading: true, previewUrl, url: '', filename: file.name || 'image.png' };
        state.uploadedImages.push(placeholder);
        renderImagePreviews();

        const extFromMime = (file.type || '').split('/')[1]?.replace('jpeg', 'jpg') || 'png';
        const uploadName = file.name && file.name.includes('.') ? file.name : `pasted_${Date.now()}.${extFromMime}`;
        const fd = new FormData();
        fd.append('file', file, uploadName);

        try {
          const res = await fetch('/api/upload', {
            method: 'POST',
            body: fd,
            credentials: 'same-origin',
          });
          if (res.status === 401) {
            const i = state.uploadedImages.indexOf(placeholder);
            if (i >= 0) state.uploadedImages.splice(i, 1);
            try { URL.revokeObjectURL(previewUrl); } catch (_) {}
            renderImagePreviews();
            showAuthOverlay('登录已过期');
            return;
          }
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const i = state.uploadedImages.indexOf(placeholder);
            if (i >= 0) state.uploadedImages.splice(i, 1);
            try { URL.revokeObjectURL(previewUrl); } catch (_) {}
            renderImagePreviews();
            showToast(err.detail || '上传失败', true);
            continue;
          }
          const data = await res.json();
          const i = state.uploadedImages.indexOf(placeholder);
          if (i >= 0) {
            state.uploadedImages[i] = data;
          }
          try { URL.revokeObjectURL(previewUrl); } catch (_) {}
          renderImagePreviews();
        } catch (_) {
          const i = state.uploadedImages.indexOf(placeholder);
          if (i >= 0) state.uploadedImages.splice(i, 1);
          try { URL.revokeObjectURL(previewUrl); } catch (_) {}
          renderImagePreviews();
          showToast('图片上传失败', true);
        }
      }
    }

    els.btnUpload.addEventListener('click', () => els.imageInput.click());

    els.imageInput.addEventListener('change', async () => {
      const files = Array.from(els.imageInput.files || []);
      els.imageInput.value = '';
      await uploadImageFiles(files);
    });

    // Direct image paste in composer (iOS/macOS clipboard screenshots & copied photos)
    els.promptInput.addEventListener('paste', (e) => {
      const cd = e.clipboardData;
      if (!cd) return;
      const pastedImages = [];
      if (cd.items && cd.items.length) {
        for (const item of cd.items) {
          if (item.kind === 'file' && item.type && item.type.startsWith('image/')) {
            const f = item.getAsFile();
            if (f) pastedImages.push(f);
          }
        }
      }
      if (!pastedImages.length && cd.files && cd.files.length) {
        for (const f of cd.files) {
          if (f.type && f.type.startsWith('image/')) pastedImages.push(f);
        }
      }
      if (pastedImages.length) {
        const hasText = Boolean(cd.getData('text/plain'));
        if (!hasText) e.preventDefault();
        uploadImageFiles(pastedImages);
      }
    });

    // Drag & drop images onto composer or chat window
    const composerCard = $('composer-card');
    let dragDepth = 0;
    function setDropHighlight(active) {
      if (!composerCard) return;
      composerCard.classList.toggle('border-blue-400', active);
      composerCard.classList.toggle('ring-2', active);
      composerCard.classList.toggle('ring-blue-500/35', active);
    }
    document.addEventListener('dragenter', (e) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
        dragDepth += 1;
        setDropHighlight(true);
      }
    });
    document.addEventListener('dragover', (e) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
        e.preventDefault();
      }
    });
    document.addEventListener('dragleave', () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) setDropHighlight(false);
    });
    document.addEventListener('drop', (e) => {
      dragDepth = 0;
      setDropHighlight(false);
      const files = e.dataTransfer?.files;
      if (files && files.length) {
        const imgs = Array.from(files).filter((f) => f.type && f.type.startsWith('image/'));
        if (imgs.length) {
          e.preventDefault();
          uploadImageFiles(imgs);
        }
      }
    });

    function autoResizeInput() {
      const ta = els.promptInput;
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 144) + 'px';
      if (typeof updateComposerActionButton === 'function') updateComposerActionButton();
    }

    // =========================================================================
    // Slash Commands Palette & Local Instant Commands (/usage, /model, /diff...)
    // =========================================================================
    const LOCAL_SLASH_COMMANDS = [
      { name: 'btw', description: '临时旁白提问（不打断主任务、不入主上下文，看完可关闭）', kind: 'local', instant: false },
      { name: 'usage', description: '查看当前会话上下文用量、模型、模式与目录信息', kind: 'local', instant: true },
      { name: 'quota', description: '查看账号各模型分组的剩余配额与重置时间', kind: 'local', instant: true },
      { name: 'model', description: '打开两级模型与思考档位选择器', kind: 'local', instant: true },
      { name: 'diff', description: '查看当前工作目录的 Git Diff 代码改动', kind: 'local', instant: true },
      { name: 'new', description: '选择目录创建新会话（支持 /new <路径>）', kind: 'local', instant: true },
      { name: 'clear', description: '在当前目录立即开启一个全新的空白会话', kind: 'local', instant: true },
      { name: 'mode', description: '切换当前 Agent 的执行模式', kind: 'local', instant: false },
      { name: 'help', description: '列出当前 Agent 可用的所有指令', kind: 'local', instant: true },
    ];

    const FALLBACK_ACP_COMMANDS = [
      { name: 'compact', description: '压缩当前对话历史，释放上下文窗口', kind: 'acp', instant: true },
      { name: 'plan', description: '先制定详细执行计划再动手修改', kind: 'acp', instant: false },
      { name: 'goal', description: '持续自主推进直到目标完全达成', kind: 'acp', instant: false },
      { name: 'grill-me', description: '通过互动问答帮我梳理设计细节与方案', kind: 'acp', instant: false },
      { name: 'schedule', description: '设置定时任务或周期性提醒指令', kind: 'acp', instant: false },
      { name: 'browser', description: '调用浏览器代理执行网页交互与搜索', kind: 'acp', instant: false },
      { name: 'teamwork', description: '启动多智能体协作完成复杂项目', kind: 'acp', instant: false },
      { name: 'learn', description: '总结本次经验并沉淀为可复用规则或技能', kind: 'acp', instant: false },
      { name: 'boost', description: '调用 Boost 多视角深度思考与验证编排', kind: 'acp', instant: false },
    ];

    const FALLBACK_CODEX_COMMANDS = [
      { name: 'compact', description: '压缩当前 Codex 对话历史，释放上下文窗口', kind: 'codex', instant: true },
      { name: 'review', description: '审查代码改动（默认未提交改动；支持 /review branch <分支> / commit <sha> / <要求>）', kind: 'codex', instant: false },
      { name: 'fork', description: '从当前 Codex 会话分叉出一个新会话继续探索', kind: 'codex', instant: true },
    ];

    function getProfileBackend(name) {
      const target = String(name || '').trim();
      if (!target) return 'agy';
      const found = (state.profiles || []).find((p) => p && p.name === target);
      if (found && found.backend) return found.backend;
      return target === 'codex' ? 'codex' : 'agy';
    }

    function getActiveBackend() {
      if (state.currentBackend) return state.currentBackend;
      return getProfileBackend(state.currentProfile);
    }

    function getAllSlashCommands() {
      const backend = getActiveBackend();
      const result = [...LOCAL_SLASH_COMMANDS];
      const seen = new Set(result.map((c) => c.name));
      const defaultKind = backend === 'codex' ? 'codex' : 'acp';
      const fallbackList = backend === 'codex' ? FALLBACK_CODEX_COMMANDS : FALLBACK_ACP_COMMANDS;
      const agentList =
        Array.isArray(state.availableCommands) && state.availableCommands.length
          ? state.availableCommands
          : fallbackList;

      agentList.forEach((c) => {
        const name = String(c?.name || '').trim().replace(/^\/+/, '');
        if (!name || seen.has(name)) return;
        seen.add(name);
        result.push({
          name,
          description: String(c?.description || '').trim(),
          kind: String(c?.kind || defaultKind),
          instant: typeof c?.instant === 'boolean' ? c.instant : (name === 'compact' || name === 'fork'),
        });
      });
      return result;
    }

    function appendLocalInfoCard(title, bodyHtml, options = {}) {
      els.emptyState.classList.add('hidden');
      const card = document.createElement('div');
      card.dataset.itemId = `sys_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
      card.className = 'rounded-2xl border border-blue-500/35 bg-surface/95 px-4 py-3 shadow-sm space-y-2 selectable-text';
      card.innerHTML = `
        <div class="flex items-center justify-between gap-2 border-b border-border/70 pb-1.5">
          <span class="text-xs font-semibold text-blue-300 flex items-center gap-1.5 min-w-0">
            <span class="local-card-dot w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0"></span>
            <span class="truncate">${escapeHtml(title)}</span>
          </span>
          <span class="local-card-status text-[10px] text-slate-500 shrink-0"></span>
        </div>
        <div class="local-card-body text-xs text-slate-200 space-y-2"></div>
      `;
      const dotEl = card.querySelector('.local-card-dot');
      const statusEl = card.querySelector('.local-card-status');
      const bodyEl = card.querySelector('.local-card-body');

      const applyState = (html, opts = {}) => {
        const isLoading = Boolean(opts.loading);
        const isError = Boolean(opts.error);
        const statusText =
          typeof opts.statusText === 'string'
            ? opts.statusText
            : isLoading
              ? '加载中...'
              : isError
                ? '获取失败'
                : '本地即时命令';
        dotEl.className = `local-card-dot w-1.5 h-1.5 rounded-full shrink-0 ${
          isError ? 'bg-rose-400' : 'bg-blue-400'
        }${isLoading ? ' animate-pulse' : ''}`;
        statusEl.className = `local-card-status text-[10px] shrink-0 ${
          isError ? 'text-rose-300' : isLoading ? 'text-blue-300/90' : 'text-slate-500'
        }`;
        statusEl.textContent = statusText;
        bodyEl.innerHTML = html;
      };

      applyState(bodyHtml, options);
      els.chatList.appendChild(card);
      state.autoScroll = true;
      scrollToBottom(true);

      return {
        card,
        bodyEl,
        dotEl,
        statusEl,
        setContent(nextHtml, nextOpts = {}) {
          applyState(nextHtml, nextOpts);
          if (card.isConnected) {
            scrollToBottom(false);
          }
        },
      };
    }

    const btwCards = new Map();

    function createBtwCard(btwId, question) {
      els.emptyState.classList.add('hidden');
      const card = document.createElement('div');
      card.dataset.btwId = btwId;
      card.className = 'rounded-2xl border border-amber-500/35 bg-surface/95 px-4 py-3 shadow-sm space-y-2 selectable-text';
      card.innerHTML = `
        <div class="flex items-center justify-between gap-2 border-b border-border/70 pb-1.5">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="btw-dot w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse shrink-0"></span>
            <span class="text-xs font-semibold text-amber-300 shrink-0">/btw 旁白</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/30 text-amber-200/90 shrink-0">不入上下文</span>
            <span class="btw-status text-[10px] text-slate-400 truncate">思考中...</span>
          </div>
          <button type="button" class="btw-close p-1 -mr-1 rounded-lg text-slate-400 hover:text-white hover:bg-card active:scale-95 transition shrink-0" title="关闭旁白卡片">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="text-xs text-slate-300 bg-bg/70 border border-border/60 rounded-xl px-2.5 py-1.5 whitespace-pre-wrap break-words">${escapeHtml(question)}</div>
        <div class="btw-body md-body text-[13px] text-slate-100 leading-relaxed min-h-[1.25rem]">
          <span class="text-xs text-slate-500">正在基于当前会话上下文回答...</span>
        </div>
      `;
      const entry = {
        card,
        dotEl: card.querySelector('.btw-dot'),
        statusEl: card.querySelector('.btw-status'),
        bodyEl: card.querySelector('.btw-body'),
        text: '',
        done: false,
      };
      card.querySelector('.btw-close').addEventListener('click', () => {
        if (!entry.done) {
          sendWs({ type: 'btw_cancel', btw_id: btwId });
        }
        btwCards.delete(btwId);
        card.remove();
      });
      btwCards.set(btwId, entry);
      els.chatList.appendChild(card);
      state.autoScroll = true;
      scrollToBottom(true);
    }

    function updateBtwCardDelta(btwId, delta) {
      if (!btwId || !delta) return;
      const entry = btwCards.get(btwId);
      if (!entry || !entry.card.isConnected) {
        btwCards.delete(btwId);
        return;
      }
      entry.text += delta;
      entry.statusEl.textContent = '回答中...';
      renderMarkdownInto(entry.bodyEl, entry.text);
      scrollToBottom(false);
    }

    function finishBtwCard(btwId, finalText, errorMsg) {
      if (!btwId) return;
      const entry = btwCards.get(btwId);
      if (!entry || !entry.card.isConnected) {
        btwCards.delete(btwId);
        return;
      }
      entry.done = true;
      entry.dotEl.classList.remove('animate-pulse');
      if (errorMsg) {
        entry.dotEl.classList.remove('bg-amber-400');
        entry.dotEl.classList.add('bg-rose-400');
        entry.statusEl.textContent = '回答失败';
        entry.statusEl.className = 'btw-status text-[10px] text-rose-300 truncate';
        entry.bodyEl.innerHTML = `<div class="text-xs text-rose-300">${escapeHtml(errorMsg)}</div>`;
      } else {
        const text = finalText || entry.text;
        entry.dotEl.classList.remove('bg-amber-400');
        entry.dotEl.classList.add('bg-emerald-400');
        entry.statusEl.textContent = '已完成 · 可随时关闭';
        if (text) {
          renderMarkdownInto(entry.bodyEl, text);
        } else {
          entry.bodyEl.innerHTML = '<div class="text-xs text-slate-400">(无回复内容)</div>';
        }
      }
      scrollToBottom(false);
    }

    async function showQuotaCard(refresh = true) {
      const loadingHtml = `
        <div class="flex items-center gap-2 text-slate-300">
          <span class="inline-block w-3.5 h-3.5 rounded-full border-2 border-blue-400/30 border-t-blue-400 animate-spin shrink-0"></span>
          <span>正在获取账号模型配额数据...</span>
        </div>
        <div class="space-y-1.5 pt-0.5 animate-pulse">
          <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-2 space-y-1.5">
            <div class="flex items-center justify-between gap-2">
              <div class="h-2.5 w-24 rounded bg-slate-700/70"></div>
              <div class="h-2.5 w-20 rounded bg-slate-700/50"></div>
            </div>
            <div class="w-full h-1.5 rounded-full bg-slate-700/60"></div>
            <div class="h-2 w-40 rounded bg-slate-700/40"></div>
          </div>
          <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-2 space-y-1.5">
            <div class="flex items-center justify-between gap-2">
              <div class="h-2.5 w-28 rounded bg-slate-700/70"></div>
              <div class="h-2.5 w-16 rounded bg-slate-700/50"></div>
            </div>
            <div class="w-full h-1.5 rounded-full bg-slate-700/60"></div>
          </div>
        </div>
      `;
      const cardCtrl = appendLocalInfoCard('/quota · 账号模型余量', loadingHtml, {
        loading: true,
        statusText: '加载中...',
      });
      try {
        const qs = new URLSearchParams();
        if (refresh) qs.set('refresh', '1');
        if (state.currentProfile) qs.set('profile', state.currentProfile);
        const url = qs.toString() ? `/api/quota?${qs.toString()}` : '/api/quota';
        const res = await fetch(url, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        const groups = Array.isArray(data.groups) ? data.groups : [];
        const fmtReset = (iso) => {
          if (!iso) return '';
          const t = new Date(iso);
          if (isNaN(t.getTime())) return '';
          const mins = Math.max(0, Math.round((t.getTime() - Date.now()) / 60000));
          let rel;
          if (mins >= 1440) {
            const days = Math.floor(mins / 1440);
            const hrs = Math.floor((mins % 1440) / 60);
            rel = hrs > 0 ? `${days}天${hrs}小时` : `${days}天`;
          } else if (mins >= 60) {
            rel = `${Math.floor(mins / 60)}小时${mins % 60}分`;
          } else {
            rel = `${mins}分钟`;
          }
          const timeStr = mins >= 1440
            ? `${t.getMonth() + 1}/${t.getDate()} ${t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
            : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          return `${timeStr} 重置（${rel}后）`;
        };
        const barOf = (frac) => {
          const pct = Math.round((Number(frac) || 0) * 100);
          const bar = pct <= 15 ? 'bg-rose-500' : pct <= 40 ? 'bg-amber-400' : 'bg-emerald-500';
          return { pct, html: `<div class="w-full h-1.5 rounded-full bg-surface overflow-hidden"><div class="h-full ${bar} rounded-full" style="width:${pct}%"></div></div>` };
        };
        const winLabel = (w) => (w === '5h' ? '5 小时限额' : w === 'weekly' ? '每周限额' : w);
        const renderAccountBody = (acc) => {
          if (acc.error) {
            return `<div class="text-rose-300 text-[11px]">${escapeHtml(acc.error)}</div>`;
          }
          const accSummary = Array.isArray(acc.summary) ? acc.summary : [];
          const accGroups = Array.isArray(acc.groups) ? acc.groups : [];
          const summaryRows = accSummary
            .map((g) => {
              const bs = (g.buckets || [])
                .map((b) => {
                  const { pct, html } = barOf(b.remaining);
                  return `
                  <div class="space-y-0.5">
                    <div class="flex items-center justify-between gap-2">
                      <span class="text-slate-300">${escapeHtml(winLabel(b.window))} · <span class="font-mono text-slate-100">${pct}%</span></span>
                      <span class="text-[10px] text-slate-400">${escapeHtml(fmtReset(b.reset_time))}</span>
                    </div>${html}
                  </div>`;
                })
                .join('');
              return `
                <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-2 space-y-1.5">
                  <div class="text-slate-100 font-medium">${escapeHtml(g.name)}</div>
                  ${bs}
                  <div class="text-[10px] text-slate-400 leading-relaxed">${(g.members || []).map(escapeHtml).join(' · ')}</div>
                </div>`;
            })
            .join('');
          const rows = accGroups
            .map((g) => {
              const { pct, html: barHtml } = barOf(g.remaining);
              return `
                <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-2 space-y-1">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-slate-100 font-mono">剩余 ${pct}%</span>
                    <span class="text-[10px] text-slate-400">${escapeHtml(fmtReset(g.reset_time))}</span>
                  </div>
                  ${barHtml}
                  <div class="text-[10px] text-slate-400 leading-relaxed">${(g.models || []).map(escapeHtml).join(' · ')}</div>
                </div>`;
            })
            .join('');
          return summaryRows + rows || '<div class="text-slate-400">接口未返回配额数据</div>';
        };

        const accounts = Array.isArray(data.accounts) && data.accounts.length
          ? data.accounts
          : [{ profile: data.profile || state.currentProfile, email: state.currentProfileEmail, is_current: true, summary: data.summary, groups }];

        const cardsHtml = accounts
          .map((acc) => {
            const pname = acc.profile || 'default';
            const titleText = acc.email ? `${pname} · ${acc.email}` : pname;
            const actionHtml = acc.is_current
              ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">当前使用</span>'
              : `<button type="button" data-switch-profile="${escapeHtml(pname)}" class="text-[10px] px-2 py-0.5 rounded bg-blue-600/80 hover:bg-blue-500 text-white active:scale-95 transition">切到此号</button>`;
            return `
              <div class="rounded-xl border ${acc.is_current ? 'border-blue-500/40' : 'border-border/70'} bg-surface/60 p-2 space-y-1.5">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-[11px] font-medium text-slate-200 truncate">${escapeHtml(titleText)}</span>
                  ${actionHtml}
                </div>
                ${renderAccountBody(acc)}
              </div>`;
          })
          .join('');
        cardCtrl.setContent(`<div class="space-y-2">${cardsHtml}</div>`, { loading: false });
      } catch (err) {
        cardCtrl.setContent(`<div class="text-rose-300">${escapeHtml(err.message || String(err))}</div>`, {
          loading: false,
          error: true,
        });
      }
    }

    function runLocalSlashCommand(rawText) {
      const m = rawText.match(/^\/([a-zA-Z0-9_-]+)(?:\s+([\s\S]*))?$/);
      if (!m) return false;
      const cmd = m[1].toLowerCase();
      const arg = (m[2] || '').trim();

      if (cmd === 'btw') {
        if (!arg) {
          showToast('请输入旁白问题，例如：/btw 这个函数在哪被调用？');
          setTimeout(() => {
            els.promptInput.value = '/btw ';
            autoResizeInput();
            els.promptInput.focus();
          }, 0);
          return true;
        }
        const btwId = `btw_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
        createBtwCard(btwId, arg);
        if (!sendWs({ type: 'btw_prompt', btw_id: btwId, question: arg })) {
          finishBtwCard(btwId, '', 'WebSocket 未连接，请稍后重试');
        }
        return true;
      }

      if (cmd === 'quota') {
        showQuotaCard(true);
        return true;
      }

      if (cmd === 'usage') {
        const u = state.usage || { used: 0, size: 0 };
        const hasSize = u.size > 0;
        const pct = hasSize ? Math.min(100, Math.round((u.used / u.size) * 100)) : 0;
        const usageText = hasSize
          ? `${(u.used / 1000).toFixed(1)}k / ${(u.size / 1000).toFixed(0)}k tokens (${pct}%)`
          : '暂无用量数据（发送消息后自动更新）';
        const barColor = pct >= 85 ? 'bg-rose-500' : pct >= 60 ? 'bg-amber-400' : 'bg-blue-500';
        const modelLabel = getModelLabel(state.currentModel) || state.currentModel || '未获取';
        const modeVal = els.modeSelect.value || 'default';
        const msgCount = state.items.length;

        appendLocalInfoCard(
          '/usage · 会话用量与状态',
          `
            <div class="space-y-1">
              <div class="flex items-center justify-between gap-2">
                <span class="text-slate-400">上下文窗口</span>
                <span class="font-mono text-slate-100">${escapeHtml(usageText)}</span>
              </div>
              ${hasSize ? `<div class="w-full h-1.5 rounded-full bg-bg overflow-hidden border border-border/60"><div class="h-full ${barColor} rounded-full" style="width:${pct}%"></div></div>` : ''}
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5 pt-1 text-[11px]">
              <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-1.5">
                <div class="text-slate-400">当前模型</div>
                <div class="text-slate-100 font-medium truncate mt-0.5">${escapeHtml(modelLabel)}</div>
              </div>
              <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-1.5">
                <div class="text-slate-400">执行模式</div>
                <div class="text-slate-100 font-mono truncate mt-0.5">${escapeHtml(modeVal)}</div>
              </div>
              <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-1.5 sm:col-span-2">
                <div class="text-slate-400">工作目录</div>
                <div class="text-emerald-300 font-mono truncate mt-0.5">${escapeHtml(state.currentWorkdir || '-')}</div>
              </div>
              <div class="rounded-lg bg-bg border border-border/60 px-2.5 py-1.5 sm:col-span-2 flex items-center justify-between gap-2">
                <span class="text-slate-400 shrink-0">会话 ID</span>
                <span class="text-slate-300 font-mono truncate">${escapeHtml(state.currentSessionId || '-')} · ${msgCount} 条记录</span>
              </div>
            </div>
          `
        );
        return true;
      }

      if (cmd === 'model') {
        openModelModal();
        return true;
      }

      if (cmd === 'diff') {
        els.diffModal.classList.remove('hidden');
        loadDiff();
        return true;
      }

      if (cmd === 'new') {
        if (arg) {
          requestNewSession(arg);
        } else {
          openDirModal(state.currentWorkdir);
        }
        return true;
      }

      if (cmd === 'clear') {
        requestNewSession(state.currentWorkdir);
        return true;
      }

      if (cmd === 'mode') {
        const opts = Array.from(els.modeSelect.options).map((o) => o.value);
        if (arg && opts.includes(arg)) {
          sendWs({ type: 'set_mode', mode_id: arg });
          showToast(`已切换执行模式: ${arg}`);
        } else {
          appendLocalInfoCard(
            '/mode · 可用执行模式',
            `<div class="text-slate-300">当前模式：<span class="font-mono text-blue-300">${escapeHtml(els.modeSelect.value || 'default')}</span></div>
             <div class="text-slate-400">可用参数：${opts.map((v) => `<code class="px-1.5 py-0.5 rounded bg-bg border border-border text-slate-200">/mode ${escapeHtml(v)}</code>`).join(' ')}</div>`
          );
        }
        return true;
      }

      if (cmd === 'help') {
        const all = getAllSlashCommands();
        const engineLabel = getActiveBackend() === 'codex' ? 'Codex' : 'Antigravity';
        const rows = all
          .map(
            (c) => `
            <div class="flex items-start gap-2 py-1 border-b border-border/40 last:border-0">
              <span class="font-mono text-blue-300 shrink-0 min-w-[92px]">/${escapeHtml(c.name)}</span>
              <span class="text-slate-300 leading-relaxed">${escapeHtml(c.description || '')}</span>
            </div>`
          )
          .join('');
        appendLocalInfoCard(`/help · ${engineLabel} 可用指令`, `<div>${rows}</div>`);
        return true;
      }

      return false;
    }

    function closeSlashPalette() {
      state.slashOpen = false;
      els.slashPalette.classList.add('hidden');
    }

    function applySlashCommand(cmd) {
      closeSlashPalette();
      if (!cmd) return;
      if (cmd.kind === 'local' && cmd.instant) {
        els.promptInput.value = '';
        autoResizeInput();
        runLocalSlashCommand(`/${cmd.name}`);
        return;
      }
      if (cmd.kind !== 'local' && cmd.instant) {
        els.promptInput.value = `/${cmd.name}`;
        autoResizeInput();
        submitPrompt();
        return;
      }
      els.promptInput.value = `/${cmd.name} `;
      autoResizeInput();
      els.promptInput.focus();
    }

    function renderSlashPalette(query = '') {
      const q = String(query || '').trim().toLowerCase();
      const all = getAllSlashCommands();
      const filtered = q
        ? all.filter(
            (c) =>
              c.name.toLowerCase().includes(q) ||
              String(c.description || '').toLowerCase().includes(q)
          )
        : all;

      state.slashItems = filtered;
      state.slashActiveIdx = Math.min(state.slashActiveIdx, Math.max(0, filtered.length - 1));
      els.slashCount.textContent = `${filtered.length} 条`;

      if (!filtered.length) {
        closeSlashPalette();
        return;
      }

      els.slashList.innerHTML = '';
      filtered.forEach((cmd, idx) => {
        const isActive = idx === state.slashActiveIdx;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `w-full text-left px-2.5 py-2 rounded-xl flex items-center justify-between gap-2 transition active:scale-[0.99] ${
          isActive ? 'bg-blue-600/25 border border-blue-500/45' : 'border border-transparent hover:bg-card'
        }`;
        let badge;
        if (cmd.kind === 'local') {
          badge = '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 shrink-0">快捷</span>';
        } else if (cmd.kind === 'codex') {
          badge = '<span class="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/15 border border-cyan-500/30 text-cyan-300 shrink-0">Codex</span>';
        } else if (cmd.kind === 'skill') {
          badge = '<span class="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 border border-purple-500/30 text-purple-300 shrink-0">Skill</span>';
        } else {
          badge = '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 border border-blue-500/30 text-blue-300 shrink-0">Agy</span>';
        }
        btn.innerHTML = `
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5">
              <span class="font-mono text-xs font-semibold text-blue-300">/${escapeHtml(cmd.name)}</span>
              ${badge}
            </div>
            <div class="text-[11px] text-slate-400 truncate mt-0.5">${escapeHtml(cmd.description || '')}</div>
          </div>
        `;
        btn.addEventListener('mousedown', (e) => e.preventDefault());
        btn.addEventListener('click', () => applySlashCommand(cmd));
        els.slashList.appendChild(btn);
      });

      state.slashOpen = true;
      els.slashPalette.classList.remove('hidden');
      const activeEl = els.slashList.children[state.slashActiveIdx];
      if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
    }

    function syncSlashPaletteFromInput() {
      const val = els.promptInput.value;
      const m = val.match(/^\/([^\s]*)$/);
      if (m) {
        state.slashActiveIdx = 0;
        renderSlashPalette(m[1]);
      } else if (state.slashOpen) {
        closeSlashPalette();
      }
    }

    const SEND_ICON_SVG =
      '<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18"/></svg>';
    const STOP_ICON_SVG =
      '<span class="w-3 h-3 rounded-[3px] bg-rose-300 block"></span>';

    function updateComposerActionButton() {
      const hasDraft =
        els.promptInput.value.trim().length > 0 ||
        state.uploadedImages.length > 0 ||
        Boolean(state.quotedReply && state.quotedReply.excerpt);
      const showStop = state.isBusy && !hasDraft;
      if (showStop) {
        els.btnSend.dataset.action = 'stop';
        els.btnSend.title = '中断当前任务';
        els.btnSend.className =
          'w-8 h-8 rounded-xl border border-rose-500/50 bg-rose-500/20 hover:bg-rose-500/30 active:scale-95 flex items-center justify-center text-rose-200 transition';
        els.btnSend.innerHTML = STOP_ICON_SVG;
      } else {
        els.btnSend.dataset.action = 'send';
        els.btnSend.title = state.isBusy ? '加入待发送队列' : '发送消息';
        els.btnSend.className =
          'w-8 h-8 rounded-xl bg-blue-600 hover:bg-blue-500 active:scale-95 flex items-center justify-center text-white disabled:opacity-40 transition';
        els.btnSend.innerHTML = SEND_ICON_SVG;
      }
    }

    els.promptInput.addEventListener('input', () => {
      autoResizeInput();
      syncSlashPaletteFromInput();
    });

    document.addEventListener('click', (e) => {
      if (state.slashOpen && !els.slashPalette.contains(e.target) && e.target !== els.promptInput) {
        closeSlashPalette();
      }
    });

    function submitPrompt() {
      closeSlashPalette();
      const rawText = els.promptInput.value.trim();
      const quoteExcerpt = state.quotedReply && state.quotedReply.excerpt ? state.quotedReply.excerpt.trim() : '';
      if (state.uploadedImages.some((im) => im.uploading)) {
        showToast('图片正在上传，请稍候...');
        return;
      }
      const imgs = state.uploadedImages.filter((im) => im && im.url);
      if (!rawText && !imgs.length && !quoteExcerpt) return;

      // Handle built-in instant slash commands locally (zero token cost)
      if (!imgs.length && !quoteExcerpt && rawText.startsWith('/') && runLocalSlashCommand(rawText)) {
        els.promptInput.value = '';
        autoResizeInput();
        return;
      }

      const finalText = quoteExcerpt
        ? (rawText ? `> ${quoteExcerpt}\n\n${rawText}` : `> ${quoteExcerpt}`)
        : rawText;

      if (
        sendWs({
          type: 'prompt',
          text: finalText,
          display_text: finalText,
          images: imgs,
        })
      ) {
        els.promptInput.value = '';
        autoResizeInput();
        state.uploadedImages = [];
        renderImagePreviews();
        clearQuotedReply();
        state.autoScroll = true;
        scrollToBottom(true);
      }
    }

    els.btnQuoteClear.addEventListener('click', () => {
      clearQuotedReply();
      els.promptInput.focus();
    });
    els.quotePreviewJump.addEventListener('click', () => {
      if (state.quotedReply && state.quotedReply.itemId) {
        flashMessageNode(state.quotedReply.itemId);
      }
    });

    els.btnSend.addEventListener('click', () => {
      if (els.btnSend.dataset.action === 'stop') {
        sendWs({ type: 'cancel' });
        showToast('已发送中断请求');
      } else {
        submitPrompt();
      }
    });
    els.promptInput.addEventListener('keydown', (e) => {
      if (state.slashOpen && state.slashItems.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          state.slashActiveIdx = (state.slashActiveIdx + 1) % state.slashItems.length;
          renderSlashPalette((els.promptInput.value.match(/^\/([^\s]*)$/) || [])[1] || '');
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          state.slashActiveIdx = (state.slashActiveIdx - 1 + state.slashItems.length) % state.slashItems.length;
          renderSlashPalette((els.promptInput.value.match(/^\/([^\s]*)$/) || [])[1] || '');
          return;
        }
        if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
          e.preventDefault();
          applySlashCommand(state.slashItems[state.slashActiveIdx]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          closeSlashPalette();
          return;
        }
      }
      if (e.key === 'Escape' && state.quotedReply) {
        e.preventDefault();
        clearQuotedReply();
        return;
      }
      if (e.key === 'Enter' && e.shiftKey && !e.isComposing) {
        e.preventDefault();
        submitPrompt();
      }
    });

