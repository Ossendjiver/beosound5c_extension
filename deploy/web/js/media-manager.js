/**
 * MediaManager — owns all "what's playing" state.
 *
 * Manages:
 *  - mediaInfo (now-playing metadata from router)
 *  - appleTVMediaInfo (SHOWING view, polled from backend)
 *  - activePlayingPreset (which renderer draws the PLAYING view)
 *  - activeSource / activeSourcePlayer
 *
 * Dispatches:
 *  - bs5c:media-update  { data, reason }   — after every media update
 *  - bs5c:media-text-updated               — after crossfade text swap (via crossfadeText)
 */

/**
 * Soft crossfade for text changes on the playing view.
 * Fades out, swaps text, fades back in. Cancels pending swaps on rapid updates.
 */
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

const SHARED_MEDIA_STYLE_ID = 'bs5c-shared-media-style';
const SHOWING_INPUT_URL = 'http://localhost:8767';

function isPausedPlaybackState(state) {
    return String(state || '').trim().toLowerCase() === 'paused';
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

const PlayingQueueOverlay = (() => {
    const STYLE_ID = 'bs5c-playing-queue-style';
    const REFRESH_MS = 5000;
    const MAX_ITEMS = 80;

    let mountedContainer = null;
    let activeSourceId = '';
    let refreshTimer = null;
    let queueRequestId = 0;
    let ui = createUiState();
    let queueState = createQueueState();

    function createUiState() {
        return {
            open: false,
            mode: 'list',
            selectedIndex: 0,
            actionIndex: 0,
            pendingReveal: false,
            busy: false,
            message: '',
            detailItemKey: '',
            detailPayload: null,
        };
    }

    function createQueueState() {
        return {
            loading: false,
            error: '',
            items: [],
            currentIndex: -1,
        };
    }

    function isPlayingRoute() {
        return window.uiStore?.currentRoute === 'menu/playing';
    }

    function routerUrl() {
        return window.AppConfig?.routerUrl || 'http://localhost:8770';
    }

    function normalizeSourceId(sourceId) {
        return String(sourceId || window.uiStore?.activeSource || '').trim().toLowerCase();
    }

    function queueConfigFor(sourceId = activeSourceId) {
        const preset = window.SourcePresets?.[normalizeSourceId(sourceId)];
        const config = preset?.queueOverlay;
        if (!config?.playing) return null;
        if (String(config.mode || '').trim().toLowerCase() === 'custom') return null;
        return config;
    }

    function queueCapabilities(config = queueConfigFor()) {
        const declared = config?.capabilities || {};
        const gotoTargets = Array.isArray(declared.goto)
            ? declared.goto.map((entry) => String(entry || '').trim()).filter(Boolean)
            : [];
        return {
            playNow: declared.playNow !== false,
            playNext: declared.playNext === true,
            remove: declared.remove === true,
            showInformation: declared.showInformation !== false,
            goto: gotoTargets,
        };
    }

    function isEnabledFor(sourceId = activeSourceId) {
        return Boolean(queueConfigFor(sourceId));
    }

    function queueItemKey(item) {
        return String(
            item?.queue_item_id
            || item?.item_id
            || item?.id
            || `queue-index-${item?.index ?? 0}`
        ).trim();
    }

    function selectedItem() {
        return queueState.items[ui.selectedIndex] || null;
    }

    function requestReveal() {
        ui.pendingReveal = true;
    }

    function clearMessage() {
        ui.message = '';
    }

    function clearDetail() {
        ui.detailItemKey = '';
        ui.detailPayload = null;
    }

    function closeOverlay() {
        ui.open = false;
        ui.mode = 'list';
        ui.actionIndex = 0;
        ui.pendingReveal = false;
        ui.busy = false;
        clearMessage();
        clearDetail();
        render();
        return true;
    }

    function openOverlay() {
        if (!isEnabledFor()) return false;
        ui.open = true;
        ui.mode = 'list';
        ui.actionIndex = 0;
        requestReveal();
        clearMessage();
        render();
        void refreshQueue(true);
        return true;
    }

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
            #now-playing .bs5c-playing-queue-overlay {
                position: absolute;
                inset: 0;
                display: flex;
                justify-content: flex-end;
                align-items: center;
                padding: 14px 18px;
                pointer-events: none;
                z-index: 8;
            }
            #now-playing .bs5c-playing-queue-overlay[hidden] {
                display: none !important;
            }
            #now-playing .bs5c-playing-queue-panel {
                width: min(49%, 470px);
                max-height: 72vh;
                display: flex;
                flex-direction: column;
                gap: 10px;
                padding: 20px 22px;
                border-radius: 24px;
                background: linear-gradient(180deg, rgba(15, 19, 29, 0.90), rgba(9, 11, 18, 0.95));
                border: 1px solid rgba(255, 255, 255, 0.10);
                box-shadow: 0 18px 48px rgba(0, 0, 0, 0.34);
                backdrop-filter: blur(18px);
                pointer-events: auto;
                overflow: hidden;
            }
            #now-playing .bs5c-playing-queue-kicker {
                color: rgba(255, 255, 255, 0.62);
                font-size: 0.8rem;
                letter-spacing: 0.22em;
                text-transform: uppercase;
            }
            #now-playing .bs5c-playing-queue-heading {
                color: #fff;
                font-size: 1.9rem;
                line-height: 1.05;
                font-weight: 600;
                min-height: 2.2rem;
            }
            #now-playing .bs5c-playing-queue-copy,
            #now-playing .bs5c-playing-queue-meta,
            #now-playing .bs5c-playing-queue-detail-copy,
            #now-playing .bs5c-playing-queue-detail-subtitle {
                color: rgba(255, 255, 255, 0.72);
                font-size: 0.95rem;
                line-height: 1.45;
            }
            #now-playing .bs5c-playing-queue-meta {
                color: rgba(255, 255, 255, 0.54);
                min-height: 1.2rem;
            }
            #now-playing .bs5c-playing-queue-body {
                flex: 1;
                overflow: auto;
                padding-right: 4px;
            }
            #now-playing .bs5c-playing-queue-body::-webkit-scrollbar {
                width: 0;
                height: 0;
            }
            #now-playing .bs5c-playing-queue-empty {
                color: rgba(255, 255, 255, 0.74);
                padding: 10px 0;
            }
            #now-playing .bs5c-playing-queue-list,
            #now-playing .bs5c-playing-queue-actions {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            #now-playing .bs5c-playing-queue-item,
            #now-playing .bs5c-playing-queue-action {
                width: 100%;
                border: 0;
                background: rgba(255, 255, 255, 0.05);
                color: #fff;
                border-radius: 18px;
                text-align: left;
                cursor: pointer;
            }
            #now-playing .bs5c-playing-queue-item {
                display: grid;
                grid-template-columns: 42px minmax(0, 1fr);
                gap: 12px;
                padding: 14px 16px;
            }
            #now-playing .bs5c-playing-queue-action {
                padding: 16px 18px;
                font-size: 1rem;
            }
            #now-playing .bs5c-playing-queue-item.is-selected,
            #now-playing .bs5c-playing-queue-action.is-selected {
                background: rgba(255, 255, 255, 0.17);
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
            }
            #now-playing .bs5c-playing-queue-item.is-current .bs5c-playing-queue-title {
                color: #98ffc1;
            }
            #now-playing .bs5c-playing-queue-index {
                color: rgba(255, 255, 255, 0.48);
                font-size: 0.92rem;
                font-weight: 600;
                padding-top: 2px;
            }
            #now-playing .bs5c-playing-queue-title {
                display: block;
                color: #fff;
                font-size: 1rem;
                line-height: 1.28;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            #now-playing .bs5c-playing-queue-subtitle {
                display: block;
                color: rgba(255, 255, 255, 0.58);
                font-size: 0.9rem;
                line-height: 1.3;
                margin-top: 5px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            #now-playing .bs5c-playing-queue-detail {
                display: flex;
                flex-direction: column;
                gap: 12px;
            }
            #now-playing .bs5c-playing-queue-detail-image {
                width: 100%;
                aspect-ratio: 1 / 1;
                object-fit: cover;
                border-radius: 18px;
                background: rgba(255, 255, 255, 0.04);
            }
        `;
        document.head.appendChild(style);
    }

    function ensureOverlay(container = mountedContainer) {
        if (!container) return null;
        let overlay = container.querySelector('.bs5c-playing-queue-overlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'bs5c-playing-queue-overlay';
        overlay.hidden = true;
        overlay.innerHTML = `
            <div class="bs5c-playing-queue-panel">
                <div class="bs5c-playing-queue-kicker">Queue</div>
                <div class="bs5c-playing-queue-heading">Active Queue</div>
                <div class="bs5c-playing-queue-copy"></div>
                <div class="bs5c-playing-queue-meta"></div>
                <div class="bs5c-playing-queue-body"></div>
            </div>
        `;
        overlay.addEventListener('click', (event) => {
            const queueItem = event.target.closest('[data-queue-index]');
            if (queueItem) {
                ui.selectedIndex = Math.max(0, Number(queueItem.dataset.queueIndex) || 0);
                ui.mode = 'list';
                requestReveal();
                clearMessage();
                render();
                return;
            }
            const queueAction = event.target.closest('[data-queue-action-index]');
            if (queueAction) {
                ui.actionIndex = Math.max(0, Number(queueAction.dataset.queueActionIndex) || 0);
                render();
                void activateSelectedAction();
            }
        });
        container.appendChild(overlay);
        return overlay;
    }

    function actionItems(config = queueConfigFor()) {
        const caps = queueCapabilities(config);
        const items = [];
        if (caps.playNow) items.push({ id: 'play_now', title: 'Play Now' });
        if (caps.playNext) items.push({ id: 'play_next', title: 'Play Next' });
        if (caps.showInformation) items.push({ id: 'show_information', title: 'Show Information' });
        if (caps.remove) items.push({ id: 'remove', title: 'Remove' });
        return items;
    }

    function subtitleFor(item) {
        return [item?.artist, item?.album]
            .map((part) => String(part || '').trim())
            .filter(Boolean)
            .join(' - ');
    }

    function payloadFor(item) {
        const index = Number.isFinite(Number(item?.index)) ? Number(item.index) : ui.selectedIndex;
        return {
            position: index,
            index,
            id: item?.id || '',
            item_id: item?.item_id || item?.id || '',
            queue_item_id: item?.queue_item_id || item?.item_id || item?.id || '',
        };
    }

    function requestSelectionReveal() {
        ui.pendingReveal = true;
    }

    function revealSelected(body) {
        if (!ui.pendingReveal || !body) return;
        ui.pendingReveal = false;
        const selectedIndexSnapshot = ui.selectedIndex;
        requestAnimationFrame(() => {
            const selected = body.querySelector(`[data-queue-index="${selectedIndexSnapshot}"]`);
            if (!selected) return;
            const itemTop = selected.offsetTop;
            const itemBottom = itemTop + selected.offsetHeight;
            const viewportTop = body.scrollTop;
            const viewportBottom = viewportTop + body.clientHeight;
            const padding = Math.max(12, Math.round(selected.offsetHeight * 0.35));
            if (itemTop < viewportTop + padding) {
                body.scrollTop = Math.max(0, itemTop - padding);
                return;
            }
            if (itemBottom > viewportBottom - padding) {
                body.scrollTop = Math.max(0, itemBottom - body.clientHeight + padding);
            }
        });
    }

    function renderQueueList(body) {
        body.innerHTML = '';
        if (queueState.loading && !queueState.items.length) {
            body.innerHTML = '<div class="bs5c-playing-queue-empty">Loading queue...</div>';
            return;
        }
        if (queueState.error) {
            body.innerHTML = `<div class="bs5c-playing-queue-empty">${queueState.error}</div>`;
            return;
        }
        if (!queueState.items.length) {
            body.innerHTML = '<div class="bs5c-playing-queue-empty">Queue empty</div>';
            return;
        }
        const list = document.createElement('div');
        list.className = 'bs5c-playing-queue-list';
        queueState.items.forEach((item, index) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'bs5c-playing-queue-item';
            if (index === ui.selectedIndex) row.classList.add('is-selected');
            if (index === queueState.currentIndex || item.current) row.classList.add('is-current');
            row.dataset.queueIndex = String(index);
            row.innerHTML = `
                <span class="bs5c-playing-queue-index">${index + 1}</span>
                <span class="bs5c-playing-queue-text">
                    <span class="bs5c-playing-queue-title">${String(item.name || item.title || 'Queued Item').trim()}</span>
                    <span class="bs5c-playing-queue-subtitle">${subtitleFor(item) || '&nbsp;'}</span>
                </span>
            `;
            list.appendChild(row);
        });
        body.appendChild(list);
        revealSelected(body);
    }

    function renderActionList(body, actions) {
        body.innerHTML = '';
        if (!actions.length) {
            body.innerHTML = '<div class="bs5c-playing-queue-empty">No queue actions are available here.</div>';
            return;
        }
        const list = document.createElement('div');
        list.className = 'bs5c-playing-queue-actions';
        actions.forEach((action, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'bs5c-playing-queue-action';
            if (index === ui.actionIndex) button.classList.add('is-selected');
            button.dataset.queueActionIndex = String(index);
            button.textContent = action.title;
            list.appendChild(button);
        });
        body.appendChild(list);
        requestAnimationFrame(() => {
            list.querySelector('.is-selected')?.scrollIntoView({ block: 'nearest' });
        });
    }

    function fallbackDetailPayload(item) {
        return {
            title: String(item?.name || item?.title || 'Queue Item').trim() || 'Queue Item',
            subtitle: subtitleFor(item),
            image: String(item?.artwork || '').trim(),
            description: 'Further source information is not available for this queue item yet.',
        };
    }

    function renderDetail(body) {
        body.innerHTML = '';
        const payload = ui.detailPayload || fallbackDetailPayload(selectedItem());
        const detail = document.createElement('div');
        detail.className = 'bs5c-playing-queue-detail';
        const image = String(payload.image || '').trim();
        if (image) {
            const img = document.createElement('img');
            img.className = 'bs5c-playing-queue-detail-image';
            img.src = image;
            img.alt = payload.title || 'Queue item artwork';
            detail.appendChild(img);
        }
        if (payload.subtitle) {
            const subtitle = document.createElement('div');
            subtitle.className = 'bs5c-playing-queue-detail-subtitle';
            subtitle.textContent = payload.subtitle;
            detail.appendChild(subtitle);
        }
        if (payload.description) {
            const copy = document.createElement('div');
            copy.className = 'bs5c-playing-queue-detail-copy';
            copy.textContent = payload.description;
            detail.appendChild(copy);
        }
        body.appendChild(detail);
    }

    function render() {
        const overlay = ensureOverlay();
        if (!overlay) return;
        const enabled = isEnabledFor(activeSourceId);
        overlay.hidden = !enabled || !ui.open || !isPlayingRoute();
        if (overlay.hidden) return;

        const actions = actionItems();
        const heading = overlay.querySelector('.bs5c-playing-queue-heading');
        const copy = overlay.querySelector('.bs5c-playing-queue-copy');
        const meta = overlay.querySelector('.bs5c-playing-queue-meta');
        const body = overlay.querySelector('.bs5c-playing-queue-body');
        if (!heading || !copy || !meta || !body) return;

        if (ui.mode === 'context') {
            heading.textContent = selectedItem()?.title || selectedItem()?.name || 'Queue Actions';
            copy.textContent = ui.message || 'Choose an action';
            meta.textContent = 'GO Select   RIGHT Back';
            renderActionList(body, actions);
            return;
        }

        if (ui.mode === 'detail') {
            heading.textContent = ui.detailPayload?.title || selectedItem()?.title || selectedItem()?.name || 'Queue Item';
            copy.textContent = ui.message || '';
            meta.textContent = 'RIGHT Back';
            renderDetail(body);
            return;
        }

        heading.textContent = 'Active Queue';
        copy.textContent = ui.busy
            ? 'Working...'
            : (ui.message || (queueState.loading && !queueState.items.length ? 'Loading queue...' : 'GO Actions   RIGHT Close'));
        meta.textContent = queueState.items.length
            ? `${queueState.items.length} item${queueState.items.length === 1 ? '' : 's'}`
            : '';
        renderQueueList(body);
    }

    function stopRefresh() {
        if (!refreshTimer) return;
        clearInterval(refreshTimer);
        refreshTimer = null;
    }

    function ensureRefresh() {
        if (refreshTimer || !isEnabledFor(activeSourceId)) return;
        refreshTimer = setInterval(() => {
            void refreshQueue(false);
        }, REFRESH_MS);
    }

    async function refreshQueue(force = false) {
        if (!isEnabledFor(activeSourceId)) return;
        const requestId = ++queueRequestId;
        queueState.loading = true;
        if (force) queueState.error = '';
        render();
        try {
            const response = await fetch(`${routerUrl()}/router/queue?start=0&max_items=${MAX_ITEMS}`, { cache: 'no-store' });
            const data = await response.json().catch(() => ({}));
            if (requestId !== queueRequestId) return;
            if (!response.ok || data.error || data.state === 'error') {
                queueState.items = [];
                queueState.currentIndex = -1;
                queueState.error = 'Queue unavailable';
            } else {
                const nextItems = Array.isArray(data.tracks) ? data.tracks : [];
                const nextSelectedIndex = Math.max(0, Math.min(ui.selectedIndex, Math.max(0, nextItems.length - 1)));
                if (nextSelectedIndex !== ui.selectedIndex) requestSelectionReveal();
                queueState.items = nextItems;
                queueState.currentIndex = Number.isFinite(Number(data.current_index)) ? Number(data.current_index) : -1;
                ui.selectedIndex = nextSelectedIndex;
                queueState.error = '';
                if (ui.open) requestSelectionReveal();
            }
        } catch (error) {
            if (requestId !== queueRequestId) return;
            queueState.items = [];
            queueState.currentIndex = -1;
            queueState.error = 'Queue unavailable';
        } finally {
            if (requestId === queueRequestId) {
                queueState.loading = false;
                render();
            }
        }
    }

    async function postQueueAction(url, payload, successMessage) {
        if (ui.busy) return true;
        ui.busy = true;
        ui.message = successMessage || 'Working...';
        render();
        try {
            const response = await fetch(`${routerUrl()}${url}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.error || data.state === 'error' || data.status === 'error') {
                ui.message = 'Queue update failed';
            } else {
                ui.message = successMessage || 'Queue updated';
                if (url.endsWith('/play')) {
                    closeOverlay();
                } else {
                    await refreshQueue(true);
                }
            }
        } catch (error) {
            ui.message = 'Queue update failed';
        } finally {
            ui.busy = false;
            render();
        }
        return true;
    }

    async function activateSelectedAction() {
        const actions = actionItems();
        const action = actions[ui.actionIndex];
        const item = selectedItem();
        if (!action || !item) return true;
        if (action.id === 'play_now') {
            return postQueueAction('/router/queue/play', payloadFor(item), 'Playing');
        }
        if (action.id === 'play_next') {
            return postQueueAction('/router/queue/play-next', payloadFor(item), 'Moved next');
        }
        if (action.id === 'remove') {
            return postQueueAction('/router/queue/remove', payloadFor(item), 'Removed');
        }
        if (action.id === 'show_information') {
            ui.mode = 'detail';
            ui.detailItemKey = queueItemKey(item);
            ui.detailPayload = fallbackDetailPayload(item);
            render();
            return true;
        }
        return true;
    }

    function sync(container, context = {}) {
        mountedContainer = container || mountedContainer;
        ensureStyles();

        const nextSourceId = normalizeSourceId(context.activeSource);
        const sourceChanged = nextSourceId !== activeSourceId;
        activeSourceId = nextSourceId;

        if (!isEnabledFor(activeSourceId)) {
            stopRefresh();
            queueRequestId += 1;
            queueState = createQueueState();
            ui = createUiState();
            render();
            return;
        }

        ensureOverlay(mountedContainer);
        ensureRefresh();
        if (sourceChanged) {
            queueRequestId += 1;
            queueState = createQueueState();
            ui = createUiState();
            void refreshQueue(true);
        }
        render();
    }

    function handleNavEvent(data, sourceId = window.uiStore?.activeSource) {
        if (!isPlayingRoute()) return false;
        if (!isEnabledFor(sourceId)) return false;
        const delta = String(data?.direction || '').toLowerCase() === 'counter' ? -1 : 1;
        if (!ui.open) return openOverlay();
        if (ui.mode === 'detail') return true;
        if (ui.mode === 'context') {
            const actions = actionItems();
            if (!actions.length) return true;
            ui.actionIndex = Math.max(0, Math.min(actions.length - 1, ui.actionIndex + delta));
            render();
            return true;
        }
        if (!queueState.items.length) return true;
        ui.selectedIndex = Math.max(0, Math.min(queueState.items.length - 1, ui.selectedIndex + delta));
        requestReveal();
        clearMessage();
        render();
        return true;
    }

    function handleButton(button, sourceId = window.uiStore?.activeSource) {
        if (!isPlayingRoute()) return false;
        if (!isEnabledFor(sourceId)) return false;
        const normalized = String(button || '').toLowerCase();
        if (normalized === '__close_playing_overlay__') {
            return closeOverlay();
        }
        if (!ui.open) return false;
        if (normalized === 'left') return true;
        if (normalized === 'right') {
            if (ui.mode === 'detail') {
                clearDetail();
                ui.mode = 'context';
                render();
                return true;
            }
            if (ui.mode === 'context') {
                ui.mode = 'list';
                clearMessage();
                render();
                return true;
            }
            return closeOverlay();
        }
        if (normalized !== 'go') return true;
        if (ui.mode === 'detail') {
            return true;
        }
        if (ui.mode === 'context') {
            if (normalized === 'go') {
                void activateSelectedAction();
                return true;
            }
            return true;
        }
        if (normalized === 'go') {
            if (!queueState.items.length) return true;
            ui.mode = 'context';
            ui.actionIndex = 0;
            clearMessage();
            render();
            return true;
        }
        return true;
    }

    document.addEventListener('bs5c:view-change', (event) => {
        if (event.detail?.to !== 'menu/playing') {
            closeOverlay();
            stopRefresh();
            return;
        }
        if (isEnabledFor(window.uiStore?.activeSource)) {
            ensureRefresh();
            render();
        }
    });

    return {
        sync,
        handleNavEvent,
        handleButton,
        isEnabledFor,
        isOpen() {
            return ui.open;
        },
        close: closeOverlay,
    };
})();
window.PlayingQueueOverlay = PlayingQueueOverlay;

const DEFAULT_PLAYING_PRESET = {
    // All sources push metadata via the unified router media path.
    // media_update events reach this preset via handleMediaUpdate() → updateNowPlayingView().
    onUpdate(container, data) {
        const titleEl = container.querySelector('.media-view-title');
        const artistEl = container.querySelector('.media-view-artist');
        const albumEl = container.querySelector('.media-view-album');
        crossfadeText(titleEl, data.title || '—');
        crossfadeText(artistEl, data.artist || '—');
        crossfadeText(albumEl, data.album || '—');
        const img = container.querySelector('.playing-artwork');
        if (img && window.ArtworkManager) {
            // During idle/boot we haven't received real media yet — use the
            // fully transparent placeholder so the UI doesn't flash a
            // "no artwork" graphic that looks like a broken state. Once media
            // is actually playing/paused but the service didn't supply art, we
            // fall back to the silent vinyl-circle glyph.
            const hasMedia = data.state === 'playing' || data.state === 'paused';
            const placeholderType = hasMedia ? 'noArtwork' : 'blank';
            window.ArtworkManager.displayArtwork(
                img, data.artwork, placeholderType, data.artwork_candidates || []
            );
        }
        // Back artwork (show/hide back face based on availability)
        const backFace = container.querySelector('.playing-back');
        const backImg = container.querySelector('.playing-artwork-back');
        if (backFace && backImg) {
            if (data.back_artwork) {
                backImg.src = data.back_artwork;
                backFace.style.display = '';
            } else if (!backFace.querySelector('.cd-back-tracklist')) {
                // No back artwork and no source-populated content — hide
                backFace.style.display = 'none';
                // Un-flip if back was removed while flipped
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
        window.PlayingQueueOverlay?.sync(container, {
            activeSource: window.uiStore?.activeSource,
        });
    },
    onRemove(container) {
        const flipper = container.querySelector('.playing-flipper');
        if (flipper?._clickHandler) {
            flipper.removeEventListener('click', flipper._clickHandler);
        }
        window.PlayingQueueOverlay?.close?.();
    }
};

class MediaManager {
    constructor() {
        this.mediaInfo = {
            title: '—',
            artist: '—',
            album: '—',
            artwork: '',
            canvas_url: '',
            track_id: '',
            relay_id: '',
            state: 'idle'
        };

        this.appleTVMediaInfo = {
            title: '—',
            friendly_name: '—',
            app_name: '—',
            artwork: '',
            state: 'unknown'
        };

        this.activeSource = null;          // id of active source, or null
        this.activeSourcePlayer = null;    // "local" | "remote" | null
        this.activePlayingPreset = DEFAULT_PLAYING_PRESET;

        this._appleTVRefreshInterval = null;
        ensureSharedMediaStyle();
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

    // ── Now-playing (router media WS) ──

    shouldTreatAsStaleNowPlaying(data) {
        const state = String(data?.state || '').trim().toLowerCase();
        const relayId = String(data?.relay_id || '').trim().toLowerCase();
        return !this.activeSource && !relayId && state === 'stopped';
    }

    normalizeMediaUpdatePayload(data) {
        if (!this.shouldTreatAsStaleNowPlaying(data)) return data;
        return {
            ...data,
            title: '',
            artist: '',
            album: '',
            artwork: '',
            back_artwork: '',
            canvas_url: '',
            music_video_url: '',
            track_id: '',
            relay_id: '',
            source_id: '',
            state: 'idle',
            position: '0:00',
            duration: '0:00'
        };
    }

    handleMediaUpdate(data, reason = 'update') {
        const incoming = this.normalizeMediaUpdatePayload(data || {});
        data = incoming;
        console.log(`[MEDIA-WS] ${reason}: ${incoming.title} - ${incoming.artist}`);

        // canvas_url: on track_change, clear unless payload provides one
        // (new track = new canvas). On update, keep existing if not in payload
        // (canvas arrives async for the same track).
        const keepCanvas = reason !== 'track_change' && !('canvas_url' in incoming);
        const keepMusicVideo = reason !== 'track_change' && !('music_video_url' in incoming);
        const keepArtwork = reason !== 'track_change' && !('artwork' in incoming);
        const keepBackArtwork = reason !== 'track_change' && !('back_artwork' in incoming);
        // track_id: stamped by the router from the player's _track_uri
        // hint — used by canvas-panel.js to verify the canvas it's
        // about to show actually belongs to the currently playing
        // track. On track_change always replace; on update preserve
        // existing if payload doesn't include one (e.g. canvas_inject
        // re-broadcasts mutate canvas_url but keep the same track_id).
        const keepTrackId = reason !== 'track_change' && !('track_id' in incoming);
        const hasInternalSourceId = Object.prototype.hasOwnProperty.call(data, '_source_id');
        const hasSourceId = Object.prototype.hasOwnProperty.call(data, 'source_id');
        const sourceId = hasInternalSourceId
            ? (data._source_id || '')
            : hasSourceId
                ? (data.source_id || '')
                : (this.mediaInfo.source_id || this.activeSource || '');
        this.mediaInfo = {
            title: data.title || '—',
            artist: data.artist || '—',
            album: data.album || '—',
            artwork: keepArtwork
                ? (this.mediaInfo.artwork || '')
                : this.resolveArtworkUrl(data.artwork || '', sourceId),
            artwork_candidates: Array.isArray(data.artwork_candidates)
                ? data.artwork_candidates.map(url => this.resolveArtworkUrl(url, sourceId)).filter(Boolean)
                : (keepArtwork ? (this.mediaInfo.artwork_candidates || []) : []),
            back_artwork: keepBackArtwork
                ? (this.mediaInfo.back_artwork || '')
                : this.resolveArtworkUrl(data.back_artwork || '', sourceId),
            canvas_url: keepCanvas ? (this.mediaInfo.canvas_url || '') : (data.canvas_url || ''),
            music_video_url: keepMusicVideo ? (this.mediaInfo.music_video_url || '') : (data.music_video_url || ''),
            track_id: keepTrackId ? (this.mediaInfo.track_id || '') : (data.track_id || ''),
            relay_id: data.relay_id || '',
            source_id: sourceId,
            state: data.state || 'unknown',
            position: data.position || '0:00',
            duration: data.duration || '0:00'
        };

        document.dispatchEvent(new CustomEvent('bs5c:media-update', {
            detail: { data: this.mediaInfo, reason }
        }));

        this.updateNowPlayingView();
    }

    updateNowPlayingView() {
        const container = document.getElementById('now-playing');
        if (container && this.activePlayingPreset?.onUpdate) {
            this.activePlayingPreset.onUpdate(container, this.mediaInfo);
        }
        setPausedOverlayVisible(
            container?.querySelector('.playing-artwork-slot'),
            this.activePlayingPreset === DEFAULT_PLAYING_PRESET
                && isPausedPlaybackState(this.mediaInfo.state)
        );
        window.PlayingQueueOverlay?.sync(container, {
            activeSource: this.activeSource,
            mediaInfo: this.mediaInfo,
            activePlayingPreset: this.activePlayingPreset,
        });
    }

    shouldUseShowingAsPlaying() {
        return !this.activeSource && this.hasActiveShowingRelay();
    }

    hasActiveShowingRelay() {
        const state = String(this.mediaInfo?.state || '').trim().toLowerCase();
        return this.mediaInfo?.relay_id === 'showing'
            && !!state
            && !['idle', 'unknown', 'off', 'standby', 'unavailable'].includes(state);
    }

    shouldRoutePlayingButtonsToShowing() {
        return this.hasActiveShowingRelay();
    }

    _hasShowingTransportTarget() {
        if (this.hasActiveShowingRelay()) return true;
        const state = String(this.appleTVMediaInfo?.state || '').trim().toLowerCase();
        return !!state && !['error', 'unknown', 'unavailable'].includes(state);
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
            return true;
        } catch (error) {
            console.warn('[SHOWING UI] Transport command error:', command, error);
            return false;
        }
    }

    handleShowingButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (!this._hasShowingTransportTarget()) return false;

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

    /**
     * Switch the PLAYING view to a source's preset (or default).
     */
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

    // ── Apple TV / SHOWING view ──

    async fetchAppleTVMediaInfo() {
        const isMac = navigator.platform.toLowerCase().includes('mac');
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

        if (isMac && isLocalhost && window.EmulatorMockData) {
            const mockData = window.EmulatorMockData.getCurrentAppleTVShow();
            this.appleTVMediaInfo = {
                title: mockData.title || '—',
                friendly_name: mockData.friendly_name || '—',
                app_name: mockData.app_name || '—',
                artwork: mockData.artwork || window.EmulatorMockData.generateShowingArtwork(mockData),
                state: mockData.state || 'playing'
            };
            this.updateAppleTVMediaView();
            return;
        }

        try {
            const response = await fetch('http://localhost:8767/appletv');
            if (!response.ok) return;

            const data = await response.json();
            this.appleTVMediaInfo = {
                title: data.title || '—',
                friendly_name: data.friendly_name || '—',
                app_name: data.app_name || '—',
                artwork: data.artwork || '',
                state: data.state
            };
            this.updateAppleTVMediaView();
        } catch (error) {
            console.error('Error fetching Apple TV info:', error);
        }
    }

    updateAppleTVMediaView() {
        const artworkEl = document.getElementById('apple-tv-artwork');
        const titleEl = document.getElementById('apple-tv-media-title');
        const detailsEl = document.getElementById('apple-tv-media-details');
        const stateEl = document.getElementById('apple-tv-state');
        const artworkHost = document.getElementById('apple-tv-artwork-container');

        if (titleEl) titleEl.textContent = this.appleTVMediaInfo.title;
        if (detailsEl) detailsEl.textContent = this.appleTVMediaInfo.app_name;
        if (stateEl) stateEl.textContent = this.appleTVMediaInfo.state;

        if (artworkEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artworkEl, this.appleTVMediaInfo.artwork, 'showing');
        }

        setPausedOverlayVisible(artworkHost, isPausedPlaybackState(this.appleTVMediaInfo.state));
    }

    setupAppleTVMediaInfoRefresh() {
        this.fetchAppleTVMediaInfo();
        // Always clear first — re-entering SHOWING must not stack intervals.
        this.stopAppleTVMediaInfoRefresh();
        this._appleTVRefreshInterval = setInterval(() => {
            // Skip fetches while the tab is hidden — the Chromium kiosk
            // rarely backgrounds, but on the dev host this saves pointless
            // network traffic.
            if (document.visibilityState === 'hidden') return;
            this.fetchAppleTVMediaInfo();
        }, 5000);

        // Defense-in-depth: guarantee cleanup on page hide/unload so a
        // rogue interval can never outlive the document.
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

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { MediaManager, DEFAULT_PLAYING_PRESET, isPausedPlaybackState };
} else {
    window.MediaManager = MediaManager;
    window.DEFAULT_PLAYING_PRESET = DEFAULT_PLAYING_PRESET;
}
