/**
 * api.js —— 前端公共基础设施（认证、请求、提示、主题）
 *
 * 引入背景（旧版问题）：
 *  1. 旧版把 `const API_BASE = '/api/v1'` 写在 auth.js 里，靠「所有页面都继承
 *     auth.js」这一隐式约定工作，任何漏引 auth.js 的页面（如 iframe）都会
 *     在运行时抛 `ReferenceError: API_BASE is not defined`。
 *  2. 每个页面各写一遍 fetch + 手动拼 Authorization + 手动判断 401，
 *     错误处理四处漂移。
 *  3. 旧版 auth.js 劫持了 console.log/warn/error 并**无条件**上报服务端，
 *     页面里任何调试输出都会变成服务端日志噪声（实测 manager.js 的调试
 *     输出把日志刷满）。
 *
 * 本文件提供：
 *  - `LookUFace.api`   统一请求封装（自动带 token、统一错误、401 自动登出）
 *  - `LookUFace.auth`  登录态读写（localStorage + 跨页广播）
 *  - `LookUFace.toast` 统一提示（替代散落的 alert）
 *  - `LookUFace.log`   显式日志上报（仅 error/warn 上报，杜绝噪声）
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
    // 认证态
    // ------------------------------------------------------------------ //

    var auth = {
        TOKEN_KEY: 'token',
        USER_KEY: 'user',

        getToken: function () {
            try { return window.localStorage.getItem(this.TOKEN_KEY); } catch (e) { return null; }
        },

        getUser: function () {
            try {
                var raw = window.localStorage.getItem(this.USER_KEY);
                return raw ? JSON.parse(raw) : null;
            } catch (e) { return null; }
        },

        isLoggedIn: function () {
            return !!(this.getToken() && this.getUser());
        },

        setSession: function (token, user) {
            try {
                window.localStorage.setItem(this.TOKEN_KEY, token);
                window.localStorage.setItem(this.USER_KEY, JSON.stringify(user));
            } catch (e) { /* 隐私模式下可能失败，忽略 */ }
            this._broadcast();
        },

        clearSession: function () {
            var previous = this.getToken();
            try {
                window.localStorage.removeItem(this.TOKEN_KEY);
                window.localStorage.removeItem(this.USER_KEY);
            } catch (e) { /* ignore */ }
            this._broadcast(previous);
        },

        /** 通知同源的其他页面（含 iframe）登录态已变化。 */
        _broadcast: function (oldValue) {
            try {
                window.dispatchEvent(new StorageEvent('storage', {
                    key: this.TOKEN_KEY,
                    oldValue: oldValue || null,
                    newValue: this.getToken()
                }));
            } catch (e) {
                // 某些浏览器不允许手工构造 StorageEvent，退化为直接派发自定义事件
                window.dispatchEvent(new CustomEvent('lookuface:auth-changed'));
            }
        },

        displayName: function () {
            var user = this.getUser();
            if (!user) return '用户';
            return user.nickname || (user.email ? user.email.split('@')[0] : '用户');
        }
    };

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

    /**
     * 统一请求。
     * @param {string} path   形如 '/faces' 或 '/sessions/checkin'
     * @param {object} [opts] { method, body, form, raw, signal, skipAuthRedirect }
     */
    function request(path, opts) {
        opts = opts || {};
        var url = path.indexOf('http') === 0 ? path : BASE + path;
        var headers = Object.assign({}, opts.headers || {});
        var init = { method: opts.method || 'GET', headers: headers };

        var token = auth.getToken();
        if (token && !opts.skipAuth) {
            headers['Authorization'] = 'Bearer ' + token;
        }

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

                if (response.status === 401 && !opts.skipAuthRedirect) {
                    auth.clearSession();
                    window.dispatchEvent(new CustomEvent('lookuface:unauthorized'));
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
        });
    }

    var api = {
        BASE: BASE,
        ApiError: ApiError,
        request: request,
        get: function (path, opts) { return request(path, Object.assign({ method: 'GET' }, opts)); },
        post: function (path, body, opts) { return request(path, Object.assign({ method: 'POST', body: body }, opts)); },
        put: function (path, body, opts) { return request(path, Object.assign({ method: 'PUT', body: body }, opts)); },
        del: function (path, body, opts) {
            // DELETE 带 body 不是所有代理都支持，优先用查询串或 POST 变体；
            // 本项目后端 DELETE /faces/{name} 用路径参数，无需 body。
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
                'pointer-events:none', 'word-break:break-word'
            ].join(';');
            document.body.appendChild(el);
        }
        el.style.background = isError === false ? '#2e7d32' : '#c62828';
        el.style.color = '#fff';
        el.textContent = message;
        // 强制重排以便重复调用也有动画
        void el.offsetWidth;
        el.style.opacity = '1';
        el.style.transform = 'translateX(-50%) translateY(0)';

        clearTimeout(el._lfTimer);
        el._lfTimer = setTimeout(function () {
            el.style.opacity = '0';
            el.style.transform = 'translateX(-50%) translateY(-8px)';
        }, 3200);
    }

    // ------------------------------------------------------------------ //
    // 日志上报（显式调用，不再劫持 console）
    // ------------------------------------------------------------------ //

    var log = {
        /** 页面标识，用于服务端日志分类。 */
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

    // 兜底捕获未处理错误与 Promise 拒绝（这才是真正需要的上报）
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

        /** iframe 内调用：接收父页面的主题广播并按需回传。 */
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
        auth: auth,
        toast: toast,
        log: log,
        theme: theme,
        escapeHtml: escapeHtml
    };

    // 向后兼容旧页面里散落的 API_BASE 引用
    window.API_BASE = BASE;
    // 兼容旧的 getCurrentUser()
    window.getCurrentUser = function () { return auth.getUser(); };
})(window, document);
