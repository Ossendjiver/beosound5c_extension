/**
 * Standalone active-source queue view.
 */
(function () {
    const ROUTER_URL = () => window.AppConfig?.routerUrl || 'http://localhost:8770';
    const MAX_ITEMS = 80;

    let container = null;
    let items = [];
    let selectedIndex = 0;
    let currentIndex = -1;
    let busy = false;
    let message = '';
    let confirm = null;
    let refreshTimer = null;
    let userHasNavigated = false;
    let pendingScrollMode = '';

    function itemId(item) {
        return item?.queue_item_id || item?.item_id || item?.id || `index:${item?.index ?? 0}`;
    }

    function subtitleFor(item) {
        return [item?.artist, item?.album].map((part) => String(part || '').trim()).filter(Boolean).join(' - ');
    }

    function payloadForSelected() {
        const item = items[selectedIndex] || {};
        return {
            position: Number.isFinite(Number(item.index)) ? Number(item.index) : selectedIndex,
            index: Number.isFinite(Number(item.index)) ? Number(item.index) : selectedIndex,
            id: item.id || '',
            item_id: item.item_id || '',
            queue_item_id: item.queue_item_id || '',
        };
    }

    function setConfirm(action) {
        confirm = { action, key: itemId(items[selectedIndex]), index: selectedIndex };
        message = action === 'remove' ? 'Remove?' : 'Play next?';
        render();
    }

    function clearConfirm() {
        confirm = null;
        message = '';
    }

    function clampSelectedIndex(nextIndex) {
        return Math.max(0, Math.min(items.length - 1, Number(nextIndex) || 0));
    }

    function syncScrollPosition(list, mode) {
        if (!list || !items.length) return;
        const row = list.querySelector(`.queue-view-item[data-index="${selectedIndex}"]`);
        if (!row) return;
        if (mode === 'top') {
            list.scrollTop = Math.max(0, row.offsetTop);
            return;
        }
        row.scrollIntoView({
            block: 'nearest',
            inline: 'nearest',
            behavior: 'auto',
        });
    }

    function render() {
        if (!container) return;
        const list = container.querySelector('.queue-view-list');
        const status = container.querySelector('.queue-view-status');
        const count = container.querySelector('.queue-view-count');
        if (count) {
            count.textContent = items.length ? `${selectedIndex + 1} / ${items.length}` : 'No items';
        }
        if (status) {
            status.textContent = busy ? 'Working...' : (message || 'Left removes, right plays next');
        }
        if (!list) return;
        list.innerHTML = '';
        if (!items.length) {
            list.innerHTML = '<div class="queue-view-empty">Queue empty</div>';
            return;
        }
        items.forEach((item, index) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'queue-view-item';
            if (index === selectedIndex) row.classList.add('selected');
            if (index === currentIndex || item.current) row.classList.add('current');
            if (currentIndex >= 0 && index < currentIndex) row.classList.add('past');
            row.dataset.index = String(index);
            const title = String(item.name || item.title || 'Queued Item').trim();
            row.innerHTML = `
                <span class="queue-view-index">${index + 1}</span>
                <span class="queue-view-text">
                    <span class="queue-view-title">${title}</span>
                    <span class="queue-view-subtitle">${subtitleFor(item) || '&nbsp;'}</span>
                </span>
            `;
            row.addEventListener('click', () => {
                selectedIndex = index;
                userHasNavigated = true;
                pendingScrollMode = 'nearest';
                clearConfirm();
                render();
            });
            list.appendChild(row);
        });
        if (pendingScrollMode) {
            syncScrollPosition(list, pendingScrollMode);
            pendingScrollMode = '';
        }
    }

    async function refresh() {
        if (!container) return;
        const hadItems = items.length > 0;
        busy = true;
        message = items.length ? message : 'Loading queue...';
        render();
        try {
            const response = await fetch(`${ROUTER_URL()}/router/queue?start=0&max_items=${MAX_ITEMS}`, { cache: 'no-store' });
            const data = await response.json();
            if (!response.ok || data.error || data.state === 'error') {
                items = [];
                currentIndex = -1;
                message = 'Queue unavailable';
            } else {
                items = Array.isArray(data.tracks) ? data.tracks : [];
                currentIndex = Number.isFinite(Number(data.current_index)) ? Number(data.current_index) : -1;
                if (items.length) {
                    if (!userHasNavigated || !hadItems) {
                        selectedIndex = clampSelectedIndex(currentIndex >= 0 ? currentIndex : 0);
                        pendingScrollMode = currentIndex >= 0 ? 'top' : 'nearest';
                    } else {
                        selectedIndex = clampSelectedIndex(selectedIndex);
                        pendingScrollMode = 'nearest';
                    }
                } else {
                    selectedIndex = 0;
                }
                message = items.length ? '' : 'Queue empty';
            }
        } catch (error) {
            items = [];
            currentIndex = -1;
            message = 'Queue unavailable';
        } finally {
            busy = false;
            render();
        }
    }

    async function postQueueAction(url, payload, successMessage) {
        if (busy) return true;
        busy = true;
        render();
        try {
            const response = await fetch(`${ROUTER_URL()}${url}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.error || data.state === 'error' || data.status === 'error') {
                message = 'Queue update failed';
            } else {
                message = successMessage;
                clearConfirm();
                await refresh();
            }
        } catch (error) {
            message = 'Queue update failed';
        } finally {
            busy = false;
            render();
        }
        return true;
    }

    function confirmOrRun(action) {
        if (!items.length) return true;
        const key = itemId(items[selectedIndex]);
        if (!confirm || confirm.action !== action || confirm.key !== key) {
            setConfirm(action);
            return true;
        }
        const payload = payloadForSelected();
        if (action === 'remove') {
            void postQueueAction('/router/queue/remove', payload, 'Removed');
        } else {
            void postQueueAction('/router/queue/play-next', payload, 'Moved next');
        }
        return true;
    }

    function handleNavEvent(data) {
        if (!items.length) return true;
        const delta = String(data?.direction || '').toLowerCase() === 'counter' ? -1 : 1;
        selectedIndex = clampSelectedIndex(selectedIndex + delta);
        userHasNavigated = true;
        pendingScrollMode = 'nearest';
        clearConfirm();
        render();
        return true;
    }

    function handleButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (normalized === 'left') return confirmOrRun('remove');
        if (normalized === 'right') return confirmOrRun('play-next');
        if (normalized === 'go') {
            if (items.length) {
                void postQueueAction('/router/queue/play', payloadForSelected(), 'Playing');
            }
            return true;
        }
        return false;
    }

    function onMount(nextContainer) {
        container = nextContainer;
        items = [];
        selectedIndex = 0;
        currentIndex = -1;
        busy = false;
        message = 'Loading queue...';
        confirm = null;
        userHasNavigated = false;
        pendingScrollMode = '';
        render();
        void refresh();
        refreshTimer = setInterval(() => { void refresh(); }, 5000);
    }

    function onRemove() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = null;
        container = null;
    }

    window.QueueView = { onMount, onRemove, handleNavEvent, handleButton, refresh };
})();
