/**
 * dashboard.js —— 数据看板
 *
 * 依赖：api.js（需先引入）
 *
 * 上方表单补录一条进出场流水（只写历史，不改当前在场状态），
 * 主体展示 KPI 与服务端 matplotlib 渲染的五张多维图表。
 */
(function () {
    'use strict';

    var LF = window.LookUFace;
    var CHART_KEYS = ['user_ranking', 'daily_trend', 'hour_distribution', 'duration_buckets', 'heatmap'];

    // 把 "YYYY-MM-DDTHH:MM" 变成前端可读文案，仅用于提示
    function displayTime(value) {
        return value ? value.replace('T', ' ') : '—';
    }

    // 加载用户下拉
    function loadUsers(select) {
        if (!select) return Promise.resolve();
        select.innerHTML = '<option value="">（加载中…）</option>';
        return LF.api.get('/faces').then(function (data) {
            var faces = (data && data.faces) || [];
            select.innerHTML = faces.map(function (f) {
                return '<option value="' + LF.escapeHtml(f.name) + '">' + LF.escapeHtml(f.name) + '</option>';
            }).join('');
            if (!faces.length) {
                select.innerHTML = '<option value="">（人脸库为空，请先注册）</option>';
            }
        }).catch(function (err) {
            select.innerHTML = '<option value="">（加载失败，点刷新重试）</option>';
            throw err;
        });
    }

    // 拉取统计并渲染 KPI 与图表；图表按当前主题配色
    function loadOverview() {
        var days = document.getElementById('daysRange').value || '7';
        var dark = LF.theme.current() === 'dark' ? '1' : '0';
        return LF.api.get('/stats/overview?days=' + encodeURIComponent(days) + '&dark=' + dark).then(function (data) {
            var kpis = data.kpis || {};
            document.getElementById('kpiVisits').textContent = kpis.today_visits != null ? kpis.today_visits : '—';
            document.getElementById('kpiOnline').textContent = kpis.online_now != null ? kpis.online_now : '—';
            document.getElementById('kpiMinutes').innerHTML = kpis.today_minutes != null
                ? LF.escapeHtml(kpis.today_minutes) + '<span class="kpi-unit">分钟</span>' : '—';
            document.getElementById('kpiAverage').innerHTML = kpis.avg_minutes != null
                ? LF.escapeHtml(kpis.avg_minutes) + '<span class="kpi-unit">分钟</span>' : '—';

            var charts = data.charts || {};
            CHART_KEYS.forEach(function (key) {
                var img = document.getElementById('chart_' + key);
                if (img && charts[key]) img.src = charts[key];
            });
        });
    }

    // 表单提交：补录一条完整区间
    function bindForm() {
        var form = document.getElementById('addForm');
        if (!form) return;
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var name = document.getElementById('recordName').value;
            var checkin = document.getElementById('checkinAt').value;
            var checkout = document.getElementById('checkoutAt').value;
            if (!name || !checkin || !checkout) {
                LF.toast('请填写用户与起止时间', true);
                return;
            }
            // datetime-local 的值后端可直接解析
            LF.api.post('/records', {
                name: name,
                checkin_at: checkin,
                checkout_at: checkout
            }).then(function () {
                LF.toast('已添加：' + name + ' ' + displayTime(checkin) + ' → ' + displayTime(checkout), false);
                form.reset();
                return loadOverview();
            }).catch(function (err) {
                LF.toast(err && err.message ? err.message : '添加失败', true);
            });
        });
    }

    function bindTheme() {
        var themeToggle = document.getElementById('themeToggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', function () {
                LF.theme.toggle();
                // 图表 PNG 是按主题渲染的，切主题后重拉一次让文字配色跟随
                if (typeof loadOverview === 'function') {
                    loadOverview().catch(function () {});
                }
            });
        }
        LF.theme.init();
        window.addEventListener('storage', function (event) {
            if (event.key === LF.theme.KEY) {
                LF.theme.apply(LF.theme.current());
                if (typeof loadOverview === 'function') {
                    loadOverview().catch(function () {});
                }
            }
        });
    }

    function init() {
        bindTheme();
        bindForm();
        loadUsers(document.getElementById('recordName')).catch(function (err) {
            LF.toast(err && err.message ? err.message : '加载用户失败', true);
        });
        loadOverview().catch(function (err) {
            LF.toast(err && err.message ? err.message : '加载统计失败', true);
        });
        document.getElementById('refreshBtn').addEventListener('click', function () {
            loadOverview().catch(function (err) {
                LF.toast(err && err.message ? err.message : '刷新失败', true);
            });
        });
        document.getElementById('daysRange').addEventListener('change', function () {
            loadOverview().catch(function (err) {
                LF.toast(err && err.message ? err.message : '切换失败', true);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
