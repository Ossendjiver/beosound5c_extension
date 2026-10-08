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

// Pointer movement must reach the main arc while the mood wheel is open.
{
 const calls=[];
 const start=source.indexOf('function processLaserEvent(');
 const end=source.indexOf('function updateViaStore(',start);
 const ctx={moodWheelActive:true,lastLaserEvent:{position:123},eventsProcessed:0,
  window:{uiStore:{currentRoute:'menu/mass'},LaserPositionMapper:{laserPositionToAngle:p=>p+10}},
  console,updateViaStore(angle,pos){calls.push([angle,pos]);}};
 vm.runInNewContext(source.slice(start,end)+'\nprocessLaserEvent({position:123});',ctx);
 assert.deepEqual(calls,[[133,123]]);
 assert.equal(ctx.lastKnownPointerAngle,133);
 assert.equal(ctx.lastLaserEvent,null);
 assert.equal(ctx.eventsProcessed,1);
}
console.log('Physical pointer reaches main arc with mood wheel open');
