    // Keep #app pinned to the WebView bottom when idle, and track visualViewport when soft keyboard opens
    const appEl = $('app');
    function syncAppViewportHeight() {
      const vv = window.visualViewport;
      if (vv && vv.height < window.innerHeight - 100) {
        const kh = `${Math.round(vv.height)}px`;
        document.documentElement.style.setProperty('--app-height', kh);
        appEl.style.bottom = 'auto';
        if (vv.offsetTop) window.scrollTo(0, 0);
      } else {
        document.documentElement.style.removeProperty('--app-height');
        appEl.style.bottom = '0';
      }
    }
    syncAppViewportHeight();
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', syncAppViewportHeight);
      window.visualViewport.addEventListener('scroll', syncAppViewportHeight);
    }
    window.addEventListener('resize', syncAppViewportHeight);
    window.addEventListener('orientationchange', () => setTimeout(syncAppViewportHeight, 120));

    // Boot
    checkAuthAndBoot();
