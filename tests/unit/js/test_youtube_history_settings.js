// Guard that the Samsung history toggle is part of the real BS5c
// Settings card and that Save & Restart persists its boolean value.
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "../../..");
const source = fs.readFileSync(path.join(root, "web/softarc/config.html"), "utf8");
const defaults = JSON.parse(fs.readFileSync(path.join(root, "config/default.json"), "utf8"));
const schema = JSON.parse(fs.readFileSync(path.join(root, "docs/config.schema.json"), "utf8"));

test("Samsung watch history appears as an existing-style Settings card", () => {
    assert.match(source, /renderHA\(cfg\)\s*\+\s*renderYouTubeHistory\(cfg\)/);
    assert.match(source, /function renderYouTubeHistory\(cfg\)/);
    assert.match(source, /<div class="cfg-card" id="cfg-youtube-history">/);
    assert.match(source, /<h2>YouTube history<\/h2>/);
    assert.match(source, /id="cfg-youtube-history-samsung" type="checkbox"/);
    assert.match(source, /person\.thomas/);
});

test("Samsung toggle uses the normal Save & Restart configuration flow", () => {
    assert.match(source, /cfg\.youtube_history\.include_samsung\s*=\s*\n\s*document\.getElementById\('cfg-youtube-history-samsung'\)\?\.checked === true/);
    assert.match(source, /body: JSON\.stringify\(cfg\)/);
    assert.equal(defaults.youtube_history.include_samsung, false);
    assert.equal(schema.properties.youtube_history.properties.include_samsung.type, "boolean");
    assert.equal(schema.properties.youtube_history.properties.include_samsung.default, false);
});
