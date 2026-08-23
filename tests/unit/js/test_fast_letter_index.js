const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const {
    getLetter,
    speedToSteps,
    menuDeltaForSpeed,
    createMenuStepRamp,
    createLetterMode,
    findJumpIndex,
} = require('../../../web/js/fast-letter-index.js');


describe('fast letter indexing', () => {
    const items = [
        '1 Giant Leap',
        'A Day in the Life',
        'Across the Universe',
        'Blue Monday',
        'Breathe',
        'Come Together',
        'The Crystal Ship',
        'Dreams',
    ].map((name) => ({ name }));

    it('normalizes numbers and leading articles into useful index buckets', () => {
        assert.equal(getLetter('1 Giant Leap'), '#');
        assert.equal(getLetter('A Day in the Life'), 'D');
        assert.equal(getLetter('The Crystal Ship'), 'C');
    });

    it('maps the measured physical wheel range into useful letter tiers', () => {
        assert.equal(speedToSteps(2), 0);
        assert.equal(speedToSteps(3), 1);
        assert.equal(speedToSteps(7), 2);
        assert.equal(speedToSteps(11), 3);
        assert.equal(speedToSteps(127), 3);
    });

    it('uses fractional low-speed accumulation for precise option menus', () => {
        assert.ok(menuDeltaForSpeed(1) < menuDeltaForSpeed(5));
        const ramp = createMenuStepRamp();
        let emitted = 0;
        for (let index = 0; index < 24; index += 1) {
            emitted += ramp.push({ direction: 'clock', speed: 1 }, 100 + index * 20);
        }
        assert.equal(emitted, 0);
        assert.equal(ramp.push({ direction: 'clock', speed: 1 }, 580), 1);

        ramp.reset();
        assert.equal(ramp.push({ direction: 'clock', speed: 10 }, 1000), 0);
        assert.equal(ramp.push({ direction: 'clock', speed: 10 }, 1010), 1);
        assert.equal(ramp.push({ direction: 'counter', speed: 10 }, 1020), 0);
    });

    it('enters iPod-style letter mode only after sustained fast samples', () => {
        const mode = createLetterMode();
        let timestamp = 100;
        for (let index = 0; index < 40; index += 1) {
            timestamp += 20;
            assert.equal(mode.push({ direction: 'clock', speed: 1 }, timestamp).active, false);
        }

        timestamp += 10;
        assert.equal(mode.push({ direction: 'clock', speed: 7 }, timestamp).active, false);
        timestamp += 10;
        assert.equal(mode.push({ direction: 'clock', speed: 7 }, timestamp).active, false);
        timestamp += 10;
        const entered = mode.push({ direction: 'clock', speed: 7 }, timestamp);
        assert.deepEqual(
            { active: entered.active, steps: entered.steps, entered: entered.entered },
            { active: true, steps: 1, entered: true },
        );

        timestamp += 20;
        assert.equal(mode.push({ direction: 'clock', speed: 7 }, timestamp).steps, 0);
        timestamp += 100;
        assert.equal(mode.push({ direction: 'clock', speed: 7 }, timestamp).steps, 1);

        let state = null;
        for (let index = 0; index < 8; index += 1) {
            timestamp += 10;
            state = mode.push({ direction: 'clock', speed: 1 }, timestamp);
        }
        assert.equal(state.active, false);
        assert.equal(state.exited, true);
    });

    it('jumps forward to the first entry in the next letter group', () => {
        assert.equal(findJumpIndex(items, 3, 'clock', 1), 5);
        assert.equal(findJumpIndex(items, 3, 'clock', 2), 7);
    });

    it('jumps backward to the first entry in the previous letter group', () => {
        assert.equal(findJumpIndex(items, 7, 'counter', 1), 5);
        assert.equal(findJumpIndex(items, 7, 'counter', 2), 3);
    });

    it('clamps at both ends of the list', () => {
        assert.equal(findJumpIndex(items, 0, 'counter', 3), 0);
        assert.equal(findJumpIndex(items, items.length - 1, 'clock', 3), items.length - 1);
    });
});
