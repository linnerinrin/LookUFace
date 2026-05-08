// manager.js - 人脸库管理页面
/*
API_BASE 后端
themeToggle 主题按钮
logo

*/


const themeToggle = document.getElementById('themeToggle');
const logo = document.getElementById('logo');

/*

*获取人脸列表 调face/list-with-screenshot 接口调db
**删除人脸  调face/delete 接口调db
***预览 调face/screenshot/{filename} 接口调db


initTheme()主题初始化
toggleTheme()主题切换
isLoggedIn()检查是否已登录
checkBackend()检查后端是否存活
*loadFaces()	获取人脸列表并渲染卡片
escapeHtml(str)	特殊字符转义
***previewFace(name)获取截图并在弹窗展示
closePreview()	关闭预览弹窗
**deleteFace(name)删除人脸，刷新列表
*/


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

// 加载人脸列表 fetch list-with-screenshot
async function loadFaces() {
    const grid = document.getElementById('facesGrid');

    //未登录
    if (!isLoggedIn()) {
        grid.innerHTML = `
            <div class="empty-state-large">
                <p>请先登录查看人脸库</p>
                <button id="loginPromptBtn" class="btn btn-primary" style="margin-top: 16px;">立即登录</button>
            </div>
        `;
        document.getElementById('totalCount').innerText = '0';
        document.getElementById('facesCount').innerText = '0 条记录';

        // 绑定登录按钮
        const loginBtn = document.getElementById('loginPromptBtn');
        if (loginBtn) {
            loginBtn.onclick = () => {
                if (typeof showLoginModal === 'function') showLoginModal();
            };
        }
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

        // 如果返回 401，清除本地登录状态
        if (response.status === 401) {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            loadFaces(); // 重新加载显示未登录状态
            return;
        }

        const data = await response.json();

        document.getElementById('totalCount').innerText = data.faces?.length || 0;
        document.getElementById('facesCount').innerText = `${data.faces?.length || 0} 条记录`;

        if (!data.faces || data.faces.length === 0) {
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
        for (const face of data.faces) {
            const registerTime = face.register_time ? new Date(face.register_time).toLocaleString() : '未知';
            const safeName = face.name.replace(/'/g, "\\'").replace(/"/g, '&quot;');

            html += `
                <div class="face-card-item" data-name="${safeName}">
                    <div class="face-avatar">
                        <div class="avatar-placeholder">👤</div>
                    </div>
                    <div class="face-details">
                        <div class="face-name">${escapeHtml(face.name)}</div>
                        <div class="face-time"> ${registerTime}</div>
                    </div>
                    <div class="face-actions">
                        <button class="btn-preview" onclick="previewFace('${safeName}')">预览</button>
                        <button class="btn-delete" onclick="deleteFace('${safeName}')">删除</button>
                    </div>
                </div>
            `;
        }
        grid.innerHTML = html;

    } catch (err) {
        console.error('加载失败:', err);
        grid.innerHTML = '<div class="error-state">加载失败，请检查后端服务</div>';
    }
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
    // 检查登录
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
    // 检查登录
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

// 监听登录状态变化
window.addEventListener('storage', function(e) {
    if (e.key === 'token' || e.key === 'user') {
        console.log('登录状态变化，重新加载人脸列表');
        loadFaces();
    }
});

// 页面可见性变化时刷新
document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible') {
        loadFaces();
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
    await loadFaces();
}

document.getElementById('refreshBtn').onclick = () => loadFaces();

// 每30秒自动刷新
setInterval(() => {
    if (document.visibilityState === 'visible') {
        loadFaces();
    }
}, 30000);

init();

window.previewFace = previewFace;
window.closePreview = closePreview;
window.deleteFace = deleteFace;