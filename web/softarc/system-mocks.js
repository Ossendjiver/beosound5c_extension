// Mock services for system.html — intercepts fetch and WebSocket in demo mode.
// Only loaded on Mac + localhost (see conditional <script> in system.html).
// Production devices (Raspberry Pi) never load this file.
(function () {
    // ── Mock data ──

    const services = {
        'beo-http': 'Running', 'beo-ui': 'Running', 'beo-input': 'Running',
        'beo-bluetooth': 'Running', 'beo-masterlink': 'Running',
        'beo-player-sonos': 'Running', 'beo-source-cd': 'Running',
        'beo-source-spotify': 'Running', 'beo-source-tidal': 'Running',
        'beo-source-usb': 'Inactive', 'beo-source-news': 'Running',
    };

    const system = {
        hostname: 'beosound5c-church', ip_address: '192.168.0.163',
        uptime: '3d 14h 22m', cpu_temp: '48.2°C', memory: '1.2G / 3.8G (32%)',
        git_tag: 'v0.9.2-dev',
        device_id: '8e671b06-063c-4fd2-a16f-e9105475c60f',
    };

    const config = { device: 'Church' };

    const router = {
        active_source: 'spotify', active_source_name: 'Spotify',
        active_player: 'sonos', output_device: 'BeoLab 5',
        volume: 35, transport_mode: 'webhook',
        sources: {
            spotify: { name: 'Spotify', state: 'playing' },
            cd: { name: 'CD', state: 'stopped' },
            radio: { name: 'Radio', state: 'stopped' },
        },
    };

    const player = {
        player: 'sonos', name: 'Sonos', speaker_name: 'Living Room',
        speaker_ip: '192.168.1.100', state: 'playing', volume: 35,
        ws_clients: 1, artwork_cache_size: 42, is_grouped: false,
        current_track: { title: 'Gymnopédie No. 1', artist: 'Erik Satie', album: 'Gymnopédies' },
    };

    const people = [
        { friendly_name: 'Demo User', state: 'home', entity_picture: null },
        { friendly_name: 'Guest', state: 'away', entity_picture: null },
        { friendly_name: 'Family', state: 'home', entity_picture: null },
    ];

    const fullConfig = {
        device: 'Church',
        menu: {
            'PLAYING': 'playing', 'JOIN': 'join', 'SPOTIFY': 'spotify',
            'RADIO': 'radio', 'TIDAL': 'tidal', 'SCENES': 'scenes',
            'SECURITY': { url: 'http://homeassistant.local:8123/cameras' },
            'SYSTEM': 'system', 'SHOWING': 'showing',
        },
        player: { type: 'sonos', ip: '192.168.0.190' },
        bluetooth: { remote_mac: 'AA:BB:CC:DD:EE:FF' },
        remote: { default_source: 'spotify' },
        home_assistant: {
            url: 'http://homeassistant.local:8123',
            webhook_url: 'http://homeassistant.local:8123/api/webhook/beosound5c',
        },
        transport: { mode: 'mqtt', mqtt_broker: 'homeassistant.local' },
        volume: { type: 'beolab5', host: 'beolab5-controller.local', max: 80, step: 3, output_name: 'BeoLab 5' },
        join: { default_player: 'Kitchen and Dining' },
        spotify: { client_id: '6420bddd82d046adb24b3009960c5d81', source: 'radio' },
        tidal: { source: 'amem' },
        showing: { entity_id: 'media_player.cinema_appletv' },
    };

    const btRemotes = [
        { mac: 'AA:BB:CC:DD:EE:01', name: 'BEORC', connected: true, rssi: -52, battery: 85 },
        { mac: 'AA:BB:CC:DD:EE:02', name: 'BEORC', connected: false, rssi: null, battery: 60 },
    ];

    const spotify = {
        display_name: 'Demo User', has_credentials: true, needs_reauth: false,
        state: 'playing', playlist_count: 24, fetching: false,
        last_refresh: new Date().toISOString(), last_refresh_duration: 4.2,
        digit_playlists: {
            '0': 'Liked Songs', '1': 'Discover Weekly', '2': 'Classical',
            '3': 'Jazz', '4': 'Lo-Fi', '5': 'Dinner',
            '6': 'Party', '7': 'Sleep', '8': 'Focus', '9': 'Workout',
        },
    };

    const tidal = {
        user_name: 'Demo User', has_credentials: true, needs_reauth: false,
        state: 'stopped', playlist_count: 18, fetching: false,
        last_refresh: new Date().toISOString(), last_refresh_duration: 3.8,
        digit_playlists: {
            '0': 'Favorites', '1': 'New Releases', '2': 'Classical',
            '3': 'Jazz', '4': 'Ambient', '5': 'Dinner',
            '6': 'Party', '7': 'Sleep', '8': 'Focus', '9': 'Workout',
        },
    };

    const cd = {
        drive_connected: true, disc_inserted: true,
        metadata: { artist: 'Nils Frahm', title: 'All Melody', musicbrainz: true, artwork: null },
        playback: { state: 'stopped', current_track: 3, total_tracks: 12 },
    };

    const masterlink = { usb_connected: true, volume: 35, mute: false, speakers_on: true };

    // Drift CPU temp and memory slightly each call so sparklines animate
    let baseTemp = 48.2;
    function drift(base, range) { return base + (Math.random() - 0.5) * range; }

    function statusResponse() {
        baseTemp = Math.max(40, Math.min(60, baseTemp + (Math.random() - 0.5) * 3));
        const memPct = Math.max(20, Math.min(50, 32 + (Math.random() - 0.5) * 8));
        return {
            status: 'ok',
            ...system,
            cpu_temp: baseTemp.toFixed(1) + '°C',
            memory: `1.2G / 3.8G (${memPct.toFixed(0)}%)`,
            config, services, git_tag: system.git_tag,
        };
    }

    // ── URL → response map ──

    function mockResponse(url) {
        if (url.includes(':8767/webhook'))       return statusResponse();
        if (url.includes(':8767/info'))          return { ip_address: '192.168.0.163', hostname: 'beosound5c-dev' };
        if (url.includes(':8770/router/status'))  return router;
        if (url.includes(':8766/player/status'))  return player;
        if (url.includes(':8767/bt/remotes'))     return btRemotes;
        if (url.includes(':8768/mixer/status'))   return masterlink;
        if (url.includes(':8771/status'))          return spotify;
        if (url.includes(':8777/status'))          return tidal;
        if (url.includes(':8769/status'))          return cd;
        if (url.includes(':8767/people'))          return people;
        if (url.includes('json/config.json'))      return fullConfig;
        if (url.includes(':8767/config'))          return { ok: true };
        if (url.includes('/discover/sonos'))    return [
            { ip: '192.168.0.190', name: 'Church Living' },
            { ip: '192.168.1.118', name: 'Kitchen' },
            { ip: '192.168.1.200', name: 'Office' },
        ];
        return null;
    }

    // ── Intercept fetch ──

    const _realFetch = window.fetch;
    window.fetch = function (url, options) {
        const urlStr = typeof url === 'string' ? url : url.url;

        // QR code: return a placeholder SVG as PNG-like response
        if (urlStr.includes('/qrcode')) {
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160">
                <rect width="160" height="160" fill="#000"/>
                <rect x="10" y="10" width="140" height="140" fill="#000" stroke="#fff" stroke-width="1"/>
                <text x="80" y="72" font-family="monospace" font-size="10" fill="#888" text-anchor="middle">QR code</text>
                <text x="80" y="88" font-family="monospace" font-size="10" fill="#555" text-anchor="middle">(mock mode)</text>
            </svg>`;
            const blob = new Blob([svg], { type: 'image/svg+xml' });
            return Promise.resolve(new Response(blob, { status: 200, headers: { 'Content-Type': 'image/svg+xml' } }));
        }

        const data = mockResponse(urlStr);
        if (data !== null) {
            return Promise.resolve(new Response(JSON.stringify(data), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
            }));
        }
        return _realFetch.apply(this, arguments);
    };

    // ── Intercept WebSocket (input service on :8765) ──

    const _RealWebSocket = window.WebSocket;
    window.WebSocket = function (url, protocols) {
        if (url.includes(':8765')) {
            const ws = {
                readyState: 1,
                OPEN: 1,
                send() {},
                close() { ws.readyState = 3; if (ws.onclose) ws.onclose(); },
            };
            setTimeout(() => {
                if (ws.onopen) ws.onopen();
                if (ws.onmessage) {
                    ws.onmessage({ data: JSON.stringify({ type: 'system_info', git_tag: system.git_tag }) });
                }
            }, 50);
            return ws;
        }
        return protocols ? new _RealWebSocket(url, protocols) : new _RealWebSocket(url);
    };
    Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });

    console.log('[SYSTEM] Mock services active (demo mode)');
})();
