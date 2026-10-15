// auth.js - 用户认证（登录 / 注册 / 找回密码 / 资料）
var LF = window.LookUFace;
// 依赖：api.js（需先引入）。API 基址、请求封装、提示、日志上报均由 api.js 提供。

/*
*logs 由于各个页面都继承auth.js 于是在这里写了 所有前端控制台输出都走forntlogsload 然后给sendlog
**login showloginmodal显示登录认证框 然后给登录按钮绑handleLogin handlelogin调auth\login接口 接口调db对比邮箱密码 正确则寸token和用户数据然后放行
***register showregisterform显示注册认证框 给验证码按钮绑sendregistercode 调用auht\sentcode接口 用smtplib发验证码 给注册按钮handleregister handleregister确认验证码无误把新用户写入db
****changepassword 流程同register

FrontLogsLoad() 把所有前端控制台输出sendlog()
sendLog() 加载历史日志/把前端控制台输出贴进html
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

// API 基址、请求封装、提示与日志上报均由 api.js 提供。
// 注意：这里**不能**再写 `const LF = window.LookUFace;` ——
// manager.js / account.js 也会引用 LF，同页多个 const 声明会触发
// 'Identifier LF has already been declared' 语法错误，导致脚本整体不执行。
// 下面的 `LF` 是 api.js 挂到 window 上的全局引用（var 语义，可安全重复引用）。
let countdownInterval = null; //验证码计时器

// 说明：旧版在这里劫持了 console.log/warn/error 并把**所有**输出上报服务端，
// 结果页面里任何调试打印都会变成服务端日志噪声（manager.js 的调试输出曾把日志刷满）。
// 现在改为显式上报：只有 api.js 中的 LookUFace.log.error/warn 会发送，
// 以及 api.js 里注册的 window error / unhandledrejection 全局兜底。

//初始化
var __authInitialized = false;
function initAuth() {
    // 幂等：DOMContentLoaded 与"脚本加载时 DOM 已就绪"两条路径都调用它
    if (__authInitialized) return;
    __authInitialized = true;

    // 两步分别兜错：任何一步抛异常都不应阻止另一步。
    // 回归背景：updateAuthUI() 曾因 ReferenceError 抛错，
    // 而它排在 bindAuthEvents() 之前，导致**点击事件从未被绑定**，
    // 表现为导航栏按钮点不动、文案也不更新。
    try {
        updateAuthUI();
    } catch (err) {
        if (window.console && console.error) console.error('updateAuthUI 失败:', err);
    }
    try {
        bindAuthEvents();
    } catch (err) {
        if (window.console && console.error) console.error('bindAuthEvents 失败:', err);
    }
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

/**
 * HTML 转义。
 *
 * **为什么 auth.js 需要自己定义一份**：
 * ``escapeHtml`` 在 api.js 里是 IIFE 闭包内的局部函数，只通过
 * ``window.LookUFace.escapeHtml`` 暴露，**并不存在同名全局函数**。
 * 所以这里不能直接调用裸的 ``escapeHtml(...)``——
 * 而且 idx.html 目前的脚本顺序是 ``api.js → idx.js → auth.js``，
 * 连 ``window.LookUFace`` 都可能还没就绪。
 *
 * 因此本文件自带一份实现，保证**不依赖脚本加载顺序**。
 * （曾经用过 ``LF.escapeHtml``，在 idx.html 上同样会炸。）
 */
function authEscapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
}

//更新
function updateAuthUI() {
    const loginBtn = document.getElementById('loginBtn');
    const user = getCurrentUser();

    if (user && user.id) {
        if (loginBtn) {
            const displayName = user.nickname || (user.email ? user.email.split('@')[0] : '用户');
            // 去掉原先的 emoji，只显示名字
            loginBtn.innerHTML = `<span>${authEscapeHtml(displayName)}</span>`;
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
// 旧版自建一个 div 用 3 秒动画提示；现在统一转接 api.js 的 toast，
// 避免每个页面各写一套提示组件（showTip 的 isError 语义保持一致）。
function showTip(message, isError = true) {
    LF.toast(message, isError);
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
        const data = await LF.api.post('/auth/login', { email, password }, { skipAuthRedirect: true });

        LF.auth.setSession(data.token, data.user);
        updateAuthUI();
        closeAuthModal();

        showTip(`欢迎回来，${LF.auth.displayName()}！`, false);

        if (typeof updateAccountUI === 'function') updateAccountUI();
        if (typeof loadFaces === 'function') loadFaces();
    } catch (err) {
        if (err.status === 401) {
            showTip('邮箱或密码错误', true);
        } else {
            // 网络层失败时 err.message 已包含完整诊断（见 api.js 的 NetworkError）
            showTip(err.message || '登录失败，请稍后重试', true);
        }
        LF.log.warn('登录失败: ' + (err.message || err));
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
        const data = await LF.api.post('/auth/send-code', { email, type: 'register' });
        showTip(data.message || '验证码已发送，请查收邮件', false);
        startCountdown(btn);
    } catch (err) {
        showTip(err.message || '发送失败，请稍后重试', true);
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
        const data = await LF.api.post('/auth/register', { email, nickname, password, code });

        LF.auth.setSession(data.token, data.user);
        updateAuthUI();
        closeAuthModal();
        showTip(`注册成功！欢迎 ${LF.auth.displayName()}`, false);

        if (typeof updateAccountUI === 'function') updateAccountUI();
    } catch (err) {
        showTip(err.message || '注册失败', true);
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
        const data = await LF.api.post('/auth/send-code', { email, type: 'reset' });
        showTip(data.message || '验证码已发送，请查收邮件', false);
        startCountdown(btn);
    } catch (err) {
        showTip(err.message || '发送失败，请稍后重试', true);
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
        await LF.api.post('/auth/reset-password', {
            email, code, new_password: newPassword
        });
        showTip('密码重置成功，请重新登录', false);
        setTimeout(() => {
            showLoginModal();
        }, 1500);
    } catch (err) {
        showTip(err.message || '重置失败', true);
    }
}

//logout
function logout() {
    if (!confirm('确定要退出登录吗？')) return;

    LF.auth.clearSession();
    updateAuthUI();
    if (typeof updateAccountUI === 'function') updateAccountUI();
    LF.toast('已退出登录', false);

    if (window.location.pathname.includes('manager.html')) {
        window.location.reload();
    }
}

//获取用户
// 实现已统一到 api.js（window.getCurrentUser / LookUFace.auth.getUser），
// 这里只做一层转发，保证旧调用点（含 iframe 里的 window.parent.getCurrentUser）继续可用。
function getCurrentUser() {
    return LF.auth.getUser();
}


// 页面加载时初始化
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
        initAuth();
    });
} else {
    // 脚本在 DOM 就绪之后才执行（延迟加载 / 缓存脚本等）时，
    // DOMContentLoaded 已经错过，必须立即初始化，否则按钮既不更新也点不动。
    initAuth();
}