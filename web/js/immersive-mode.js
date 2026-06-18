(function() {
    'use strict';

    const DEFAULT_IMMERSIVE_DELAY_MS = 180000;
    const ACTIVE_PLAYBACK_STATES = new Set(['playing', 'buffering', 'transitioning']);
    const PLAYING_OVERLAY_START_ANGLE = Number(window.Constants?.overlays?.bottomOverlayStart || 200);

    let idleTimer = null;
    let overlayVisible = false;
    let overlayRoot = null;
    let lastOverlayText = { title: '', artist: '', album: '' };
    let lastPlaybackActive = false;

    function normalizePlaybackState(state) {
        return String(state || '').trim().toLowerCase();
    }

    function immersiveDelayMs() {
        const seconds = Number(window.AppConfig?.screen?.immersiveDelaySeconds || 0);
        if (Number.isFinite(seconds) && seconds > 0) {
            return Math.max(5000, seconds * 1000);
        }
        return DEFAULT_IMMERSIVE_DELAY_MS;
    }

    function mediaInfo() {
        return window.uiStore?.mediaInfo || {};
    }

    function isPlaybackActive() {
        return ACTIVE_PLAYBACK_STATES.has(normalizePlaybackState(mediaInfo().state));
    }

    function shouldArmImmersive() {
        if (!isPlaybackActive()) return false;
        if (window.CameraOverlayManager?.isActive) return false;
        const splash = document.getElementById('splash-overlay');
        if (splash && !splash.classList.contains('hidden')) return false;
        return true;
    }

    function shouldEnterImmediatelyFromLaserZone() {
        if (!shouldArmImmersive()) return false;
        const uiStore = window.uiStore;
        if (!uiStore) return false;
        if (String(uiStore.currentRoute || '').trim().toLowerCase() !== 'menu/playing') return false;
        if (uiStore.menuVisible !== false) return false;
        const angle = Number(uiStore.wheelPointerAngle);
        return Number.isFinite(angle) && angle >= PLAYING_OVERLAY_START_ANGLE;
    }

    function ensureOverlay() {
        if (overlayRoot) return overlayRoot;

        overlayRoot = document.createElement('div');
        overlayRoot.id = 'immersive-overlay';
        overlayRoot.setAttribute('aria-hidden', 'true');
        overlayRoot.innerHTML = [
            '<div class="immersive-vignette"></div>',
            '<div class="immersive-shell">',
            '  <div class="immersive-artwork-slot">',
            '    <img class="immersive-artwork" alt="">',
            '  </div>',
            '  <div class="immersive-info">',
            '    <div class="immersive-info-title"></div>',
            '    <div class="immersive-info-artist"></div>',
            '    <div class="immersive-info-album"></div>',
            '  </div>',
            '</div>'
        ].join('');
        document.body.appendChild(overlayRoot);
        return overlayRoot;
    }

    function syncOverlayDots() {
        const overlay = ensureOverlay();
        const info = mediaInfo();
        const titleEl = overlay.querySelector('.immersive-info-title');
        const artistEl = overlay.querySelector('.immersive-info-artist');
        const musicVideoEnabled = !window.MusicVideoPreference
            || window.MusicVideoPreference.enabled !== false;
        if (titleEl) titleEl.classList.toggle('has-canvas', !!info.canvas_url);
        if (artistEl) artistEl.classList.toggle('has-video', musicVideoEnabled && !!info.music_video_url);
    }

    function renderArtwork() {
        const overlay = ensureOverlay();
        const image = overlay.querySelector('.immersive-artwork');
        if (!image) return;

        const artwork = String(mediaInfo().artwork || '').trim();
        if (window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(
                image,
                artwork,
                isPlaybackActive() ? 'noArtwork' : 'blank'
            );
            return;
        }

        if (artwork) {
            image.src = artwork;
        }
    }

    function syncOverlayText(animate = false) {
        const overlay = ensureOverlay();
        const info = mediaInfo();
        const nextText = {
            title: info.title || '',
            artist: info.artist || '',
            album: info.album || ''
        };
        const fields = [
            { key: 'title', element: overlay.querySelector('.immersive-info-title') },
            { key: 'artist', element: overlay.querySelector('.immersive-info-artist') },
            { key: 'album', element: overlay.querySelector('.immersive-info-album') }
        ];

        fields.forEach(({ key, element }) => {
            if (!element) return;
            if (nextText[key] === lastOverlayText[key]) return;

            if (animate && overlayVisible) {
                element.style.opacity = '0';
                const value = nextText[key];
                window.setTimeout(() => {
                    element.textContent = value;
                    element.style.removeProperty('opacity');
                }, 150);
            } else {
                element.textContent = nextText[key];
                element.style.removeProperty('opacity');
            }
        });

        lastOverlayText = { ...nextText };
        syncOverlayDots();
    }

    function renderOverlay(animateText = false) {
        ensureOverlay();
        renderArtwork();
        syncOverlayText(animateText);
    }

    function clearIdleTimer() {
        window.clearTimeout(idleTimer);
        idleTimer = null;
    }

    function armIdleTimer() {
        clearIdleTimer();
        if (overlayVisible || !shouldArmImmersive()) return;

        idleTimer = window.setTimeout(() => {
            if (!shouldArmImmersive()) return;
            showOverlay();
        }, immersiveDelayMs());
    }

    function showOverlay() {
        if (overlayVisible || !shouldArmImmersive()) return false;
        const overlay = ensureOverlay();
        renderOverlay(false);
        overlayVisible = true;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('active');
        document.dispatchEvent(new CustomEvent('bs5c:immersive-visibility', {
            detail: { visible: true }
        }));
        return true;
    }

    function hideOverlay() {
        const overlay = ensureOverlay();
        const wasVisible = overlayVisible || overlay.classList.contains('active');
        overlayVisible = false;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.classList.remove('active');
        if (wasVisible) {
            document.dispatchEvent(new CustomEvent('bs5c:immersive-visibility', {
                detail: { visible: false }
            }));
        }
        return wasVisible;
    }

    function consumeUserActivity(kind = '') {
        const normalized = String(kind || '').trim().toLowerCase();
        if (!overlayVisible) return false;
        if (normalized === 'pointer') {
            hideOverlay();
            armIdleTimer();
            return false;
        }
        if (normalized === 'nav') {
            hideOverlay();
            armIdleTimer();
            return true;
        }
        return false;
    }

    function handlePlaybackUpdate() {
        const activeNow = isPlaybackActive();
        renderOverlay(true);

        if (!activeNow) {
            clearIdleTimer();
            hideOverlay();
        } else if (!lastPlaybackActive) {
            armIdleTimer();
        }

        lastPlaybackActive = activeNow;
    }

    function handleDocumentPointerActivity() {
        if (!overlayVisible) return;
        hideOverlay();
        armIdleTimer();
    }

    function init() {
        if (!window.uiStore) {
            window.setTimeout(init, 200);
            return;
        }

        ensureOverlay();
        renderOverlay(false);
        lastPlaybackActive = isPlaybackActive();
        if (lastPlaybackActive) {
            armIdleTimer();
        }

        document.addEventListener('bs5c:user-interaction', () => {
            if (!overlayVisible) {
                armIdleTimer();
            }
        });

        document.addEventListener('bs5c:wheel-change', () => {
            if (overlayVisible) return;
            if (shouldEnterImmediatelyFromLaserZone()) {
                clearIdleTimer();
                showOverlay();
                return;
            }
            armIdleTimer();
        });

        document.addEventListener('bs5c:view-change', () => {
            hideOverlay();
            armIdleTimer();
        });

        document.addEventListener('bs5c:media-update', handlePlaybackUpdate);

        document.addEventListener('bs5c:media-text-updated', () => {
            if (overlayVisible) {
                syncOverlayText(true);
            }
        });

        document.addEventListener('bs5c:music-video-preference', () => {
            renderOverlay(false);
        });

        document.addEventListener('mousemove', handleDocumentPointerActivity, true);
        document.addEventListener('mousedown', handleDocumentPointerActivity, true);
        document.addEventListener('touchstart', handleDocumentPointerActivity, { capture: true, passive: true });
        document.addEventListener('touchmove', handleDocumentPointerActivity, { capture: true, passive: true });

        console.log('[IMMERSIVE] Overlay manager initialized');
    }

    window.ImmersiveMode = {
        enter: showOverlay,
        exit: hideOverlay,
        consumeUserActivity,
        syncText: () => renderOverlay(false),
        get active() { return overlayVisible; },
        get progress() { return overlayVisible ? 1 : 0; }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => window.setTimeout(init, 200));
    } else {
        window.setTimeout(init, 200);
    }
})();
