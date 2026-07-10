/**
 * Tests for menu arc spacing with large menus (web/js/laser-position-mapper.js)
 *
 * With the default 5° spacing, 9+ menu items pushed the edge items onto the
 * overlay thresholds (160°/200°) — they resolved as overlay and became
 * unselectable. getMenuAngleStepFor() compresses the spacing so the full
 * span, including each edge item's ±step/2 ownership zone, stays strictly
 * inside the thresholds.
 *
 * Run with: node --test tests/unit/js/test_menu_arc.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const {
    resolveMenuSelection,
    getMenuItemAngle,
    getMenuAngleStep,
    getMenuAngleStepFor,
    updateMenuItems,
    LASER_MAPPING_CONFIG
} = require('../../../web/js/laser-position-mapper.js');


function makeItems(count) {
    return Array.from({ length: count }, (_, i) => ({
        title: `ITEM${i}`,
        path: `menu/item${i}`
    }));
}

/** Sweep every integer laser position and collect the selectable indices. */
function selectableIndices() {
    const { MIN_LASER_POS, MAX_LASER_POS } = LASER_MAPPING_CONFIG;
    const found = new Set();
    for (let pos = MIN_LASER_POS; pos <= MAX_LASER_POS; pos++) {
        const result = resolveMenuSelection(pos);
        if (result.selectedIndex >= 0) found.add(result.selectedIndex);
    }
    return found;
}


describe('getMenuAngleStepFor', () => {
    it('keeps the default 5-degree step for small menus', () => {
        assert.equal(getMenuAngleStepFor(4), 5);
        assert.equal(getMenuAngleStepFor(5), 5);
        assert.equal(getMenuAngleStepFor(7), 5);
    });

    it('compresses spacing when the menu grows', () => {
        assert.ok(getMenuAngleStepFor(9) < 5, 'expected < 5 at 9 items');
        assert.ok(getMenuAngleStepFor(10) < getMenuAngleStepFor(9));
        assert.ok(getMenuAngleStepFor(12) < getMenuAngleStepFor(10));
    });

    it('full span (step * count) stays inside the overlay thresholds', () => {
        const { TOP_OVERLAY_START, BOTTOM_OVERLAY_START } = LASER_MAPPING_CONFIG;
        const window = BOTTOM_OVERLAY_START - TOP_OVERLAY_START; // 40 degrees
        for (const count of [8, 9, 10, 12, 15]) {
            const span = getMenuAngleStepFor(count) * count;
            assert.ok(span < window, `${count} items: span ${span} >= ${window}`);
        }
    });
});


describe('large menus — every item selectable', () => {
    for (const count of [9, 10, 12]) {
        it(`all ${count} items resolve to a selectable zone (not overlay)`, () => {
            updateMenuItems(makeItems(count));
            const found = selectableIndices();
            for (let i = 0; i < count; i++) {
                assert.ok(
                    found.has(i),
                    `item ${i} of ${count} is not selectable at any laser position`
                );
            }
        });
    }

    it('edge item zones stay strictly inside the overlay thresholds', () => {
        const { TOP_OVERLAY_START, BOTTOM_OVERLAY_START } = LASER_MAPPING_CONFIG;
        for (const count of [8, 9, 10, 12]) {
            updateMenuItems(makeItems(count));
            const halfStep = getMenuAngleStep() / 2;
            const lowestEdge = getMenuItemAngle(count - 1) - halfStep;
            const highestEdge = getMenuItemAngle(0) + halfStep;
            assert.ok(
                lowestEdge > TOP_OVERLAY_START,
                `${count} items: lowest zone edge ${lowestEdge} <= ${TOP_OVERLAY_START}`
            );
            assert.ok(
                highestEdge < BOTTOM_OVERLAY_START,
                `${count} items: highest zone edge ${highestEdge} >= ${BOTTOM_OVERLAY_START}`
            );
        }
    });

    it('overlays remain reachable at both extremes with 12 items', () => {
        updateMenuItems(makeItems(12));
        assert.equal(resolveMenuSelection(3).isOverlay, true);
        assert.equal(resolveMenuSelection(123).isOverlay, true);
    });

    it('menu stays centered around 180 degrees with compressed spacing', () => {
        for (const count of [9, 12]) {
            updateMenuItems(makeItems(count));
            const first = getMenuItemAngle(0);
            const last = getMenuItemAngle(count - 1);
            assert.ok(
                Math.abs((first + last) / 2 - 180) < 0.01,
                `${count} items: first ${first} / last ${last} not centered on 180`
            );
        }
    });
});
