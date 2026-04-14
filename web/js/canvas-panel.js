/**
 * Spotify Canvas Video Panel
 *
 * Displays Spotify Canvas looping videos as a fullscreen overlay
 * during immersive mode. Auto-cycles between artwork and canvas.
 *
 * Text metadata is handled by immersive-mode.js — this module only
 * manages the video layer and progress bar.
 */
(function() {
    'use strict';

    // ── Configuration ──
    var ARTWORK_SHOW_MS = 15000;  // show artwork for 15s before switching to canvas
    var CANVAS_SHOW_MS = 10000;   // show canvas for 10s (or one video loop, whichever is longer)
    var FADE_MS = 800;

    // ── State ──
    var active = false;         // canvas video is currently showing
    var currentUrl = '';        // currently loaded canvas URL
    var currentTrackId = '';    // track_id the loaded canvas was fetched for
    var videoReady = false;     // preloaded video is ready to play
    var container = null;       // root DOM element
    var video = null;           // <video> element
    var textMirror = null;      // mirrors immersive-info text position
    var progressBar = null;     // thin progress line
    var cycleTimer = null;      // auto-cycle setTimeout ID
    var cycling = false;        // true while auto-cycle is running
    var fadeRAF = null;

    // ── Helpers ──

    function isPaused() {
        var s = window.uiStore && window.uiStore.mediaInfo && window.uiStore.mediaInfo.state;
        return s === 'paused' || s === 'idle' || s === 'stopped';
    }

    function trackMatches() {
        // Render-time guard: only show canvas if the currently playing
        // track is the one this canvas was fetched for. Without this,
        // a stale canvas can flash up briefly between a track change
        // and the next media_update arriving with a fresh canvas_url.
        if (!currentTrackId) return true;  // no id stamped — fall through
        var live = window.uiStore && window.uiStore.mediaInfo && window.uiStore.mediaInfo.track_id;
        return live === currentTrackId;
    }

    // ── DOM setup ──

    function ensureDOM() {
        if (container) return;

        container = document.createElement('div');
        container.className = 'canvas-panel';
        container.innerHTML =
            '<video class="canvas-video" autoplay loop muted playsinline></video>' +
            '<div class="canvas-text-mirror"></div>' +
            '<div class="canvas-progress"><div class="canvas-progress-fill"></div></div>';

        video = container.querySelector('.canvas-video');
        textMirror = container.querySelector('.canvas-text-mirror');
        progressBar = container.querySelector('.canvas-progress-fill');

        video.addEventListener('canplaythrough', function() {
            videoReady = true;
            if (active) fadeIn();
            tryStartCycle();
        });
        video.addEventListener('timeupdate', updateProgress);
        video.addEventListener('error', function() {
            videoReady = false;
            if (active) hide();
        });

        document.body.appendChild(container);
    }

    // ── Show / Hide (JS-animated to avoid GPU compositor layer) ──

    function animateOpacity(from, to, done) {
        if (fadeRAF) cancelAnimationFrame(fadeRAF);
        var start = null;
        function step(ts) {
            if (!start) start = ts;
            var p = Math.min((ts - start) / FADE_MS, 1);
            var eased = 1 - Math.pow(1 - p, 3);
            container.style.opacity = from + (to - from) * eased;
            if (p < 1) {
                fadeRAF = requestAnimationFrame(step);
            } else {
                fadeRAF = null;
                if (done) done();
            }
        }
        fadeRAF = requestAnimationFrame(step);
    }

    function fadeIn() {
        if (!container || !videoReady) return;
        container.style.pointerEvents = 'auto';
        video.play().catch(function() {});
        animateOpacity(parseFloat(container.style.opacity) || 0, 1);
    }

    function fadeOut() {
        if (!container) return;
        animateOpacity(parseFloat(container.style.opacity) || 1, 0, function() {
            container.style.pointerEvents = 'none';
            if (!active && video) video.pause();
        });
    }

    function syncTextMirror() {
        // Clone the immersive overlay's exact screen position and content
        var src = document.querySelector('.immersive-info');
        if (!src || !textMirror) return;
        var rect = src.getBoundingClientRect();
        textMirror.style.cssText =
            'position:absolute;left:' + rect.left + 'px;top:' + rect.top + 'px;' +
            'width:' + rect.width + 'px;color:white;pointer-events:none;z-index:2;';
        textMirror.innerHTML = src.innerHTML;
        // Copy computed styles for each child
        var srcKids = src.children;
        var mirKids = textMirror.children;
        for (var i = 0; i < srcKids.length && i < mirKids.length; i++) {
            var cs = getComputedStyle(srcKids[i]);
            mirKids[i].style.cssText =
                'font-size:' + cs.fontSize + ';font-weight:' + cs.fontWeight +
                ';margin-bottom:' + cs.marginBottom + ';opacity:' + cs.opacity +
                ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' +
                'width:' + cs.width + ';';
            mirKids[i].textContent = srcKids[i].textContent;
        }
    }

    function show() {
        if (active) return;
        if (!currentUrl || !videoReady) return;
        if (isPaused()) return;     // playback is paused — stay on artwork
        if (!trackMatches()) return; // canvas is for a different track
        active = true;
        syncTextMirror();
        fadeIn();
        document.dispatchEvent(new CustomEvent('bs5c:canvas-visibility', { detail: { visible: true } }));
    }

    function hide() {
        if (!active) return;
        active = false;
        fadeOut();
        document.dispatchEvent(new CustomEvent('bs5c:canvas-visibility', { detail: { visible: false } }));
    }

    // ── Auto-cycle ──

    function tryStartCycle() {
        if (cycling) return;
        if (!currentUrl || !videoReady) return;
        if (isPaused()) return;
        if (!trackMatches()) return;
        if (!window.ImmersiveMode || !window.ImmersiveMode.active) return;
        cycling = true;
        scheduleCanvas();
    }

    function stopCycle() {
        cycling = false;
        clearTimeout(cycleTimer);
        cycleTimer = null;
        if (active) hide();
    }

    function scheduleCanvas() {
        clearTimeout(cycleTimer);
        if (!cycling) return;
        cycleTimer = setTimeout(function() {
            if (!cycling || !videoReady || !currentUrl) return;
            if (!window.ImmersiveMode || !window.ImmersiveMode.active) { stopCycle(); return; }
            show();
            scheduleArtwork();
        }, ARTWORK_SHOW_MS);
    }

    function scheduleArtwork() {
        clearTimeout(cycleTimer);
        if (!cycling) return;
        var duration = CANVAS_SHOW_MS;
        if (video && video.duration && video.duration > 0) {
            var loopMs = video.duration * 1000;
            if (loopMs > duration) duration = loopMs;
        }
        cycleTimer = setTimeout(function() {
            if (!cycling) return;
            if (!window.ImmersiveMode || !window.ImmersiveMode.active) { stopCycle(); return; }
            hide();
            scheduleCanvas();
        }, duration);
    }

    // ── Video loading ──

    function loadVideo(url, trackId) {
        if (!url || url === currentUrl) return;
        ensureDOM();
        currentUrl = url;
        // Capture the track_id this canvas was fetched for (may be empty).
        // trackMatches() uses this at render time to refuse showing a
        // canvas that no longer belongs to the live track.
        currentTrackId = trackId || (window.uiStore && window.uiStore.mediaInfo && window.uiStore.mediaInfo.track_id) || '';
        videoReady = false;
        video.src = url;
        video.load();
    }

    function clearVideo() {
        currentUrl = '';
        currentTrackId = '';
        videoReady = false;
        stopCycle();
        if (video) {
            video.removeAttribute('src');
            video.load();
        }
    }

    // ── Progress bar ──

    function updateProgress() {
        if (!progressBar || !video || !video.duration) return;
        var pct = (video.currentTime / video.duration) * 100;
        progressBar.style.width = pct + '%';
    }

    // ── Event listeners ──

    function init() {
        var uiStore = window.uiStore;
        if (!uiStore) { setTimeout(init, 200); return; }

        ensureDOM();

        // Track changes — load new canvas video
        document.addEventListener('bs5c:media-update', function(e) {
            var reason = e.detail.reason;
            var data = e.detail.data || {};
            var url = data.canvas_url;
            var tid = data.track_id || '';
            // Pause/stop must yank us back to artwork immediately —
            // if a media_update arrives while canvas is showing and
            // the new state is not playing, hide and stop cycling.
            if (data.state && data.state !== 'playing') {
                stopCycle();
            }
            if (reason === 'track_change') {
                // New track — reset cycling, drop any stale canvas, then
                // set up for the new canvas if the payload included one.
                stopCycle();
                clearVideo();
                if (url) {
                    loadVideo(url, tid);
                    tryStartCycle();
                }
            } else if (url) {
                // Same track, canvas arrived (background fetch completed)
                loadVideo(url, tid);
                tryStartCycle();
            } else if ('canvas_url' in data) {
                // Update explicitly cleared canvas (idle push, external
                // override, stale canvas_inject correction). Drop the
                // video element so the old canvas doesn't keep playing.
                clearVideo();
            }
        });

        // Update text mirror when media changes during canvas
        document.addEventListener('bs5c:media-text-updated', function() {
            if (active) syncTextMirror();
        });

        // Immersive mode entered — start cycling
        document.addEventListener('bs5c:menu-visibility', function(e) {
            if (e.detail.visible) {
                stopCycle();
            } else {
                setTimeout(function() { tryStartCycle(); }, 800);
            }
        });

        // View change — stop cycling when leaving playing
        document.addEventListener('bs5c:view-change', function(e) {
            if (e.detail.to !== 'menu/playing') {
                stopCycle();
            }
            if (e.detail.to === 'menu/playing') {
                var url = uiStore.mediaInfo?.canvas_url;
                if (url) loadVideo(url);
            }
        });

        var initialUrl = uiStore.mediaInfo?.canvas_url;
        var initialTid = uiStore.mediaInfo?.track_id || '';
        if (initialUrl) loadVideo(initialUrl, initialTid);
    }

    // Expose for debugging
    window.CanvasPanel = {
        show: show, hide: hide,
        get active() { return active; },
        get cycling() { return cycling; },
        get hasCanvas() { return !!currentUrl && videoReady; },
        loadUrl: loadVideo,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(init, 300); });
    } else {
        setTimeout(init, 300);
    }
})();
