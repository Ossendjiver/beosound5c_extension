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

    const enterLetterMode = (mode, startAt = 100, direction = 'clock') => {
        assert.equal(mode.push({ direction, speed: 4 }, startAt, 'A').active, false);
        assert.equal(mode.push({ direction, speed: 4 }, startAt + 20, 'A').active, false);
        const entered = mode.push({ direction, speed: 4 }, startAt + 40, 'A');
        assert.equal(entered.active, true);
        assert.equal(entered.entered, true);
        return startAt + 40;
    };

    it('normalizes numbers and leading articles into useful index buckets', () => {
        assert.equal(getLetter('1 Giant Leap'), '#');
        assert.equal(getLetter('A Day in the Life'), 'D');
        assert.equal(getLetter('The Crystal Ship'), 'C');
    });

    it('maps the measured physical wheel range into useful letter tiers', () => {
        assert.equal(speedToSteps(3), 0);
        assert.equal(speedToSteps(4), 1);
        assert.equal(speedToSteps(7), 2);
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

    it('enters from measured velocity across time rather than a burst of packets', () => {
        const mode = createLetterMode();
        assert.equal(mode.push({ direction: 'clock', speed: 4 }, 100, 'A').active, false);
        assert.equal(mode.push({ direction: 'clock', speed: 4 }, 105, 'A').active, false);
        assert.equal(mode.push({ direction: 'clock', speed: 4 }, 110, 'A').active, false);
        assert.equal(mode.push({ direction: 'clock', speed: 4 }, 120, 'A').active, false);
        const entered = mode.push({ direction: 'clock', speed: 4 }, 140, 'A');
        assert.equal(entered.active, true);
        assert.equal(entered.entered, true);
    });

    it('uses cadence as a maximum dwell while tracking natural letter progress', () => {
        const mode = createLetterMode();
        let timestamp = enterLetterMode(mode);

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
    });

    it('keeps letter mode alive while low-speed movement settles', () => {
        const mode = createLetterMode();
        const enteredAt = enterLetterMode(mode);
        assert.equal(mode.push({ direction: 'clock', speed: 2 }, enteredAt + 10, 'A').active, true);
        assert.equal(mode.push({ direction: 'clock', speed: 1 }, enteredAt + 11, 'A').active, true);
        assert.equal(mode.push({ direction: 'clock', speed: 2 }, enteredAt + 12, 'A').active, true);
        assert.equal(mode.push({ direction: 'clock', speed: 1 }, enteredAt + 409, 'A').active, true);

        const exited = mode.push({ direction: 'clock', speed: 1 }, enteredAt + 410, 'A');
        assert.equal(exited.active, false);
        assert.equal(exited.exited, true);
    });

    it('confirms an immediate reversal when opposing movement becomes meaningful', () => {
        const mode = createLetterMode();
        const enteredAt = enterLetterMode(mode);
        mode.push({ direction: 'clock', speed: 2 }, enteredAt + 10, 'A');
        const bounce = mode.push({ direction: 'counter', speed: 1 }, enteredAt + 12, 'A');
        assert.equal(bounce.active, true);
        assert.equal(bounce.steps, 0);
        assert.equal(bounce.consume, true);
        mode.push({ direction: 'counter', speed: 1 }, enteredAt + 14, 'A');
        mode.push({ direction: 'counter', speed: 2 }, enteredAt + 16, 'A');

        const reversed = mode.push({ direction: 'counter', speed: 4 }, enteredAt + 108, 'A');
        assert.deepEqual(
            { active: reversed.active, steps: reversed.steps, reversed: reversed.reversed },
            { active: true, steps: 1, reversed: true },
        );

        assert.equal(mode.push({ direction: 'counter', speed: 1 }, enteredAt + 109, 'A').active, true);
        assert.equal(mode.push({ direction: 'counter', speed: 1 }, enteredAt + 110, 'A').active, true);
        assert.equal(mode.push({ direction: 'counter', speed: 1 }, enteredAt + 111, 'A').active, true);
        assert.equal(mode.push({ direction: 'counter', speed: 4 }, enteredAt + 120, 'A').active, true);
    });

    it('ignores isolated opposite-direction bounce after a stop', () => {
        const mode = createLetterMode();
        const enteredAt = enterLetterMode(mode);
        mode.push({ direction: 'clock', speed: 2 }, enteredAt + 10, 'A');
        mode.push({ direction: 'clock', speed: 1 }, enteredAt + 11, 'A');
        mode.push({ direction: 'counter', speed: 1 }, enteredAt + 20, 'A');
        mode.push({ direction: 'counter', speed: 1 }, enteredAt + 21, 'A');
        mode.push({ direction: 'counter', speed: 1 }, enteredAt + 130, 'A');
        const bounce = mode.push({ direction: 'counter', speed: 1 }, enteredAt + 300, 'A');
        assert.equal(bounce.active, true);
        assert.equal(bounce.steps, 0);
        assert.equal(bounce.consume, true);

        const exited = mode.push({ direction: 'counter', speed: 1 }, enteredAt + 410, 'A');
        assert.equal(exited.active, false);
        assert.equal(exited.exited, true);
    });

    it('treats movement after a deliberate stop as a new gesture', () => {
        const mode = createLetterMode();
        const enteredAt = enterLetterMode(mode);
        mode.push({ direction: 'clock', speed: 2 }, enteredAt + 10, 'A');
        mode.push({ direction: 'counter', speed: 1 }, enteredAt + 12, 'A');
        mode.push({ direction: 'counter', speed: 1 }, enteredAt + 100, 'A');

        const newGesture = mode.push({ direction: 'counter', speed: 4 }, enteredAt + 510, 'A');
        assert.equal(newGesture.active, false);
        assert.equal(newGesture.exited, true);
        assert.equal(newGesture.reversed, undefined);
    });

    it('preserves continuous same-direction movement at speed 1', () => {
        const mode = createLetterMode();
        let timestamp = enterLetterMode(mode);
        for (let index = 0; index < 30; index += 1) {
            timestamp += 20;
            assert.equal(mode.push({ direction: 'clock', speed: 1 }, timestamp, 'A').active, true);
        }
        assert.equal(mode.isActive(), true);
    });

    it('also confirms a sustained slow reversal before settling expires', () => {
        const mode = createLetterMode();
        let timestamp = enterLetterMode(mode);
        timestamp += 10;
        mode.push({ direction: 'clock', speed: 2 }, timestamp, 'A');

        let state;
        for (let index = 0; index < 16; index += 1) {
            timestamp += 20;
            state = mode.push({ direction: 'counter', speed: 1 }, timestamp, 'A');
        }
        assert.equal(state.active, true);
        assert.equal(state.steps, 1);
        assert.equal(state.reversed, true);
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
