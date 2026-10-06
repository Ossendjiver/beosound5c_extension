/** Independent, persistent optional YouTube sources for the wheel search panel. */
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.YouTubeSearchOptions = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    const STORAGE_KEY = 'bs5c.youtube-search.v1';
    function normalize(value = {}) {
        return { youtube_music: value.youtube_music === true, youtube_videos: value.youtube_videos === true };
    }
    function load(storage, defaults = {}) {
        try {
            const raw = storage.getItem(STORAGE_KEY);
            return normalize(raw ? JSON.parse(raw) : defaults);
        } catch (_) { return normalize(defaults); }
    }
    function toggle(options, key) {
        const result = normalize(options);
        if (key === 'youtube_music' || key === 'youtube_videos') result[key] = !result[key];
        return result;
    }
    function save(storage, options) {
        try { storage.setItem(STORAGE_KEY, JSON.stringify(normalize(options))); } catch (_) {}
    }
    function query(options) {
        const value = normalize(options);
        return `&youtube_music=${value.youtube_music ? 1 : 0}&youtube_videos=${value.youtube_videos ? 1 : 0}`;
    }
    function keys(options) {
        const value = normalize(options);
        return [
            { id: 'youtube_music', title: `YT music ${value.youtube_music ? 'ON' : 'OFF'}`, icon: 'music-note', action: 'youtube_music', actionKey: true },
            { id: 'youtube_videos', title: `YT videos ${value.youtube_videos ? 'ON' : 'OFF'}`, icon: 'youtube-logo', action: 'youtube_videos', actionKey: true },
        ];
    }
    return { normalize, load, toggle, save, query, keys };
});
