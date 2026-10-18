const test = require('node:test');
const assert = require('node:assert/strict');
const options = require('../../../web/js/youtube-search-options.js');
test('independent switches survive refresh and both-off never asks for YouTube', () => {
    let stored = null;
    const storage = { getItem: () => stored, setItem: (_, value) => { stored = value; } };
    const first = options.load(storage);
    assert.equal(options.query(first), '&youtube_music=0&youtube_videos=0');
    const music = options.toggle(first, 'youtube_music');
    assert.deepEqual(music, { youtube_music: true, youtube_videos: false });
    options.save(storage, music);
    assert.deepEqual(options.load(storage), music);
    const both = options.toggle(music, 'youtube_videos');
    assert.deepEqual(both, { youtube_music: true, youtube_videos: true });
    assert.equal(options.query(both), '&youtube_music=1&youtube_videos=1');
    assert.equal(options.keys(both)[1].title, 'YT videos ON');
});
test('defaults, malformed persistence and disabled storage are safe', () => {
    assert.deepEqual(options.load({getItem:()=>null}, {youtube_music:true}), {youtube_music:true,youtube_videos:false});
    assert.deepEqual(options.load({getItem:()=>'{bad'}, {youtube_videos:true}), {youtube_music:false,youtube_videos:true});
    assert.deepEqual(options.toggle({youtube_music:true}, 'other'), {youtube_music:true,youtube_videos:false});
    options.save({setItem:()=>{throw Error('denied');}}, {});
});
