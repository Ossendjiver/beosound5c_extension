const assert = require('node:assert/strict');
const {normalize,fromPoint,ring,discovery,radiusForDiscovery,Wheel,mood} = require('../../../web/js/mood-wheel.js');
assert.equal(fromPoint(0,-1).angle,0);assert.equal(fromPoint(1,0).angle,90);
assert.equal(fromPoint(0,1).angle,180);assert.equal(fromPoint(-1,0).angle,270);
assert.equal(fromPoint(2,0).radius,1);assert.deepEqual(normalize(-5,-1),{angle:355,radius:0});
assert.equal(discovery(.25),0);assert.equal(discovery(.250001),10);assert.equal(discovery(1),90);
for(let n=10;n<=90;n++)assert.equal(discovery(radiusForDiscovery(n)),n);
assert.equal(ring(.25),'Familiar');assert.equal(ring(.5),'Discover');assert.equal(mood(270),'Relaxed');
global.localStorage={setItem(){}};
let queue=false,closed=false;const wheel=new Wheel({onQueue(){queue=true},onPlay(){}});
wheel.render=()=>{};wheel.close=()=>{closed=true};wheel.state=normalize(90,.125);
wheel.laser(.7);assert.equal(wheel.state.radius,.7);assert.equal(wheel.state.angle,90);
wheel.changed=false;wheel.laser(.7);assert.equal(wheel.changed,false); // no debounce starvation from repeated samples
wheel.button('left');assert.equal(queue,true);wheel.button('right');assert.equal(closed,true);
console.log('Mood geometry, continuous discovery, captured pointer and queue/back controls passed');
