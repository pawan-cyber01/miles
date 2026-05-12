/* MILES app.js - Voice + qwen2.5/Groq AI + CMD + Web Scan */

// ── State ───────────────────────────────────────────
let cameraActive = false, scanActive = false, detectActive = false, voiceActive = false;
let stream = null, detectTimer = null, startTime = Date.now();
let recognition = null, ttsVoice = null, waveFrame = 0;
let currentMode = 'full';
let appSettings = { ai_provider: 'groq', groq_model: 'llama-3.3-70b-versatile', ollama_model: 'qwen2.5:latest', voice_rate: 104, voice_pitch: 6, voice_lang: 'auto' };
let activeAudio = null;
let lastProcessedCommand = { text: '', time: 0 };
const DPR = Math.max(1, Math.min(2, window.devicePixelRatio || 1));

const API_BASE = window.location.port && window.location.port !== '7420' ? 'http://127.0.0.1:7420' : '';

// ── Conversation / Friend Mode ───────────────────────
let convModeActive = false;
let convModeController = null; // AbortController to stop the loop

// ── App map ─────────────────────────────────────────
const APPS = {
  chrome: 'chrome.exe', 'google chrome': 'chrome.exe', firefox: 'firefox.exe',
  edge: 'msedge.exe', notepad: 'notepad.exe', calculator: 'calc.exe',
  explorer: 'explorer.exe', 'task manager': 'taskmgr.exe', spotify: 'Spotify.exe',
  discord: 'Discord.exe', steam: 'steam.exe', vscode: 'Code.exe',
  paint: 'mspaint.exe', word: 'WINWORD.EXE', excel: 'EXCEL.EXE', vlc: 'vlc.exe'
};

const OPEN_CMDS = {
  chrome: 'start chrome', firefox: 'start firefox',
  notepad: 'start notepad', calculator: 'start calc', 'task manager': 'start taskmgr',
  explorer: 'start explorer', paint: 'start mspaint', cmd: 'start cmd'
};

// ── Boot ────────────────────────────────────────────
const BOOTS = ['Loading STARK OS kernel...', 'Initializing neural core...',
  'Calibrating arc reactor...', 'Syncing satellite uplink...',
  'Facial recognition DB loaded...', 'AES-256 encryption active...',
  'Threat matrix initialized...', 'FRIDAY backup engaged...',
  'All systems nominal. Miles online.'];

window.addEventListener('DOMContentLoaded', () => {
  let i = 0;
  const p = document.getElementById('boot-progress'), s = document.getElementById('boot-status');
  const iv = setInterval(() => {
    p.style.width = ((i + 1) / BOOTS.length * 100) + '%'; s.textContent = BOOTS[i]; i++;
    if (i >= BOOTS.length) { clearInterval(iv); setTimeout(launchHUD, 600); }
  }, 350);
});

function launchHUD() {
  document.getElementById('boot-screen').classList.add('fade-out');
  setTimeout(() => {
    document.getElementById('boot-screen').style.display = 'none';
    document.getElementById('hud').classList.remove('hidden'); initHUD();
    setTimeout(() => speak('Miles online. All systems nominal. How can I assist you?'), 500);
  }, 800);
}

// Fire-and-forget: update CSS immediately, resize window in background
function applyMode(mode) {
  currentMode = mode;
  document.body.classList.toggle('mode-hide', mode === 'hide');
  document.body.classList.toggle('mode-top', mode === 'top');
  document.body.classList.toggle('mode-full', mode === 'full');
  
  // Update Spider Emblem size based on mode
  const emblem = document.querySelector('.spider-emblem');
  if (emblem) {
    if (mode === 'hide') {
      emblem.style.width = '140px';
      emblem.style.height = '140px';
    } else {
      emblem.style.width = '112px';
      emblem.style.height = '112px';
    }
  }

  // Non-blocking: tell Python to resize the native window
  fetch('/api/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode })
  }).catch(e => addTL('[HUD] Mode bridge offline: ' + e.message, 'err'));
}

async function setHudMode(mode) {
  applyMode(mode);
}

// ── Mini top-bar chat (mode-top) ─────────────────────
let _topReplyTimer = null;
async function sendTopChat() {
  const inp = document.getElementById('top-chat-input');
  const replyEl = document.getElementById('top-chat-reply');
  if (!inp || !replyEl) return;
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';

  // Show "thinking" state
  replyEl.textContent = '[ MILES ]: Processing…';
  replyEl.style.display = 'block';
  replyEl.style.color = 'var(--cyan)';
  if (_topReplyTimer) clearTimeout(_topReplyTimer);

  // Mirror to full chat log (so context is kept)
  addChatMsg('user', text);

  try {
    const reply = await askAI(text) || getBuiltIn(text) || 'No response.';
    replyEl.textContent = '[ MILES ]: ' + reply;
    addChatMsg('miles', reply);
    speak(reply);
  } catch (e) {
    replyEl.textContent = '[ ERROR ]: ' + e.message;
    replyEl.style.color = 'var(--red)';
  }

  // Auto-hide after 12 s
  _topReplyTimer = setTimeout(() => {
    replyEl.style.display = 'none';
  }, 12000);
}

// ── Init ────────────────────────────────────────────
function initHUD() {
  updateClock(); setInterval(updateClock, 1000); setInterval(updateUptime, 1000);
  loadSettings(); initBattery(); initSystemStats(); initAIStatus(); initVoiceStatus(); initWhatsappStatus(); startTerminal(); startRadar(); startNetGraph(); startHR(); startFPS(); initCustomCursor();
  loadQuickFiles();
  setInterval(() => {
    document.getElementById('arc-output').textContent = appSettings.ai_provider === 'groq' ? 'GROQ' : 'LOCAL';
    document.getElementById('arc-stab').textContent = (99.5 + Math.random() * .4).toFixed(1) + '%';
    document.getElementById('arc-temp').textContent = (36.5 + Math.random() * 2).toFixed(1) + '°C';
  }, 2200);
  // ── Enter key to send ──
  const chatIn = document.getElementById('chat-input');
  if (chatIn) {
    chatIn.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendChat();
      }
    });
  }
  ['voice-rate', 'voice-pitch'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateVoiceLabels);
  });
  addChatMsg('miles', 'MILES online. Hinglish supported: ask questions, run commands, open apps, scan sites, or use tools for system tasks.');
}

function initCustomCursor() {
  const cursor = document.getElementById('custom-cursor');
  if (!cursor) return;

  document.addEventListener('mousemove', (e) => {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';
    if (!cursor.classList.contains('visible')) cursor.classList.add('visible');
  });

  document.addEventListener('mouseenter', () => cursor.classList.add('visible'));
  document.addEventListener('mouseleave', () => cursor.classList.remove('visible'));

  // Detect interactive elements for cursor state
  const interactiveSelector = 'button, a, .clickable-logo, .win-btn, .hud-btn, .tool-tab, .quick-btn, .arc-reactor, input, select';
  
  document.addEventListener('mouseover', (e) => {
    if (e.target.closest(interactiveSelector)) {
      cursor.classList.add('active');
    }
  });

  document.addEventListener('mouseout', (e) => {
    if (e.target.closest(interactiveSelector)) {
      cursor.classList.remove('active');
    }
  });

  // Click animation
  document.addEventListener('mousedown', () => {
    cursor.style.transform = 'translate(-50%, -50%) scale(0.85)';
  });
  document.addEventListener('mouseup', () => {
    cursor.style.transform = 'translate(-50%, -50%) scale(1)';
  });
}

async function initAIStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/ai/status`);
    const d = await res.json();
    const ok = d.online;
    addTL(ok ? `[AI] ${d.provider} online: ${d.model}` : `[AI] ${d.provider} not ready: ${d.status || d.model}`, ok ? 'ok' : 'warn');
    const badge = Array.from(document.querySelectorAll('.badge')).find(x => x.textContent.includes('A.I.'));
    if (badge) {
      badge.classList.toggle('active', ok);
      badge.title = ok ? `${d.provider}: ${d.model}` : 'Set Groq key or start Ollama';
    }
  } catch (e) {
    addTL('[AI] Status unavailable: ' + e.message, 'err');
  }
}

async function initVoiceStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/voice/status`);
    const d = await res.json();
    addTL(`[VOICE] ${d.engine || 'mic'} ${d.ready ? 'ready' : 'starting'}`, d.ready ? 'ok' : 'warn');
  } catch (e) {
    addTL('[VOICE] Status unavailable: ' + e.message, 'warn');
  }
}

async function initWhatsappStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/whatsapp/bridge`);
    const d = await res.json();
    addTL(`[WA] Bridge ${d.running ? d.status || 'running' : d.status || 'offline'}`, d.running ? 'ok' : 'warn');
  } catch (e) {
    addTL('[WA] Status unavailable: ' + e.message, 'warn');
  }
}

// ── Clock/Uptime ─────────────────────────────────────
function updateClock() {
  const n = new Date();
  document.getElementById('clock').textContent = n.toLocaleTimeString('en-US', { hour12: false });
  document.getElementById('date-display').textContent = n.toLocaleDateString('en-US', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' }).toUpperCase();
}
function updateUptime() {
  const s = Math.floor((Date.now() - startTime) / 1000);
  document.getElementById('uptime').textContent = `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

function fitCanvas(canvas, fallbackW = 220, fallbackH = 60) {
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const cssW = Math.max(1, Math.floor(rect.width || canvas.clientWidth || fallbackW));
  const cssH = Math.max(1, Math.floor(rect.height || canvas.clientHeight || fallbackH));
  const pxW = Math.floor(cssW * DPR);
  const pxH = Math.floor(cssH * DPR);
  if (canvas.width !== pxW || canvas.height !== pxH) {
    canvas.width = pxW;
    canvas.height = pxH;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  return { ctx, w: cssW, h: cssH };
}

// ── Battery/Stats ────────────────────────────────────
async function initBattery() {
  try {
    const b = await navigator.getBattery();
    const u = () => {
      const p = Math.round(b.level * 100); document.getElementById('battery-pct').textContent = p + '%';
      document.getElementById('battery-bar').style.width = p + '%';
      document.getElementById('battery-charging').classList.toggle('hidden', !b.charging);
    };
    u(); b.addEventListener('levelchange', u); b.addEventListener('chargingchange', u);
  } catch { }
}
function initSystemStats() {
  document.getElementById('mem-stat').textContent = (navigator.deviceMemory || '?') + ' GB';
  document.getElementById('cpu-cores').textContent = navigator.hardwareConcurrency || '?';
  const c = navigator.connection; document.getElementById('net-type').textContent = c ? (c.effectiveType || '--').toUpperCase() : '--';
}

// ── Terminal ──────────────────────────────────────────
const TERMS = [{ t: 'sys', m: '[NET] Firewall active' }, { t: 'ok', m: '[CRYPTO] AES-256 active' },
{ t: 'sys', m: '[SAT] Uplink stable 12ms' }, { t: 'ok', m: '[GEO] GPS lock acquired' },
{ t: 'warn', m: '[WARN] Anomalous packet — monitoring' }, { t: 'ok', m: '[AI] Behavioral model updated' },
{ t: 'sys', m: '[SCAN] Perimeter clear' }, { t: 'ok', m: '[HUD] 60fps locked' }];
let ti = 0;
function startTerminal() { addTL(); setInterval(addTL, 5000); }
function addTL(m, t) {
  const term = document.getElementById('terminal');
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
  const e = m ? { m, t: t || 'sys' } : TERMS[ti++ % TERMS.length];
  const d = document.createElement('div'); d.className = 'term-line ' + e.t; d.textContent = `${ts} ${e.m}`;
  term.appendChild(d); if (term.children.length > 35) term.removeChild(term.firstChild); term.scrollTop = term.scrollHeight;
}

// ── Radar ─────────────────────────────────────────────
let ra = 0, rb = [];
function startRadar() { for (let i = 0; i < 5; i++)rb.push({ a: Math.random() * Math.PI * 2, d: 20 + Math.random() * 55, l: Math.random() }); drawRadar(); }
function drawRadar() {
  const cv = document.getElementById('radar-canvas');
  if (!cv) return;
  const ctx = cv.getContext('2d'), w = cv.width, h = cv.height, cx = w / 2, cy = h / 2, r = Math.min(cx, cy) - 4;
  ctx.clearRect(0, 0, w, h); ctx.fillStyle = '#000'; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = 'rgba(57,255,20,.2)'; ctx.lineWidth = 1;[r * .35, r * .65, r].forEach(rr => { ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2); ctx.stroke(); });
  ctx.beginPath(); ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy); ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r); ctx.stroke();
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(ra); const sw = ctx.createLinearGradient(0, 0, r, 0);
  sw.addColorStop(0, 'rgba(57,255,20,.55)'); sw.addColorStop(1, 'rgba(57,255,20,0)');
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, r, -.35, 0); ctx.closePath(); ctx.fillStyle = sw; ctx.fill(); ctx.restore();
  rb.forEach(b => {
    const bx = cx + Math.cos(b.a) * b.d, by = cy + Math.sin(b.a) * b.d;
    let df = Math.abs(b.a - ra) % (Math.PI * 2); if (df < .1) b.l = 1; b.l = Math.max(0, b.l - .006);
    ctx.beginPath(); ctx.arc(bx, by, 2.5, 0, Math.PI * 2); ctx.fillStyle = `rgba(57,255,20,${b.l})`; ctx.shadowColor = '#39ff14'; ctx.shadowBlur = 6; ctx.fill(); ctx.shadowBlur = 0;
    if (b.l <= 0) { b.a = Math.random() * Math.PI * 2; b.d = 20 + Math.random() * 55; b.l = .8; }
  });
  ra = (ra + .022) % (Math.PI * 2); requestAnimationFrame(drawRadar);
}

// ── Net Graph ─────────────────────────────────────────
let nh = Array.from({ length: 64 }, (_, i) => 38 + Math.sin(i / 4) * 18 + Math.random() * 8);
let netFrame = null, netPhase = 0;
function startNetGraph() {
  if (netFrame) cancelAnimationFrame(netFrame);
  const animate = () => { netPhase += 0.012; drawNG(); netFrame = requestAnimationFrame(animate); };
  animate();
  setInterval(() => {
    const last = nh[nh.length - 1] || 40;
    const next = Math.max(8, Math.min(96, last + (Math.random() * 34 - 17)));
    nh.push(next); if (nh.length > 64) nh.shift();
    const nc = document.getElementById('nodes-count');
    if (nc) nc.textContent = (2847 + Math.floor(Math.random() * 20 - 10)).toLocaleString();
  }, 600);
  window.addEventListener('resize', drawNG);
}
function drawNG() {
  const cv = document.getElementById('network-canvas'), fitted = fitCanvas(cv, 220, 72);
  if (!fitted) return;
  const { ctx, w, h } = fitted;
  ctx.clearRect(0, 0, w, h);
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, 'rgba(0,245,255,.12)');
  bg.addColorStop(1, 'rgba(0,0,0,.72)');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = 'rgba(0,245,255,.12)'; ctx.lineWidth = 1;
  for (let x = 0; x < w; x += 24) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  for (let y = 0; y < h; y += 18) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  const sweepX = ((netPhase * 90) % (w + 80)) - 40;
  const sweep = ctx.createLinearGradient(sweepX - 30, 0, sweepX + 30, 0);
  sweep.addColorStop(0, 'rgba(0,245,255,0)');
  sweep.addColorStop(.5, 'rgba(0,245,255,.20)');
  sweep.addColorStop(1, 'rgba(0,245,255,0)');
  ctx.fillStyle = sweep; ctx.fillRect(sweepX - 30, 0, 60, h);
  ctx.beginPath();
  nh.forEach((v, i) => { const x = (i / (nh.length - 1)) * w, y = h - (v / 100) * h; i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
  ctx.strokeStyle = '#00f5ff'; ctx.lineWidth = 2; ctx.shadowColor = '#00f5ff'; ctx.shadowBlur = 8; ctx.stroke(); ctx.shadowBlur = 0;
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath(); ctx.fillStyle = 'rgba(0,229,255,.05)'; ctx.fill();
}

// ── Heart Rate ────────────────────────────────────────
let hh = Array.from({ length: 64 }, (_, i) => 18 + Math.sin(i / 3) * 5), htk = 0;
let hrFrame = null, hrPhase = 0;
function startHR() {
  if (hrFrame) cancelAnimationFrame(hrFrame);
  const animate = () => { hrPhase += 0.035; drawHR(); hrFrame = requestAnimationFrame(animate); };
  animate();
  setInterval(() => {
    const hr = 68 + Math.floor(Math.random() * 12);
    document.getElementById('hr-val').textContent = hr;
    const b = [18, 18, 20, 8, 40, 4, 28, 18, 18][htk++ % 9] * (hr / 75); hh.push(b); if (hh.length > 64) hh.shift();
  }, 700);
  window.addEventListener('resize', drawHR);
}
function drawHR() {
  const cv = document.getElementById('heartrate-canvas'), fitted = fitCanvas(cv, 220, 54);
  if (!fitted) return;
  const { ctx, w, h } = fitted;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(0,0,0,.72)'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = 'rgba(255,45,85,.12)'; ctx.lineWidth = 1;
  for (let x = 0; x < w; x += 22) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  const pulse = (Math.sin(hrPhase) + 1) / 2;
  ctx.fillStyle = `rgba(255,45,85,${0.03 + pulse * 0.06})`;
  ctx.fillRect(0, 0, w, h);
  ctx.beginPath();
  hh.forEach((v, i) => { const x = (i / (hh.length - 1)) * w, y = h - (v / 44) * h; i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
  ctx.strokeStyle = '#ff3366'; ctx.lineWidth = 2; ctx.shadowColor = '#ff3366'; ctx.shadowBlur = 8; ctx.stroke(); ctx.shadowBlur = 0;
}

// ── FPS ───────────────────────────────────────────────
let ff = 0, fl = Date.now();
function startFPS() { const t = () => { ff++; const n = Date.now(); if (n - fl >= 1000) { document.getElementById('frame-counter').textContent = 'FPS: ' + ff; ff = 0; fl = n; } requestAnimationFrame(t); }; requestAnimationFrame(t); }

// ── Voice (Edge TTS Realistic) ────────────────────────
function speak(text) {
  if (!text) return;
  const payload = {
    text: String(text).slice(0, 900),
    rate: appSettings.voice_rate,
    pitch: appSettings.voice_pitch,
    lang: appSettings.voice_lang || 'auto'
  };
  const fallbackSpeak = () => {
    if (!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(String(text).slice(0, 700));
    const raw = String(text).toLowerCase();
    const hindiish = /[\u0900-\u097f]/.test(raw) || /\b(kya|hai|hain|nahi|main|mujhe|aap|tum|kaise|karo|batao|suno|chahiye|theek|haan|kyun|abhi)\b/.test(raw);
    utterance.lang = (appSettings.voice_lang === 'hi' || (appSettings.voice_lang === 'auto' && hindiish)) ? 'hi-IN' : 'en-IN';
    utterance.rate = Math.max(0.7, Math.min(1.7, appSettings.voice_rate / 100));
    utterance.pitch = Math.max(0.2, Math.min(2, 1 + (appSettings.voice_pitch / 50)));
    window.speechSynthesis.speak(utterance);
  };

  if (activeAudio) {
    activeAudio.pause();
    activeAudio.src = '';
    activeAudio = null;
  }
  fetch(`${API_BASE}/api/say`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(res => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  }).catch(e => {
    console.warn('System speaker failed:', e);
    fallbackSpeak();
  });
}

// ── Ollama AI ─────────────────────────────────────────
async function askAI(question) {
  // Screen Scan NLP
  const tl = question.toLowerCase();
  if (/scan.*screen|what.*on.*screen|look.*at.*screen|what is this|scan.*page/.test(tl)) {
    addChatMsg('miles', 'Scanning your screen...');
    speak(appSettings.voice_lang === 'hi' ? 'Main screen scan kar raha hoon.' : 'Scanning your screen.');
    try {
      const res = await fetch(`${API_BASE}/api/scan-screen`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question })
      });
      const data = await res.json();
      if (data.error) {
        addChatMsg('miles', 'Error scanning screen: ' + data.error);
        speak('I encountered an error scanning the screen.');
      } else {
        addChatMsg('miles', data.text);
        speak(data.text);
      }
    } catch (e) {
      addChatMsg('miles', 'Screen scan failed: ' + e.message);
      speak('Screen scan failed.');
    }
    return;
  }

  try {
    addChatMsg('sys', 'Transmitting to ' + (appSettings.ai_provider === 'groq' ? 'Groq Neural Net' : 'Ollama Core') + '...');
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question })
    });
    const d = await res.json().catch(() => ({ error: `Backend returned HTTP ${res.status}` }));
    if (!res.ok) return "ERROR: " + (d.error || `HTTP ${res.status}`);
    if (d.error) return "ERROR: " + d.error;
    return d.text || null;
  } catch (e) {
    return "ERROR: Failed to connect to AI backend. " + e.message;
  }
}

async function writeNotepad(content, title = 'edith_note') {
  try {
    const res = await fetch(`${API_BASE}/api/notepad`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, title })
    });
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    addChatMsg('miles', d.response || 'Opened Notepad.');
    speak('Opened in Notepad.');
  } catch (e) {
    addChatMsg('miles', 'Notepad failed: ' + e.message);
    speak('Notepad action failed.');
  }
}

async function loadSettings() {
  try {
    const res = await fetch(`${API_BASE}/api/settings`);
    const d = await res.json();
    appSettings = { ...appSettings, ...d };
    const keyEl = document.getElementById('groq-api-key');
    const modelEl = document.getElementById('groq-model');
    const ollamaModelEl = document.getElementById('ollama-model');
    const providerEl = document.getElementById('ai-provider');
    const rateEl = document.getElementById('voice-rate');
    const pitchEl = document.getElementById('voice-pitch');
    const langEl = document.getElementById('voice-lang');
    if (keyEl) keyEl.placeholder = d.groq_configured ? 'Saved - enter new key to replace' : 'gsk_...';
    if (modelEl) modelEl.value = appSettings.groq_model || '';
    if (ollamaModelEl) ollamaModelEl.value = appSettings.ollama_model || 'qwen2.5:latest';
    if (providerEl) providerEl.value = appSettings.ai_provider || 'groq';
    if (langEl) langEl.value = appSettings.voice_lang || 'auto';
    if (rateEl) rateEl.value = appSettings.voice_rate || 100;
    if (pitchEl) pitchEl.value = appSettings.voice_pitch || 0;
    updateVoiceLabels();
  } catch (e) {
    addTL('[SETTINGS] Load failed: ' + e.message, 'warn');
  }
}

function updateVoiceLabels() {
  const rate = document.getElementById('voice-rate')?.value || appSettings.voice_rate || 100;
  const pitch = document.getElementById('voice-pitch')?.value || appSettings.voice_pitch || 0;
  const rateLabel = document.getElementById('voice-rate-label');
  const pitchLabel = document.getElementById('voice-pitch-label');
  if (rateLabel) rateLabel.textContent = rate + '%';
  if (pitchLabel) pitchLabel.textContent = `${Number(pitch) >= 0 ? '+' : ''}${pitch}Hz`;
}

async function saveSettings() {
  const payload = {
    groq_api_key: document.getElementById('groq-api-key')?.value.trim() || undefined,
    groq_model: document.getElementById('groq-model')?.value.trim() || appSettings.groq_model,
    ollama_model: document.getElementById('ollama-model')?.value.trim() || appSettings.ollama_model,
    ai_provider: document.getElementById('ai-provider')?.value || 'groq',
    voice_lang: document.getElementById('voice-lang')?.value || 'auto',
    voice_rate: Number(document.getElementById('voice-rate')?.value || 100),
    voice_pitch: Number(document.getElementById('voice-pitch')?.value || 0)
  };
  try {
    const res = await fetch(`${API_BASE}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    appSettings = { ...appSettings, ...d };
    const keyEl = document.getElementById('groq-api-key');
    if (keyEl) keyEl.value = '';
    updateVoiceLabels();
    initAIStatus();
    addChatMsg('miles', 'Settings saved. AI provider updated, voice tuning active.');
    speak('Settings saved.');
  } catch (e) {
    addChatMsg('miles', 'Settings save failed: ' + e.message);
  }
}

function showToolTab(tab) {
  document.querySelectorAll('.tool-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
  document.querySelectorAll('.tool-pane').forEach(pane => pane.classList.toggle('active', pane.id === 'tool-' + tab));
  if (tab === 'files') loadQuickFiles();
}

function formatFileSize(bytes = 0) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function openQuickPath(path) {
  try {
    const res = await fetch(`${API_BASE}/api/files/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path })
    });
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    addTL('[FILES] ' + (d.response || 'Opened'), 'ok');
  } catch (e) {
    addTL('[FILES] ' + e.message, 'err');
    addChatMsg('miles', 'File open failed: ' + e.message);
  }
}

async function loadQuickFiles() {
  const folders = document.getElementById('quick-folder-list');
  const files = document.getElementById('quick-file-list');
  if (!folders || !files) return;
  files.textContent = 'Loading quick files...';
  try {
    const res = await fetch(`${API_BASE}/api/files/quick`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    folders.innerHTML = '';
    (data.shortcuts || []).forEach(item => {
      const btn = document.createElement('button');
      btn.className = 'quick-btn';
      btn.textContent = item.name;
      btn.title = item.path;
      btn.onclick = () => openQuickPath(item.path);
      folders.appendChild(btn);
    });
    files.innerHTML = '';
    if (!(data.files || []).length) {
      files.textContent = 'No recent quick files found.';
      return;
    }
    data.files.forEach(item => {
      const row = document.createElement('button');
      row.className = 'file-row';
      row.title = item.path;
      const date = new Date(item.modified * 1000).toLocaleDateString();
      const name = document.createElement('span');
      name.className = 'file-name';
      name.textContent = item.name;
      const meta = document.createElement('span');
      meta.className = 'file-meta';
      meta.textContent = `${formatFileSize(item.size)} // ${date}`;
      row.appendChild(name);
      row.appendChild(meta);
      row.onclick = () => openQuickPath(item.path);
      files.appendChild(row);
    });
  } catch (e) {
    files.textContent = 'Quick files unavailable: ' + e.message;
  }
}

function setBrowserQuery(text) {
  const q = document.getElementById('browser-query');
  if (q) q.value = text;
}

async function askMilesBrowser() {
  const url = document.getElementById('browser-url')?.value.trim() || '';
  const query = document.getElementById('browser-query')?.value.trim() || '';
  const output = document.getElementById('browser-result');
  if (!output) return;
  if (!url && !query) {
    output.textContent = 'Enter a website or ask Miles Browser a question.';
    return;
  }
  output.textContent = 'Miles Browser is thinking...';
  try {
    const res = await fetch(`${API_BASE}/api/browser`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, query })
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    output.textContent = `${data.source || 'Miles Browser'}\n\n${data.answer || 'No answer.'}`;
    addChatMsg('miles', data.answer || 'Miles Browser finished.');
    speak((data.answer || '').substring(0, 260));
  } catch (e) {
    output.textContent = 'Browser error: ' + e.message;
  }
}

function addMiniTerminal(text, kind = 'sys') {
  const box = document.getElementById('command-terminal');
  if (!box) return;
  const line = document.createElement('div');
  line.className = 'term-line ' + kind;
  line.textContent = text;
  box.appendChild(line);
  if (box.children.length > 28) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

async function runTerminalInput() {
  const input = document.getElementById('terminal-input');
  const cmd = input?.value.trim();
  if (!cmd) return;
  input.value = '';
  addMiniTerminal('> ' + cmd, 'warn');
  try {
    const res = await fetch(`${API_BASE}/api/cmd`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: cmd }) });
    const d = await res.json();
    addMiniTerminal(d.error ? 'ERROR: ' + d.error : (d.output || 'Done.'), d.error ? 'err' : 'ok');
  } catch (e) {
    addMiniTerminal('ERROR: ' + e.message, 'err');
  }
}

async function runCyberTask(task, directTarget = '') {
  const targetEl = document.getElementById('cyber-target');
  const output = document.getElementById('cyber-output');
  const target = (directTarget || targetEl?.value || '').trim();
  if (!target) {
    if (output) output.textContent = 'Target required. Example: example.com';
    return;
  }
  showToolTab('cyber');
  if (output) output.textContent = `Running ${task} on ${target}...\nAuthorized targets only.`;
  try {
    const res = await fetch(`${API_BASE}/api/cyber`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, target })
    });
    const d = await res.json();
    if (output) output.textContent = d.error ? 'ERROR: ' + d.error : d.output;
    addTL(`[CYBER] ${task} ${target}`, d.error ? 'err' : 'ok');
  } catch (e) {
    if (output) output.textContent = 'ERROR: ' + e.message;
  }
}

async function toggleWhatsappBridge(action = 'start') {
  try {
    const res = await fetch('/api/whatsapp/bridge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    });
    const d = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    if (d.error) throw new Error(d.error);
    addTL('[WA] ' + (d.response || 'Bridge updated'), d.running ? 'ok' : 'warn');
    addChatMsg('miles', d.response || 'WhatsApp bridge updated.');
    speak(d.running ? 'WhatsApp bridge online.' : 'WhatsApp bridge stopped.');
  } catch (e) {
    addChatMsg('miles', 'WhatsApp bridge failed: ' + e.message);
    speak('WhatsApp bridge failed.');
  }
}

// ── Natural Language → CMD ────────────────────────────
function resolveNatural(text) {
  const t = text.toLowerCase().trim();
  const kill = t.match(/^(?:close|kill|stop|end|quit|terminate|shut down)\s+(.+)/);
  if (kill) { const a = kill[1].trim().toLowerCase(); const exe = APPS[a] || (a.endsWith('.exe') ? a : a + '.exe'); return { cmd: `taskkill /IM "${exe}" /F`, desc: `Closing ${kill[1]}` }; }
  const open = t.match(/^(?:open|launch|start)\s+(.+)/);
  if (open) { const a = open[1].trim().toLowerCase(); const cmd = OPEN_CMDS[a] || `start ${APPS[a] || a}`; return { cmd, desc: `Opening ${open[1]}` }; }

  const playSpot = t.match(/^(?:play)\s+(.+?)\s+(?:on\s+spotify)/);
  if (playSpot) return { cmd: `start spotify:search:${playSpot[1].trim()}`, desc: `Playing ${playSpot[1]} on Spotify` };

  const playYT = t.match(/^(?:play|search)\s+(.+?)\s+(?:on\s+youtube)/);
  if (playYT) return { cmd: `start https://www.youtube.com/results?search_query=${encodeURIComponent(playYT[1].trim())}`, desc: `Searching ${playYT[1]} on YouTube` };

  if (/my ip|ip address|what.*my ip/.test(t)) return { cmd: 'ipconfig', desc: 'Fetching IP address' };
  if (/disk space|storage|free space/.test(t)) return { cmd: 'wmic logicaldisk get caption,freespace,size', desc: 'Checking disk space' };
  if (/running|processes|task list/.test(t)) return { cmd: 'tasklist', desc: 'Listing running processes' };
  if (/wifi|network info/.test(t)) return { cmd: 'ipconfig /all', desc: 'Network information' };
  if (/screenshot/.test(t)) return { cmd: 'snippingtool', desc: 'Opening Snipping Tool' };
  if (/clear terminal|clear log/.test(t)) { document.getElementById('terminal').innerHTML = ''; return null; }
  return null;
}

// ── CMD Execution ─────────────────────────────────────
async function runCmd(cmd, desc) {
  addChatMsg('miles', `${desc || 'Executing'}: ${cmd}`);
  speak(desc || 'Running command on your system.');
  addTL('[CMD] ' + cmd, 'warn');
  try {
    const res = await fetch('/api/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: cmd }) });
    const d = await res.json();
    if (d.error) { addChatMsg('miles', 'Error: ' + d.error); speak('Command failed.'); }
    else { addChatMsg('cmd-out', d.output || 'Done.'); speak('Command completed.'); }
  } catch { addChatMsg('miles', 'Backend offline. Restart miles.py'); speak('Backend is offline.'); }
}

// ── Chat ──────────────────────────────────────────────
function addChatMsg(role, text) {
  const box = document.getElementById('chat-messages');
  const d = document.createElement('div'); d.className = 'chat-msg ' + role;
  const lbl = document.createElement('div'); lbl.className = 'msg-label';
  lbl.textContent = role === 'user' ? '▶ YOU' : role === 'miles' ? '◆ MILES' : role === 'cmd-out' ? '⬡ TERMINAL' : '● SYSTEM';
  const body = document.createElement('div'); body.textContent = text;
  d.appendChild(lbl); d.appendChild(body); box.appendChild(d); box.scrollTop = box.scrollHeight;
}
function clearChat() {
  const box = document.getElementById('chat-messages');
  if (box) box.innerHTML = '';
  addChatMsg('miles', 'Chat cleared.');
}

async function sendChat() {
  const inp = document.getElementById('chat-input'); const text = inp.value.trim(); if (!text) return;
  inp.value = ''; addChatMsg('user', text); await processCommand(text);
}

// ── Main Command Router ───────────────────────────────
async function processCommand(text) {
  let t = text.toLowerCase().trim();
  const now = Date.now();
  if (t && t === lastProcessedCommand.text && now - lastProcessedCommand.time < 1800) {
    addTL('[VOICE] Duplicate command ignored: ' + t, 'warn');
    return;
  }
  lastProcessedCommand = { text: t, time: now };

  // Text-based Wake Up
  const wakeMatch = t.match(/^(?:hey\s+|ok\s+|okay\s+)?(?:miles|myles|mile|mines|jarvis)\s*(.*)/i);
  if (wakeMatch) {
    if (!wakeMatch[1]) {
      addChatMsg('miles', 'Yes?');
      speak('Yes?');
      toggleVoice();
      return;
    }
    t = wakeMatch[1].trim();
    text = text.substring(text.length - t.length);
  }

  // Local Ollama backend handles AI requests.

  // HUD controls
  if (/open camera|camera on|start camera/.test(t)) {
    if (!cameraActive) await toggleCamera();
    const r = 'Camera initialized.'; addChatMsg('miles', r); speak(r); return;
  }
  if (/close camera|camera off/.test(t)) { if (cameraActive) toggleCamera(); const r = 'Camera off.'; addChatMsg('miles', r); speak(r); return; }
  if (/activate scan|start scan|scan mode/.test(t)) { if (!scanActive) toggleScan(); const r = 'Scan mode active.'; addChatMsg('miles', r); speak(r); return; }
  if (/stop scan|disable scan/.test(t)) { if (scanActive) toggleScan(); const r = 'Scan deactivated.'; addChatMsg('miles', r); speak(r); return; }
  if (/detect|face detect/.test(t)) { if (!detectActive) toggleDetect(); const r = 'Face detection active.'; addChatMsg('miles', r); speak(r); return; }
  if (/^(?:\/clear|clear chats?|clear conversation|reset chats?|delete chats?)$/.test(t)) { clearChat(); speak('Chat cleared.'); return; }

  // Window Modes
  if (/set on top|slim|navbar|top mode/.test(t)) { await setHudMode('top'); speak('Slim navbar active.'); return; }
  if (/hide|minimize|arc mode|reactor mode/.test(t)) { await setHudMode('hide'); speak('Arc reactor standby.'); return; }
  if (/full screen|maximize|restore|full mode/.test(t)) { await setHudMode('full'); speak('Full interface restored.'); return; }

  // WhatsApp bridge mode
  if (/connect whatsapp|start whatsapp|whatsapp mode|open whatsapp bridge/.test(t)) { await toggleWhatsappBridge('start'); return; }
  if (/disconnect whatsapp|stop whatsapp|close whatsapp bridge/.test(t)) { await toggleWhatsappBridge('stop'); return; }

  // Write code or text to Notepad
  const codeM = text.match(/^(?:write|create|generate|make)\s+(?:code|a script|script|program)\s+(?:for|to|that)?\s*(.+?)\s+(?:in|on|to)\s+notepad/i);
  if (codeM) {
    addChatMsg('miles', 'Generating code for Notepad...');
    const code = await askAI(`Write complete, runnable code for: ${codeM[1]}. Return only the code with minimal comments.`);
    if (code.startsWith("ERROR:")) { addChatMsg('miles', code); return; }
    await writeNotepad(code || `// Could not generate code for: ${codeM[1]}`, 'miles_code');
    return;
  }

  const writeM = text.match(/^(?:write|type|draft)\s+(.+?)\s+(?:in|on|to)\s+notepad/i);
  if (writeM) {
    await writeNotepad(writeM[1], 'miles_note');
    return;
  }

  // App Exit
  if (/^(bye|goodbye|be|exit|close|shut down|sleep)$/.test(t)) {
    addChatMsg('miles', 'Shutting down systems. Goodbye.');
    speak('Shutting down systems. Goodbye.');
    setTimeout(() => fetch('/api/exit', { method: 'POST' }), 1500);
    return;
  }

  // Status
  if (/^status$|system status/.test(t)) {
    const bat = document.getElementById('battery-pct').textContent;
    const up = document.getElementById('uptime').textContent;
    const r = `All systems nominal. Battery ${bat}. Uptime ${up}. Arc reactor stable at 3.2 gigawatts. Neural network online.`;
    addChatMsg('miles', r); speak(r); return;
  }

  // Explicit run command
  const runM = t.match(/^(?:run|execute|terminal|cmd)\s+(.+)/i);
  if (runM) { await runCmd(runM[1].trim(), 'Executing'); return; }

  const cyberM = t.match(/^cyber\s+(dns|ping|trace|headers)\s+(.+)/i);
  if (cyberM) { await runCyberTask(cyberM[1].toLowerCase(), cyberM[2].trim()); return; }

  const browserM = text.match(/^(?:miles\s+browser|browser|browse)\s+(.+)/i);
  if (browserM) {
    showToolTab('browser');
    const q = document.getElementById('browser-query');
    if (q) q.value = browserM[1].trim();
    await askMilesBrowser();
    return;
  }

  if (/^(?:show|open)\s+(?:quick\s+)?files$|^files$/.test(t)) {
    showToolTab('files');
    await loadQuickFiles();
    speak('Quick files ready.');
    return;
  }

  // Scan URL
  const urlM = t.match(/^(?:scan|analyze|inspect)\s+(https?:\/\/\S+|\S+\.\S+)/i);
  if (urlM) {
    let url = urlM[1]; if (!url.startsWith('http')) url = 'https://' + url;
    addChatMsg('miles', 'Scanning ' + url + '...'); speak('Scanning ' + url.split('/')[2]); addTL('[SCAN] ' + url, 'sys');
    try {
      const res = await fetch('/api/scan-url?url=' + encodeURIComponent(url)); const d = await res.json();
      if (d.error) { addChatMsg('miles', 'Scan failed: ' + d.error); speak('Scan failed.'); }
      else { addChatMsg('miles', `Scan complete. HTTP ${d.status}.\n${d.content.substring(0, 350)}...`); speak(`Scan complete. Status ${d.status}.`); addTL('[SCAN] OK ' + d.status, 'ok'); }
    } catch { addChatMsg('miles', 'Backend offline.'); speak('Backend offline.'); } return;
  }

  // Web search → Ollama
  const srchM = t.match(/^(?:search|find|look up|google|what is|who is|tell me|explain)\s+(.+)/i);
  if (srchM) {
    const q = srchM[1]; addChatMsg('miles', 'Searching: ' + q); speak('Searching.');
    const g = await askAI(q) || getBuiltIn(q);
    if (typeof g === 'string' && g.startsWith("ERROR:")) { addChatMsg('miles', g); return; }
    addChatMsg('miles', g); speak(g.substring(0, 300)); return;
  }

  // Natural language system command
  const nat = resolveNatural(t);
  if (nat) { await runCmd(nat.cmd, nat.desc); return; }

  // Jarvis Hardware & System Commands
  const jarvisRegex = /^(?:translate|ip address|find my location|where am i|show nearby|take a photo|take a picture|battery|set volume|set brightness|shutdown|restart|sleep|lock|wikipedia|play music|stop music|next song|previous song|mute|unmute|take a screenshot|recent screenshot|recent photo|recent picture|check network|check internet|speed|turn on bluetooth|turn off bluetooth|flip a coin|roll a dice)/i;
  if (jarvisRegex.test(t)) {
    addChatMsg('miles', 'Executing system command...');
    try {
      const res = await fetch(`${API_BASE}/api/jarvis_cmd`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: t })
      });
      const data = await res.json();
      if (data.error) { addChatMsg('miles', 'Error: ' + data.error); speak('I encountered an error.'); return; }
      if (data.response) { addChatMsg('miles', data.response); speak(data.response); return; }
      if (data.unhandled) { } // fall through
    } catch (e) {
      addChatMsg('miles', 'Failed to execute system command.');
      return;
    }
  }

  // Everything else → AI Backend → built-in
  const aiR = await askAI(text);
  if (aiR && !aiR.startsWith("ERROR:")) {
    addChatMsg('miles', aiR); speak(aiR.substring(0, 300)); return;
  }
  // If it's a configuration error, don't fall back to built-ins, show the error
  if (aiR && (aiR.includes("API key") || aiR.includes("Ollama"))) {
    addChatMsg('miles', aiR); return;
  }
  const bi = getBuiltIn(t); addChatMsg('miles', bi); speak(bi.substring(0, 280));
}

// ── Built-in Knowledge ────────────────────────────────
function getBuiltIn(q) {
  q = q.toLowerCase();
  if (/your name|who are you|what are you/.test(q)) return "I am MILES — Multipurpose Intelligent Linked Engine System. Pawan's personal AI, now serving you.";
  if (/hello|hi\b|hey|good morning|good evening/.test(q)) return 'Hello. MILES online and fully operational. Kya chahiye aapko?';
  if (/stark|tony|pawan/.test(q)) return 'Pawan built systems like me as part of his legacy. I am exclusively his AI.';
  if (/iron man|suit|armor/.test(q)) return 'The Iron Man armor is a powered exoskeleton by Stark Industries. The Mark 85 is the most advanced ever built.';
  if (/avengers/.test(q)) return "Earth's mightiest heroes. I have full dossiers on all members.";
  if (/time|clock/.test(q)) return 'Current time: ' + new Date().toLocaleTimeString('en-US', { hour12: false }) + '. Date: ' + new Date().toDateString();
  if (/battery|power/.test(q)) return 'Battery at ' + document.getElementById('battery-pct').textContent + '. Arc reactor output stable.';
  if (/thank/.test(q)) return "You're welcome. That's what I'm here for.";
  if (/help|what can you do/.test(q)) return 'Main questions answer kar sakta hoon (qwen2.5 local / Groq online), commands run kar sakta hoon, apps open/close kar sakta hoon, websites scan kar sakta hoon. Try: "run ipconfig", "close chrome", "scan github.com".';
  if (/cpu|processor/.test(q)) return `Your system has ${navigator.hardwareConcurrency || 'unknown'} logical cores.`;
  if (/ram|memory/.test(q)) return `Device memory: approximately ${navigator.deviceMemory || 'unknown'} GB.`;
  if (/ollama|qwen|model/.test(q)) return 'Primary AI: qwen2.5 via Ollama local. Backup: Groq online (llama-3.3-70b). Start Ollama with "ollama serve".';
  return "Samajh nahi aaya. Try: 'run [command]', 'close [app]', 'search [topic]', or 'scan [website]'.";
}

// ── Camera ────────────────────────────────────────────
async function toggleCamera() {
  const btn = document.getElementById('btn-cam'), nm = document.getElementById('no-cam-msg'), f = document.getElementById('camera-feed');
  if (cameraActive) {
    stream?.getTracks().forEach(t => t.stop()); f.srcObject = null; cameraActive = false;
    nm.style.display = 'flex'; btn.classList.remove('active'); document.getElementById('sdo-mode').textContent = 'MODE: STANDBY'; return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    f.srcObject = stream; cameraActive = true; nm.style.display = 'none'; btn.classList.add('active');
    document.getElementById('sdo-mode').textContent = 'MODE: LIVE FEED'; addTL('[CAM] Feed online', 'sys');
  }
  catch (e) { addChatMsg('miles', 'Camera denied: ' + e.message); }
}

function toggleScan() {
  scanActive = !scanActive;
  const l = document.getElementById('scan-line'), b = document.getElementById('btn-scan');
  l.style.display = scanActive ? 'block' : 'none'; b.classList.toggle('active', scanActive);
  document.getElementById('sdo-status').textContent = scanActive ? 'STATUS: SCANNING' : 'STATUS: IDLE';
}

function toggleDetect() {
  detectActive = !detectActive;
  document.getElementById('btn-detect').classList.toggle('active', detectActive);
  if (detectActive) runDetect();
  else {
    clearTimeout(detectTimer); document.getElementById('scan-canvas').getContext('2d').clearRect(0, 0, 9999, 9999);
    document.getElementById('sdo-faces').textContent = 'FACES: 0';
  }
}

function runDetect() {
  if (!detectActive) return;
  const cv = document.getElementById('scan-canvas'), f = document.getElementById('camera-feed');
  cv.width = f.offsetWidth || 400; cv.height = f.offsetHeight || 280; const ctx = cv.getContext('2d'); ctx.clearRect(0, 0, cv.width, cv.height);
  if (cameraActive && Math.random() > .15) {
    const n = Math.random() > .7 ? 2 : 1;
    for (let i = 0; i < n; i++) {
      const isTarget = (i === 0);
      const x = isTarget ? (cv.width / 2 - 60) : 50 + (Math.random() * 40), y = isTarget ? (cv.height / 2 - 70) : 30 + (Math.random() * 20), w = 120, h = 150;
      const color = isTarget ? '#00e5ff' : '#39ff14';
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.shadowColor = color; ctx.shadowBlur = 10; ctx.strokeRect(x, y, w, h); ctx.shadowBlur = 0;

      ctx.fillStyle = color; ctx.font = '12px "Share Tech Mono"';
      ctx.fillText(isTarget ? 'IDENTITY: MILES MASTER' : 'ID: UNKNOWN' + i, x, y - 18);
      if (isTarget) ctx.fillText('ACCESS: OMEGA (ADMIN)', x, y - 5);

      ctx.fillText('CONF: ' + (isTarget ? '99.9' : (86 + Math.random() * 12).toFixed(1)) + '%', x, y + h + 15);

      ctx.strokeStyle = '#ffd700'; ctx.lineWidth = 1.5;
      [[x, y, 12, 12], [x + w, y, -12, 12], [x, y + h, 12, -12], [x + w, y + h, -12, -12]].forEach(([cx, cy, dx, dy]) => {
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + dx, cy); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy + dy); ctx.stroke();
      });

      if (isTarget) {
        // Draw spinning 3D wireframe head
        const cx = x + w / 2, cy = y + h / 2, r = 45;
        ctx.strokeStyle = 'rgba(0, 229, 255, 0.6)'; ctx.lineWidth = 1;
        const t = Date.now() / 800;
        for (let j = 0; j < 5; j++) {
          ctx.beginPath();
          ctx.ellipse(cx, cy, Math.max(0.1, r * Math.abs(Math.cos(t + j * Math.PI / 5))), r, 0, 0, 2 * Math.PI);
          ctx.stroke();
        }
        for (let j = 0; j < 3; j++) {
          ctx.beginPath();
          ctx.ellipse(cx, cy, r, Math.max(0.1, r * Math.abs(Math.sin(t + j * Math.PI / 3))), 0, 0, 2 * Math.PI);
          ctx.stroke();
        }
      }
    }
    document.getElementById('sdo-faces').textContent = 'FACES: ' + n;
    document.getElementById('sdo-status').textContent = 'STATUS: BIOMETRIC LOCK';
  }
  else document.getElementById('sdo-faces').textContent = 'FACES: 0';
  detectTimer = setTimeout(runDetect, 50); // Fast refresh for smooth animation
}

// ── Voice Input (Python Local Backend) ─────────────────────────
async function toggleVoice() {
  if (voiceActive) return;
  voiceActive = true;

  const micEls = [
    document.querySelector('.pawan-logo'),
    document.getElementById('mic-btn'),
    document.getElementById('top-mic-btn'),
    document.getElementById('voice-badge')
  ].filter(Boolean);

  const ar = document.querySelector('.arc-reactor');

  // 1. Apply visual state FIRST
  micEls.forEach(el => { el.classList.add('active'); });
  if (ar) ar.classList.add('arc-listening');
  addTL('[VOICE] Microphone armed', 'sys');

  // 2. Wait 2 frames so browser repaints the red radar BEFORE the fetch blocks
  await new Promise(r => setTimeout(r, 80));

  let timeout = null;
  try {
    const controller = new AbortController();
    timeout = setTimeout(() => controller.abort(), 20000);
    const res = await fetch('/api/voice', { method: 'POST', signal: controller.signal });
    clearTimeout(timeout);
    timeout = null;
    const data = await res.json();
    if (data.error) {
      addChatMsg('miles', '⚠ Voice: ' + data.error);
    } else if (data.text && data.text.trim()) {
      addChatMsg('system', '🎙 Heard: ' + data.text);
      document.getElementById('chat-input').value = data.text;
      sendChat();
    } else {
      addChatMsg('miles', "I didn't catch that. Please try again.");
    }
  } catch (e) {
    if (e.name === 'AbortError' || e.name === 'TimeoutError') {
      addChatMsg('miles', 'Voice timeout — try again.');
    } else {
      addChatMsg('miles', '⚠ Could not reach backend: ' + e.message);
    }
  } finally {
    if (timeout) clearTimeout(timeout);
    voiceActive = false;
    micEls.forEach(el => { el.classList.remove('active'); });
    if (ar) ar.classList.remove('arc-listening');
  }
}

function handleArcClick() {
  if (document.body.classList.contains('mode-hide')) {
    applyMode('full');
    speak('Restoring interface.');
  } else {
    toggleVoice();
  }
}

// ── Conversation / Friend Mode ────────────────────────
function setConvOverlay(state, text) {
  const el = document.getElementById('conv-mode-overlay');
  if (!el) return;
  el.className = 'conv-mode-overlay';
  if (state === 'hidden') { el.classList.add('hidden'); return; }
  if (state === 'thinking') el.classList.add('conv-thinking');
  if (state === 'speaking') el.classList.add('conv-speaking');
  const span = el.querySelector('span');
  if (span) span.textContent = text || 'FRIEND MODE ACTIVE — LISTENING...';
  el.classList.remove('hidden');
}

async function toggleConversationMode() {
  const btn = document.getElementById('conv-mode-btn');
  const icon = document.getElementById('conv-btn-icon');
  if (convModeActive) {
    // Stop
    convModeActive = false;
    if (convModeController) { convModeController.abort(); convModeController = null; }
    btn && btn.classList.remove('conv-active');
    if (icon) icon.textContent = '🎙';
    setConvOverlay('hidden');
    addChatMsg('miles', 'Friend mode deactivated. Wake word required again.');
    speak('Friend mode off.');
    return;
  }
  // Start
  convModeActive = true;
  btn && btn.classList.add('conv-active');
  if (icon) icon.textContent = '⏹';
  addChatMsg('miles', 'Friend mode active! Bol kuch bhi — main sun raha hoon. Baat karte rehte hain.');
  speak('Friend mode active. I am listening.');
  setConvOverlay('listening', 'FRIEND MODE — LISTENING...');
  convModeController = new AbortController();
  conversationLoop(convModeController.signal);
}

async function conversationLoop(signal) {
  const ar = document.querySelector('.arc-reactor');
  while (convModeActive && !signal.aborted) {
    // Visual: listening state
    setConvOverlay('listening', 'FRIEND MODE — LISTENING...');
    if (ar) ar.classList.add('arc-listening');
    try {
      const res = await fetch('/api/voice/continuous', {
        method: 'POST',
        signal: AbortSignal.timeout ? AbortSignal.timeout(18000) : signal
      });
      if (signal.aborted || !convModeActive) break;
      const data = await res.json();
      if (ar) ar.classList.remove('arc-listening');

      if (data.busy) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      if (data.error) {
        if (data.error === 'timeout') { continue; } // silence, keep looping
        addTL('[CONV] ' + data.error, 'warn');
        await new Promise(r => setTimeout(r, 1000));
        continue;
      }
      const text = (data.text || '').trim();
      if (!text) continue;

      // Show heard text
      addChatMsg('user', '🎙 ' + text);
      addTL('[CONV] Heard: ' + text, 'sys');

      // Thinking state
      setConvOverlay('thinking', 'MILES — THINKING...');

      // Process the command / AI
      await processCommandConv(text);

      // Brief pause after speaking before listening again
      setConvOverlay('speaking', 'MILES — SPEAKING...');
      await new Promise(r => setTimeout(r, 1800));

    } catch (e) {
      if (ar) ar.classList.remove('arc-listening');
      if (signal.aborted || !convModeActive) break;
      // network hiccup — retry after short delay
      await new Promise(r => setTimeout(r, 1200));
    }
  }
  // Cleanup
  if (ar) ar.classList.remove('arc-listening');
  setConvOverlay('hidden');
}

// Thin wrapper: process command without repeating the voice toggle path
async function processCommandConv(text) {
  await processCommand(text);
}
