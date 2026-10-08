const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
    constructor() {
        this.hidden = false;
        this.children = [];
        this.listeners = {};
        this.classes = new Set();
        this.classList = {
            toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
            contains: name => this.classes.has(name),
        };
    }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    querySelectorAll() { return this.children; }
}
function harness() {
    const elements = Object.fromEntries(['context-suggestion-overlay', 'context-suggestion-question', 'context-suggestion-options'].map(id => [id, new Element()]));
    elements['context-suggestion-overlay'].hidden = true;
    const requests = [];
    const timers = new Map();
    let timerId = 0;
    let underlyingInputs = 0;
    const ctx = vm.createContext({
        window: {
            addEventListener() {},
            location: { protocol: 'http:', hostname: 'fixture.local' },
            WsBackoff: { wsNextBackoff: n => n, WS_RECONNECT_BASE_MS: 3000 },
            AppConfig: {},
            PlaybackTargets: { handleNav() { underlyingInputs++; }, handleButton() { underlyingInputs++; } },
            ImmersiveMode: { consumeUserActivity() { underlyingInputs++; return true; } },
        },
        AppConfig: { websocket: { input: '' } },
        document: {
            getElementById: id => elements[id] || null,
            createElement: () => new Element(),
            addEventListener() {}, dispatchEvent() {},
        },
        console: { log() {}, warn() {}, error() {} },
        CustomEvent: class {},
        setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        setInterval() {}, clearInterval() {},
        fetch: async (url, options) => { requests.push({ url, ...options }); return { ok: true }; },
    });
    for (const name of ['hardware-input', 'ws-dispatcher']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../../../web/js', name + '.js'), 'utf8'), ctx);
    }
    const ui = { currentRoute: 'menu/playing', handleWheelChange() { underlyingInputs++; } };
    return {
        ctx, requests, elements, timers, ui,
        get underlyingInputs() { return underlyingInputs; },
        buttons: () => elements['context-suggestion-options'].children,
        show: options => ctx.showContextSuggestion({ id: 'fixture-prompt', kind: 'music', question: 'Would you like to play some music?', options }),
        wheel: direction => ctx.handleNavEvent(ui, { direction }),
        go: () => ctx.handleButtonEvent(ui, { button: 'go' }),
        right: () => ctx.handleButtonEvent(ui, { button: 'right' }),
    };
}
const choices = [{ id: 'play', label: 'Yes' }, { id: 'dismiss', label: 'No thanks' }];

test('wheel highlights options without playing or dismissing; GO selects highlighted answer', () => {
    const h = harness();
    h.show(choices);
    assert.equal(h.buttons()[0].classList.contains('selected'), true);
    h.wheel('clock');
    assert.equal(h.buttons()[0].classList.contains('selected'), false);
    assert.equal(h.buttons()[1].classList.contains('selected'), true);
    assert.equal(h.requests.length, 0);
    h.go();
    assert.equal(h.requests.length, 1);
    assert.equal(JSON.parse(h.requests[0].body).action, 'dismiss');
    assert.equal(h.elements['context-suggestion-overlay'].hidden, true);
    assert.equal(h.underlyingInputs, 0);
});

test('counter-clockwise returns to Yes; repeated wheel steps clamp at either end', () => {
    const h = harness(); h.show(choices);
    h.wheel('clock'); h.wheel('clock'); h.wheel('counter'); h.wheel('counter');
    assert.equal(h.buttons()[0].classList.contains('selected'), true);
    h.go();
    assert.equal(JSON.parse(h.requests[0].body).action, 'play');
});

test('three-option prompt keeps all options reachable and GO selects the third', () => {
    const h = harness();
    h.show([{ id: 'choice-a', label: 'Option A' }, { id: 'choice-b', label: 'Option B' }, { id: 'dismiss', label: 'No thanks' }]);
    h.wheel('clock'); h.wheel('clock');
    assert.equal(h.buttons()[2].classList.contains('selected'), true);
    h.go();
    assert.equal(JSON.parse(h.requests[0].body).action, 'dismiss');
});

test('RIGHT dismisses without leaking a skip command', () => {
    const h = harness(); h.show(choices); h.right();
    assert.equal(JSON.parse(h.requests[0].body).action, 'dismiss');
    assert.equal(h.underlyingInputs, 0);
});

test('repeated prompt refresh preserves the highlighted choice', () => {
    const h = harness(); h.show(choices); h.wheel('clock'); h.show(choices);
    assert.equal(h.buttons()[1].classList.contains('selected'), true);
    assert.equal(h.requests.length, 0);
});

test('expired prompt releases input and sends no action', () => {
    const h = harness(); h.show(choices);
    [...h.timers.values()].find(timer => timer.delay === 12 * 60 * 1000).fn();
    assert.equal(h.ctx.window.ContextSuggestions.isActive, false);
    assert.equal(h.ctx.window.ContextSuggestions.handleButton('go'), false);
    assert.equal(h.requests.length, 0);
});

test('click selection still submits the clicked answer once', () => {
    const h = harness(); h.show(choices);
    h.buttons()[1].listeners.click(); h.buttons()[1].listeners.click();
    assert.equal(h.requests.length, 1);
    assert.equal(JSON.parse(h.requests[0].body).action, 'dismiss');
});

test('invalid choices do not capture the input wheel', () => {
    const h = harness(); h.show([{ label: 'Missing action' }]);
    assert.equal(h.ctx.window.ContextSuggestions.isActive, false);
    assert.equal(h.ctx.window.ContextSuggestions.handleNav({ direction: 'clock' }), false);
});


test('PLAYING transport switches between configured fallback and a newly selected direct source', () => {
    const h = harness();
    h.ctx.window.AppConfig.showing = { entityId: 'configured-at-runtime', exclusivePlayingFallback: true };
    const stalePreset = {};
    const directButtons = [];
    const fallbackButtons = [];
    h.ctx.window.SourcePresets = {
        direct: { playing: stalePreset, controller: { isActive: true, handleButton(button) { directButtons.push(button); return true; } } },
    };
    h.ui.activePlayingPreset = stalePreset;
    h.ui.activeSource = null;
    h.ui.media = {
        shouldRoutePlayingButtonsToShowing: () => !h.ui.activeSource,
        handleShowingButton(button) { fallbackButtons.push(button); return true; },
    };
    h.go();
    assert.deepEqual(fallbackButtons, ['go']);
    assert.deepEqual(directButtons, []);
    h.ui.activeSource = 'direct';
    h.go();
    assert.deepEqual(directButtons, ['go']);
    assert.deepEqual(fallbackButtons, ['go']);
    h.ui.activeSource = null;
    h.go();
    assert.deepEqual(fallbackButtons, ['go', 'go']);
    assert.equal(h.requests.length, 0);
});
