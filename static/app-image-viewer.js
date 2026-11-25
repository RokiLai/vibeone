    // In-App Image Lightbox Viewer (Pinch-to-zoom, Pan, Double-tap, Wheel zoom)
    const lbZoom = { scale: 1, tx: 0, ty: 0 };
    const btnImageZoomReset = $('btn-image-zoom-reset');

    function clampLightboxPan() {
      if (lbZoom.scale <= 1) {
        lbZoom.tx = 0;
        lbZoom.ty = 0;
        return;
      }
      const stageRect = els.imageModalStage.getBoundingClientRect();
      const baseW = els.imageModalImg.offsetWidth || stageRect.width;
      const baseH = els.imageModalImg.offsetHeight || stageRect.height;
      const maxX = Math.max(0, (baseW * lbZoom.scale - stageRect.width) / 2 + 40);
      const maxY = Math.max(0, (baseH * lbZoom.scale - stageRect.height) / 2 + 40);
      lbZoom.tx = Math.max(-maxX, Math.min(maxX, lbZoom.tx));
      lbZoom.ty = Math.max(-maxY, Math.min(maxY, lbZoom.ty));
    }

    function applyLightboxTransform(animate = false) {
      clampLightboxPan();
      els.imageModalImg.style.transition = animate ? 'transform 180ms ease-out' : 'none';
      els.imageModalImg.style.transform = `translate3d(${lbZoom.tx}px, ${lbZoom.ty}px, 0) scale(${lbZoom.scale})`;
      const zoomed = lbZoom.scale > 1.02;
      btnImageZoomReset.classList.toggle('hidden', !zoomed);
      if (zoomed) btnImageZoomReset.textContent = `${lbZoom.scale.toFixed(1)}x`;
    }

    function resetLightboxZoom(animate = false) {
      lbZoom.scale = 1;
      lbZoom.tx = 0;
      lbZoom.ty = 0;
      applyLightboxTransform(animate);
    }

    function renderImageModal() {
      const imgs = state.lightboxImages || [];
      if (!imgs.length) return;
      const idx = Math.max(0, Math.min(state.lightboxIndex, imgs.length - 1));
      state.lightboxIndex = idx;
      resetLightboxZoom(false);
      els.imageModalImg.src = imgs[idx];
      els.imageModalCounter.textContent = `${idx + 1} / ${imgs.length}`;
      const multi = imgs.length > 1;
      els.btnImagePrev.classList.toggle('hidden', !multi || idx === 0);
      els.btnImageNext.classList.toggle('hidden', !multi || idx === imgs.length - 1);
    }

    function openImageModal(urls, startIdx = 0) {
      const list = (Array.isArray(urls) ? urls : [urls]).filter(Boolean);
      if (!list.length) return;
      state.lightboxImages = list;
      state.lightboxIndex = startIdx;
      renderImageModal();
      els.imageModal.classList.remove('hidden');
    }

    function closeImageModal() {
      els.imageModal.classList.add('hidden');
      resetLightboxZoom(false);
      els.imageModalImg.src = '';
      state.lightboxImages = [];
    }

    $('btn-image-close').addEventListener('click', closeImageModal);
    btnImageZoomReset.addEventListener('click', (e) => {
      e.stopPropagation();
      resetLightboxZoom(true);
    });
    els.btnImagePrev.addEventListener('click', (e) => {
      e.stopPropagation();
      if (state.lightboxIndex > 0) {
        state.lightboxIndex -= 1;
        renderImageModal();
      }
    });
    els.btnImageNext.addEventListener('click', (e) => {
      e.stopPropagation();
      if (state.lightboxIndex < state.lightboxImages.length - 1) {
        state.lightboxIndex += 1;
        renderImageModal();
      }
    });
    els.imageModalStage.addEventListener('click', (e) => {
      if (e.target === els.imageModalStage && lbZoom.scale <= 1.02) closeImageModal();
    });
    els.imageModal.addEventListener('click', (e) => {
      if (e.target === els.imageModal && lbZoom.scale <= 1.02) closeImageModal();
    });

    // Desktop double-click & mouse wheel zoom
    els.imageModalImg.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      if (lbZoom.scale > 1.05) {
        resetLightboxZoom(true);
      } else {
        const rect = els.imageModalStage.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const nextScale = 2.5;
        lbZoom.tx = (cx - e.clientX) * (nextScale - 1);
        lbZoom.ty = (cy - e.clientY) * (nextScale - 1);
        lbZoom.scale = nextScale;
        applyLightboxTransform(true);
      }
    });
    els.imageModalStage.addEventListener('wheel', (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 0.87;
      lbZoom.scale = Math.max(1, Math.min(5, lbZoom.scale * factor));
      if (lbZoom.scale <= 1.02) lbZoom.scale = 1;
      applyLightboxTransform(false);
    }, { passive: false });

    // Mobile touch gestures: Pinch-to-zoom, Pan when zoomed, Double-tap toggle, Swipe down to close / left-right to switch
    let touchMode = 'none'; // 'none' | 'pan' | 'swipe' | 'pinch'
    let startX = 0, startY = 0, startTx = 0, startTy = 0;
    let pinchStartDist = 0, pinchStartScale = 1, pinchMidX = 0, pinchMidY = 0;
    let lastTapTime = 0, lastTapX = 0, lastTapY = 0;

    function getTouchDist(t1, t2) {
      return Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
    }

    els.imageModalStage.addEventListener('touchstart', (e) => {
      if (e.touches.length === 2) {
        e.preventDefault();
        touchMode = 'pinch';
        pinchStartDist = getTouchDist(e.touches[0], e.touches[1]) || 1;
        pinchStartScale = lbZoom.scale;
        pinchMidX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        pinchMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        startTx = lbZoom.tx;
        startTy = lbZoom.ty;
      } else if (e.touches.length === 1) {
        const t = e.touches[0];
        startX = t.clientX;
        startY = t.clientY;
        startTx = lbZoom.tx;
        startTy = lbZoom.ty;
        touchMode = lbZoom.scale > 1.02 ? 'pan' : 'swipe';
      }
    }, { passive: false });

    els.imageModalStage.addEventListener('touchmove', (e) => {
      if (touchMode === 'pinch' && e.touches.length === 2) {
        e.preventDefault();
        const dist = getTouchDist(e.touches[0], e.touches[1]);
        const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        const nextScale = Math.max(0.85, Math.min(5, pinchStartScale * (dist / pinchStartDist)));
        const stageRect = els.imageModalStage.getBoundingClientRect();
        const cx = stageRect.left + stageRect.width / 2;
        const cy = stageRect.top + stageRect.height / 2;
        const ratio = nextScale / pinchStartScale;
        lbZoom.scale = nextScale;
        lbZoom.tx = (midX - cx) - (pinchMidX - cx - startTx) * ratio;
        lbZoom.ty = (midY - cy) - (pinchMidY - cy - startTy) * ratio;
        applyLightboxTransform(false);
      } else if (touchMode === 'pan' && e.touches.length === 1) {
        e.preventDefault();
        const t = e.touches[0];
        lbZoom.tx = startTx + (t.clientX - startX);
        lbZoom.ty = startTy + (t.clientY - startY);
        applyLightboxTransform(false);
      }
    }, { passive: false });

    els.imageModalStage.addEventListener('touchend', (e) => {
      if (touchMode === 'pinch') {
        if (e.touches.length < 2) {
          if (lbZoom.scale <= 1.05) resetLightboxZoom(true);
          else applyLightboxTransform(true);
          touchMode = 'none';
        }
        return;
      }

      if (!e.changedTouches.length) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - startX;
      const dy = t.clientY - startY;
      const moved = Math.hypot(dx, dy);

      // Double-tap to toggle 2.5x zoom at tap point
      if (moved < 12 && e.target === els.imageModalImg) {
        const now = Date.now();
        if (now - lastTapTime < 280 && Math.hypot(t.clientX - lastTapX, t.clientY - lastTapY) < 35) {
          lastTapTime = 0;
          if (lbZoom.scale > 1.05) {
            resetLightboxZoom(true);
          } else {
            const rect = els.imageModalStage.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const nextScale = 2.5;
            lbZoom.tx = (cx - t.clientX) * (nextScale - 1);
            lbZoom.ty = (cy - t.clientY) * (nextScale - 1);
            lbZoom.scale = nextScale;
            applyLightboxTransform(true);
          }
          touchMode = 'none';
          return;
        }
        lastTapTime = now;
        lastTapX = t.clientX;
        lastTapY = t.clientY;
      }

      if (touchMode === 'swipe' && lbZoom.scale <= 1.02) {
        if (dy > 75 && Math.abs(dy) > Math.abs(dx) * 1.4) {
          closeImageModal();
        } else if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.4) {
          if (dx < 0 && state.lightboxIndex < state.lightboxImages.length - 1) {
            state.lightboxIndex += 1;
            renderImageModal();
          } else if (dx > 0 && state.lightboxIndex > 0) {
            state.lightboxIndex -= 1;
            renderImageModal();
          }
        }
      }
      touchMode = 'none';
    }, { passive: true });

    // Also intercept clicks on any markdown <img> inside assistant messages
    els.chatList.addEventListener('click', (e) => {
      const img = e.target.closest('.md-body img');
      if (img && img.src) {
        e.preventDefault();
        openImageModal([img.src], 0);
      }
    });

    document.addEventListener('keydown', (e) => {
      if (!els.imageModal.classList.contains('hidden')) {
        if (e.key === 'Escape') { closeImageModal(); return; }
        if (e.key === 'ArrowLeft' && state.lightboxIndex > 0) {
          state.lightboxIndex -= 1;
          renderImageModal();
          return;
        }
        if (e.key === 'ArrowRight' && state.lightboxIndex < state.lightboxImages.length - 1) {
          state.lightboxIndex += 1;
          renderImageModal();
          return;
        }
      }
      if (e.key === 'Escape') {
        if (!els.dirModal.classList.contains('hidden')) closeDirModal();
        if (!els.modelModal.classList.contains('hidden')) {
          if (state.selectedModelGroupKey) {
            state.selectedModelGroupKey = null;
            renderModelList();
          } else {
            closeModelModal();
          }
        }
        if (!els.diffModal.classList.contains('hidden')) els.diffModal.classList.add('hidden');
        if (els.filePreviewModal && !els.filePreviewModal.classList.contains('hidden')) {
          closeFilePreview();
          return;
        }
        if (els.settingsModal && !els.settingsModal.classList.contains('hidden')) {
          if (state.settingsView === 'archived') {
            setSettingsView('main');
          } else {
            closeSettingsModal();
          }
          return;
        }
        closeSidebar();
      }
    });

