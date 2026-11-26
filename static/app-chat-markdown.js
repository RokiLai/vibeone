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

