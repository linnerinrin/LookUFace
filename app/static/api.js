/**
 * api.js —— 前端公共基础设施（认证、请求、提示、主题）
 *
 * 本文件提供：
 *  - `LookUFace.api`   统一请求封装（统一错误、网络诊断）
 *  - `LookUFace.toast` 统一提示
 *  - `LookUFace.log`   显式日志上报（仅 error/warn 上报）
 *  - `LookUFace.theme` 主题读写与跨 iframe 同步
 */
(function (window, document) {
    'use strict';

    var BASE = '/api/v1';

    // ------------------------------------------------------------------ //
    // 工具
    // ------------------------------------------------------------------ //

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
        });
    }

    function parseJsonSafe(text) {
        try { return JSON.parse(text); } catch (e) { return null; }
    }

    // ------------------------------------------------------------------ //
    // 请求封装
    // ------------------------------------------------------------------ //

    function ApiError(message, status, code, details) {
        this.name = 'ApiError';
        this.message = message;
        this.status = status;
        this.code = code || 'error';
        this.details = details || null;
    }
    ApiError.prototype = Object.create(Error.prototype);

    // ------------------------------------------------------------------ //
    // 网络层失败的诊断
    // ------------------------------------------------------------------ //

    /**
     * fetch 在 HTTP 层面失败时抛出的错误
     * 失败时对同源 /health 做一次探测，把结论写进 hint
     */
    function NetworkError(cause, url) {
        this.name = 'NetworkError';
        this.cause = cause;
        this.url = url;
        this.status = 0;
        this.code = 'network_error';
        this.hint = '无法连接服务器。';
        this.message = '无法连接服务器（' + (cause && cause.message ? cause.message : '未知网络错误') + '）';
    }
    NetworkError.prototype = Object.create(Error.prototype);

    /** 探测同源连通性，返回面向用户的诊断结论 */
    function diagnoseConnectivity() {
        var healthUrl = BASE + '/health';

        if (window.location.protocol === 'file:') {
            return Promise.resolve(
                '页面是用 file:// 直接打开的，这样所有 /api/... 请求都会指向本地文件系统，' +
                '必然失败。请先运行 start.bat，再访问 http://127.0.0.1:8835/static/idx.html'
            );
        }

        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timer = setTimeout(function () {
            if (controller) controller.abort();
        }, 4000);

        return window.fetch(healthUrl, {
            method: 'GET',
            cache: 'no-store',
            // 加一个非简单头，强制走预检，从而也能验证 CORS 是否放行
            headers: { 'X-Requested-With': 'LookUFace-Diag' },
            signal: controller ? controller.signal : undefined
        }).then(function (response) {
            clearTimeout(timer);
            if (response.ok) {
                return '服务器可达且 CORS 放行，但该接口仍然失败，请查看服务端日志。';
            }
            return '服务器可达但返回 HTTP ' + response.status + '，请查看服务端日志。';
        }).catch(function (probeError) {
            clearTimeout(timer);
            var origin = window.location.origin;
            var aborted = probeError && probeError.name === 'AbortError';

            var lines = [];
            lines.push('服务器无法访问：' + healthUrl);
            lines.push('页面来源（Origin）：' + (origin === 'null' ? 'null（file:// 打开）' : origin));
            lines.push('异常：' + (probeError && probeError.message ? probeError.message : String(probeError)));

            if (aborted) {
                lines.push('结论：连接被挂起直至超时。这是「浏览器配置了代理、但代理不可用」的典型特征。');
            } else {
                lines.push('结论：连接被立即拒绝。通常是服务未启动，或代理拒绝了该请求。');
            }

            lines.push('');
            lines.push('请依次检查：');
            lines.push('  1. 服务是否在运行：在项目目录执行 start.bat，确认窗口没有闪退');
            lines.push('  2. 浏览器代理：若系统代理指向一个没有在运行的程序（例如 127.0.0.1:7890），');
            lines.push('     请关闭代理（Windows 设置 → 网络和 Internet → 代理 → 关闭「使用代理服务器」）');
            lines.push('  3. 访问方式：请使用 http://127.0.0.1:8835/static/idx.html，');
            lines.push('     端口必须与 .env 里的 PORT 一致；不要用 file:// 打开页面，');
            lines.push('     也不要用 192.168.x.x / 机器名访问');
            lines.push('     （服务端 ALLOWED_ORIGINS 只允许 127.0.0.1 与 localhost 的对应端口）');

            return lines.join('\n');
        });
    }

    /** 把诊断结论附加到错误对象上（供提示框展示） */
    function attachDiagnosis(err) {
        if (!(err instanceof NetworkError)) return Promise.resolve(err);
        return diagnoseConnectivity().then(function (hint) {
            err.hint = hint;
            err.message = '无法连接服务器\n\n' + hint;
            return err;
        });
    }

    /**
     * 公开的连通性探测，供页面上的「重新检测」按钮调用
     */
    function probeConnectivity() {
        return diagnoseConnectivity();
    }

    /** 服务不可达时在页面顶部显示一条可复制的诊断横幅 */
    var _diagBannerId = 'lfConnDiag';

    function showConnectivityBanner(hint) {
        if (typeof document === 'undefined' || !document.body) return;

        var el = document.getElementById(_diagBannerId);
        if (!el) {
            el = document.createElement('div');
            el.id = _diagBannerId;
            el.setAttribute('role', 'alert');
            el.style.cssText = [
                'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:2147483646',
                'background:#b71c1c', 'color:#fff', 'padding:14px 18px',
                'font-size:13px', 'line-height:1.6', 'white-space:pre-line',
                'max-height:60vh', 'overflow:auto', 'text-align:left',
                'box-shadow:0 4px 16px rgba(0,0,0,0.3)'
            ].join(';');

            var button = document.createElement('button');
            button.textContent = '重新检测';
            button.style.cssText = [
                'margin-top:10px', 'margin-right:10px', 'padding:6px 14px',
                'border:none', 'border-radius:6px', 'cursor:pointer',
                'background:#fff', 'color:#b71c1c', 'font-weight:600'
            ].join(';');
            button.onclick = function () {
                button.disabled = true;
                button.textContent = '检测中…';
                probeConnectivity().then(function (text) {
                    var body = document.getElementById(_diagBannerId + '-text');
                    if (body) body.textContent = text;
                    button.disabled = false;
                    button.textContent = '重新检测';
                });
            };

            var close = document.createElement('button');
            close.textContent = '关闭';
            close.style.cssText = button.style.cssText;
            close.onclick = function () { hideConnectivityBanner(); };

            var body = document.createElement('div');
            body.id = _diagBannerId + '-text';
            body.textContent = hint;

            el.appendChild(body);
            el.appendChild(button);
            el.appendChild(close);
            document.body.appendChild(el);
        } else {
            var existing = document.getElementById(_diagBannerId + '-text');
            if (existing) existing.textContent = hint;
        }
    }

    function hideConnectivityBanner() {
        var el = document.getElementById(_diagBannerId);
        if (el && el.parentNode) el.parentNode.removeChild(el);
    }

    /**
     * 统一请求
     * @param {string} path   形如 '/faces' 或 '/sessions/checkin'
     * @param {object} [opts] { method, body, form, raw, signal }
     */
    function request(path, opts) {
        opts = opts || {};
        var url = path.indexOf('http') === 0 ? path : BASE + path;
        var headers = Object.assign({}, opts.headers || {});
        var init = { method: opts.method || 'GET', headers: headers };

        if (opts.form) {
            init.body = opts.form;            // FormData：不要设置 Content-Type
        } else if (opts.body !== undefined) {
            headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.body);
        }
        if (opts.signal) init.signal = opts.signal;

        return window.fetch(url, init).then(function (response) {
            if (opts.raw) {
                if (!response.ok) {
                    throw new ApiError('HTTP ' + response.status, response.status);
                }
                return response;
            }

            return response.text().then(function (text) {
                var data = parseJsonSafe(text);

                if (response.ok) {
                    return data === null ? {} : data;
                }

                // 统一错误结构：{detail, code, request_id, details?}
                var message = (data && data.detail) || '请求失败（HTTP ' + response.status + '）';
                if (data && data.details && typeof data.details === 'object') {
                    var fieldMessages = Object.keys(data.details).map(function (key) {
                        return key + ': ' + data.details[key];
                    });
                    if (fieldMessages.length) message += ' — ' + fieldMessages.join('；');
                }
                throw new ApiError(message, response.status, data && data.code, data && data.details);
            });
        }).catch(function (err) {
            // 网络层失败：包装为 NetworkError 并补一次连通性探测
            if (err instanceof ApiError) throw err;   // 业务错误原样抛出

            return attachDiagnosis(new NetworkError(err, url)).then(function (netErr) {
                // 显示可复制的完整诊断横幅
                try { showConnectivityBanner(netErr.hint); } catch (e) { /* ignore */ }
                throw netErr;
            });
        });
    }

    var api = {
        BASE: BASE,
        ApiError: ApiError,
        NetworkError: NetworkError,
        diagnoseConnectivity: diagnoseConnectivity,
        probe: probeConnectivity,
        showConnectivityBanner: showConnectivityBanner,
        hideConnectivityBanner: hideConnectivityBanner,
        request: request,
        get: function (path, opts) { return request(path, Object.assign({ method: 'GET' }, opts)); },
        post: function (path, body, opts) { return request(path, Object.assign({ method: 'POST', body: body }, opts)); },
        put: function (path, body, opts) { return request(path, Object.assign({ method: 'PUT', body: body }, opts)); },
        del: function (path, body, opts) {
            // DELETE 带 body 不是所有代理都支持，优先用查询串或 POST 变体
            // 本项目后端 DELETE /faces/{name} 用路径参数，无需 body
            return request(path, Object.assign({ method: 'DELETE', body: body }, opts));
        },
        upload: function (path, formData, opts) {
            return request(path, Object.assign({ method: 'POST', form: formData }, opts));
        },
        url: function (path) { return BASE + path; }
    };

    // ------------------------------------------------------------------ //
    // 提示
    // ------------------------------------------------------------------ //

    function toast(message, isError) {
        var el = document.getElementById('lfToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'lfToast';
            el.setAttribute('role', 'status');
            el.style.cssText = [
                'position:fixed', 'top:20px', 'left:50%',
                'transform:translateX(-50%) translateY(-8px)',
                'padding:12px 22px', 'border-radius:10px', 'font-size:14px',
                'font-weight:500', 'z-index:2147483647', 'max-width:80vw',
                'box-shadow:0 6px 24px rgba(0,0,0,0.18)',
                'opacity:0', 'transition:opacity .18s ease, transform .18s ease',
                'pointer-events:none', 'word-break:break-word',
                // 诊断信息是多行的，需要保留换行
                'white-space:pre-line', 'text-align:left',
                'max-height:70vh', 'overflow:auto'
            ].join(';');
            document.body.appendChild(el);
        }
        el.style.background = isError === false ? '#2e7d32' : '#c62828';
        el.style.color = '#fff';
        // 用 textContent 赋值，避免任何注入风险
        el.textContent = message;
        // 强制重排以便重复调用也有动画
        void el.offsetWidth;
        el.style.opacity = '1';
        el.style.transform = 'translateX(-50%) translateY(0)';

        clearTimeout(el._lfTimer);
        // 内容越长停留越久（最多 20 秒），避免长诊断信息来不及看
        var text = String(message == null ? '' : message);
        var duration = text.length > 120
            ? Math.min(20000, 6000 + text.length * 30)
            : 3200;
        el._lfTimer = setTimeout(function () {
            el.style.opacity = '0';
            el.style.transform = 'translateX(-50%) translateY(-8px)';
        }, duration);
    }

    // ------------------------------------------------------------------ //
    // 日志上报（显式调用）
    // ------------------------------------------------------------------ //

    var log = {
        /** 页面标识，用于服务端日志分类 */
        page: function () {
            var name = window.location.pathname.split('/').pop();
            return name || 'unknown';
        },

        send: function (level, message) {
            // 只上报 error / warn，避免把调试输出灌进服务端日志
            if (level !== 'error' && level !== 'warn') return;
            try {
                window.fetch(BASE + '/logs/frontend', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        level: level,
                        message: String(message).slice(0, 2000),
                        page: this.page()
                    })
                }).catch(function () { /* 上报失败不影响业务 */ });
            } catch (e) { /* ignore */ }
        },

        error: function (message) { this.send('error', message); },
        warn: function (message) { this.send('warn', message); }
    };

    // 兜底捕获未处理错误与 Promise 拒绝
    window.addEventListener('error', function (event) {
        if (event && event.message) {
            log.error(event.message + ' (' + (event.filename || '?') + ':' + (event.lineno || '?') + ')');
        }
    });
    window.addEventListener('unhandledrejection', function (event) {
        var reason = event && event.reason;
        log.error('未处理的 Promise 拒绝: ' + (reason && reason.message ? reason.message : reason));
    });

    // ------------------------------------------------------------------ //
    // 主题
    // ------------------------------------------------------------------ //

    var theme = {
        KEY: 'theme',

        current: function () {
            try { return window.localStorage.getItem(this.KEY) === 'dark' ? 'dark' : 'light'; }
            catch (e) { return 'light'; }
        },

        apply: function (mode) {
            document.body.classList.toggle('dark', mode === 'dark');
        },

        init: function () {
            this.apply(this.current());
        },

        toggle: function () {
            var next = this.current() === 'dark' ? 'light' : 'dark';
            try { window.localStorage.setItem(this.KEY, next); } catch (e) { /* ignore */ }
            this.apply(next);
            // 通知同源 iframe
            try {
                window.dispatchEvent(new StorageEvent('storage', { key: this.KEY, newValue: next }));
            } catch (e) { /* ignore */ }
            return next;
        },

        /** iframe 内调用：接收父页面的主题广播并按需回传 */
        bindInFrame: function () {
            var self = this;
            self.init();
            window.addEventListener('message', function (event) {
                if (event && event.data && event.data.type === 'theme') {
                    self.apply(event.data.dark ? 'dark' : 'light');
                }
            });
            window.addEventListener('storage', function (event) {
                if (event.key === self.KEY) self.apply(self.current());
            });
        }
    };

    // ------------------------------------------------------------------ //
    // 导出
    // ------------------------------------------------------------------ //

    window.LookUFace = {
        api: api,
        toast: toast,
        log: log,
        theme: theme,
        escapeHtml: escapeHtml
    };

    // 向后兼容旧页面里散落的 API_BASE 引用
    window.API_BASE = BASE;
})(window, document);
