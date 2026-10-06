/**
 * Physical-wheel navigation helpers for precise menus and alphabetic jumps.
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
    const MENU_SPEED_CURVE = [
        [1, 0.52],
        [2, 0.535],
        [3, 0.555],
        [4, 0.58],
        [5, 0.61],
        [6, 0.645],
        [8, 0.72],
        [10, 0.81],
        [12, 0.875],
    ];

    const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

    function interpolateCurve(value, points) {
        const normalized = Math.max(0, Number(value || 0));
        if (!Number.isFinite(normalized) || !points.length) return 0;
        if (normalized <= points[0][0]) return points[0][1];
        for (let index = 1; index < points.length; index += 1) {
            const previous = points[index - 1];
            const current = points[index];
            if (normalized <= current[0]) {
                const progress = (normalized - previous[0]) / (current[0] - previous[0]);
                return previous[1] + (current[1] - previous[1]) * progress;
            }
        }
        return points[points.length - 1][1];
    }

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
        const threshold = Math.max(1, Number(options.threshold || 4));
        const tierSize = Math.max(1, Number(options.tierSize || 3));
        const maxSteps = Math.max(1, Number(options.maxSteps || 3));
        const normalized = Number(speed || 0);
        if (!Number.isFinite(normalized) || normalized < threshold) return 0;
        return Math.min(maxSteps, 1 + Math.floor((normalized - threshold) / tierSize));
    }

    function menuDeltaForSpeed(speed, options = {}) {
        const curve = Array.isArray(options.curve) && options.curve.length
            ? options.curve
            : MENU_SPEED_CURVE;
        return interpolateCurve(speed, curve);
    }

    function createMenuStepRamp(options = {}) {
        const idleResetMs = Math.max(40, Number(options.idleResetMs || 240));
        const maxStepsPerEvent = Math.max(1, Number(options.maxStepsPerEvent || 2));
        let residual = 0;
        let lastDirection = '';
        let lastAt = 0;

        const reset = () => {
            residual = 0;
            lastDirection = '';
            lastAt = 0;
        };

        const push = (data, at = Date.now()) => {
            const direction = String(data?.direction || '');
            if (direction !== 'clock' && direction !== 'counter') return 0;
            const timestamp = Number(at || 0);
            if ((lastAt && timestamp - lastAt > idleResetMs)
                    || (lastDirection && direction !== lastDirection)) {
                residual = 0;
            }
            lastAt = timestamp;
            lastDirection = direction;
            residual += menuDeltaForSpeed(data?.speed, options);
            const steps = Math.min(maxStepsPerEvent, Math.floor(residual + 1e-9));
            if (steps > 0) residual -= steps;
            return steps;
        };

        return { push, reset };
    }

    function letterIntervalForSpeed(speed, options = {}) {
        const slowSpeed = Math.max(1, Number(options.slowSpeed || 5));
        const slowIntervalMs = Math.max(40, Number(options.slowIntervalMs || 800));
        const fastIntervalMs = Math.max(30, Number(options.fastIntervalMs || 200));
        const fullSpeed = Math.max(slowSpeed + 1, Number(options.fullSpeed || 11));
        const progress = clamp((Number(speed || 0) - slowSpeed) / (fullSpeed - slowSpeed), 0, 1);
        return slowIntervalMs - (slowIntervalMs - fastIntervalMs) * progress;
    }

    function createLetterMode(options = {}) {
        const enterSpeed = Math.max(1, Number(options.enterSpeed || 4));
        const enterInclusive = options.enterInclusive !== false;
        const exitSpeed = Math.max(0, Number(options.exitSpeed ?? 2));
        const enterSamples = Math.max(1, Number(options.enterSamples || 3));
        const exitSamples = Math.max(1, Number(options.exitSamples || 3));
        const idleResetMs = Math.max(100, Number(options.idleResetMs || 340));
        let active = false;
        let highSpeedSamples = 0;
        let lowSpeedSamples = 0;
        let lastAt = 0;
        let lastJumpAt = 0;
        let lastDirection = '';
        let lastGroupKey = '';

        const reset = () => {
            active = false;
            highSpeedSamples = 0;
            lowSpeedSamples = 0;
            lastAt = 0;
            lastJumpAt = 0;
            lastDirection = '';
            lastGroupKey = '';
        };

        const push = (data, at = Date.now(), groupKey = '') => {
            const direction = String(data?.direction || '');
            const speed = Math.max(0, Number(data?.speed || 0));
            const timestamp = Number(at || 0);
            const normalizedGroupKey = String(groupKey || '');
            if (direction !== 'clock' && direction !== 'counter') {
                return { active, steps: 0, entered: false, exited: false };
            }
            if (!active && lastAt && timestamp - lastAt > idleResetMs) reset();
            lastAt = timestamp;

            if (!active) {
                const atEntrySpeed = enterInclusive ? speed >= enterSpeed : speed > enterSpeed;
                highSpeedSamples = atEntrySpeed ? highSpeedSamples + 1 : 0;
                lastDirection = direction;
                if (normalizedGroupKey) lastGroupKey = normalizedGroupKey;
                if (highSpeedSamples < enterSamples) {
                    return { active: false, steps: 0, entered: false, exited: false };
                }
                active = true;
                lowSpeedSamples = 0;
                lastJumpAt = timestamp;
                return { active: true, steps: 0, entered: true, exited: false };
            }

            if (normalizedGroupKey && lastGroupKey && normalizedGroupKey !== lastGroupKey) {
                lastJumpAt = timestamp;
            }
            if (normalizedGroupKey) lastGroupKey = normalizedGroupKey;

            const reversed = Boolean(lastDirection && direction !== lastDirection);

            lowSpeedSamples = speed <= exitSpeed ? lowSpeedSamples + 1 : 0;
            if (lowSpeedSamples >= exitSamples) {
                active = false;
                highSpeedSamples = 0;
                lowSpeedSamples = 0;
                lastJumpAt = 0;
                return { active: false, steps: 0, entered: false, exited: true };
            }

            if (reversed) {
                lastDirection = direction;
                lastJumpAt = timestamp;
                return { active: true, steps: 1, entered: false, exited: false, reversed: true };
            }

            const intervalMs = letterIntervalForSpeed(speed, options);
            if (lastJumpAt && timestamp - lastJumpAt < intervalMs) {
                return { active: true, steps: 0, entered: false, exited: false };
            }
            lastJumpAt = timestamp;
            return { active: true, steps: 1, entered: false, exited: false };
        };

        return { push, reset, isActive: () => active };
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

    return {
        getLetter,
        speedToSteps,
        menuDeltaForSpeed,
        createMenuStepRamp,
        letterIntervalForSpeed,
        createLetterMode,
        findJumpIndex,
    };
});
