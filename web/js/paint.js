// 2D paint board. The canvas is the actual texture atlas: what you paint here is
// what the GLB gets, and the UV lines come from Blender's own layout export.
import * as THREE from 'three';

export class PaintBoard {
  constructor(container) {
    this.container = container;
    this.size = 1024;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'paint-canvas';
    this.uv = document.createElement('canvas');
    this.uv.id = 'uvLayer';
    this.wrap = document.createElement('div');
    this.wrap.className = 'paint-wrap';
    this.wrap.append(this.canvas, this.uv);
    container.append(this.wrap);
    this.ctx = this.canvas.getContext('2d');
    this.uvCtx = this.uv.getContext('2d');
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.flipY = false;
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.tool = { color: '#c8ff4d', size: 28, opacity: 1, eraser: false };
    this.overlayOn = true;
    this.undoStack = [];
    this.onChange = () => {};
    this._drawing = false;
    this._last = null;
    this._bind();
  }

  async load({ textureUrl, uvUrl, size }) {
    this.size = size || 1024;
    for (const c of [this.canvas, this.uv]) {
      c.width = this.size;
      c.height = this.size;
    }
    this.ctx.fillStyle = '#d6d7db';
    this.ctx.fillRect(0, 0, this.size, this.size);
    if (textureUrl) {
      try {
        const img = await this._image(textureUrl);
        this.ctx.drawImage(img, 0, 0, this.size, this.size);
      } catch {
        /* a fresh atlas is fine */
      }
    }
    this.uvCtx.clearRect(0, 0, this.size, this.size);
    if (uvUrl) {
      try {
        const img = await this._image(uvUrl, true);
        this.uvCtx.globalAlpha = 0.95;
        this.uvCtx.drawImage(img, 0, 0, this.size, this.size);
      } catch {
        /* no overlay is not fatal */
      }
    }
    this.undoStack = [];
    this.texture.needsUpdate = true;
    this.onChange();
  }

  async _image(url, isSvg = false) {
    let src = url;
    if (isSvg) {
      const text = await fetch(url).then((r) => r.text());
      const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
      for (const el of doc.querySelectorAll('polygon, path, polyline, line')) {
        el.setAttribute('fill', 'none');
        el.setAttribute('stroke', '#6ef2c0');
        el.setAttribute('stroke-width', '1.3');
        el.setAttribute('stroke-opacity', '0.95');
      }
      src = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(doc)], { type: 'image/svg+xml' }));
    }
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
  }

  setTool(patch) {
    Object.assign(this.tool, patch);
  }

  setOverlay(on) {
    this.overlayOn = on;
    this.uv.style.display = on ? 'block' : 'none';
  }

  fill(color) {
    this._pushUndo();
    this.ctx.save();
    this.ctx.globalCompositeOperation = 'source-over';
    this.ctx.globalAlpha = 1;
    this.ctx.fillStyle = color;
    this.ctx.fillRect(0, 0, this.size, this.size);
    this.ctx.restore();
    this._changed();
  }

  undo() {
    const data = this.undoStack.pop();
    if (!data) return false;
    const img = new Image();
    img.onload = () => {
      this.ctx.clearRect(0, 0, this.size, this.size);
      this.ctx.drawImage(img, 0, 0, this.size, this.size);
      this._changed();
    };
    img.src = data;
    return true;
  }

  dataURL() {
    return this.canvas.toDataURL('image/png');
  }

  _pushUndo() {
    this.undoStack.push(this.canvas.toDataURL('image/png'));
    if (this.undoStack.length > 8) this.undoStack.shift();
  }

  _changed() {
    this.texture.needsUpdate = true;
    this.onChange(this.texture);
  }

  _point(e) {
    const rect = this.canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * this.size,
      y: ((e.clientY - rect.top) / rect.height) * this.size,
    };
  }

  _stroke(a, b) {
    const ctx = this.ctx;
    const scale = this.size / Math.max(this.canvas.getBoundingClientRect().width, 1);
    const radius = Math.max(1, (this.tool.size * scale) / 2);
    ctx.save();
    const erasing = this.tool.eraser;
    ctx.globalAlpha = this.tool.opacity;
    // Erase back to the atlas base colour: transparent holes render black in
    // engines that treat the material as opaque.
    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = erasing ? '#d6d7db' : this.tool.color;
    ctx.fillStyle = erasing ? '#d6d7db' : this.tool.color;
    ctx.lineWidth = radius * 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(b.x, b.y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  _bind() {
    this.canvas.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      this.canvas.setPointerCapture(e.pointerId);
      this._drawing = true;
      this._pushUndo();
      this._last = this._point(e);
      this._stroke(this._last, this._last);
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (!this._drawing) return;
      const p = this._point(e);
      this._stroke(this._last, p);
      this._last = p;
    });
    const end = () => {
      if (!this._drawing) return;
      this._drawing = false;
      this._last = null;
      this._changed();
    };
    this.canvas.addEventListener('pointerup', end);
    this.canvas.addEventListener('pointercancel', end);
    this.canvas.addEventListener('pointerleave', end);
  }
}
