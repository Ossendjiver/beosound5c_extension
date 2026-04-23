/**
 * Shared playback target state.
 *
 * Router owns the active audio/video target selections. The UI keeps a small
 * local mirror so source controllers can include the selected target with
 * transport and transfer commands.
 */
(function () {
    const ROUTER_URL = () => window.AppConfig?.routerUrl || 'http://localhost:8770';
    const YOUTUBE_PREF_KEY = 'bs5c.youtubeVideosEnabled';

    const defaults = {
        audio_targets: [
            { id: '08a2eca2-247c-96fe-7998-7baddf01b2b1', name: 'Cuisine' },
            { id: '64ad9554-d5e6-116c-8b0b-069c1f0b7885', name: 'Bedroom Mini' },
            { id: 'up50411c87e1c0', name: 'Link' },
        ],
        video_targets: [],
        audio_target_id: 'up50411c87e1c0',
        video_target_id: '',
        music_video_enabled: true,
    };

    let state = { ...defaults };

    function normalizeTargets(targets) {
        if (!Array.isArray(targets)) return [];
        return targets
            .map((target) => ({
                id: String(target?.id || target?.player_id || target?.value || '').trim(),
                name: String(target?.name || target?.label || target?.title || target?.id || '').trim(),
            }))
            .filter((target) => target.id)
            .map((target) => ({ ...target, name: target.name || target.id }));
    }

    function readMusicVideoLocal() {
        try {
            return localStorage.getItem(YOUTUBE_PREF_KEY) !== 'false';
        } catch (error) {
            return true;
        }
    }

    function writeMusicVideoLocal(enabled) {
        const normalized = enabled !== false;
        try {
            localStorage.setItem(YOUTUBE_PREF_KEY, normalized ? 'true' : 'false');
        } catch (error) {}
        if (window.MusicVideoPreference?.setEnabled) {
            window.MusicVideoPreference.setEnabled(normalized);
        } else {
            document.dispatchEvent(new CustomEvent('bs5c:music-video-preference', {
                detail: { enabled: normalized },
            }));
        }
    }

    function applyState(next) {
        if (!next || typeof next !== 'object') return state;
        const audioTargets = normalizeTargets(next.audio_targets);
        const videoTargets = normalizeTargets(next.video_targets);
        state = {
            audio_targets: audioTargets.length ? audioTargets : state.audio_targets || defaults.audio_targets,
            video_targets: videoTargets,
            audio_target_id: String(next.audio_target_id || state.audio_target_id || '').trim(),
            video_target_id: String(next.video_target_id || state.video_target_id || '').trim(),
            music_video_enabled: next.music_video_enabled == null
                ? state.music_video_enabled
                : next.music_video_enabled !== false,
        };
        if (!state.audio_target_id && state.audio_targets.length) {
            state.audio_target_id = state.audio_targets[0].id;
        }
        if (!state.video_target_id && state.video_targets.length) {
            state.video_target_id = state.video_targets[0].id;
        }
        writeMusicVideoLocal(state.music_video_enabled);
        document.dispatchEvent(new CustomEvent('bs5c:playback-targets', { detail: { state } }));
        return state;
    }

    async function refresh() {
        try {
            const response = await fetch(`${ROUTER_URL()}/router/playback`, { cache: 'no-store' });
            if (!response.ok) return state;
            return applyState(await response.json());
        } catch (error) {
            state.music_video_enabled = readMusicVideoLocal();
            return state;
        }
    }

    async function update(patch) {
        try {
            const response = await fetch(`${ROUTER_URL()}/router/playback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch || {}),
            });
            if (!response.ok) return state;
            return applyState(await response.json());
        } catch (error) {
            if ('music_video_enabled' in (patch || {})) {
                state.music_video_enabled = patch.music_video_enabled !== false;
                writeMusicVideoLocal(state.music_video_enabled);
            }
            return state;
        }
    }

    function targetsFor(sourceId) {
        return sourceId === 'kodi' ? state.video_targets : state.audio_targets;
    }

    function selectedTargetIdFor(sourceId) {
        return sourceId === 'kodi' ? state.video_target_id : state.audio_target_id;
    }

    function selectedTargetFor(sourceId) {
        const selected = selectedTargetIdFor(sourceId);
        return targetsFor(sourceId).find((target) => target.id === selected) || targetsFor(sourceId)[0] || null;
    }

    function payloadFor(sourceId) {
        const target = selectedTargetFor(sourceId);
        return {
            playback: { ...state },
            audio_target_id: state.audio_target_id,
            video_target_id: state.video_target_id,
            target_player_id: target?.id || '',
        };
    }

    document.addEventListener('bs5c:music-video-preference', (event) => {
        const enabled = event.detail?.enabled !== false;
        if (state.music_video_enabled !== enabled) {
            state.music_video_enabled = enabled;
            void update({ music_video_enabled: enabled });
        }
    });

    window.PlaybackTargets = {
        get state() { return state; },
        applyState,
        refresh,
        targetsFor,
        selectedTargetIdFor,
        selectedTargetFor,
        payloadFor,
        setAudioTarget: (id) => update({ audio_target_id: id }),
        setVideoTarget: (id) => update({ video_target_id: id }),
        setMusicVideoEnabled: (enabled) => update({ music_video_enabled: enabled !== false }),
    };

    setTimeout(() => { void refresh(); }, 500);
})();
