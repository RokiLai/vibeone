    // =========================================================================
    // Mobile Sidebar Drawer Controls & Edge/Swipe Gestures
    // =========================================================================
    const desktopSidebarMql = window.matchMedia('(min-width: 1024px)');
    let _sidebarBackdropHideTimer = null;

    function isDesktopSidebarLayout() {
      return desktopSidebarMql.matches;
    }

    function isSidebarOpen() {
      return !els.sidebar.classList.contains('-translate-x-full');
    }

    function clearSidebarInlineStyles() {
      els.sidebar.style.transition = '';
      els.sidebar.style.transform = '';
      els.sidebarBackdrop.style.transition = '';
      els.sidebarBackdrop.style.opacity = '';
    }

    function openSidebar() {
      clearTimeout(_sidebarBackdropHideTimer);
      _sidebarBackdropHideTimer = null;
      clearSidebarInlineStyles();
      els.sidebar.classList.remove('-translate-x-full');
      els.sidebarBackdrop.classList.remove('hidden');
      loadSidebarSessions();
    }

    function closeSidebar() {
      if (els.sidebarSearch && document.activeElement === els.sidebarSearch) {
        els.sidebarSearch.blur();
      }
      clearTimeout(_sidebarBackdropHideTimer);
      _sidebarBackdropHideTimer = null;
      clearSidebarInlineStyles();
      els.sidebar.classList.add('-translate-x-full');
      els.sidebarBackdrop.classList.add('hidden');
    }

    function finishSidebarGesture(shouldOpen) {
      clearTimeout(_sidebarBackdropHideTimer);
      _sidebarBackdropHideTimer = null;
      els.sidebar.style.transition = '';
      els.sidebarBackdrop.style.transition = 'opacity 200ms ease-out';
      // Force reflow so WebKit/Safari animates smoothly from the dragged offset
      void els.sidebar.offsetWidth;
      els.sidebar.style.transform = '';
      if (shouldOpen) {
        els.sidebar.classList.remove('-translate-x-full');
        els.sidebarBackdrop.classList.remove('hidden');
        els.sidebarBackdrop.style.opacity = '1';
        _sidebarBackdropHideTimer = setTimeout(() => {
          _sidebarBackdropHideTimer = null;
          els.sidebarBackdrop.style.transition = '';
          els.sidebarBackdrop.style.opacity = '';
        }, 210);
        loadSidebarSessions();
      } else {
        els.sidebar.classList.add('-translate-x-full');
        els.sidebarBackdrop.style.opacity = '0';
        _sidebarBackdropHideTimer = setTimeout(() => {
          _sidebarBackdropHideTimer = null;
          els.sidebarBackdrop.classList.add('hidden');
          els.sidebarBackdrop.style.transition = '';
          els.sidebarBackdrop.style.opacity = '';
        }, 210);
      }
    }

    function hasBlockingOverlayForSidebarGesture() {
      const overlays = [
        els.offlineOverlay,
        els.authOverlay,
        els.dirModal,
        els.modelModal,
        els.diffModal,
        els.filePreviewModal,
        els.settingsModal,
        els.imageModal,
      ];
      return overlays.some((el) => el && !el.classList.contains('hidden'));
    }

    function isTouchBlockedForSidebarSwipe(target, startX, wasOpen) {
      if (!target || !(target instanceof Element)) return false;
      if (target.closest('input, textarea, select, [contenteditable="true"]')) return true;
      const sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed && String(sel).trim()) return true;
      if (!wasOpen && startX > 28) {
        let cur = target;
        while (cur && cur !== document.body && cur !== document.documentElement) {
          if (cur.scrollWidth > cur.clientWidth + 2) {
            const overflowX = window.getComputedStyle(cur).overflowX;
            if (overflowX === 'auto' || overflowX === 'scroll') {
              return true;
            }
          }
          cur = cur.parentElement;
        }
      }
      return false;
    }

    (() => {
      const g = {
        phase: 'none', // 'none' | 'pending' | 'dragging' | 'cancel'
        wasOpen: false,
        startX: 0,
        startY: 0,
        lockDx: 0,
        lastX: 0,
        lastTime: 0,
        vx: 0,
        sidebarWidth: 280,
        progress: 0,
      };

      const resetGesture = () => {
        g.phase = 'none';
        g.vx = 0;
        g.progress = 0;
      };

      document.addEventListener('touchstart', (e) => {
        resetGesture();
        if (isDesktopSidebarLayout() || hasBlockingOverlayForSidebarGesture()) return;
        if (e.touches.length !== 1) return;
        const t = e.touches[0];
        const wasOpen = isSidebarOpen();
        if (isTouchBlockedForSidebarSwipe(e.target, t.clientX, wasOpen)) return;
        g.phase = 'pending';
        g.wasOpen = wasOpen;
        g.startX = t.clientX;
        g.startY = t.clientY;
        g.lastX = t.clientX;
        g.lastTime = Date.now();
        g.vx = 0;
        g.progress = wasOpen ? 1 : 0;
      }, { passive: true });

      document.addEventListener('touchmove', (e) => {
        if (g.phase === 'none' || g.phase === 'cancel') return;
        if (e.touches.length !== 1 || isDesktopSidebarLayout()) {
          if (g.phase === 'dragging') finishSidebarGesture(g.wasOpen);
          resetGesture();
          return;
        }
        const t = e.touches[0];
        const dx = t.clientX - g.startX;
        const dy = t.clientY - g.startY;

        if (g.phase === 'pending') {
          if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
          const isOpenSwipe = !g.wasOpen && dx > 0 && dx > Math.abs(dy) * 1.35;
          const isCloseSwipe = g.wasOpen && dx < 0 && Math.abs(dx) > Math.abs(dy) * 1.35;
          if (!isOpenSwipe && !isCloseSwipe) {
            g.phase = 'cancel';
            return;
          }
          g.phase = 'dragging';
          g.lockDx = dx;
          g.sidebarWidth = els.sidebar.getBoundingClientRect().width || 280;
          clearTimeout(_sidebarBackdropHideTimer);
          _sidebarBackdropHideTimer = null;
          if (!g.wasOpen && document.activeElement && typeof document.activeElement.blur === 'function') {
            const tag = document.activeElement.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') document.activeElement.blur();
          }
          els.sidebar.style.transition = 'none';
          els.sidebarBackdrop.style.transition = 'none';
          els.sidebarBackdrop.classList.remove('hidden');
        }

        if (g.phase === 'dragging') {
          if (e.cancelable) e.preventDefault();
          const now = Date.now();
          const dt = now - g.lastTime;
          if (dt > 0) {
            const instVx = (t.clientX - g.lastX) / dt;
            g.vx = g.vx * 0.4 + instVx * 0.6;
            g.lastX = t.clientX;
            g.lastTime = now;
          }
          const rawProgress = g.wasOpen
            ? 1 + (dx - g.lockDx) / g.sidebarWidth
            : (dx - g.lockDx) / g.sidebarWidth;
          g.progress = Math.max(0, Math.min(1, rawProgress));
          const translateX = -g.sidebarWidth * (1 - g.progress);
          els.sidebar.style.transform = `translate3d(${translateX.toFixed(1)}px, 0, 0)`;
          els.sidebarBackdrop.style.opacity = g.progress.toFixed(3);
        }
      }, { passive: false });

      const onTouchFinish = (e) => {
        if (g.phase !== 'dragging') {
          resetGesture();
          return;
        }
        const isCancel = e.type === 'touchcancel';
        const recentVx = (Date.now() - g.lastTime) <= 120 ? g.vx : 0;
        let shouldOpen = g.wasOpen;
        if (!isCancel) {
          if (!g.wasOpen) {
            shouldOpen = g.progress >= 0.32 || (recentVx > 0.35 && g.progress > 0.06);
          } else {
            shouldOpen = !(g.progress <= 0.68 || (recentVx < -0.35 && g.progress < 0.94));
          }
        }
        resetGesture();
        finishSidebarGesture(shouldOpen);
      };

      document.addEventListener('touchend', onTouchFinish, { passive: true });
      document.addEventListener('touchcancel', onTouchFinish, { passive: true });

      const onLayoutChange = () => {
        if (isDesktopSidebarLayout()) {
          resetGesture();
          clearTimeout(_sidebarBackdropHideTimer);
          _sidebarBackdropHideTimer = null;
          clearSidebarInlineStyles();
          els.sidebarBackdrop.classList.add('hidden');
        }
      };
      if (desktopSidebarMql.addEventListener) {
        desktopSidebarMql.addEventListener('change', onLayoutChange);
      } else if (desktopSidebarMql.addListener) {
        desktopSidebarMql.addListener(onLayoutChange);
      }
    })();

    function focusRunningTasks() {
      state.runningSectionCollapsed = false;
      if (els.sidebarSearch && (els.sidebarSearch.value || '').trim()) {
        els.sidebarSearch.value = '';
        resetFullTextSearchState();
      }
      openSidebar();
      renderSidebarSessions();
      requestAnimationFrame(() => {
        if (els.sidebarSessions) {
          els.sidebarSessions.scrollTo({ top: 0, behavior: 'smooth' });
        }
        const runningSec = $('sidebar-running-section');
        if (runningSec) {
          runningSec.classList.add('ring-2', 'ring-blue-400/70');
          setTimeout(() => {
            runningSec.classList.remove('ring-2', 'ring-blue-400/70');
          }, 900);
        }
      });
    }

    $('btn-sidebar-open').addEventListener('click', openSidebar);
    $('btn-sidebar-close').addEventListener('click', closeSidebar);
    els.sidebarBackdrop.addEventListener('click', closeSidebar);
    $('btn-empty-sidebar').addEventListener('click', openSidebar);
    els.bgTasksBadge.addEventListener('click', focusRunningTasks);

