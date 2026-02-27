/**
 * Voice Agent v2 — Browser WebSocket Client
 * ==========================================
 * Handles:
 *  - WebSocket connection to /ws/{session_id}
 *  - Microphone capture via getUserMedia → AudioWorklet → PCM chunks → base64
 *  - Client-side VAD (energy-based) for barge-in detection
 *  - Audio playback queue for TTS chunks
 *  - UI updates: transcript, streaming tokens, waveform, agent switching
 *  - Keyboard shortcut: Space = barge-in
 */

'use strict';

// ─── Config ─────────────────────────────────────────────────────────────────
// crypto.randomUUID() requires a secure context (HTTPS / localhost).
// Fall back to Math.random() UUID so the page works from any origin.
function _uuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}
const SESSION_ID   = _uuid();
const WS_PROTOCOL  = location.protocol === 'https:' ? 'wss' : 'ws';
const WS_URL       = `${WS_PROTOCOL}://${location.host}/ws/${SESSION_ID}`;
const SAMPLE_RATE  = 16000;
const CHUNK_MS     = 50;              // send audio every 50ms
const VAD_THRESHOLD = 0.015;         // RMS above → speech
// ── Barge-in tuning ──────────────────────────────────────────────────────────────
const BARGE_RMS         = 0.045;  // RMS must exceed this to interrupt (raised from 0.018)
const BARGE_COOLDOWN_MS = 2500;   // minimum ms between two barge-in events
const BARGE_DEAF_MS     = 800;    // ms to ignore mic after barge-in (echo decay)
const MIN_SPEECH_MS     = 400;    // minimum real speech duration before sending to STT
const LOG_MAX       = 500;           // max log lines to keep in DOM

// ─── State ──────────────────────────────────────────────────────────────────
let ws               = null;
let currentTurnId    = 0;
let currentAgent     = 'bob';
let isMicActive      = false;
let isTtsPlaying     = false;
let streamingText    = '';
let activeStreamTurn = null;

// Barge-in protection
let _bargeLastMs   = 0;       // timestamp of last barge-in
let _bargeDeafUntil = 0;      // ignore mic RMS until this timestamp

// Audio
let audioCtx         = null;
let mediaStream      = null;
let workletNode      = null;
let waveformAnimId   = null;
let analyser         = null;

// TTS playback queue
let ttsQueue         = [];  // list of { buffer: ArrayBuffer }
let ttsPlaying       = false;

// Waveform
let waveformData     = new Float32Array(128);

// ─── DOM refs ────────────────────────────────────────────────────────────────
const $transcript      = document.getElementById('transcript');
const $streamResp      = document.getElementById('streaming-response');
const $tokenDisplay    = document.getElementById('token-display');
const $micBtn          = document.getElementById('mic-btn');
const $micIcon         = document.getElementById('mic-icon');
const $micLabel        = document.getElementById('mic-label');
const $vadIndicator    = document.getElementById('vad-indicator');
const $rmsDisplay      = document.getElementById('rms-display');
const $waveCanvas      = document.getElementById('waveform-canvas');
const $statePanel      = document.getElementById('state-panel');
const $latency         = document.getElementById('latency-display');
const $statusDot       = document.getElementById('status-dot');
const $statusLabel     = document.getElementById('status-label');
const $textInput       = document.getElementById('text-input');

// ─── WebRTC state ─────────────────────────────────────────────────────────────
let pc             = null;   // RTCPeerConnection
let rtcMicStream   = null;   // MediaStream for WebRTC mic track
let rtcAudioEl    = null;   // <audio> element for incoming agent track
let rtcReady      = false;  // true once ICE connected

// ─── WebSocket ───────────────────────────────────────────────────────────────
function connectWS() {
  setStatus('connecting');
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setStatus('connected');
    pingLoop();
    // Start WebRTC negotiation immediately after WS opens
    initWebRTC().catch(err => logToPanel(`WebRTC init error: ${err.message}`, 'ERROR'));
  };

  ws.onmessage = (evt) => {
    try {
      const payload = JSON.parse(evt.data);
      const events = Array.isArray(payload) ? payload : [payload];
      events.forEach(handleEvent);
    } catch {}
  };

  ws.onclose = () => {
    setStatus('disconnected');
    closeWebRTC();
    setTimeout(connectWS, 3000);
  };

  ws.onerror = () => ws.close();
}

// ─── WebRTC ───────────────────────────────────────────────────────────────────
// Uses aiortc on the server side. Signaling travels over the existing WebSocket.
// Audio travels over DTLS-SRTP — browser AEC eliminates TTS echo automatically.

async function initWebRTC() {
  closeWebRTC();
  pc = new RTCPeerConnection({
    // No STUN/TURN needed — all local loopback
    iceServers: [],
    iceTransportPolicy: 'all',
  });

  // ── Incoming agent audio track (TTS) ────────────────────────────────────
  pc.ontrack = (evt) => {
    if (evt.track.kind === 'audio') {
      logToPanel('WebRTC: agent audio track received', 'INFO');
      if (!rtcAudioEl) {
        rtcAudioEl = document.createElement('audio');
        rtcAudioEl.autoplay = true;
        rtcAudioEl.style.display = 'none';
        document.body.appendChild(rtcAudioEl);
      }
      rtcAudioEl.srcObject = new MediaStream([evt.track]);
      // NOTE: do NOT touch isTtsPlaying here — the TTS queue may still be
      // playing the greeting through the Web Audio API path.
    }
  };

  // ── ICE candidate relay ──────────────────────────────────────────────────
  pc.onicecandidate = (evt) => {
    if (evt.candidate) {
      send({
        type: 'ice_candidate',
        candidate: {
          candidate:     evt.candidate.candidate,
          sdpMid:        evt.candidate.sdpMid,
          sdpMLineIndex: evt.candidate.sdpMLineIndex,
        },
      });
    }
  };

  pc.onconnectionstatechange = () => {
    rtcReady = pc.connectionState === 'connected';
    logToPanel(`WebRTC: ${pc.connectionState}`, 'INFO');
  };

  // ── Mic track (send to server for STT) ──────────────────────────────────
  try {
    rtcMicStream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    rtcMicStream.getAudioTracks().forEach(t => pc.addTrack(t, rtcMicStream));
    logToPanel('WebRTC: mic track added', 'INFO');
  } catch (err) {
    logToPanel(`WebRTC mic error: ${err.message}`, 'WARN');
    // Continue — server still works via WebSocket audio_chunk fallback
  }

  // ── Generate SDP offer and send to server ────────────────────────────────
  const offer = await pc.createOffer({ offerToReceiveAudio: true });
  await pc.setLocalDescription(offer);
  send({ type: 'webrtc_offer', sdp: offer.sdp });
  logToPanel('WebRTC: SDP offer sent', 'INFO');
}

function closeWebRTC() {
  if (pc) { pc.close(); pc = null; }
  if (rtcMicStream) { rtcMicStream.getTracks().forEach(t => t.stop()); rtcMicStream = null; }
  if (rtcAudioEl)   { rtcAudioEl.srcObject = null; rtcAudioEl.remove(); rtcAudioEl = null; }
  rtcReady = false;
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function pingLoop() {
  setInterval(() => send({ type: 'ping' }), 20000);
}

// ─── Event Router ────────────────────────────────────────────────────────────
function handleEvent(ev) {
  const { type, turn_id } = ev;

  switch (type) {
    case 'connected':
      currentAgent = ev.agent || 'bob';
      updateAgentUI(currentAgent);
      setStatus('connected');   // ensure status is green even if onopen raced
      break;

    case 'pong': break;

    case 'webrtc_answer':
      if (pc && ev.sdp) {
        pc.setRemoteDescription({ type: 'answer', sdp: ev.sdp })
          .then(() => logToPanel('WebRTC: remote description set', 'INFO'))
          .catch(e => logToPanel(`WebRTC answer error: ${e.message}`, 'ERROR'));
      }
      break;

    case 'ice_candidate':
      if (pc && ev.candidate) {
        pc.addIceCandidate(ev.candidate)
          .catch(e => logToPanel(`ICE candidate error: ${e.message}`, 'WARN'));
      }
      break;

    case 'webrtc_state':
      logToPanel(`WebRTC server state: ${ev.state}`, 'INFO');
      break;

    case 'stt_processing':
      if (turn_id < currentTurnId) return;
      showVad('processing');
      break;

    case 'partial_transcript':
      if (turn_id < currentTurnId) return;
      showPartialTranscript(ev.text);
      break;

    case 'final_transcript':
      if (turn_id < currentTurnId) return;
      hidePartialTranscript();
      addTurn('user', ev.text);
      if (ev.latency_ms) $latency.textContent = `STT: ${ev.latency_ms}ms`;
      // Start streaming response container
      streamingText = '';
      $tokenDisplay.textContent = '';
      $streamResp.classList.remove('hidden');
      activeStreamTurn = turn_id;
      break;

    case 'llm_token':
      if (turn_id < currentTurnId && turn_id !== activeStreamTurn) return;
      appendStreamingToken(ev.token);
      break;

    case 'tts_chunk':
      if (turn_id < currentTurnId) return;
      enqueueTtsChunk(ev.audio);
      break;

    case 'tts_done':
      if (turn_id < currentTurnId) return;
      finalizeStreamingTurn();
      break;

    case 'agent_change':
      currentAgent = ev.agent;
      updateAgentUI(ev.agent);
      addSystemTurn(`Transferring to ${ev.agent === 'alice' ? 'Alice 👩‍🔧' : 'Bob 🧑‍💼'}…`);
      break;

    case 'guardrail_blocked':
      finalizeStreamingTurn();
      logToPanel(`🛡️ Content blocked: ${ev.reason || 'Policy violation'}`, 'WARN');
      break;

    case 'barge_in_ack':
      logToPanel('✋ Interrupted — context saved', 'INFO');
      stopTtsQueue();
      finalizeStreamingTurn();
      break;

    case 'checkpoint_saved':
      addSystemTurn(`📌 Context checkpointed: "${(ev.partial || '').substring(0,60)}…"`);
      break;

    case 'checkpoint_restored':
      addSystemTurn(`🔄 Resuming from checkpoint: "${(ev.partial || '').substring(0,60)}…"`);
      break;

    case 'state_update':
      renderStatePanel(ev.state);
      break;

    case 'error':
      logToPanel(`❌ ${ev.message}`, 'ERROR');
      break;

    case 'log':
      appendLogLine(ev);
      break;
  }
}

// ─── Always-On Mic (push-to-talk REMOVED) ────────────────────────────────────
// Mic streams continuously. Client VAD detects end-of-utterance automatically:
//   - VAD_MIN_SPEECH_MS of speech detected
//   - followed by VAD_SILENCE_MS of silence
//   → auto-fires end_of_audio (no button release needed)
// Barge-in: speak (RMS > BARGE_RMS) while TTS plays to interrupt the agent.

const VAD_MIN_SPEECH_MS = 200;   // ms of speech to qualify as an utterance
const VAD_SILENCE_MS_C  = 10000; // ms of silence after speech → end_of_audio

let _vadSpeechStart = null;
let _vadLastSpeech  = null;
let _vadInSpeech    = false;
let _vadFired       = false;

function _resetVadState() {
  _vadSpeechStart = null;
  _vadLastSpeech  = null;
  _vadInSpeech    = false;
  _vadFired       = false;
}

async function startMicAlwaysOn() {
  if (isMicActive) return;
  try {
    audioCtx    = new AudioContext({ sampleRate: SAMPLE_RATE });
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    const source = audioCtx.createMediaStreamSource(mediaStream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);

    await audioCtx.audioWorklet.addModule('/static/pcm-processor.js');
    workletNode = new AudioWorkletNode(audioCtx, 'pcm-processor');
    source.connect(workletNode);

    let chunkBuffer = [];
    _resetVadState();

    workletNode.port.onmessage = (e) => {
      const rms = computeRms(e.data);
      updateWaveformData(e.data);
      updateVadUI(rms);
      const now = Date.now();

      // ── Barge-in detection ──────────────────────────────────────────────────────
      if (isTtsPlaying && rms > BARGE_RMS && now > _bargeDeafUntil) triggerBargein();

      // ── While TTS plays: buffer but don't VAD-fire or send audio_chunk ────────────
      // Sending mic audio while TTS plays causes the server to transcribe
      // room echo as if the user spoke. Only accumulate after TTS stops.
      if (isTtsPlaying || now < _bargeDeafUntil) {
        return;  // gate: discard mic audio during TTS playback + echo decay
      }

      // ── Normal VAD → end-of-utterance ──────────────────────────────────────────
      chunkBuffer.push(float32ToPcm16(e.data));

      if (rms >= VAD_THRESHOLD) {
        if (!_vadInSpeech) { _vadInSpeech = true; _vadSpeechStart = now; _vadFired = false; }
        _vadLastSpeech = now;
      } else if (_vadInSpeech && _vadLastSpeech) {
        const silence = now - _vadLastSpeech;
        const speech  = _vadLastSpeech - _vadSpeechStart;
        // Guard: require minimum real speech before sending to STT
        if (!_vadFired && speech >= MIN_SPEECH_MS && silence >= VAD_SILENCE_MS_C) {
          _vadFired = true;
          if (chunkBuffer.length) {
            send({ type: 'audio_chunk', data: arrayBufferToBase64(mergeChunks(chunkBuffer)), turn_id: currentTurnId });
            chunkBuffer = [];
          }
          send({ type: 'end_of_audio', turn_id: currentTurnId });
          showVad('processing');
          _resetVadState();
        }
      }
    };

    // Continuous chunk flush for server-side VAD
    // GATED: do not send audio_chunk while TTS is playing or during echo decay.
    // Sending mic audio during TTS causes the server to transcribe room echo.
    workletNode._sendInterval = setInterval(() => {
      if (!chunkBuffer.length || _vadFired) return;
      if (isTtsPlaying || Date.now() < _bargeDeafUntil) return;  // gate
      send({ type: 'audio_chunk', data: arrayBufferToBase64(mergeChunks(chunkBuffer)), turn_id: currentTurnId });
      chunkBuffer = [];
    }, CHUNK_MS);

    isMicActive = true;
    $micBtn.classList.add('recording');
    $micIcon.textContent = '🔴';
    $micLabel.textContent = 'Session Active (Recording)';
    document.getElementById('end-btn').style.display = 'flex';
    startWaveformDraw();
    showVad('listening');
    logToPanel('🎤 Mic on — speak naturally, stops automatically', 'INFO');

  } catch (err) {
    logToPanel(`Microphone error: ${err.message}`, 'ERROR');
  }
}

function stopMicAlwaysOn() {
  if (workletNode) { clearInterval(workletNode._sendInterval); workletNode.disconnect(); workletNode = null; }
  if (mediaStream)  { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  if (audioCtx)     { audioCtx.close(); audioCtx = null; }
  isMicActive = false;
  $micBtn.classList.remove('recording');
  $micIcon.textContent = '🎤';
  $micLabel.textContent = 'Start Session';
  document.getElementById('end-btn').style.display = 'none';
  stopWaveformDraw();
  showVad('idle');
  _resetVadState();
}

// Public toggle (called from onclick and keyboard shortcut)
function toggleMic() {
  if (isMicActive) return; // if active, use the End Session button to stop
  startMicAlwaysOn();
}

// Ensure the UI closes fully on 'end session'
function endSession() {
  stopMicAlwaysOn();
  logToPanel('⏹ Session ended by user.', 'INFO');
  addSystemTurn('Thank you for using the Voice Agent. Have a great day!');
  if (ws) {
    ws.close();
  }
}

// ─── Barge-in ────────────────────────────────────────────────────────────────
function doBargein() {
  currentTurnId++;
  isTtsPlaying = false;       // immediately clear so next mic frame is not re-triggered
  _bargeDeafUntil = Date.now() + BARGE_DEAF_MS;  // suppress echo for a bit
  send({ type: 'barge_in', turn_id: currentTurnId });
  stopTtsQueue();
  finalizeStreamingTurn();
}

function triggerBargein() {
  if (!isTtsPlaying) return;
  const now = Date.now();
  if (now < _bargeDeafUntil) return;          // still in echo-decay deaf period
  if (now - _bargeLastMs < BARGE_COOLDOWN_MS) return;  // too soon after last barge-in
  _bargeLastMs = now;
  doBargein();
}

function sendTransfer(agent) {
  const phrase = agent === 'alice' ? 'Transfer me to Alice' : 'Go back to Bob';
  send({ type: 'text_input', text: phrase, turn_id: ++currentTurnId });
  addTurn('user', phrase);
  showStreamingStart();
}

// ─── Text input ──────────────────────────────────────────────────────────────
function sendText() {
  const text = $textInput.value.trim();
  if (!text) return;
  $textInput.value = '';
  currentTurnId++;
  send({ type: 'text_input', text, turn_id: currentTurnId });
  addTurn('user', text);
  showStreamingStart();
}

$textInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendText(); });

// ─── TTS Playback Queue ───────────────────────────────────────────────────────
async function enqueueTtsChunk(base64Audio) {
  const bytes = base64ToArrayBuffer(base64Audio);
  ttsQueue.push(bytes);
  isTtsPlaying = true;
  if (!ttsPlaying) drainTtsQueue();
}

async function drainTtsQueue() {
  ttsPlaying = true;
  while (ttsQueue.length > 0) {
    const chunk = ttsQueue.shift();
    try {
      const ctx = new AudioContext();
      const decoded = await ctx.decodeAudioData(chunk.slice(0));
      await playBuffer(ctx, decoded);
      ctx.close();
    } catch {}
  }
  ttsPlaying = false;
  // Notify server that playback finished
  send({ type: 'tts_playback_done' });
  isTtsPlaying = false;
  // Dead period: room echo can linger 400-700ms after last TTS chunk.
  // Gate mic audio + barge-in for this window so the echo can't cancel the
  // next LLM response (mirrors server-side TTS_DEAF_SECS = 0.7).
  _bargeDeafUntil = Date.now() + 700;
}

function playBuffer(ctx, buffer) {
  return new Promise(resolve => {
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    src.onended = resolve;
    src.start(0);
  });
}

function stopTtsQueue() {
  ttsQueue = [];
  ttsPlaying = false;
  isTtsPlaying = false;
}

// ─── Streaming UI ─────────────────────────────────────────────────────────────
function showStreamingStart() {
  streamingText = '';
  $tokenDisplay.textContent = '';
  $streamResp.classList.remove('hidden');
  scrollTranscript();
}

function appendStreamingToken(token) {
  streamingText += token;
  $tokenDisplay.textContent = streamingText;
  scrollTranscript();
}

function finalizeStreamingTurn() {
  if (streamingText.trim()) {
    addTurn(currentAgent, streamingText);
  }
  streamingText = '';
  $tokenDisplay.textContent = '';
  $streamResp.classList.add('hidden');
  activeStreamTurn = null;
}

// ─── Transcript ───────────────────────────────────────────────────────────────
let partialEl = null;

function showPartialTranscript(text) {
  if (!partialEl) {
    partialEl = document.createElement('div');
    partialEl.className = 'turn user';
    partialEl.innerHTML = `
      <div class="turn-avatar">👤</div>
      <div class="turn-content">
        <div class="turn-speaker">You (listening…)</div>
        <div class="partial-bubble" id="partial-text"></div>
      </div>`;
    $transcript.appendChild(partialEl);
  }
  document.getElementById('partial-text').textContent = text;
  scrollTranscript();
}

function hidePartialTranscript() {
  if (partialEl) { partialEl.remove(); partialEl = null; }
}

function addTurn(speaker, text) {
  hidePartialTranscript();
  const avatars = { user: '👤', bob: '🧑‍💼', alice: '👩‍🔧' };
  const labels  = { user: 'You', bob: 'Bob', alice: 'Alice' };

  const div = document.createElement('div');
  div.className = `turn ${speaker}`;
  div.innerHTML = `
    <div class="turn-avatar">${avatars[speaker] || '🤖'}</div>
    <div class="turn-content">
      <div class="turn-speaker">${labels[speaker] || speaker}</div>
      <div class="turn-bubble">${escapeHtml(text)}</div>
    </div>`;
  $transcript.appendChild(div);
  scrollTranscript();
}

function addSystemTurn(text) {
  const div = document.createElement('div');
  div.className = 'turn system';
  div.innerHTML = `<div class="turn-content" style="width:100%;text-align:center"><div class="turn-bubble">${escapeHtml(text)}</div></div>`;
  $transcript.appendChild(div);
  scrollTranscript();
}

function scrollTranscript() {
  $transcript.scrollTop = $transcript.scrollHeight;
}

// ─── Agent UI ──────────────────────────────────────────────────────────────────
function updateAgentUI(agent) {
  document.getElementById('card-bob').classList.toggle('active', agent === 'bob');
  document.getElementById('card-alice').classList.toggle('active', agent === 'alice');
}

// ─── State Panel ──────────────────────────────────────────────────────────────
function renderStatePanel(state) {
  if (!state || !state.project) return;
  const p = state.project;
  const rows = [
    ['Room',     p.room || '—'],
    ['Budget',   p.budget || '—'],
    ['Timeline', p.timeline || '—'],
    ['Mode',     p.diy_or_contractor || '—'],
    ['Goals',    (p.goals || []).join(', ') || '—'],
    ['Risks',    (state.risks || []).join(', ') || '—'],
  ];
  $statePanel.innerHTML = rows.map(([k, v]) =>
    `<div class="state-item"><span class="state-key">${k}</span><span class="state-val">${escapeHtml(String(v))}</span></div>`
  ).join('');
}

// ─── VAD / Waveform UI ────────────────────────────────────────────────────────
function updateVadUI(rms) {
  $rmsDisplay.textContent = `RMS: ${rms.toFixed(4)}`;
  if (rms > VAD_THRESHOLD) {
    const state = (isTtsPlaying && rms > BARGE_RMS) ? 'barge-in' : 'speech';
    showVad(state);
    $waveCanvas.classList.add('active');
  } else {
    showVad('listening');
    $waveCanvas.classList.remove('active');
  }
}

function showVad(state) {
  const labels = { idle: 'Idle', listening: 'Listening', speech: 'Speaking', processing: 'Processing…', 'barge-in': 'Barge-In!' };
  $vadIndicator.textContent = labels[state] || state;
  $vadIndicator.className = '';
  $vadIndicator.id = 'vad-indicator';
  if (state === 'speech') $vadIndicator.classList.add('speech');
  if (state === 'barge-in') $vadIndicator.classList.add('barge-in');
}

function startWaveformDraw() {
  const canvas = $waveCanvas;
  const ctx = canvas.getContext('2d');
  let w = 0, h = 0;

  function resize() {
    w = canvas.width = canvas.offsetWidth;
    h = canvas.height = canvas.offsetHeight;
  }
  resize();
  new ResizeObserver(resize).observe(canvas);

  function draw() {
    waveformAnimId = requestAnimationFrame(draw);
    ctx.clearRect(0, 0, w, h);

    if (!analyser) return;
    const buf = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteTimeDomainData(buf);

    ctx.lineWidth = 2;
    ctx.strokeStyle = currentAgent === 'alice' ? '#f472b6' : '#34d399';
    ctx.shadowColor = currentAgent === 'alice' ? '#f472b6' : '#34d399';
    ctx.shadowBlur = 8;
    ctx.beginPath();

    const sliceW = w / buf.length;
    let x = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = buf[i] / 128.0;
      const y = (v * h) / 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      x += sliceW;
    }
    ctx.lineTo(w, h / 2);
    ctx.stroke();
  }
  draw();
}

function stopWaveformDraw() {
  if (waveformAnimId) { cancelAnimationFrame(waveformAnimId); waveformAnimId = null; }
  const ctx = $waveCanvas.getContext('2d');
  ctx.clearRect(0, 0, $waveCanvas.width, $waveCanvas.height);
}

function updateWaveformData(float32) {
  waveformData = float32;
}

// ─── Tab switching (State / Logs) ────────────────────────────────────────────
let logsUnread = 0;
let activeTab  = 'state';

function switchTab(tab) {
  activeTab = tab;
  document.getElementById('pane-state').classList.toggle('hidden', tab !== 'state');
  document.getElementById('pane-logs' ).classList.toggle('hidden', tab !== 'logs');
  document.getElementById('tab-state').classList.toggle('active', tab === 'state');
  document.getElementById('tab-logs' ).classList.toggle('active', tab === 'logs');

  // Clear unread badge when switching to logs
  if (tab === 'logs') {
    logsUnread = 0;
    document.getElementById('tab-logs').removeAttribute('data-unread');
  }
}

function clearLogs() {
  document.getElementById('log-panel').innerHTML = '';
  logsUnread = 0;
  document.getElementById('tab-logs').removeAttribute('data-unread');
}

// ─── Main Tab switching (Chat / Visualizer) ──────────────────────────────────
let activeMainTab = 'chat';

function switchMainTab(tab) {
  activeMainTab = tab;
  document.getElementById('main-pane-chat').classList.toggle('hidden', tab !== 'chat');
  document.getElementById('main-pane-visualizer').classList.toggle('hidden', tab !== 'visualizer');
  document.getElementById('main-tab-chat').classList.toggle('active', tab === 'chat');
  document.getElementById('main-tab-visualizer').classList.toggle('active', tab === 'visualizer');
}

const LOG_LEVEL_ORDER = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
const MAX_LOG_LINES   = window.LOG_MAX || 500;

function appendLogLine(ev) {
  const panel = document.getElementById('log-panel');
  if (!panel) return;

  const level = ev.level || 'INFO';
  const div   = document.createElement('div');
  div.className = `log-line log-${level}`;

  // Shorten logger name: streaming.server → s.server
  const loggerShort = (ev.logger || '').replace(/\b(\w)\w+\./g, '$1.');

  div.innerHTML =
    `<span class="log-ts">${escapeHtml(ev.ts || '')}</span>` +
    `<span class="log-badge ${level}">${level}</span>` +
    `<span class="log-msg" title="${escapeHtml(ev.logger || '')}">${escapeHtml(ev.message || '')}</span>`;

  // Traceback block (errors)
  if (ev.traceback) {
    const tb = document.createElement('pre');
    tb.className = 'log-traceback';
    tb.textContent = ev.traceback;
    div.appendChild(tb);
  }

  panel.appendChild(div);

  // Prune old lines
  while (panel.children.length > MAX_LOG_LINES) {
    panel.removeChild(panel.firstChild);
  }

  // Auto-scroll if near bottom
  if (panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60) {
    panel.scrollTop = panel.scrollHeight;
  }

  // Unread badge when Logs tab is not active
  if (activeTab !== 'logs') {
    // Only badge for WARNING+ to avoid noise
    if ((LOG_LEVEL_ORDER[level] || 0) >= LOG_LEVEL_ORDER.WARNING) {
      logsUnread++;
      document.getElementById('tab-logs').setAttribute('data-unread', logsUnread > 99 ? '99+' : String(logsUnread));
    }
  }
}

// ─── Connection Status ────────────────────────────────────────────────────────
function setStatus(state) {
  const labels = { connected: 'Connected', connecting: 'Connecting…', disconnected: 'Reconnecting…' };
  $statusDot.className = 'status-dot ' + state;
  $statusLabel.textContent = labels[state] || state;
}

// ─── Log-panel helper (replaces toast) ───────────────────────────────────────
// Appends a client-generated line to the Logs tab, same visual style as
// server log lines so everything is in one place.
function logToPanel(msg, level = 'INFO') {
  const now = new Date();
  const hh  = String(now.getHours()).padStart(2,'0');
  const mm  = String(now.getMinutes()).padStart(2,'0');
  const ss  = String(now.getSeconds()).padStart(2,'0');
  const ms  = String(now.getMilliseconds()).padStart(3,'0');
  appendLogLine({ ts: `${hh}:${mm}:${ss}.${ms}`, level, logger: 'client', message: msg });
  // Auto-switch to Logs tab for ERROR-level messages so they're not missed
  if (level === 'ERROR') switchTab('logs');
}

// ─── Keyboard shortcuts ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.code === 'Space' && document.activeElement !== $textInput) {
    e.preventDefault();
    if (isTtsPlaying || streamingText) doBargein();  // Space = barge-in only
  }
});

// ─── Utilities ────────────────────────────────────────────────────────────────
function float32ToPcm16(float32Array) {
  const buf = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

function mergeChunks(chunks) {
  const total = chunks.reduce((acc, c) => acc + c.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) { merged.set(new Uint8Array(c), offset); offset += c.byteLength; }
  return merged.buffer;
}

function computeRms(float32) {
  if (!float32.length) return 0;
  const sum = float32.reduce((a, v) => a + v * v, 0);
  return Math.sqrt(sum / float32.length);
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let s = '';
  bytes.forEach(b => s += String.fromCharCode(b));
  return btoa(s);
}

function base64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const buf = new ArrayBuffer(bin.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i);
  return buf;
}

function escapeHtml(text) {
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── Bootstrap ───────────────────────────────────────────────────────────────
switchTab('state'); // init tab visibility
connectWS();
