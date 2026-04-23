/**
 * MASS Source Preset
 */
const _massPlayingPreset = (() => {
    const PAGE_IDS = ['now', 'transfer'];
    const QUEUE_REFRESH_MS = 3000;
    const NOW_PLAYING_REFRESH_MS = 2000;
    const PAGE_CYCLE_COOLDOWN_MS = 520;
    const YOUTUBE_PREF_KEY = 'bs5c.youtubeVideosEnabled';
    const DEFAULT_TRANSFER_TARGETS = [
        { id: '08a2eca2-247c-96fe-7998-7baddf01b2b1', name: 'Cuisine' },
        { id: '64ad9554-d5e6-116c-8b0b-069c1f0b7885', name: 'Bedroom Mini' },
        { id: 'up50411c87e1c0', name: 'Link' },
    ];
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
    let transferState = {
        selectedIndex: 0,
        focusIndex: 0,
        sending: false,
        message: '',
        error: '',
    };

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
        const normalized = Object.assign({}, data || {});
        if (normalized.artwork) normalized.artwork = resolveArtworkUrl(normalized.artwork);
        if (normalized.back_artwork) normalized.back_artwork = resolveArtworkUrl(normalized.back_artwork);
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
            #now-playing.mass-playing-active[data-mass-page="queue"] .mass-playing-panel,
            #now-playing.mass-playing-active[data-mass-page="transfer"] .mass-playing-panel {
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

            #now-playing.mass-playing-active .mass-playing-transfer {
                margin-top: 18px;
                display: flex;
                flex-direction: column;
                gap: 10px;
                pointer-events: auto;
            }

            #now-playing.mass-playing-active .mass-transfer-option,
            #now-playing.mass-playing-active .mass-transfer-action {
                width: 100%;
                min-height: 42px;
                border: 1px solid rgba(148, 199, 255, 0.26);
                border-radius: 6px;
                background: rgba(255, 255, 255, 0.08);
                color: #ffffff;
                font: 500 14px/1.2 Arial, sans-serif;
                text-align: left;
                padding: 0 14px;
                touch-action: manipulation;
            }

            #now-playing.mass-playing-active .mass-transfer-option.active {
                background: rgba(148, 199, 255, 0.22);
                border-color: rgba(158, 209, 255, 0.74);
            }

            #now-playing.mass-playing-active .mass-transfer-option.focused,
            #now-playing.mass-playing-active .mass-transfer-action.focused,
            #now-playing.mass-playing-active .mass-youtube-toggle.focused {
                border-color: rgba(255, 255, 255, 0.86);
                box-shadow: 0 0 0 2px rgba(158, 209, 255, 0.34);
            }

            #now-playing.mass-playing-active .mass-transfer-action {
                margin-top: 4px;
                text-align: center;
                background: rgba(158, 209, 255, 0.18);
            }

            #now-playing.mass-playing-active .mass-transfer-action:disabled {
                opacity: 0.55;
            }

            #now-playing.mass-playing-active .mass-youtube-toggle {
                width: 100%;
                min-height: 46px;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 6px;
                background: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                display: grid;
                grid-template-columns: 1fr 52px;
                gap: 12px;
                align-items: center;
                padding: 0 12px 0 14px;
                font: 500 14px/1.2 Arial, sans-serif;
                text-align: left;
                touch-action: manipulation;
            }

            #now-playing.mass-playing-active .mass-youtube-toggle.active {
                border-color: rgba(158, 209, 255, 0.62);
                background: rgba(158, 209, 255, 0.12);
            }

            #now-playing.mass-playing-active .mass-youtube-switch {
                position: relative;
                width: 44px;
                height: 24px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.22);
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
                background: rgba(158, 209, 255, 0.72);
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
                <div class="mass-playing-transfer" hidden></div>
            </div>
            <div class="mass-playing-indicators">
                <span class="mass-playing-indicator" data-page="now"></span>
                <span class="mass-playing-indicator" data-page="transfer"></span>
            </div>
        `;
        overlay.addEventListener('click', (event) => {
            const youtubeToggle = event.target.closest('[data-youtube-toggle]');
            if (youtubeToggle) {
                transferState.focusIndex = youtubeFocusIndex();
                toggleYoutubeVideos();
                return;
            }
            const option = event.target.closest('[data-transfer-target]');
            if (option) {
                selectTransferTarget(option.dataset.transferTarget);
                return;
            }
            const action = event.target.closest('[data-transfer-action]');
            if (action) {
                transferState.focusIndex = actionFocusIndex();
                void transferQueueToSelected();
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

    function transferTargets() {
        const configured = window.PlaybackTargets?.targetsFor?.('mass');
        return (configured && configured.length ? configured : DEFAULT_TRANSFER_TARGETS)
            .map((target) => ({
                id: String(target.id || '').trim(),
                name: String(target.name || target.label || target.id || '').trim(),
            }))
            .filter((target) => target.id)
            .map((target) => ({ ...target, name: target.name || target.id }));
    }

    function actionFocusIndex() {
        return transferTargets().length;
    }

    function youtubeFocusIndex() {
        return transferTargets().length + 1;
    }

    function clampTransferFocus() {
        const targets = transferTargets();
        const maxFocus = targets.length + 1;
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

    function selectTransferTarget(targetId) {
        const targets = transferTargets();
        const nextIndex = targets.findIndex((target) => target.id === targetId);
        if (nextIndex >= 0) {
            transferState.selectedIndex = nextIndex;
            transferState.focusIndex = nextIndex;
            transferState.message = '';
            transferState.error = '';
            if (mountedContainer) renderOverlay(mountedContainer);
        }
    }

    function stepTransferFocus(delta) {
        const targets = transferTargets();
        const count = targets.length + 2;
        if (!count) return;
        transferState.focusIndex = ((transferState.focusIndex || 0) + delta + count) % count;
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
            transferState.message = `Selected ${targets[transferState.selectedIndex].name}`;
            transferState.error = '';
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        }
        if (transferState.focusIndex === actionFocusIndex()) {
            void transferQueueToSelected();
            return true;
        }
        return toggleYoutubeVideos();
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

        const action = document.createElement('button');
        action.type = 'button';
        action.className = 'mass-transfer-action';
        action.dataset.transferAction = 'send';
        action.disabled = transferState.sending || !selected;
        if (transferState.focusIndex === actionFocusIndex()) action.classList.add('focused');
        action.textContent = transferState.sending
            ? 'Transferring...'
            : (selected ? `Transfer to ${selected.name}` : 'No targets configured');
        transferEl.appendChild(action);

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
    }

    async function transferQueueToSelected() {
        if (transferState.sending) return true;
        const target = currentTransferTarget();
        if (!target) return true;
        transferState.sending = true;
        transferState.message = `Sending queue to ${target.name}`;
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);

        try {
            if (window.PlaybackTargets?.setAudioTarget) {
                await window.PlaybackTargets.setAudioTarget(target.id);
            }
            const targetPayload = window.PlaybackTargets?.payloadFor?.('mass') || {};
            const response = await fetch(`${getServiceUrlSafe()}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    command: 'transfer_queue',
                    target_player_id: target.id,
                    ...targetPayload,
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
                setTimeout(() => {
                    void refreshNowPlaying(true);
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
        const transferEl = overlay.querySelector('.mass-playing-transfer');
        const pausedEl = overlay.querySelector('.mass-paused-overlay');
        const pageId = currentPageId();
        const isPaused = String(lastMedia.state || '').trim().toLowerCase() === 'paused';

        container.dataset.massPage = pageId;
        container.classList.toggle('is-mass-paused', isPaused);
        if (pausedEl) pausedEl.hidden = !isPaused;
        overlay.querySelectorAll('.mass-playing-indicator').forEach((node) => {
            node.classList.toggle('active', node.dataset.page === pageId);
        });

        if (!panel || !kickerEl || !headingEl || !copyEl || !metaEl || !queueEl || !transferEl) return;

        if (pageId === 'now') {
            queueEl.hidden = true;
            transferEl.hidden = true;
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
            transferEl.hidden = true;
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

        if (pageId === 'transfer') {
            queueEl.hidden = true;
            transferEl.hidden = false;
            const target = currentTransferTarget();
            kickerEl.textContent = 'Transfer';
            headingEl.textContent = 'Transfer Queue';
            copyEl.textContent = transferState.error || transferState.message || '';
            metaEl.textContent = target
                ? `Selected: ${target.name} - YouTube: ${youtubeVideosEnabled() ? 'On' : 'Off'}`
                : '';
            renderTransferOptions(transferEl);
            return;
        }

        queueEl.hidden = false;
        transferEl.hidden = true;
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
                artwork: resolveArtworkUrl(payload.artwork || ''),
                state: String(payload.state || '').trim() || lastMedia.state || 'unknown',
                queue_id: String(payload.queue_id || '').trim(),
                player_id: String(payload.player_id || '').trim(),
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
                const payloadCurrentIndex = Number(payload.current_index);
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
                        title: String(track?.name || track?.title || 'Queued Item'),
                        subtitle: [subtitle, album].filter(Boolean).join(' - '),
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
        }
        return true;
    }

    function handleTransferButton(button) {
        if (currentPageId() !== 'transfer') return false;
        const normalized = String(button || '').toLowerCase();
        if (normalized === 'left') {
            stepTransferFocus(-1);
            return true;
        }
        if (normalized === 'right') {
            stepTransferFocus(1);
            return true;
        }
        if (normalized === 'go') {
            return activateTransferFocus();
        }
        if (normalized === 'up' || normalized === 'down') {
            return toggleYoutubeVideos();
        }
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
        if (mountedContainer && currentPageId() === 'transfer') {
            renderOverlay(mountedContainer);
        }
    });

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
            scheduleNowPlayingRefresh();
            void refreshNowPlaying(true);
        },

        onUpdate(container, data) {
            mountedContainer = container;
            ensureOverlay(container);
            if (!applyMediaSnapshot(data || {}, { syncSource: true }) && mountedContainer) {
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
            transferState = {
                selectedIndex: 0,
                focusIndex: 0,
                sending: false,
                message: '',
                error: '',
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
        refreshNowPlaying(force = false) {
            return refreshNowPlaying(force);
        },
        handleButton(button) {
            return handleTransferButton(button);
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
