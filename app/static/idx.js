/**
 * idx.js —— 首页（双 iframe：入口 / 出口摄像头）
 *
 * 依赖：api.js（需先引入）
 *
 * 相对旧版的修复：
 *  1. 主题逻辑改用 LookUFace.theme，并通过 storage 事件与 iframe 双向同步
 *     （旧版只在切换时 postMessage，iframe 内主动切换主题后父页面不同步）。
 *  2. postMessage 校验来源，避免处理任意来源的消息。
 *  3. 状态指示器元素缺失时不再抛错（旧版直接访问 statusDot.className，
 *     但 idx.html 里根本没有这两个元素）。
 */
(function () {
    'use strict';

    var LF = window.LookUFace;
    var themeToggle = document.getElementById('themeToggle');
    var logo = document.getElementById('logo');

    function initTheme() {
        LF.theme.init();
    }

    function toggleTheme() {
        var next = LF.theme.toggle();
        // 通知两个 iframe
        document.querySelectorAll('.iframe-row iframe').forEach(function (iframe) {
            try {
                iframe.contentWindow.postMessage({ type: 'theme', dark: next === 'dark' }, '*');
            } catch (e) { /* iframe 未就绪 */ }
        });
    }

    if (themeToggle) themeToggle.addEventListener('click', toggleTheme);
    initTheme();

    // 同源其他标签页切换主题时同步
    window.addEventListener('storage', function (event) {
        if (event.key === LF.theme.KEY) {
            LF.theme.apply(LF.theme.current());
            var dark = LF.theme.current() === 'dark';
            document.querySelectorAll('.iframe-row iframe').forEach(function (iframe) {
                try {
                    iframe.contentWindow.postMessage({ type: 'theme', dark: dark }, '*');
                } catch (e) { /* ignore */ }
            });
        }
    });

    if (logo) {
        logo.addEventListener('click', function () { window.location.href = 'idx.html'; });
    }

    /* ---------- iframe 状态上报 ---------- */
    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin) return;
        if (!event.data || event.data.type !== 'status') return;

        var statusDot = document.getElementById('statusDot');
        var statusText = document.getElementById('statusText');
        // 首页当前没有这两个元素；保留逻辑以便将来加入时自动生效
        if (!statusDot || !statusText) return;

        if (event.data.connected) {
            statusDot.className = 'status-dot connected';
            statusText.innerText = event.data.role === 'exit' ? '出口已连接' : '入口已连接';
        } else {
            statusDot.className = 'status-dot offline';
            statusText.innerText = '未连接';
        }
    });
})();
