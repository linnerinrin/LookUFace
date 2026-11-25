/**
 * idx.js —— 首页（双 iframe：入口 / 出口摄像头）
 *
 * 依赖：api.js（需先引入）
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

})();
