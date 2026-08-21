const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

global.window = {
    location: { hostname: 'localhost' },
};

global.document = {
    getElementById() {
        return null;
    },
    dispatchEvent() {},
};

global.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
    }
};

const { MediaManager } = require('../../../web/js/media-manager.js');

function makeManager(activeSource = null) {
    const manager = Object.create(MediaManager.prototype);
    manager.activeSource = activeSource;
    manager.mediaInfo = {
        title: 'Old Title',
        artist: 'Old Artist',
        album: 'Old Album',
        artwork: 'old-artwork',
        back_artwork: 'old-back',
        canvas_url: 'old-canvas',
        music_video_url: 'old-video',
        track_id: 'old-track',
        relay_id: '',
        source_id: 'mass',
        state: 'playing',
        position: '1:23',
        duration: '4:56',
    };
    manager.activePlayingPreset = null;
    manager.updateNowPlayingView = () => {};
    return manager;
}

describe('MediaManager stale stopped payload handling', () => {
    it('normalizes stopped media without an active source to idle', () => {
        const manager = makeManager(null);
        manager.handleMediaUpdate({
            title: 'BBC News',
            artist: 'BBC',
            album: 'BBC News',
            artwork: 'stale-artwork',
            state: 'stopped',
        }, 'client_connect');

        assert.notEqual(manager.mediaInfo.title, 'BBC News');
        assert.notEqual(manager.mediaInfo.artist, 'BBC');
        assert.notEqual(manager.mediaInfo.album, 'BBC News');
        assert.equal(manager.mediaInfo.state, 'idle');
        assert.equal(manager.mediaInfo.artwork, '');
        assert.equal(manager.mediaInfo.track_id, '');
        assert.equal(manager.mediaInfo.source_id, '');
    });

    it('keeps stopped media when an active source still owns the page', () => {
        const manager = makeManager('mass');
        manager.handleMediaUpdate({
            title: 'BBC News',
            artist: 'BBC',
            album: 'BBC News',
            state: 'stopped',
        }, 'state_change');

        assert.equal(manager.mediaInfo.title, 'BBC News');
        assert.equal(manager.mediaInfo.artist, 'BBC');
        assert.equal(manager.mediaInfo.state, 'stopped');
    });

    it('keeps stopped relay media without an active source', () => {
        const manager = makeManager(null);
        manager.handleMediaUpdate({
            title: 'Apple TV',
            artist: 'Showing',
            album: 'Cinema',
            relay_id: 'showing',
            state: 'stopped',
        }, 'showing_relay');

        assert.equal(manager.mediaInfo.title, 'Apple TV');
        assert.equal(manager.mediaInfo.relay_id, 'showing');
        assert.equal(manager.mediaInfo.state, 'stopped');
    });
});

describe('MediaManager showing transport routing', () => {
    it('keeps showing transport available on PLAYING when relay media is active', () => {
        const manager = makeManager('mass');
        manager.handleMediaUpdate({
            title: 'Apple TV',
            artist: 'Showing',
            album: 'Cinema',
            relay_id: 'showing',
            state: 'paused',
        }, 'showing_relay');

        assert.equal(manager.shouldUseShowingAsPlaying(), false);
        assert.equal(manager.shouldRoutePlayingButtonsToShowing(), true);
        assert.equal(manager._hasShowingTransportTarget(), true);
    });

    it('does not route PLAYING buttons to showing once relay media is idle', () => {
        const manager = makeManager('mass');
        manager.handleMediaUpdate({
            title: '',
            artist: '',
            album: '',
            relay_id: 'showing',
            state: 'idle',
        }, 'showing_relay');

        assert.equal(manager.shouldRoutePlayingButtonsToShowing(), false);
    });
});
