const { test } = require('node:test');
const assert = require('node:assert/strict');
global.window = {};
global.document = { dispatchEvent() {}, addEventListener() {} };
global.CustomEvent = class { constructor(type, detail) { Object.assign(this, detail); } };
const { MediaManager } = require('../../../web/js/media-manager.js');
function manager(activeSource = null) {
    const media = Object.create(MediaManager.prototype);
    media.activeSource = activeSource;
    media.mediaInfo = { title: 'Old track', artwork: 'old.jpg', canvas_url: 'old.mp4', source_id: '' };
    media.updateNowPlayingView = () => {};
    return media;
}
for (const state of ['idle', 'stopped', 'off', 'standby', 'unknown', 'unavailable']) {
    test(`idle fallback clears title and artwork: ${state}`, () => {
        const media = manager();
        media.handleMediaUpdate({ relay_id: 'showing', state, title: 'Cached title', artwork: 'cached.jpg' });
        assert.equal(media.mediaInfo.title, '—');
        assert.equal(media.mediaInfo.artwork, '');
        assert.equal(media.mediaInfo.canvas_url, '');
        assert.equal(media.mediaInfo.state, 'idle');
    });
}
test('empty router response clears an old display', () => {
    const media = manager();
    media.handleMediaUpdate({}, 'view_entry_resync');
    assert.equal(media.mediaInfo.title, '—');
    assert.equal(media.mediaInfo.artwork, '');
});
test('paused direct BS5c playback retains its title', () => {
    const media = manager('mass');
    media.handleMediaUpdate({ title: 'Direct track', state: 'paused', artwork: 'direct.jpg', _source_id: 'mass' });
    assert.equal(media.mediaInfo.title, 'Direct track');
    assert.equal(media.mediaInfo.artwork, 'direct.jpg');
});
test('active SHOWING fallback still renders its metadata', () => {
    const media = manager();
    media.handleMediaUpdate({ title: 'Lounge track', state: 'playing', artwork: 'lounge.jpg', relay_id: 'showing' });
    assert.equal(media.mediaInfo.title, 'Lounge track');
    assert.equal(media.shouldUseShowingAsPlaying(), true);
});

test('direct source takes control immediately even while old relay metadata remains', () => {
    const media = manager('mass');
    media.mediaInfo = { relay_id: 'showing', state: 'playing', title: 'Old fallback' };
    assert.equal(media.hasActiveShowingRelay(), true);
    assert.equal(media.shouldRoutePlayingButtonsToShowing(), false);
    media.activeSource = null;
    assert.equal(media.shouldRoutePlayingButtonsToShowing(), true);
});
