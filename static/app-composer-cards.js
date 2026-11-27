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
      if (!entry.rafId) {
        const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (fn) => setTimeout(fn, 16);
        entry.rafId = schedule(() => {
          entry.rafId = null;
          if (!entry.card.isConnected || entry.done) return;
          renderMarkdownInto(entry.bodyEl, entry.text, { skipHighlight: true });
          scrollToBottom(false);
        });
      }
    }

    function finishBtwCard(btwId, finalText, errorMsg) {
      if (!btwId) return;
      const entry = btwCards.get(btwId);
      if (!entry || !entry.card.isConnected) {
        btwCards.delete(btwId);
        return;
      }
      entry.done = true;
      if (entry.rafId) {
        if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(entry.rafId);
        else clearTimeout(entry.rafId);
        entry.rafId = null;
      }
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
          renderMarkdownInto(entry.bodyEl, text, { deferHighlight: true });
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

