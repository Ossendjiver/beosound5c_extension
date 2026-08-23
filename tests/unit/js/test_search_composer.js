const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const {
    MAX_QUERY_LENGTH,
    QWERTY_ROWS,
    buildKeySpecs,
    applyAction,
} = require('../../../web/js/search-composer.js');


describe('wheel search composer', () => {
    it('exposes voice/search editing actions followed by qwerty rows', () => {
        const keys = buildKeySpecs();
        assert.deepEqual(keys.slice(0, 5).map((key) => key.action), [
            'voice', 'submit', 'backspace', 'space', 'clear',
        ]);
        assert.deepEqual(QWERTY_ROWS, ['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM', '1234567890', "&'-."]);
        assert.equal(keys[5].title, 'Q');
        assert.equal(keys[5].rowStart, true);
    });

    it('builds and edits a query one GO action at a time', () => {
        let query = '';
        query = applyAction(query, 'character', 'B');
        query = applyAction(query, 'character', 'L');
        query = applyAction(query, 'space');
        query = applyAction(query, 'space');
        query = applyAction(query, 'character', 'U');
        assert.equal(query, 'BL U');
        assert.equal(applyAction(query, 'backspace'), 'BL ');
        assert.equal(applyAction(query, 'clear'), '');
    });

    it('enforces the query length limit', () => {
        const full = 'X'.repeat(MAX_QUERY_LENGTH);
        assert.equal(applyAction(full, 'character', 'Y'), full);
        assert.equal(applyAction(full, 'space'), full);
    });
});
