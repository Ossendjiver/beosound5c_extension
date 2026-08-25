const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const {
    getLetter,
    speedToSteps,
    menuDeltaForSpeed,
    createMenuStepRamp,
    letterIntervalForSpeed,
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
        assert.equal(speedToSteps(5), 0);
        assert.equal(speedToSteps(6), 1);
        assert.equal(speedToSteps(9), 2);
        assert.equal(speedToSteps(11), 3);
        assert.equal(speedToSteps(127), 3);
    });

    it('uses the midpoint between the original and slower option-menu curves', () => {
        assert.equal(menuDeltaForSpeed(1), 0.52);
        assert.equal(menuDeltaForSpeed(5), 0.61);
        const ramp = createMenuStepRamp();
        assert.equal(ramp.push({ direction: 'clock', speed: 1 }, 100), 0);
        assert.equal(ramp.push({ direction: 'clock', speed: 1 }, 120), 1);

        ramp.reset();
        assert.equal(ramp.push({ direction: 'clock', speed: 10 }, 1000), 0);
        assert.equal(ramp.push({ direction: 'clock', speed: 10 }, 1010), 1);
        assert.equal(ramp.push({ direction: 'counter', speed: 10 }, 1020), 0);
    });

    it('interpolates letter cadence from 800 ms at speed 5 to 200 ms at 11', () => {
        assert.equal(letterIntervalForSpeed(5), 800);
        assert.equal(letterIntervalForSpeed(8), 500);
        assert.equal(letterIntervalForSpeed(11), 200);
        assert.equal(letterIntervalForSpeed(127), 200);
    });

    it('uses cadence as a maximum dwell while tracking natural letter progress', () => {
        const mode = createLetterMode({ enterInclusive: false, exitSamples: 1 });
        let timestamp = 100;
        for (let index = 0; index < 3; index += 1) {
            timestamp += 10;
            assert.equal(mode.push({ direction: 'clock', speed: 5 }, timestamp, 'A').active, false);
        }

        timestamp += 10;
        assert.equal(mode.push({ direction: 'clock', speed: 6 }, timestamp, 'A').active, false);
        timestamp += 10;
        assert.equal(mode.push({ direction: 'clock', speed: 6 }, timestamp, 'A').active, false);
        timestamp += 10;
        const entered = mode.push({ direction: 'clock', speed: 6 }, timestamp, 'A');
        assert.deepEqual(
            { active: entered.active, steps: entered.steps, entered: entered.entered },
            { active: true, steps: 0, entered: true },
        );

        for (let index = 0; index < 3; index += 1) {
            timestamp += 200;
            assert.equal(mode.push({ direction: 'clock', speed: 5 }, timestamp, 'A').steps, 0);
        }
        timestamp += 200;
        assert.equal(mode.push({ direction: 'clock', speed: 5 }, timestamp, 'A').steps, 1);

        timestamp += 100;
        assert.equal(mode.push({ direction: 'clock', speed: 5 }, timestamp, 'B').steps, 0);
        for (let index = 0; index < 3; index += 1) {
            timestamp += 200;
            assert.equal(mode.push({ direction: 'clock', speed: 5 }, timestamp, 'B').steps, 0);
        }
        timestamp += 200;
        assert.equal(mode.push({ direction: 'clock', speed: 5 }, timestamp, 'B').steps, 1);

        timestamp += 10;
        const state = mode.push({ direction: 'clock', speed: 2 }, timestamp, 'B');
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
