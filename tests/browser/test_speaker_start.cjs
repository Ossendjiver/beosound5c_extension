const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.BS5C_TEST_CHROMIUM });
    try {
        const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
        const options = [
            { id: 'dining-bl4000', name: 'Dining BL4000' },
            { id: 'dining-penta', name: 'Dining Penta' },
            { id: 'lounge-window', name: 'Lounge Window' },
            { id: 'lounge-cinema', name: 'Lounge Cinema' },
        ];
        const sent = [];
        let available = true;
        await page.route('http://localhost/**', async (route) => {
            if (route.request().url().endsWith('/router/speaker-start')) {
                if (route.request().method() === 'POST') {
                    sent.push(route.request().postDataJSON());
                    return route.fulfill({ json: { state: 'playing' } });
                }
                return route.fulfill({ json: { options: available ? options : [] } });
            }
            if (route.request().url().endsWith('/router/playback')) return route.fulfill({ json: {} });
            return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body></body></html>' });
        });
        await page.goto('http://localhost/');
        await page.evaluate(() => { window.AppConfig = { routerUrl: 'http://localhost' }; });
        await page.addScriptTag({ path: path.join(__dirname, '../../web/js/playback-targets.js') });
        const playback = { command: 'play_item', url: 'library://track/123' };
        await page.evaluate((command) => { window.choice = PlaybackTargets.chooseSpeakerForStart(command); }, playback);
        const picker = page.locator('#playback-target-queue-prompt');
        await picker.waitFor();
        assert.deepEqual(await picker.locator('.speaker-option').allTextContents(), options.map(option => option.name));
        for (let step = 0; step < 3; step++) {
            await page.evaluate(() => PlaybackTargets.handleSpeakerNav({ direction: 'clock' }));
        }
        assert.equal(await picker.locator('.selected').textContent(), 'Lounge Cinema');
        assert.equal(sent.length, 0);
        await page.evaluate(() => PlaybackTargets.handleSpeakerButton('go'));
        assert.equal((await page.evaluate(() => window.choice)).state, 'playing');
        assert.deepEqual(sent, [{ target: 'lounge-cinema', playback }]);
        await picker.waitFor({ state: 'hidden' });

        await page.evaluate(() => { window.choice = PlaybackTargets.chooseSpeakerForStart(); });
        await picker.waitFor();
        await page.keyboard.press('Escape');
        assert.equal(await page.evaluate(() => window.choice), null);
        assert.equal(sent.length, 1);

        available = false;
        assert.equal(await page.evaluate(() => PlaybackTargets.chooseSpeakerForStart()), undefined);
        assert.equal(await picker.isVisible(), false);
        console.log('Speaker picker passed physical navigation, GO confirmation, cancel and active-output bypass');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exit(1); });
