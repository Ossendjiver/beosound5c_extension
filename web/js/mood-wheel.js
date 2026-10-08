/* MoodWheel: clockwise degrees from north, radius 0..1. */
(function(root) {
    const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, Number(n)));
    const normalize = (angle, radius) => ({angle: ((Number(angle) % 360) + 360) % 360, radius: clamp(radius, 0, 1)});
    const fromPoint = (x, y) => normalize(Math.atan2(x, -y) * 180 / Math.PI, Math.hypot(x, y));
    const ring = radius => radius <= .25 ? 'Familiar' : 'Discover';
    const mood = angle => ['Bright', 'Bright / energetic', 'Energetic', 'Energetic / contemplative',
        'Contemplative', 'Contemplative / relaxed', 'Relaxed', 'Relaxed / bright'][Math.round(normalize(angle, 0).angle / 45) % 8];
    const discovery = radius => radius <= .25 ? 0 : Math.min(90, 10 + Math.round((clamp(radius,0,1)-.25)/.75*80));
    const radiusForDiscovery = value => value <= 0 ? .125 : Math.min(1, .25 + (clamp(value,10,90)-10)/80*.75 + .000001);
    const layerMove = (state,direction) => normalize(state.angle, radiusForDiscovery(
        Math.max(0, Math.min(90, discovery(state.radius) + (direction === 'right' ? 1 : -1)))));
    class Wheel {
        constructor({onPlay, onClose, onSuggest, onAdjust, onQueue, onState}) { this.onAdjust=onAdjust; this.onQueue=onQueue; this.onState=onState; this.onSuggest=onSuggest; this.onPlay = onPlay; this.onClose = onClose; this.state = normalize(0, .25); }
        open() {
            if (this.el) return;
            try { this.state = normalize(...JSON.parse(localStorage.getItem('bs5c-mood') || '[0,0.25]')); } catch (_) {}
            this.el = document.createElement('section'); this.el.className = 'mood-view';
            this.el.innerHTML = `<button class="mood-back" data-close aria-label="Back" title="Back">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 12H5m6-6-6 6 6 6"/></svg></button>
                <div class="mood-stage"><div class="mood-disc" role="group" aria-label="Mood wheel">
                    <i class="mood-point"></i></div></div>
                <div class="mood-playing"></div><div class="mood-label" aria-live="polite"><span class="mood-atmosphere"></span><span class="mood-discovery"></span></div>
                <div class="mood-status" aria-live="polite"></div><button class="mood-play" aria-label="Play this mood" title="Play this mood">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 4 12 8-12 8Z"/></svg></button>`;
            // Use the same body background as the rest of this interface.
            this.el.style.backgroundColor = getComputedStyle(document.body).backgroundColor;
            document.body.appendChild(this.el);
            this.el.querySelector('[data-close]').onclick = () => this.close();
            this.el.querySelector('.mood-play').onclick = () => this.play();
            const disc = this.el.querySelector('.mood-disc');
            const point = e => { const rect = disc.getBoundingClientRect(); const rad = rect.width/2;
                this.changed=true; localStorage.setItem('bs5c-mood-user','1'); this.state = fromPoint((e.clientX-rect.left-rad)/rad, (e.clientY-rect.top-rad)/rad); this.render(); };
            disc.onpointerdown = e => { disc.setPointerCapture(e.pointerId); point(e); };
            disc.onpointermove = e => { if (disc.hasPointerCapture(e.pointerId)) point(e); };
            this.render(); this.notify(true);
            this.poll = setInterval(async () => {
                // Reassert ownership after mounting or a missed open message.
                if (this.el) this.notify(true);
                try { const state = await this.onState?.(); if (this.el) this.el.querySelector(".mood-playing").textContent = state?.title || ""; } catch (_) {}
            }, 1500);
            this.changed = false;
            if (!localStorage.getItem('bs5c-mood-user') && this.onSuggest) {
                Promise.resolve(this.onSuggest()).then(mood => {
                    if (mood && this.el && !this.changed) { this.state=normalize(mood.angle,mood.radius); this.render(); }
                }).catch(()=>{});
            }
        }
        notify(active) { if (window.parent !== window) window.parent.postMessage({type:'bs5c-mood-active', active}, '*'); }
        render() {
            if (!this.el) return;
            const t = this.state.angle * Math.PI/180, p = this.el.querySelector('.mood-point');
            p.style.left = `${50+48*this.state.radius*Math.sin(t)}%`; p.style.top = `${50-48*this.state.radius*Math.cos(t)}%`;
            this.el.classList.remove('settled');
            clearTimeout(this.dwellTimer);
            const view = this.el;
            this.dwellTimer = setTimeout(() => {
                if (this.el !== view) return;
                view.querySelector('.mood-atmosphere').textContent = mood(this.state.angle);
                view.querySelector('.mood-discovery').textContent = `${ring(this.state.radius)} · ${discovery(this.state.radius)}% discovery`;
                view.classList.add('settled');
                if (this.changed && this.onAdjust) Promise.resolve(this.onAdjust({...this.state})).catch(e => { if (this.el) this.el.querySelector('.mood-status').textContent=e.message; });
            }, 1000);
            localStorage.setItem('bs5c-mood', JSON.stringify([this.state.angle,this.state.radius]));
        }
        nav(data) { this.changed=true; localStorage.setItem('bs5c-mood-user','1'); const sign = data?.direction === 'clock' ? 1 : data?.direction === 'counter' ? -1 : 0;
            this.state = normalize(this.state.angle + sign * 5 * clamp(data?.speed || 1,1,4), this.state.radius); this.render(); }
        laser(level) {
            const radius=clamp(level,0,1);
            if (discovery(radius) === discovery(this.state.radius)) return;
            this.changed=true; localStorage.setItem('bs5c-mood-user','1');
            this.state = normalize(this.state.angle, radius); this.render();
        }
        button(button) {
            if (button === 'go') return this.play();
            if (button === 'go_long' || button === 'go_hold' || button === 'back') return this.close();
            if (button === 'left') return this.onQueue?.();
            if (button === 'right') return this.close();
        }
        key(key) {
            if (['Escape','Backspace'].includes(key)) return this.close();
            if (key === 'Enter') return this.play();
            if (key === 'ArrowUp') this.nav({direction:'counter'});
            if (key === 'ArrowDown') this.nav({direction:'clock'});
            if (key === 'ArrowLeft') return this.button('left');
            if (key === 'ArrowRight') return this.button('right');
        }
        async play() {
            if (this.busy || !this.el) return; this.busy = true;
            const status = this.el.querySelector('.mood-status'), button = this.el.querySelector('.mood-play'); button.disabled = true;
            status.textContent = 'Building your mix…';
            try { status.textContent = await this.onPlay({...this.state}) || 'Playing'; }
            catch(e) { status.textContent = e.message || 'Could not start this mood'; }
            finally { this.busy=false; if (this.el) button.disabled=false; }
        }
        close() { clearInterval(this.poll); clearTimeout(this.dwellTimer); this.el?.remove(); this.el = null; this.notify(false); this.onClose?.(); }
        get active() { return !!this.el; }
    }
    const api = {normalize, fromPoint, ring, mood, discovery, radiusForDiscovery, layerMove, Wheel}; root.MoodWheel = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
