// manager.js - 人脸库管理页面
var LF = window.LookUFace;
// 依赖：api.js（需先引入）。
/*

*获取人脸列表 调face/list-with-screenshot 接口调db 并且可以按照name/duration/online排序
**对人脸操作 开模态框 关闭时调face/modify(delete) 接口调db 可以接图调face/register
***手动登入暂离登出 开模态框 关闭时调check 接checkservice

initTheme()主题初始化
toggleTheme()主题切换
isLoggedIn()检查是否已登录
checkBackend()检查后端是否存活
updateUIAfterLoginChange() 通过登录状态更新按钮列表
sortFaces() 列表排序
exportToExcel() 导出excel
showModifyModal/closeModifyModal/ 修改模态框操作
confirmModify  调/face/modify 将模态框数据写入db
*loadFaces()	调face/list-with-screenshot获取人脸列表并渲染卡片
renderFaces() 渲染列表
escapeHtml(str)	特殊字符转义
****previewFace(name)获取截图并在弹窗展示
closePreview()	关闭预览弹窗
***deleteFace(name)删除人脸，刷新列表
**modifyFace(name)修改人脸名字，刷新列表
exportToExcel() 导出CSV
sortFaces() 排序
checkinface()/closeCheckinConfirmModal()  打开/关闭登入模态框 关闭时调checkin
showCheckoutModal()/closeCheckoutModal()  打开/关闭登出模态框 可选择暂离/签退 关闭时调checkout
*/




let currentSort = 'default'; //排序方式
let facesData = []; //人脸列表
let modifyTargetName = ''; //要修改的名字
let checkoutTargetName = ''; //登出的脸
let checkinTargetName = ''; //登入的脸



//检查是否已登录
function isLoggedIn() {
    return LF.auth.isLoggedIn();
}


//检查后端状态 fetch health接口
async function checkBackend() {
    const dot = document.getElementById('statusDot');
    const label = document.getElementById('statusText');
    try {
        const health = await LF.api.get('/health', { skipAuth: true });
        if (dot) dot.className = 'status-dot connected';
        if (label) label.innerText = health.status === 'healthy' ? '已连接' : '降级运行';
        return health.status === 'healthy';
    } catch (err) {
        if (dot) dot.className = 'status-dot offline';
        if (label) label.innerText = '未连接';
        LF.log.warn('后端健康检查失败: ' + err.message);
        return false;
    }
}

// 登录状态变化时刷新按钮和列表
function updateUIAfterLoginChange() {
    const btnContainer = document.getElementById('sortBtnContainer');
    if (btnContainer) {
        btnContainer.style.display = isLoggedIn() ? 'flex' : 'none';
    }
    if (isLoggedIn()) {
        loadFaces();
    } else {
        document.getElementById('facesGrid').innerHTML = `
            <div class="empty-state-large">
                <p>请先登录查看人脸库</p>
                <button id="loginPromptBtn" class="btn btn-primary" style="margin-top: 16px;">立即登录</button>
            </div>
        `;
        document.getElementById('totalCount').innerText = '0';
        document.getElementById('facesCount').innerText = '0 条记录';
        const loginBtn = document.getElementById('loginPromptBtn');
        if (loginBtn) {
            loginBtn.onclick = () => {
                if (typeof showLoginModal === 'function') showLoginModal();
            };
        }
    }
}

//监听登录状态变化
window.addEventListener('storage', function(e) {
    if (e.key === 'token' || e.key === 'user') {
        updateUIAfterLoginChange();
    }
});

//排序按钮 有name/online/duration排序
function sortFaces(faces, sortType) {
    const sorted = [...faces];
    switch (sortType) {
        case 'name':
            sorted.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
            break;
        case 'online':
            sorted.sort((a, b) => {
                if (a.is_online && !b.is_online) return -1;
                if (!a.is_online && b.is_online) return 1;
                if (a.is_online && b.is_online) {
                    const timeA = a.checkin_time ? new Date(a.checkin_time).getTime() : 0;
                    const timeB = b.checkin_time ? new Date(b.checkin_time).getTime() : 0;
                    return timeA - timeB;
                }
                return 0;
            });
            break;
        case 'duration':
            sorted.sort((a, b) => (b.online_time || 0) - (a.online_time || 0));
            break;
    }
    return sorted;
}

//excel导出
function exportToExcel() {
    if (!facesData.length) { alert('没有数据可导出'); return; }

    let csv = '\uFEFF序号,姓名,注册时间,在线状态,签到时间,总在线时长(分钟)\n';
    facesData.forEach((face, i) => {
        csv += `${i + 1},${face.name},${face.register_time || ''},${face.is_online ? '在线' : '离线'},${face.checkin_time || ''},${face.online_time || 0}\n`;
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `人脸库_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}


//打开修改弹窗
function showModifyModal(name) {
    modifyTargetName = name;
    const face = facesData.find(f => f.name === name);
    if (!face) return;

    document.getElementById('modifyName').value = face.name;
    document.getElementById('modifyRegisterTime').value = face.register_time
        ? new Date(face.register_time).toISOString().slice(0, 16)
        : '';
    document.getElementById('modifyImage').value = '';
    document.getElementById('modifyModal').style.display = 'flex';
}

// 关闭修改弹窗
function closeModifyModal() {
    const modal = document.getElementById('modifyModal');
    if (modal) modal.style.display = 'none';
}

// 确认修改
async function confirmModify() {
    const newName = document.getElementById('modifyName').value.trim();
    const newRegisterTime = document.getElementById('modifyRegisterTime').value;
    const imageFile = document.getElementById('modifyImage').files[0];

    if (!newName) { LF.toast('请输入名字', true); return; }

    try {
        await LF.api.put('/faces/' + encodeURIComponent(modifyTargetName), {
            old_name: modifyTargetName,
            new_name: newName,
            // datetime-local 是本地时间，补上秒与 ISO 格式
            register_time: newRegisterTime ? newRegisterTime + ':00' : null
        });
    } catch (err) {
        LF.toast(err.message || '修改失败', true);
        return;
    }

    if (imageFile) {
        const formData = new FormData();
        formData.append('file', imageFile);
        formData.append('name', newName);
        try {
            const regResult = await LF.api.upload('/faces/register', formData);
            if (!regResult.success) {
                LF.toast('图片更新失败：' + (regResult.message || '未知错误'), true);
            }
        } catch (err) {
            LF.toast('图片更新失败：' + err.message, true);
        }
    }

    LF.toast('已保存', false);
    closeModifyModal();
    loadFaces();
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModifyModal();
});

//加载
async function loadFaces() {
    const grid = document.getElementById('facesGrid');

    if (!isLoggedIn()) {
        updateUIAfterLoginChange();
        return;
    }

    grid.innerHTML = '<div class="loading-state">加载中...</div>';

    try {
        const data = await LF.api.get('/faces');
        facesData = data.faces || [];
        const onlineCount = facesData.filter(f => f.is_online).length;
        const onlineEl = document.getElementById('onlineCount');
        if (onlineEl) onlineEl.innerText = onlineCount;
        renderFaces(facesData);
    } catch (err) {
        if (err.status === 401) {
            LF.auth.clearSession();
            updateUIAfterLoginChange();
            return;
        }
        LF.log.error('加载人脸列表失败: ' + err.message);
        grid.innerHTML = '<div class="error-state">加载失败，请检查后端服务</div>';
    }
}

//显示人脸列表
function renderFaces(faces) {
    const grid = document.getElementById('facesGrid');
    const sorted = sortFaces(faces, currentSort);

    document.getElementById('totalCount').innerText = sorted.length || 0;
    document.getElementById('facesCount').innerText = `${sorted.length || 0} 条记录`;

    if (!sorted.length) {
        grid.innerHTML = `
            <div class="empty-state-large">
                <div class="empty-icon">👥</div>
                <p>暂无注册人脸</p>
                <p class="empty-hint">前往"实时识别"页面注册人脸</p>
            </div>
        `;
        return;
    }

    let html = '';
    sorted.forEach((face, i) => {
        const registerTime = face.register_time ? new Date(face.register_time).toLocaleString() : '未知';
        const onlineBadge = face.is_online
            ? (face.is_away
                ? '<span style="color:#ff9800; font-weight:600;">● 暂离中</span>'
                : '<span style="color:#4caf50; font-weight:600;">● 在线</span>')
            : '<span style="color:#999;">● 离线</span>';
        const checkinTime = face.checkin_time ? new Date(face.checkin_time).toLocaleString() : '未签到';
        const totalMinutes = face.online_time || 0;
        const durationStr = `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;

        // 需要重新注册的特征（旧版 pickle 格式）给出显式提示
        const staleBadge = face.features_current === false
            ? ' <span style="color:#e53935; font-weight:600;" title="旧版特征格式，需重新注册后才能被识别">● 需重新注册</span>'
            : '';

        const onlineBtn = face.is_online
            ? `<button class="btn-checkout" data-action="checkout">签退</button>`
            : `<button class="btn-checkin" data-action="checkin">签到</button>`;

        // 只有正在计时的人才有「账」可看；离线后不再显示账单入口
        // （离线时接口也会返回 payable=false 并留空二维码与支付链接）。
        const billingBtn = face.is_online
            ? `<button class="btn-billing" data-action="billing">账单</button>`
            : '';

        // 注意：名字只进 data-name 属性（经 escapeHtml 处理），绝不拼进 onclick。
        // 旧版把名字插进 onclick="f('...')" 且只做了 \' 转义，可被注入。
        html += `
            <div class="face-card-item" data-name="${escapeHtml(face.name)}">
                <div style="font-weight:700; color:var(--text-secondary); min-width:30px;"># ${i + 1}</div>
                <div class="face-avatar">
                    <div class="avatar-placeholder">👤</div>
                </div>
                <div class="face-details">
                    <div class="face-name">${escapeHtml(face.name)} ${onlineBadge}${staleBadge}</div>
                    <div class="face-time">签到: ${escapeHtml(checkinTime)} | 注册: ${escapeHtml(registerTime)} | 总在线: ${durationStr}</div>
                </div>
                <div class="face-actions">
                    <button class="btn-preview" data-action="preview">预览</button>
                    <button class="btn-modify" data-action="modify">修改</button>
                    ${onlineBtn}
                    ${billingBtn}
                    <button class="btn-delete" data-action="delete">删除</button>
                </div>
            </div>
        `;
    });
    grid.innerHTML = html;
}

// 事件委托：所有按钮通过 data-action + 最近的 [data-name] 定位目标人脸。
// 这样用户数据永远不进入 HTML 属性里的可执行上下文。
document.getElementById('facesGrid').addEventListener('click', function (event) {
    const button = event.target.closest('button[data-action]');
    if (!button) return;

    const card = button.closest('[data-name]');
    if (!card) return;
    const name = card.getAttribute('data-name');

    switch (button.getAttribute('data-action')) {
        case 'preview': previewFace(name); break;
        case 'modify': showModifyModal(name); break;
        case 'checkin': checkinFace(name); break;
        case 'checkout': showCheckoutModal(name); break;
        case 'delete': deleteFace(name); break;
        case 'billing': showBillingModal(name); break;
    }
});

//特殊字符转义
function escapeHtml(str) {
    // 统一使用 api.js 的实现，旧版漏掉了引号与单引号
    return LF.escapeHtml(str);
}

//预览
async function previewFace(name) {
    if (!isLoggedIn()) {
        alert('请先登录');
        if (typeof showLoginModal === 'function') showLoginModal();
        return;
    }

    const modal = document.getElementById('previewModal');
    const img = document.getElementById('modalImg');
    const info = document.getElementById('modalInfo');
    modal.style.display = 'flex';
    info.innerHTML = '加载中...';
    img.style.display = 'none';

    try {
        const response = await LF.api.post('/faces/screenshot', { name }, { raw: true });
        const blob = await response.blob();

        // 释放上一次的 objectURL，避免内存泄漏
        if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
        const url = URL.createObjectURL(blob);
        img.dataset.objectUrl = url;

        img.src = url;
        img.style.display = 'block';
        info.innerHTML = ` ${escapeHtml(name)} 注册时的照片 | 点击图片放大`;
        img.onclick = () => window.open(url, '_blank');
    } catch (err) {
        if (err.status === 401) {
            LF.toast('请先登录', true);
            closePreview();
            if (typeof showLoginModal === 'function') showLoginModal();
            return;
        }
        info.innerHTML = ` 加载失败: ${escapeHtml(err.message)}`;
    }
}

//关闭预览
function closePreview() {
    const modal = document.getElementById('previewModal');
    const img = document.getElementById('modalImg');
    modal.style.display = 'none';
    // 只 revoke 我们自己创建的 objectURL（旧版直接 revoke img.src，
    // 若 src 是普通路径会抛错，且容易重复 revoke）
    if (img.dataset.objectUrl) {
        URL.revokeObjectURL(img.dataset.objectUrl);
        delete img.dataset.objectUrl;
    }
    img.src = '';
}

//删 调用face.delete
async function deleteFace(name) {
    if (!isLoggedIn()) {
        LF.toast('请先登录', true);
        if (typeof showLoginModal === 'function') showLoginModal();
        return;
    }

    if (!confirm(`确定要删除 "${name}" 吗？此操作不可恢复。`)) return;

    try {
        await LF.api.del('/faces/' + encodeURIComponent(name));
        LF.toast(`已删除 ${name}`, false);
        loadFaces();
    } catch (err) {
        if (err.status === 401) {
            LF.toast('请先登录', true);
            if (typeof showLoginModal === 'function') showLoginModal();
            return;
        }
        LF.toast('删除失败：' + err.message, true);
    }
}



//登入
function checkinFace(name) {
    checkinTargetName = name;
    document.getElementById('checkinTargetName').textContent = name;
    document.getElementById('checkinConfirmModal').style.display = 'flex';
}

function closeCheckinConfirmModal() {
    document.getElementById('checkinConfirmModal').style.display = 'none';
}

document.getElementById('confirmCheckinBtn').addEventListener('click', async () => {
    // 说明：旧版要传 request_times=999 来绕过前端的帧计数去抖；
    // 新接口不再接受该字段，去抖由服务端负责。
    const button = document.getElementById('confirmCheckinBtn');
    button.disabled = true;
    try {
        const data = await LF.api.post('/sessions/checkin', { name: checkinTargetName });
        LF.toast(data.message, data.success === false);
    } catch (err) {
        LF.toast(err.message || '签到失败', true);
    } finally {
        button.disabled = false;
        closeCheckinConfirmModal();
        loadFaces();
    }
});

function showCheckoutModal(name) {
    checkoutTargetName = name;
    const face = facesData.find(f => f.name === name);
    if (!face) return;

    // ── 关键修复 ──────────────────────────────────────────────────────────
    // 旧版把「在线且未暂离」默认勾选为「暂离」：
    //     const defaultAway = face.is_online && !face.is_away;
    //     document.getElementById('checkoutAway').checked = defaultAway;
    // 而「暂离」走的是不结算的分支，返回的 session_minutes / amount 必然为 0，
    // 于是用户点「确认签退」看到的是「0 分钟 0 元」；
    // 第二次打开时标题变成「签退结算」但复选框**没有复位**，仍是勾选状态，
    // 再次提交依然走暂离分支 —— 表现为「无论多久都是 0 分钟 0 元」。
    //
    // 现在：默认永远是「签退结算」（复选框不勾），暂离必须由用户显式勾选。
    const awayCheckbox = document.getElementById('checkoutAway');
    if (awayCheckbox) awayCheckbox.checked = false;

    const isOnline = !!face.is_online;
    const isAway = !!face.is_away;

    document.getElementById('checkoutCheckinTime').value = face.checkin_time
        ? new Date(face.checkin_time).toLocaleString()
        : '未签到';

    // 在场时间改为只读展示，且由服务端 /sessions/status 回填 ——
    // 前端本地根据 checkin_time 算出来的值可能与服务端不一致（时钟、暂离扣除）。
    const durationEl = document.getElementById('checkoutDuration');
    if (durationEl) durationEl.value = isOnline ? '计算中…' : '未在计时';

    const title = document.getElementById('checkoutTitle');
    const button = document.getElementById('confirmCheckoutBtn');
    const hint = document.getElementById('checkoutHint');

    if (!isOnline) {
        // 未在计时：既不能签退也不能暂离
        if (title) title.textContent = '未在计时';
        if (button) {
            button.textContent = '未在计时，无需签退';
            button.disabled = true;
        }
        if (hint) hint.textContent = '该人脸当前没有进行中的会话。请先签到后再操作。';
    } else {
        if (title) title.textContent = isAway ? '暂离中 · 签退结算' : '签退结算';
        if (button) {
            button.textContent = '确认签退结算';
            button.disabled = false;
        }
        if (hint) {
            hint.textContent = '在场时长与金额完全由服务端按签到时间计算，前端不参与。'
                + '勾选「仅标记暂离」则不会结算，归位后继续计时。';
        }
    }

    document.getElementById('checkoutModal').style.display = 'flex';

    // 用服务端数据回填在场时间与明细
    if (isOnline) {
        LF.api.post('/sessions/status', { name: name })
            .then(function (status) {
                if (checkoutTargetName !== name) return;  // 期间切换了对象
                if (durationEl && status.is_online) {
                    durationEl.value = formatMinutes(status.elapsed_minutes)
                        + '（计费 ' + status.preview_minutes + ' 分钟）';
                }
                if (hint) {
                    hint.textContent =
                        '在场 ' + formatMinutes(status.elapsed_minutes)
                        + (status.away_minutes ? '，其中暂离 ' + formatMinutes(status.away_minutes) : '')
                        + '；计费 ' + status.preview_minutes + ' 分钟，应付 ¥ '
                        + (status.amount || 0).toFixed(2)
                        + '。勾选「仅标记暂离」则不结算。';
                }
                if (isAway || status.is_away) {
                    if (title) title.textContent = '暂离中 · 签退结算';
                }
            })
            .catch(function () {
                if (durationEl) durationEl.value = '暂时取不到（以签退结果为准）';
            });
    }
}

/** 把分钟数格式化成「X 小时 Y 分钟」。 */
function formatMinutes(totalMinutes) {
    const total = Math.max(0, Math.floor(Number(totalMinutes) || 0));
    const hours = Math.floor(total / 60);
    const mins = total % 60;
    return hours ? (hours + ' 小时 ' + mins + ' 分钟') : (mins + ' 分钟');
}



function closeCheckoutModal() {
    document.getElementById('checkoutModal').style.display = 'none';
}

document.getElementById('confirmCheckoutBtn').addEventListener('click', async () => {
    const away = document.getElementById('checkoutAway')?.checked || false;
    const button = document.getElementById('confirmCheckoutBtn');
    button.disabled = true;

    try {
        // 时长与金额完全由服务端按数据库时间戳计算，前端不参与。
        const data = await LF.api.post('/sessions/checkout', {
            name: checkoutTargetName,
            away: away
        });

        // 必须区分「暂离」与「签退结算」：
        // 暂离不结算，此时 session_minutes / amount 必然是 0，
        // 若按签退文案展示就会误导为「0 分钟 0 元」。
        const isAwayAction = data.is_away_action === true || away;
        const settled = data.settled === true;

        if (data.success === false) {
            LF.toast(data.message, true);
        } else if (isAwayAction && !settled) {
            LF.toast(
                data.message + '（暂离不结算，归位后继续计时）',
                false
            );
        } else if (settled) {
            LF.toast(
                `签退成功：在场 ${formatMinutes(data.elapsed_minutes)}` +
                (data.away_minutes ? `（其中暂离 ${formatMinutes(data.away_minutes)}）` : '') +
                `，计费 ${data.session_minutes} 分钟，应付 ¥ ${(data.amount || 0).toFixed(2)}`,
                false
            );
        } else {
            LF.toast(data.message, false);
        }
    } catch (err) {
        LF.toast(err.message || '操作失败', true);
    } finally {
        button.disabled = false;
        closeCheckoutModal();
        loadFaces();
    }
});


/* ==================== 账单 ==================== */

/**
 * 生成并展示账单二维码。
 *
 * 时长与金额完全由服务端按签到时间计算（POST /billing/quote 只传姓名），
 * 旧版是把前端输入框里的「本次在线时长」当参数传给 /check/pay，
 * 既能免单也能超收，且前端根本没有调用入口。
 */
/**
 * 展示账单：**在场时间 + 应付价格**。
 *
 * 按需求：只有存在应付金额时才显示二维码与支付链接；
 * 金额为 0（未计时 / 不足一分钟 / 已签退离线）时，付款区整体隐藏，
 * 只显示在场时间与价格说明 —— 避免"0 元也能出账"。
 */
async function showBillingModal(name) {
    const modal = document.getElementById('billingModal');
    if (!modal) return;

    const paySection = document.getElementById('billingPaySection');
    const noPaySection = document.getElementById('billingNoPaySection');
    const noPayNotice = document.getElementById('billingNoPayNotice');
    const qrcode = document.getElementById('billingQrcode');
    const hint = document.getElementById('billingQrcodeHint');
    const payBtn = document.getElementById('billingPayLinkBtn');
    const summaryEl = document.getElementById('billingSummary');
    const awayGroup = document.getElementById('billingAwayGroup');

    // 打开时先清空，避免显示上一个人的残留
    document.getElementById('billingName').value = name;
    document.getElementById('billingTitle').textContent = name + ' 的账单';
    document.getElementById('billingElapsed').value = '计算中…';
    document.getElementById('billingMinutes').value = '计算中…';
    document.getElementById('billingAmount').value = '计算中…';
    document.getElementById('billingAway').value = '—';
    if (summaryEl) summaryEl.textContent = '';
    if (awayGroup) awayGroup.style.display = 'none';
    paySection.style.display = 'none';
    noPaySection.style.display = 'none';
    qrcode.style.display = 'none';
    qrcode.removeAttribute('src');
    hint.textContent = '';
    payBtn.disabled = true;
    payBtn.dataset.payUrl = '';
    modal.style.display = 'flex';

    try {
        const quote = await LF.api.post('/billing/quote', { name: name });

        document.getElementById('billingElapsed').value = quote.is_online
            ? formatMinutes(quote.elapsed_minutes)
            : (quote.elapsed_minutes ? formatMinutes(quote.elapsed_minutes) : '未在计时');
        document.getElementById('billingAway').value = formatMinutes(quote.away_minutes);
        if (awayGroup) awayGroup.style.display = quote.away_minutes > 0 ? '' : 'none';

        document.getElementById('billingMinutes').value =
            quote.minutes + ' 分钟（单价 ' + (quote.rate_per_minute || 0).toFixed(2) + ' 元/分钟）';
        document.getElementById('billingAmount').value = '¥ ' + (quote.amount || 0).toFixed(2);

        if (summaryEl) summaryEl.textContent = quote.summary || '';

        if (quote.payable && quote.qrcode_url) {
            // 有应付金额：开放二维码与支付链接
            qrcode.src = quote.qrcode_url;
            qrcode.style.display = 'block';
            hint.textContent = '扫码支付 ¥ ' + (quote.amount || 0).toFixed(2);
            payBtn.dataset.payUrl = quote.pay_url || '';
            payBtn.disabled = !quote.pay_url;
            paySection.style.display = '';
            noPaySection.style.display = 'none';
        } else {
            // 无应付金额：不出二维码、不给支付链接
            paySection.style.display = 'none';
            noPaySection.style.display = '';
            if (noPayNotice) {
                noPayNotice.textContent = quote.payable === false
                    ? (quote.summary || '当前没有应付金额。')
                    : '当前没有应付金额。';
            }
            qrcode.style.display = 'none';
            qrcode.removeAttribute('src');
            hint.textContent = '';
            payBtn.dataset.payUrl = '';
            payBtn.disabled = true;
        }
    } catch (err) {
        document.getElementById('billingElapsed').value = '—';
        document.getElementById('billingMinutes').value = '—';
        document.getElementById('billingAmount').value = '—';
        if (summaryEl) summaryEl.textContent = '获取账单失败：' + err.message;
        paySection.style.display = 'none';
        noPaySection.style.display = '';
        if (noPayNotice) noPayNotice.textContent = '获取账单失败：' + err.message;
        LF.log.error('生成账单失败: ' + err.message);
    }
}

function closeBillingModal() {
    const modal = document.getElementById('billingModal');
    if (modal) modal.style.display = 'none';
}

document.getElementById('billingPayLinkBtn')?.addEventListener('click', function () {
    const url = this.dataset.payUrl;
    if (url) window.open(url, '_blank', 'noopener');
});

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeBillingModal();
});

window.showBillingModal = showBillingModal;
window.closeBillingModal = closeBillingModal;

// 页面可见性变化时刷新
document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible') {
        updateUIAfterLoginChange();
    }
});

document.addEventListener('click', function(e) {
    const modal = document.getElementById('previewModal');
    if (e.target === modal) closePreview();
});

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closePreview();
});

async function init() {
    await checkBackend();
    updateUIAfterLoginChange();
}

document.getElementById('refreshBtn').onclick = () => loadFaces();

// 每30秒自动刷新
setInterval(() => {
    if (document.visibilityState === 'visible') {
        updateUIAfterLoginChange();
    }
}, 30000);

// 「仅标记暂离」勾选状态变化时，同步标题与按钮文案。
// 旧版这里改的是 label 文案（for="checkoutDuration"），但该 label 在新结构里
// 已改为只读展示，因此改为同步真正会变化的两处：标题与确认按钮。
document.getElementById('checkoutAway')?.addEventListener('change', function () {
    const title = document.getElementById('checkoutTitle');
    const button = document.getElementById('confirmCheckoutBtn');
    const hint = document.getElementById('checkoutHint');
    const face = facesData.find(f => f.name === checkoutTargetName);
    const isAwayNow = !!(face && face.is_away);

    if (this.checked) {
        if (title) title.textContent = '标记暂离';
        if (button) button.textContent = '确认暂离（不结算）';
        if (hint) hint.textContent = '暂离不会结算、也不会清零计时；归位后继续累计。'
            + '注意：暂离超过超时时长会被自动签退结算。';
    } else {
        if (title) title.textContent = isAwayNow ? '暂离中 · 签退结算' : '签退结算';
        if (button) button.textContent = '确认签退结算';
        if (hint) hint.textContent = '在场时长与金额完全由服务端按签到时间计算，前端不参与。'
            + '勾选「仅标记暂离」则不会结算，归位后继续计时。';
    }
});

init();

window.previewFace = previewFace;
window.closePreview = closePreview;
window.deleteFace = deleteFace;