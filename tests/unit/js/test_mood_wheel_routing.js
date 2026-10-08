const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../../web/js/hardware-input.js'),'utf8');
const start=source.indexOf('function handleButtonEvent(');
const end=source.indexOf('function routeButtonToView(',start);
for(const [input,expected] of [['left','left'],['right','right'],['go','go'],['go_hold','go_long']]) {
 const calls=[];
 const ctx={moodWheelActive:true,notifyUserInteraction(){},console:{log(){}},
  window:{IframeMessenger:{sendButtonEvent(page,button){calls.push([page,button]);}},
    ContextSuggestions:{handleButton(){throw Error('Mood buttons leaked to a context prompt');}},
    PlaybackTargets:{handleButton(){throw Error('Mood buttons leaked to player controls');}}},
  routeButtonToView(){throw Error('Mood buttons leaked to generic routing');},sendWebhook(){throw Error('No HA command should be sent');}};
 vm.runInNewContext(source.slice(start,end)+`\nhandleButtonEvent({currentRoute:'menu/mass'},{button:'${input}'});`,ctx);
 assert.deepEqual(calls,[['menu/mass',expected]]);
}
console.log('Open mood wheel owns physical buttons without playback/HA fallthrough');

// Captured pointer selects discovery and never leaks into the main arc.
{
 const calls=[];
 const start=source.indexOf('function processLaserEvent(');
 const end=source.indexOf('function updateViaStore(',start);
 const ctx={moodWheelActive:true,lastLaserEvent:{position:63},eventsProcessed:0,
  window:{uiStore:{currentRoute:'menu/mass'},IframeMessenger:{sendToRoute(...args){calls.push(args);}}},
  console,updateViaStore(){throw Error('Captured pointer leaked to the main arc');}};
 vm.runInNewContext(source.slice(start,end)+'\nprocessLaserEvent({position:63});',ctx);
 assert.equal(calls[0][0],'menu/mass');assert.equal(calls[0][1],'mood-laser');assert.equal(calls[0][2].position,.5);
 assert.equal(ctx.lastLaserEvent,null);
}
console.log('Pointer captured for mood discovery');
