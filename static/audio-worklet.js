// Traitement audio dans le navigateur (AudioWorklet).
// - mic    : micro -> PCM 16 bits mono 24 kHz, par trames de 80 ms (format Gradium)
// - player : PCM 16 bits 24 kHz reçu du serveur -> haut-parleur, coupable à tout moment
// Les deux renvoient un niveau sonore (0..1) pour animer l'orbe.

const RATE = 24000;
const FRAME = 1920; // 80 ms à 24 kHz

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / RATE; // ex. 48000 / 24000 = 2
    this.pos = 0;
    this.frame = new Int16Array(FRAME);
    this.n = 0;
    this.sum = 0;
    this.count = 0;
  }
  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;
    // Rééchantillonnage linéaire vers 24 kHz
    while (this.pos < input.length - 1) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const s = input[i] * (1 - f) + input[i + 1] * f;
      this.frame[this.n++] = Math.max(-1, Math.min(1, s)) * 0x7fff;
      this.sum += s * s; this.count++;
      if (this.n === FRAME) {
        const level = Math.sqrt(this.sum / this.count);
        this.port.postMessage({ pcm: this.frame.buffer, level }, [this.frame.buffer]);
        this.frame = new Int16Array(FRAME);
        this.n = 0; this.sum = 0; this.count = 0;
      }
      this.pos += this.step;
    }
    this.pos -= input.length;
    return true;
  }
}

class PlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];      // Float32Array à 24 kHz
    this.cur = null;
    this.pos = 0;         // position (fractionnaire) dans this.cur
    this.step = RATE / sampleRate;
    this.sum = 0; this.count = 0; this.playing = false;
    this.port.onmessage = (e) => {
      if (e.data === "clear") { this.queue = []; this.cur = null; this.pos = 0; return; }
      const pcm = new Int16Array(e.data);
      const f = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
      this.queue.push(f);
    };
  }
  next() {
    if (this.cur && this.pos < this.cur.length - 1) return true;
    if (this.cur && this.queue.length) {
      this.pos -= this.cur.length; // garde la continuité entre deux morceaux
      if (this.pos < 0) this.pos = 0;
    } else {
      this.pos = 0;
    }
    this.cur = this.queue.shift() || null;
    return !!this.cur;
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    let active = false;
    for (let k = 0; k < out.length; k++) {
      if (!this.next()) { out[k] = 0; continue; }
      const i = Math.floor(this.pos), f = this.pos - i;
      const b = i + 1 < this.cur.length ? this.cur[i + 1] : this.cur[i];
      const s = this.cur[i] * (1 - f) + b * f;
      out[k] = s; this.sum += s * s; this.count++; active = true;
      this.pos += this.step;
    }
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    if (this.count >= 1200 || (!active && this.playing)) {
      const level = this.count ? Math.sqrt(this.sum / this.count) : 0;
      this.port.postMessage({ level, playing: active });
      this.sum = 0; this.count = 0;
    }
    this.playing = active;
    return true;
  }
}

registerProcessor("mic", MicProcessor);
registerProcessor("player", PlayerProcessor);
