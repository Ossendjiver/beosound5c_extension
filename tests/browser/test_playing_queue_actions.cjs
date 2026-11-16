const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1024,height:768}});
 const actions=[];
 let tracks=[{queue_item_id:'current',name:'Current song',artist:'Artist',album:'Album',uri:'tidal://one',duration:200,index:5},
             {queue_item_id:'next',name:'Next song',artist:'Other artist',album:'Next album',uri:'tidal://two',duration:210,index:6}];
 let bound=true, readOnly=false;
 await page.route('**/*',async r=>{
  const req=r.request(),url=req.url();
  if(url.includes('/playing_queue/action')){
   const data=req.postDataJSON();actions.push(data);
   if(data.action==='remove')tracks=tracks.filter(t=>t.queue_item_id!==data.queue_item_id);
   return r.fulfill({json:{state:'removed'}});
  }
  if(url.includes('/playing_queue'))return r.fulfill({json:{bound,read_only:readOnly,queue_id:'link-queue',current_item_id:'current',current_index:0,tracks,
   capabilities:readOnly?{playNow:false,playNext:false,remove:false,showInformation:true,goto:[]}:
    {playNow:true,playNext:true,remove:true,startRadio:true,showInformation:true,goto:['artist','album','genre']}}});
  if(url.includes('/item_info'))return r.fulfill({json:{title:'Detailed song',description:'Rich track information',subtitle:'Artist • Album'}});
  return r.fulfill({contentType:'text/html',body:'<html><head></head><body><div id="now-playing"></div></body></html>'});
 });
 await page.goto('http://bs5c.test');
 await page.evaluate(()=>{
  window.getServiceUrl=()=> 'http://bs5c.test';
  window.AppConfig={showing:{exclusivePlayingFallback:true}};
  window.uiStore={currentRoute:'menu/playing',activeSource:null,mediaInfo:{title:'Current song',artist:'Artist',state:'playing'},navigateToView(route){this.currentRoute=route;}};
  window.SourcePresets={mass:{queueOverlay:{playing:true,mode:'custom'}}};
  window.IframeMessenger={sent:[],sendToRoute(...args){this.sent.push(args);}};
  window.setInterval=fn=>{window.queueRefreshTick=fn;return 1;};
 });
 await page.addScriptTag({path:path.resolve('web/js/media-manager.js')});
 await page.evaluate(()=>PlayingQueueOverlay.sync(document.querySelector('#now-playing'),{activeSource:null,mediaInfo:uiStore.mediaInfo}));
 await page.waitForFunction(()=>PlayingQueueOverlay.isEnabledFor(''));
 const wheel=direction=>page.evaluate(direction=>PlayingQueueOverlay.handleNavEvent({direction},''),direction);
 const button=b=>page.evaluate(b=>PlayingQueueOverlay.handleButton(b,''),b);
 await wheel('clockwise');
 await page.waitForSelector('.bs5c-playing-queue-item');
 await button('go');
 assert.deepEqual(await page.locator('.bs5c-playing-queue-action').allTextContents(),[
  'Play Now','Play Next','Play Radio from here','Show Information','Go to Artist/Album/Genre','Remove']);
 await page.getByRole('button',{name:'Show Information',exact:true}).click();
 await page.waitForFunction(()=>document.body.textContent.includes('Rich track information'));
 await button('right');
 await page.getByRole('button',{name:'Go to Artist/Album/Genre',exact:true}).click();
 assert.deepEqual(await page.locator('.bs5c-playing-queue-action').allTextContents(),['Go to Artist','Go to Album','Go to Genre']);
 await page.getByRole('button',{name:'Go to Artist',exact:true}).click();
 const sent=await page.evaluate(()=>IframeMessenger.sent[0]);
 assert.equal(sent[0],'menu/mass');assert.equal(sent[1],'queue-focus');assert.equal(sent[2].kind,'artist');assert.equal(sent[2].item.uri,'tidal://one');
 assert.equal(actions.length,0,'Navigation and information must never mutate playback');
 await page.evaluate(()=>uiStore.currentRoute='menu/playing');
 await wheel('clockwise');await button('go');
 await page.getByRole('button',{name:'Play Radio from here',exact:true}).click();
 await page.waitForFunction(()=>!PlayingQueueOverlay.isOpen());
 assert.equal(actions[0].action,'start_radio');assert.equal(actions[0].queue_id,'link-queue');assert.equal(actions[0].queue_item_id,'current');assert.equal(actions[0].expected_current_item_id,'current');
 assert.equal(actions[0].title,'Current song');assert.equal(actions[0].index,undefined);
 // Reordering a refreshed queue must keep the selected row, and removing it closes its stale menu.
 await wheel('clockwise');await wheel('clockwise');await button('go');
 tracks=[tracks[1],tracks[0]];
 await page.evaluate(()=>queueRefreshTick());
 await page.waitForTimeout(50);
 await page.getByRole('button',{name:'Remove',exact:true}).click();
 await page.waitForTimeout(50);
 assert.equal(actions[1].queue_item_id,'next');
 assert.equal(await page.locator('.bs5c-playing-queue-action').count(),0);
 // Old/read-only backend must not accidentally expose active controls.
 readOnly=true;
 await page.evaluate(()=>queueRefreshTick());await page.waitForTimeout(50);await button('go');
 assert.deepEqual(await page.locator('.bs5c-playing-queue-action').allTextContents(),['Show Information']);
 await button('right');await button('right');
 // Real MASS presets use custom mode; the mood host still opens the verified shared queue.
 readOnly=false;
 await page.evaluate(()=>{uiStore.currentRoute='menu/mass';PlayingQueueOverlay.openForMood();});
 await page.waitForSelector('.bs5c-mood-queue-host .bs5c-playing-queue-item');
 assert.equal(await page.evaluate(()=>PlayingQueueOverlay.moodOpen()),true);
 await page.evaluate(()=>PlayingQueueOverlay.handleButton('go','mass'));
 assert.ok((await page.locator('.bs5c-mood-queue-host .bs5c-playing-queue-action').allTextContents()).includes('Play Radio from here'));
 await browser.close();console.log('PASS: full bound queue menu, information, Artist navigation, radio identity, stable selection, read-only fallback and real custom-mode mood host.');
})().catch(e=>{console.error(e);process.exit(1)});
