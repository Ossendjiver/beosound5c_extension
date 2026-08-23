/**
 * Alphabetic jump helpers for high-speed physical wheel navigation.
 *
 * Ordinary wheel movement remains item-by-item.  A deliberate fast spin can
 * use these helpers to move between the first entries of adjacent letter
 * groups without making low-speed navigation twitchy.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.FastLetterIndex = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    const DEFAULT_ARTICLES = new Set(['A', 'AN', 'THE']);

    function getLetter(value, articles = DEFAULT_ARTICLES) {
        const base = String(value || '')
            .split('\n')[0]
            .replace(/^[^A-Za-z0-9]+/, '')
            .trim();
        if (!base) return '';

        const tokens = base.split(/\s+/).filter(Boolean);
        const token = tokens.length > 1 && articles.has(tokens[0].toUpperCase())
            ? tokens[1]
            : tokens[0];
        const match = token.match(/[A-Za-z0-9]/);
        if (!match) return '';
        return /\d/.test(match[0]) ? '#' : match[0].toUpperCase();
    }

    function speedToSteps(speed, options = {}) {
        const threshold = Math.max(1, Number(options.threshold || 24));
        const tierSize = Math.max(1, Number(options.tierSize || 32));
        const maxSteps = Math.max(1, Number(options.maxSteps || 3));
        const normalized = Number(speed || 0);
        if (!Number.isFinite(normalized) || normalized < threshold) return 0;
        return Math.min(maxSteps, 1 + Math.floor((normalized - threshold) / tierSize));
    }

    function findJumpIndex(items, currentIndex, direction, letterSteps = 1, getName = (item) => item?.name) {
        if (!Array.isArray(items) || items.length === 0) return -1;

        const lastIndex = items.length - 1;
        let position = Math.max(0, Math.min(lastIndex, Math.round(Number(currentIndex || 0))));
        let remaining = Math.max(1, Math.floor(Number(letterSteps || 1)));
        let currentLetter = getLetter(getName(items[position]));

        if (direction === 'clock') {
            let cursor = position + 1;
            while (remaining > 0) {
                while (cursor <= lastIndex && getLetter(getName(items[cursor])) === currentLetter) cursor += 1;
                if (cursor > lastIndex) return lastIndex;
                position = cursor;
                currentLetter = getLetter(getName(items[position]));
                remaining -= 1;
                cursor = position + 1;
            }
            return position;
        }

        if (direction === 'counter') {
            let cursor = position - 1;
            while (remaining > 0) {
                while (cursor >= 0 && getLetter(getName(items[cursor])) === currentLetter) cursor -= 1;
                if (cursor < 0) return 0;

                const previousLetter = getLetter(getName(items[cursor]));
                while (cursor > 0 && getLetter(getName(items[cursor - 1])) === previousLetter) cursor -= 1;
                position = cursor;
                currentLetter = previousLetter;
                remaining -= 1;
                cursor = position - 1;
            }
            return position;
        }

        return position;
    }

    return { getLetter, speedToSteps, findJumpIndex };
});
