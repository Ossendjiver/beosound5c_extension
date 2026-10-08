/* MoodWheel: clockwise degrees from north, radius 0..1. */
(function(root) {
    const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, Number(n)));
    const normalize = (angle, radius) => ({angle: ((Number(angle) % 360) + 360) % 360, radius: clamp(radius, 0, 1)});
    const fromPoint = (x, y) => normalize(Math.atan2(x, -y) * 180 / Math.PI, Math.hypot(x, y));
    const ring = radius => radius <= 1/3 ? 'Familiar' : radius <= 2/3 ? 'Blend' : 'Discover';
    class Wheel {
        constructor({onPlay, onClose, onSuggest}) { this.onSuggest=onSuggest; this.onPlay = onPlay; this.onClose = onClose; this.state = normalize(0, .25); }
        open() {
            if (this.el) return;
            try { this.state = normalize(...JSON.parse(localStorage.getItem('bs5c-mood') || '[0,0.25]')); } catch (_) {}
            this.el = document.createElement('section'); this.el.className = 'mood-view';
            this.el.innerHTML = `<header><h1>Mood wheel</h1><button data-close aria-label="Close mood wheel">Back</button></header>
                <div class="mood-stage"><span class="mood-north">Bright</span><span class="mood-west">Relaxed</span><span class="mood-east">Energetic</span><span class="mood-south">Contemplative</span>
                <div class="mood-disc" role="group" aria-label="Mood wheel"><i class="mood-ring one"></i><i class="mood-ring two"></i><i class="mood-point"></i></div></div>
                <div class="mood-label" aria-live="polite"></div><p>Wheel: mood · Pointer: familiar to discovery · GO: play</p>
                <div class="mood-status" aria-live="polite"></div><button class="mood-play">Play this mood</button>`;
            document.body.appendChild(this.el);
            this.el.querySelector('[data-close]').onclick = () => this.close();
            this.el.querySelector('.mood-play').onclick = () => this.play();
            const disc = this.el.querySelector('.mood-disc');
            const point = e => { const rect = disc.getBoundingClientRect(); const rad = rect.width/2;
                this.changed=true; localStorage.setItem('bs5c-mood-user','1'); this.state = fromPoint((e.clientX-rect.left-rad)/rad, (e.clientY-rect.top-rad)/rad); this.render(); };
            disc.onpointerdown = e => { disc.setPointerCapture(e.pointerId); point(e); };
            disc.onpointermove = e => { if (disc.hasPointerCapture(e.pointerId)) point(e); };
            this.render(); this.notify(true);
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
            this.el.querySelector('.mood-label').textContent = `${ring(this.state.radius)} · ${Math.round(this.state.angle)}°`;
            localStorage.setItem('bs5c-mood', JSON.stringify([this.state.angle,this.state.radius]));
        }
        nav(data) { this.changed=true; localStorage.setItem('bs5c-mood-user','1'); const sign = data?.direction === 'clock' ? 1 : data?.direction === 'counter' ? -1 : 0;
            this.state = normalize(this.state.angle + sign * 5 * clamp(data?.speed || 1,1,4), this.state.radius); this.render(); }
        laser(position) { this.changed=true; localStorage.setItem('bs5c-mood-user','1'); this.state = normalize(this.state.angle,(Number(position)-3)/120); this.render(); }
        key(key) {
            if (['Escape','Backspace'].includes(key)) return this.close();
            if (key === 'Enter') return this.play();
            if (key === 'ArrowUp') this.nav({direction:'counter'});
            if (key === 'ArrowDown') this.nav({direction:'clock'});
            if (key === 'ArrowLeft' || key === 'ArrowRight') { this.state = normalize(this.state.angle,this.state.radius+(key === 'ArrowRight' ? .05 : -.05)); this.render(); }
        }
        async play() {
            if (this.busy) return; this.busy = true;
            const status = this.el.querySelector('.mood-status'), button = this.el.querySelector('.mood-play'); button.disabled = true;
            status.textContent = 'Building your mix…';
            try { status.textContent = await this.onPlay({...this.state}) || 'Playing'; }
            catch(e) { status.textContent = e.message || 'Could not start this mood'; }
            finally { this.busy=false; if (this.el) button.disabled=false; }
        }
        close() { this.el?.remove(); this.el = null; this.notify(false); this.onClose?.(); }
        get active() { return !!this.el; }
    }
    const api = {normalize, fromPoint, ring, Wheel}; root.MoodWheel = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
