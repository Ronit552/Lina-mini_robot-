const STATE = {
    mode: 'manual',
    battery: 100,
    connected: true,
    speed: 180,
    servoAngle: 90,
    direction: 'stop',
    distance: 45,
    irLeft: false,
    irRight: false,
    isRecording: false,
    failCount: 0,
    isOffline: false,
    commandHistory: [],
    lastTelemetryTime: 0
};

/* FETCH HELPER */
async function linaFetch(url, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    
    options.signal = controller.signal;
    options.headers = options.headers || {};
    options.headers['X-Requested-With'] = 'XMLHttpRequest';
    
    try {
        const response = await fetch(url, options);
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            handleError(url, response.status);
            return null;
        }
        
        return await response.json();
    } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError') {
            handleTimeout();
        } else {
            handleError(url, err);
        }
        return null;
    }
}

/* OFFLINE DETECTION */
function handleTimeout() {
    STATE.failCount++;
    if (STATE.failCount >= 3 && !STATE.isOffline) {
        setOffline(true);
    }
}

function handleError(url, err) {
    STATE.failCount++;
    if (STATE.failCount >= 3 && !STATE.isOffline) {
        setOffline(true);
    }
}

function setOffline(offline) {
    STATE.isOffline = offline;
    const banner = document.getElementById('offline-banner');
    const allControls = document.getElementById('dashboard-grid');
    
    if (offline) {
        banner.classList.add('visible');
        allControls.classList.add('controls-disabled');
    } else {
        banner.classList.remove('visible');
        allControls.classList.remove('controls-disabled');
        STATE.failCount = 0;
    }
}

/* POLLING LOOPS */
async function pollStatus() {
    const data = await linaFetch('/api/status');
    if (data) {
        if (STATE.isOffline) setOffline(false);
        STATE.failCount = 0;
        
        STATE.connected = data.connected;
        STATE.battery = data.battery;
        STATE.mode = data.mode;
        updateHeader();
    }
}

async function pollTelemetry() {
    const data = await linaFetch('/api/telemetry');
    if (data) {
        if (STATE.isOffline) setOffline(false);
        STATE.failCount = 0;
        
        STATE.lastTelemetryTime = Date.now();
        STATE.distance = data.distance_cm;
        STATE.irLeft = data.ir_left;
        STATE.irRight = data.ir_right;
        STATE.servoAngle = data.servo_angle;
        STATE.direction = data.direction;
        
        updateSensorCard();
        updateSVG();
    }
}

function checkStaleTelemetry() {
    const isStale = (Date.now() - STATE.lastTelemetryTime > 3000);
    const distanceValue = document.getElementById('distance-value');
    const irLeftInd = document.getElementById('ir-left-indicator');
    const irRightInd = document.getElementById('ir-right-indicator');
    
    if (isStale) {
        distanceValue.classList.add('stale-badge');
        irLeftInd.classList.add('stale-badge');
        irRightInd.classList.add('stale-badge');
    } else {
        distanceValue.classList.remove('stale-badge');
        irLeftInd.classList.remove('stale-badge');
        irRightInd.classList.remove('stale-badge');
    }
}

/* UI UPDATE FUNCTIONS */
function flashValue(id, newValue) {
    const el = document.getElementById(id);
    if (!el || el.textContent === String(newValue)) return;
    el.textContent = newValue;
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
}

function updateHeader() {
    flashValue('battery-label', STATE.battery + '%');
    
    const batteryFill = document.getElementById('battery-fill');
    batteryFill.style.width = STATE.battery + '%';
    batteryFill.dataset.level = STATE.battery;
    
    const statusDot = document.querySelector('.status-dot');
    statusDot.dataset.connected = STATE.connected.toString();
    
    const connLabel = document.getElementById('connection-label');
    connLabel.textContent = STATE.connected ? 'CONNECTED' : 'DISCONNECTED';
    
    const modeBadge = document.getElementById('mode-badge');
    modeBadge.dataset.mode = STATE.mode;
    modeBadge.textContent = STATE.mode.toUpperCase();
}

function updateSensorCard() {
    flashValue('distance-value', STATE.distance);
    
    const distanceValue = document.getElementById('distance-value');
    distanceValue.classList.remove('danger', 'caution');
    if (STATE.distance < 20) {
        distanceValue.classList.add('danger');
    } else if (STATE.distance < 40) {
        distanceValue.classList.add('caution');
    }
    
    const irLeft = document.getElementById('ir-left-indicator');
    if (STATE.irLeft) {
        irLeft.classList.add('triggered');
        irLeft.textContent = 'DETECTED';
    } else {
        irLeft.classList.remove('triggered');
        irLeft.textContent = 'CLEAR';
    }
    
    const irRight = document.getElementById('ir-right-indicator');
    if (STATE.irRight) {
        irRight.classList.add('triggered');
        irRight.textContent = 'DETECTED';
    } else {
        irRight.classList.remove('triggered');
        irRight.textContent = 'CLEAR';
    }
}

function updateSVG() {
    const cone = document.getElementById('us-cone');
    if (cone) {
        if (STATE.distance < 20) {
            cone.style.fill = 'var(--red)';
            cone.style.stroke = 'var(--red)';
        } else if (STATE.distance < 40) {
            cone.style.fill = '#f59e0b';
            cone.style.stroke = '#f59e0b';
        } else {
            cone.style.fill = 'var(--green)';
            cone.style.stroke = 'var(--green)';
        }
    }
    
    if (STATE.distance < 15) {
        const svg = document.getElementById('robot-svg');
        if (svg) {
            svg.classList.remove('flash-obstacle');
            void svg.offsetWidth;
            svg.classList.add('flash-obstacle');
        }
    }
    
    const irLeftDot = document.getElementById('ir-left-dot');
    if (irLeftDot) irLeftDot.style.fill = STATE.irLeft ? 'var(--red)' : 'var(--text-dim)';
    
    const irRightDot = document.getElementById('ir-right-dot');
    if (irRightDot) irRightDot.style.fill = STATE.irRight ? 'var(--red)' : 'var(--text-dim)';
    
    const headDir = document.getElementById('head-direction');
    if (headDir) {
        headDir.setAttribute('transform', `rotate(${STATE.servoAngle - 90}, 150, 80)`);
    }
    
    const robotSvg = document.getElementById('robot-svg');
    if (robotSvg) {
        if (STATE.direction !== 'stop') {
            robotSvg.classList.add('wheels-spinning');
        } else {
            robotSvg.classList.remove('wheels-spinning');
        }
    }
    
    const arrow = document.getElementById('direction-arrow');
    if (arrow) {
        arrow.style.display = STATE.direction === 'stop' ? 'none' : 'block';
        let arrowAngle = 0;
        if (STATE.direction === 'forward') arrowAngle = 0;
        else if (STATE.direction === 'back') arrowAngle = 180;
        else if (STATE.direction === 'left') arrowAngle = -90;
        else if (STATE.direction === 'right') arrowAngle = 90;
        arrow.setAttribute('transform', `rotate(${arrowAngle}, 150, 190)`);
    }
}

/* MODE CONTROLS */
async function setMode(mode) {
    const result = await linaFetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
    });
    
    if (result) {
        STATE.mode = mode;
        updateHeader();
        updateModeButtons();
        updateDpadVisibility();
        addCommandHistory('mode', mode, null);
    } else {
        shakeElement('mode-controls');
    }
}

function updateModeButtons() {
    const buttons = document.querySelectorAll('.mode-btn');
    buttons.forEach(btn => {
        btn.classList.remove('active');
        const m = btn.dataset.mode || btn.id.replace('btn-', '');
        if (m === STATE.mode) {
            btn.classList.add('active');
        }
    });
}

function updateDpadVisibility() {
    const dpad = document.getElementById('dpad-section');
    if (!dpad) return;
    dpad.style.display = STATE.mode === 'manual' ? 'block' : 'none';
    
    if (window.innerWidth < 768) {
        if (STATE.mode === 'manual') document.body.classList.add('mode-manual');
        else document.body.classList.remove('mode-manual');
    }
}

/* D-PAD CONTROLS */
let moveInterval = null;

function startMove(direction) {
    if (STATE.mode !== 'manual') return;
    sendMove(direction);
    clearInterval(moveInterval);
    moveInterval = setInterval(() => sendMove(direction), 200);
}

function stopMove() {
    if (moveInterval !== null) {
        clearInterval(moveInterval);
        moveInterval = null;
    }
    sendMove('stop');
}

async function sendMove(direction) {
    const result = await linaFetch('/api/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction, speed: STATE.speed })
    });
    
    if (result) {
        if (STATE.direction !== direction) {
            STATE.direction = direction;
            addCommandHistory('move', direction, STATE.speed);
        } else {
            STATE.direction = direction;
        }
    } else {
        shakeElement('btn-' + direction);
    }
}

/* SPEED SLIDER */
function setupSpeedSlider() {
    const slider = document.getElementById('speed-slider');
    if (!slider) return;
    slider.addEventListener('input', () => {
        STATE.speed = parseInt(slider.value);
        const valEl = document.getElementById('speed-value');
        if (valEl) valEl.textContent = STATE.speed;
    });
}

/* SERVO ARC CONTROL */
let servoDebounce = null;

function setupServoControl() {
    const slider = document.getElementById('servo-slider');
    if (!slider) return;
    slider.addEventListener('input', () => {
        STATE.servoAngle = parseInt(slider.value);
        updateServoArc(STATE.servoAngle);
        clearTimeout(servoDebounce);
        servoDebounce = setTimeout(() => sendServo(STATE.servoAngle), 100);
    });
}

function updateServoArc(angle) {
    const label = document.getElementById('servo-angle-label');
    if (label) label.textContent = angle + '°';
    
    const track = document.getElementById('servo-track');
    if (track) {
        const radius = 90;
        const totalLength = Math.PI * radius;
        const activePortion = (angle / 180) * totalLength;
        track.style.strokeDasharray = totalLength;
        track.style.strokeDashoffset = totalLength - activePortion;
    }
    
    const dot = document.getElementById('servo-dot');
    if (dot) {
        const radius = 90;
        const angleRad = angle * (Math.PI / 180);
        const cx = 100 + radius * Math.cos(Math.PI - angleRad);
        const cy = 100 - radius * Math.sin(Math.PI - angleRad);
        dot.setAttribute('cx', cx);
        dot.setAttribute('cy', cy);
    }
}

async function sendServo(angle) {
    const result = await linaFetch('/api/servo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ angle })
    });
    if (result) {
        addCommandHistory('servo', 'angle', angle);
    } else {
        shakeElement('servo-section');
    }
}

/* VOICE RECORDING */
let mediaRecorder = null;
let audioChunks = [];
let micStream = null;

function setupVoice() {
    const voiceBtn = document.getElementById('voice-btn');
    if (!voiceBtn) return;
    voiceBtn.addEventListener('mousedown', startRecording);
    voiceBtn.addEventListener('mouseup', stopRecording);
    voiceBtn.addEventListener('mouseleave', stopRecording);
    voiceBtn.addEventListener('touchstart', e => { 
        e.preventDefault(); 
        startRecording(); 
    }, { passive: false });
    voiceBtn.addEventListener('touchend', stopRecording);
}

async function startRecording() {
    if (STATE.isRecording) return;
    try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(micStream);
        audioChunks = [];
        
        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = sendVoiceAudio;
        
        mediaRecorder.start();
        const voiceBtn = document.getElementById('voice-btn');
        voiceBtn.dataset.state = 'recording';
        STATE.isRecording = true;
        
        const transcript = document.getElementById('voice-transcript');
        transcript.textContent = 'Listening...';
        transcript.style.color = 'var(--text-muted)';
        
        startWaveform(micStream);
    } catch (err) {
        const transcript = document.getElementById('voice-transcript');
        if (transcript) {
            transcript.textContent = 'Microphone access required';
            transcript.style.color = 'var(--red)';
        }
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        if (micStream) {
            micStream.getTracks().forEach(track => track.stop());
        }
        const voiceBtn = document.getElementById('voice-btn');
        voiceBtn.dataset.state = 'processing';
        stopWaveform();
        
        const transcript = document.getElementById('voice-transcript');
        transcript.textContent = 'Processing...';
    }
}

async function sendVoiceAudio() {
    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');
    
    const result = await linaFetch('/api/voice', {
        method: 'POST',
        body: formData
    });
    
    const voiceBtn = document.getElementById('voice-btn');
    if (voiceBtn) voiceBtn.dataset.state = 'idle';
    STATE.isRecording = false;
    
    const transcript = document.getElementById('voice-transcript');
    if (result) {
        if (transcript) {
            transcript.textContent = '"' + result.transcript + '"';
            transcript.style.color = '';
        }
        typewriterText('tts-text', result.response_text);
        addCommandHistory('voice', result.transcript, null);
        animateSpeaker();
    } else {
        shakeElement('voice-btn');
        if (transcript) {
            transcript.textContent = 'Voice processing failed';
            transcript.style.color = 'var(--red)';
        }
    }
}

/* AUDIO WAVEFORM (Canvas) */
let audioContext = null;
let analyser = null;
let waveformFrame = null;

function startWaveform(stream) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    
    audioContext = new AudioContextClass();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 128;
    source.connect(analyser);
    drawWaveform();
}

function stopWaveform() {
    cancelAnimationFrame(waveformFrame);
    if (audioContext && audioContext.state !== 'closed') {
        audioContext.close();
    }
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawFlatLine();
}

function drawWaveform() {
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    
    function draw() {
        waveformFrame = requestAnimationFrame(draw);
        analyser.getByteFrequencyData(dataArray);
        ctx.clearRect(0, 0, W, H);
        
        const barWidth = (W / bufferLength) * 2.5;
        let x = 0;
        
        for (let i = 0; i < bufferLength; i++) {
            const barHeight = (dataArray[i] / 255) * H;
            ctx.fillStyle = '#e8820c';
            ctx.fillRect(x, H - barHeight, barWidth - 1, barHeight);
            x += barWidth + 1;
        }
    }
    draw();
}

function drawFlatLine() {
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.strokeStyle = '#2a2e33';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, canvas.height / 2);
    ctx.lineTo(canvas.width, canvas.height / 2);
    ctx.stroke();
}

/* TTS TYPEWRITER */
function typewriterText(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = '';
    let i = 0;
    
    function tick() {
        if (i < text.length) {
            el.textContent += text[i];
            i++;
            setTimeout(tick, 30);
        }
    }
    tick();
}

function animateSpeaker() {
    const icon = document.getElementById('speaker-icon');
    if (!icon) return;
    icon.classList.add('speaking');
    setTimeout(() => icon.classList.remove('speaking'), 3000);
}

/* SSE LOG STREAM */
function initLogStream() {
    const es = new EventSource('/api/log/stream');
    
    es.onmessage = function(e) {
        try {
            const data = JSON.parse(e.data);
            appendLogLine(data.timestamp, data.source, data.message);
        } catch(err) {}
    };
    
    es.onerror = function() {
        appendLogLine(now(), 'SERVER', 'Log stream disconnected');
    };
}

function appendLogLine(timestamp, source, message) {
    const log = document.getElementById('serial-log');
    if (!log) return;
    const line = document.createElement('span');
    line.className = 'log-line';
    
    const sourceClass = 'log-source-' + source.toLowerCase();
    line.innerHTML = `[${timestamp}] <span class="${sourceClass}">[${source}]</span> ${escapeHtml(message)}`;
    
    log.appendChild(line);
    
    while (log.children.length > 200) {
        log.removeChild(log.firstChild);
    }
    log.scrollTop = log.scrollHeight;
}

/* COMMAND HISTORY */
function addCommandHistory(type, action, value) {
    STATE.commandHistory.unshift({ type, action, value, time: Date.now() });
    if (STATE.commandHistory.length > 10) {
        STATE.commandHistory.pop();
    }
    renderCommandHistory();
}

function renderCommandHistory() {
    const ul = document.getElementById('command-history');
    if (!ul) return;
    ul.innerHTML = '';
    
    STATE.commandHistory.forEach(cmd => {
        const li = document.createElement('li');
        li.className = 'history-item';
        li.dataset.type = cmd.type;
        
        const elapsed = Math.round((Date.now() - cmd.time) / 1000);
        const label = cmd.value !== null ? `${cmd.action} @ ${cmd.value}` : cmd.action;
        
        li.innerHTML = `
            <span class="history-action">${escapeHtml(label)}</span>
            <span class="history-time">${elapsed}s ago</span>
        `;
        ul.appendChild(li);
    });
}

function updateHistoryTimestamps() {
    renderCommandHistory();
}

/* UTILITY FUNCTIONS */
function shakeElement(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('shake');
    void el.offsetWidth; // Trigger reflow
    el.classList.add('shake');
    setTimeout(() => el.classList.remove('shake'), 400);
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function now() {
    return new Date().toTimeString().slice(0, 8);
}

/* INITIALIZATION */
document.addEventListener('DOMContentLoaded', () => {
    // Read initial state
    const modeBadge = document.getElementById('mode-badge');
    if (modeBadge && modeBadge.dataset.mode) {
        STATE.mode = modeBadge.dataset.mode;
    }
    
    const batteryFill = document.getElementById('battery-fill');
    if (batteryFill && batteryFill.dataset.level) {
        STATE.battery = parseInt(batteryFill.dataset.level);
    }
    
    const servoSlider = document.getElementById('servo-slider');
    if (servoSlider) {
        STATE.servoAngle = parseInt(servoSlider.value);
    }
    
    const speedSlider = document.getElementById('speed-slider');
    if (speedSlider) {
        STATE.speed = parseInt(speedSlider.value);
    }
    
    updateServoArc(STATE.servoAngle);
    updateDpadVisibility();
    drawFlatLine();
    
    // Mode Buttons
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const md = btn.dataset.mode || btn.id.replace('btn-', '');
            setMode(md);
        });
    });
    
    // DPAD
    const dirs = ['forward', 'back', 'left', 'right'];
    dirs.forEach(dir => {
        const btn = document.getElementById('btn-' + dir);
        if (btn) {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                startMove(dir);
            });
        }
    });

    const stopBtn = document.getElementById('btn-stop');
    if (stopBtn) {
        stopBtn.addEventListener('click', (e) => {
            e.preventDefault();
            stopMove();
        });
    }
    
    setupSpeedSlider();
    setupServoControl();
    setupVoice();
    
    pollStatus();
    setInterval(pollStatus, 2000);
    
    pollTelemetry();
    setInterval(pollTelemetry, 500);
    
    setInterval(checkStaleTelemetry, 1000);
    
    initLogStream();
    
    setInterval(updateHistoryTimestamps, 5000);
});
