/**
 * Shared playback target state.
 *
 * Router owns the active audio/video target selections. The UI keeps a small
 * local mirror so source controllers can include the selected target with
 * transport and transfer commands.
 */
(function () {
    const ROUTER_URL = () => window.AppConfig?.routerUrl || 'http://localhost:8770';
    const MASS_URL = () => window.AppConfig?.massServiceUrl || 'http://localhost:8783';
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
    let targetPrompt = null;

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

    function ensurePromptElement() {
        let overlay = document.getElementById('playback-target-queue-prompt');
        if (overlay) return overlay;
        const style = document.createElement('style');
        style.textContent = `
            #playback-target-queue-prompt {
                position: fixed; inset: 0; z-index: 2147483000;
                display: grid; place-items: center;
                background: rgba(0, 0, 0, .48);
                font-family: inherit; color: #f4f7fb;
            }
            #playback-target-queue-prompt[hidden] { display: none; }
            #playback-target-queue-prompt .target-queue-card {
                width: min(540px, calc(100vw - 64px));
                box-sizing: border-box; padding: 28px 30px 24px;
                border: 1px solid rgba(145, 195, 255, .62);
                border-radius: 22px;
                background: rgba(7, 13, 24, .94);
                box-shadow: 0 22px 70px rgba(0, 0, 0, .58);
            }
            #playback-target-queue-prompt .target-queue-kicker {
                color: #8dc7ff; font-size: 13px; font-weight: 700;
                letter-spacing: .14em; text-transform: uppercase;
            }
            #playback-target-queue-prompt .target-queue-title {
                margin: 7px 0 4px; font-size: 28px; font-weight: 650;
            }
            #playback-target-queue-prompt .target-queue-copy {
                margin: 0 0 18px; color: rgba(244, 247, 251, .72); font-size: 15px;
            }
            #playback-target-queue-prompt .target-queue-option {
                padding: 13px 16px; margin-top: 8px; border-radius: 12px;
                border: 1px solid rgba(255,255,255,.12); font-size: 18px;
            }
            #playback-target-queue-prompt .target-queue-option.selected {
                color: #06111f; background: #9bd0ff; border-color: #cce7ff;
            }
            #playback-target-queue-prompt .target-queue-status {
                min-height: 20px; margin-top: 12px; color: #ffb5b5; font-size: 14px;
            }
            #playback-target-queue-prompt .target-queue-hint {
                margin-top: 6px; color: rgba(244, 247, 251, .5); font-size: 12px;
                letter-spacing: .08em; text-transform: uppercase;
            }
        `;
        document.head.appendChild(style);
        overlay = document.createElement('div');
        overlay.id = 'playback-target-queue-prompt';
        overlay.hidden = true;
        overlay.innerHTML = `
            <div class="target-queue-card">
                <div class="target-queue-kicker">Playback target</div>
                <div class="target-queue-title"></div>
                <div class="target-queue-copy"></div>
                <div class="target-queue-options"></div>
                <div class="target-queue-status"></div>
                <div class="target-queue-hint">Wheel Select &nbsp; GO Confirm &nbsp; RIGHT Cancel</div>
            </div>
        `;
        overlay.addEventListener('click', (event) => {
            const option = event.target.closest('[data-target-queue-action]');
            if (!option || !targetPrompt?.resolve || targetPrompt.busy) return;
            targetPrompt.selectedIndex = option.dataset.targetQueueAction === 'clear' ? 1 : 0;
            renderTargetPrompt();
            void activateTargetPrompt();
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    function renderTargetPrompt() {
        const overlay = ensurePromptElement();
        overlay.hidden = !targetPrompt;
        if (!targetPrompt) return;
        overlay.querySelector('.target-queue-title').textContent = `Move playback to ${targetPrompt.targetName}?`;
        overlay.querySelector('.target-queue-copy').textContent =
            `${targetPrompt.queueName} has an active queue (${targetPrompt.itemCount} items).`;
        const options = [
            { action: 'transfer', label: 'transfer queue here?' },
            { action: 'clear', label: 'clear current queue' },
        ];
        overlay.querySelector('.target-queue-options').innerHTML = options.map((option, index) => `
            <div class="target-queue-option${index === targetPrompt.selectedIndex ? ' selected' : ''}"
                 data-target-queue-action="${option.action}">${option.label}</div>
        `).join('');
        overlay.querySelector('.target-queue-status').textContent =
            targetPrompt.busy ? 'Updating the active queue…' : (targetPrompt.error || '');
    }

    function closeTargetPrompt(result = state) {
        const prompt = targetPrompt;
        targetPrompt = null;
        const overlay = document.getElementById('playback-target-queue-prompt');
        if (overlay) overlay.hidden = true;
        prompt?.resolve?.(result);
    }

    async function fetchActiveMassQueue() {
        try {
            const response = await fetch(`${MASS_URL()}/queue`, { cache: 'no-store' });
            if (!response.ok) return null;
            const payload = await response.json();
            return payload && typeof payload === 'object' ? payload : null;
        } catch (error) {
            return null;
        }
    }

    async function activateTargetPrompt() {
        if (!targetPrompt || targetPrompt.busy) return true;
        const prompt = targetPrompt;
        const action = prompt.selectedIndex === 1 ? 'clear' : 'transfer';
        prompt.busy = true;
        prompt.error = '';
        renderTargetPrompt();
        try {
            const commandPayload = action === 'transfer'
                ? {
                    command: 'transfer_queue',
                    source_queue_id: prompt.queueId,
                    target_player_id: prompt.targetId,
                    target_queue_id: prompt.targetId,
                    auto_play: ['playing', 'buffering'].includes(prompt.queueState),
                }
                : {
                    command: 'clear_queue',
                    source_queue_id: prompt.queueId,
                    queue_id: prompt.queueId,
                };
            const response = await fetch(`${MASS_URL()}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(commandPayload),
            });
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload || payload.state === 'error' || payload.status === 'error') {
                throw new Error(payload?.reason || 'queue_update_failed');
            }
            const nextState = await update({
                audio_target_id: prompt.targetId,
                queue_resolution: action,
            });
            closeTargetPrompt(nextState);
        } catch (error) {
            if (targetPrompt !== prompt) return true;
            prompt.busy = false;
            prompt.error = action === 'transfer'
                ? 'Unable to transfer the active queue.'
                : 'Unable to clear the active queue.';
            renderTargetPrompt();
        }
        return true;
    }

    async function requestAudioTargetChange(id, options = {}) {
        const targetId = String(id || '').trim();
        if (!targetId) return state;
        if (options.skipQueuePrompt === true) {
            return update({ audio_target_id: targetId, queue_resolution: options.queueResolution || 'synced' });
        }
        if (targetPrompt) return state;
        const queue = await fetchActiveMassQueue();
        const queueId = String(queue?.player_id || queue?.queue_id || '').trim();
        const tracks = Array.isArray(queue?.tracks) ? queue.tracks : [];
        if (!queueId || queueId === targetId || !tracks.length) {
            return update({ audio_target_id: targetId });
        }
        const target = state.audio_targets.find((item) => item.id === targetId);
        return new Promise((resolve) => {
            targetPrompt = {
                resolve,
                targetId,
                targetName: target?.name || targetId,
                queueId,
                queueName: String(queue?.queue_name || queueId).trim(),
                queueState: String(queue?.state || '').trim().toLowerCase(),
                itemCount: tracks.length,
                selectedIndex: 0,
                busy: false,
                error: '',
            };
            renderTargetPrompt();
        });
    }

    function handlePromptNav(data) {
        if (!targetPrompt || targetPrompt.busy) return Boolean(targetPrompt);
        targetPrompt.selectedIndex = String(data?.direction || '').toLowerCase() === 'counter' ? 0 : 1;
        renderTargetPrompt();
        return true;
    }

    function handlePromptButton(button) {
        if (!targetPrompt) return false;
        const normalized = String(button || '').toLowerCase();
        if (normalized === 'right') {
            closeTargetPrompt(state);
            return true;
        }
        if (normalized === 'go') {
            void activateTargetPrompt();
        }
        return true;
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
        requestAudioTargetChange,
        setAudioTarget: requestAudioTargetChange,
        isQueuePromptOpen: () => Boolean(targetPrompt),
        handleNav: handlePromptNav,
        handleButton: handlePromptButton,
        setVideoTarget: (id) => update({ video_target_id: id }),
        setMusicVideoEnabled: (enabled) => update({ music_video_enabled: enabled !== false }),
    };

    setTimeout(() => { void refresh(); }, 500);
})();
