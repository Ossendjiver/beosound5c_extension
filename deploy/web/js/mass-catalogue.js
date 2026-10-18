/* Read-only on-demand provider browsing. Never sends playback or library writes. */
(function (root) {
    'use strict';
    const uri = item => String(item?.play_url || item?.url || item?.uri || '').trim();
    const canShowAll = item => ['artist', 'album'].includes(String(item?.media_type || '')) && !!uri(item);
    const isFolder = item => !!item?.catalogue_expandable || (Array.isArray(item?.tracks) && item.tracks.length > 0);
    async function load(item, base, fetcher = root.fetch.bind(root)) {
        if (!canShowAll(item)) throw Error('Select an artist or album');
        const response = await fetcher(`${base}/catalogue?uri=${encodeURIComponent(uri(item))}`, {
            cache: 'no-store', signal: AbortSignal.timeout(65000),
        });
        const result = await response.json();
        if (!response.ok || result.state !== 'ready' || !Array.isArray(result.item?.tracks))
            throw Error(result.error || 'Provider catalogue unavailable');
        return result;
    }
    const api = {uri, canShowAll, isFolder, load};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.MassCatalogue = api;
})(typeof window !== 'undefined' ? window : globalThis);
