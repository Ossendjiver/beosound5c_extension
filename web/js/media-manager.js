/**
 * MediaManager owns all "what's playing" state.
 *
 * It keeps:
 * - router media for PLAYING
 * - showing entity media for SHOWING
 * - a displayed media snapshot that can fall back from PLAYING to SHOWING
 *   when there is no active source / router media
 */

const PLACEHOLDER = '\u2014';
const SHOWING_INPUT_URL = 'http://localhost:8767';
const SHOWING_REFRESH_INTERVAL_MS = 5000;
const SHARED_MEDIA_STYLE_ID = 'bs5c-shared-media-style';

function crossfadeText(el, newText) {
    if (!el) return;
    if (el.textContent === newText) return;
    clearTimeout(el._crossfadeTimer);
    el.style.opacity = '0';
    el._crossfadeTimer = setTimeout(() => {
        el.textContent = newText;
        el.style.removeProperty('opacity');
        document.dispatchEvent(new CustomEvent('bs5c:media-text-updated'));
    }, 200);
}
window.crossfadeText = crossfadeText;

function isActivePlaybackState(state) {
    const normalized = String(state || '').trim().toLowerCase();
    return normalized === 'playing' || normalized === 'paused' || normalized === 'buffering';
}

function isPausedPlaybackState(state) {
    return String(state || '').trim().toLowerCase() === 'paused';
}

function hasDisplayValue(value) {
    const text = String(value || '').trim();
    return !!text && text !== PLACEHOLDER && text.toLowerCase() !== 'unknown';
}

function normalizeText(value, fallback = PLACEHOLDER) {
    const text = String(value || '').trim();
    return text || fallback;
}

function formatPlaybackStateLabel(state) {
    const normalized = String(state || '').trim().toLowerCase();
    if (!normalized) return 'Unknown';
    if (normalized === 'playing') return 'Playing';
    if (normalized === 'paused') return 'Paused';
    if (normalized === 'buffering') return 'Buffering';
    if (normalized === 'idle') return 'Idle';
    if (normalized === 'off') return 'Off';
    if (normalized === 'unavailable') return 'Unavailable';
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function ensureSharedMediaStyle() {
    if (document.getElementById(SHARED_MEDIA_STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = SHARED_MEDIA_STYLE_ID;
    style.textContent = `
        .bs5c-paused-overlay {
            position: absolute;
            top: 18px;
            right: 18px;
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 12px 14px;
            border-radius: 999px;
            background: rgba(0, 0, 0, 0.38);
            backdrop-filter: blur(10px);
            box-shadow: 0 14px 30px rgba(0, 0, 0, 0.26);
            opacity: 0;
            transform: translateY(-6px);
            transition: opacity 180ms ease, transform 180ms ease;
            pointer-events: none;
            z-index: 4;
        }
        .bs5c-paused-overlay.is-visible {
            opacity: 1;
            transform: translateY(0);
        }
        .bs5c-paused-bar {
            width: 7px;
            height: 24px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: 0 0 12px rgba(255, 255, 255, 0.14);
        }
        #now-playing.is-showing-fallback .playing-face img {
            object-fit: contain;
            background: rgba(0, 0, 0, 0.28);
        }
    `;
    document.head.appendChild(style);
}

function ensurePausedOverlay(host) {
    if (!host) return null;
    let overlay = host.querySelector('.bs5c-paused-overlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'bs5c-paused-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = `
        <span class="bs5c-paused-bar"></span>
        <span class="bs5c-paused-bar"></span>
    `;
    host.appendChild(overlay);
    return overlay;
}

function setPausedOverlayVisible(host, visible) {
    const overlay = ensurePausedOverlay(host);
    if (!overlay) return;
    overlay.classList.toggle('is-visible', !!visible);
}

const DEFAULT_PLAYING_PRESET = {
    onUpdate(container, data) {
        const titleEl = container.querySelector('.media-view-title');
        const artistEl = container.querySelector('.media-view-artist');
        const albumEl = container.querySelector('.media-view-album');
        crossfadeText(titleEl, data.title || PLACEHOLDER);
        crossfadeText(artistEl, data.artist || PLACEHOLDER);
        crossfadeText(albumEl, data.album || PLACEHOLDER);

        const img = container.querySelector('.playing-artwork');
        if (img && window.ArtworkManager) {
            const hasMedia = data.state === 'playing' || data.state === 'paused';
            const placeholderType = hasMedia ? 'noArtwork' : 'blank';
            window.ArtworkManager.displayArtwork(img, data.artwork, placeholderType);
        }

        const backFace = container.querySelector('.playing-back');
        const backImg = container.querySelector('.playing-artwork-back');
        if (backFace && backImg) {
            if (data.back_artwork) {
                backImg.src = data.back_artwork;
                backFace.style.display = '';
            } else if (!backFace.querySelector('.cd-back-tracklist')) {
                backFace.style.display = 'none';
                const flipper = container.querySelector('.playing-flipper');
                if (flipper) flipper.classList.remove('flipped');
            }
        }
    },
    onMount(container) {
        const flipper = container.querySelector('.playing-flipper');
        if (flipper) {
            flipper._clickHandler = () => {
                const back = flipper.querySelector('.playing-back');
                if (back && back.style.display !== 'none') {
                    flipper.classList.add('playing-flipper-snap');
                    flipper.classList.toggle('flipped');
                    setTimeout(() => flipper.classList.remove('playing-flipper-snap'), 200);
                }
            };
            flipper.addEventListener('click', flipper._clickHandler);
        }
    },
    onRemove(container) {
        const flipper = container.querySelector('.playing-flipper');
        if (flipper?._clickHandler) {
            flipper.removeEventListener('click', flipper._clickHandler);
        }
    }
};

class MediaManager {
    constructor() {
        this.mediaInfo = {
            title: PLACEHOLDER,
            artist: PLACEHOLDER,
            album: PLACEHOLDER,
            artwork: '',
            back_artwork: '',
            canvas_url: '',
            music_video_url: '',
            track_id: '',
            source_id: '',
            state: 'idle',
            position: '0:00',
            duration: '0:00'
        };
        this._routerMediaInfo = { ...this.mediaInfo };

        this.appleTVMediaInfo = {
            title: PLACEHOLDER,
            artist: PLACEHOLDER,
            album: PLACEHOLDER,
            friendly_name: PLACEHOLDER,
            app_name: PLACEHOLDER,
            artwork: '',
            state: 'unknown',
            entity_id: '',
            supported_features: 0
        };

        this.activeSource = null;
        this.activeSourcePlayer = null;
        this.activePlayingPreset = DEFAULT_PLAYING_PRESET;
        this._appleTVRefreshInterval = null;
        this._appleTVUnloadBound = null;

        ensureSharedMediaStyle();
        document.addEventListener('bs5c:view-change', (event) => {
            this.handleRouteChange(event.detail?.to || '');
        });
    }

    resolveArtworkUrl(url, sourceId = '') {
        const value = String(url || '').trim();
        if (!value || !value.startsWith('/art/')) return value;

        const source = String(
            sourceId
            || this.activeSource
            || this.mediaInfo.source_id
            || window.uiStore?.activeSource
            || ''
        ).toLowerCase();

        let key = '';
        let port = 0;
        if (source === 'mass') {
            key = 'massServiceUrl';
            port = 8783;
        } else if (source === 'kodi') {
            key = 'kodiServiceUrl';
            port = 8782;
        } else {
            return value;
        }

        const base = typeof window.getServiceUrl === 'function'
            ? window.getServiceUrl(key, port)
            : `http://${window.location.hostname || 'localhost'}:${port}`;
        return `${String(base).replace(/\/$/, '')}${value}`;
    }

    _hasRouterMedia() {
        return isActivePlaybackState(this._routerMediaInfo.state)
            && (
                hasDisplayValue(this._routerMediaInfo.title)
                || hasDisplayValue(this._routerMediaInfo.artist)
                || !!String(this._routerMediaInfo.artwork || '').trim()
            );
    }

    _hasShowingMedia() {
        return isActivePlaybackState(this.appleTVMediaInfo.state)
            && (
                hasDisplayValue(this.appleTVMediaInfo.title)
                || hasDisplayValue(this.appleTVMediaInfo.artist)
                || hasDisplayValue(this.appleTVMediaInfo.app_name)
                || !!String(this.appleTVMediaInfo.artwork || '').trim()
            );
    }

    shouldUseShowingAsPlaying() {
        return !this.activeSource && !this._hasRouterMedia() && this._hasShowingMedia();
    }

    _buildShowingFallbackMedia() {
        return {
            title: this.appleTVMediaInfo.title || PLACEHOLDER,
            artist: this.appleTVMediaInfo.artist || this.appleTVMediaInfo.app_name || PLACEHOLDER,
            album: this.appleTVMediaInfo.album || this.appleTVMediaInfo.friendly_name || PLACEHOLDER,
            artwork: this.appleTVMediaInfo.artwork || '',
            back_artwork: '',
            canvas_url: '',
            music_video_url: '',
            track_id: '',
            source_id: 'showing',
            state: this.appleTVMediaInfo.state || 'unknown',
            position: '0:00',
            duration: '0:00'
        };
    }

    _applyDisplayedMedia(next, reason = 'update') {
        const normalized = {
            title: next.title || PLACEHOLDER,
            artist: next.artist || PLACEHOLDER,
            album: next.album || PLACEHOLDER,
            artwork: next.artwork || '',
            back_artwork: next.back_artwork || '',
            canvas_url: next.canvas_url || '',
            music_video_url: next.music_video_url || '',
            track_id: next.track_id || '',
            source_id: next.source_id || '',
            state: next.state || 'unknown',
            position: next.position || '0:00',
            duration: next.duration || '0:00'
        };

        const changed = [
            'title', 'artist', 'album', 'artwork', 'back_artwork',
            'canvas_url', 'music_video_url', 'track_id', 'source_id',
            'state', 'position', 'duration'
        ].some((key) => this.mediaInfo[key] !== normalized[key]);

        this.mediaInfo = normalized;

        if (changed) {
            document.dispatchEvent(new CustomEvent('bs5c:media-update', {
                detail: { data: this.mediaInfo, reason }
            }));
        }

        this.updateNowPlayingView();
    }

    syncDisplayedMedia(reason = 'sync') {
        const next = this.shouldUseShowingAsPlaying()
            ? this._buildShowingFallbackMedia()
            : this._routerMediaInfo;
        this._applyDisplayedMedia(next, reason);
    }

    syncActiveSourceContext() {
        this.syncDisplayedMedia('source_context');
    }

    handleRouteChange(path) {
        const route = String(path || window.uiStore?.currentRoute || '').trim();
        if (route === 'menu/playing' || route === 'menu/showing') {
            this.setupAppleTVMediaInfoRefresh();
            if (route === 'menu/showing') {
                this.updateAppleTVMediaView();
            } else {
                this.syncDisplayedMedia('route_change');
            }
        } else {
            this.stopAppleTVMediaInfoRefresh();
        }
    }

    handleMediaUpdate(data, reason = 'update') {
        console.log(`[MEDIA-WS] ${reason}: ${data.title} - ${data.artist}`);

        const keepCanvas = reason !== 'track_change' && !('canvas_url' in data);
        const keepMusicVideo = reason !== 'track_change' && !('music_video_url' in data);
        const keepArtwork = reason !== 'track_change' && !('artwork' in data);
        const keepBackArtwork = reason !== 'track_change' && !('back_artwork' in data);
        const keepTrackId = reason !== 'track_change' && !('track_id' in data);
        const sourceId = data._source_id || data.source_id || this._routerMediaInfo.source_id || this.activeSource || '';

        this._routerMediaInfo = {
            title: data.title || PLACEHOLDER,
            artist: data.artist || PLACEHOLDER,
            album: data.album || PLACEHOLDER,
            artwork: keepArtwork
                ? (this._routerMediaInfo.artwork || '')
                : this.resolveArtworkUrl(data.artwork || '', sourceId),
            back_artwork: keepBackArtwork
                ? (this._routerMediaInfo.back_artwork || '')
                : this.resolveArtworkUrl(data.back_artwork || '', sourceId),
            canvas_url: keepCanvas ? (this._routerMediaInfo.canvas_url || '') : (data.canvas_url || ''),
            music_video_url: keepMusicVideo ? (this._routerMediaInfo.music_video_url || '') : (data.music_video_url || ''),
            track_id: keepTrackId ? (this._routerMediaInfo.track_id || '') : (data.track_id || ''),
            source_id: sourceId,
            state: data.state || 'unknown',
            position: data.position || '0:00',
            duration: data.duration || '0:00'
        };

        this.syncDisplayedMedia(reason);
    }

    updateNowPlayingView() {
        const container = document.getElementById('now-playing');
        if (!container || !this.activePlayingPreset?.onUpdate) return;

        this.activePlayingPreset.onUpdate(container, this.mediaInfo);
        container.classList.toggle('is-showing-fallback', this.mediaInfo.source_id === 'showing');

        const showGenericPausedOverlay = this.activePlayingPreset === DEFAULT_PLAYING_PRESET;
        setPausedOverlayVisible(
            container.querySelector('.playing-artwork-slot'),
            showGenericPausedOverlay && isPausedPlaybackState(this.mediaInfo.state)
        );
    }

    setActivePlayingPreset(sourceId) {
        const preset = sourceId && window.SourcePresets?.[sourceId]?.playing;
        const newPreset = preset || DEFAULT_PLAYING_PRESET;
        if (newPreset === this.activePlayingPreset) {
            this.updateNowPlayingView();
            return;
        }

        const container = document.getElementById('now-playing');
        if (!container) {
            this.activePlayingPreset = newPreset;
            return;
        }

        if (this.activePlayingPreset?.onRemove) this.activePlayingPreset.onRemove(container);
        this.activePlayingPreset = newPreset;
        if (this.activePlayingPreset.onMount) this.activePlayingPreset.onMount(container);
        this.updateNowPlayingView();
    }

    async fetchAppleTVMediaInfo() {
        const isMac = navigator.platform.toLowerCase().includes('mac');
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

        if (isMac && isLocalhost && window.EmulatorMockData) {
            const mockData = window.EmulatorMockData.getCurrentAppleTVShow();
            this.appleTVMediaInfo = {
                title: normalizeText(mockData.title),
                artist: normalizeText(mockData.app_name),
                album: normalizeText(mockData.friendly_name),
                friendly_name: normalizeText(mockData.friendly_name),
                app_name: normalizeText(mockData.app_name),
                artwork: mockData.artwork || window.EmulatorMockData.generateShowingArtwork(mockData),
                state: mockData.state || 'playing',
                entity_id: 'media_player.apple_tv',
                supported_features: 0
            };
            this.updateAppleTVMediaView();
            this.syncDisplayedMedia('showing_refresh');
            return;
        }

        try {
            const response = await fetch(`${SHOWING_INPUT_URL}/appletv`);
            if (!response.ok) {
                this.appleTVMediaInfo = {
                    title: PLACEHOLDER,
                    artist: PLACEHOLDER,
                    album: PLACEHOLDER,
                    friendly_name: PLACEHOLDER,
                    app_name: PLACEHOLDER,
                    artwork: '',
                    state: 'unavailable',
                    entity_id: '',
                    supported_features: 0
                };
                this.updateAppleTVMediaView();
                this.syncDisplayedMedia('showing_unavailable');
                return;
            }

            const data = await response.json();
            this.appleTVMediaInfo = {
                title: normalizeText(data.title),
                artist: normalizeText(data.artist || data.app_name),
                album: normalizeText(data.album || data.friendly_name),
                friendly_name: normalizeText(data.friendly_name),
                app_name: normalizeText(data.app_name),
                artwork: data.artwork || '',
                state: data.state || 'unknown',
                entity_id: data.entity_id || '',
                supported_features: Number(data.supported_features || 0)
            };
            this.updateAppleTVMediaView();
            this.syncDisplayedMedia('showing_refresh');
        } catch (error) {
            console.error('Error fetching Apple TV info:', error);
            this.appleTVMediaInfo = {
                title: PLACEHOLDER,
                artist: PLACEHOLDER,
                album: PLACEHOLDER,
                friendly_name: PLACEHOLDER,
                app_name: PLACEHOLDER,
                artwork: '',
                state: 'error',
                entity_id: '',
                supported_features: 0
            };
            this.updateAppleTVMediaView();
            this.syncDisplayedMedia('showing_error');
        }
    }

    updateAppleTVMediaView() {
        const artworkEl = document.getElementById('apple-tv-artwork');
        const titleEl = document.getElementById('apple-tv-media-title');
        const detailsEl = document.getElementById('apple-tv-media-details');
        const stateEl = document.getElementById('apple-tv-state');
        const artworkHost = document.getElementById('apple-tv-artwork-container');

        const details = [
            this.appleTVMediaInfo.artist,
            this.appleTVMediaInfo.app_name
        ].filter((value, index, values) => hasDisplayValue(value) && values.indexOf(value) === index).join(' · ') || PLACEHOLDER;

        const stateBits = [formatPlaybackStateLabel(this.appleTVMediaInfo.state)];
        if (hasDisplayValue(this.appleTVMediaInfo.friendly_name)) {
            stateBits.push(this.appleTVMediaInfo.friendly_name);
        }

        if (titleEl) crossfadeText(titleEl, this.appleTVMediaInfo.title || PLACEHOLDER);
        if (detailsEl) crossfadeText(detailsEl, details);
        if (stateEl) stateEl.textContent = stateBits.join(' · ');

        if (artworkEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artworkEl, this.appleTVMediaInfo.artwork, 'showing');
        }

        setPausedOverlayVisible(artworkHost, isPausedPlaybackState(this.appleTVMediaInfo.state));
    }

    async sendShowingTransport(command) {
        try {
            const response = await fetch(`${SHOWING_INPUT_URL}/appletv/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command }),
            });
            if (!response.ok) {
                console.warn('[SHOWING UI] Transport command failed:', command, response.status);
                return false;
            }
            setTimeout(() => {
                void this.fetchAppleTVMediaInfo();
            }, 250);
            return true;
        } catch (error) {
            console.warn('[SHOWING UI] Transport command error:', command, error);
            return false;
        }
    }

    handleShowingButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (!this._hasShowingMedia()) return false;

        if (normalized === 'left') {
            void this.sendShowingTransport('previous');
            return true;
        }
        if (normalized === 'right') {
            void this.sendShowingTransport('next');
            return true;
        }
        if (normalized === 'go_long' || normalized === 'go_hold') {
            void this.sendShowingTransport('stop');
            return true;
        }
        if (normalized === 'go') {
            void this.sendShowingTransport('toggle');
            return true;
        }
        return false;
    }

    setupAppleTVMediaInfoRefresh() {
        this.fetchAppleTVMediaInfo();
        this.stopAppleTVMediaInfoRefresh();
        this._appleTVRefreshInterval = setInterval(() => {
            if (document.visibilityState === 'hidden') return;
            this.fetchAppleTVMediaInfo();
        }, SHOWING_REFRESH_INTERVAL_MS);

        if (!this._appleTVUnloadBound) {
            this._appleTVUnloadBound = () => this.stopAppleTVMediaInfoRefresh();
            window.addEventListener('pagehide', this._appleTVUnloadBound);
        }
    }

    stopAppleTVMediaInfoRefresh() {
        if (this._appleTVRefreshInterval) {
            clearInterval(this._appleTVRefreshInterval);
            this._appleTVRefreshInterval = null;
        }
    }
}

window.MediaManager = MediaManager;
window.DEFAULT_PLAYING_PRESET = DEFAULT_PLAYING_PRESET;
