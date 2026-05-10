// out.js - 出口摄像头逻辑（签退 + 暂离）

const video = document.getElementById('video');
const overlayCanvas = document.getElementById('overlayCanvas');
const overlayCtx = overlayCanvas.getContext('2d');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const recordBtn = document.getElementById('recordBtn');
const resultContent = document.getElementById('resultContent');
const fpsElement = document.getElementById('fps');

let ws = null, stream = null, animationId = null;
let frameCount = 0, lastFpsUpdate = 0, isRunning = false;
let mediaRecorder = null, recordedChunks = [], isRecording = false;
let currentFaces = [];
let checkouttimes = {};

const CHECKOUT_THRESHOLD = 30;

const WS_URL = 'ws://' + window.location.host + '/api/v1/ws/camera-exit';


function resizeCanvas() {
    if (video.videoWidth && video.videoHeight) {
        overlayCanvas.width = video.videoWidth;
        overlayCanvas.height = video.videoHeight;
    }
}
video.addEventListener('loadedmetadata', resizeCanvas);

function drawFaces(faces, videoWidth) {
    overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    if (!faces || faces.length === 0) return;
    currentFaces = faces;
    faces.forEach(face => {
        let [x1, y1, x2, y2] = face.bbox;
        const mirroredX1 = videoWidth - x2;
        const mirroredX2 = videoWidth - x1;
        const width = mirroredX2 - mirroredX1;
        const height = y2 - y1;
        const isKnown = face.identity !== 'Unknown';
        const boxColor = isKnown ? '#4caf50' : '#ff5722';
        const displayName = isKnown ? face.identity : '陌生人';
        overlayCtx.strokeStyle = boxColor;
        overlayCtx.lineWidth = 3;
        overlayCtx.strokeRect(mirroredX1, y1, width, height);
        overlayCtx.font = 'bold 14px Arial';
        const textWidth = overlayCtx.measureText(displayName).width;
        overlayCtx.fillStyle = boxColor;
        overlayCtx.fillRect(mirroredX1, y1 - 22, textWidth + 10, 22);
        overlayCtx.fillStyle = '#000';
        overlayCtx.fillText(displayName, mirroredX1 + 5, y1 - 5);
    });
}

async function startCamera() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480, frameRate: 15 }
        });
        video.srcObject = stream;
        await video.play();
        resizeCanvas();
        return true;
    } catch (err) {
        resultContent.innerHTML = `<div class="empty-state"><p>摄像头错误: ${err.message}</p></div>`;
        return false;
    }
}

function stopCamera() {
    if (stream) {
        stream.getTracks().forEach(track => track.stop());
        stream = null;
    }
    video.srcObject = null;
}

const captureCanvas = document.createElement('canvas');
const captureCtx = captureCanvas.getContext('2d');

async function captureAndSend() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !video.videoWidth) return;
    const width = video.videoWidth, height = video.videoHeight;
    captureCanvas.width = width; captureCanvas.height = height;
    captureCtx.drawImage(video, 0, 0, width, height);
    const imageData = captureCtx.getImageData(0, 0, width, height);
    const flippedCanvas = document.createElement('canvas');
    flippedCanvas.width = width; flippedCanvas.height = height;
    const flippedCtx = flippedCanvas.getContext('2d');
    flippedCtx.translate(width, 0);
    flippedCtx.scale(-1, 1);
    flippedCtx.putImageData(imageData, 0, 0);
    flippedCanvas.toBlob(async (blob) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(await blob.arrayBuffer());
        }
    }, 'image/jpeg', 0.7);
    frameCount++;
    const now = performance.now();
    if (now - lastFpsUpdate >= 1000) {
        fpsElement.textContent = `FPS: ${(frameCount * 1000 / (now - lastFpsUpdate)).toFixed(1)}`;
        frameCount = 0; lastFpsUpdate = now;
    }
}

function startCaptureLoop() {
    if (animationId) clearInterval(animationId);
    frameCount = 0; lastFpsUpdate = performance.now();
    animationId = setInterval(() => {
        if (isRunning && ws?.readyState === WebSocket.OPEN) captureAndSend();
    }, 100);
}

function stopCaptureLoop() {
    if (animationId) { clearInterval(animationId); animationId = null; }
}

async function updateResults(data) {
    if (data.success) {
        if (data.faces && overlayCanvas.width > 0) {
            drawFaces(data.faces, overlayCanvas.width);
        } else {
            overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
        }
        if (data.face_count === 0) {
            resultContent.innerHTML = '<div class="empty-state"><p>未检测到人脸</p></div>';
            return;
        }
        let html = '';
        data.faces.forEach(async (face, i) => {
            const isKnown = face.identity !== 'Unknown';
            const cardClass = isKnown ? 'face-card known' : 'face-card unknown';
            html += `
                <div class="${cardClass}">
                    <h3>人脸 ${i + 1}</h3>
                    <p>身份: <strong>${face.identity}</strong> ${face.identity_confidence > 0 ? `(${(face.identity_confidence * 100).toFixed(0)}%)` : ''}</p>
                    <p>性别: <strong>${face.gender}</strong></p>
                    <p>年龄: <strong>${face.age}</strong> 岁</p>
                    <p>置信度: ${(face.confidence * 100).toFixed(1)}%</p>
                </div>
            `;

            if (isKnown) {
                const faceName = face.identity;
                if (!checkouttimes[faceName]) {
                    checkouttimes[faceName] = { times: 0, last_request_time: performance.now() };
                }
                checkouttimes[faceName].times++;
                checkouttimes[faceName].last_request_time = performance.now();

                // ← 新增：只有达到阈值才触发操作，防止扫到就退
                if (checkouttimes[faceName].times < CHECKOUT_THRESHOLD) {
                    return;
                }

                // ← 查当前状态，自动判断是暂离还是签退
                const statusRes = await fetch(`${API_BASE}/check/status`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${localStorage.getItem('token')}`
                    },
                    body: JSON.stringify({ name: faceName })
                });
                const status = await statusRes.json();

                let away;
                if (status.is_online && !status.is_away) {
                    // 在线且未暂离 → 暂离
                    away = true;
                } else if (status.is_online && status.is_away) {
                    // 在线且暂离中 → 签退
                    away = false;
                } else {
                    // 不在线或状态异常 → 不操作
                    delete checkouttimes[faceName];
                    return;
                }

                const response = await fetch(`${API_BASE}/check/checkout`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${localStorage.getItem('token')}`
                    },
                    body: JSON.stringify({
                        name: faceName,
                        away: away,                    // ← 修复：用查到的 away 值
                        away_time: away ? 30 : null    // 暂离时长上限
                    })
                });
                const data = await response.json();
                if (response.ok && data.success) {
                    alert(data.message);
                    delete checkouttimes[faceName];
                }
            }
        });
        resultContent.innerHTML = html;
    } else {
        resultContent.innerHTML = `<div class="empty-state" style="color:red;">${data.error || '分析失败'}</div>`;
    }
}

function startRecording() {
    if (!stream) return;
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
    mediaRecorder.onstop = () => {
        const blob = new Blob(recordedChunks, { type: 'video/webm' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `face_recording_${new Date().toISOString().slice(0,19)}.webm`;
        a.click();
        URL.revokeObjectURL(url);
        isRecording = false;
        recordBtn.textContent = '录制';
        recordBtn.classList.remove('btn-danger');
        recordBtn.classList.add('btn-outline');
    };
    mediaRecorder.start(1000);
    isRecording = true;
    recordBtn.textContent = '停止录制';
    recordBtn.classList.remove('btn-outline');
    recordBtn.classList.add('btn-danger');
}

function stopRecording() { if (mediaRecorder && isRecording) mediaRecorder.stop(); }
function toggleRecording() { if (!isRunning) { alert('请先开始识别'); return; } if (isRecording) stopRecording(); else startRecording(); }

async function connect() {
    const user = window.parent.getCurrentUser ? window.parent.getCurrentUser() : null;
    if (!user || !user.id) { alert('请先登录'); return; }
    const cameraStarted = await startCamera();
    if (!cameraStarted) return;
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
        console.log('出口 WebSocket 已连接');
        isRunning = true;
        startCaptureLoop();
        recordBtn.disabled = false;
        window.parent.postMessage({ type: 'status', connected: true }, '*');
    };
    ws.onmessage = async (event) => {
        try { await updateResults(JSON.parse(event.data)); }
        catch (e) { console.error('解析失败:', e); }
    };
    ws.onclose = () => disconnect();
}

function disconnect() {
    if (!isRunning) return;
    isRunning = false;
    stopCaptureLoop();
    if (ws) { try { ws.close(); } catch(e) {} ws = null; }
    stopCamera();
    startBtn.disabled = false; stopBtn.disabled = true;
    recordBtn.disabled = true;
    resultContent.innerHTML = '<div class="empty-state"><p>点击"开始识别"启动摄像头</p></div>';
    fpsElement.textContent = 'FPS: --';
    overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    if (isRecording) stopRecording();
    window.parent.postMessage({ type: 'status', connected: false }, '*');
}

function checkLoginStatus() {
    const user = window.parent.getCurrentUser ? window.parent.getCurrentUser() : null;
    const isLoggedIn = !!(user && user.id);
    const loginOverlay = document.getElementById('loginOverlay');
    if (loginOverlay) {
        loginOverlay.style.display = isLoggedIn ? 'none' : 'flex';
        startBtn.disabled = !isLoggedIn;
    }
}

document.getElementById('overlayLoginBtn')?.addEventListener('click', () => {
    if (typeof window.parent.showLoginModal === 'function') window.parent.showLoginModal();
});

window.addEventListener('storage', (e) => { if (e.key === 'user' || e.key === 'token') checkLoginStatus(); });
checkLoginStatus();

startBtn.onclick = async () => { startBtn.disabled = true; stopBtn.disabled = false; await connect(); };
stopBtn.onclick = disconnect;
recordBtn.onclick = toggleRecording;

window.addEventListener('message', (e) => {
    if (e.data.type === 'theme') {
        if (e.data.dark) {
            document.body.classList.add('dark');
        } else {
            document.body.classList.remove('dark');
        }
    }
});

function initTheme() {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
        document.body.classList.add('dark');
    }
}
initTheme();