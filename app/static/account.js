// account.js - 账号管理页面（使用邮箱登录）

/*
initTheme()	初始化主题
toggleTheme()	切主题
updateAccountUI()	显示页面
loadUserProfile()	加载登录信息
saveProfile()	保存昵称修改
showPasswordModal()	显示修改密码弹窗
closePasswordModal()	关闭弹窗并清空输入
changePassword()	修改密码
logout()	logout
*/
const themeToggle = document.getElementById('themeToggle');
const logo = document.getElementById('logo');

//初始化主题
function initTheme() {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
        document.body.classList.add('dark');
    }
}

//切
function toggleTheme() {
    document.body.classList.toggle('dark');
    const isDark = document.body.classList.contains('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
}

themeToggle.addEventListener('click', toggleTheme);
initTheme();

//回idxhtml
logo.addEventListener('click', () => {
    window.location.href = 'idx.html';
});

//使用资料显示页面
function updateAccountUI() {
    const userStr = localStorage.getItem('user');
    let user = null;
    if (userStr) {
        try { user = JSON.parse(userStr); } catch(e) {}
    }

    const notLoggedIn = document.getElementById('notLoggedIn');
    const loggedIn = document.getElementById('loggedIn');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const loginBtn = document.getElementById('loginBtn');

    if (user && user.id) {
        notLoggedIn.style.display = 'none';
        loggedIn.style.display = 'block';
        statusDot.className = 'status-dot connected';
        const displayName = user.nickname || (user.email ? user.email.split('@')[0] : '用户');
        statusText.innerText = displayName;
        if (loginBtn) loginBtn.innerHTML = `<span>👤</span> ${displayName}`;

        document.getElementById('profileName').innerText = displayName;
        document.getElementById('profileEmail').innerText = user.email;
        document.getElementById('profileNickname').innerText = user.nickname || '未设置';
        document.getElementById('profileSince').innerText = user.created_at ? new Date(user.created_at).toLocaleDateString() : '-';
        document.getElementById('editNickname').value = user.nickname || '';
    } else {
        notLoggedIn.style.display = 'block';
        loggedIn.style.display = 'none';
        statusDot.className = 'status-dot offline';
        statusText.innerText = '未登录';
        if (loginBtn) loginBtn.innerHTML = `<span>登录账号</span> `;
    }
}


//加载用户资料 fetch profile.get
async function loadUserProfile() {
    const token = localStorage.getItem('token');
    if (!token) {
        updateAccountUI();
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/profile`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            const user = await response.json();
            localStorage.setItem('user', JSON.stringify(user));
            updateAccountUI();
            if (typeof updateAuthUI === 'function') updateAuthUI();
        } else {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            updateAccountUI();
            if (typeof updateAuthUI === 'function') updateAuthUI();
        }
    } catch (err) {
        console.error('加载失败:', err);
        updateAccountUI();
    }
}

//更新用户资料 fetch profile.put
async function saveProfile() {
    const nickname = document.getElementById('editNickname').value.trim();
    const token = localStorage.getItem('token');

    if (!token) {
        alert('请先登录');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/profile`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ nickname, bio: '' })
        });

        if (response.ok) {
            alert('保存成功');
            await loadUserProfile();
            if (typeof updateAuthUI === 'function') updateAuthUI();
        } else {
            const data = await response.json();
            alert(data.detail || '保存失败');
        }
    } catch (err) {
        alert('保存失败: ' + err.message);
    }
}

//登录框
function showPasswordModal() {
    document.getElementById('passwordModal').style.display = 'flex';
}


//改密码关
function closePasswordModal() {
    document.getElementById('passwordModal').style.display = 'none';
    document.getElementById('oldPassword').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('confirmNewPassword').value = '';
}

//改密码 fetch change-passowrd
async function changePassword() {
    const oldPassword = document.getElementById('oldPassword').value;
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmNewPassword').value;
    const token = localStorage.getItem('token');

    if (!token) {
        alert('请先登录');
        return;
    }
    if (!oldPassword || !newPassword) {
        alert('请填写密码');
        return;
    }
    if (newPassword !== confirmPassword) {
        alert('两次输入的新密码不一致');
        return;
    }
    if (newPassword.length < 6) {
        alert('新密码长度至少6位');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/change-password`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
        });

        if (response.ok) {
            alert('密码修改成功，请重新登录');
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            updateAccountUI();
            if (typeof updateAuthUI === 'function') updateAuthUI();
        } else {
            const data = await response.json();
            alert(data.detail || '修改失败');
        }
    } catch (err) {
        alert('修改失败: ' + err.message);
    }
}


//登录
function logout() {
    if (confirm('确定要退出登录吗？')) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        updateAccountUI();
        if (typeof updateAuthUI === 'function') updateAuthUI();
        alert('已退出登录');

        if (window.location.pathname.includes('manager.html')) {
            window.location.reload();
        }

        window.dispatchEvent(new StorageEvent('storage', {
            key: 'token',
            newValue: null,
            oldValue: localStorage.getItem('token')
        }));
    }
}

window.addEventListener('storage', function(e) {
    if (e.key === 'user' || e.key === 'token') {
        updateAccountUI();
    }
});

document.getElementById('gotoLoginBtn')?.addEventListener('click', () => {
    if (typeof showLoginModal === 'function') showLoginModal();
});
document.getElementById('saveProfileBtn')?.addEventListener('click', saveProfile);
document.getElementById('changePasswordBtn')?.addEventListener('click', showPasswordModal);
document.getElementById('logoutBtn')?.addEventListener('click', logout);
document.getElementById('confirmPwdBtn')?.addEventListener('click', changePassword);

setTimeout(loadUserProfile, 100);