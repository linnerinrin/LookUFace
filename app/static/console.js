/**
 * console.js —— 控制台日志页面
 *
 * 依赖：api.js（需先引入）
 *
 * 相对旧版的修复：
 *  1. 旧版把日志 WebSocket 指向 /api/v1/ws/log；新端点为 /api/v1/ws/logs。
 *  2. 旧版劫持 console.log/warn/error 把本页所有输出再塞回控制台，
 *     形成「看日志的页面自己产生日志」的循环噪声，已移除。
 *  3. 旧版在 loadHistory().then(...) 里用裸标识符 logCount / consoleOutput /
 *     logCountSpan —— 它们是 `let`/`const` 声明在文件中部，处于**暂时性死区**，
 *     首次执行必然抛 ReferenceError。现改为在文件顶部先取元素。
 *  4. 历史日志与实时日志混在一起无法区分；现在实时日志用不同前缀标注。
 */
(function () {
    'use strict';

    var LF = window.LookUFace;

    var consoleOutput = document.getElementById('consoleOutput');
    var logCountSpan = document.getElementById('logCount');
    var logCount = 0;
    var wsLog = null;
    var reconnectTimer = null;
    var seenHistory = false;

    // ------------------------------------------------------------------ //
    // 渲染
    // ------------------------------------------------------------------ //

    function formatTime() {
        var d = new Date();
        var pad = function (n) { return String(n).padStart(2, '0'); };
        return d.getFullYear() + '/' + pad(d.getMonth() + 1) + '/' + pad(d.getDate()) + ' ' +
            pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    function levelOf(text) {
        if (text.indexOf('[ERROR]') !== -1 || text.indexOf('[CRITICAL]') !== -1) return 'error';
        if (text.indexOf('[WARNING]') !== -1 || text.indexOf('[WARN]') !== -1) return 'warn';
        if (text.indexOf('[DEBUG]') !== -1) return 'debug';
        return 'info';
    }

    function addLog(message, options) {
        options = options || {};
        if (!consoleOutput) return;

        logCount++;
        if (logCountSpan) logCountSpan.textContent = logCount + ' 条';

        var line = document.createElement('div');
        var level = options.level || levelOf(String(message));
        var prefix = options.live ? '[实时] ' : '';
        var text = prefix + String(message);

        if (options.raw) {
            line.innerHTML = '<span class="log-' + level + '"></span>';
            line.firstChild.textContent = text;
        } else {
            line.innerHTML = '<span class="log-timestamp">[' + formatTime() + ']</span>' +
                '<span class="log-' + level + '"></span>';
            line.lastChild.textContent = text;
        }

        consoleOutput.appendChild(line);
        consoleOutput.scrollTop = consoleOutput.scrollHeight;
    }

    function clearConsole(silent) {
        if (!consoleOutput) return;
        consoleOutput.innerHTML = '';
        logCount = 0;
        if (logCountSpan) logCountSpan.textContent = '0 条';
        if (!silent) addLog('控制台已清空');
    }

    // ------------------------------------------------------------------ //
    // 历史日志
    // ------------------------------------------------------------------ //

    function loadHistory() {
        var linesEl = document.getElementById('filterLines');
        var levelEl = document.getElementById('filterLevel');
        var dateEl = document.getElementById('filterDate');

        var payload = {
            lines: parseInt(linesEl && linesEl.value, 10) || 100,
            level: (levelEl && levelEl.value) || 'all',
            date: (dateEl && dateEl.value) || null
        };

        return LF.api.post('/logs/recent', payload, { skipAuth: true })
            .then(function (data) {
                clearConsole(true);
                seenHistory = true;
                if (data.logs && data.logs.length) {
                    data.logs.forEach(function (line) { addLog(line, { raw: true }); });
                } else {
                    addLog('没有匹配的日志（' + (data.file || '今日文件不存在') + '）');
                }
            })
            .catch(function (err) {
                clearConsole(true);
                addLog('加载日志失败：' + err.message, { level: 'error' });
            });
    }

    // ------------------------------------------------------------------ //
    // 实时日志
    // ------------------------------------------------------------------ //

    function connectLogWS() {
        if (reconnectTimer) {
            window.clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }

        var protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        wsLog = new WebSocket(protocol + window.location.host + '/api/v1/ws/logs');

        wsLog.onopen = function () {
            addLog('已连接实时日志流');
        };

        wsLog.onmessage = function (event) {
            addLog(event.data, { live: true });
        };

        wsLog.onclose = function () {
            addLog('实时日志流已断开，3 秒后重连…', { level: 'warn' });
            reconnectTimer = window.setTimeout(connectLogWS, 3000);
        };

        wsLog.onerror = function () {
            LF.log.warn('日志 WebSocket 连接错误');
        };
    }

    // ------------------------------------------------------------------ //
    // 导出
    // ------------------------------------------------------------------ //

    function exportLog() {
        if (!consoleOutput) return;
        var blob = new Blob([consoleOutput.innerText], { type: 'text/plain;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'console_log_' + new Date().toISOString().slice(0, 10) + '.txt';
        anchor.click();
        URL.revokeObjectURL(url);
    }

    // ------------------------------------------------------------------ //
    // 主题与初始化
    // ------------------------------------------------------------------ //

    var themeToggle = document.getElementById('themeToggle');
    var logo = document.getElementById('logo');
    if (themeToggle) themeToggle.addEventListener('click', function () { LF.theme.toggle(); });
    LF.theme.init();
    if (logo) logo.addEventListener('click', function () { window.location.href = 'idx.html'; });

    // 暴露给 onclick
    window.clearConsole = clearConsole;
    window.exportLog = exportLog;
    window.loadHistory = loadHistory;

    loadHistory().then(connectLogWS);
    void seenHistory;
})();
