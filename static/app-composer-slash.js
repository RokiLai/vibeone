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

