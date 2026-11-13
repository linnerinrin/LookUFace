/**
 * settings.js —— 场地设置
 *
 * 依赖：api.js（需先引入）
 *
 * 后端接口：
 *   GET  /api/v1/settings  -> 当前生效值 + persisted + env_file + host
 *   PUT  /api/v1/settings  -> 局部更新（只传要改的字段）
 *
 * 改动由后端「改运行时 + 写回 .env」两步完成；若 .env 不可写，
 * 后端仍会让值在本次运行中生效，但会在响应里回 persisted=false，
 * 本页据此给出「重启后会回到旧值」的提示
 */
(function () {
    'use strict';

    var LF = window.LookUFace;

    // 字段 -> 输入框 id，顺序与页面一致
    var FIELDS = [
        ['billing_rate_per_minute', 'billingRate'],
        ['max_session_minutes', 'maxSession'],
        ['away_timeout_minutes', 'awayTimeout'],
        ['checkin_debounce_seconds', 'checkinDebounce'],
        ['checkout_debounce_seconds', 'checkoutDebounce'],
        ['recognize_threshold', 'recognizeThreshold'],
        ['face_conf_threshold', 'faceConfThreshold']
    ];

    var statusEl = null;

    function el(id) {
        return document.getElementById(id);
    }

    function setStatus(html) {
        if (statusEl) statusEl.innerHTML = html;
    }

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
        });
    }

    /** 把后端返回的值写进输入框 */
    function fillForm(data) {
        FIELDS.forEach(function (pair) {
            var input = el(pair[1]);
            if (!input) return;
            var value = data[pair[0]];
            input.value = value == null ? '' : value;
        });
    }

    function describe(persisted, envFile, host) {
        var parts = [];
        if (persisted) {
            parts.push('<span class="ok">已写入 ' + esc(envFile) + '，重启后仍然保留。</span>');
        } else {
            parts.push('<span class="warn">已在当前进程中生效，但写回 .env 失败（文件或目录不可写），'
                + '重启后会回到旧值。</span>');
        }
        parts.push('服务监听地址：' + esc(host));
        if (host === '0.0.0.0' || host === '::') {
            parts.push('<span class="warn">注意：系统没有登录校验，监听 0.0.0.0 意味着同网段任何人都能'
                + '查看人脸库、修改账单与这些设置。</span>');
        }
        return parts.join('<br>');
    }

    /** 读当前生效值 */
    async function loadSettings(quiet) {
        try {
            var data = await LF.api.get('/settings');
            fillForm(data);
            setStatus(describe(data.persisted, data.env_file, data.host));
            if (!quiet) LF.toast('已读取当前设置', false);
        } catch (err) {
            setStatus('<span class="warn">读取失败：' + esc(err.message) + '</span>');
            LF.log.warn('读取场地设置失败: ' + err.message);
        }
    }

    /** 收集表单里的值并全量提交 */
    function collect() {
        var payload = {};
        FIELDS.forEach(function (pair) {
            var input = el(pair[1]);
            if (!input) return;
            var raw = String(input.value == null ? '' : input.value).trim();
            if (raw === '') return;
            var number = Number(raw);
            if (!isFinite(number)) {
                throw new Error('「' + pair[1] + '」不是有效数字：' + raw);
            }
            payload[pair[0]] = number;
        });
        return payload;
    }

    async function save() {
        var payload;
        try {
            payload = collect();
        } catch (err) {
            LF.toast(err.message, true);
            return;
        }

        var btn = el('saveBtn');
        if (btn) btn.disabled = true;
        try {
            var data = await LF.api.put('/settings', payload);
            fillForm(data);
            setStatus(describe(data.persisted, data.env_file, data.host));
            LF.toast('已保存，立即生效', false);
        } catch (err) {
            LF.toast(err.message || '保存失败', true);
            LF.log.warn('保存场地设置失败: ' + err.message);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function bind() {
        statusEl = el('settingsStatus');

        var saveBtn = el('saveBtn');
        if (saveBtn) saveBtn.addEventListener('click', save);

        var reloadBtn = el('reloadBtn');
        if (reloadBtn) reloadBtn.addEventListener('click', function () { loadSettings(true); });

        var themeToggle = el('themeToggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', function () { LF.theme.toggle(); });
        }
        LF.theme.init();

        loadSettings(true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
