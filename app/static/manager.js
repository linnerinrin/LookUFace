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
        // 空状态不再使用图标（原先是一个 emoji）
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
        // 状态徽标：圆点用纯 CSS 画，不再使用 ●（U+25CF）。
        // 虽然 ● 不是 emoji 码位，但在本机由 seguiemj 字体提供，
        // 实际渲染出来就是彩色实心圆点（已用 Chrome 截图确认），
        // 与用户要求去掉的 emoji 观感一致，因此一并替换。
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

        // 说明：原先这里还有一个「账单」按钮。
        // 按需求已删除 —— 账单不再单独查询，改为**签退结算成功后自动弹出**
        // 支付弹窗（二维码 + 支付链接），避免「没签退也能出账」的困惑。

        // 注意：名字只进 data-name 属性（经 escapeHtml 处理），绝不拼进 onclick。
        // 旧版把名字插进 onclick="f('...')" 且只做了 \' 转义，可被注入。
        html += `
            <div class="face-card-item" data-name="${escapeHtml(face.name)}">
                <div style="font-weight:700; color:var(--text-secondary); min-width:30px;"># ${i + 1}</div>
                <div class="face-avatar">
                    <span class="avatar-initial">${escapeHtml(avatarInitial(face.name))}</span>
                </div>
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
    }
});

//特殊字符转义
function escapeHtml(str) {
    // 统一使用 api.js 的实现，旧版漏掉了引号与单引号
    return LF.escapeHtml(str);
}

/**
 * 头像里显示的首字。
 *
 * 原先头像里放的是一个 emoji，按需求去掉。改为显示人名首字 ——
 * 用 Array.from 而不是 charAt，这样带代理对的字符（如某些生僻字）
 * 不会被截成半个。
 */
function avatarInitial(name) {
    const chars = Array.from(String(name == null ? '' : name).trim());
    return chars.length ? chars[0] : '?';
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

/** 把 Date 转成 datetime-local 控件需要的本地时间字符串（精确到秒）。 */
function toLocalInputValue(date) {
    const pad = n => String(n).padStart(2, '0');
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
        + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
}

/** datetime-local 控件的值 -> 提交给后端的字符串（无时区，服务端按本地时间解释）。 */
function fromLocalInputValue(value) {
    if (!value) return null;
    // 控件在 step=1 时给出 "YYYY-MM-DDTHH:MM" 或 "YYYY-MM-DDTHH:MM:SS"
    return value.length === 16 ? value + ':00' : value;
}

function showCheckoutModal(name) {
    checkoutTargetName = name;
    const face = facesData.find(f => f.name === name);
    if (!face) return;

    // ── 关键修复（保留）──────────────────────────────────────────────────
    // 旧版把「在线且未暂离」默认勾选为「暂离」，而暂离走的是不结算分支，
    // 返回的 session_minutes / amount 必然为 0，表现为「无论多久都是 0 分钟 0 元」。
    // 现在默认永远是「签退结算」，暂离必须由用户显式勾选。
    const awayCheckbox = document.getElementById('checkoutAway');
    if (awayCheckbox) awayCheckbox.checked = false;

    const isOnline = !!face.is_online;
    const isAway = !!face.is_away;

    const checkinEl = document.getElementById('checkoutCheckinTime');
    const customEl = document.getElementById('checkoutCustomTime');
    const amountEl = document.getElementById('checkoutAmount');
    const title = document.getElementById('checkoutTitle');
    const button = document.getElementById('confirmCheckoutBtn');
    const hint = document.getElementById('checkoutHint');
    const errorEl = document.getElementById('checkoutError');

    if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }

    const checkinDate = face.checkin_time ? new Date(face.checkin_time) : null;
    if (checkinEl) {
        checkinEl.value = checkinDate ? checkinDate.toLocaleString() : '未签到';
    }

    // 默认签退时间 = 现在（精确到秒），可由用户改成真实离开时间。
    //
    // 这里必须**先清空再赋值**：Chrome 的"表单值恢复"会在页面加载/重开弹窗时
    // 把上一次的值塞回 datetime-local 控件，并且会覆盖脚本刚赋的值。
    // 实测后果很严重 —— 控件里留着上一次的旧时刻，用户直接点确认，
    // 服务端就按那个旧时刻结算，于是"签退后账单全是 0"
    //（日志里可见：计费秒数=3 / 11）。
    if (customEl) {
        customEl.value = '';
        customEl.value = toLocalInputValue(new Date());
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
        // 标题不带括号：按需求去掉冗余括号
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
        // 本地按「签到时间 -> 签退时间」预演金额，仅用于填表时的即时反馈；
        // 真正的金额一律以服务端签退结果为准。
        const settleValue = customEl && customEl.value ? customEl.value : null;
        const settleDate = settleValue ? new Date(settleValue) : new Date();
        const checkin = serverStatus.checkin_time ? new Date(serverStatus.checkin_time) : null;

        if (!checkin) { amountEl.value = '未知'; return; }
        if (settleDate < checkin) {
            amountEl.value = '签退时间早于签到时间';
            return;
        }

        const totalMinutes = Math.floor((settleDate - checkin) / 60000);
        const awayMinutes = serverStatus.away_minutes || 0;
        const billable = Math.max(0, totalMinutes - awayMinutes);
        const rate = serverStatus.rate_per_minute || 0;
        const amount = billable * rate;

        // 金额是主角，时长明细放进下面的说明行
        amountEl.value = '¥ ' + amount.toFixed(2);
        if (hint) {
            hint.textContent = '在场 ' + formatMinutes(totalMinutes)
                + (awayMinutes ? '，其中暂离 ' + formatMinutes(awayMinutes) : '')
                + '；计费 ' + billable + ' 分钟 × ' + rate.toFixed(2) + ' 元/分钟'
                + '。最终金额以服务端结算结果为准。';
        }
    }

    if (isOnline) {
        // 输入变化时即时预演，并标记"用户已手动改过"。
        // 这个标记用于提交时区分"用户明确指定了时间"与"还是默认值" ——
        // 后者不该传给服务端（浏览器可能回填旧值，见上面的说明）。
        if (customEl) {
            const markEdited = function () {
                customEl.dataset.edited = '1';
                preview();
            };
            customEl.oninput = markEdited;
            customEl.onchange = markEdited;
        }
        // 用服务端的权威数据回填（含暂离扣除、费率、签到时间）
        LF.api.post('/sessions/status', { name: name })
            .then(function (status) {
                if (checkoutTargetName !== name) return;
                serverStatus = status;
                if (isAway || status.is_away) {
                    if (title) title.textContent = '暂离中 · 签退结算';
                }
                preview();
            })
            .catch(function () {
                if (amountEl) amountEl.value = '暂时取不到（以签退结果为准）';
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
    const errorEl = document.getElementById('checkoutError');
    const customEl = document.getElementById('checkoutCustomTime');

    if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }
    button.disabled = true;

    // 只在**用户明确改过**签退时间时才把它传给服务端。
    //
    // 为什么要这么严格：
    //   * Chrome 的"表单值恢复"会把上一次的值回填进 datetime-local 控件，
    //     甚至覆盖脚本刚赋的默认值。实测后果是：控件里留着上一次的旧时刻，
    //     用户直接点确认 -> 服务端按那个旧时刻结算 -> "账单全是 0"
    //     （日志里可见 计费秒数=3 / 11）。
    //   * 没改过就等于"现在签退"，让服务端用自己的当前时间最可靠，
    //     既不受浏览器回填影响，也不受本机与服务器时钟差影响。
    //
    // 用户改过的情况仍然完整支持（事后补签退）。
    let checkoutTime = null;
    if (!away && customEl && customEl.dataset.edited === '1' && customEl.value) {
        const nowValue = toLocalInputValue(new Date());
        customEl.max = nowValue;
        // 若用户填的时刻已超过"现在"，纠正为现在（服务端也会再做一次收敛兜底）
        if (customEl.value > nowValue) {
            customEl.value = nowValue;
        }
        checkoutTime = fromLocalInputValue(customEl.value);
    }

    // 暂离不使用自定义时间（暂离不结算）；签退仅在用户指定时才带上时间。
    const payload = { name: checkoutTargetName, away: away };
    if (checkoutTime) {
        payload.checkout_time = checkoutTime;
    }

    try {
        const data = await LF.api.post('/sessions/checkout', payload);

        // 必须区分「暂离」与「签退结算」：
        // 暂离不结算，此时 session_minutes / amount 必然是 0，
        // 若按签退文案展示就会误导为「0 分钟 0 元」。
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
 * 二维码占位图：**只写「开发中」三个字，不加任何样式**。
 *
 * 需求：二维码内容暂且写文字「开发中」，等接入真实收银台后再替换为下单结果。
 *
 * 实现：用内联 SVG（data URI），只放一行文字 —— 没有边框、没有配色、
 * 没有额外说明。这样既不新增静态资源、不依赖网络，也不会让人误以为
 * 那是一张真的二维码。
 *
 * 接入真实支付时，把 ``showPayModal`` 里赋给 ``qrcode.src`` 的值换成
 * 服务端返回的二维码地址即可。
 */
function buildPlaceholderQrSvg(text) {
    const label = text || '开发中';
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        + '<text x="100" y="100" text-anchor="middle" dominant-baseline="middle"'
        + ' font-size="28">' + label + '</text>'
        + '</svg>';
    // 用 encodeURIComponent 而不是 base64，便于直接阅读与排查
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

/**
 * 签退结算成功后展示的支付弹窗。
 *
 * @param {string} name  人脸名
 * @param {object} data  /sessions/checkout 的响应（已结算）
 *
 * 金额与时长直接取自签退响应 —— 那是服务端的权威结算结果，
 * 不再二次查询、也不允许前端改动。
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

    document.getElementById('payTitle').textContent = name + ' · 签退完成，待支付';
    setText('payName', name);
    setText('payElapsed', formatMinutes(elapsed));
    setText('payAway', formatMinutes(away));
    setText('payMinutes', minutes + ' 分钟（单价 ' + rate.toFixed(2) + ' 元/分钟）');
    setText('payAmount', '¥ ' + amount.toFixed(2));

    const awayGroup = document.getElementById('payAwayGroup');
    if (awayGroup) awayGroup.style.display = away > 0 ? '' : 'none';

    const summaryEl = document.getElementById('paySummary');
    if (summaryEl) {
        summaryEl.textContent = '本次在场 ' + formatMinutes(elapsed)
            + (away ? '（其中暂离 ' + formatMinutes(away) + '）' : '')
            + '，计费 ' + minutes + ' 分钟，应付 ¥ ' + amount.toFixed(2) + '。';
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

    // 金额为 0（不足一分钟）时说明清楚，避免用户以为漏了
    if (amount <= 0 && summaryEl) {
        summaryEl.textContent += ' 不足 1 分钟按规则不计费，此处仅作展示。';
    }

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