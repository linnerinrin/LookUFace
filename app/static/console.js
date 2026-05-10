//console.js 控制台
/*
connectLogWS 连logws
loadhistory 调logs/recent接口 调历史日志文件
formatTime()  保持时间格式与后端日志一致
addLog()新增一句日志
clearConsole()清空控制台
exportLog() 导出日志
*/

const WS_URL = 'ws://' + window.location.host + '/api/v1/ws/log';

        let wsLog;

        function connectLogWS() {
            wsLog = new WebSocket(WS_URL);
            wsLog.onmessage = (event) => {
                addLog(event.data);
            };
            wsLog.onclose = () => {
                setTimeout(connectLogWS, 3000);
            };
        }

        async function loadHistory() {
            const lines = parseInt(document.getElementById('filterLines').value) || 100;
            const level = document.getElementById('filterLevel').value || 'all';
            const date = document.getElementById('filterDate').value || '';

            try {
                const res = await fetch('/api/v1/logs/recent', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lines, level, date })
                });
                const data = await res.json();
                consoleOutput.innerHTML = '';
                logCount = 0;
                logCountSpan.textContent = '0 条';

                if (data.logs && data.logs.length > 0) {
                    data.logs.forEach(line => addLog(line, 'info', true));
                } else {
                    addLog('没有匹配的日志', 'info');
                }
            } catch(e) {
                addLog('加载日志失败: ' + e.message, 'error');
            }
        }

        loadHistory().then(() => connectLogWS());

        // 主题切换
        const themeToggle = document.getElementById('themeToggle');
        const logo = document.getElementById('logo');

        function initTheme() {
            const savedTheme = localStorage.getItem('theme');
            if (savedTheme === 'dark') document.body.classList.add('dark');
        }
        function toggleTheme() {
            document.body.classList.toggle('dark');
            const isDark = document.body.classList.contains('dark');
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        }
        themeToggle?.addEventListener('click', toggleTheme);
        initTheme();
        logo?.addEventListener('click', () => { window.location.href = 'idx.html'; });

        let logCount = 0;
        const consoleOutput = document.getElementById('consoleOutput');
        const logCountSpan = document.getElementById('logCount');

        function formatTime() {
            const d = new Date();
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        }

        function addLog(message, level = 'info', skipTimestamp = false) {
            logCount++;
            logCountSpan.textContent = `${logCount} 条`;
            const line = document.createElement('div');

            if (skipTimestamp) {
                line.innerHTML = `<span>${message}</span>`;
            } else {
                const now  = formatTime();
                line.innerHTML = `<span class="log-timestamp">[${now}]</span><span class="log-${level}">${message}</span>`;
            }

            consoleOutput.appendChild(line);
            consoleOutput.scrollTop = consoleOutput.scrollHeight;
        }

        function clearConsole(silent = false) {
            consoleOutput.innerHTML = '';
            logCount = 0;
            logCountSpan.textContent = '0 条';
            if (!silent) {
                addLog('控制台已清空', 'info');
            }
        }

        function exportLog() {
            const text = consoleOutput.innerText;
            const blob = new Blob([text], { type: 'text/plain;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `console_log_${new Date().toISOString().slice(0,10)}.txt`;
            a.click();
            URL.revokeObjectURL(url);
        }

        (function() {
            const originalLog = console.log;
            const originalWarn = console.warn;
            const originalError = console.error;

            console.log = function(...args) {
                originalLog.apply(console, args);
                addLog(args.join(' '), 'info');
            };
            console.warn = function(...args) {
                originalWarn.apply(console, args);
                addLog(args.join(' '), 'warn');
            };
            console.error = function(...args) {
                originalError.apply(console, args);
                addLog(args.join(' '), 'error');
            };

            // 捕获未处理的错误
            window.addEventListener('error', (e) => {
                addLog(`[未捕获] ${e.message} (${e.filename}:${e.lineno})`, 'error');
            });
        })();