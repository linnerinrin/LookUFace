// account.js - 账号管理页面（使用邮箱登录）
var LF = window.LookUFace;
// 依赖：api.js（需先引入）。

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



//使用资料显示页面
function updateAccountUI() {
    const user = LF.auth.getUser();

    const notLoggedIn = document.getElementById('notLoggedIn');
    const loggedIn = document.getElementById('loggedIn');
    const loginBtn = document.getElementById('loginBtn');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');

    // account.html 里没有 statusDot / statusText（那是 manager.html 的元素），
    // 旧实现直接 .className = ... 会抛 "Cannot set properties of null"。
    // 所有元素访问一律做空值保护。
    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.innerText = value;
    }

    // 头像里显示名字首字（原先是一个 emoji）。
    // 用 Array.from 而不是 charAt，避免把代理对字符截成半个。
    function firstChar(name) {
        const chars = Array.from(String(name == null ? '' : name).trim());
        return chars.length ? chars[0] : '?';
    }

    if (user && user.id) {
        if (notLoggedIn) notLoggedIn.style.display = 'none';
        if (loggedIn) loggedIn.style.display = 'block';
        if (statusDot) statusDot.className = 'status-dot connected';
        if (statusText) statusText.innerText = LF.auth.displayName();
        // 头像与按钮都去掉 emoji：按钮只显示名字，头像显示名字首字
        if (loginBtn) loginBtn.innerHTML = `<span>${LF.escapeHtml(LF.auth.displayName())}</span>`;
        setText('profileAvatar', firstChar(LF.auth.displayName()));

        setText('profileName', LF.auth.displayName());
        setText('profileEmail', user.email || '');
        setText('profileNickname', user.nickname || '未设置');
        setText('profileSince', user.created_at
            ? new Date(user.created_at).toLocaleDateString()
            : '-');

        const nicknameInput = document.getElementById('editNickname');
        if (nicknameInput) nicknameInput.value = user.nickname || '';
    } else {
        if (notLoggedIn) notLoggedIn.style.display = 'block';
        if (loggedIn) loggedIn.style.display = 'none';
        if (statusDot) statusDot.className = 'status-dot offline';
        if (statusText) statusText.innerText = '未登录';
        if (loginBtn) loginBtn.innerHTML = `<span>登录账号</span> `;
    }
}


//加载用户资料 fetch profile.get
async function loadUserProfile() {
    if (!LF.auth.isLoggedIn()) {
        updateAccountUI();
        return;
    }

    try {
        const user = await LF.api.get('/auth/profile');
        // 只刷新 user，保留原 token
        localStorage.setItem('user', JSON.stringify(user));
        updateAccountUI();
        if (typeof updateAuthUI === 'function') updateAuthUI();
    } catch (err) {
        if (err.status === 401) {
            // 令牌失效：清空登录态
            LF.auth.clearSession();
            updateAccountUI();
            if (typeof updateAuthUI === 'function') updateAuthUI();
        } else {
            LF.log.warn('加载用户资料失败: ' + err.message);
            updateAccountUI();
        }
    }
}

//更新用户资料 fetch profile.put
async function saveProfile() {
    const nickname = document.getElementById('editNickname').value.trim();

    if (!LF.auth.isLoggedIn()) {
        LF.toast('请先登录', true);
        return;
    }
    if (!nickname) {
        LF.toast('昵称不能为空', true);
        return;
    }

    try {
        const data = await LF.api.put('/auth/profile', { nickname });
        localStorage.setItem('user', JSON.stringify(data.user));
        LF.toast('保存成功', false);
        updateAccountUI();
        if (typeof updateAuthUI === 'function') updateAuthUI();
    } catch (err) {
        LF.toast(err.message || '保存失败', true);
    }
}

//登录框
function showPasswordModal() {
    const modal = document.getElementById('passwordModal');
    if (modal) modal.style.display = 'flex';
}


//改密码关
function closePasswordModal() {
    const modal = document.getElementById('passwordModal');
    if (modal) modal.style.display = 'none';

    ['oldPassword', 'newPassword', 'confirmNewPassword'].forEach(function (id) {
        const input = document.getElementById(id);
        if (input) input.value = '';
    });
}

//改密码 fetch change-passowrd
async function changePassword() {
    const oldInput = document.getElementById('oldPassword');
    const newInput = document.getElementById('newPassword');
    const confirmInput = document.getElementById('confirmNewPassword');
    const oldPassword = oldInput ? oldInput.value : '';
    const newPassword = newInput ? newInput.value : '';
    const confirmPassword = confirmInput ? confirmInput.value : '';

    if (!LF.auth.isLoggedIn()) {
        LF.toast('请先登录', true);
        return;
    }
    if (!oldPassword || !newPassword) {
        LF.toast('请填写密码', true);
        return;
    }
    if (newPassword !== confirmPassword) {
        LF.toast('两次输入的新密码不一致', true);
        return;
    }

    try {
        await LF.api.post('/auth/change-password', {
            old_password: oldPassword,
            new_password: newPassword
        });
        LF.toast('密码修改成功，请重新登录', false);
        LF.auth.clearSession();
        closePasswordModal();
        updateAccountUI();
        if (typeof updateAuthUI === 'function') updateAuthUI();
    } catch (err) {
        LF.toast(err.message || '修改失败', true);
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