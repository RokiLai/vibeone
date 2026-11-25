    // =========================================================================
    // State & DOM Refs
    // =========================================================================
    const state = {
      ws: null,
      reconnectTimer: null,
      reconnectDelay: 1000,
      heartbeatTimer: null,
      pongTimer: null,
      connectTimer: null,
      lastWsMessageAt: 0,
      manualClose: false,
      items: [],
      itemsById: new Map(),
      totalItems: 0,
      itemsOffset: 0,
      hasMoreItems: false,
      loadingOlderItems: false,
      lastScrollTop: 0,
      suppressAutoLoadUntil: 0,
      lastOlderLoadAt: 0,
      pendingPermissions: new Map(),
      queuedPrompts: [],
      uploadedImages: [],
      quotedReply: null,
      isBusy: false,
      bgBusyCount: 0,
      bgPermCount: 0,
      currentProfile: '',
      currentProfileEmail: '',
      profiles: [],
      profilePickerOpen: false,
      currentSessionId: '',
      currentWorkdir: '',
      currentModel: '',
      availableModels: [],
      availableCommands: [],
      usage: { used: 0, size: 0 },
      slashOpen: false,
      slashItems: [],
      slashActiveIdx: 0,
      selectedModelGroupKey: null,
      sessionsCache: [],
      archivedSessionsCache: [],
      sidebarSearchQuery: '',
      sidebarSearchProfile: '',
      sidebarSearchLoading: false,
      sidebarSearchHits: new Map(),
      archivedSearchQuery: '',
      archivedSearchProfile: '',
      archivedSearchLoading: false,
      archivedSearchHits: new Map(),
      settingsView: 'main',
      batchArchiveDir: null,
      batchSelectedIds: new Set(),
      batchBusy: false,
      collapsedDirs: new Set(),
      runningSectionCollapsed: false,
      browsePath: '',
      browseParent: null,
      browseShowHidden: false,
      lightboxImages: [],
      lightboxIndex: 0,
      autoScroll: true,
      renderTimers: new Map(),
    };

    const $ = (id) => document.getElementById(id);
    const els = {
      offlineOverlay: $('offline-overlay'),
      offlineDetail: $('offline-detail'),
      btnOfflineRetry: $('btn-offline-retry'),
      authOverlay: $('auth-overlay'),
      authForm: $('auth-form'),
      authInput: $('auth-token-input'),
      authError: $('auth-error'),
      btnPasskeyLogin: $('btn-passkey-login'),
      btnPasskeyBind: $('btn-passkey-bind'),
      passkeyBindLabel: $('passkey-bind-label'),
      sidebar: $('sidebar'),
      sidebarBackdrop: $('sidebar-backdrop'),
      sidebarSearch: $('sidebar-search'),
      sidebarSessions: $('sidebar-sessions'),
      sidebarUsage: $('sidebar-usage'),
      btnSidebarSettings: $('btn-sidebar-settings'),
      settingsModal: $('settings-modal'),
      btnSettingsBack: $('btn-settings-back'),
      settingsModalTitle: $('settings-modal-title'),
      settingsModalSubtitle: $('settings-modal-subtitle'),
      settingsMainView: $('settings-main-view'),
      settingsArchivedView: $('settings-archived-view'),
      btnSettingsArchived: $('btn-settings-archived'),
      settingsArchivedCount: $('settings-archived-count'),
      archivedSearch: $('archived-search'),
      settingsArchivedList: $('settings-archived-list'),
      settingsPasskeySection: $('settings-passkey-section'),
      btnTokenRevealCopy: $('btn-token-reveal-copy'),
      settingsTokenValue: $('settings-token-value'),
      settingsTokenAction: $('settings-token-action'),
      btnTokenHide: $('btn-token-hide'),
      btnPushToggle: $('btn-push-toggle'),
      pushToggleLabel: $('push-toggle-label'),
      btnPushTest: $('btn-push-test'),
      connDot: $('conn-dot'),
      sessionTitle: $('session-title'),
      busyBadge: $('busy-badge'),
      bgTasksBadge: $('bg-tasks-badge'),
      headerDir: $('header-dir'),
      headerDirLabel: $('header-dir-label'),
      headerError: $('header-error'),
      chatScroll: $('chat-scroll'),
      chatList: $('chat-list'),
      emptyState: $('empty-state'),
      btnScrollBottom: $('btn-scroll-bottom'),
      permissionBar: $('permission-bar'),
      permissionList: $('permission-list'),
      queueBar: $('queue-bar'),
      queueList: $('queue-list'),
      queueCount: $('queue-count'),
      btnQueueRun: $('btn-queue-run'),
      btnQueueClear: $('btn-queue-clear'),
      quotePreviewBar: $('quote-preview-bar'),
      quotePreviewLabel: $('quote-preview-label'),
      quotePreviewText: $('quote-preview-text'),
      quotePreviewJump: $('quote-preview-jump'),
      btnQuoteClear: $('btn-quote-clear'),
      imagePreviewRow: $('image-preview-row'),
      imageInput: $('image-input'),
      promptInput: $('prompt-input'),
      btnUpload: $('btn-upload'),
      slashPalette: $('slash-palette'),
      slashList: $('slash-list'),
      slashCount: $('slash-count'),
      btnModel: $('btn-model'),
      btnModelLabel: $('btn-model-label'),
      modeSelect: $('mode-select'),
      btnSend: $('btn-send'),
      dirModal: $('dir-modal'),
      dirShortcuts: $('dir-shortcuts'),
      dirPathForm: $('dir-path-form'),
      dirPathInput: $('dir-path-input'),
      btnDirUp: $('btn-dir-up'),
      dirCurrentBadge: $('dir-current-badge'),
      btnDirHidden: $('btn-dir-hidden'),
      btnDirMkdir: $('btn-dir-mkdir'),
      dirList: $('dir-list'),
      dirCtaPath: $('dir-cta-path'),
      btnDirCreateSession: $('btn-dir-create-session'),
      modelModal: $('model-modal'),
      btnModelBack: $('btn-model-back'),
      modelModalTitle: $('model-modal-title'),
      modelCount: $('model-count'),
      modelSearchWrap: $('model-search-wrap'),
      modelSearch: $('model-search'),
      modelList: $('model-list'),
      diffModal: $('diff-modal'),
      diffBranch: $('diff-branch'),
      diffWorkdir: $('diff-workdir'),
      diffContent: $('diff-content'),
      filePreviewModal: $('file-preview-modal'),
      filePreviewName: $('file-preview-name'),
      filePreviewLang: $('file-preview-lang'),
      filePreviewRange: $('file-preview-range'),
      filePreviewPath: $('file-preview-path'),
      filePreviewMeta: $('file-preview-meta'),
      filePreviewBanner: $('file-preview-banner'),
      filePreviewContent: $('file-preview-content'),
      btnFilePreviewMdToggle: $('btn-file-preview-md-toggle'),
      btnFilePreviewWrap: $('btn-file-preview-wrap'),
      btnFilePreviewCopy: $('btn-file-preview-copy'),
      btnFilePreviewRefresh: $('btn-file-preview-refresh'),
      btnFilePreviewClose: $('btn-file-preview-close'),
      imageModal: $('image-modal'),
      imageModalStage: $('image-modal-stage'),
      imageModalImg: $('image-modal-img'),
      imageModalCounter: $('image-modal-counter'),
      btnImagePrev: $('btn-image-prev'),
      btnImageNext: $('btn-image-next'),
      toast: $('toast'),
    };

    marked.setOptions({ gfm: true, breaks: true });

    function escapeHtml(str) {
      return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function basenamePath(p) {
      if (!p) return '';
      const cleaned = String(p).replace(/\/+$/, '');
      if (!cleaned) return '/';
      const parts = cleaned.split('/');
      return parts[parts.length - 1] || cleaned;
    }

    function normalizeDirKey(p) {
      const cleaned = String(p || '').trim();
      if (!cleaned) return '';
      if (cleaned === '/') return '/';
      return cleaned.replace(/\/+$/, '');
    }

    function parentPathHint(p) {
      const norm = normalizeDirKey(p);
      if (!norm || norm === '/') return '';
      const idx = norm.lastIndexOf('/');
      if (idx <= 0) return '/';
      return norm.slice(0, idx);
    }

    function formatRelativeTime(tsSec) {
      if (!tsSec) return '';
      const diff = Math.max(0, Math.floor(Date.now() / 1000 - tsSec));
      if (diff < 60) return '刚刚';
      if (diff < 3600) return `${Math.floor(diff / 60)}分前`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}时前`;
      if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}天前`;
      const d = new Date(tsSec * 1000);
      return `${d.getMonth() + 1}/${d.getDate()}`;
    }

    let toastTimer = null;
    function showToast(msg, isError = false) {
      els.toast.textContent = msg;
      els.toast.className = `fixed top-16 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-full border text-xs shadow-xl whitespace-nowrap max-w-[90vw] truncate ${
        isError
          ? 'bg-rose-950/95 border-rose-500/40 text-rose-200'
          : 'bg-slate-900/95 border-border text-slate-100'
      }`;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => els.toast.classList.add('hidden'), 2600);
    }

    async function copyText(text, btn) {
      let ok = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
          ok = true;
        }
      } catch (_) {}
      if (!ok) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { ok = document.execCommand('copy'); } catch (_) {}
        document.body.removeChild(ta);
      }
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = ok ? '已复制 ✓' : '复制失败';
        setTimeout(() => { btn.textContent = orig; }, 1400);
      } else {
        showToast(ok ? '已复制' : '复制失败', !ok);
      }
      return ok;
    }

