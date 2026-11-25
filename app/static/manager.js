// manager.js - 人脸库管理页面
var LF = window.LookUFace;
// 依赖：api.js（需先引入）
/*

*获取人脸列表 调face/list-with-screenshot 接口调db 并且可以按照name/duration/online排序
**对人脸操作 开模态框 关闭时调face/modify(delete) 接口调db 可以接图调face/register
***手动签到/签退 开模态框 关闭时调check 接checkservice

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
showCheckoutModal()/closeCheckoutModal()  打开/关闭签退模态框 关闭时调checkout
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
        console.warn('后端健康检查失败: ' + err.message);
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
        console.warn('加载人脸列表失败: ' + err.message);
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
            ? '<span class="badge badge-online"><i class="badge-dot"></i>在线</span>'
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

    const isOnline = !!face.is_online;
    const checkinEl = document.getElementById('checkoutCheckinTime');
    const durationEl = document.getElementById('checkoutDurationInput');
    const customEl = document.getElementById('checkoutCustomTime');
    const title = document.getElementById('checkoutTitle');
    const button = document.getElementById('confirmCheckoutBtn');
    const errorEl = document.getElementById('checkoutError');

    if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }

    const checkinDate = face.checkin_time ? new Date(face.checkin_time) : null;
    if (checkinEl) {
        checkinEl.value = checkinDate ? checkinDate.toLocaleString() : '未签到';
    }

    if (durationEl) {
        durationEl.value = '';
        durationEl.disabled = !isOnline;
        durationEl.placeholder = '例如 60 表示 1 小时';
    }

    if (customEl) {
        customEl.value = '';
        customEl.value = toLocalInputValue(new Date());
        customEl.max = toLocalInputValue(new Date());
        customEl.dataset.edited = '0';
        customEl.disabled = !isOnline;
    }

    if (!isOnline) {
        if (title) title.textContent = '未在场';
        if (button) {
            button.textContent = '未在场，无需签退';
            button.disabled = true;
        }
    } else {
        if (title) title.textContent = '签退';
        if (button) {
            button.textContent = '确认签退';
            button.disabled = false;
        }
    }

    document.getElementById('checkoutModal').style.display = 'flex';

    if (isOnline) {
        LF.api.post('/sessions/status', { name: name })
            .then(function (status) {
                if (checkoutTargetName !== name) return;
                if (durationEl && durationEl.value === '') {
                    durationEl.value = String(status.elapsed_minutes || 0);
                }
                if (customEl && customEl.dataset.edited !== '1' && status.server_now) {
                    customEl.value = toLocalInputValue(new Date(status.server_now));
                }
            })
            .catch(function () {});
    }
}

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
    const button = document.getElementById('confirmCheckoutBtn');
    const errorEl = document.getElementById('checkoutError');
    const durationEl = document.getElementById('checkoutDurationInput');
    const customEl = document.getElementById('checkoutCustomTime');

    if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }
    button.disabled = true;

    const payload = { name: checkoutTargetName };
    const rawDuration = durationEl && durationEl.value !== '' ? Number(durationEl.value) : null;
    if (rawDuration !== null && isFinite(rawDuration) && rawDuration >= 0) {
        payload.duration_minutes = Math.floor(rawDuration);
    }

    if (customEl && customEl.dataset.edited === '1' && customEl.value) {
        payload.checkout_time = fromLocalInputValue(customEl.value);
    }

    try {
        const data = await LF.api.post('/sessions/checkout', payload);
        if (data.success === false) {
            if (errorEl) { errorEl.textContent = data.message; errorEl.style.display = 'block'; }
            LF.toast(data.message, true);
            return;
        }

        closeCheckoutModal();
        LF.toast(data.message, false);
    } catch (err) {
        const message = err.message || '操作失败';
        if (errorEl) { errorEl.textContent = message; errorEl.style.display = 'block'; }
        LF.toast(message, true);
    } finally {
        button.disabled = false;
        loadFaces();
    }
});


document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeCheckoutModal(); }
});

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

const themeToggle = document.getElementById('themeToggle');
if (themeToggle) {
    themeToggle.addEventListener('click', function () {
        LF.theme.toggle();
    });
}
LF.theme.init();

window.addEventListener('storage', function (event) {
    if (event.key === LF.theme.KEY) {
        LF.theme.apply(LF.theme.current());
    }
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


init();

window.previewFace = previewFace;
window.closePreview = closePreview;
window.deleteFace = deleteFace;