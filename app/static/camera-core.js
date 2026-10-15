/**
 * camera-core.js —— 摄像头页面的公共实现
 *
 * 引入背景（旧版问题）：
 *  1. in.js 与 out.js 约 60% 代码逐行重复（resizeCanvas / drawFaces /
 *     startCamera / captureAndSend / startCaptureLoop / 录制 / 主题），
 *     任何修复都要改两遍，且两份逻辑已经开始漂移。
 *  2. **每帧发包**：旧版 updateResults 在每一帧（约 10 fps）里对每张已识别
 *     人脸都发一次 /check/checkin，出口页更是一次 /check/status + 一次
 *     /check/checkout。单人使用即产生 20~30 次 HTTP 请求与数据库写入/秒。
 *  3. **业务判定放在前端**：是否暂离、是否签退由 JS 用帧计数（30 帧）猜测，
 *     触发时间在 3~15 秒之间浮动，不可预测。
 *  4. 旧版用 alert() 弹业务结果，持续识别时会连续弹窗。
 *
 * 现在：
 *  - 业务动作全部由服务端完成（见 app/services/frame_pipeline.py），
 *    前端只负责渲染服务端返回的 actions，**不再发任何业务请求**；
 *  - DOM 写入按帧节流，避免每帧重排；
 *  - 业务提示改用页内提示条，不再弹 alert。
 */
(function (window, document) {
    'use strict';

    var LF = window.LookUFace;
    if (!LF) {
        throw new Error('camera-core.js 依赖 api.js，请先引入 api.js');
    }

    /** 帧发送间隔（毫秒）。服务端有背压，这里控制上游发送节奏。 */
    var SEND_INTERVAL_MS = 150;
    /** DOM 渲染节流间隔（毫秒）。 */
    var RENDER_INTERVAL_MS = 300;
    /** 心跳间隔（毫秒）。用于探测僵死连接。 */
    var HEARTBEAT_MS = 20000;

    function CameraApp(options) {
        this.role = options.role;                       // 'entry' | 'exit'
        this.wsPath = options.wsPath;                   // /api/v1/ws/camera
        this.onActions = options.onActions || null;

        this.video = document.getElementById('video');
        this.overlay = document.getElementById('overlayCanvas');
        this.overlayCtx = this.overlay ? this.overlay.getContext('2d') : null;
        this.resultContent = document.getElementById('resultContent');
        this.fpsElement = document.getElementById('fps');

        this.startBtn = document.getElementById('startBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.recordBtn = document.getElementById('recordBtn');
        this.registerBtn = document.getElementById('registerBtn');
        this.registerName = document.getElementById('registerName');

        this.ws = null;
        this.stream = null;
        this.sendTimer = null;
        this.heartbeatTimer = null;
        this.isRunning = false;
        this.lastRenderAt = 0;
        this.pendingFaces = null;
        this.pendingActions = null;
        this.lastSentAt = 0;

        this.mediaRecorder = null;
        this.recordedChunks = [];
        this.isRecording = false;

        this._captureCanvas = document.createElement('canvas');
        this._captureCtx = this._captureCanvas.getContext('2d');
        this._flipCanvas = document.createElement('canvas');
        this._flipCtx = this._flipCanvas.getContext('2d');

        this._bind();
    }

    // ------------------------------------------------------------------ //
    // 初始化
    // ------------------------------------------------------------------ //

    CameraApp.prototype._bind = function () {
        var self = this;

        if (this.video) {
            this.video.addEventListener('loadedmetadata', function () { self.resizeCanvas(); });
        }
        if (this.startBtn) {
            this.startBtn.addEventListener('click', function () {
                self.startBtn.disabled = true;
                if (self.stopBtn) self.stopBtn.disabled = false;
                self.connect();
            });
        }
        if (this.stopBtn) {
            this.stopBtn.addEventListener('click', function () { self.disconnect(); });
        }
        if (this.recordBtn) {
            this.recordBtn.addEventListener('click', function () { self.toggleRecording(); });
        }
        if (this.registerBtn) {
            this.registerBtn.addEventListener('click', function () { self.registerCurrentFace(); });
        }
        var overlayLoginBtn = document.getElementById('overlayLoginBtn');
        if (overlayLoginBtn) {
            overlayLoginBtn.addEventListener('click', function () {
                if (typeof window.parent.showLoginModal === 'function') {
                    window.parent.showLoginModal();
                }
            });
        }

        window.addEventListener('storage', function (event) {
            if (event.key === 'token' || event.key === 'user') self.checkLoginStatus();
        });
        window.addEventListener('lookuface:auth-changed', function () { self.checkLoginStatus(); });

        // 主题：接收父页面广播
        LF.theme.bindInFrame();

        this.checkLoginStatus();
    };

    CameraApp.prototype.checkLoginStatus = function () {
        var loggedIn = LF.auth.isLoggedIn();
        var overlay = document.getElementById('loginOverlay');
        if (overlay) overlay.style.display = loggedIn ? 'none' : 'flex';
        if (this.startBtn && !this.isRunning) this.startBtn.disabled = !loggedIn;
    };

    // ------------------------------------------------------------------ //
    // 画布
    // ------------------------------------------------------------------ //

    CameraApp.prototype.resizeCanvas = function () {
        if (!this.overlay || !this.video) return;
        if (this.video.videoWidth && this.video.videoHeight) {
            this.overlay.width = this.video.videoWidth;
            this.overlay.height = this.video.videoHeight;
        }
    };

    CameraApp.prototype.drawFaces = function (faces) {
        if (!this.overlayCtx || !this.overlay) return;

        var ctx = this.overlayCtx;
        ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
        if (!faces || !faces.length) return;

        // 视频是镜像显示的，框也要镜像
        var videoWidth = this.overlay.width;

        for (var i = 0; i < faces.length; i++) {
            var face = faces[i];
            var bbox = face.bbox || [];
            if (bbox.length < 4) continue;

            var x1 = bbox[0], y1 = bbox[1], x2 = bbox[2], y2 = bbox[3];
            var mirroredX1 = videoWidth - x2;
            var width = x2 - x1;
            var height = y2 - y1;
            var isKnown = face.identity && face.identity !== 'Unknown';
            var color = isKnown ? '#4caf50' : '#ff5722';
            var label = isKnown ? face.identity : '陌生人';

            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.strokeRect(mirroredX1, y1, width, height);

            ctx.font = 'bold 14px Arial, sans-serif';
            var textWidth = ctx.measureText(label).width;
            ctx.fillStyle = color;
            ctx.fillRect(mirroredX1, Math.max(0, y1 - 22), textWidth + 10, 22);
            ctx.fillStyle = '#000';
            ctx.fillText(label, mirroredX1 + 5, Math.max(12, y1 - 6));
        }
    };

    // ------------------------------------------------------------------ //
    // 摄像头
    // ------------------------------------------------------------------ //

    CameraApp.prototype.startCamera = function () {
        var self = this;
        if (!window.navigator.mediaDevices || !window.navigator.mediaDevices.getUserMedia) {
            self.setMessage('当前浏览器不支持摄像头访问，请使用 https 或 localhost 访问');
            return Promise.resolve(false);
        }

        return window.navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } }
        }).then(function (stream) {
            self.stream = stream;
            self.video.srcObject = stream;
            return self.video.play();
        }).then(function () {
            self.resizeCanvas();
            return true;
        }).catch(function (err) {
            self.setMessage('摄像头错误：' + (err.message || err.name));
            LF.log.error('摄像头打开失败: ' + (err.message || err.name));
            return false;
        });
    };

    CameraApp.prototype.stopCamera = function () {
        if (this.stream) {
            this.stream.getTracks().forEach(function (track) { track.stop(); });
            this.stream = null;
        }
        if (this.video) this.video.srcObject = null;
    };

    // ------------------------------------------------------------------ //
    // 帧发送
    // ------------------------------------------------------------------ //

    CameraApp.prototype.captureAndSend = function () {
        var self = this;
        if (!this.ws || this.ws.readyState !== window.WebSocket.OPEN) return;
        if (!this.video || !this.video.videoWidth) return;

        var now = Date.now();
        if (now - this.lastSentAt < SEND_INTERVAL_MS) return;
        this.lastSentAt = now;

        var width = this.video.videoWidth;
        var height = this.video.videoHeight;

        this._captureCanvas.width = width;
        this._captureCanvas.height = height;
        this._captureCtx.drawImage(this.video, 0, 0, width, height);

        // 镜像后再发送，使服务端拿到的画面与用户所见一致
        this._flipCanvas.width = width;
        this._flipCanvas.height = height;
        this._flipCtx.save();
        this._flipCtx.translate(width, 0);
        this._flipCtx.scale(-1, 1);
        this._flipCtx.drawImage(this._captureCanvas, 0, 0);
        this._flipCtx.restore();

        this._flipCanvas.toBlob(function (blob) {
            if (!blob) return;
            if (self.ws && self.ws.readyState === window.WebSocket.OPEN) {
                // 避免堆积：仅当缓冲区空闲时才发送
                if (self.ws.bufferedAmount > 512 * 1024) return;
                self.ws.send(blob);
            }
        }, 'image/jpeg', 0.7);
    };

    CameraApp.prototype.startSendLoop = function () {
        var self = this;
        this.stopSendLoop();
        this.sendTimer = window.setInterval(function () {
            if (self.isRunning) self.captureAndSend();
        }, SEND_INTERVAL_MS);
    };

    CameraApp.prototype.stopSendLoop = function () {
        if (this.sendTimer) {
            window.clearInterval(this.sendTimer);
            this.sendTimer = null;
        }
    };

    // ------------------------------------------------------------------ //
    // WebSocket
    // ------------------------------------------------------------------ //

    CameraApp.prototype.connect = function () {
        var self = this;
        if (!LF.auth.isLoggedIn()) {
            LF.toast('请先登录', true);
            if (this.startBtn) this.startBtn.disabled = false;
            return;
        }

        this.startCamera().then(function (ok) {
            if (!ok) {
                if (self.startBtn) self.startBtn.disabled = false;
                if (self.stopBtn) self.stopBtn.disabled = true;
                return;
            }

            var protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            var url = protocol + window.location.host + self.wsPath +
                '?token=' + encodeURIComponent(LF.auth.getToken() || '');

            self.ws = new window.WebSocket(url);

            self.ws.onopen = function () {
                self.isRunning = true;
                self.startSendLoop();
                self._startHeartbeat();
                if (self.recordBtn) self.recordBtn.disabled = false;
                if (self.registerBtn) self.registerBtn.disabled = false;
                self.setMessage('摄像头已连接，正在识别…');
                self._notifyParent(true);
            };

            self.ws.onmessage = function (event) {
                var data;
                try { data = JSON.parse(event.data); } catch (e) { return; }
                if (data.type === 'pong' || data.type === 'stats') return;
                self.handleResult(data);
            };

            self.ws.onclose = function (event) {
                if (event && event.code === 1008) {
                    LF.toast('未认证或登录已过期，请重新登录', true);
                    LF.auth.clearSession();
                    self.checkLoginStatus();
                }
                self.disconnect();
            };

            self.ws.onerror = function () {
                LF.log.warn('摄像头 WebSocket 连接错误');
            };
        });
    };

    CameraApp.prototype.disconnect = function () {
        if (this.ws) {
            try { this.ws.onclose = null; this.ws.close(); } catch (e) { /* ignore */ }
            this.ws = null;
        }
        this._stopHeartbeat();
        if (!this.isRunning) { this.stopCamera(); return; }

        this.isRunning = false;
        this.stopSendLoop();
        this.stopCamera();

        if (this.startBtn) this.startBtn.disabled = false;
        if (this.stopBtn) this.stopBtn.disabled = true;
        if (this.recordBtn) this.recordBtn.disabled = true;
        if (this.registerBtn) this.registerBtn.disabled = true;

        this.setMessage('点击「开始识别」启动摄像头');
        if (this.fpsElement) this.fpsElement.textContent = 'FPS: --';
        if (this.overlayCtx) this.overlayCtx.clearRect(0, 0, this.overlay.width, this.overlay.height);
        if (this.isRecording) this.stopRecording();
        this._notifyParent(false);
    };

    CameraApp.prototype._startHeartbeat = function () {
        var self = this;
        this._stopHeartbeat();
        this.heartbeatTimer = window.setInterval(function () {
            if (self.ws && self.ws.readyState === window.WebSocket.OPEN) {
                try { self.ws.send('ping'); } catch (e) { /* ignore */ }
            }
        }, HEARTBEAT_MS);
    };

    CameraApp.prototype._stopHeartbeat = function () {
        if (this.heartbeatTimer) {
            window.clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
    };

    CameraApp.prototype._notifyParent = function (connected) {
        try {
            window.parent.postMessage({ type: 'status', connected: connected, role: this.role }, '*');
        } catch (e) { /* ignore */ }
    };

    // ------------------------------------------------------------------ //
    // 渲染
    // ------------------------------------------------------------------ //

    CameraApp.prototype.setMessage = function (text, isError) {
        if (!this.resultContent) return;
        this.resultContent.innerHTML =
            '<div class="empty-state"' + (isError ? ' style="color:#ff7043;"' : '') + '>' +
            LF.escapeHtml(text) + '</div>';
    };

    CameraApp.prototype.handleResult = function (data) {
        if (!data.success) {
            this.setMessage(data.detail || '分析失败', true);
            return;
        }

        this.drawFaces(data.faces);

        // 业务动作由服务端产生，前端只展示（不再弹 alert）
        if (data.actions && data.actions.length) {
            for (var i = 0; i < data.actions.length; i++) {
                LF.toast(data.actions[i], false);
            }
            if (this.onActions) this.onActions(data.actions);
        }

        // DOM 渲染节流：约 300ms 一次，避免每帧重排
        var now = Date.now();
        this.pendingFaces = data.faces;
        if (now - this.lastRenderAt < RENDER_INTERVAL_MS) return;
        this.lastRenderAt = now;
        this.renderFaces(data.faces);
    };

    CameraApp.prototype.renderFaces = function (faces) {
        if (!this.resultContent) return;

        if (!faces || !faces.length) {
            this.setMessage('未检测到人脸');
            return;
        }

        var html = '';
        for (var i = 0; i < faces.length; i++) {
            var face = faces[i];
            var isKnown = face.identity && face.identity !== 'Unknown';
            var cardClass = isKnown ? 'face-card known' : 'face-card unknown';
            var confidence = (face.identity_confidence > 0)
                ? ' (' + (face.identity_confidence * 100).toFixed(0) + '%)'
                : '';

            html += '<div class="' + cardClass + '">' +
                '<h3>人脸 ' + (i + 1) + '</h3>' +
                '<p>身份: <strong>' + LF.escapeHtml(face.identity) + '</strong>' + confidence + '</p>' +
                '<p>性别: <strong>' + LF.escapeHtml(face.gender) + '</strong></p>' +
                '<p>年龄: <strong>' + LF.escapeHtml(face.age) + '</strong></p>' +
                '<p>置信度: ' + (face.confidence * 100).toFixed(1) + '%</p>' +
                '</div>';
        }
        this.resultContent.innerHTML = html;
    };

    // ------------------------------------------------------------------ //
    // 注册人脸
    // ------------------------------------------------------------------ //

    CameraApp.prototype.registerCurrentFace = function () {
        var self = this;
        if (!this.registerName) return;

        var name = this.registerName.value.trim();
        if (!name) { LF.toast('请输入姓名', true); return; }
        if (!LF.auth.isLoggedIn()) {
            LF.toast('请先登录后再注册人脸', true);
            if (typeof window.parent.showLoginModal === 'function') window.parent.showLoginModal();
            return;
        }
        if (!this.isRunning) { LF.toast('请先点击「开始识别」', true); return; }

        var width = this.video.videoWidth || 640;
        var height = this.video.videoHeight || 480;
        var canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        canvas.getContext('2d').drawImage(this.video, 0, 0, width, height);

        canvas.toBlob(function (blob) {
            if (!blob) { LF.toast('截帧失败，请重试', true); return; }

            var form = new FormData();
            form.append('file', blob, 'register.jpg');
            form.append('name', name);

            if (self.registerBtn) self.registerBtn.disabled = true;
            LF.api.upload('/faces/register', form)
                .then(function (result) {
                    if (result.success) {
                        LF.toast(result.message, false);
                        self.registerName.value = '';
                    } else {
                        LF.toast(result.message || '注册失败', true);
                    }
                })
                .catch(function (err) {
                    LF.toast('注册失败：' + err.message, true);
                })
                .then(function () {
                    if (self.registerBtn && self.isRunning) self.registerBtn.disabled = false;
                });
        }, 'image/jpeg', 0.95);
    };

    // ------------------------------------------------------------------ //
    // 录制
    // ------------------------------------------------------------------ //

    CameraApp.prototype.toggleRecording = function () {
        if (!this.isRunning) { LF.toast('请先开始识别', true); return; }
        if (this.isRecording) this.stopRecording();
        else this.startRecording();
    };

    CameraApp.prototype.startRecording = function () {
        if (!this.stream || !window.MediaRecorder) return;

        var self = this;
        this.recordedChunks = [];
        this.mediaRecorder = new window.MediaRecorder(this.stream);

        this.mediaRecorder.addEventListener('dataavailable', function (event) {
            if (event.data && event.data.size > 0) self.recordedChunks.push(event.data);
        });

        this.mediaRecorder.addEventListener('stop', function () {
            var blob = new Blob(self.recordedChunks, { type: 'video/webm' });
            var url = URL.createObjectURL(blob);
            var anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = 'face_recording_' + new Date().toISOString().slice(0, 19).replace(/[:]/g, '-') + '.webm';
            anchor.click();
            URL.revokeObjectURL(url);

            self.isRecording = false;
            if (self.recordBtn) {
                self.recordBtn.textContent = '录制';
                self.recordBtn.classList.remove('btn-danger');
                self.recordBtn.classList.add('btn-outline');
            }
        });

        this.mediaRecorder.start(1000);
        this.isRecording = true;
        if (this.recordBtn) {
            this.recordBtn.textContent = '停止录制';
            this.recordBtn.classList.remove('btn-outline');
            this.recordBtn.classList.add('btn-danger');
        }
    };

    CameraApp.prototype.stopRecording = function () {
        if (this.mediaRecorder && this.isRecording) {
            try { this.mediaRecorder.stop(); } catch (e) { /* ignore */ }
        }
    };

    // 页面卸载时释放摄像头与连接
    window.addEventListener('beforeunload', function () { /* 浏览器会自动释放 */ });

    window.CameraApp = CameraApp;
})(window, document);
