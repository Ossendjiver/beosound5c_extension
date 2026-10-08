const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1024,height:768}});
 await page.route('**/*',r=>r.request().url().includes('/router/queue') ? r.fulfill({json:{current_index:0,tracks:[{id:'1',name:'Playing song',artist:'Artist',url:'track://1'},{id:'2',name:'Next song',url:'track://2'}]}}) : r.fulfill({contentType:'text/html',body:'<html><head></head><body></body></html>'}));
 await page.goto('http://bs5c.test');
 await page.evaluate(()=>{window.uiStore={currentRoute:'menu/mass',activeSource:'mass'};window.SourcePresets={mass:{queueOverlay:{playing:true}}};});
 await page.addScriptTag({path:path.resolve('web/js/media-manager.js')});
 await page.evaluate(()=>PlayingQueueOverlay.openForMood());
 await page.waitForTimeout(300);
 assert.equal(await page.locator('.bs5c-mood-queue-host').count(),1);
 assert.ok((await page.locator('.bs5c-mood-queue-host').textContent()).includes('Playing song'));
 assert.equal(await page.evaluate(()=>PlayingQueueOverlay.moodOpen()),true);
 await page.evaluate(()=>PlayingQueueOverlay.handleButton('right','mass'));
 assert.equal(await page.locator('.bs5c-mood-queue-host').count(),0);
 assert.equal(await page.evaluate(()=>PlayingQueueOverlay.moodOpen()),false);
 await browser.close();console.log('Mood queue overlay mounts, displays shared queue and closes back to wheel');
})().catch(e=>{console.error(e);process.exit(1)});
