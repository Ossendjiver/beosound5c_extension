/**
 * MASS Source Preset
 */
const _massPlayingPreset = (() => {
    const PAGE_IDS = ['now', 'options'];
    const QUEUE_REFRESH_MS = 3000;
    const NOW_PLAYING_REFRESH_MS = 2000;
    const PAGE_CYCLE_COOLDOWN_MS = 520;
    const YOUTUBE_PREF_KEY = 'bs5c.youtubeVideosEnabled';
    const DEFAULT_TRANSFER_TARGETS = [
        { id: '08a2eca2-247c-96fe-7998-7baddf01b2b1', name: 'Cuisine' },
        { id: '64ad9554-d5e6-116c-8b0b-069c1f0b7885', name: 'Bedroom Mini' },
        { id: 'up50411c87e1c0', name: 'Link' },
    ];
    const QUEUE_ACTION_ITEMS = [
        { id: 'play_now', title: 'Play Now' },
        { id: 'play_next', title: 'Play Next' },
        { id: 'start_radio', title: 'Start Radio' },
        { id: 'show_information', title: 'Show Information' },
        { id: 'goto', title: 'Go to Artist/Album/Genre' },
        { id: 'remove', title: 'Remove' },
    ];
    const QUEUE_DESTINATION_ITEMS = [
        { id: 'artist', title: 'Go to Artist' },
        { id: 'album', title: 'Go to Album' },
        { id: 'genre', title: 'Go to Genre' },
    ];
    let currentPageIndex = 0;
    let mountedContainer = null;
    let queueTimer = null;
    let nowPlayingTimer = null;
    let queueRequestId = 0;
    let artistRequestId = 0;
    let nowPlayingRequestId = 0;
    let queueDetailRequestId = 0;
    let lastPageCycleAt = 0;
    let lastRouterResyncTrackKey = '';
    let lastRouterResyncAt = 0;
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
        currentIndex: -1,
        queueId: '',
        playerId: '',
        queueName: '',
        state: 'idle',
    };
    let queueUi = createQueueUiState();
    let artistState = {
        key: '',
        loading: false,
        error: '',
        bio: '',
        name: '',
    };
    let transferState = {
        selectedIndex: 0,
        focusIndex: 0,
        sending: false,
        message: '',
        error: '',
        targets: [],
    };

    function createQueueUiState() {
        return {
            open: false,
            mode: 'list',
            selectedIndex: 0,
            pendingReveal: false,
            actionIndex: 0,
            destinationIndex: 0,
            busy: false,
            message: '',
            detailItemKey: '',
            detailLoading: false,
            detailError: '',
            detailPayload: null,
        };
    }

    function getServiceUrlSafe() {
        return (typeof getServiceUrl === 'function')
            ? getServiceUrl('massServiceUrl', 8783)
            : 'http://localhost:8783';
    }

    function youtubeVideosEnabled() {
        if (window.MusicVideoPreference) {
            return window.MusicVideoPreference.enabled !== false;
        }
        try {
            return localStorage.getItem(YOUTUBE_PREF_KEY) !== 'false';
        } catch (error) {
            return true;
        }
    }

    function setYoutubeVideosEnabled(enabled) {
        const normalized = enabled !== false;
        if (window.MusicVideoPreference?.setEnabled) {
            return window.MusicVideoPreference.setEnabled(normalized);
        }
        try {
            localStorage.setItem(YOUTUBE_PREF_KEY, normalized ? 'true' : 'false');
        } catch (error) {}
        document.dispatchEvent(new CustomEvent('bs5c:music-video-preference', {
            detail: { enabled: normalized }
        }));
        return normalized;
    }

    function toggleYoutubeVideos() {
        const enabled = setYoutubeVideosEnabled(!youtubeVideosEnabled());
        transferState.message = `YouTube videos ${enabled ? 'enabled' : 'disabled'}`;
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function resolveArtworkUrl(value) {
        const url = String(value || '').trim();
        if (!url || !url.startsWith('/art/')) return url;
        return `${String(getServiceUrlSafe()).replace(/\/$/, '')}${url}`;
    }

    function isMeaningfulText(value) {
        const text = String(value || '').trim();
        return Boolean(text && !['—', '–', '-', 'â€”'].includes(text));
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

    function normalizeTrackKeyPart(value) {
        return String(value || '')
            .trim()
            .replace(/\s+/g, ' ')
            .toLowerCase();
    }

    function buildTrackKey(data) {
        if (!data || typeof data !== 'object') return '';
        return [
            normalizeTrackKeyPart(data.title),
            normalizeTrackKeyPart(data.artist),
            normalizeTrackKeyPart(data.album),
        ].filter(Boolean).join('||');
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
        if (
            window.uiStore.activePlayingPreset !== _massPlayingPreset
            && typeof window.uiStore.setActivePlayingPreset === 'function'
        ) {
            window.uiStore.setActivePlayingPreset('mass');
        }
    }

    function buildSharedMediaSnapshot(data) {
        return {
            title: data?.title || '—',
            artist: data?.artist || '—',
            album: data?.album || '—',
            artwork: data?.artwork || '',
            back_artwork: data?.back_artwork || '',
            state: data?.state || 'unknown',
            position: data?.position || '0:00',
            duration: data?.duration || '0:00',
            source_id: 'mass',
        };
    }

    function seedMediaFromUiStore() {
        const snapshot = buildSharedMediaSnapshot(window.uiStore?.mediaInfo || {});
        return hasMeaningfulMedia(snapshot) ? snapshot : null;
    }

    function publishSharedMediaSnapshot(data, reason = 'mass_now_playing') {
        if (!window.uiStore) return;
        const snapshot = buildSharedMediaSnapshot(data);
        if (typeof window.uiStore.handleMediaUpdate === 'function') {
            window.uiStore.handleMediaUpdate(snapshot, reason);
        } else {
            window.uiStore.mediaInfo = snapshot;
        }
    }

    async function requestRouterResyncForTrack(media) {
        const trackKey = buildTrackKey(media);
        if (!trackKey || !window.uiStore || window.uiStore.activeSource !== 'mass') return false;

        const now = Date.now();
        if (trackKey === lastRouterResyncTrackKey && (now - lastRouterResyncAt) < 15000) {
            return false;
        }

        lastRouterResyncTrackKey = trackKey;
        lastRouterResyncAt = now;

        try {
            const routerBase = String(window.AppConfig?.routerUrl || 'http://localhost:8770').replace(/\/$/, '');
            await fetch(`${routerBase}/router/resync`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: 'mass', reason: 'mass_now_playing_poll' }),
            });
            return true;
        } catch (error) {
            return false;
        }
    }

    function applyMediaSnapshot(data, options = {}) {
        const normalized = Object.assign({}, data || {});
        const publishToUiStore = options.publishToUiStore === true;
        const publishReason = options.publishReason || 'mass_now_playing';
        if (normalized.artwork) normalized.artwork = resolveArtworkUrl(normalized.artwork);
        if (normalized.back_artwork) normalized.back_artwork = resolveArtworkUrl(normalized.back_artwork);
        ['title', 'artist', 'album'].forEach((field) => {
            if (field in normalized && !isMeaningfulText(normalized[field])) {
                delete normalized[field];
            }
        });
        const hasUpdate = hasMeaningfulMedia(normalized)
            || Boolean(String(normalized.state || '').trim())
            || Boolean(String(normalized.back_artwork || '').trim());
        if (!hasUpdate) return false;
        const previousArtistKey = normalizeArtistKey(lastMedia.artist);
        lastMedia = Object.assign({}, lastMedia, normalized);
        const nextArtistKey = normalizeArtistKey(lastMedia.artist);
        if (nextArtistKey !== previousArtistKey) {
            resetArtistState(nextArtistKey);
        }
        if (publishToUiStore) {
            publishSharedMediaSnapshot(lastMedia, publishReason);
        } else if (window.uiStore) {
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
        const artwork = resolveArtworkUrl(track.image || track.artwork || '');
        const media = {
            title: String(track.name || track.title || '').trim(),
            artist,
            album: String(track.album || '').trim(),
            artwork,
            state: 'playing',
        };
        return hasMeaningfulMedia(media) ? media : null;
    }

    function queueItemKey(item) {
        return String(item?.id || `queue-index-${item?.index ?? 0}`).trim();
    }

    function selectedQueueItem() {
        return queueState.items[queueUi.selectedIndex] || null;
    }

    function requestQueueReveal() {
        queueUi.pendingReveal = true;
    }

    function clearQueueMessage() {
        queueUi.message = '';
    }

    function clearQueueDetail() {
        queueDetailRequestId += 1;
        queueUi.detailItemKey = '';
        queueUi.detailLoading = false;
        queueUi.detailError = '';
        queueUi.detailPayload = null;
    }

    function closeQueueOverlay() {
        queueUi.open = false;
        queueUi.mode = 'list';
        queueUi.pendingReveal = false;
        queueUi.busy = false;
        clearQueueMessage();
        clearQueueDetail();
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function openQueueOverlay() {
        queueUi.open = true;
        queueUi.mode = 'list';
        queueUi.actionIndex = 0;
        queueUi.destinationIndex = 0;
        clearQueueMessage();
        clearQueueDetail();
        requestQueueReveal();
        if (mountedContainer) renderOverlay(mountedContainer);
        void refreshQueue(true);
        return true;
    }

    function queuePageId() {
        if (!queueUi.open) return '';
        if (queueUi.mode === 'context') return 'queue-menu';
        if (queueUi.mode === 'goto') return 'queue-goto';
        if (queueUi.mode === 'detail') return 'queue-detail';
        return 'queue';
    }

    function queueStatusText() {
        if (queueUi.busy) return 'Working on the queue...';
        if (queueUi.message) return queueUi.message;
        if (queueState.error) return queueState.error;
        if (queueState.loading && !queueState.items.length) return 'Loading the active queue...';
        return '';
    }

    function activeQueueHeading() {
        const name = String(queueState.queueName || '').trim();
        return name ? `Active Queue — ${name}` : 'Active Queue';
    }

    function queueMetaText() {
        const queuePrefix = queueState.queueName ? `${queueState.queueName}   ` : '';
        if (queueUi.mode === 'context') return `${queuePrefix}GO Select   RIGHT Back`;
        if (queueUi.mode === 'goto') return `${queuePrefix}GO Open   RIGHT Back`;
        if (queueUi.mode === 'detail') return `${queuePrefix}RIGHT Back`;
        if (!queueState.items.length) return 'LEFT Transfer   RIGHT Close';
        return 'LEFT Transfer   GO Actions   RIGHT Close';
    }

    function queueCommandPayload(item) {
        const index = Number.isFinite(Number(item?.index)) ? Number(item.index) : queueUi.selectedIndex;
        const id = String(item?.id || '').trim();
        const uri = String(item?.uri || '').trim();
        return {
            source_queue_id: queueState.queueId,
            queue_id: queueState.queueId,
            target_player_id: queueState.playerId || queueState.queueId,
            audio_target_id: queueState.playerId || queueState.queueId,
            id,
            item_id: id,
            queue_item_id: id,
            index,
            position: index,
            url: uri,
            play_url: uri,
            title: String(item?.title || '').trim(),
            artist: String(item?.artist || '').trim(),
            album: String(item?.album || '').trim(),
        };
    }

    async function postQueueCommand(command, item, options = {}) {
        if (queueUi.busy || !item) return true;
        const closeOnSuccess = options.closeOnSuccess === true;
        const successMessage = String(options.successMessage || '').trim();

        queueUi.busy = true;
        queueUi.message = successMessage || 'Working...';
        if (mountedContainer) renderOverlay(mountedContainer);

        try {
            const response = await fetch(`${getServiceUrlSafe()}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    command,
                    ...(window.PlaybackTargets?.payloadFor?.('mass') || {}),
                    ...queueCommandPayload(item),
                }),
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            const failed = !response.ok || !payload || payload.state === 'error' || payload.status === 'error';
            if (failed) {
                const reason = String(payload?.reason || '').trim();
                const reasonMessages = {
                    queue_item_not_found: 'That item is no longer in the active queue',
                    queue_item_already_buffered: 'That item is already playing or buffered',
                    play_index_failed: 'Could not start that queue item',
                    move_failed: 'Could not move that item in the active queue',
                    remove_failed: 'Could not remove that item from the active queue',
                };
                queueUi.message = reasonMessages[reason] || 'Queue update failed';
                if (mountedContainer) renderOverlay(mountedContainer);
                return true;
            }
            queueUi.message = successMessage || 'Queue updated';
            if (closeOnSuccess) {
                closeQueueOverlay();
            } else if (mountedContainer) {
                renderOverlay(mountedContainer);
            }
            setTimeout(() => {
                void refreshQueue(true);
                void refreshNowPlaying(true);
            }, 220);
        } catch (error) {
            queueUi.message = 'Queue update failed';
            if (mountedContainer) renderOverlay(mountedContainer);
        } finally {
            queueUi.busy = false;
            if (mountedContainer) renderOverlay(mountedContainer);
        }
        return true;
    }

    function buildQueueDetailFallback(item) {
        const artist = String(item?.artist || '').trim();
        const album = String(item?.album || '').trim();
        return {
            title: String(item?.title || 'Queue Item').trim() || 'Queue Item',
            subtitle: [artist, album].filter(Boolean).join(' • '),
            image: resolveArtworkUrl(item?.artwork || ''),
            facts: [],
            description_label: '',
            description: 'Extra information is not available for this queue item yet.',
        };
    }

    async function openQueueDetail(item) {
        if (!item) return false;
        queueUi.open = true;
        queueUi.mode = 'detail';
        queueUi.detailItemKey = queueItemKey(item);
        queueUi.detailLoading = true;
        queueUi.detailError = '';
        queueUi.detailPayload = buildQueueDetailFallback(item);
        if (mountedContainer) renderOverlay(mountedContainer);

        const uri = String(item?.uri || '').trim();
        if (!uri) {
            queueUi.detailLoading = false;
            queueUi.detailError = 'No item information is available for this queue entry.';
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        }

        const requestId = ++queueDetailRequestId;
        try {
            const response = await fetch(`${getServiceUrlSafe()}/item_info?uri=${encodeURIComponent(uri)}`, { cache: 'no-store' });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            if (requestId !== queueDetailRequestId || queueUi.detailItemKey !== queueItemKey(item)) return true;
            if (!response.ok || !payload || payload.state === 'error') {
                throw new Error(payload?.error || `item_info_${response.status}`);
            }
            queueUi.detailPayload = {
                title: String(payload.title || item.title || 'Queue Item').trim(),
                subtitle: String(payload.subtitle || '').trim(),
                image: resolveArtworkUrl(payload.image || item.artwork || ''),
                facts: Array.isArray(payload.facts) ? payload.facts : [],
                description_label: String(payload.description_label || '').trim(),
                description: String(payload.description || '').trim(),
            };
            queueUi.detailError = '';
        } catch (error) {
            if (requestId !== queueDetailRequestId || queueUi.detailItemKey !== queueItemKey(item)) return true;
            queueUi.detailPayload = buildQueueDetailFallback(item);
            queueUi.detailError = 'Unable to load the latest details for this queue item.';
        } finally {
            if (requestId === queueDetailRequestId && queueUi.detailItemKey === queueItemKey(item)) {
                queueUi.detailLoading = false;
                if (mountedContainer) renderOverlay(mountedContainer);
            }
        }
        return true;
    }

    function navigateQueueDestination(kind, item) {
        const normalized = String(kind || '').trim().toLowerCase();
        if (!item || !normalized) return false;
        if (normalized === 'genre') {
            queueUi.message = 'Genre navigation is not available in MASS yet.';
            queueUi.mode = 'goto';
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        }
        const payload = {
            kind: normalized,
            item: {
                title: String(item.title || '').trim(),
                artist: String(item.artist || '').trim(),
                album: String(item.album || '').trim(),
                uri: String(item.uri || '').trim(),
                artwork: String(item.artwork || '').trim(),
            },
        };
        closeQueueOverlay();
        window.uiStore?.navigateToView?.('menu/mass');
        window.IframeMessenger?.sendToRoute?.('menu/mass', 'queue-focus', payload);
        setTimeout(() => window.IframeMessenger?.sendToRoute?.('menu/mass', 'queue-focus', payload), 90);
        setTimeout(() => window.IframeMessenger?.sendToRoute?.('menu/mass', 'queue-focus', payload), 260);
        return true;
    }

    function ensureStyles() {
        if (document.getElementById('mass-playing-preset-style')) return;
        const style = document.createElement('style');
        style.id = 'mass-playing-preset-style';
        style.textContent = `
            #now-playing.mass-playing-active {
                position: relative;
                overflow: hidden;
            }

            #now-playing.mass-playing-active.immersive-active {
                overflow: visible;
            }

            #now-playing.mass-playing-active .mass-playing-overlay {
                position: absolute;
                inset: 0;
                display: flex;
                align-items: center;
                justify-content: flex-end;
                pointer-events: none;
                z-index: 3;
            }

            #now-playing.mass-playing-active .mass-playing-panel {
                position: relative;
                z-index: 1;
                width: clamp(420px, 48vw, 560px);
                max-height: 72vh;
                margin: 0 40px 0 auto;
                padding: 16px 18px 16px;
                display: flex;
                flex-direction: column;
                justify-content: flex-start;
                background: rgba(10, 10, 10, 0.84);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 24px;
                box-shadow: 0 22px 48px rgba(0, 0, 0, 0.42);
                backdrop-filter: blur(16px);
                opacity: 0;
                transform: translateY(-8px);
                transition: opacity 160ms ease, transform 160ms ease;
                overflow: hidden;
                pointer-events: auto;
            }

            #now-playing.mass-playing-active .mass-transfer-layer {
                position: absolute;
                z-index: 4;
                top: 50%;
                right: 18px;
                width: clamp(300px, 34vw, 400px);
                max-height: 58vh;
                padding: 16px 18px;
                display: flex;
                flex-direction: column;
                background: rgba(7, 9, 13, 0.96);
                border: 1px solid rgba(176, 204, 242, 0.28);
                border-radius: 22px;
                box-shadow: 0 26px 62px rgba(0, 0, 0, 0.68);
                backdrop-filter: blur(20px);
                transform: translateY(calc(-50% - 10px));
                overflow: hidden;
                pointer-events: auto;
            }

            #now-playing.mass-playing-active .mass-transfer-layer[hidden] {
                display: none;
            }

            #now-playing.mass-playing-active[data-mass-page="options"] .mass-playing-panel {
                opacity: 0.78;
                transform: translate(-54px, 20px) scale(0.98);
            }

            #now-playing.mass-playing-active .mass-transfer-layer-kicker {
                font: 600 11px/1.2 Arial, sans-serif;
                letter-spacing: 1.8px;
                text-transform: uppercase;
                color: rgba(255, 255, 255, 0.58);
                margin-bottom: 6px;
            }

            #now-playing.mass-playing-active .mass-transfer-layer-heading {
                font: 300 22px/1.12 Arial, sans-serif;
                color: #ffffff;
                margin-bottom: 8px;
            }

            #now-playing.mass-playing-active .mass-transfer-layer-copy {
                margin-bottom: 6px;
                font: 400 11px/1.35 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.64);
            }

            #now-playing.mass-playing-active .mass-transfer-layer-meta {
                margin-bottom: 10px;
                font: 500 11px/1.25 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.48);
                letter-spacing: 1.2px;
                text-transform: uppercase;
            }

            #now-playing.mass-playing-active[data-mass-page="queue"] .mass-playing-panel,
            #now-playing.mass-playing-active[data-mass-page="queue-menu"] .mass-playing-panel,
            #now-playing.mass-playing-active[data-mass-page="queue-goto"] .mass-playing-panel,
            #now-playing.mass-playing-active[data-mass-page="queue-detail"] .mass-playing-panel {
                opacity: 1;
                transform: translateY(0);
            }

            #now-playing.mass-playing-active .mass-playing-kicker {
                font: 600 11px/1.2 Arial, sans-serif;
                letter-spacing: 1.8px;
                text-transform: uppercase;
                color: rgba(255, 255, 255, 0.58);
                margin-bottom: 6px;
            }

            #now-playing.mass-playing-active .mass-playing-heading {
                font: 300 22px/1.12 Arial, sans-serif;
                color: #ffffff;
                margin-bottom: 8px;
            }

            #now-playing.mass-playing-active .mass-playing-copy {
                min-height: 0;
                margin-bottom: 6px;
                font: 400 11px/1.35 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.56);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            #now-playing.mass-playing-active .mass-playing-meta {
                margin-bottom: 10px;
                font: 500 11px/1.25 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.45);
                letter-spacing: 1.2px;
                text-transform: uppercase;
            }

            #now-playing.mass-playing-active .mass-playing-indicators {
                display: none;
            }

            #now-playing.mass-playing-active .mass-paused-overlay {
                position: absolute;
                inset: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 18px;
                background: rgba(0, 0, 0, 0.34);
                opacity: 0;
                transition: opacity 180ms ease;
                pointer-events: none;
                z-index: 3;
            }

            #now-playing.mass-playing-active.is-mass-paused .mass-paused-overlay {
                opacity: 1;
            }

            #now-playing.mass-playing-active .mass-paused-bar {
                width: 18px;
                height: 96px;
                border-radius: 3px;
                background: rgba(255, 255, 255, 0.88);
                box-shadow: 0 12px 36px rgba(0, 0, 0, 0.38);
            }

            #now-playing.mass-playing-active[data-mass-page="queue"] .playing-info-slot,
            #now-playing.mass-playing-active[data-mass-page="queue-menu"] .playing-info-slot,
            #now-playing.mass-playing-active[data-mass-page="queue-goto"] .playing-info-slot,
            #now-playing.mass-playing-active[data-mass-page="queue-detail"] .playing-info-slot {
                opacity: 0.18;
            }

            #now-playing.mass-playing-active[data-mass-page="queue"] .playing-artwork-slot,
            #now-playing.mass-playing-active[data-mass-page="queue-menu"] .playing-artwork-slot,
            #now-playing.mass-playing-active[data-mass-page="queue-goto"] .playing-artwork-slot,
            #now-playing.mass-playing-active[data-mass-page="queue-detail"] .playing-artwork-slot {
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
                gap: 8px;
                max-height: 52vh;
                overflow-y: auto;
                padding-right: 2px;
                pointer-events: auto;
            }

            #now-playing.mass-playing-active .mass-playing-queue::-webkit-scrollbar {
                display: none;
            }

            #now-playing.mass-playing-active .mass-playing-queue-item {
                display: grid;
                grid-template-columns: 28px 1fr;
                gap: 12px;
                align-items: start;
                padding: 8px 0;
                border-top: 1px solid rgba(255, 255, 255, 0.08);
                background: transparent;
                border-right: none;
                border-bottom: none;
                border-left: none;
                width: 100%;
                text-align: left;
                color: inherit;
                cursor: pointer;
            }

            #now-playing.mass-playing-active .mass-playing-queue-item:first-child {
                border-top: none;
                padding-top: 0;
            }

            #now-playing.mass-playing-active .mass-playing-queue-item.is-selected {
                background: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.18);
                border-radius: 14px;
                padding: 10px 12px;
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

            #now-playing.mass-playing-active .mass-playing-action-list {
                display: flex;
                flex-direction: column;
                gap: 6px;
                max-height: 52vh;
                overflow-y: auto;
                padding-right: 2px;
            }

            #now-playing.mass-playing-active .mass-playing-action-list::-webkit-scrollbar {
                display: none;
            }

            #now-playing.mass-playing-active .mass-playing-action {
                width: 100%;
                min-height: 40px;
                border: 1px solid transparent;
                border-radius: 12px;
                background: transparent;
                color: #ffffff;
                font: 500 14px/1.25 Arial, sans-serif;
                text-align: left;
                padding: 10px 12px;
                touch-action: manipulation;
            }

            #now-playing.mass-playing-active .mass-playing-action.focused {
                background: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.28);
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
            }

            #now-playing.mass-playing-active .mass-playing-detail {
                max-height: 52vh;
                overflow-y: auto;
                padding-right: 2px;
                color: #ffffff;
            }

            #now-playing.mass-playing-active .mass-playing-detail::-webkit-scrollbar {
                display: none;
            }

            #now-playing.mass-playing-active .mass-playing-detail-image {
                width: 100%;
                aspect-ratio: 1.4;
                object-fit: contain;
                border-radius: 16px;
                background: rgba(255, 255, 255, 0.04);
                margin-bottom: 12px;
            }

            #now-playing.mass-playing-active .mass-playing-detail-subtitle {
                margin-bottom: 12px;
                font: 400 13px/1.45 Arial, sans-serif;
                color: rgba(207, 218, 236, 0.82);
            }

            #now-playing.mass-playing-active .mass-playing-detail-facts {
                display: grid;
                gap: 6px;
                margin-bottom: 12px;
            }

            #now-playing.mass-playing-active .mass-playing-detail-fact {
                display: grid;
                grid-template-columns: 78px 1fr;
                gap: 12px;
                align-items: start;
            }

            #now-playing.mass-playing-active .mass-playing-detail-fact-label {
                font: 600 11px/1.25 Arial, sans-serif;
                letter-spacing: 1.1px;
                text-transform: uppercase;
                color: rgba(255, 255, 255, 0.52);
            }

            #now-playing.mass-playing-active .mass-playing-detail-fact-value,
            #now-playing.mass-playing-active .mass-playing-detail-copy {
                font: 400 13px/1.48 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.86);
            }

            #now-playing.mass-playing-active .mass-playing-detail-copy p {
                margin: 0 0 10px;
            }

            #now-playing.mass-playing-active .mass-playing-transfer {
                display: flex;
                flex-direction: column;
                gap: 4px;
                max-height: 52vh;
                overflow-y: auto;
                padding-right: 2px;
                pointer-events: auto;
            }

            #now-playing.mass-playing-active .mass-playing-transfer::-webkit-scrollbar {
                display: none;
            }

            #now-playing.mass-playing-active .mass-transfer-option,
            #now-playing.mass-playing-active .mass-youtube-toggle {
                width: 100%;
                min-height: 38px;
                border: 1px solid transparent;
                border-radius: 12px;
                background: transparent;
                color: #ffffff;
                font: 500 14px/1.2 Arial, sans-serif;
                text-align: left;
                padding: 8px 10px;
                touch-action: manipulation;
            }

            #now-playing.mass-playing-active .mass-transfer-option.active {
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
            }

            #now-playing.mass-playing-active .mass-transfer-option.focused,
            #now-playing.mass-playing-active .mass-youtube-toggle.focused {
                background: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.28);
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
            }

            #now-playing.mass-playing-active .mass-youtube-toggle {
                display: grid;
                grid-template-columns: 1fr 52px;
                gap: 12px;
                align-items: center;
            }

            #now-playing.mass-playing-active .mass-youtube-toggle.active {
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
            }

            #now-playing.mass-playing-active .mass-youtube-switch {
                position: relative;
                width: 44px;
                height: 24px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.18);
                justify-self: end;
            }

            #now-playing.mass-playing-active .mass-youtube-switch::after {
                content: "";
                position: absolute;
                left: 3px;
                top: 3px;
                width: 18px;
                height: 18px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.9);
                transition: transform 160ms ease;
            }

            #now-playing.mass-playing-active .mass-youtube-toggle.active .mass-youtube-switch {
                background: rgba(255, 255, 255, 0.36);
            }

            #now-playing.mass-playing-active .mass-youtube-toggle.active .mass-youtube-switch::after {
                transform: translateX(20px);
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
            <div class="mass-paused-overlay" hidden>
                <span class="mass-paused-bar"></span>
                <span class="mass-paused-bar"></span>
            </div>
            <div class="mass-playing-panel">
                <div class="mass-playing-kicker">Music</div>
                <div class="mass-playing-heading">—</div>
                <div class="mass-playing-copy">—</div>
                <div class="mass-playing-meta"></div>
                <div class="mass-playing-queue" hidden></div>
            </div>
            <div class="mass-transfer-layer" hidden>
                <div class="mass-transfer-layer-kicker">Playing</div>
                <div class="mass-transfer-layer-heading">Transfer Queue</div>
                <div class="mass-transfer-layer-copy" hidden></div>
                <div class="mass-transfer-layer-meta"></div>
                <div class="mass-playing-transfer"></div>
            </div>
            <div class="mass-playing-indicators">
                <span class="mass-playing-indicator" data-page="now"></span>
                <span class="mass-playing-indicator" data-page="options"></span>
            </div>
        `;
        overlay.addEventListener('click', (event) => {
            const queueItem = event.target.closest('[data-queue-index]');
            if (queueItem) {
                queueUi.selectedIndex = Math.max(0, Number(queueItem.dataset.queueIndex) || 0);
                queueUi.mode = 'list';
                requestQueueReveal();
                clearQueueMessage();
                if (mountedContainer) renderOverlay(mountedContainer);
                return;
            }
            const queueAction = event.target.closest('[data-queue-action]');
            if (queueAction) {
                queueUi.actionIndex = Math.max(0, Number(queueAction.dataset.queueActionIndex) || 0);
                if (mountedContainer) renderOverlay(mountedContainer);
                void activateQueueAction();
                return;
            }
            const queueDestination = event.target.closest('[data-queue-destination]');
            if (queueDestination) {
                queueUi.destinationIndex = Math.max(0, Number(queueDestination.dataset.queueDestinationIndex) || 0);
                if (mountedContainer) renderOverlay(mountedContainer);
                void activateQueueDestination();
                return;
            }
            const youtubeToggle = event.target.closest('[data-youtube-toggle]');
            if (youtubeToggle) {
                transferState.focusIndex = youtubeFocusIndex();
                toggleYoutubeVideos();
                if (mountedContainer) renderOverlay(mountedContainer);
                return;
            }
            const option = event.target.closest('[data-transfer-target]');
            if (option) {
                selectTransferTarget(option.dataset.transferTarget);
                void transferQueueToSelected({ closeOnSuccess: true });
                return;
            }
        });
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
            window.ArtworkManager.displayArtwork(
                artEl, data?.artwork || '', 'noArtwork', data?.artwork_candidates || []
            );
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
        if (currentPageIndex === 1 && canShowTransferOverlay()) return 'options';
        const queuePage = queuePageId();
        if (queuePage) return queuePage;
        if (!canShowTransferOverlay()) return 'now';
        return PAGE_IDS[currentPageIndex] || 'now';
    }

    function revealSelectedQueueItem(queueEl) {
        if (!queueUi.pendingReveal || !queueEl) return;
        queueUi.pendingReveal = false;
        const selectedIndexSnapshot = queueUi.selectedIndex;
        requestAnimationFrame(() => {
            const selected = queueEl.querySelector(`[data-queue-index="${selectedIndexSnapshot}"]`);
            if (!selected) return;
            const itemTop = selected.offsetTop;
            const itemBottom = itemTop + selected.offsetHeight;
            const viewportTop = queueEl.scrollTop;
            const viewportBottom = viewportTop + queueEl.clientHeight;
            const padding = Math.max(12, Math.round(selected.offsetHeight * 0.35));
            if (itemTop < viewportTop + padding) {
                queueEl.scrollTop = Math.max(0, itemTop - padding);
                return;
            }
            if (itemBottom > viewportBottom - padding) {
                queueEl.scrollTop = Math.max(0, itemBottom - queueEl.clientHeight + padding);
            }
        });
    }

    function moveQueueSelection(delta) {
        if (!queueState.items.length) return;
        const maxIndex = Math.max(0, queueState.items.length - 1);
        queueUi.selectedIndex = Math.max(0, Math.min(maxIndex, queueUi.selectedIndex + delta));
        queueUi.mode = 'list';
        clearQueueMessage();
        requestQueueReveal();
        if (mountedContainer) renderOverlay(mountedContainer);
    }

    function stepQueueActionFocus(delta) {
        const maxIndex = Math.max(0, QUEUE_ACTION_ITEMS.length - 1);
        queueUi.actionIndex = Math.max(0, Math.min(maxIndex, queueUi.actionIndex + delta));
        if (mountedContainer) renderOverlay(mountedContainer);
    }

    function stepQueueDestinationFocus(delta) {
        const maxIndex = Math.max(0, QUEUE_DESTINATION_ITEMS.length - 1);
        queueUi.destinationIndex = Math.max(0, Math.min(maxIndex, queueUi.destinationIndex + delta));
        if (mountedContainer) renderOverlay(mountedContainer);
    }

    async function activateQueueAction() {
        const action = QUEUE_ACTION_ITEMS[queueUi.actionIndex];
        const item = selectedQueueItem();
        if (!action || !item) return true;
        if (action.id === 'play_now') {
            return postQueueCommand('play_index', item, {
                successMessage: 'Playing now',
                closeOnSuccess: true,
            });
        }
        if (action.id === 'play_next') {
            return postQueueCommand('queue_play_next', item, {
                successMessage: 'Queued to play next',
            });
        }
        if (action.id === 'start_radio') {
            return postQueueCommand('play_radio', item, {
                successMessage: 'Starting radio',
                closeOnSuccess: true,
            });
        }
        if (action.id === 'show_information') {
            return openQueueDetail(item);
        }
        if (action.id === 'goto') {
            queueUi.mode = 'goto';
            queueUi.destinationIndex = 0;
            clearQueueMessage();
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        }
        if (action.id === 'remove') {
            return postQueueCommand('queue_remove', item, {
                successMessage: 'Removed from queue',
            });
        }
        return true;
    }

    function activateQueueDestination() {
        const destination = QUEUE_DESTINATION_ITEMS[queueUi.destinationIndex];
        const item = selectedQueueItem();
        if (!destination || !item) return true;
        return navigateQueueDestination(destination.id, item);
    }

    function renderQueueActionList(queueEl, items, focusIndex, attributeName) {
        if (!queueEl) return;
        queueEl.innerHTML = '';
        const list = document.createElement('div');
        list.className = 'mass-playing-action-list';
        items.forEach((entry, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'mass-playing-action';
            if (index === focusIndex) button.classList.add('focused');
            button.textContent = entry.title;
            button.dataset[attributeName] = entry.id;
            button.dataset[`${attributeName}Index`] = String(index);
            list.appendChild(button);
        });
        queueEl.appendChild(list);
        requestAnimationFrame(() => {
            list.querySelector('.focused')?.scrollIntoView({ block: 'nearest' });
        });
    }

    function renderQueueDetail(queueEl) {
        if (!queueEl) return;
        queueEl.innerHTML = '';
        const detail = document.createElement('div');
        detail.className = 'mass-playing-detail';
        const payload = queueUi.detailPayload || {};
        if (queueUi.detailLoading) {
            detail.innerHTML = '<div class="mass-playing-copy">Loading queue item information...</div>';
            queueEl.appendChild(detail);
            return;
        }
        const image = resolveArtworkUrl(payload.image || '');
        if (image) {
            const img = document.createElement('img');
            img.className = 'mass-playing-detail-image';
            img.src = image;
            img.alt = payload.title || 'Queue item artwork';
            detail.appendChild(img);
        }
        if (payload.subtitle) {
            const subtitle = document.createElement('div');
            subtitle.className = 'mass-playing-detail-subtitle';
            subtitle.textContent = payload.subtitle;
            detail.appendChild(subtitle);
        }
        const facts = Array.isArray(payload.facts) ? payload.facts.filter((entry) =>
            String(entry?.label || '').trim() && String(entry?.value || '').trim()) : [];
        if (facts.length) {
            const factsEl = document.createElement('div');
            factsEl.className = 'mass-playing-detail-facts';
            facts.forEach((entry) => {
                const row = document.createElement('div');
                row.className = 'mass-playing-detail-fact';
                row.innerHTML = `
                    <div class="mass-playing-detail-fact-label">${entry.label}</div>
                    <div class="mass-playing-detail-fact-value">${entry.value}</div>
                `;
                factsEl.appendChild(row);
            });
            detail.appendChild(factsEl);
        }
        const description = String(payload.description || '').trim();
        if (description) {
            const copy = document.createElement('div');
            copy.className = 'mass-playing-detail-copy';
            description.split(/\n{2,}/).map((entry) => entry.trim()).filter(Boolean).forEach((entry) => {
                const p = document.createElement('p');
                p.textContent = entry;
                copy.appendChild(p);
            });
            detail.appendChild(copy);
        }
        if (queueUi.detailError) {
            const error = document.createElement('div');
            error.className = 'mass-playing-copy';
            error.textContent = queueUi.detailError;
            detail.appendChild(error);
        }
        queueEl.appendChild(detail);
    }

    function transferTargets() {
        if (Array.isArray(transferState.targets) && transferState.targets.length) {
            return transferState.targets;
        }
        const configured = window.PlaybackTargets?.targetsFor?.('mass');
        return (configured && configured.length ? configured : DEFAULT_TRANSFER_TARGETS)
            .map((target) => ({
                id: String(target.id || '').trim(),
                name: String(target.name || target.label || target.id || '').trim(),
            }))
            .filter((target) => target.id)
            .map((target) => ({ ...target, name: target.name || target.id }));
    }

    async function refreshTransferTargets() {
        try {
            const selectedId = currentTransferTarget()?.id || '';
            const response = await fetch(`${getServiceUrlSafe()}/status`, { cache: 'no-store' });
            const payload = await response.json();
            if (!response.ok || !Array.isArray(payload?.transfer_targets)) return false;
            const targets = payload.transfer_targets
                .map((target) => ({
                    id: String(target?.id || target?.player_id || '').trim(),
                    name: String(target?.name || target?.display_name || target?.id || '').trim(),
                }))
                .filter((target) => target.id)
                .map((target) => ({ ...target, name: target.name || target.id }));
            if (!targets.length) return false;
            transferState.targets = targets;
            const selectedIndex = targets.findIndex((target) => target.id === selectedId);
            if (selectedIndex >= 0) {
                transferState.selectedIndex = selectedIndex;
                transferState.focusIndex = selectedIndex;
            }
            clampTransferFocus();
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        } catch (error) {
            return false;
        }
    }

    function youtubeFocusIndex() {
        return transferTargets().length;
    }

    function canShowTransferOverlay() {
        return window.uiStore?.currentRoute === 'menu/playing'
            && window.uiStore?.menuVisible !== false;
    }

    function clampTransferFocus() {
        const targets = transferTargets();
        const maxFocus = Math.max(0, targets.length);
        transferState.selectedIndex = Math.max(0, Math.min(transferState.selectedIndex, Math.max(0, targets.length - 1)));
        transferState.focusIndex = Math.max(0, Math.min(transferState.focusIndex || 0, maxFocus));
        if (transferState.focusIndex < targets.length) {
            transferState.selectedIndex = transferState.focusIndex;
        }
    }

    function currentTransferTarget() {
        const targets = transferTargets();
        clampTransferFocus();
        return targets[transferState.selectedIndex] || targets[0] || null;
    }

    function openOptionsMenu() {
        if (!canShowTransferOverlay()) return false;
        currentPageIndex = 1;
        clampTransferFocus();
        transferState.focusIndex = Math.min(
            Math.max(0, transferState.selectedIndex || 0),
            youtubeFocusIndex(),
        );
        transferState.message = '';
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
        void refreshTransferTargets();
        return true;
    }

    function closePlayingMenu() {
        currentPageIndex = 0;
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function selectTransferTarget(targetId) {
        const targets = transferTargets();
        const nextIndex = targets.findIndex((target) => target.id === targetId);
        if (nextIndex >= 0) {
            transferState.selectedIndex = nextIndex;
            transferState.focusIndex = nextIndex;
            transferState.message = `Selected ${targets[nextIndex].name}`;
            transferState.error = '';
            if (mountedContainer) renderOverlay(mountedContainer);
        }
    }

    function stepTransferFocus(delta) {
        if (currentPageId() !== 'options' || !canShowTransferOverlay()) return;

        const targets = transferTargets();
        const count = targets.length + 1;
        if (!count) return;
        const nextFocus = Math.max(0, Math.min(count - 1, (transferState.focusIndex || 0) + delta));
        transferState.focusIndex = nextFocus;
        if (transferState.focusIndex < targets.length) {
            transferState.selectedIndex = transferState.focusIndex;
        }
        transferState.message = '';
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
    }

    function activateTransferFocus() {
        const targets = transferTargets();
        clampTransferFocus();
        if (transferState.focusIndex < targets.length) {
            transferState.selectedIndex = transferState.focusIndex;
            void transferQueueToSelected({ closeOnSuccess: true });
            return true;
        }
        const handled = toggleYoutubeVideos();
        if (mountedContainer) renderOverlay(mountedContainer);
        return handled;
    }

    function renderTransferOptions(transferEl) {
        if (!transferEl) return;
        const targets = transferTargets();
        clampTransferFocus();
        const selected = currentTransferTarget();
        transferEl.innerHTML = '';
        targets.forEach((target, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'mass-transfer-option';
            if (target.id === selected?.id) button.classList.add('active');
            if (transferState.focusIndex === index) button.classList.add('focused');
            button.dataset.transferTarget = target.id;
            button.textContent = target.name;
            transferEl.appendChild(button);
        });

        const youtubeEnabled = youtubeVideosEnabled();
        const youtube = document.createElement('button');
        youtube.type = 'button';
        youtube.className = 'mass-youtube-toggle';
        youtube.dataset.youtubeToggle = '1';
        youtube.setAttribute('role', 'switch');
        youtube.setAttribute('aria-checked', youtubeEnabled ? 'true' : 'false');
        if (youtubeEnabled) youtube.classList.add('active');
        if (transferState.focusIndex === youtubeFocusIndex()) youtube.classList.add('focused');

        const label = document.createElement('span');
        label.className = 'mass-youtube-label';
        label.textContent = `YouTube Videos ${youtubeEnabled ? 'On' : 'Off'}`;
        const switchEl = document.createElement('span');
        switchEl.className = 'mass-youtube-switch';
        youtube.appendChild(label);
        youtube.appendChild(switchEl);
        transferEl.appendChild(youtube);
        requestAnimationFrame(() => {
            transferEl.querySelector('.focused')?.scrollIntoView({
                block: 'nearest',
            });
        });
    }

    async function transferQueueToSelected(options = {}) {
        const closeOnSuccess = options.closeOnSuccess === true;
        if (transferState.sending) return true;
        const target = currentTransferTarget();
        if (!target) return true;
        transferState.sending = true;
        transferState.message = `Sending queue to ${target.name}`;
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);

        try {
            const targetPayload = window.PlaybackTargets?.payloadFor?.('mass') || {};
            const sourceQueueId = String(queueState.queueId || lastMedia.queue_id || '').trim();
            const response = await fetch(`${getServiceUrlSafe()}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    command: 'transfer_queue',
                    ...targetPayload,
                    source_queue_id: sourceQueueId,
                    target_player_id: target.id,
                    target_queue_id: target.id,
                }),
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            if (!response.ok || !payload || payload.state === 'error' || payload.status === 'error') {
                transferState.error = `Unable to transfer to ${target.name}`;
                transferState.message = '';
            } else {
                transferState.message = `Queue transferred to ${target.name}`;
                transferState.error = '';
                if (window.PlaybackTargets?.setAudioTarget) {
                    await window.PlaybackTargets.setAudioTarget(target.id, {
                        skipQueuePrompt: true,
                        queueResolution: 'transfer',
                    });
                }
                if (closeOnSuccess) {
                    currentPageIndex = 0;
                }
                setTimeout(() => {
                    void refreshNowPlaying(true);
                    void refreshQueue(true);
                }, 500);
            }
        } catch (error) {
            transferState.error = `Unable to transfer to ${target.name}`;
            transferState.message = '';
        } finally {
            transferState.sending = false;
            if (mountedContainer) renderOverlay(mountedContainer);
        }
        return true;
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

        queueState.items.forEach((item, index) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'mass-playing-queue-item';
            const subtitle = item.subtitle || '';
            if (item.isCurrent) row.classList.add('is-current');
            if (index === queueUi.selectedIndex) row.classList.add('is-selected');
            row.dataset.queueIndex = String(index);
            row.innerHTML = `
                <div class="mass-playing-queue-index">${index + 1}</div>
                <div>
                    <div class="mass-playing-queue-title">${item.title || 'Queued Item'}</div>
                    <div class="mass-playing-queue-subtitle">${subtitle || '&nbsp;'}</div>
                </div>
            `;
            queueEl.appendChild(row);
        });
        revealSelectedQueueItem(queueEl);
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
        const transferEl = overlay.querySelector('.mass-playing-transfer');
        const transferLayer = overlay.querySelector('.mass-transfer-layer');
        const transferKickerEl = overlay.querySelector('.mass-transfer-layer-kicker');
        const transferHeadingEl = overlay.querySelector('.mass-transfer-layer-heading');
        const transferCopyEl = overlay.querySelector('.mass-transfer-layer-copy');
        const transferMetaEl = overlay.querySelector('.mass-transfer-layer-meta');
        const pausedEl = overlay.querySelector('.mass-paused-overlay');
        const pageId = currentPageId();
        const isPaused = String(lastMedia.state || '').trim().toLowerCase() === 'paused';

        container.dataset.massPage = pageId;
        container.classList.toggle('is-mass-paused', isPaused);
        if (pausedEl) pausedEl.hidden = !isPaused;
        overlay.querySelectorAll('.mass-playing-indicator').forEach((node) => {
            node.classList.toggle('active', node.dataset.page === pageId);
        });

        if (!panel || !kickerEl || !headingEl || !copyEl || !metaEl || !queueEl || !transferEl
            || !transferLayer || !transferKickerEl || !transferHeadingEl || !transferCopyEl || !transferMetaEl) return;

        transferLayer.hidden = pageId !== 'options';

        if (pageId === 'now') {
            queueEl.hidden = true;
            transferEl.hidden = true;
            copyEl.hidden = false;
            kickerEl.textContent = 'Now Playing';
            headingEl.textContent = lastMedia.title || '—';
            copyEl.textContent = [lastMedia.artist || '', lastMedia.album || '']
                .filter(Boolean)
                .join('\n') || 'Select something from the library to start playback.';
            metaEl.textContent = lastMedia.state ? `State: ${String(lastMedia.state).toUpperCase()}` : '';
            metaEl.hidden = !metaEl.textContent;
            return;
        }

        if (pageId === 'artist') {
            queueEl.hidden = true;
            transferEl.hidden = true;
            copyEl.hidden = false;
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
            metaEl.hidden = !metaEl.textContent;
            return;
        }

        if (pageId === 'options') {
            queueEl.hidden = false;
            transferEl.hidden = false;
            kickerEl.textContent = 'Queue';
            headingEl.textContent = activeQueueHeading();
            copyEl.textContent = queueStatusText();
            copyEl.hidden = !copyEl.textContent;
            metaEl.textContent = queueState.items.length
                ? `${queueState.items.length} item${queueState.items.length === 1 ? '' : 's'}`
                : '';
            metaEl.hidden = !metaEl.textContent;
            renderQueueList(queueEl);
            transferKickerEl.textContent = 'Playing';
            transferHeadingEl.textContent = queueState.queueName
                ? `Transfer ${queueState.queueName} Queue`
                : 'Transfer Queue';
            transferCopyEl.textContent = transferState.error || transferState.message || '';
            transferCopyEl.hidden = !transferCopyEl.textContent;
            transferMetaEl.textContent = transferTargets().length
                ? 'GO Select   RIGHT Back'
                : 'Populate mass.transfer_targets in config';
            transferMetaEl.hidden = !transferMetaEl.textContent;
            renderTransferOptions(transferEl);
            return;
        }

        if (pageId === 'queue' || pageId === 'queue-menu' || pageId === 'queue-goto' || pageId === 'queue-detail') {
            queueEl.hidden = false;
            transferEl.hidden = true;
            copyEl.hidden = false;
            kickerEl.textContent = pageId === 'queue-detail' ? 'Information' : 'Queue';
            if (pageId === 'queue') {
                headingEl.textContent = activeQueueHeading();
            } else if (pageId === 'queue-menu') {
                headingEl.textContent = selectedQueueItem()?.title || 'Queue Actions';
            } else if (pageId === 'queue-goto') {
                headingEl.textContent = 'Go To';
            } else {
                headingEl.textContent = queueUi.detailPayload?.title || selectedQueueItem()?.title || 'Queue Item';
            }
            copyEl.textContent = queueStatusText();
            copyEl.hidden = !copyEl.textContent;
            metaEl.textContent = queueMetaText();
            metaEl.hidden = !metaEl.textContent;
            if (pageId === 'queue') {
                renderQueueList(queueEl);
            } else if (pageId === 'queue-menu') {
                renderQueueActionList(queueEl, QUEUE_ACTION_ITEMS, queueUi.actionIndex, 'queueAction');
            } else if (pageId === 'queue-goto') {
                renderQueueActionList(queueEl, QUEUE_DESTINATION_ITEMS, queueUi.destinationIndex, 'queueDestination');
            } else {
                renderQueueDetail(queueEl);
            }
            return;
        }

        queueEl.hidden = false;
        transferEl.hidden = true;
        copyEl.hidden = false;
        kickerEl.textContent = 'Queue';
        headingEl.textContent = activeQueueHeading();
        copyEl.textContent = queueState.loading && !queueState.items.length
            ? 'Fetching the current queue…'
            : (queueState.error || '');
        metaEl.textContent = queueState.items.length
            ? `${queueState.items.length} item${queueState.items.length === 1 ? '' : 's'}`
            : '';
        metaEl.hidden = !metaEl.textContent;
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
                artwork: resolveArtworkUrl(payload.artwork || ''),
                state: String(payload.state || '').trim() || lastMedia.state || 'unknown',
                queue_id: String(payload.queue_id || '').trim(),
                player_id: String(payload.player_id || '').trim(),
            };

            if (hasMeaningfulMedia(media)) {
                const previousTrackKey = buildTrackKey(lastMedia);
                const nextTrackKey = buildTrackKey(media);
                if (nextTrackKey && nextTrackKey !== previousTrackKey) {
                    void requestRouterResyncForTrack(media);
                }
                applyMediaSnapshot(media, {
                    syncSource: true,
                    publishToUiStore: true,
                    publishReason: 'mass_now_playing',
                });
            } else if (media.state && media.state !== lastMedia.state) {
                lastMedia = Object.assign({}, lastMedia, { state: media.state });
                publishSharedMediaSnapshot(lastMedia, 'mass_state');
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
                queueState.currentIndex = -1;
                queueState.queueId = '';
                queueState.playerId = '';
                queueState.queueName = '';
                queueState.state = 'idle';
                queueState.error = 'Unable to load the active queue right now.';
            } else {
                const tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
                const payloadCurrentIndex = Number(payload.current_index);
                queueState.currentIndex = Number.isFinite(payloadCurrentIndex) ? payloadCurrentIndex : -1;
                queueState.queueId = String(payload.queue_id || '').trim();
                queueState.playerId = String(payload.player_id || payload.queue_id || '').trim();
                queueState.queueName = String(payload.queue_name || payload.player_name || '').trim();
                queueState.state = String(payload.state || '').trim() || 'idle';
                queueState.items = tracks.map((track, index) => {
                    const trackIndex = Number(track?.index);
                    const absoluteIndex = Number.isFinite(trackIndex) ? trackIndex : index;
                    const artistText = String(track?.artist || '').trim();
                    const isCurrent = Boolean(track?.current)
                        || artistText.toLowerCase().startsWith('now playing')
                        || (Number.isFinite(payloadCurrentIndex) && absoluteIndex === payloadCurrentIndex);
                    const subtitle = artistText.replace(/^Now Playing\s*-\s*/i, '').trim();
                    const album = String(track?.album || '').trim();
                    return {
                        id: String(track?.id || `queue_item_${absoluteIndex}`),
                        title: String(track?.name || track?.title || 'Queued Item'),
                        artist: subtitle,
                        album,
                        artwork: resolveArtworkUrl(track?.artwork || ''),
                        uri: String(track?.uri || track?.url || '').trim(),
                        index: absoluteIndex,
                        subtitle: [subtitle, album].filter(Boolean).join(' - '),
                        isCurrent,
                    };
                });
                queueUi.selectedIndex = Math.max(
                    0,
                    Math.min(queueUi.selectedIndex, Math.max(0, queueState.items.length - 1)),
                );
                if (queueUi.open && (force || queueUi.mode === 'list')) {
                    requestQueueReveal();
                }
                queueState.error = '';
            }
        } catch (error) {
            if (requestId !== queueRequestId) return;
            queueState.items = [];
            queueState.currentIndex = -1;
            queueState.queueId = '';
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
        const pageId = currentPageId();
        if (pageId === 'now') {
            lastPageCycleAt = now;
            return openQueueOverlay();
        }
        if (now - lastPageCycleAt < PAGE_CYCLE_COOLDOWN_MS) return true;
        lastPageCycleAt = now;
        const direction = String(data?.direction || 'clock').toLowerCase();
        const delta = direction === 'counter' ? -1 : 1;
        if (pageId === 'queue') {
            if (!queueState.items.length) return true;
            moveQueueSelection(delta);
            return true;
        }
        if (pageId === 'queue-menu') {
            stepQueueActionFocus(delta);
            return true;
        }
        if (pageId === 'queue-goto') {
            stepQueueDestinationFocus(delta);
            return true;
        }
        if (pageId === 'queue-detail') return true;
        stepTransferFocus(delta);
        return true;
    }

    function handleTransferButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (normalized === '__close_playing_overlay__') {
            closeQueueOverlay();
            return closePlayingMenu();
        }
        if (normalized === '__close_transfer_overlay__') {
            return closePlayingMenu();
        }
        const pageId = currentPageId();
        const isQueuePage = pageId === 'queue'
            || pageId === 'queue-menu'
            || pageId === 'queue-goto'
            || pageId === 'queue-detail';
        if (pageId === 'now') {
            return false;
        }
        if (isQueuePage && normalized === 'left') return openOptionsMenu();
        if (isQueuePage && normalized === 'right') {
            if (pageId === 'queue-detail') {
                clearQueueDetail();
                queueUi.mode = 'context';
            } else if (pageId === 'queue-goto') {
                queueUi.mode = 'context';
            } else if (pageId === 'queue-menu') {
                queueUi.mode = 'list';
            } else {
                return closeQueueOverlay();
            }
            clearQueueMessage();
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        }
        if (pageId === 'queue') {
            if (normalized === 'go') {
                if (!queueState.items.length) return true;
                queueUi.mode = 'context';
                queueUi.actionIndex = 0;
                clearQueueMessage();
                if (mountedContainer) renderOverlay(mountedContainer);
                return true;
            }
            return true;
        }
        if (pageId === 'queue-menu') {
            if (normalized === 'go') {
                void activateQueueAction();
                return true;
            }
            return true;
        }
        if (pageId === 'queue-goto') {
            if (normalized === 'go') {
                return activateQueueDestination();
            }
            return true;
        }
        if (pageId === 'queue-detail') {
            return true;
        }
        if (normalized === 'right') {
            return closePlayingMenu();
        }
        if (normalized === 'up') {
            stepTransferFocus(-1);
            return true;
        }
        if (normalized === 'down') {
            stepTransferFocus(1);
            return true;
        }
        if (normalized === 'go') {
            return activateTransferFocus();
        }
        if (normalized === 'left') return true;
        return false;
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

    document.addEventListener('bs5c:music-video-preference', () => {
        if (mountedContainer && currentPageId() !== 'now') {
            renderOverlay(mountedContainer);
        }
    });

    return {
        onMount(container) {
            ensureStyles();
            mountedContainer = container;
            currentPageIndex = 0;
            queueUi = createQueueUiState();
            lastMedia = seedMediaFromUiStore() || (hasMeaningfulMedia(lastMedia) ? lastMedia : {
                title: '—',
                artist: '—',
                album: '—',
                artwork: '',
                state: 'loading',
            });
            resetArtistState(normalizeArtistKey(lastMedia.artist));
            syncUiStoreSource();
            ensureOverlay(container);
            updateBaseView(container, lastMedia);
            renderOverlay(container);
            scheduleQueueRefresh();
            scheduleNowPlayingRefresh();
            void refreshQueue(true);
            void refreshNowPlaying(true);
        },

        onUpdate(container, data) {
            mountedContainer = container;
            ensureOverlay(container);
            if (!applyMediaSnapshot(data || {}, { syncSource: false }) && mountedContainer) {
                updateBaseView(container, lastMedia);
                renderOverlay(container);
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
            queueRequestId += 1;
            artistRequestId += 1;
            nowPlayingRequestId += 1;
            queueDetailRequestId += 1;
            queueUi = createQueueUiState();
            queueState = {
                loading: false,
                error: '',
                items: [],
                currentIndex: -1,
                queueId: '',
            };
            transferState = {
                selectedIndex: 0,
                focusIndex: 0,
                sending: false,
                message: '',
                error: '',
                targets: [],
            };
            resetArtistState('');
            const overlay = container?.querySelector('.mass-playing-overlay');
            if (overlay) overlay.remove();
            if (container) {
                container.classList.remove('mass-playing-active');
                container.classList.remove('is-mass-paused');
                container.removeAttribute('data-mass-page');
            }
        },

        cyclePage,
        shouldCaptureNav() {
            return true;
        },
        isOverlayOpen() {
            return queueUi.open || currentPageIndex !== 0;
        },
        refreshNowPlaying(force = false) {
            return refreshNowPlaying(force);
        },
        handleButton(button) {
            return handleTransferButton(button);
        },
    };
})();

document.addEventListener('bs5c:menu-visibility', (event) => {
    if (event.detail?.visible === false) {
        _massPlayingPreset.handleButton('__close_playing_overlay__');
    }
});

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
                body: JSON.stringify({
                    command,
                    ...(window.PlaybackTargets?.payloadFor?.('mass') || {}),
                }),
            });
            if (!response.ok) {
                console.warn('[MASS UI] Transport command failed:', command, response.status);
                return false;
            }
            setTimeout(() => {
                void _massPlayingPreset.refreshNowPlaying(true);
            }, 350);
            return true;
        } catch (error) {
            console.warn('[MASS UI] Transport command error:', command, error);
            return false;
        }
    }

    function handlePlayingButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (_massPlayingPreset.handleButton(normalized)) {
            return true;
        }
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
        wantsPlayingNav() {
            return isPlayingRoute() && _massPlayingPreset.shouldCaptureNav();
        },

        hasPlayingOverlay() {
            return isPlayingRoute() && _massPlayingPreset.isOverlayOpen();
        },

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
    queueOverlay: {
        playing: true,
        mode: 'custom',
        capabilities: {
            playNow: true,
            playNext: true,
            remove: true,
            showInformation: true,
            goto: ['artist', 'album', 'genre'],
        },
    },
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
