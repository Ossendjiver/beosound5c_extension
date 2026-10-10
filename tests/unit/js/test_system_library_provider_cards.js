const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../../../web/softarc/system.html'),'utf8');
function card(id){
    const start=html.indexOf(`id: '${id}'`);
    const end=html.indexOf('\n            {',start);
    return vm.runInNewContext('({' + html.slice(start,end).replace(/},\s*$/,'')+'})',{
        location:{hostname:'bs5c'},row:(name,value)=>`${name}: ${value};`,fetch:async()=>({ok:true,json:async()=>({library:{status:'running'},provider:{running:true}})})});
}
test('library card renders operational counts and suppression',async()=>{
    const c=card('library');assert.equal(c.service,'beo-library');
    assert.equal((await c.fetch()).status,'running');
    assert.match(c.render({mix_sessions:2,listens:25,relationships:7,music_prompts_suppressed:true}),/Suppressed by playback/);
});
test('provider card renders progress, completion and absent cache',async()=>{
    const c=card('provider');assert.equal(c.service,'beo-provider-profile');
    assert.equal((await c.fetch()).running,true);
    assert.match(c.render({running:true,sweep:{visited:25,total:100}}),/25%/);
    assert.match(c.render({sweep:{complete:true,total:100,visited:100}}),/Complete/);
    assert.match(c.render({successful_samples:111}),/Successful Samples: 111/);
    assert.match(c.render({}),/Not profiled yet/);
});
