const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../../../web/softarc/system.html'),'utf8');
function method(name,next){return html.slice(html.indexOf('            '+name+'('),html.indexOf('            '+next+'('));}
function panel(scan){
 const context={SUBCARDS:[{id:scan}],WebSocket:{OPEN:1},setTimeout:()=>123,clearTimeout:()=>{}};
 const c=vm.runInNewContext('({' + method('startSubcardScan','receiveScanResult').trim().replace(/}\s*$/, '},')+method('receiveScanResult','restartSubcardService').split('// ── Service restart')[0]+'})',context);
 Object.assign(c,{currentSubcard:0,scanPending:null,scanMessage:{},tidalSaving:false,renderCurrentSubcard(){},fetchSubcard(){},inputWs:{readyState:1,send(value){c.sent=JSON.parse(value);}}});return c;
}
for(const scan of ['provider','tidal-download'])test(scan+' uses fixed scan command and waits for acknowledgement',()=>{
 const c=panel(scan);c.startSubcardScan();assert.equal(c.scanPending,scan);assert.equal(c.sent.command,'start_scan');assert.equal(c.sent.params.scan,scan);
 c.sent=null;c.startSubcardScan();assert.equal(c.sent,null);
 c.receiveScanResult({scan:'other',message:'wrong'});assert.equal(c.scanPending,scan);
 c.receiveScanResult({scan,message:'Already running'});assert.equal(c.scanPending,null);assert.equal(c.scanMessage[scan],'Already running');
});
test('disconnected request stays local and never restarts services',()=>{const c=panel('provider');c.inputWs=null;c.startSubcardScan();assert.equal(c.sent,undefined);assert.match(c.scanMessage.provider,/disconnected/);});
test('send failure clears pending state',()=>{const c=panel('provider');c.inputWs.send=()=>{throw Error('closed');};c.startSubcardScan();assert.equal(c.scanPending,null);assert.match(c.scanMessage.provider,/Could not send/);});
test('scan cards use shared button; GO retains unsaved count editing',()=>{
 assert.match(html,/data-start-scan/);assert.match(html,/scanButton.onclick = \(\) => this.startSubcardScan/);
 const start=html.indexOf('            restartSubcardService()');const end=html.indexOf("if (card.id === 'ipod')",start);
 assert.match(html.slice(start,end),/this.tidalTrackEditing\) this.saveTidalCount/);
 assert.match(html.slice(start,end),/else this.startSubcardScan/);
});
