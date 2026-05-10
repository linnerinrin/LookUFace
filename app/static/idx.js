// idx.js - 主页面逻辑

/* ========== 主题切换 ========== */
const themeToggle = document.getElementById('themeToggle');
const logo = document.getElementById('logo');

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
    document.querySelectorAll('.iframe-row iframe').forEach(iframe => {
        iframe.contentWindow.postMessage({ type: 'theme', dark: isDark }, '*');
    });
}

themeToggle?.addEventListener('click', toggleTheme);
initTheme();

// Logo 点击返回首页
logo?.addEventListener('click', () => {
    window.location.href = 'idx.html';
});

/* ========== 状态指示器（由 iframe 通过 postMessage 通知更新） ========== */
window.addEventListener('message', (e) => {
    if (e.data.type === 'status') {
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (e.data.connected) {
            statusDot.className = 'status-dot connected';
            statusText.innerText = '已连接';
        } else {
            statusDot.className = 'status-dot offline';
            statusText.innerText = '未连接';
        }
    }
});