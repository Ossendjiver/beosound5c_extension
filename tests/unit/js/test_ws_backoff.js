/**
 * Tests for web/js/ws-backoff.js
 *
 * Run with: node --test tests/unit/js/test_ws_backoff.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const {
    WS_RECONNECT_BASE_MS,
    WS_RECONNECT_MAX_MS,
    WS_RECONNECT_FACTOR,
    wsNextBackoff,
} = require('../../../web/js/ws-backoff.js');

describe('wsNextBackoff', () => {
    it('base is 3000ms', () => {
        assert.equal(WS_RECONNECT_BASE_MS, 3000);
    });

    it('cap is 60000ms', () => {
        assert.equal(WS_RECONNECT_MAX_MS, 60000);
    });

    it('grows geometrically from base', () => {
        let v = WS_RECONNECT_BASE_MS;
        v = wsNextBackoff(v); assert.equal(v, 4800);
        v = wsNextBackoff(v); assert.equal(v, 7680);
        v = wsNextBackoff(v); assert.equal(v, 12288);
    });

    it('caps at WS_RECONNECT_MAX_MS', () => {
        // Start near the cap and verify it clamps rather than overshooting.
        assert.equal(wsNextBackoff(50000), WS_RECONNECT_MAX_MS);
        assert.equal(wsNextBackoff(WS_RECONNECT_MAX_MS), WS_RECONNECT_MAX_MS);
    });

    it('monotonically non-decreasing until cap', () => {
        let v = WS_RECONNECT_BASE_MS;
        for (let i = 0; i < 20; i++) {
            const next = wsNextBackoff(v);
            assert.ok(next >= v, `step ${i}: ${next} < ${v}`);
            v = next;
        }
        assert.equal(v, WS_RECONNECT_MAX_MS);
    });

    it('reaches cap within ~8 steps from base', () => {
        // At factor 1.6 from 3000 → cap 60000, ceiling is ~log_1.6(20) ≈ 6.3.
        // Lock this at ≤8 steps so anyone retuning the factor sees the impact.
        let v = WS_RECONNECT_BASE_MS;
        let steps = 0;
        while (v < WS_RECONNECT_MAX_MS && steps < 20) {
            v = wsNextBackoff(v);
            steps++;
        }
        assert.equal(v, WS_RECONNECT_MAX_MS);
        assert.ok(steps <= 8, `took ${steps} steps to hit cap`);
    });

    it('factor matches constant', () => {
        // If someone changes WS_RECONNECT_FACTOR, the growth expectation updates
        // too — this guards against constant/formula drift.
        assert.equal(
            wsNextBackoff(WS_RECONNECT_BASE_MS),
            Math.round(WS_RECONNECT_BASE_MS * WS_RECONNECT_FACTOR),
        );
    });
});
