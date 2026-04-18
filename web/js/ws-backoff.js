/**
 * WS reconnect backoff — shared by hardware + media WS reconnect paths.
 *
 * Grows geometrically (×1.6) from a 3s base up to a 60s cap. Split into its
 * own module so the progression can be unit-tested in Node without loading
 * the full ws-dispatcher (which requires a browser WebSocket global).
 */

const WS_RECONNECT_BASE_MS = 3000;
const WS_RECONNECT_MAX_MS = 60000;
const WS_RECONNECT_FACTOR = 1.6;

function wsNextBackoff(current) {
    return Math.min(Math.round(current * WS_RECONNECT_FACTOR), WS_RECONNECT_MAX_MS);
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { WS_RECONNECT_BASE_MS, WS_RECONNECT_MAX_MS, WS_RECONNECT_FACTOR, wsNextBackoff };
} else {
    window.WsBackoff = { WS_RECONNECT_BASE_MS, WS_RECONNECT_MAX_MS, WS_RECONNECT_FACTOR, wsNextBackoff };
}
