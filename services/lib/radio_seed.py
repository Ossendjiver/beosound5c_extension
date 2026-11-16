"""Bounded on-demand descriptors for radio seeds outside the cached library."""
import asyncio
from . import music_features, radio_genres


def usable(item):
    return bool(radio_genres.families(radio_genres.labels(item)) or
                music_features.radio_audio_assessment(item, item)['status'] == 'compatible')


async def enrich(seed, cache, fetch, now):
    uri = str(seed.get('uri') or '')
    stored = cache.get(uri, {})
    combined = music_features.merge(stored, seed)
    if not uri or usable(combined):
        return combined, False, 'ready' if uri else 'missing_uri'
    if float(stored.get('_radio_retry_after') or 0) > now:
        return combined, False, 'waiting_for_metadata_retry'
    entry = dict(stored)
    status = 'provider_metadata_unavailable'
    try:
        detail = await asyncio.wait_for(fetch(uri), timeout=5)
        if not isinstance(detail, dict):
            raise ValueError('Invalid provider item')
        provider, identifier = uri.split('://', 1)
        identifier = identifier.rsplit('/', 1)[-1]
        if detail.get('uri') != uri and not any(
                str(m.get('item_id')) == identifier and m.get('provider_instance') == provider
                for m in detail.get('provider_mappings') or [] if isinstance(m, dict)):
            raise ValueError('Provider returned a different recording')
        entry = music_features.merge(entry, music_features.metadata(detail))
        combined = music_features.merge(entry, seed)
        status = 'ready' if usable(combined) else 'provider_metadata_insufficient'
    except asyncio.CancelledError:
        raise
    except Exception:
        pass  # No provider failure is grounds for unrelated popularity fallback.
    entry['_radio_checked_at'] = now
    entry['_radio_retry_after'] = now + (86400 if status == 'ready' else 900)
    cache[uri] = entry
    return combined, True, status
