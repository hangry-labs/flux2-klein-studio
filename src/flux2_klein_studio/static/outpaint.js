import {msg} from './i18n.js';
// A bounded reference canvas. Green is an RGB input marker, never an alpha mask.
const $ = id => document.getElementById(id);
export class GreenPainter {
  constructor() {
    this.canvas = $('paint-canvas'); this.base = document.createElement('canvas'); this.mask = document.createElement('canvas');
    this.strokes = []; this.source = null; this.disabled = true; this.current = null;
    this.canvas.onpointerdown = event => {
      if (this.disabled || !event.isPrimary || event.button !== 0) return;
      event.preventDefault(); this.canvas.setPointerCapture(event.pointerId);
      this.begin(this.point(event));
    };
    this.canvas.onpointermove = event => {if (this.current && !this.disabled) {event.preventDefault(); this.extend(this.point(event));}};
    this.canvas.onpointerup = this.canvas.onpointercancel = () => {this.current = null;};
    this.canvas.onkeydown = event => {
      if (this.disabled || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown',' '].includes(event.key)) return;
      event.preventDefault(); this.cursor ||= [this.width / 2, this.height / 2];
      const step = event.shiftKey ? 32 : 8;
      if (event.key === 'ArrowLeft') this.cursor[0] -= step;
      if (event.key === 'ArrowRight') this.cursor[0] += step;
      if (event.key === 'ArrowUp') this.cursor[1] -= step;
      if (event.key === 'ArrowDown') this.cursor[1] += step;
      this.cursor = [Math.max(0,Math.min(this.width,this.cursor[0])), Math.max(0,Math.min(this.height,this.cursor[1]))];
      if (event.key === ' ') {this.begin(this.cursor); this.current = null;}
      this.preview();
    };
    this.canvas.onblur = () => {this.cursor = null; this.preview();};
    $('paint-undo').onclick = () => {this.strokes.pop(); this.rebuild();};
    $('paint-clear').onclick = () => {this.strokes = []; this.rebuild();};
    $('paint-anchor').onchange = () => this.resize(this.width, this.height);
    $('paint-size').oninput = () => {$('paint-size-value').textContent = `${$('paint-size').value} px`;};
  }
  async setSource(url, width, height) {
    const image = new Image(); image.src = url; await image.decode();
    this.source = image; this.sourceWidth = width; this.sourceHeight = height;
    this.strokes = []; this.cursor = null; $('paint-anchor').value = 'center';
    this.resize(width, height);
  }
  clear() {$('paint-meta').textContent = ''; this.source = null; this.strokes = []; this.current = null; this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);}
  setDisabled(disabled) {
    this.disabled = Boolean(disabled);
    this.canvas.setAttribute('aria-disabled', String(this.disabled));
    for (const id of ['paint-size','paint-tool','paint-anchor','paint-undo','paint-clear']) $(id).disabled = this.disabled;
    if (this.disabled) this.current = null;
  }
  resize(width, height) {
    if (!this.source || ![width,height].every(n => Number.isInteger(n) && n >= 64 && n <= 1920 && n % 16 === 0)) return;
    this.width = width; this.height = height;
    const scale = Math.min(1, Math.sqrt(1024 * 1024 / (width * height)));
    const preparedWidth = Math.max(16,Math.floor(width * scale / 16) * 16), preparedHeight = Math.max(16,Math.floor(height * scale / 16) * 16);
    for (const canvas of [this.canvas,this.base,this.mask]) {canvas.width = preparedWidth; canvas.height = preparedHeight;}
    this.sx = preparedWidth / width; this.sy = preparedHeight / height;
    this.fit = Math.min(1,width / this.sourceWidth,height / this.sourceHeight);
    const drawnWidth = this.sourceWidth * this.fit, drawnHeight = this.sourceHeight * this.fit;
    const anchor = $('paint-anchor').value;
    this.x = (width - drawnWidth) * (anchor.includes('left') ? 0 : anchor.includes('right') ? 1 : .5);
    this.y = (height - drawnHeight) * (anchor.includes('top') ? 0 : anchor.includes('bottom') ? 1 : .5);
    const context = this.base.getContext('2d'); context.fillStyle = '#00FF00'; context.fillRect(0,0,preparedWidth,preparedHeight);
    context.drawImage(this.source,Math.round(this.x * this.sx),Math.round(this.y * this.sy),Math.round(drawnWidth * this.sx),Math.round(drawnHeight * this.sy));
    this.rebuild();
    this.describe();
  }
  describe() {
    if (!this.source) return;
    $('paint-meta').textContent = msg`Output ${this.width} × ${this.height} · Prepared reference ${this.canvas.width} × ${this.canvas.height}. Extra canvas is green; smaller canvases fit the source proportionally.`;
  }
  point(event) {
    const rect = this.canvas.getBoundingClientRect();
    return [(event.clientX - rect.left) * this.width / rect.width,(event.clientY - rect.top) * this.height / rect.height];
  }
  sourcePoint(point) {return [(point[0] - this.x) / this.fit,(point[1] - this.y) / this.fit];}
  begin(point) {
    this.current = {tool:$('paint-tool').value,size:Number($('paint-size').value) / this.fit,points:[this.sourcePoint(point)]};
    this.strokes.push(this.current); this.drawStroke(this.current); this.preview();
  }
  extend(point) {
    const previous = this.current.points.at(-1), next = this.sourcePoint(point);
    this.current.points.push(next); this.drawStroke({...this.current,points:[previous,next]}); this.preview();
  }
  drawStroke(stroke) {
    const context = this.mask.getContext('2d'); context.save();
    context.setTransform(this.sx * this.fit,0,0,this.sy * this.fit,this.x * this.sx,this.y * this.sy);
    context.globalCompositeOperation = stroke.tool === 'erase' ? 'destination-out' : 'source-over';
    context.strokeStyle = context.fillStyle = '#00FF00'; context.lineWidth = stroke.size; context.lineCap = context.lineJoin = 'round';
    context.beginPath();
    if (stroke.points.length === 1) {context.arc(stroke.points[0][0],stroke.points[0][1],stroke.size / 2,0,Math.PI * 2); context.fill();}
    else {context.moveTo(...stroke.points[0]); for (const point of stroke.points.slice(1)) context.lineTo(...point); context.stroke();}
    context.restore();
  }
  rebuild() {
    this.mask.getContext('2d').clearRect(0,0,this.mask.width,this.mask.height);
    for (const stroke of this.strokes) this.drawStroke(stroke);
    this.preview();
  }
  preview() {
    const context = this.canvas.getContext('2d'); context.clearRect(0,0,this.canvas.width,this.canvas.height);
    context.drawImage(this.base,0,0); context.drawImage(this.mask,0,0);
    if (this.cursor && this.source) {
      context.save(); context.setTransform(this.sx,0,0,this.sy,0,0); context.strokeStyle = '#fff'; context.lineWidth = 2 / this.sx;
      context.beginPath(); context.arc(...this.cursor,Number($('paint-size').value) / 2,0,Math.PI * 2); context.stroke(); context.restore();
    }
  }
  async blob() {
    const output = document.createElement('canvas'); output.width = this.base.width; output.height = this.base.height;
    const context = output.getContext('2d'); context.drawImage(this.base,0,0);
    const image = context.getImageData(0,0,output.width,output.height), mask = this.mask.getContext('2d').getImageData(0,0,output.width,output.height);
    // Canvas strokes have antialiased edges. Submit binary, exact RGB markers.
    for (let i = 0; i < image.data.length; i += 4) if (mask.data[i + 3] >= 128) {image.data[i] = 0; image.data[i + 1] = 255; image.data[i + 2] = 0; image.data[i + 3] = 255;}
    context.putImageData(image,0,0);
    return new Promise((resolve,reject) => output.toBlob(blob => blob ? resolve(blob) : reject(new Error(msg('Could not prepare the painted reference.'))),'image/png'));
  }
}
