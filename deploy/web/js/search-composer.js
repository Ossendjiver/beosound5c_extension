/** Wheel-native text composition helpers for the MASS Search panel. */
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.SearchComposer = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    const MAX_QUERY_LENGTH = 64;
    const QWERTY_ROWS = ['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM', '1234567890', "&'-."];

    const ACTION_KEYS = [
        { id: 'voice', title: 'Voice', icon: 'microphone-stage', action: 'voice', actionKey: true },
        { id: 'submit', title: 'Search', icon: 'magnifying-glass', action: 'submit', actionKey: true },
        { id: 'backspace', title: 'Delete', icon: 'backspace', action: 'backspace', actionKey: true },
        { id: 'space', title: 'Space', icon: 'text-aa', action: 'space', actionKey: true },
        { id: 'clear', title: 'Clear', icon: 'eraser', action: 'clear', actionKey: true },
    ];

    function buildKeySpecs() {
        const entries = ACTION_KEYS.map((entry) => Object.assign({}, entry));
        QWERTY_ROWS.forEach((row) => {
            Array.from(row).forEach((character, index) => {
                entries.push({
                    id: `char_${character}`,
                    title: character,
                    value: character,
                    action: 'character',
                    rowStart: index === 0,
                });
            });
        });
        return entries;
    }

    function applyAction(query, action, value = '', maxLength = MAX_QUERY_LENGTH) {
        const current = String(query || '').slice(0, maxLength);
        if (action === 'backspace') return Array.from(current).slice(0, -1).join('');
        if (action === 'clear') return '';
        if (action === 'space') {
            if (!current || /\s$/.test(current) || current.length >= maxLength) return current;
            return current + ' ';
        }
        if (action === 'character') {
            const character = String(value || '').slice(0, 1);
            return character && current.length < maxLength ? current + character : current;
        }
        return current;
    }

    return { MAX_QUERY_LENGTH, QWERTY_ROWS, buildKeySpecs, applyAction };
});
