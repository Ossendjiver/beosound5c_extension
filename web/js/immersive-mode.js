(function() {
    'use strict';

    // ── Configuration ──
    const IDLE_TIMEOUT = 30000;       // 30s inactivity before immersive
    const ARTWORK_MS = 600;           // transition duration for idle enter/exit
    const OVERLAY_ANGLE_START = 200;  // laser angle where immersive starts
    const OVERLAY_ANGLE_END = 210;    // laser angle where immersive is fully active
    const OVERLAY_RANGE = OVERLAY_ANGLE_END - OVERLAY_ANGLE_START; // 10 degrees

    // Transform targets at full immersive (progress = 1)
    const TX = -243, TY = 50, SCALE_EXTRA = 0.21;

    // ── State ──
    let progress = 0;           // 0 = normal, 1 = fully immersive
    let idleTimer = null;
    let isTracking = false;     // true while laser is actively driving progress
    let lastOverlayText = { title: '', artist: '', album: '' };
    let transitionCleanup = null;  // setTimeout ID for post-transition cleanup

    function isFullyImmersive() { return progress >= 1; }
    function isPartiallyImmersive() { return progress > 0; }

    // ── Playback state check ──
    function isPlaying() {
        const state = window.uiStore?.mediaInfo?.state;
        return state === 'playing' || state === 'TRANSITIONING';
    }

    // ── DOM helpers ──

    function getContainer() {
        return document.getElementById('now-playing');
    }

    function ensureOverlay() {
        const container = getContainer();
        if (!container) return null;
        let el = container.querySelector('.immersive-info');
        if (!el) {
            el = document.createElement('div');
            el.className = 'immersive-info';
            el.innerHTML =
                '<div class="immersive-info-title"></div>' +
                '<div class="immersive-info-artist"></div>' +
                '<div class="immersive-info-album"></div>';
            container.appendChild(el);
        }
        return el;
    }

    // ── Apply visual state from progress value ──

    function applyProgress(p) {
        const container = getContainer();
        if (!container) return;

        const artwork = container.querySelector('.media-view-artwork');
        const info = container.querySelector('.media-view-info');
        const overlay = ensureOverlay();

        if (p <= 0) {
            // Fully normal
            container.classList.remove('immersive-active');
            if (artwork) artwork.style.transform = '';
            if (info) { info.style.opacity = ''; info.style.pointerEvents = ''; }
            if (overlay) overlay.style.opacity = '0';
            return;
        }

        // Partially or fully immersive
        container.classList.add('immersive-active');

        // Artwork transform: interpolate from identity to full immersive
        if (artwork) {
            artwork.style.transform = `translate(${TX * p}px, ${TY * p}px) scale(${1 + SCALE_EXTRA * p})`;
        }

        // Info fades out in the first half of progress
        if (info) {
            const infoOpacity = Math.max(0, 1 - p * 2);
            info.style.opacity = String(infoOpacity);
            info.style.pointerEvents = infoOpacity < 0.1 ? 'none' : '';
        }

        // Sync overlay text just before it becomes visible
        if (p > 0.4) {
            syncOverlayText(false);
        }

        // Overlay fades in during the second half of progress
        if (overlay) {
            const overlayOpacity = p > 0.5 ? (p - 0.5) * 2 : 0;
            overlay.style.opacity = String(overlayOpacity);
        }
    }

    // ── Smooth text update with per-field fade ──

    function syncOverlayText(animate) {
        const overlay = ensureOverlay();
        if (!overlay) return;

        const mi = window.uiStore?.mediaInfo;
        const newText = {
            title: mi?.title || '',
            artist: mi?.artist || '',
            album: mi?.album || ''
        };

        const fields = [
            { key: 'title', el: overlay.querySelector('.immersive-info-title') },
            { key: 'artist', el: overlay.querySelector('.immersive-info-artist') },
            { key: 'album', el: overlay.querySelector('.immersive-info-album') }
        ];

        for (const f of fields) {
            if (!f.el) continue;
            if (newText[f.key] === lastOverlayText[f.key]) continue;

            if (animate && isPartiallyImmersive()) {
                // Fade out, swap text, fade in
                f.el.style.opacity = '0';
                const newVal = newText[f.key];
                setTimeout(() => {
                    f.el.textContent = newVal;
                    f.el.style.opacity = '';
                }, 250);
            } else {
                // Instant update
                f.el.textContent = newText[f.key];
            }
        }

        lastOverlayText = { ...newText };
    }

    // ── Laser-driven progressive animation ──

    function updateFromLaser() {
        const uiStore = window.uiStore;
        if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;

        const angle = uiStore.wheelPointerAngle;

        if (angle >= OVERLAY_ANGLE_START) {
            // Laser is in overlay zone — track progressively
            const newProgress = Math.min(1, (angle - OVERLAY_ANGLE_START) / OVERLAY_RANGE);

            if (!isTracking && newProgress > 0) {
                isTracking = true;
                clearIdleTimer();
                // Enable short tracking transitions to smooth discrete laser steps
                setTrackingMode(true);
            }

            progress = newProgress;
            applyProgress(progress);
        } else if (isTracking) {
            // Laser moved back into menu zone — exit tracking
            isTracking = false;
            progress = 0;
            setTrackingMode(false);
            applyProgress(0);
            resetIdleTimer();
        }
    }

    // ── Transition mode control ──

    function setTrackingMode(on) {
        const container = getContainer();
        if (!container) return;
        container.classList.remove('immersive-transitioning');
        if (on) {
            container.classList.add('immersive-tracking');
        } else {
            container.classList.remove('immersive-tracking');
        }
    }

    function enableIdleTransitions() {
        const container = getContainer();
        if (!container) return;
        container.classList.remove('immersive-tracking');
        container.classList.add('immersive-transitioning');
    }

    function clearTransitions() {
        const container = getContainer();
        if (!container) return;
        container.classList.remove('immersive-transitioning', 'immersive-tracking');
    }

    // ── Animated enter/exit (for idle timer) ──

    function scheduleTransitionCleanup() {
        clearTimeout(transitionCleanup);
        transitionCleanup = setTimeout(() => {
            const c = getContainer();
            if (c) c.classList.remove('immersive-transitioning');
            transitionCleanup = null;
        }, ARTWORK_MS);
    }

    function animatedEnter() {
        if (isFullyImmersive() || isTracking) return;
        const container = getContainer();
        if (!container) return;

        syncOverlayText(false);
        enableIdleTransitions();

        // Force reflow so the transition actually animates from current state
        container.offsetHeight;

        progress = 1;
        applyProgress(1);
        scheduleTransitionCleanup();

        console.log('[IMMERSIVE] Entered (idle)');
    }

    function animatedExit() {
        if (!isPartiallyImmersive() || isTracking) return;
        const container = getContainer();
        if (!container) return;

        enableIdleTransitions();
        container.offsetHeight;

        progress = 0;
        applyProgress(0);
        scheduleTransitionCleanup();

        console.log('[IMMERSIVE] Exited (animated)');
    }

    function instantExit() {
        if (!isPartiallyImmersive()) return;
        isTracking = false;
        progress = 0;
        clearTimeout(transitionCleanup);
        transitionCleanup = null;
        clearTransitions();
        applyProgress(0);
    }

    // ── Idle timer ──

    function resetIdleTimer() {
        clearTimeout(idleTimer);
        idleTimer = null;
        const uiStore = window.uiStore;
        if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;
        if (isFullyImmersive() || isTracking) return;

        idleTimer = setTimeout(() => {
            if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;
            if (isFullyImmersive() || isTracking) return;
            if (!isPlaying()) return;  // only go immersive if something is playing
            uiStore.setMenuVisible(false);
            animatedEnter();
        }, IDLE_TIMEOUT);
    }

    function clearIdleTimer() {
        clearTimeout(idleTimer);
        idleTimer = null;
    }

    // ── Init: listen for UIStore events ──

    function init() {
        const uiStore = window.uiStore;
        if (!uiStore) { setTimeout(init, 200); return; }

        // 1. Menu visibility: exit immersive when menu reappears
        document.addEventListener('bs5c:menu-visibility', (e) => {
            if (e.detail.visible && isPartiallyImmersive() && !isTracking) {
                animatedExit();
            }
        });

        // 2. Wheel change: laser tracking + idle timer reset
        document.addEventListener('bs5c:wheel-change', () => {
            updateFromLaser();
            if (!isTracking) resetIdleTimer();
        });

        // 3. View change: cleanup on navigation
        document.addEventListener('bs5c:view-change', (e) => {
            const { from, to } = e.detail;
            const wasPlaying = from === 'menu/playing';
            const wasImmersive = isPartiallyImmersive();

            if (wasPlaying && to !== 'menu/playing') {
                clearIdleTimer();
                instantExit();
            }
            if (to === 'menu/playing') {
                // DOM was rebuilt by updateView() — overlay is gone, text cache is stale
                lastOverlayText = { title: '', artist: '', album: '' };
                if (wasImmersive && wasPlaying) {
                    // Re-apply immersive state after DOM rebuild (e.g. spurious wake)
                    setTimeout(() => {
                        ensureOverlay();
                        syncOverlayText(false);
                        applyProgress(progress);
                    }, 100);
                } else if (!wasPlaying && isPlaying()) {
                    // Waking to playing view while music is active — go straight to immersive
                    setTimeout(() => {
                        ensureOverlay();
                        uiStore.setMenuVisible(false);
                        animatedEnter();
                    }, 200);
                } else {
                    resetIdleTimer();
                    setTimeout(() => ensureOverlay(), 100);
                }
            }
        });

        // 4. Media text updated: sync overlay text on track change
        document.addEventListener('bs5c:media-text-updated', () => {
            syncOverlayText(true);
        });

        // Initial setup
        if (uiStore.currentRoute === 'menu/playing') {
            resetIdleTimer();
            ensureOverlay();
        }

        console.log('[IMMERSIVE] Module initialized (v6.0)');
    }

    // Expose for debugging / manual toggle
    window.ImmersiveMode = {
        enter: animatedEnter,
        exit: animatedExit,
        get active() { return isPartiallyImmersive(); },
        get progress() { return progress; },
        syncText: () => syncOverlayText(false)
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(init, 200));
    } else {
        setTimeout(init, 200);
    }
})();
