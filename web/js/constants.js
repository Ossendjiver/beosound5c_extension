/**
 * Centralized constants for BeoSound 5c frontend
 *
 * This file consolidates all magic numbers and configuration values
 * that were previously scattered across multiple files.
 *
 * Arc geometry constants (radius, centerX, centerY, menuAngleMin, menuAngleMax)
 * are derived from Beolyd5 by Lars Baunwall:
 * https://github.com/larsbaunwall/Beolyd5
 * Licensed under Apache License 2.0
 */

const Constants = {
    // Arc geometry (used by ui.js, arcs.js)
    arc: {
        radius: 1000,
        centerX: 1147,
        centerY: 387,
        menuAngleMin: 158,
        menuAngleMax: 202,
        menuAngleStep: 5
    },

    // Laser position mapping (from laser-position-mapper.js)
    laser: {
        minPosition: 3,
        midPosition: 72,
        maxPosition: 123,
        minAngle: 150,
        midAngle: 180,
        maxAngle: 210,
        defaultPosition: 93
    },

    // Overlay transition thresholds
    overlays: {
        topOverlayStart: 160,     // Below this angle = 'menu/showing'
        bottomOverlayStart: 200   // Above this angle = 'menu/playing'
    },

    // Timeouts (in milliseconds)
    timeouts: {
        websocketReconnect: 3000,
        websocketConnectionTimeout: 1000,
        cursorHide: 2000,
        volumeProcessing: 50,
        splashFadeDelay: 500,
        splashRemoveDelay: 800,
        viewTransition: 250,
        artworkFadeIn: 100,
        artworkFadeInComplete: 20,
        iframeFocusDelay: 200,
        iframePointerEventsDelay: 100,
        wsInitDelay: 100,
        scrollIndicatorFade: 3000,
        scrollIndicatorShow: 1500
    },

    // Animation durations (CSS-compatible values)
    animations: {
        artworkFade: '0.6s',
        contentFade: '0.25s'
    },

    // WebSocket configuration
    websocket: {
        inputPort: 8765,
        mediaPort: 8766,
        maxReconnectDelay: 30000,  // Max delay for exponential backoff
        logThrottle: 1000
    },

    // Menu items (static views only — dynamic sources and webpages are added by the router)
    menuItems: [
        { title: 'PLAYING', path: 'menu/playing' },
        { title: 'SCENES', path: 'menu/scenes' },
        { title: 'SYSTEM', path: 'menu/system' },
        { title: 'SHOWING', path: 'menu/showing' }
    ],

    // Iframe mappings (IDs match the preloaded iframe elements)
    // Source iframes (spotify, apple_music, etc.) self-register via SourcePresets
    iframes: {
        'menu/scenes': 'preload-scenes',
        'menu/system': 'system-iframe'
    },

    // Softarc positioning (shared by ArcList, CD view, Spotify view, etc.)
    softarc: {
        scrollSpeed: 0.5,
        scrollStep: 0.5,
        snapDelay: 1000,
        middleIndex: 4,
        baseItemSize: 128,
        maxRadius: 220,
        horizontalMultiplier: 0.35,
        baseXOffset: 100
    },

    // Placeholder artwork SVGs.
    // `blank`      : fully transparent — used during idle/boot so nothing flashes
    // `noArtwork`  : silent dark square with a subtle vinyl-circle glyph; shown
    //                when media is actually playing but the service returned no art
    // `showing`    : same aesthetic for the Apple-TV SHOWING view
    placeholders: {
        blank: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        noArtwork: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><rect width='200' height='200' fill='%231a1a1a'/><circle cx='100' cy='100' r='62' stroke='%23333' stroke-width='1.5' fill='none'/><circle cx='100' cy='100' r='24' stroke='%23333' stroke-width='1' fill='none'/><circle cx='100' cy='100' r='4' fill='%23333'/></svg>",
        artworkUnavailable: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><rect width='200' height='200' fill='%231a1a1a'/><circle cx='100' cy='100' r='62' stroke='%23333' stroke-width='1.5' fill='none'/><circle cx='100' cy='100' r='24' stroke='%23333' stroke-width='1' fill='none'/><circle cx='100' cy='100' r='4' fill='%23333'/></svg>",
        showing: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><rect width='200' height='200' fill='%23151515'/><circle cx='100' cy='100' r='62' stroke='%23333' stroke-width='1.5' fill='none'/><circle cx='100' cy='100' r='24' stroke='%23333' stroke-width='1' fill='none'/><circle cx='100' cy='100' r='4' fill='%23333'/></svg>"
    }
};

// Make available globally
window.Constants = Constants;

// Freeze to prevent accidental modification
Object.freeze(Constants);
Object.freeze(Constants.arc);
Object.freeze(Constants.laser);
Object.freeze(Constants.overlays);
Object.freeze(Constants.timeouts);
Object.freeze(Constants.animations);
Object.freeze(Constants.websocket);
Object.freeze(Constants.iframes);
Object.freeze(Constants.softarc);
Object.freeze(Constants.placeholders);
Constants.menuItems.forEach(item => Object.freeze(item));
Object.freeze(Constants.menuItems);
