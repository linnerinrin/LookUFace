// auth.js - 完整的用户认证模块

/*
countdownInterval 验证码定时器
*/
const API_BASE = '/api/v1'
let countdownInterval = null;

/*
**login showloginmodal显示登录认证框 然后给登录按钮绑handleLogin handlelogin调auth\login接口 接口调db对比邮箱密码 正确则寸token和用户数据然后放行
***register showregisterform显示注册认证框 给验证码按钮绑sendregistercode 调用auht\sentcode接口 用smtplib发验证码 给注册按钮handleregister handleregister确认验证码无误把新用户写入db
****changepassword 流程同register


initAuth()	初始化
bindAuthEvents()	登录按钮跳转
updateAuthUI()	更新导航栏登录按钮
showAuthModal()	显示认证弹窗
closeAuthModal()	关闭认证弹窗并清除倒计时
**isValidEmail(email)	校验邮箱格式是否合法
validateEmailInput(input)	注册页实时校验邮箱输入框
showTip(message, isError)	页面顶部弹出成功消息
startCountdown(btn)	验证码计时
**showLoginModal()	切换到登录表单
**handleLogin()	登录并存jwt
***showRegisterForm()	切换到注册表单
***sendRegisterCode()	发验证码
***handleRegister()	注册并登录
****showForgotForm()	切换到找回密码表单
****sendResetCode()	发验证码
****handleResetPassword()	重置密码
logout()	登出 清除jwt
getCurrentUser()	解析user
*/

//初始化
function initAuth() {
    console.log('初始化认证模块');
    updateAuthUI();
    bindAuthEvents();
}

//绑按钮
function bindAuthEvents() {
    const loginBtn = document.getElementById('loginBtn');
    if (loginBtn) {
        const newLoginBtn = loginBtn.cloneNode(true);
        loginBtn.parentNode.replaceChild(newLoginBtn, loginBtn);
        newLoginBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const user = getCurrentUser();
            if (user && user.id) {
                window.location.href = 'account.html';
            } else {
                showLoginModal();
            }
        });
    }
}

//更新
function updateAuthUI() {
    const loginBtn = document.getElementById('loginBtn');
    const user = getCurrentUser();

    if (user && user.id) {
        if (loginBtn) {
            const displayName = user.nickname || (user.email ? user.email.split('@')[0] : '用户');
            loginBtn.innerHTML = `<span>👤</span> ${displayName}`;
        }
    } else {
        if (loginBtn) {
            loginBtn.innerHTML = `<span>登录账号</span> `;
        }
    }
}

//认证弹窗
function showAuthModal() {
    const modal = document.getElementById('authModal');
    if (modal) modal.style.display = 'flex';
}

//关闭认证弹窗
function closeAuthModal() {
    const modal = document.getElementById('authModal');
    if (modal) modal.style.display = 'none';
    if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
    }
}

//邮箱合法
function isValidEmail(email) {
    const emailRegex = /^[^\s@]+@([^\s@]+\.)+[^\s@]+$/;
    if (!email) return false;
    if (!emailRegex.test(email)) return false;
    if (email.length > 100) return false;
    const parts = email.split('@');
    if (parts.length !== 2) return false;
    if (parts[1].split('.').length < 2) return false;
    return true;
}

// 实时验证邮箱格式
function validateEmailInput(input) {
    const errorSpan = document.getElementById('emailError');
    if (!errorSpan) return;

    const email = input.value.trim();
    if (email && !isValidEmail(email)) {
        errorSpan.style.display = 'block';
        input.style.borderColor = '#f44336';
    } else {
        errorSpan.style.display = 'none';
        input.style.borderColor = '';
    }
}

// 显示提示消息
function showTip(message, isError = true) {
    let tipDiv = document.getElementById('globalTip');
    if (!tipDiv) {
        tipDiv = document.createElement('div');
        tipDiv.id = 'globalTip';
        tipDiv.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            z-index: 10000;
            animation: fadeInOut 3s ease forwards;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        `;
        document.body.appendChild(tipDiv);

        if (!document.getElementById('tipStyle')) {
            const style = document.createElement('style');
            style.id = 'tipStyle';
            style.textContent = `
                @keyframes fadeInOut {
                    0% { opacity: 0; transform: translateX(-50%) translateY(-10px); }
                    10% { opacity: 1; transform: translateX(-50%) translateY(0); }
                    90% { opacity: 1; transform: translateX(-50%) translateY(0); }
                    100% { opacity: 0; transform: translateX(-50%) translateY(-10px); }
                }
            `;
            document.head.appendChild(style);
        }
    }

    tipDiv.style.background = isError ? '#f44336' : '#4caf50';
    tipDiv.style.color = 'white';
    tipDiv.textContent = message;

    setTimeout(() => {
        if (tipDiv && tipDiv.parentNode) {
            tipDiv.remove();
        }
    }, 3000);
}

// 倒计时
function startCountdown(btn) {
    let time = 60;
    btn.disabled = true;
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(() => {
        time--;
        btn.textContent = `${time}秒后重试`;
        if (time <= 0) {
            clearInterval(countdownInterval);
            countdownInterval = null;
            btn.disabled = false;
            btn.textContent = '获取验证码';
        }
    }, 1000);
}

//登录表单
function showLoginModal() {
    document.getElementById('authTitle').innerText = '登录账号';
    document.getElementById('authBody').innerHTML = `
        <div id="loginForm">
            <div class="form-group">
                <label>邮箱</label>
                <input type="email" id="loginEmail" class="auth-input" placeholder="请输入邮箱">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" id="loginPassword" class="auth-input" placeholder="请输入密码">
            </div>
            <button class="btn btn-primary btn-block" onclick="handleLogin()">登录</button>
            <div class="auth-links">
                <a href="#" onclick="showRegisterForm()">还没有账号？立即注册</a>
                <a href="#" onclick="showForgotForm()">忘记密码？</a>
            </div>
        </div>
    `;
    showAuthModal();
    //给enter绑handlelogin
    setTimeout(() => {
        const passwordInput = document.getElementById('loginPassword');
        if (passwordInput) {
            passwordInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') handleLogin();
            });
        }
    }, 100);
}

//登录输入
async function handleLogin() {
    const emailInput = document.getElementById('loginEmail');
    const email = emailInput?.value.trim();
    const password = document.getElementById('loginPassword')?.value;

    if (!email) {
        showTip('请输入邮箱', true);
        emailInput?.focus();
        return;
    }
    if (!isValidEmail(email)) {
        showTip('请输入正确的邮箱格式', true);
        emailInput?.focus();
        return;
    }
    if (!password) {
        showTip('请输入密码', true);
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
         if (response.status === 401) {
                showTip('邮箱或密码错误', true);
                return;
            }
        const data = await response.json();

        if (response.ok) {
            localStorage.setItem('token', data.token);
            localStorage.setItem('user', JSON.stringify(data.user));
            updateAuthUI();
            closeAuthModal();
            window.dispatchEvent(new StorageEvent('storage', {
                key: 'token',
                newValue: data.token,
                oldValue: null
            }));

            const displayName = data.user.nickname || data.user.email.split('@')[0];
            showTip(`欢迎回来，${displayName}！`, false);

            if (typeof updateAccountUI === 'function') updateAccountUI();
            if (typeof loadFaces === 'function') loadFaces();
        } else {
            showTip(data.detail || '登录失败，请检查邮箱和密码', true);
        }
    } catch (err) {
        console.error('登录错误:', err);
        showTip('网络错误，请稍后重试', true);
    }
}

//注册表单
function showRegisterForm() {
    document.getElementById('authTitle').innerText = '注册账号';
    document.getElementById('authBody').innerHTML = `
        <div id="registerForm">
            <div class="form-group">
                <label>邮箱</label>
                <input type="email" id="regEmail" class="auth-input" placeholder="请输入邮箱" oninput="validateEmailInput(this)">
                <span id="emailError" style="font-size: 12px; color: #f44336; display: none;">邮箱格式不正确</span>
            </div>
            <div class="form-group">
                <label>昵称（可选）</label>
                <input type="text" id="regNickname" class="auth-input" placeholder="昵称，不填则使用邮箱前缀">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" id="regPassword" class="auth-input" placeholder="请输入密码（至少6位）">
            </div>
            <div class="form-group">
                <label>确认密码</label>
                <input type="password" id="regConfirmPassword" class="auth-input" placeholder="请再次输入密码">
            </div>
            <div class="form-group">
                <label>验证码</label>
                <div class="code-group">
                    <input type="text" id="regCode" class="auth-input" placeholder="请输入验证码">
                    <button class="btn btn-outline" id="sendCodeBtn" onclick="sendRegisterCode()">获取验证码</button>
                </div>
            </div>
            <button class="btn btn-primary btn-block" onclick="handleRegister()">注册</button>
            <div class="auth-links">
                <a href="#" onclick="showLoginModal()">已有账号？返回登录</a>
            </div>
        </div>
    `;
    showAuthModal();
}

//发验证码
async function sendRegisterCode() {
    const emailInput = document.getElementById('regEmail');
    const email = emailInput?.value.trim();

    if (!email) {
        showTip('请输入邮箱地址', true);
        emailInput?.focus();
        return;
    }
    if (!isValidEmail(email)) {
        showTip('请输入正确的邮箱格式，例如: user@example.com', true);
        emailInput?.focus();
        return;
    }

    const btn = document.getElementById('sendCodeBtn');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = '发送中...';

    try {
        const response = await fetch(`${API_BASE}/auth/send-code`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, type: 'register' })
        });

        const data = await response.json();

        if (response.ok) {
            showTip('验证码已发送，请查看后端控制台', false);
            startCountdown(btn);
        } else {
            showTip(data.detail || data.message || '发送失败，请稍后重试', true);
            btn.disabled = false;
            btn.textContent = '获取验证码';
        }
    } catch (err) {
        console.error('发送验证码错误:', err);
        showTip('网络错误，请检查后端服务是否运行', true);
        btn.disabled = false;
        btn.textContent = '获取验证码';
    }
}

//注册
async function handleRegister() {
    const email = document.getElementById('regEmail')?.value.trim();
    const nickname = document.getElementById('regNickname')?.value.trim();
    const password = document.getElementById('regPassword')?.value;
    const confirmPassword = document.getElementById('regConfirmPassword')?.value;
    const code = document.getElementById('regCode')?.value.trim();

    if (!email) {
        showTip('请输入邮箱', true);
        return;
    }
    if (!isValidEmail(email)) {
        showTip('请输入正确的邮箱格式', true);
        return;
    }
    if (!password) {
        showTip('请输入密码', true);
        return;
    }
    if (password !== confirmPassword) {
        showTip('两次输入的密码不一致', true);
        return;
    }
    if (password.length < 6) {
        showTip('密码长度至少6位', true);
        return;
    }
    if (!code) {
        showTip('请输入验证码', true);
        return;
    }
    if (!/^\d{6}$/.test(code)) {
        showTip('验证码应为6位数字', true);
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, nickname, password, code })
        });

        const data = await response.json();

        if (response.ok) {
            localStorage.setItem('token', data.token);
            localStorage.setItem('user', JSON.stringify(data.user));
            updateAuthUI();
            closeAuthModal();
            const displayName = data.user.nickname || data.user.email.split('@')[0];
            showTip(`注册成功！欢迎 ${displayName}`, false);

            if (typeof updateAccountUI === 'function') updateAccountUI();
        } else {
            showTip(data.detail || '注册失败', true);
        }
    } catch (err) {
        console.error('注册错误:', err);
        showTip('网络错误，请稍后重试', true);
    }
}

//召回密码表单
function showForgotForm() {
    document.getElementById('authTitle').innerText = '重置密码';
    document.getElementById('authBody').innerHTML = `
        <div id="forgotForm">
            <div class="form-group">
                <label>邮箱</label>
                <input type="email" id="resetEmail" class="auth-input" placeholder="请输入注册邮箱">
            </div>
            <div class="form-group">
                <label>验证码</label>
                <div class="code-group">
                    <input type="text" id="resetCode" class="auth-input" placeholder="请输入验证码">
                    <button class="btn btn-outline" id="sendResetCodeBtn" onclick="sendResetCode()">获取验证码</button>
                </div>
            </div>
            <div class="form-group">
                <label>新密码</label>
                <input type="password" id="resetPassword" class="auth-input" placeholder="请输入新密码（至少6位）">
            </div>
            <div class="form-group">
                <label>确认新密码</label>
                <input type="password" id="resetConfirmPassword" class="auth-input" placeholder="请再次输入新密码">
            </div>
            <button class="btn btn-primary btn-block" onclick="handleResetPassword()">重置密码</button>
            <div class="auth-links">
                <a href="#" onclick="showLoginModal()">返回登录</a>
            </div>
        </div>
    `;
    showAuthModal();
}

//重置密码验证码
async function sendResetCode() {
    const emailInput = document.getElementById('resetEmail');
    const email = emailInput?.value.trim();

    if (!email) {
        showTip('请输入邮箱地址', true);
        emailInput?.focus();
        return;
    }
    if (!isValidEmail(email)) {
        showTip('请输入正确的邮箱格式，例如: user@example.com', true);
        emailInput?.focus();
        return;
    }

    const btn = document.getElementById('sendResetCodeBtn');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = '发送中...';

    try {
        const response = await fetch(`${API_BASE}/auth/send-code`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, type: 'reset' })
        });

        const data = await response.json();

        if (response.ok) {
            showTip('验证码已发送，请查看后端控制台', false);
            startCountdown(btn);
        } else {
            showTip(data.detail || data.message || '发送失败，请稍后重试', true);
            btn.disabled = false;
            btn.textContent = '获取验证码';
        }
    } catch (err) {
        console.error('发送验证码错误:', err);
        showTip('网络错误，请检查后端服务是否运行', true);
        btn.disabled = false;
        btn.textContent = '获取验证码';
    }
}


//重置密码
async function handleResetPassword() {
    const email = document.getElementById('resetEmail')?.value.trim();
    const code = document.getElementById('resetCode')?.value.trim();
    const newPassword = document.getElementById('resetPassword')?.value;
    const confirmPassword = document.getElementById('resetConfirmPassword')?.value;

    if (!email) {
        showTip('请输入邮箱', true);
        return;
    }
    if (!isValidEmail(email)) {
        showTip('请输入正确的邮箱格式', true);
        return;
    }
    if (!code) {
        showTip('请输入验证码', true);
        return;
    }
    if (!/^\d{6}$/.test(code)) {
        showTip('验证码应为6位数字', true);
        return;
    }
    if (!newPassword) {
        showTip('请输入新密码', true);
        return;
    }
    if (newPassword !== confirmPassword) {
        showTip('两次输入的密码不一致', true);
        return;
    }
    if (newPassword.length < 6) {
        showTip('密码长度至少6位', true);
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, code, new_password: newPassword })
        });

        const data = await response.json();

        if (response.ok) {
            showTip('密码重置成功，请重新登录', false);
            setTimeout(() => {
                showLoginModal();
            }, 1500);
        } else {
            showTip(data.detail || '重置失败', true);
        }
    } catch (err) {
        console.error('重置密码错误:', err);
        showTip('网络错误，请稍后重试', true);
    }
}

//logout
function logout() {
    if (confirm('确定要退出登录吗？')) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        updateAuthUI();
        if (typeof updateAccountUI === 'function') updateAccountUI();
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

//获取用户
function getCurrentUser() {
    const userStr = localStorage.getItem('user');
    if (userStr) {
        try {
            return JSON.parse(userStr);
        } catch(e) {
            return null;
        }
    }
    return null;
}


// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    initAuth();
});