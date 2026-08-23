const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const {
    getLetter,
    speedToSteps,
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

    it('only enters letter mode on a deliberate fast spin', () => {
        assert.equal(speedToSteps(23), 0);
        assert.equal(speedToSteps(24), 1);
        assert.equal(speedToSteps(56), 2);
        assert.equal(speedToSteps(88), 3);
        assert.equal(speedToSteps(127), 3);
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
