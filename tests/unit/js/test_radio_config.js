/**
 * Structural tests for the Radio Favourites Config UI in
 * web/softarc/config.html.
 *
 * The card lays out 14 fixed slot rows (digits 0–9 + RED/GREEN/YELLOW/BLUE)
 * regardless of how many favourites exist; the user picks a station for
 * each slot via a popup browser. These tests pin invariants that, when
 * broken, silently regress the feature — endpoint URLs, button slot
 * definitions, and the wiring between buildConfig / save / reload.
 *
 * Run with: node --test tests/unit/js/test_radio_config.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const HTML = fs.readFileSync(
    path.join(__dirname, '../../../web/softarc/config.html'), 'utf8');

// Pull the inline <script> block out of config.html. There's exactly
// one big script block at the bottom; tests run small slices of it in
// a sandboxed vm context where helpful.
function extractScript() {
    const m = HTML.match(/<script>([\s\S]*?)<\/script>/);
    assert.ok(m, 'expected an inline <script> block in config.html');
    return m[1];
}

describe('Radio Favourites — DOM elements', () => {
    it('Radio Favourites card has a list container and intro hint', () => {
        // No empty-state — the card always renders 14 fixed rows. The
        // intro must explain the slot/alias/colour-override semantics so
        // a fresh user understands what to type into the alias field.
        assert.ok(/cfg-rf-list/.test(HTML), 'missing #cfg-rf-list container');
        assert.ok(/Map BeoRemote digit/i.test(HTML),
            'card intro must explain the slot mapping');
        assert.ok(/colour buttons override/i.test(HTML),
            'card intro must call out colour-button override behaviour');
    });

    it('Add-station picker modal is in the document', () => {
        // The modal must exist outside the card so it overlays correctly
        // (z-index + position:fixed). Check both the wrapper id and the
        // list it populates.
        assert.ok(/id="cfg-rf-modal"/.test(HTML), 'missing #cfg-rf-modal');
        assert.ok(/id="cfg-rf-modal-list"/.test(HTML), 'missing #cfg-rf-modal-list');
        assert.ok(/id="cfg-rf-modal-close"/.test(HTML), 'missing close button');
    });

    it('Slot row renders label, station picker, alias input, and test button', () => {
        const js = extractScript();
        // The renderer template literal must produce all four controls
        // (otherwise the row layout collapses).
        assert.ok(/cfg-rf-slot-row/.test(js), 'missing row container class');
        assert.ok(/cfg-rf-slot-label/.test(js), 'missing slot label class');
        assert.ok(/cfg-rf-slot-station/.test(js), 'missing station picker button class');
        assert.ok(/cfg-rf-slot-alias/.test(js), 'missing alias input class');
        assert.ok(/cfg-rf-slot-test/.test(js), 'missing test-play button class');
    });

    it('CSS uses a fixed-column grid so all 14 rows align', () => {
        // The new layout is grid-based, not flex. Pin grid-template-columns
        // so a future flex/grid revert doesn't silently misalign rows.
        const css = HTML.split('</style>')[0];
        const rowMatch = css.match(/\.cfg-rf-slot-row\s*\{[^}]*\}/);
        assert.ok(rowMatch, 'no .cfg-rf-slot-row CSS block');
        assert.ok(/display:\s*grid/.test(rowMatch[0]),
            'rows must use grid layout for alignment');
        assert.ok(/grid-template-columns/.test(rowMatch[0]),
            'rows must declare grid-template-columns');
    });
});


describe('Radio Favourites — button slot definitions', () => {
    // Spin up the slot-defining code in a sandbox so we can inspect the
    // actual exports rather than parsing them out of source as strings.
    const js = extractScript();
    const slotSnippet = js.match(/const RADIO_BUTTON_SLOTS = \[([\s\S]*?)\];/);
    assert.ok(slotSnippet, 'missing RADIO_BUTTON_SLOTS definition');

    const ctx = vm.createContext({});
    vm.runInContext(`const RADIO_BUTTON_SLOTS = [${slotSnippet[1]}]; this.slots = RADIO_BUTTON_SLOTS;`, ctx);
    const slots = ctx.slots;

    it('exposes 0–9 plus four colour buttons (14 slots total)', () => {
        const keys = slots.map(s => s.key);
        for (const d of '0123456789') {
            assert.ok(keys.includes(d), `missing digit slot ${d}`);
        }
        for (const c of ['red', 'green', 'yellow', 'blue']) {
            assert.ok(keys.includes(c), `missing colour slot ${c}`);
        }
        assert.equal(slots.length, 14);
    });

    it('every slot has a label', () => {
        for (const s of slots) {
            assert.ok(s.label, `slot ${s.key} missing label`);
        }
    });

    it('colour slots are flagged as colour=true', () => {
        // The flag is what renderRow uses to pick the cfg-rf-slot-label-<key>
        // CSS class so the colour swatch styling kicks in.
        for (const c of ['red', 'green', 'yellow', 'blue']) {
            const slot = slots.find(s => s.key === c);
            assert.equal(slot.color, true, `${c} should have color:true`);
        }
    });
});


describe('Radio Favourites — endpoint wiring', () => {
    const js = extractScript();

    it('hits port 8779 for /favourites GET', () => {
        // The Config UI loads the favourite list from the radio service.
        // If this URL drifts (e.g., from 8779 to something else without
        // updating the Worker / install scripts), the card stays empty.
        assert.ok(/:8779\/favourites/.test(js),
            'expected GET to :8779/favourites');
    });

    it('POSTs alias updates to /favourites/short_name', () => {
        // The alias input saves immediately on debounced input. The
        // endpoint name is contractual with the radio service.
        assert.ok(/\/favourites\/short_name/.test(js),
            'expected /favourites/short_name endpoint');
        // Must POST JSON (CORS preflight depends on this).
        const aliasFn = js.match(/scheduleAliasSave[\s\S]*?\}\s*,\s*\d+\)\)/);
        assert.ok(aliasFn, 'missing scheduleAliasSave function');
        assert.ok(/method:\s*['"]POST['"]/.test(aliasFn[0]),
            'alias save must use POST');
        assert.ok(/Content-Type[^]*application\/json/.test(aliasFn[0]),
            'alias save must send application/json');
    });

    it('Picker uses /browse?path=… and posts add_favourite via /command', () => {
        // The picker hierarchy is the same /browse the on-device UI uses
        // — keeping these aligned means the user sees the same
        // submenus on the device and in the Config UI.
        assert.ok(/\/browse\?path=/.test(js), 'expected /browse?path= endpoint');
        // When the user picks a station from a non-favourites view, we
        // must persist it via add_favourite so the binding resolves later.
        assert.ok(/command:\s*['"]add_favourite['"]/.test(js),
            'expected add_favourite command on pick');
    });
});


describe('Radio Favourites — buildConfig / station_buttons round-trip', () => {
    const js = extractScript();

    it('saveConfig writes radio.station_buttons only when bindings exist', () => {
        // buildConfig() copies non-empty bindings to cfg.radio.station_buttons
        // and removes the key (and the parent radio block) when there are
        // none. Tests the *intent* by string-matching the relevant lines —
        // executing buildConfig wholesale would require a JSDOM.
        const m = js.match(/Radio button bindings[\s\S]{0,800}/);
        assert.ok(m, 'missing "Radio button bindings" block in buildConfig');
        assert.ok(/radioButtons\[k\]\s*=\s*v/.test(m[0]),
            'must copy non-empty bindings into the saved object');
        assert.ok(/cfg\.radio\.station_buttons\s*=\s*radioButtons/.test(m[0]),
            'must assign to cfg.radio.station_buttons');
        assert.ok(/delete\s+cfg\.radio\.station_buttons/.test(m[0]),
            'must clear station_buttons when empty (so saved config is tidy)');
    });

    it('initial bindings seed from cfg.radio.station_buttons', () => {
        // Without this seed, refreshes of the page would lose unsaved
        // edits AND the saved bindings — silent data loss.
        assert.ok(/cfg\.radio\s*&&\s*cfg\.radio\.station_buttons/.test(js),
            'must read existing station_buttons from cfg on render');
    });

    it('slot picker reassignment evicts duplicate uuids and re-renders', () => {
        // The station-picker callback (onSlotStationChange) is the single
        // mutation entry point. It must (a) clear any other slot that
        // currently holds the same uuid, (b) trigger a re-render so the
        // dropdown options recompute their disabled state.
        const fn = js.match(/function onSlotStationChange[\s\S]*?\n\}/);
        assert.ok(fn, 'missing onSlotStationChange function');
        assert.ok(/refreshRadioFavouritesList\(\)/.test(fn[0]),
            'binding change must trigger a re-render');
        // Must remove the uuid from any other slot before assigning.
        assert.ok(/v === uuid[\s\S]{0,80}delete _radioBindings/.test(fn[0]),
            'must evict uuid from previous slot before reassigning');
    });
});


describe('Radio Favourites — test-play button', () => {
    const js = extractScript();

    it('row template includes a per-slot test button', () => {
        // Row markup must include the test button class — without it,
        // the test-play feature is invisible. Disabled when the slot has
        // no station (or has a dangling uuid) so the user can't fire a
        // play on a station that doesn't exist.
        assert.ok(/cfg-rf-slot-test/.test(js),
            'missing .cfg-rf-slot-test in renderRow');
        assert.ok(/data-slot="\$\{esc\(slot\.key\)\}"/.test(js),
            'test button must carry data-slot for the click handler');
    });

    it('testPlayBySlot calls play_station with the bound uuid', () => {
        // testPlayBySlot deliberately uses play_station (uuid-direct) rather
        // than the digit/play_button path — the digit/button mapping only
        // takes effect after Save & Restart, but the user expects "test"
        // to play their *current dropdown choice* immediately.
        const fnSrc = js.match(/async function testPlayBySlot[\s\S]*?\n\}/)[0];
        const captured = [];
        const ctx = vm.createContext({
            _radioBindings: { '3': 'uuid-b', red: 'uuid-c' },
            RADIO_URL_PREFIX: () => 'http://test:8779',
            AbortSignal: { timeout: () => null },
            CSS: { escape: (s) => s },
            document: {
                querySelector: () => ({
                    classList: { add(){}, remove(){} },
                    disabled: false,
                }),
            },
            fetch: async (url, opts) => {
                captured.push({ url, body: JSON.parse(opts.body) });
                return { ok: true, status: 200,
                         json: async () => ({ status: 'ok' }) };
            },
            setTimeout: (fn) => { fn(); return 0; },
        });
        vm.runInContext(fnSrc, ctx);
        vm.runInContext('this._fn = testPlayBySlot;', ctx);

        // Digit slot
        return ctx._fn('3').then(() => {
            assert.equal(captured[0].body.command, 'play_station');
            assert.equal(captured[0].body.stationuuid, 'uuid-b');
            // Colour slot — same command (uuid-direct), different uuid
            captured.length = 0;
            return ctx._fn('red').then(() => {
                assert.equal(captured[0].body.command, 'play_station');
                assert.equal(captured[0].body.stationuuid, 'uuid-c');
            });
        });
    });

    it('CSS includes test-button visual feedback states', () => {
        const css = HTML.split('</style>')[0];
        assert.ok(/\.cfg-rf-slot-test\.playing/.test(css),
            '.playing class missing — no green confirmation');
        assert.ok(/\.cfg-rf-slot-test\.error/.test(css),
            '.error class missing — no red failure indicator');
    });
});


describe('Radio Favourites — custom stream URL', () => {
    const js = extractScript();

    it('picker root surfaces a "Custom stream URL…" entry', () => {
        // The user must be able to discover the custom URL flow.
        // Marker: the data-custom attribute on the modal entry.
        assert.ok(/data-custom="1"/.test(js),
            'missing [data-custom] entry in picker root');
        assert.ok(/Custom stream URL/.test(js),
            'missing user-visible "Custom stream URL" label');
    });

    it('showCustomUrlForm renders name + url inputs', () => {
        const fn = js.match(/function showCustomUrlForm[\s\S]*?\n\}/);
        assert.ok(fn, 'missing showCustomUrlForm');
        assert.ok(/cfg-rf-custom-name/.test(fn[0]), 'name input id missing');
        assert.ok(/cfg-rf-custom-url/.test(fn[0]), 'url input id missing');
        // Cancel + add buttons
        assert.ok(/data-custom-cancel/.test(fn[0]), 'cancel button missing');
        assert.ok(/data-custom-add/.test(fn[0]), 'add button missing');
    });

    it('submitCustomUrl validates name and URL scheme', () => {
        // Name required, URL must start with http:// or https://. Without
        // this, users can save dead entries that the player rejects later.
        const fn = js.match(/async function submitCustomUrl[\s\S]*?\n\}/);
        assert.ok(fn, 'missing submitCustomUrl');
        assert.ok(/Name is required/.test(fn[0]),
            'missing name-required message');
        assert.ok(/\^https\?:\\\/\\\//.test(fn[0]),
            'missing http(s) URL validation regex');
    });

    it('submitCustomUrl posts add_favourite with custom-prefixed uuid', () => {
        const fn = js.match(/async function submitCustomUrl[\s\S]*?\n\}/)[0];
        // Custom entries must use the "custom-" prefix so the backend
        // can apply different validation (URL required) and so future
        // migrations can spot user-entered stations.
        assert.ok(/custom-\$\{Date\.now/.test(fn) || /`custom-\$\{/.test(fn),
            'custom uuid prefix missing');
        assert.ok(/command:\s*['"]add_favourite['"]/.test(fn),
            'must POST add_favourite');
    });
});


describe('Radio Favourites — onSlotStationChange logic', () => {
    // Run the actual function in a vm context with a stub _radioBindings.
    const js = extractScript();
    const fnSrc = js.match(/function onSlotStationChange[\s\S]*?\n\}/)[0];

    function freshCtx(initialBindings) {
        const ctx = vm.createContext({
            _radioBindings: { ...initialBindings },
            refreshRadioFavouritesList: () => {},
        });
        vm.runInContext(fnSrc, ctx);
        return ctx;
    }

    it('reassigns slot from one uuid to another', () => {
        const ctx = freshCtx({ red: 'uuid-a' });
        vm.runInContext('onSlotStationChange("red", "uuid-b")', ctx);
        assert.deepEqual(ctx._radioBindings, { red: 'uuid-b' });
    });

    it('clears existing slot for a uuid before setting a new one', () => {
        // Each station can be bound to at most one slot. Reassigning a
        // station from "1" to "red" must drop "1".
        const ctx = freshCtx({ '1': 'uuid-a' });
        vm.runInContext('onSlotStationChange("red", "uuid-a")', ctx);
        assert.deepEqual(ctx._radioBindings, { red: 'uuid-a' });
    });

    it('empty uuid clears the slot', () => {
        const ctx = freshCtx({ '5': 'uuid-a', red: 'uuid-b' });
        vm.runInContext('onSlotStationChange("5", "")', ctx);
        // unbinding "5" leaves red alone
        assert.deepEqual(ctx._radioBindings, { red: 'uuid-b' });
    });

    it('switching a slot evicts its previous owner', () => {
        // If red was bound to uuid-a, and we set red → uuid-b, uuid-a
        // must end up unbound (no orphan binding).
        const ctx = freshCtx({ red: 'uuid-a' });
        vm.runInContext('onSlotStationChange("red", "uuid-b")', ctx);
        assert.deepEqual(ctx._radioBindings, { red: 'uuid-b' });
        assert.ok(!Object.values(ctx._radioBindings).includes('uuid-a'),
            'uuid-a must be evicted');
    });
});
