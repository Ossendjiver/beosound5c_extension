/**
 * Kodi Source Preset
 *
 * Default iframe target assumes the deployed page is available at `softarc/kodi.html`.
 * If your deployed filename differs, change `KODI_IFRAME_SRC` below.
 */
const KODI_IFRAME_SRC = (window.AppConfig && window.AppConfig.kodiIframeSrc) || 'softarc/kodi.html';

const _kodiController = (() => {
    function currentRoute() {
        return window.uiStore?.currentRoute || '';
    }

    function isPlayingRoute() {
        return currentRoute() === 'menu/playing';
    }

    function sendToIframe(type, data) {
        if (!window.IframeMessenger) {
            console.error('[KODI UI] IframeMessenger is missing!');
            return false;
        }
        console.log(`[KODI UI] Sending ${type} to iframe:`, data);
        return IframeMessenger.sendToRoute('menu/kodi', type, data);
    }

    async function sendTransport(command) {
        const serviceUrl = (typeof getServiceUrl === 'function')
            ? getServiceUrl('kodiServiceUrl', 8782)
            : 'http://localhost:8782';
        try {
            const response = await fetch(`${serviceUrl}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command }),
            });
            if (!response.ok) {
                console.warn('[KODI UI] Transport command failed:', command, response.status);
                return false;
            }
            return true;
        } catch (error) {
            console.warn('[KODI UI] Transport command error:', command, error);
            return false;
        }
    }

    return {
        get isActive() { return true; },

        updateMetadata() {},

        handleNavEvent(data) {
            if (isPlayingRoute()) return true;
            console.log('[KODI UI] Hardware Wheel Turned:', data);
            return sendToIframe('nav', { data });
        },

        handleButton(button) {
            console.log('[KODI UI] Hardware Button Pressed:', button);
            const normalized = String(button || '').toLowerCase();
            if (isPlayingRoute()) {
                if (normalized === 'left') {
                    void sendTransport('transport_previous');
                    return true;
                }
                if (normalized === 'right') {
                    void sendTransport('transport_next');
                    return true;
                }
                if (normalized === 'go') {
                    void sendTransport('transport_toggle');
                    return true;
                }
                if (normalized === 'go_long' || normalized === 'go_hold') {
                    void sendTransport('transport_stop');
                    return true;
                }
                return true;
            }
            if (sendToIframe('button', { button })) return true;
            return false;
        },
    };
})();

window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.kodi = {
    controller: _kodiController,
    item: { title: 'KODI', path: 'menu/kodi' },
    after: 'menu/playing',
    view: {
        title: 'KODI',
        content: '<div id="kodi-container" style="width:100%;height:100%;"></div>',
        containerId: 'kodi-container',
        preloadId: 'preload-kodi',
        iframeSrc: KODI_IFRAME_SRC
    },

    onAdd() {},

    onMount() {
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/kodi', 'preload-kodi');
        }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/kodi');
        }
    },
};
