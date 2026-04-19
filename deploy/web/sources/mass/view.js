/**
 * MASS Source Preset
 */
const _massPlayingPreset = (() => {
    const PAGE_IDS = ['now', 'artist', 'queue'];
    const QUEUE_REFRESH_MS = 3000;
    const NOW_PLAYING_REFRESH_MS = 2000;
    const PAGE_CYCLE_COOLDOWN_MS = 520;
    let currentPageIndex = 0;
    let mountedContainer = null;
    let queueTimer = null;
    let nowPlayingTimer = null;
    let queueRequestId = 0;
    let artistRequestId = 0;
    let nowPlayingRequestId = 0;
    let lastPageCycleAt = 0;
    let lastMedia = {
        title: '—',
        artist: '—',
        album: '—',
        artwork: '',
        state: 'idle',
    };
    let queueState = {
        loading: false,
        error: '',
        items: [],
    };
    let artistState = {
        key: '',
        loading: false,
        error: '',
        bio: '',
        name: '',
    };

    function getServiceUrlSafe() {
        return (typeof getServiceUrl === 'function')
            ? getServiceUrl('massServiceUrl', 8783)
            : 'http://localhost:8783';
    }

    function isMeaningfulText(value) {
        const text = String(value || '').trim();
        return Boolean(text && text !== 'â€”' && text !== '-');
    }

    function hasMeaningfulMedia(data) {
        if (!data || typeof data !== 'object') return false;
        return (
            isMeaningfulText(data.title) ||
            isMeaningfulText(data.artist) ||
            isMeaningfulText(data.album) ||
            Boolean(String(data.artwork || '').trim())
        );
    }

    function normalizeArtistKey(value) {
        return String(value || '')
            .replace(/^Now Playing\s*-\s*/i, '')
            .trim()
            .toLowerCase();
    }

    function resetArtistState(key = '') {
        artistState = {
            key,
            loading: false,
            error: '',
            bio: '',
            name: '',
        };
    }

    function syncUiStoreSource() {
        if (!window.uiStore) return;
        window.uiStore.activeSource = 'mass';
        if (typeof window.uiStore.setActivePlayingPreset === 'function') {
            window.uiStore.setActivePlayingPreset('mass');
        }
    }

    function applyMediaSnapshot(data, options = {}) {
        if (!hasMeaningfulMedia(data)) return false;
        const previousArtistKey = normalizeArtistKey(lastMedia.artist);
        lastMedia = Object.assign({}, lastMedia, data || {});
        const nextArtistKey = normalizeArtistKey(lastMedia.artist);
        if (nextArtistKey !== previousArtistKey) {
            resetArtistState(nextArtistKey);
        }
        if (window.uiStore) {
            window.uiStore.mediaInfo = {
                title: lastMedia.title || 'â€”',
                artist: lastMedia.artist || 'â€”',
                album: lastMedia.album || 'â€”',
                artwork: lastMedia.artwork || '',
                back_artwork: lastMedia.back_artwork || '',
                state: lastMedia.state || 'unknown',
                position: lastMedia.position || '0:00',
                duration: lastMedia.duration || '0:00',
            };
        }
        if (options.syncSource !== false) {
            syncUiStoreSource();
        }
        if (mountedContainer) {
            updateBaseView(mountedContainer, lastMedia);
            renderOverlay(mountedContainer);
            if (currentPageId() === 'artist' && nextArtistKey) {
                void refreshArtistInfo(false);
            }
        }
        return true;
    }

    function buildMediaFromQueueTrack(track) {
        if (!track || typeof track !== 'object') return null;
        const rawArtist = String(track.artist || '').trim();
        const artist = rawArtist.replace(/^Now Playing\s*-\s*/i, '').trim();
        const artwork = String(track.image || track.artwork || '').trim();
        const media = {
            title: String(track.name || track.title || '').trim(),
            artist,
            album: String(track.album || '').trim(),
            artwork,
            state: 'playing',
        };
        return hasMeaningfulMedia(media) ? media : null;
    }

    function ensureStyles() {
        if (document.getElementById('mass-playing-preset-style')) return;
        const style = document.createElement('style');
        style.id = 'mass-playing-preset-style';
        style.textContent = `
            #now-playing {
                position: relative;
                overflow: hidden;
            }

            #now-playing.mass-playing-active .mass-playing-overlay {
                position: absolute;
                inset: 0;
                display: flex;
                align-items: stretch;
                justify-content: flex-end;
                pointer-events: none;
                z-index: 3;
            }

            #now-playing.mass-playing-active .mass-playing-panel {
                width: min(44%, 390px);
                height: 100%;
                margin-left: auto;
                padding: 48px 42px 42px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                background: linear-gradient(90deg, rgba(8, 10, 16, 0) 0%, rgba(8, 10, 16, 0.56) 20%, rgba(8, 10, 16, 0.9) 100%);
                opacity: 0;
                transform: translateX(18px);
                transition: opacity 180ms ease, transform 180ms ease;
            }

            #now-playing.mass-playing-active[data-mass-page="artist"] .mass-playing-panel,
            #now-playing.mass-playing-active[data-mass-page="queue"] .mass-playing-panel {
                opacity: 1;
                transform: translateX(0);
            }

            #now-playing.mass-playing-active .mass-playing-kicker {
                font: 600 11px/1.2 Arial, sans-serif;
                letter-spacing: 2.8px;
                text-transform: uppercase;
                color: rgba(160, 184, 222, 0.8);
                margin-bottom: 14px;
            }

            #now-playing.mass-playing-active .mass-playing-heading {
                font: 300 30px/1.08 Arial, sans-serif;
                color: #ffffff;
                margin-bottom: 14px;
                white-space: pre-line;
            }

            #now-playing.mass-playing-active .mass-playing-copy {
                font: 400 16px/1.55 Arial, sans-serif;
                color: rgba(223, 232, 247, 0.82);
                white-space: pre-line;
            }

            #now-playing.mass-playing-active .mass-playing-meta {
                margin-top: 18px;
                font: 500 13px/1.5 Arial, sans-serif;
                color: rgba(160, 184, 222, 0.78);
                letter-spacing: 0.4px;
            }

            #now-playing.mass-playing-active .mass-playing-indicators {
                position: absolute;
                left: 50%;
                bottom: 26px;
                transform: translateX(-50%);
                display: flex;
                gap: 10px;
                z-index: 4;
            }

            #now-playing.mass-playing-active .mass-playing-indicator {
                width: 8px;
                height: 8px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.22);
                transition: transform 160ms ease, background-color 160ms ease;
            }

            #now-playing.mass-playing-active .mass-playing-indicator.active {
                background: rgba(148, 199, 255, 0.95);
                transform: scale(1.45);
            }

            #now-playing.mass-playing-active[data-mass-page="queue"] .playing-info-slot {
                opacity: 0.18;
            }

            #now-playing.mass-playing-active[data-mass-page="queue"] .playing-artwork-slot {
                opacity: 0.24;
                transform: scale(0.92);
                transition: opacity 180ms ease, transform 180ms ease;
            }

            #now-playing.mass-playing-active[data-mass-page="artist"] .playing-info-slot {
                opacity: 0.25;
            }

            #now-playing.mass-playing-active .mass-playing-queue {
                margin-top: 8px;
                display: flex;
                flex-direction: column;
                gap: 10px;
                max-height: 360px;
                overflow: hidden;
            }

            #now-playing.mass-playing-active .mass-playing-queue-item {
                display: grid;
                grid-template-columns: 28px 1fr;
                gap: 12px;
                align-items: start;
                padding: 8px 0;
                border-top: 1px solid rgba(255, 255, 255, 0.08);
            }

            #now-playing.mass-playing-active .mass-playing-queue-item:first-child {
                border-top: none;
                padding-top: 0;
            }

            #now-playing.mass-playing-active .mass-playing-queue-index {
                font: 600 13px/1.4 Arial, sans-serif;
                color: rgba(122, 153, 203, 0.74);
                text-align: right;
            }

            #now-playing.mass-playing-active .mass-playing-queue-title {
                font: 500 15px/1.32 Arial, sans-serif;
                color: #ffffff;
            }

            #now-playing.mass-playing-active .mass-playing-queue-subtitle {
                margin-top: 3px;
                font: 400 12px/1.45 Arial, sans-serif;
                color: rgba(207, 218, 236, 0.72);
            }

            #now-playing.mass-playing-active .mass-playing-queue-item.is-current .mass-playing-queue-title {
                color: #9ed1ff;
            }
        `;
        document.head.appendChild(style);
    }

    function ensureOverlay(container) {
        let overlay = container.querySelector('.mass-playing-overlay');
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.className = 'mass-playing-overlay';
        overlay.innerHTML = `
            <div class="mass-playing-panel">
                <div class="mass-playing-kicker">Music</div>
                <div class="mass-playing-heading">—</div>
                <div class="mass-playing-copy">—</div>
                <div class="mass-playing-meta"></div>
                <div class="mass-playing-queue" hidden></div>
            </div>
            <div class="mass-playing-indicators">
                <span class="mass-playing-indicator" data-page="now"></span>
                <span class="mass-playing-indicator" data-page="artist"></span>
                <span class="mass-playing-indicator" data-page="queue"></span>
            </div>
        `;
        container.classList.add('mass-playing-active');
        container.appendChild(overlay);
        return overlay;
    }

    function updateBaseView(container, data) {
        const titleEl = container.querySelector('.media-view-title');
        const artistEl = container.querySelector('.media-view-artist');
        const albumEl = container.querySelector('.media-view-album');
        const title = data?.title || '—';
        const artist = data?.artist || '—';
        const album = data?.album || '—';

        if (typeof window.crossfadeText === 'function') {
            window.crossfadeText(titleEl, title);
            window.crossfadeText(artistEl, artist);
            window.crossfadeText(albumEl, album);
        } else {
            if (titleEl) titleEl.textContent = title;
            if (artistEl) artistEl.textContent = artist;
            if (albumEl) albumEl.textContent = album;
        }

        const artEl = container.querySelector('.playing-artwork');
        if (artEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artEl, data?.artwork || '', 'noArtwork');
        } else if (artEl && data?.artwork) {
            artEl.src = data.artwork;
        }

        const backFace = container.querySelector('.playing-back');
        const backImg = container.querySelector('.playing-artwork-back');
        if (backFace && backImg) {
            if (data?.back_artwork) {
                backImg.src = data.back_artwork;
                backFace.style.display = '';
            } else if (!backFace.querySelector('.cd-back-tracklist')) {
                backFace.style.display = 'none';
            }
        }
    }

    function currentPageId() {
        return PAGE_IDS[currentPageIndex] || 'now';
    }

    function renderQueueList(queueEl) {
        if (!queueEl) return;
        queueEl.innerHTML = '';

        if (queueState.loading && !queueState.items.length) {
            queueEl.innerHTML = '<div class="mass-playing-copy">Loading the active queue…</div>';
            return;
        }

        if (queueState.error) {
            queueEl.innerHTML = `<div class="mass-playing-copy">${queueState.error}</div>`;
            return;
        }

        if (!queueState.items.length) {
            queueEl.innerHTML = '<div class="mass-playing-copy">The queue is empty right now.</div>';
            return;
        }

        queueState.items.slice(0, 9).forEach((item, index) => {
            const row = document.createElement('div');
            row.className = 'mass-playing-queue-item';
            const subtitle = item.subtitle || '';
            if (item.isCurrent) row.classList.add('is-current');
            row.innerHTML = `
                <div class="mass-playing-queue-index">${index + 1}</div>
                <div>
                    <div class="mass-playing-queue-title">${item.title || 'Queued Item'}</div>
                    <div class="mass-playing-queue-subtitle">${subtitle || '&nbsp;'}</div>
                </div>
            `;
            queueEl.appendChild(row);
        });
    }

    function renderOverlay(container) {
        if (!container) return;
        const overlay = ensureOverlay(container);
        const panel = overlay.querySelector('.mass-playing-panel');
        const kickerEl = overlay.querySelector('.mass-playing-kicker');
        const headingEl = overlay.querySelector('.mass-playing-heading');
        const copyEl = overlay.querySelector('.mass-playing-copy');
        const metaEl = overlay.querySelector('.mass-playing-meta');
        const queueEl = overlay.querySelector('.mass-playing-queue');
        const pageId = currentPageId();

        container.dataset.massPage = pageId;
        overlay.querySelectorAll('.mass-playing-indicator').forEach((node) => {
            node.classList.toggle('active', node.dataset.page === pageId);
        });

        if (!panel || !kickerEl || !headingEl || !copyEl || !metaEl || !queueEl) return;

        if (pageId === 'now') {
            queueEl.hidden = true;
            kickerEl.textContent = 'Now Playing';
            headingEl.textContent = lastMedia.title || '—';
            copyEl.textContent = [lastMedia.artist || '', lastMedia.album || '']
                .filter(Boolean)
                .join('\n') || 'Select something from the library to start playback.';
            metaEl.textContent = lastMedia.state ? `State: ${String(lastMedia.state).toUpperCase()}` : '';
            return;
        }

        if (pageId === 'artist') {
            queueEl.hidden = true;
            kickerEl.textContent = 'Artist';
            headingEl.textContent = artistState.name || lastMedia.artist || 'Unknown Artist';
            if (artistState.loading && !artistState.bio) {
                copyEl.textContent = 'Loading artist biography…';
            } else if (artistState.bio) {
                copyEl.textContent = artistState.bio;
            } else if (artistState.error) {
                copyEl.textContent = artistState.error;
            } else {
                copyEl.textContent = 'No biography is available for the current artist.';
            }
            metaEl.textContent = lastMedia.title ? `Track: ${lastMedia.title}` : '';
            return;
        }

        queueEl.hidden = false;
        kickerEl.textContent = 'Queue';
        headingEl.textContent = 'Active Queue';
        copyEl.textContent = queueState.loading && !queueState.items.length
            ? 'Fetching the current queue…'
            : (queueState.error || '');
        metaEl.textContent = queueState.items.length
            ? `${queueState.items.length} item${queueState.items.length === 1 ? '' : 's'}`
            : '';
        renderQueueList(queueEl);
    }

    async function refreshArtistInfo(force = false) {
        if (!mountedContainer) return;
        const artistKey = normalizeArtistKey(lastMedia.artist);
        if (!artistKey) {
            resetArtistState('');
            renderOverlay(mountedContainer);
            return;
        }
        if (!force && artistState.key === artistKey && (artistState.loading || artistState.bio || artistState.error)) {
            return;
        }

        const requestId = ++artistRequestId;
        artistState = {
            key: artistKey,
            loading: true,
            error: '',
            bio: '',
            name: lastMedia.artist || '',
        };
        renderOverlay(mountedContainer);

        try {
            const response = await fetch(`${getServiceUrlSafe()}/artist_bio`, { cache: 'no-store' });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            if (requestId !== artistRequestId) return;

            if (!response.ok || !payload || payload.state === 'error') {
                artistState.loading = false;
                artistState.error = 'Unable to load artist biography right now.';
            } else {
                const responseName = String(payload.name || lastMedia.artist || '').trim();
                artistState = {
                    key: artistKey,
                    loading: false,
                    error: '',
                    bio: String(payload.bio || '').trim(),
                    name: responseName || lastMedia.artist || '',
                };
            }
        } catch (error) {
            if (requestId !== artistRequestId) return;
            artistState.loading = false;
            artistState.error = 'Unable to load artist biography right now.';
        } finally {
            if (requestId === artistRequestId && mountedContainer) {
                renderOverlay(mountedContainer);
            }
        }
    }

    async function refreshNowPlaying(force = false) {
        if (!mountedContainer) return;

        const requestId = ++nowPlayingRequestId;
        try {
            const response = await fetch(`${getServiceUrlSafe()}/now_playing`, { cache: 'no-store' });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            if (requestId !== nowPlayingRequestId) return;
            if (!response.ok || !payload || payload.state === 'error' || payload.state === 'empty') {
                return;
            }

            const media = {
                title: String(payload.title || '').trim(),
                artist: String(payload.artist || '').trim(),
                album: String(payload.album || '').trim(),
                artwork: String(payload.artwork || '').trim(),
                state: String(payload.state || '').trim() || lastMedia.state || 'unknown',
            };

            if (hasMeaningfulMedia(media)) {
                applyMediaSnapshot(media, { syncSource: true });
            } else if (media.state && media.state !== lastMedia.state) {
                lastMedia = Object.assign({}, lastMedia, { state: media.state });
                if (mountedContainer) {
                    updateBaseView(mountedContainer, lastMedia);
                    renderOverlay(mountedContainer);
                }
            } else if (force && mountedContainer) {
                renderOverlay(mountedContainer);
            }
        } catch (error) {
            if (requestId !== nowPlayingRequestId) return;
        }
    }

    async function refreshQueue(force = false) {
        if (!mountedContainer) return;

        const requestId = ++queueRequestId;
        queueState.loading = true;
        queueState.error = '';
        renderOverlay(mountedContainer);

        try {
            const response = await fetch(`${getServiceUrlSafe()}/queue`, { cache: 'no-store' });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            if (requestId !== queueRequestId) return;

            if (!response.ok || !payload || payload.state === 'error') {
                queueState.items = [];
                queueState.error = 'Unable to load the active queue right now.';
            } else {
                const tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
                queueState.items = tracks.map((track) => {
                    const artistText = String(track?.artist || '').trim();
                    const isCurrent = artistText.toLowerCase().startsWith('now playing');
                    const subtitle = artistText.replace(/^Now Playing\s*-\s*/i, '').trim();
                    return {
                        title: String(track?.name || 'Queued Item'),
                        subtitle,
                        isCurrent,
                    };
                });
                queueState.error = '';
            }
        } catch (error) {
            if (requestId !== queueRequestId) return;
            queueState.items = [];
            queueState.error = 'Unable to load the active queue right now.';
        } finally {
            if (requestId === queueRequestId) {
                queueState.loading = false;
                renderOverlay(mountedContainer);
            }
        }
    }

    function cyclePage(data) {
        const now = Date.now();
        if (now - lastPageCycleAt < PAGE_CYCLE_COOLDOWN_MS) return true;
        lastPageCycleAt = now;
        const direction = String(data?.direction || 'clock').toLowerCase();
        const delta = direction === 'counter' ? -1 : 1;
        currentPageIndex = (currentPageIndex + delta + PAGE_IDS.length) % PAGE_IDS.length;
        if (mountedContainer) {
            renderOverlay(mountedContainer);
            if (currentPageId() === 'artist') {
                void refreshArtistInfo(false);
            }
            if (currentPageId() === 'queue') {
                void refreshQueue(true);
            }
        }
        return true;
    }

    function scheduleQueueRefresh() {
        if (queueTimer) clearInterval(queueTimer);
        queueTimer = setInterval(() => {
            void refreshQueue(false);
        }, QUEUE_REFRESH_MS);
    }

    function scheduleNowPlayingRefresh() {
        if (nowPlayingTimer) clearInterval(nowPlayingTimer);
        nowPlayingTimer = setInterval(() => {
            void refreshNowPlaying(false);
        }, NOW_PLAYING_REFRESH_MS);
    }

    return {
        onMount(container) {
            ensureStyles();
            mountedContainer = container;
            currentPageIndex = 0;
            lastMedia = {
                title: 'â€”',
                artist: 'â€”',
                album: 'â€”',
                artwork: '',
                state: 'loading',
            };
            resetArtistState(normalizeArtistKey(lastMedia.artist));
            syncUiStoreSource();
            ensureOverlay(container);
            updateBaseView(container, lastMedia);
            renderOverlay(container);
            scheduleQueueRefresh();
            scheduleNowPlayingRefresh();
            void refreshNowPlaying(true);
            void refreshQueue(true);
        },

        onUpdate(container, data) {
            mountedContainer = container;
            ensureOverlay(container);
            updateBaseView(container, lastMedia);
            renderOverlay(container);
            if (currentPageId() === 'artist') {
                void refreshArtistInfo(false);
            }
            if (currentPageId() === 'queue') {
                void refreshQueue(false);
            }
            void refreshNowPlaying(false);
        },

        onRemove(container) {
            if (queueTimer) {
                clearInterval(queueTimer);
                queueTimer = null;
            }
            if (nowPlayingTimer) {
                clearInterval(nowPlayingTimer);
                nowPlayingTimer = null;
            }
            mountedContainer = null;
            currentPageIndex = 0;
            artistRequestId += 1;
            nowPlayingRequestId += 1;
            resetArtistState('');
            const overlay = container?.querySelector('.mass-playing-overlay');
            if (overlay) overlay.remove();
            if (container) {
                container.classList.remove('mass-playing-active');
                container.removeAttribute('data-mass-page');
            }
        },

        cyclePage,
        refreshQueue(force = false) {
            return refreshQueue(force);
        },
        refreshNowPlaying(force = false) {
            return refreshNowPlaying(force);
        },
    };
})();

const _massController = (() => {
    function currentRoute() {
        return window.uiStore?.currentRoute || '';
    }

    function isPlayingRoute() {
        return currentRoute() === 'menu/playing';
    }

    function sendToIframe(type, data) {
        if (!window.IframeMessenger) {
            console.error('[MASS UI] IframeMessenger is missing!');
            return false;
        }
        return IframeMessenger.sendToRoute('menu/mass', type, data);
    }

    async function sendTransport(command) {
        const serviceUrl = (typeof getServiceUrl === 'function')
            ? getServiceUrl('massServiceUrl', 8783)
            : 'http://localhost:8783';
        try {
            const response = await fetch(`${serviceUrl}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command }),
            });
            if (!response.ok) {
                console.warn('[MASS UI] Transport command failed:', command, response.status);
                return false;
            }
            setTimeout(() => {
                void _massPlayingPreset.refreshNowPlaying(true);
                void _massPlayingPreset.refreshQueue(true);
            }, 350);
            return true;
        } catch (error) {
            console.warn('[MASS UI] Transport command error:', command, error);
            return false;
        }
    }

    function handlePlayingButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (normalized === 'left') {
            void sendTransport('transport_previous');
            return true;
        }
        if (normalized === 'right') {
            void sendTransport('transport_next');
            return true;
        }
        if (normalized === 'go_long' || normalized === 'go_hold') {
            void sendTransport('transport_stop');
            return true;
        }
        if (normalized === 'go') {
            void sendTransport('transport_toggle');
            return true;
        }
        return true;
    }

    return {
        get isActive() { return true; },

        updateMetadata(data) {
            const container = document.getElementById('now-playing');
            if (container && window.uiStore?.activeSource === 'mass' && window.uiStore.currentRoute === 'menu/playing') {
                _massPlayingPreset.onUpdate(container, data || window.uiStore.mediaInfo || {});
            }
        },

        handleNavEvent(data) {
            if (isPlayingRoute()) {
                return _massPlayingPreset.cyclePage(data);
            }
            return sendToIframe('nav', { data });
        },

        handleButton(button) {
            if (isPlayingRoute()) {
                return handlePlayingButton(button);
            }
            return sendToIframe('button', { button });
        },
    };
})();

window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.mass = {
    controller: _massController,
    playing: _massPlayingPreset,
    item: { title: 'MUSIC', path: 'menu/mass' },
    after: 'menu/playing',
    view: {
        title: 'MUSIC',
        content: '<div id="mass-container" style="width:100%;height:100%;"></div>',
        containerId: 'mass-container',
        preloadId: 'preload-mass',
        iframeSrc: 'softarc/mass.html'
    },

    onAdd() {},

    onMount() {
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/mass', 'preload-mass');
        }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/mass');
        }
    },
};
