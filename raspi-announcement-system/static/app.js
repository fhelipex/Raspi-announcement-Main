/**
 * app.js — Aura 2-Way Intercom Dashboard
 *
 * Mic Strategy (auto-fallback):
 *   1. Try Web Speech API (webkitSpeechRecognition) — works in Chrome/Edge
 *      with internet access to Google STT.
 *   2. On "network" / "service-not-allowed" errors, automatically fall back to
 *      MediaRecorder mode: records raw audio and POSTs it to /api/transcribe
 *      where the server uses speech_recognition (SpeechRecognition library)
 *      to transcribe it locally/offline.
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

const cfg = window.AURA_CONFIG || { nodeName: 'Aura Node', port: 5000, peerUrl: 'http://localhost:5001' };

let selectedTarget   = 'both';
let isRecording      = false;
let recognition      = null;
let liveTranscript   = '';
let pollInterval     = null;
let logPollInterval  = null;

// MediaRecorder fallback state
let mediaRecorder    = null;
let audioChunks      = [];
let audioStream      = null;
let useFallbackMic   = false;   // set true after first Web Speech network error

// ─────────────────────────────────────────────────────────────────────────────
// DOM refs
// ─────────────────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

const micBtn            = $('mic-btn');
const micHint           = $('mic-hint');
const transcriptText    = $('transcript-text');
const transcriptHolder  = $('transcript-placeholder');
const broadcastTextarea = $('broadcast-text');
const sendBtn           = $('send-btn');
const sendFeedback      = $('send-feedback');
const peerBadge         = $('peer-badge');
const peerBadgeLabel    = $('peer-badge-label');
const clockEl           = $('clock');
const enginePill        = $('engine-pill');
const logList           = $('log-list');
const clearLogBtn       = $('clear-log-btn');

// Telemetry
const telemUptime = $('telem-uptime');
const telemQueue  = $('telem-queue');
const telemCpu    = $('telem-cpu');
const telemMem    = $('telem-mem');

// Peer details
const pdStatus         = $('pd-status');
const pdName           = $('pd-name');
const pdUrl            = $('pd-url');
const pdLastseen       = $('pd-lastseen');
const pdUptime         = $('pd-uptime');
const peerLatencyBadge = $('peer-latency-badge');

// Settings
const selEngine   = $('sel-engine');
const rngVolume   = $('rng-volume');
const rngSpeed    = $('rng-speed');
const volVal      = $('vol-val');
const spdVal      = $('spd-val');
const chkFallback = $('chk-fallback');
const applyBtn    = $('apply-settings-btn');

// Target buttons
document.querySelectorAll('.target-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.target-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    selectedTarget = btn.dataset.target;
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Clock
// ─────────────────────────────────────────────────────────────────────────────

function updateClock() {
  clockEl.textContent = new Date().toLocaleTimeString('en-US', { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();

// ─────────────────────────────────────────────────────────────────────────────
// Toast Notifications
// ─────────────────────────────────────────────────────────────────────────────

function showToast(message, type = 'info', duration = 3500) {
  const container = $('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'toast-out 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared mic UI helpers
// ─────────────────────────────────────────────────────────────────────────────

function setMicRecordingUI(active, hintText) {
  if (active) {
    micBtn.classList.add('recording');
    micHint.classList.add('recording');
  } else {
    micBtn.classList.remove('recording');
    micHint.classList.remove('recording');
  }
  micHint.textContent = hintText || (active ? 'Listening… speak now' : 'Click to start recording');
}

function clearTranscriptUI() {
  transcriptText.textContent = '';
  transcriptHolder.style.display = '';
  liveTranscript = '';
}

function setTranscript(text) {
  transcriptText.textContent = text;
  transcriptHolder.style.display = 'none';
  broadcastTextarea.value = text;
  liveTranscript = text;
}

// ─────────────────────────────────────────────────────────────────────────────
// METHOD A — Web Speech API (primary, online)
// ─────────────────────────────────────────────────────────────────────────────

function setupWebSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;

  const rec = new SR();
  rec.continuous      = true;
  rec.interimResults  = true;
  rec.lang            = 'en-US';
  rec.maxAlternatives = 1;

  let final = '';

  rec.onstart = () => {
    isRecording = true;
    final = '';
    liveTranscript = '';
    setMicRecordingUI(true, 'Listening via browser… speak now');
    transcriptHolder.style.display = 'none';
    transcriptText.textContent = '';
    showToast('Microphone active — speak now', 'info', 2000);
  };

  rec.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const t = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        final += t + ' ';
      } else {
        interim += t;
      }
    }
    setTranscript((final + interim).trim());
  };

  rec.onerror = (event) => {
    console.warn('[WebSpeech] error:', event.error);

    if (event.error === 'not-allowed') {
      showToast('Microphone access denied. Allow mic in browser settings.', 'error', 6000);
      setMicRecordingUI(false, 'Mic access denied');
      isRecording = false;
      return;
    }

    if (event.error === 'network' || event.error === 'service-not-allowed') {
      // Switch permanently to MediaRecorder fallback
      showToast(
        'Web Speech API network error. Switching to server-side transcription mode.',
        'error', 5000
      );
      useFallbackMic = true;
      isRecording = false;
      setMicRecordingUI(false, 'Click to record (server mode)');
      micHint.style.color = '#f59e0b';
      return;
    }

    if (event.error !== 'no-speech') {
      showToast(`Mic error: ${event.error}`, 'error', 3000);
    }
    isRecording = false;
    setMicRecordingUI(false);
  };

  rec.onend = () => {
    // Do NOT auto-broadcast — just leave the transcript in the textarea
    // so the user can review and click Broadcast themselves.
    isRecording = false;
    const text = liveTranscript.trim();
    if (text) {
      setMicRecordingUI(false, 'Review your message, then click Broadcast ▶');
    } else {
      setMicRecordingUI(false);
    }
  };

  return rec;
}

// ─────────────────────────────────────────────────────────────────────────────
// METHOD B — MediaRecorder → /api/transcribe (fallback, works offline)
// ─────────────────────────────────────────────────────────────────────────────

// State for Web Audio WAV Recorder
let audioCtx      = null;
let scriptNode    = null;
let micSource     = null;
let wavLeftChannel = [];
let wavLength     = 0;
let wavSampleRate = 16000;

async function startFallbackRecording() {
  if (isRecording) {
    stopFallbackRecording();
    return;
  }

  try {
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (err) {
    showToast('Cannot access microphone: ' + err.message, 'error', 5000);
    return;
  }

  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  } catch (e) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }

  wavSampleRate = audioCtx.sampleRate;
  micSource = audioCtx.createMediaStreamSource(audioStream);
  scriptNode = audioCtx.createScriptProcessor(4096, 1, 1);

  wavLeftChannel = [];
  wavLength = 0;

  scriptNode.onaudioprocess = (e) => {
    const inputData = e.inputBuffer.getChannelData(0);
    wavLeftChannel.push(new Float32Array(inputData));
    wavLength += inputData.length;
  };

  micSource.connect(scriptNode);
  scriptNode.connect(audioCtx.destination);

  isRecording = true;
  setMicRecordingUI(true, 'Recording (WAV Mode)… click to stop');
  showToast('Recording audio (WAV Mode)… click mic to stop', 'info', 2500);
}

function stopFallbackRecording() {
  if (!isRecording) return;

  if (scriptNode) {
    scriptNode.disconnect();
    scriptNode.onaudioprocess = null;
  }
  if (micSource) micSource.disconnect();
  if (audioCtx) audioCtx.close();

  if (audioStream) {
    audioStream.getTracks().forEach(t => t.stop());
    audioStream = null;
  }

  isRecording = false;
  setMicRecordingUI(false, 'Transcribing… please wait');

  const wavBlob = encodeWAV(wavLeftChannel, wavLength, wavSampleRate);
  wavLeftChannel = [];
  wavLength = 0;

  uploadWavFile(wavBlob);
}

async function uploadWavFile(blob) {
  try {
    const form = new FormData();
    form.append('audio', blob, 'recording.wav');

    const res  = await fetch('/api/transcribe', { method: 'POST', body: form });
    const data = await res.json();

    if (data.status === 'success' && data.text) {
      setTranscript(data.text);
      // Do NOT auto-broadcast — populate textarea and let user confirm with Broadcast button.
      showToast('Transcribed! Review and click Broadcast ▶', 'info', 3500);
      setMicRecordingUI(false, 'Review your message, then click Broadcast ▶');
    } else {
      const msg = data.message || 'Could not transcribe audio';
      showToast('Transcription failed: ' + msg, 'error', 4000);
      setMicRecordingUI(false, 'Click to record (server mode)');
    }
  } catch (err) {
    showToast('Transcription request failed: ' + err.message, 'error', 4000);
    setMicRecordingUI(false, 'Click to record (server mode)');
  }
}

function encodeWAV(samplesList, totalLen, sampleRate) {
  const buffer = new ArrayBuffer(44 + totalLen * 2);
  const view = new DataView(buffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + totalLen * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, totalLen * 2, true);

  let offset = 44;
  for (let i = 0; i < samplesList.length; i++) {
    const chunk = samplesList[i];
    for (let j = 0; j < chunk.length; j++) {
      let s = Math.max(-1, Math.min(1, chunk[j]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      offset += 2;
    }
  }

  return new Blob([view], { type: 'audio/wav' });
}

function writeString(view, offset, string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Mic button — unified click handler
// ─────────────────────────────────────────────────────────────────────────────

micBtn.addEventListener('click', () => {
  if (useFallbackMic) {
    // MediaRecorder mode
    if (isRecording) {
      stopFallbackRecording();
    } else {
      startFallbackRecording();
    }
    return;
  }

  // Web Speech API mode
  if (!recognition) {
    recognition = setupWebSpeech();
  }

  if (!recognition) {
    // Browser doesn't support Web Speech API at all — go straight to fallback
    useFallbackMic = true;
    startFallbackRecording();
    return;
  }

  if (isRecording) {
    try { recognition.stop(); } catch (_) {}
  } else {
    try {
      recognition.start();
    } catch (e) {
      try { recognition.stop(); } catch (_) {}
      setTimeout(() => {
        try { recognition.start(); } catch (_) {}
      }, 300);
    }
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Send Broadcast
// ─────────────────────────────────────────────────────────────────────────────

async function sendBroadcast(textOverride) {
  const text = (textOverride || broadcastTextarea.value || '').trim();
  if (!text) {
    showToast('Nothing to broadcast — type or speak a message first.', 'error');
    return;
  }

  sendBtn.classList.add('sending');
  sendBtn.disabled = true;
  sendFeedback.textContent = 'Sending...';
  sendFeedback.className   = 'send-feedback';

  try {
    const payload = {
      text,
      target: selectedTarget,
      engine: selEngine.value,
      volume: parseFloat(rngVolume.value),
      speed:  parseInt(rngSpeed.value),
    };

    const res  = await fetch('/api/broadcast', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    const data = await res.json();

    if (data.status === 'success') {
      const localOk = data.results?.local?.status  === 'queued';
      const peerOk  = data.results?.peer?.status   === 'forwarded';

      let msg = '';
      if (selectedTarget === 'both')  msg = (localOk && peerOk) ? 'Broadcast sent to both nodes!' : 'Sent locally, peer may be offline.';
      if (selectedTarget === 'local') msg = localOk ? 'Playing on local speaker.' : 'Local playback failed.';
      if (selectedTarget === 'peer')  msg = peerOk  ? 'Sent to peer node.'        : 'Peer is offline.';

      sendFeedback.textContent = msg;
      sendFeedback.className   = 'send-feedback success';
      showToast(msg, (peerOk || localOk) ? 'success' : 'error');

      broadcastTextarea.value = '';
      clearTranscriptUI();
    } else {
      throw new Error(data.message || 'Unknown error');
    }
  } catch (err) {
    sendFeedback.textContent = 'Error: ' + err.message;
    sendFeedback.className   = 'send-feedback error';
    showToast('Broadcast failed: ' + err.message, 'error');
  } finally {
    sendBtn.classList.remove('sending');
    sendBtn.disabled = false;
    setTimeout(() => {
      sendFeedback.textContent = '';
      sendFeedback.className   = 'send-feedback';
    }, 4000);
  }
}

sendBtn.addEventListener('click', () => sendBroadcast());

broadcastTextarea.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendBroadcast();
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────────────────────────────────────

rngVolume.addEventListener('input', () => {
  volVal.textContent = Math.round(rngVolume.value * 100) + '%';
});

rngSpeed.addEventListener('input', () => {
  spdVal.textContent = rngSpeed.value + ' wpm';
});

applyBtn.addEventListener('click', async () => {
  try {
    const payload = {
      engine:           selEngine.value,
      volume:           parseFloat(rngVolume.value),
      speed:            parseInt(rngSpeed.value),
      offline_fallback: chkFallback.checked,
    };
    const res  = await fetch('/api/settings', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === 'success') {
      enginePill.textContent = data.settings.engine;
      showToast('Settings applied!', 'success');
    }
  } catch (err) {
    showToast('Failed to apply settings.', 'error');
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Transmit/Listen Mode Control
// ─────────────────────────────────────────────────────────────────────────────

function updateModeUI(mode) {
  const intercomSection = $('intercom-section');
  const listenBanner = $('listen-mode-banner');
  const btnTransmit = $('mode-btn-transmitting');
  const btnListen = $('mode-btn-listening');

  if (!intercomSection || !listenBanner || !btnTransmit || !btnListen) return;

  if (mode === 'listening') {
    intercomSection.classList.add('listen-mode-active');
    listenBanner.style.display = 'flex';
    btnListen.classList.add('active');
    btnTransmit.classList.remove('active');
    
    // Stop recording if active when switching
    if (isRecording) {
      if (useFallbackMic) {
        stopFallbackRecording();
      } else if (recognition) {
        try { recognition.stop(); } catch (_) {}
      }
      isRecording = false;
      setMicRecordingUI(false, 'Listen Only Mode');
    } else {
      setMicRecordingUI(false, 'Listen Only Mode');
    }
  } else {
    intercomSection.classList.remove('listen-mode-active');
    listenBanner.style.display = 'none';
    btnTransmit.classList.add('active');
    btnListen.classList.remove('active');
    
    setMicRecordingUI(false, useFallbackMic ? 'Click to record (server mode)' : 'Click to start recording');
  }
}

async function setNodeMode(mode) {
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });
    const data = await res.json();
    if (data.status === 'success') {
      updateModeUI(data.settings.mode);
      showToast(`Switched to ${mode === 'listening' ? 'Listen' : 'Transmit'} Mode`, 'success');
    }
  } catch (err) {
    showToast('Failed to change mode.', 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Status Polling
// ─────────────────────────────────────────────────────────────────────────────

function formatUptime(seconds) {
  if (seconds < 60)   return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

async function pollStatus() {
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();

    telemUptime.textContent = formatUptime(data.uptime || 0);
    telemQueue.textContent  = data.queue_length ?? 0;
    telemCpu.textContent    = data.telemetry?.cpu  != null ? data.telemetry.cpu.toFixed(1)    + '%' : '—';
    telemMem.textContent    = data.telemetry?.memory != null ? data.telemetry.memory.toFixed(1) + '%' : '—';

    if (data.settings?.engine) {
      enginePill.textContent = data.settings.engine;
      selEngine.value        = data.settings.engine;
    }
    if (data.settings?.volume != null) {
      rngVolume.value    = data.settings.volume;
      volVal.textContent = Math.round(data.settings.volume * 100) + '%';
    }
    if (data.settings?.speed != null) {
      rngSpeed.value     = data.settings.speed;
      spdVal.textContent = data.settings.speed + ' wpm';
    }
    if (data.settings?.offline_fallback != null) {
      chkFallback.checked = data.settings.offline_fallback;
    }
    if (data.settings?.mode) {
      updateModeUI(data.settings.mode);
    }

    const peer   = data.peer || {};
    const online = peer.online === true;

    peerBadge.className        = 'peer-badge ' + (online ? 'online' : 'offline');
    peerBadgeLabel.textContent = online
      ? `${peer.name || 'Peer'} · ${peer.latency_ms}ms`
      : 'Peer Offline';

    pdStatus.textContent   = online ? 'Online' : 'Offline';
    pdStatus.style.color   = online ? 'var(--success)' : 'var(--danger)';
    pdName.textContent     = peer.name     || '—';
    pdLastseen.textContent = peer.last_seen || '—';
    pdUptime.textContent   = peer.uptime  != null ? formatUptime(peer.uptime) : '—';
    peerLatencyBadge.textContent = peer.latency_ms != null ? `${peer.latency_ms}ms` : '—';

  } catch (_) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// Activity Log Polling
// ─────────────────────────────────────────────────────────────────────────────

let knownLogIds = new Set();

function statusClass(s) {
  if (!s) return '';
  const m = s.toLowerCase();
  if (m.includes('queue'))   return 'status-queued';
  if (m.includes('speak'))   return 'status-speaking';
  if (m.includes('complet')) return 'status-completed';
  if (m.includes('fail'))    return 'status-failed';
  return 'status-queued';
}

async function pollLogs() {
  try {
    const res  = await fetch('/api/logs');
    const logs = await res.json();
    if (!logs.length) return;

    const hasNew = logs.some(l => !knownLogIds.has(l.id));
    if (!hasNew) {
      logs.forEach(entry => {
        const el = document.querySelector(`[data-log-id="${entry.id}"] .log-entry-status`);
        if (el) {
          el.textContent = entry.status || '';
          el.className   = `log-entry-status ${statusClass(entry.status)}`;
        }
      });
      return;
    }

    knownLogIds   = new Set(logs.map(l => l.id));
    logList.innerHTML = '';
    logs.forEach(entry => {
      const div = document.createElement('div');
      div.className     = 'log-entry';
      div.dataset.logId = entry.id;
      div.innerHTML = `
        <div class="log-entry-header">
          <span class="log-entry-source">${escapeHtml(entry.source || 'Unknown')}</span>
          <span class="log-entry-time">${escapeHtml(entry.timestamp || '')}</span>
        </div>
        <div class="log-entry-text">${escapeHtml(entry.text || '')}</div>
        <span class="log-entry-status ${statusClass(entry.status)}">${escapeHtml(entry.status || '')}</span>
      `;
      logList.appendChild(div);
    });
  } catch (_) {}
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

clearLogBtn.addEventListener('click', async () => {
  try {
    await fetch('/api/logs/clear', { method: 'POST' });
    logList.innerHTML = '<div class="log-empty">No announcements yet.</div>';
    knownLogIds.clear();
    showToast('Log cleared.', 'info');
  } catch (_) {}
});

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────

function init() {
  if (pdUrl) pdUrl.textContent = cfg.peerUrl;

  const btnTransmit = $('mode-btn-transmitting');
  const btnListen = $('mode-btn-listening');
  if (btnTransmit) btnTransmit.addEventListener('click', () => setNodeMode('transmitting'));
  if (btnListen) btnListen.addEventListener('click', () => setNodeMode('listening'));

  pollStatus();
  pollLogs();
  pollInterval    = setInterval(pollStatus, 4000);
  logPollInterval = setInterval(pollLogs,   2500);

  // Try setting up Web Speech API — if not available, pre-select fallback
  recognition = setupWebSpeech();
  if (!recognition) {
    useFallbackMic = true;
    micHint.textContent  = 'Click to record (server mode)';
    micHint.style.color  = '#f59e0b';
    showToast('Browser mic not supported — using server-side recording.', 'info', 4000);
  } else {
    showToast(cfg.nodeName + ' is online!', 'success', 3000);
  }
}

document.addEventListener('DOMContentLoaded', init);
