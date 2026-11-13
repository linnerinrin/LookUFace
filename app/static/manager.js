// manager.js - 人脸库管理页面
var LF = window.LookUFace;
// 依赖：api.js（需先引入）
/*

*获取人脸列表 调face/list-with-screenshot 接口调db 并且可以按照name/duration/online排序
**对人脸操作 开模态框 关闭时调face/modify(delete) 接口调db 可以接图调face/register
***手动登入暂离登出 开模态框 关闭时调check 接checkservice

initTheme()主题初始化
toggleTheme()主题切换
checkBackend()检查后端是否存活
reloadFaces() 重新拉取人脸列表
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

// 刷新人脸列表
function reloadFaces() {
    loadFaces();
}

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

    grid.innerHTML = '<div class="loading-state">加载中...</div>';

    try {
        const data = await LF.api.get('/faces');
        facesData = data.faces || [];
        renderFaces(facesData);
    } catch (err) {
        LF.log.error('加载人脸列表失败: ' + err.message);
        grid.innerHTML = '<div class="error-state">加载失败，请检查后端服务</div>';
    }
}

//显示人脸列表
function renderFaces(faces) {
    const grid = document.getElementById('facesGrid');
    const sorted = sortFaces(faces, currentSort);

    // 更新「人脸列表」标题旁的条数
    setTextIfPresent('facesCount', `${sorted.length || 0} 条记录`);

    if (!sorted.length) {
        grid.innerHTML = `
            <div class="empty-state-large">
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
                ? '<span class="badge badge-away"><i class="badge-dot"></i>暂离中</span>'
                : '<span class="badge badge-online"><i class="badge-dot"></i>在线</span>')
            : '<span class="badge badge-offline"><i class="badge-dot"></i>离线</span>';
        const checkinTime = face.checkin_time ? new Date(face.checkin_time).toLocaleString() : '未签到';
        const totalMinutes = face.online_time || 0;
        const durationStr = `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;

        // 需要重新注册的特征（旧版 pickle 格式）给出显式提示
        const staleBadge = face.features_current === false
            ? ' <span class="badge badge-stale" title="旧版特征格式，需重新注册后才能被识别">需重新注册</span>'
            : '';

        const onlineBtn = face.is_online
            ? `<button class="btn-checkout" data-action="checkout">签退</button>`
            : `<button class="btn-checkin" data-action="checkin">签到</button>`;

        // 名字只进 data-name 属性（经 escapeHtml 处理），绝不拼进 onclick
        // 头像位置留空占位，维持卡片内各行的对齐间距
        html += `
            <div class="face-card-item" data-name="${escapeHtml(face.name)}">
                <div style="font-weight:700; color:var(--text-secondary); min-width:30px;"># ${i + 1}</div>
                <div class="face-avatar-blank"></div>
                <div class="face-details">
                    <div class="face-name">${escapeHtml(face.name)} ${onlineBadge}${staleBadge}</div>
                    <div class="face-time">签到: ${escapeHtml(checkinTime)} | 注册: ${escapeHtml(registerTime)} | 总在线: ${durationStr}</div>
                </div>
                <div class="face-actions">
                    <button class="btn-preview" data-action="preview">预览</button>
                    <button class="btn-modify" data-action="modify">修改</button>
                    ${onlineBtn}
                    <button class="btn-delete" data-action="delete">删除</button>
                </div>
            </div>
        `;
    });
    grid.innerHTML = html;
}

// 事件委托：所有按钮通过 data-action + 最近的 [data-name] 定位目标人脸，
// 用户数据不进入 HTML 属性里的可执行上下文
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
    }
});

//特殊字符转义
function escapeHtml(str) {
    // 统一使用 api.js 的实现
    return LF.escapeHtml(str);
}

/**
 * 安全地给元素设置文字：元素不存在时跳过，避免 null 抛错中断后续渲染
 */
function setTextIfPresent(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerText = value;
}

//预览
async function previewFace(name) {
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
        info.innerHTML = ` 加载失败: ${escapeHtml(err.message)}`;
    }
}

//关闭预览
function closePreview() {
    const modal = document.getElementById('previewModal');
    const img = document.getElementById('modalImg');
    modal.style.display = 'none';
    // 只 revoke 我们自己创建的 objectURL
    if (img.dataset.objectUrl) {
        URL.revokeObjectURL(img.dataset.objectUrl);
        delete img.dataset.objectUrl;
    }
    img.src = '';
}

//删 调用face.delete
async function deleteFace(name) {
    if (!confirm(`确定要删除 "${name}" 吗？此操作不可恢复。`)) return;

    try {
        await LF.api.del('/faces/' + encodeURIComponent(name));
        LF.toast(`已删除 ${name}`, false);
        loadFaces();
    } catch (err) {
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

/** 把 Date 转成 datetime-local 控件需要的本地时间字符串（精确到秒） */
function toLocalInputValue(date) {
    const pad = n => String(n).padStart(2, '0');
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
        + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
}

/** datetime-local 控件的值 -> 提交给后端的字符串（无时区，服务端按本地时间解释） */
function fromLocalInputValue(value) {
    if (!value) return null;
    // 控件在 step=1 时给出 "YYYY-MM-DDTHH:MM" 或 "YYYY-MM-DDTHH:MM:SS"
    return value.length === 16 ? value + ':00' : value;
}

function showCheckoutModal(name) {
    checkoutTargetName = name;
    const face = facesData.find(f => f.name === name);
    if (!face) return;

    // 默认永远是「签退结算」，暂离必须由用户显式勾选
    const awayCheckbox = document.getElementById('checkoutAway');
    if (awayCheckbox) awayCheckbox.checked = false;

    const isOnline = !!face.is_online;
    const isAway = !!face.is_away;

    const checkinEl = document.getElementById('checkoutCheckinTime');
    const durationEl = document.getElementById('checkoutDurationInput');
    const customEl = document.getElementById('checkoutCustomTime');
    const amountEl = document.getElementById('checkoutAmount');
    const title = document.getElementById('checkoutTitle');
    const button = document.getElementById('confirmCheckoutBtn');
    const hint = document.getElementById('checkoutHint');
    const errorEl = document.getElementById('checkoutError');

    if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }

    const checkinDate = face.checkin_time ? new Date(face.checkin_time) : null;
    if (checkinEl) {
        // 只读展示，避免 Chrome 的表单值恢复把旧值塞进可编辑控件
        checkinEl.value = checkinDate ? checkinDate.toLocaleString() : '未签到';
    }

    // 在场时长（分钟）—— 主要输入：直接填分钟数，金额随填随算
    if (durationEl) {
        durationEl.value = '';
        durationEl.disabled = !isOnline;
        // 预填当前已流逝分钟数，用户改成想要的值即可
        durationEl.placeholder = '例如 600 表示 10 小时';
    }

    // 默认签退时间 = 现在（精确到秒），可由用户改成真实离开时间
    // 必须先清空再赋值，避免 Chrome 的表单值恢复把上一次的值塞回控件
    if (customEl) {
        customEl.value = '';
        customEl.value = toLocalInputValue(new Date());
        // 上限 = 当前时刻，稍后用服务端返回的 server_now 校正
        customEl.max = toLocalInputValue(new Date());
        // 记录"用户是否手动改过"，用于区分默认值与用户意图
        customEl.dataset.edited = '0';
    }

    if (!isOnline) {
        if (title) title.textContent = '未在计时';
        if (button) {
            button.textContent = '未在计时，无需签退';
            button.disabled = true;
        }
        if (amountEl) amountEl.value = '未在计时';
        if (customEl) customEl.disabled = true;
        if (hint) hint.textContent = '该人脸当前没有进行中的会话。请先签到后再操作。';
    } else {
        // 标题不带括号
        if (title) title.textContent = isAway ? '暂离中 · 签退结算' : '签退结算';
        if (button) {
            button.textContent = '确认签退结算';
            button.disabled = false;
        }
        if (customEl) customEl.disabled = false;
        if (amountEl) amountEl.value = '计算中…';
        if (hint) hint.textContent = '金额由服务端按「签退时间」计算，前端不参与。';
    }

    document.getElementById('checkoutModal').style.display = 'flex';

    // 抓到服务端的算式明细，供本地预演使用
    let serverStatus = null;

    function preview() {
        if (!amountEl) return;
        if (!serverStatus || !serverStatus.is_online) {
            amountEl.value = '未在计时';
            return;
        }
        const rate = serverStatus.rate_per_minute || 0;
        const awayMinutes = serverStatus.away_minutes || 0;

        // 以「在场时长（分钟）」为准预演，没填时回落到「签到时间 -> 签退时间」的实际流逝
        const raw = durationEl && durationEl.value !== '' ? Number(durationEl.value) : null;
        const settleValue = customEl && customEl.value ? customEl.value : null;
        const settleDate = settleValue ? new Date(settleValue) : new Date();
        const checkin = serverStatus.checkin_time ? new Date(serverStatus.checkin_time) : null;

        let totalMinutes;
        let source;
        if (raw !== null && isFinite(raw) && raw >= 0) {
            totalMinutes = Math.floor(raw);
            source = 'fill';
        } else if (checkin) {
            totalMinutes = Math.max(0, Math.floor((settleDate - checkin) / 60000));
            source = 'elapsed';
        } else {
            amountEl.value = '未知';
            return;
        }

        const billable = Math.max(0, totalMinutes - awayMinutes);
        amountEl.value = '¥ ' + (billable * rate).toFixed(2);

        if (hint) {
            hint.textContent = source === 'fill'
                ? '按你填的 ' + totalMinutes + ' 分钟'
                    + (awayMinutes ? ' 扣除暂离 ' + awayMinutes + ' 分钟' : '')
                    + ' 计费 ' + billable + ' 分钟 × ' + rate.toFixed(2)
                    + ' 元/分钟。最终金额以服务端结算结果为准。'
                : '在场 ' + formatMinutes(totalMinutes)
                    + (awayMinutes ? '，其中暂离 ' + formatMinutes(awayMinutes) : '')
                    + '；计费 ' + billable + ' 分钟 × ' + rate.toFixed(2) + ' 元/分钟'
                    + '。最终金额以服务端结算结果为准。';
        }
    }

    if (isOnline) {
        // 两个输入都触发即时预演
        if (durationEl) {
            durationEl.oninput = preview;
            durationEl.onchange = preview;
        }
        if (customEl) {
            customEl.oninput = preview;
            customEl.onchange = preview;
        }
        // 用服务端的权威数据回填（含暂离扣除、费率、签到时间）
        LF.api.post('/sessions/status', { name: name })
            .then(function (status) {
                if (checkoutTargetName !== name) return;
                serverStatus = status;
                if (isAway || status.is_away) {
                    if (title) title.textContent = '暂离中 · 签退结算';
                }
                // 预填当前已流逝分钟数，用户改成想要的时长即可
                if (durationEl && durationEl.value === '') {
                    durationEl.value = String(status.elapsed_minutes || 0);
                }
                // 签退时间对齐服务端时刻（首个默认值）
                if (customEl && customEl.dataset.edited !== '1' && status.server_now) {
                    customEl.value = toLocalInputValue(new Date(status.server_now));
                }
                preview();
            })
            .catch(function () {
                if (amountEl) amountEl.value = '暂时取不到（以签退结果为准）';
            });
    }
}

/** 把分钟数格式化成「X 小时 Y 分钟」 */
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
    const errorEl = document.getElementById('checkoutError');
    const durationEl = document.getElementById('checkoutDurationInput');
    const customEl = document.getElementById('checkoutCustomTime');

    if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }
    button.disabled = true;

    // 暂离不结算，带上时长或时刻都没有意义
    if (away) {
        try {
            const data = await LF.api.post('/sessions/checkout',
                { name: checkoutTargetName, away: true });
            closeCheckoutModal();
            LF.toast(data.message + '（暂离不结算，归位后继续计时）', false);
        } catch (err) {
            const message = err.message || '操作失败';
            if (errorEl) { errorEl.textContent = message; errorEl.style.display = 'block'; }
            LF.toast(message, true);
        } finally {
            button.disabled = false;
            loadFaces();
        }
        return;
    }

    // 以「在场时长（分钟）」为准：服务端据此把签到时间顺推到该时长
    const payload = { name: checkoutTargetName, away: false };
    const rawDuration = durationEl && durationEl.value !== '' ? Number(durationEl.value) : null;
    if (rawDuration !== null && isFinite(rawDuration) && rawDuration >= 0) {
        payload.duration_minutes = Math.floor(rawDuration);
    }

    // 签退时间：仅当用户明确改过时才传（默认交给服务端的当前时钟）
    if (customEl && customEl.dataset.edited === '1' && customEl.value) {
        payload.checkout_time = fromLocalInputValue(customEl.value);
        // 用户把签退时间改早了又没填时长：用实际已流逝的分钟数，与签退时间保持自洽
        if (payload.duration_minutes === undefined) {
            const elapsed = serverStatus && serverStatus.elapsed_minutes
                ? serverStatus.elapsed_minutes : 0;
            payload.duration_minutes = Math.max(0, Math.floor(elapsed));
        }
    }

    try {
        const data = await LF.api.post('/sessions/checkout', payload);

        // 区分「暂离」与「签退结算」：暂离不结算，session_minutes / amount 为 0
        const isAwayAction = data.is_away_action === true || away;
        const settled = data.settled === true;

        if (data.success === false) {
            if (errorEl) { errorEl.textContent = data.message; errorEl.style.display = 'block'; }
            LF.toast(data.message, true);
            return;   // 不关弹窗，便于修正签退时间后重试
        }

        closeCheckoutModal();

        if (isAwayAction && !settled) {
            LF.toast(data.message + '（暂离不结算，归位后继续计时）', false);
        } else if (settled) {
            // 签退结算成功 -> 弹出支付弹窗（二维码 + 支付链接）
            showPayModal(checkoutTargetName, data);
        } else {
            LF.toast(data.message, false);
        }
    } catch (err) {
        // 校验类错误（签退时间早于签到、晚于当前）留在弹窗里显示，方便直接改
        const message = err.message || '操作失败';
        if (errorEl) { errorEl.textContent = message; errorEl.style.display = 'block'; }
        LF.toast(message, true);
    } finally {
        button.disabled = false;
        loadFaces();
    }
});


/* ==================== 支付 ==================== */

/**
 * 二维码占位图：内联 SVG（data URI），只写「开发中」文字
 * 接入真实支付时，把 ``showPayModal`` 里赋给 ``qrcode.src`` 的值换成
 * 服务端返回的二维码地址即可
 */
function buildPlaceholderQrSvg(text) {
    const label = text || '开发中';
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        + '<text x="100" y="100" text-anchor="middle" dominant-baseline="middle"'
        + ' font-size="28">' + label + '</text>'
        + '</svg>';
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

/**
 * 签退结算成功后展示的支付弹窗
 *
 * @param {string} name  人脸名
 * @param {object} data  /sessions/checkout 的响应（已结算）
 *
 * 金额与时长直接取自签退响应（服务端的权威结算结果），不再二次查询
 */
function showPayModal(name, data) {
    const modal = document.getElementById('payModal');
    if (!modal) return;

    const elapsed = data.elapsed_minutes || 0;
    const away = data.away_minutes || 0;
    const minutes = data.session_minutes || 0;
    const amount = data.amount || 0;
    const rate = data.rate_per_minute || 0;

    const setText = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.value = text;
    };

    /** 把服务端时间戳格式化成「2026/10/16 04:30:51」 */
    const fmtTime = (value) => {
        if (!value) return '—';
        const date = new Date(value);
        if (isNaN(date.getTime())) return '—';
        const pad = n => String(n).padStart(2, '0');
        return date.getFullYear() + '/' + pad(date.getMonth() + 1) + '/' + pad(date.getDate())
            + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes())
            + ':' + pad(date.getSeconds());
    };

    document.getElementById('payTitle').textContent = name + ' · 签退完成，待支付';
    setText('payName', name);
    // 时间账全部来自服务端（用户的签退时间可能被自定义或被收敛）
    setText('payCheckinAt', fmtTime(data.checkin_time));
    setText('payCheckoutAt', fmtTime(data.checkout_time));
    setText('payElapsed', formatMinutes(elapsed));
    setText('payAway', formatMinutes(away));
    setText('payMinutes', minutes + ' 分钟（单价 ' + rate.toFixed(2) + ' 元/分钟）');
    setText('payAmount', '¥ ' + amount.toFixed(2));

    const awayGroup = document.getElementById('payAwayGroup');
    if (awayGroup) awayGroup.style.display = away > 0 ? '' : 'none';

    const summaryEl = document.getElementById('paySummary');
    if (summaryEl) {
        let text = '本次在场 ' + formatMinutes(elapsed)
            + (away ? '（其中暂离 ' + formatMinutes(away) + '）' : '')
            + '，计费 ' + minutes + ' 分钟，应付 ¥ ' + amount.toFixed(2) + '。';

        // 金额为 0 时明确写出原因，便于用户自行核对
        if (amount <= 0) {
            const seconds = Math.max(0, Math.floor(data.elapsed_seconds || 0));
            text += ' 签到到签退不足 1 分钟'
                + (seconds > 0 ? '（实际 ' + seconds + ' 秒）' : '')
                + '，按规则不计费。'
                + ' 若要补录较长的在场时间，可在签退弹窗里把「签退时间」'
                + '改成真实离开的时刻，再签退一次。';
        }
        summaryEl.textContent = text;
    }

    // 二维码：占位内容「开发中」
    const qrcode = document.getElementById('payQrcode');
    if (qrcode) {
        qrcode.src = buildPlaceholderQrSvg('开发中');
        qrcode.alt = '支付二维码（开发中）';
    }

    // 支付链接：指向静态占位页 pay-dev.html，并带上本次结算参数
    const payBtn = document.getElementById('payLinkBtn');
    if (payBtn) {
        const params = new URLSearchParams({
            name: name,
            minutes: String(minutes),
            amount: amount.toFixed(2),
            rate: rate.toFixed(2)
        });
        const url = 'pay-dev.html?' + params.toString();
        payBtn.dataset.payUrl = url;
        payBtn.disabled = false;
    }

    // 金额为 0 的说明已在上面写入 summaryEl

    modal.style.display = 'flex';
}

function closePayModal() {
    const modal = document.getElementById('payModal');
    if (modal) modal.style.display = 'none';
}

document.getElementById('payLinkBtn')?.addEventListener('click', function () {
    const url = this.dataset.payUrl;
    if (url) window.open(url, '_blank', 'noopener');
});

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closePayModal(); closeCheckoutModal(); }
});

window.showPayModal = showPayModal;
window.closePayModal = closePayModal;

// 页面可见性变化时刷新
document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible') {
        reloadFaces();
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
    reloadFaces();
}

document.getElementById('refreshBtn').onclick = () => loadFaces();

// 每30秒自动刷新
setInterval(() => {
    if (document.visibilityState === 'visible') {
        reloadFaces();
    }
}, 30000);

// 「仅标记暂离」勾选状态变化时，同步标题与按钮文案
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