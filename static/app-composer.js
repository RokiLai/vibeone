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


    let _lastComposerBtnMode = '';
    function updateComposerActionButton() {
      const hasDraft =
        els.promptInput.value.trim().length > 0 ||
        state.uploadedImages.length > 0 ||
        Boolean(state.quotedReply && state.quotedReply.excerpt);
      const showStop = state.isBusy && !hasDraft;
      const mode = showStop ? 'stop' : state.isBusy ? 'queue' : 'send';
      if (mode === _lastComposerBtnMode) return;
      _lastComposerBtnMode = mode;
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

