// Rectangle coordinates are fractions of the source, independent of output size.
const $ = id => document.getElementById(id);
const keys = ['x','y','width','height'];
export class ZoomSelector {
  constructor() {
    this.canvas = $('zoom-canvas'); this.disabled = true; this.source = null;
    this.canvas.onpointerdown = event => {
      if (this.disabled || this.locked || !event.isPrimary || event.button !== 0) return;
      event.preventDefault(); this.canvas.setPointerCapture(event.pointerId);
      this.start = this.point(event); this.previous = {...this.region};
    };
    this.canvas.onpointermove = event => {
      if (!this.start || this.disabled) return;
      event.preventDefault(); const [x,y] = this.point(event), [sx,sy] = this.start;
      this.region = {x:Math.min(x,sx),y:Math.min(y,sy),width:Math.abs(x-sx),height:Math.abs(y-sy)};
      this.preview();
    };
    this.canvas.onpointerup = () => {
      if (!this.start) return;
      if (this.region.width < .01 || this.region.height < .01) this.region = this.previous;
      this.start = null; this.preview();
    };
    this.canvas.onpointercancel = this.canvas.onlostpointercapture = () => {
      if (this.start) {this.region = this.previous; this.start = null; this.preview();}
    };
    for (const key of keys) $(`zoom-${key}`).onchange = () => {
      const value = Number($(`zoom-${key}`).value);
      if (!Number.isFinite(value)) return this.preview();
      this.region[key] = Math.max(key === 'x' || key === 'y' ? 0 : .01,Math.min(key === 'x' || key === 'y' ? .99 : 1,value / 100));
      this.region.width = Math.min(this.region.width,1-this.region.x);
      this.region.height = Math.min(this.region.height,1-this.region.y);
      this.preview();
    };
    $('zoom-reset').onclick = () => {this.region = {x:.25,y:.25,width:.5,height:.5}; this.preview();};
  }
  setSource(source, locked = false) {
    this.source = source; this.locked = locked; this.start = null;
    this.region = typeof locked === 'object' ? {...locked} : {x:.25,y:.25,width:.5,height:.5};
    this.knownRegion = !locked || typeof locked === 'object';
    this.canvas.width = source.naturalWidth; this.canvas.height = source.naturalHeight;
    this.preview(); this.setDisabled(this.disabled);
  }
  clear() {this.source = null; this.start = null; this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);}
  setDisabled(disabled) {
    this.disabled = Boolean(disabled);
    const blocked = this.disabled || this.locked;
    this.canvas.setAttribute('aria-disabled',String(Boolean(blocked)));
    for (const key of [...keys,'reset']) $(`zoom-${key}`).disabled = Boolean(blocked);
    if (this.disabled && this.start) {this.region = this.previous; this.start = null; this.preview();}
  }
  point(event) {
    const rect = this.canvas.getBoundingClientRect();
    return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
  }
  preview() {
    if (!this.source) return;
    const {width:w,height:h} = this.canvas, ctx = this.canvas.getContext('2d');
    ctx.drawImage(this.source,0,0);
    if (!this.locked) {
      const r = this.region, x = Math.min(w-1,Math.round(r.x*w)), y = Math.min(h-1,Math.round(r.y*h));
      const right = Math.max(x,Math.min(w-1,Math.round((r.x+r.width)*w)-1)), bottom = Math.max(y,Math.min(h-1,Math.round((r.y+r.height)*h)-1));
      const thickness = Math.min(Math.max(3,Math.round(Math.min(w,h)*.008)),Math.max(1,Math.floor(Math.min(right-x+1,bottom-y+1)/2)));
      ctx.fillStyle = '#FF0000';
      ctx.fillRect(x,y,right-x+1,thickness); ctx.fillRect(x,bottom-thickness+1,right-x+1,thickness);
      ctx.fillRect(x,y,thickness,bottom-y+1); ctx.fillRect(right-thickness+1,y,thickness,bottom-y+1);
    }
    for (const key of keys) $(`zoom-${key}`).value = this.knownRegion ? Number((this.region[key]*100).toFixed(2)) : '';
    $('zoom-meta').textContent = this.locked ? 'Saved recipe: its red rectangle is already part of the reference. Generate to replay it, or use the result to select a fresh area.' : `Source ${w} × ${h} · Hollow red outline · Use “Zoom again from this result” after generation to continue closer.`;
  }
}
