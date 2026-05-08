
// 引入认证模块
// 需要先加载 auth.js
// idx.js - 实时识别页面
const video = document.getElementById('video');
const overlayCanvas = document.getElementById('overlayCanvas');
const overlayCtx = overlayCanvas.getContext('2d');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const recordBtn = document.getElementById('recordBtn');
const registerBtn = document.getElementById('registerBtn');
const registerName = document.getElementById('registerName');
const resultContent = document.getElementById('resultContent');
const resultStats = document.getElementById('resultStats');
const faceCountSpan = document.getElementById('faceCount');
const processingTimeSpan = document.getElementById('processingTime');
const fpsElement = document.getElementById('fps');

// 主题切换
const themeToggle = document.getElementById('themeToggle');
const logo = document.getElementById('logo');

// 状态变量
let ws = null;
let stream = null;
let animationId = null;
let frameCount = 0;
let lastFpsUpdate = 0;
let isRunning = false;
let isLeaving = false;

// 录制相关
let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;

// 当前帧数据
let currentFrameData = null;
let currentFaces = [];

const WS_URL = 'ws://localhost:8000/api/v1/ws/camera';

// idx.js 开头添加
let isLoggedIn = false;

// 检查登录状态
function checkLoginStatus() {
    const user = getCurrentUser();
    isLoggedIn = !!(user && user.id);

    const loginOverlay = document.getElementById('loginOverlay');
    const startBtn = document.getElementById('startBtn');

    if (loginOverlay) {
        if (isLoggedIn) {
            loginOverlay.style.display = 'none';
            startBtn.disabled = false;
        } else {
            loginOverlay.style.display = 'flex';
            startBtn.disabled = true;
        }
    }
}

// 监听登录状态变化
window.addEventListener('storage', function(e) {
    if (e.key === 'user' || e.key === 'token') {
        checkLoginStatus();
    }
});

// 遮罩层登录按钮
document.getElementById('overlayLoginBtn')?.addEventListener('click', () => {
    showLoginModal();
});

// 修改 connect 函数，增加登录检查
async function connect() {
    const user = getCurrentUser();
    if (!user || !user.id) {
        alert('请先登录');
        return;
    }

    resultContent.innerHTML = '<div class="empty-state"><div class="empty-icon"></div><p>正在启动...</p></div>';
    resultStats.style.display = 'none';

    const cameraStarted = await startCamera();
    if (!cameraStarted) return;

    ws = new WebSocket(WS_URL);
    // ... 其余代码不变
}

// 在初始化时调用检查
checkLoginStatus();

// 主题切换
function initTheme() {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
        document.body.classList.add('dark');
    }
}

function toggleTheme() {
    document.body.classList.toggle('dark');
    const isDark = document.body.classList.contains('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
}

themeToggle.addEventListener('click', toggleTheme);
initTheme();

// Logo 点击返回首页
logo.addEventListener('click', () => {
    window.location.href = 'idx.html';
});

// 更新状态指示器
function updateStatusDot(connected) {
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    if (connected) {
        statusDot.className = 'status-dot connected';
        statusText.innerText = '已连接';
    } else {
        statusDot.className = 'status-dot offline';
        statusText.innerText = '未连接';
    }
}

// Canvas 绘制
function resizeCanvas() {
    if (video.videoWidth && video.videoHeight) {
        overlayCanvas.width = video.videoWidth;
        overlayCanvas.height = video.videoHeight;
        overlayCanvas.style.width = '100%';
        overlayCanvas.style.height = '100%';
    }
}

video.addEventListener('loadedmetadata', resizeCanvas);
window.addEventListener('resize', resizeCanvas);

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

// 摄像头
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
        resultContent.innerHTML = `<div class="empty-state"><div class="empty-icon"></div><p>摄像头错误: ${err.message}</p></div>`;
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

// WebSocket
const captureCanvas = document.createElement('canvas');
const captureCtx = captureCanvas.getContext('2d');

async function captureAndSend() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !video.videoWidth) return;

    const width = video.videoWidth;
    const height = video.videoHeight;
    captureCanvas.width = width;
    captureCanvas.height = height;
    captureCtx.drawImage(video, 0, 0, width, height);

    const frameCopy = captureCtx.getImageData(0, 0, width, height);
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = width;
    tempCanvas.height = height;
    tempCanvas.getContext('2d').putImageData(frameCopy, 0, 0);
    currentFrameData = tempCanvas;

    const imageData = captureCtx.getImageData(0, 0, width, height);
    const flippedCanvas = document.createElement('canvas');
    flippedCanvas.width = width;
    flippedCanvas.height = height;
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
        frameCount = 0;
        lastFpsUpdate = now;
    }
}

function startCaptureLoop() {
    if (animationId) clearInterval(animationId);
    frameCount = 0;
    lastFpsUpdate = performance.now();
    animationId = setInterval(() => {
        if (isRunning && ws?.readyState === WebSocket.OPEN) captureAndSend();
    }, 100);
}

function stopCaptureLoop() {
    if (animationId) { clearInterval(animationId); animationId = null; }
}

function updateResults(data) {
    if (data.success) {
        resultStats.style.display = 'block';
        faceCountSpan.textContent = data.face_count;
        processingTimeSpan.textContent = data.processing_ms;

        if (data.faces && overlayCanvas.width > 0) {
            drawFaces(data.faces, overlayCanvas.width);
        } else {
            overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
        }

        if (data.face_count === 0) {
            resultContent.innerHTML = '<div class="empty-state"><div class="empty-icon">👤</div><p>未检测到人脸</p></div>';
            return;
        }

        let html = '';
        data.faces.forEach((face, i) => {
            const isKnown = face.identity !== 'Unknown';
            const cardClass = isKnown ? 'face-card known' : 'face-card unknown';
            html += `
                <div class="${cardClass}">
                    <h3> 人脸 ${i + 1}</h3>
                    <p> 身份: <strong>${face.identity}</strong> ${face.identity_confidence > 0 ? `(${(face.identity_confidence * 100).toFixed(0)}%)` : ''}</p>
                    <p> 性别: <strong>${face.gender}</strong></p>
                    <p> 年龄: <strong>${face.age}</strong> 岁</p>
                    <p> 置信度: ${(face.confidence * 100).toFixed(1)}%</p>
                </div>
            `;
        });
        resultContent.innerHTML = html;
    } else {
        resultStats.style.display = 'none';
        resultContent.innerHTML = `<div class="empty-state" style="color:red;"> ${data.error || '分析失败'}</div>`;
    }
}

// 人脸注册
// 人脸注册
async function registerCurrentFace() {
    const name = registerName.value.trim();
    if (!name) {
        alert('请输入姓名');
        return;
    }
    if (!currentFrameData) {
        alert('没有摄像头画面，请先开始识别');
        return;
    }

    // 检查登录状态
    const token = localStorage.getItem('token');
    if (!token) {
        alert('请先登录后再注册人脸');
        showLoginModal();
        return;
    }

    // 检查画面中是否只有一个人脸
    if (currentFaces.length !== 1) {
        alert(`检测到 ${currentFaces.length} 张人脸，请确保画面中只有一个人脸时再注册`);
        return;
    }

    const canvas = currentFrameData;
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg'));
    const formData = new FormData();
    formData.append('file', blob);
    formData.append('name', name);
    formData.append('token', token);  // 关键：传递 token

    try {
        const response = await fetch('http://localhost:8000/api/v1/face/register', {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        if (result.success) {
            alert(`注册成功！欢迎 ${name}`);
            registerName.value = '';
        } else {
            alert('注册失败：' + (result.message || '请确保人脸清晰可见'));
        }
    } catch (err) {
        alert('注册失败: ' + err.message);
    }
}
// 录制功能
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
        recordBtn.textContent = '录制视频';
        recordBtn.classList.remove('btn-danger');
        recordBtn.classList.add('btn-outline');
        const badge = document.querySelector('.recording-badge');
        if (badge) badge.remove();
    };
    mediaRecorder.start(1000);
    isRecording = true;
    recordBtn.textContent = '停止录制';
    recordBtn.classList.remove('btn-outline');
    recordBtn.classList.add('btn-danger');
    const badge = document.createElement('div');
    badge.className = 'recording-badge';
    badge.textContent = '录制中...';
    document.body.appendChild(badge);
}

function stopRecording() {
    if (mediaRecorder && isRecording) mediaRecorder.stop();
}

function toggleRecording() {
    if (!isRunning) { alert('请先开始识别'); return; }
    if (isRecording) stopRecording();
    else startRecording();
}

// 连接管理
async function connect() {
    resultContent.innerHTML = '<div class="empty-state"><div class="empty-icon">🔄</div><p>正在启动...</p></div>';
    resultStats.style.display = 'none';

    const cameraStarted = await startCamera();
    if (!cameraStarted) return;

    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
        console.log('WebSocket 已连接');
        updateStatusDot(true);
        isRunning = true;
        startCaptureLoop();
        recordBtn.disabled = false;
        registerBtn.disabled = false;
    };
    ws.onmessage = (event) => {
        try { updateResults(JSON.parse(event.data)); }
        catch (e) { console.error('解析失败:', e); }
    };
    ws.onerror = () => { updateStatusDot(false); };
    ws.onclose = () => { disconnect(); };
}

function disconnect() {
    if (!isRunning) return;
    isRunning = false;
    stopCaptureLoop();
    if (ws) {
        try { ws.close(); } catch(e) {}
        ws = null;
    }
    stopCamera();
    updateStatusDot(false);
    startBtn.disabled = false;
    stopBtn.disabled = true;
    recordBtn.disabled = true;
    registerBtn.disabled = true;
    resultStats.style.display = 'none';
    resultContent.innerHTML = '<div class="empty-state"><div class="empty-icon">👤</div><p>点击"开始识别"启动摄像头</p></div>';
    fpsElement.textContent = 'FPS: --';
    overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    if (isRecording) stopRecording();
}

// 页面切换提示
window.addEventListener('beforeunload', function(e) {
    if (isRunning && !isLeaving) {
        e.preventDefault();
        e.returnValue = '';
        return '';
    }
});

document.querySelectorAll('.nav-item').forEach(link => {
    link.addEventListener('click', function(e) {
        if (isRunning && !isLeaving) {
            e.preventDefault();
            const userConfirmed = confirm('识别正在进行中，确定要离开吗？。');
            if (userConfirmed) {
                isLeaving = true;
                disconnect();
                setTimeout(() => {
                    window.location.href = this.href;
                }, 200);
            }
        }
    });
});

document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible') {
        isLeaving = false;
    }
});

// 按钮事件
startBtn.onclick = async () => {
    startBtn.disabled = true;
    stopBtn.disabled = false;
    await connect();
};
stopBtn.onclick = disconnect;
recordBtn.onclick = toggleRecording;
registerBtn.onclick = registerCurrentFace;

window.onbeforeunload = () => {
    if (animationId) clearInterval(animationId);
    if (ws) ws.close();
    if (stream) stopCamera();
};

// 预览功能
async function previewFace(name) {
    const modal = document.getElementById('previewModal');
    const img = document.getElementById('modalImg');
    const info = document.getElementById('modalInfo');
    modal.style.display = 'flex';
    info.innerHTML = '加载中...';
    img.style.display = 'none';
    try {
        const response = await fetch(`http://localhost:8000/api/v1/face/screenshot/${encodeURIComponent(name)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        img.src = url;
        img.style.display = 'block';
        info.innerHTML = `${name} 注册时的照片`;
        img.onclick = () => window.open(url, '_blank');
    } catch (err) {
        info.innerHTML = `加载失败: ${err.message}`;
    }
}

function closePreview() {
    const modal = document.getElementById('previewModal');
    const img = document.getElementById('modalImg');
    modal.style.display = 'none';
    if (img.src) {
        URL.revokeObjectURL(img.src);
        img.src = '';
    }
}

window.previewFace = previewFace;
window.closePreview = closePreview;
