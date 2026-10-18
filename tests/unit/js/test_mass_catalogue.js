const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const catalogue=require('../../../web/js/mass-catalogue.js');
const html=fs.readFileSync(path.join(__dirname,'../../../web/softarc/mass.html'),'utf8');
test('only exact artist and album contexts expose show all',()=>{
 for(const media_type of ['artist','album'])assert.equal(catalogue.canShowAll({media_type,play_url:`library://${media_type}/1`}),true);
 for(const media_type of ['track','playlist','radio','folder'])assert.equal(catalogue.canShowAll({media_type,url:'x'}),false);
 assert.equal(catalogue.canShowAll({media_type:'artist'}),false);
 assert.equal(catalogue.isFolder({catalogue_expandable:true,tracks:[]}),true);
});
test('show all sends only read-only GET and escapes exact identity',async()=>{
 let call;const item={media_type:'artist',play_url:'tidal://artist/a?b&c'};
 const result=await catalogue.load(item,'http://localhost:8783',async(url,options)=>{call={url,options};return {ok:true,json:async()=>({state:'ready',item:{tracks:[]}})};});
 assert.equal(call.url,'http://localhost:8783/catalogue?uri=tidal%3A%2F%2Fartist%2Fa%3Fb%26c');
 assert.equal(call.options.method,undefined);assert.equal(call.options.body,undefined);assert.equal(result.state,'ready');
});
test('provider failure is visible and cannot turn into an empty success',async()=>{
 await assert.rejects(catalogue.load({media_type:'album',url:'tidal://album/1'},'',async()=>({ok:false,json:async()=>({state:'error',error:'Unavailable'})})),/Unavailable/);
});
function showAllHarness(){
 const c={window:{MassCatalogue:catalogue},AbortSignal,console};let selection={media_type:'artist',play_url:'library://artist/1'};
 let resolve;c.window.MassCatalogue={...catalogue,load:()=>new Promise(r=>{resolve=r;})};
 Object.assign(c,{getCurrentItem:()=>selection,folderStack:[],serviceBaseUrl:'http://localhost',showActionToast:()=>{},settleActionToast:()=>{},dismissOptionsMenu:()=>{},enterFolder:item=>{c.entered=item;}});
 const start=html.indexOf('            let catalogueRequestId = 0;');const end=html.indexOf('            const buildMassOptionsEntries',start);
 vm.createContext(c);vm.runInContext(html.slice(start,end)+'globalThis.showAllCatalogue=showAllCatalogue;',c);
 return {c,change:()=>{selection={};},finish:()=>resolve({state:'ready',item:{tracks:[{name:'Album'}]}})};
}
test('late show all response does not navigate after selection changes',async()=>{const h=showAllHarness();const p=h.c.showAllCatalogue({});h.change();h.finish();await p;assert.equal(h.c.entered,undefined);});
test('successful show all opens returned catalogue without any playback operation',async()=>{const h=showAllHarness();const p=h.c.showAllCatalogue({});h.finish();await p;assert.equal(h.c.entered.tracks.length,1);});
test('context menu and lazy navigation are connected',()=>{
 assert.match(html,/MassCatalogue.canShowAll\(safeItem\)/);assert.match(html,/title: 'Show all'/);
 assert.match(html,/current\?\.catalogue_expandable && !current.catalogue_loaded/);
 assert.match(html,/item\?\.youtube_video \|\| item\?\.catalogue_expandable/);
});
