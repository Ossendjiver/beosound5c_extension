const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../../../web/softarc/mass.html'), 'utf8');
const source = html.slice(html.indexOf("let moodQueue = ''"), html.indexOf('const getDetailSpec'));

for (const initial of ['idle', 'playing', 'unavailable']) {
    test(`Mood GO with a ${initial} queue`, async () => {
        const actions = [];
        let wheel;
        const seed = { uri: 'library://track/123', name: 'Seed', duration: 123 };
        const context = {
            libraryBase: 'http://library', sourceCommandUrl: 'http://mass/command', AbortSignal,
            activeMassTargetPayload: () => ({}), publishContextMenu() {}, async refreshQueueRoot() {},
            console: { warn() {} },
            arcList: { async sendGoWebhook(command) { actions.push(command); return {state: 'playing', queue_id: 'selected'}; } },
            window: {MoodWheel: {Wheel: class { constructor(options) { wheel = options; } open() {} }}},
            async fetch(url, options = {}) {
                const body = JSON.parse(options.body || '{}');
                if (url.endsWith('/command')) {
                    if (!body.queue_id && initial === 'unavailable') throw new Error('snapshot unavailable');
                    const item = { queue_item_id: 'reused-id', media_item: seed };
                    return {ok: true, json: async () => ({state: 'ok', queue_id: body.queue_id || 'old',
                        snapshot: {state: body.queue_id ? 'playing' : initial, current_item: item}})};
                }
                if (url.includes('/recommend/')) return {ok: true, json: async () => ({items: [seed]})};
                if (body.action) actions.push([body.action, body.queue_id]);
                return {ok: true, json: async () => ({active: true})};
            },
        };
        vm.runInNewContext(`let moodWheel; ${source}`, context);
        await wheel.onPlay({angle: 0, radius: 0.2});
        assert.deepEqual(actions, initial === 'playing' ? [['update', 'old']]
            : ['play_now', ['begin', 'selected'], ['update', 'selected']]);
    });
}
