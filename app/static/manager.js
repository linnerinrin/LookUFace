// manager.js - 人脸库管理页面
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


const themeToggle = document.getElementById('themeToggle');
const logo = document.getElementById('logo');


let currentSort = 'default'; //排序方式
let facesData = []; //人脸列表
let modifyTargetName = ''; //要修改的名字
let checkoutTargetName = ''; //登出的脸
let checkinTargetName = ''; //登入的脸

//主题操作
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
}

themeToggle.addEventListener('click', toggleTheme);
initTheme();
logo.addEventListener('click', () => {
    window.location.href = 'idx.html';
});

//检查是否已登录
function isLoggedIn() {
    const token = localStorage.getItem('token');
    const user = localStorage.getItem('user');
    return !!(token && user);
}


//检查后端状态 fetch health接口
async function checkBackend() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        if (response.ok) {
            document.getElementById('statusDot').className = 'status-dot connected';
            document.getElementById('statusText').innerText = '已连接';
            return true;
        }
    } catch (e) {
        console.error('后端连接失败:', e);
    }
    document.getElementById('statusDot').className = 'status-dot offline';
    document.getElementById('statusText').innerText = '未连接';
    return false;
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
            sorted.sort((a, b) => (b.total_online_minutes || 0) - (a.total_online_minutes || 0));
            break;
    }
    return sorted;
}

//excel导出
function exportToExcel() {
    if (!facesData.length) { alert('没有数据可导出'); return; }

    let csv = '\uFEFF序号,姓名,注册时间,在线状态,签到时间,总在线时长(分钟)\n';
    facesData.forEach((face, i) => {
        csv += `${i + 1},${face.name},${face.register_time || ''},${face.is_online ? '在线' : '离线'},${face.checkin_time || ''},${face.total_online_minutes || 0}\n`;
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
    document.getElementById('modifyModal').style.display = 'none';
}

// 确认修改
async function confirmModify() {
    const newName = document.getElementById('modifyName').value.trim();
    const newRegisterTime = document.getElementById('modifyRegisterTime').value;
    const imageFile = document.getElementById('modifyImage').files[0];

    if (!newName) { alert('请输入名字'); return; }

    const token = localStorage.getItem('token');

    const response = await fetch(`${API_BASE}/face/modify`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
            old_name: modifyTargetName,
            new_name: newName,
            register_time: newRegisterTime || null
        })
    });

    if (!response.ok) {
        alert('修改失败');
        return;
    }

    const result = await response.json();

    if (imageFile) {
        const formData = new FormData();
        formData.append('file', imageFile);
        formData.append('name', newName);

        const regResponse = await fetch(`${API_BASE}/face/register`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData
        });

        if (regResponse.ok) {
            const regResult = await regResponse.json();
            if (!regResult.success) {
                alert('图片更新失败: ' + (regResult.message || '未知错误'));
            }
        }
    }

    alert(`已保存`);
    closeModifyModal();
    loadFaces();
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModifyModal();
});

//加载
async function loadFaces() {
    const grid = document.getElementById('facesGrid');
    const onlineCount = facesData.filter(f => f.is_online).length;
    document.getElementById('onlineCount').innerText = onlineCount;
    if (!isLoggedIn()) {
        updateUIAfterLoginChange();
        return;
    }

    grid.innerHTML = '<div class="loading-state">加载中...</div>';

    try {
        const response = await fetch(`${API_BASE}/face/list_with_screenshots`, {
            method:'POST',
            headers: { 'Content-Type': 'application/json',
                       'Authorization': `Bearer ${localStorage.getItem('token')}`
             },
        });

        if (response.status === 401) {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            updateUIAfterLoginChange();
            return;
        }

        const data = await response.json();
        facesData = data.faces || [];
        renderFaces(facesData);

    } catch (err) {
        console.error('加载失败:', err);
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
        const safeName = face.name.replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const onlineBadge = face.is_online ? '<span style="color:#4caf50; font-weight:600;">● 在线</span>' : '<span style="color:#999;">● 离线</span>';
        const checkinTime = face.checkin_time ? new Date(face.checkin_time).toLocaleString() : '未签到';
        const totalMinutes = face.total_online_minutes || 0;
        const hours = Math.floor(totalMinutes / 60);
        const mins = totalMinutes % 60;
        const durationStr = `${hours}h ${mins}m`;
        const onlineBtn = face.is_online
            ? `<button class="btn-checkout" onclick="showCheckoutModal('${safeName}')">签退</button>`
            : `<button class="btn-checkin" onclick="checkinFace('${safeName}')">签到</button>`;
        html += `
            <div class="face-card-item" data-name="${safeName}">
                <div style="font-weight:700; color:var(--text-secondary); min-width:30px;"># ${i + 1}</div>
                <div class="face-avatar">
                    <div class="avatar-placeholder">👤</div>
                </div>
                <div class="face-details">
                    <div class="face-name">${escapeHtml(face.name)} ${onlineBadge}</div>
                    <div class="face-time">签到: ${checkinTime} | 注册: ${registerTime} | 总在线: ${durationStr}</div>
                </div>
                <div class="face-actions">
                    <button class="btn-preview" onclick="previewFace('${safeName}')">预览</button>
                    <button class="btn-modify" onclick="showModifyModal('${safeName}')">修改</button>
                    ${onlineBtn}
                    <button class="btn-delete" onclick="deleteFace('${safeName}')">删除</button>
                </div>
            </div>
        `;
    });
    grid.innerHTML = html;
}

//特殊字符转义
function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
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
        const response = await fetch(`${API_BASE}/face/screenshot`, {
            method:'POST',
            headers: { 'Content-Type': 'application/json',
                       'Authorization': `Bearer ${localStorage.getItem('token')}`
             },
            body:JSON.stringify({name})
        });

        if (response.status === 401) {
            alert('请先登录');
            closePreview();
            if (typeof showLoginModal === 'function') showLoginModal();
            return;
        }

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        img.src = url;
        img.style.display = 'block';
        info.innerHTML = ` ${escapeHtml(name)} 注册时的照片 | 点击图片放大`;
        img.onclick = () => window.open(url, '_blank');
    } catch (err) {
        info.innerHTML = ` 加载失败: ${err.message}`;
    }
}

//关闭预览
function closePreview() {
    const modal = document.getElementById('previewModal');
    const img = document.getElementById('modalImg');
    modal.style.display = 'none';
    if (img.src) {
        URL.revokeObjectURL(img.src);
        img.src = '';
    }
}

//删 调用face.delete
async function deleteFace(name) {
    if (!isLoggedIn()) {
        alert('请先登录');
        if (typeof showLoginModal === 'function') showLoginModal();
        return;
    }

    if (!confirm(`确定要删除 "${name}" 吗？此操作不可恢复。`)) return;

    try {
        const response = await fetch(`${API_BASE}/face`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json',
                       'Authorization': `Bearer ${localStorage.getItem('token')}`
             },
            body: JSON.stringify({name})
        });

        if (response.status === 401) {
            alert('请先登录');
            if (typeof showLoginModal === 'function') showLoginModal();
            return;
        }

        const result = await response.json();
        if (result.success) {
            alert(`已删除 ${name}`);
            loadFaces();
        } else {
            alert('删除失败');
        }
    } catch (err) {
        alert('删除失败: ' + err.message);
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
    const token = localStorage.getItem('token');
    const response = await fetch(`${API_BASE}/check/checkin`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ name: checkinTargetName, request_times: 999 })
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
            alert(data.message);
        }
    }
    closeCheckinConfirmModal();
    loadFaces();
});

function showCheckoutModal(name) {
    checkoutTargetName = name;
    const face = facesData.find(f => f.name === name);
    if (!face) return;

    const defaultAway = face.is_online && !face.is_away;
    document.getElementById('checkoutAway').checked = defaultAway;
    document.querySelector('#checkoutModal .modal-header h3').textContent = defaultAway ? '暂离' : '签退结算';

    document.getElementById('checkoutCheckinTime').value = face.checkin_time
        ? new Date(face.checkin_time).toLocaleString()
        : '未签到';

    document.querySelector('#checkoutModal .form-group label[for="checkoutDuration"]').textContent =
        defaultAway ? '暂离时长（分钟）' : '本次在线时长（分钟）';

    let defaultDuration = defaultAway ? 30 : 60;
    if (face.checkin_time && !defaultAway) {
        defaultDuration = Math.floor((Date.now() - new Date(face.checkin_time).getTime()) / 60000);
    }
    document.getElementById('checkoutDuration').value = defaultDuration;

    document.getElementById('checkoutModal').style.display = 'flex';
}



function closeCheckoutModal() {
    document.getElementById('checkoutModal').style.display = 'none';
}

document.getElementById('confirmCheckoutBtn').addEventListener('click', async () => {
    const duration = parseInt(document.getElementById('checkoutDuration').value) || 0;
    const token = localStorage.getItem('token');
    const away = document.getElementById('checkoutAway')?.checked || false;

    const response = await fetch(`${API_BASE}/check/checkout`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
            name: checkoutTargetName,
            away: away,         // ← 用复选框的值
            away_time: away ? duration : null
        })
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
            alert(away ? `暂离成功，请在 ${duration} 分钟内归来` : `签退成功，在线时长: ${data.online_time || duration} 分钟`);
        }
    }
    closeCheckoutModal();
    loadFaces();
});


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

document.getElementById('checkoutAway')?.addEventListener('change', function() {
    const label = document.querySelector('#checkoutModal .form-group label[for="checkoutDuration"]');
    const title = document.querySelector('#checkoutModal .modal-header h3');
    if (this.checked) {
        if (label) label.textContent = '暂离时长（分钟）';
        if (title) title.textContent = '暂离';
    } else {
        if (label) label.textContent = '本次在线时长（分钟）';
        if (title) title.textContent = '签退结算';
    }
});

init();

window.previewFace = previewFace;
window.closePreview = closePreview;
window.deleteFace = deleteFace;