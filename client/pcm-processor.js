/**
 * AudioWorklet processor — runs on dedicated audio thread.
 * Receives Float32 PCM frames from microphone and posts them to main thread.
 * This avoids blocking the UI thread with audio processing.
 */

class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._sampleCount = 0;
    // ~50ms of audio at 16kHz = 800 samples
    this._flushSamples = 800;
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch || ch.length === 0) return true;

    this._buf.push(new Float32Array(ch));
    this._sampleCount += ch.length;

    if (this._sampleCount >= this._flushSamples) {
      // Merge and post to main thread
      const merged = new Float32Array(this._sampleCount);
      let off = 0;
      for (const b of this._buf) { merged.set(b, off); off += b.length; }
      this._buf = [];
      this._sampleCount = 0;
      this.port.postMessage(merged);
    }

    return true; // keep processor alive
  }
}

registerProcessor('pcm-processor', PcmProcessor);
