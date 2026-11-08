// Coordinates are source fractions; the rectangle's pixel aspect follows output size.
const $ = id => document.getElementById(id);
const keys = ['x','y','width','height'];
const clamp = (value,min,max) => Math.max(min,Math.min(max,value));
export class ZoomSelector {
  constructor(onChange = () => {}) {
    this.canvas = $('zoom-canvas'); this.disabled = true; this.source = null;
    this.onChange = onChange;
    this.canvas.onpointerdown = event => {
      if (this.disabled || this.locked || !event.isPrimary || event.button !== 0) return;
      event.preventDefault(); this.canvas.setPointerCapture(event.pointerId);
      this.start = this.point(event); this.previous = this.region ? {...this.region} : null;
      this.moving = this.contains(this.start);
      if (!this.moving) this.region = null;
      this.changed(); this.cursor(this.start);
    };
    this.canvas.onpointermove = event => {
      if (!this.start || this.disabled) {this.cursor(this.point(event)); return;}
      event.preventDefault(); const [x,y] = this.point(event), [sx,sy] = this.start;
      if (this.moving) {
        this.region = {...this.previous,x:clamp(this.previous.x+x-sx,0,1-this.previous.width),y:clamp(this.previous.y+y-sy,0,1-this.previous.height)};
      } else {
        // Fit an output-shaped rectangle between the anchor and the image edges.
        const right = x >= sx, down = y >= sy;
        const width = Math.min(Math.max(Math.abs(x-sx),Math.abs(y-sy)*this.ratio),right ? 1-sx : sx,(down ? 1-sy : sy)*this.ratio);
        const height = width / this.ratio;
        this.region = width > 0 && height > 0 ? {x:right ? sx : sx-width,y:down ? sy : sy-height,width,height} : null;
      }
      this.changed(); this.cursor([x,y]);
    };
    this.canvas.onpointerup = event => {
      if (!this.start) return;
      if (!this.moving && this.region && (this.region.width < .01 || this.region.height < .01)) this.region = null;
      this.start = null; this.changed(); this.cursor(this.point(event));
    };
    this.canvas.onpointercancel = this.canvas.onlostpointercapture = () => {
      if (this.start) {this.region = this.previous; this.start = null; this.changed(); this.cursor();}
    };
    this.canvas.onpointerleave = () => {if (!this.start) this.cursor();};
    for (const key of keys) $(`zoom-${key}`).onchange = () => {
      const value = Number($(`zoom-${key}`).value);
      if (!Number.isFinite(value)) return this.preview();
      this.region ||= this.centered();
      if (key === 'x' || key === 'y') this.region[key] = clamp(value/100,0,1-this.region[key === 'x' ? 'width' : 'height']);
      else {
        const width = clamp(value/100,.01,1)*(key === 'height' ? this.ratio : 1);
        this.region = this.fit(width,this.region.x+this.region.width/2,this.region.y+this.region.height/2);
      }
      this.changed();
    };
    $('zoom-reset').onclick = () => {this.region = this.centered(); this.changed();};
  }
  get ratio() {return (this.outputWidth/this.outputHeight)/(this.canvas.width/this.canvas.height);}
  fit(width, cx, cy) {
    width = Math.min(width,1,this.ratio); const height = width/this.ratio;
    return {x:clamp(cx-width/2,0,1-width),y:clamp(cy-height/2,0,1-height),width,height};
  }
  centered() {return this.fit(Math.min(1,this.ratio)*.5,.5,.5);}
  contains([x,y]) {const r=this.region; return r && x>=r.x && x<=r.x+r.width && y>=r.y && y<=r.y+r.height;}
  cursor(point = [-1,-1]) {this.canvas.style.cursor = this.disabled || this.locked ? 'default' : this.start && this.moving ? 'grabbing' : this.contains(point) ? 'grab' : 'crosshair';}
  changed() {this.preview(); this.onChange();}
  hasSelection() {return Boolean(this.source && (this.locked || this.region));}
  setOutputSize(width,height) {
    if (![width,height].every(n=>Number.isInteger(n)&&n>=64&&n<=1920&&n%16===0)) return;
    const oldRatio = this.outputWidth/this.outputHeight;
    this.outputWidth=width; this.outputHeight=height;
    if (!this.source || this.locked || oldRatio===width/height) return;
    if (this.region) {
      const r=this.region;
      this.region=this.fit(Math.sqrt(r.width*r.height*this.ratio),r.x+r.width/2,r.y+r.height/2);
    }
    this.changed();
  }
  setSource(source, locked = false, width = source.naturalWidth, height = source.naturalHeight) {
    this.source = source; this.locked = locked; this.start = null;
    this.knownRegion = !locked || typeof locked === 'object';
    this.canvas.width = source.naturalWidth; this.canvas.height = source.naturalHeight;
    this.outputWidth=width; this.outputHeight=height;
    this.region = typeof locked === 'object' ? {...locked} : this.centered();
    this.changed(); this.setDisabled(this.disabled);
  }
  clear() {this.source = null; this.start = null; this.region = null; this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);}
  setDisabled(disabled) {
    this.disabled = Boolean(disabled);
    const blocked = this.disabled || this.locked;
    this.canvas.setAttribute('aria-disabled',String(Boolean(blocked)));
    for (const key of [...keys,'reset']) $(`zoom-${key}`).disabled = Boolean(blocked);
    if (this.disabled && this.start) {this.region = this.previous; this.start = null; this.preview();}
    this.cursor();
  }
  point(event) {
    const rect = this.canvas.getBoundingClientRect();
    return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
  }
  preview() {
    if (!this.source) return;
    const {width:w,height:h} = this.canvas, ctx = this.canvas.getContext('2d');
    ctx.drawImage(this.source,0,0);
    if (!this.locked && this.region) {
      const r = this.region, x = Math.min(w-1,Math.round(r.x*w)), y = Math.min(h-1,Math.round(r.y*h));
      const right = Math.max(x,Math.min(w-1,Math.round((r.x+r.width)*w)-1)), bottom = Math.max(y,Math.min(h-1,Math.round((r.y+r.height)*h)-1));
      const thickness = Math.min(Math.max(3,Math.round(Math.min(w,h)*.008)),Math.max(1,Math.floor(Math.min(right-x+1,bottom-y+1)/2)));
      ctx.fillStyle = '#FF0000';
      ctx.fillRect(x,y,right-x+1,thickness); ctx.fillRect(x,bottom-thickness+1,right-x+1,thickness);
      ctx.fillRect(x,y,thickness,bottom-y+1); ctx.fillRect(right-thickness+1,y,thickness,bottom-y+1);
    }
    for (const key of keys) $(`zoom-${key}`).value = this.knownRegion && this.region ? Number((this.region[key]*100).toFixed(2)) : '';
    $('zoom-meta').textContent = this.locked ? 'Saved recipe: its red rectangle is already part of the reference. Generate to replay it, or use the result to select a fresh area.' : this.region ? `Source ${w} × ${h} · Selection matches output ${this.outputWidth} × ${this.outputHeight} · Drag inside to move; outside to replace.` : 'Selection cleared. Drag on the picture to select an area, or use Centre selection.';
  }
}
