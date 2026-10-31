/* 登录/注册页 — 双 mode 切换、提交、登录后接续账号会话并跳回来源页 */

let authMode = 'login';

function switchMode(mode) {
    authMode = mode;
    document.getElementById('tabLogin').classList.toggle('active', mode === 'login');
    document.getElementById('tabRegister').classList.toggle('active', mode === 'register');
    document.getElementById('nicknameGroup').style.display = mode === 'register' ? '' : 'none';
    document.getElementById('confirmGroup').style.display = mode === 'register' ? '' : 'none';
    document.getElementById('password').autocomplete = mode === 'register' ? 'new-password' : 'current-password';
    document.getElementById('authSubmitLabel').textContent = mode === 'register' ? '创建账号' : '登录';
    hideError();
    const url = new URL(window.location.href);
    if (mode === 'register') { url.searchParams.set('mode', 'register'); } else { url.searchParams.delete('mode'); }
    history.replaceState(null, '', url);
}

function showError(msg) {
    const el = document.getElementById('authError');
    el.innerHTML = '<i class="fas fa-circle-exclamation"></i><span></span>';
    el.querySelector('span').textContent = msg;
    el.style.display = 'flex';
}

function hideError() {
    document.getElementById('authError').style.display = 'none';
}

// 仅接受站内相对路径（以单个 / 开头），防开放重定向
function safeNext() {
    const next = new URLSearchParams(window.location.search).get('next');
    if (next && next.startsWith('/') && !next.startsWith('//')) return next;
    return '/';
}

function disableSubmit(seconds) {
    const btn = document.getElementById('authSubmitBtn');
    if (!seconds) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-right-to-bracket"></i> <span id="authSubmitLabel">' + (authMode === 'register' ? '创建账号' : '登录') + '</span>'; return; }
    btn.disabled = true;
    const tick = () => {
        btn.textContent = `请 ${seconds} 秒后重试`;
        if (seconds-- > 0) { setTimeout(tick, 1000); } else { disableSubmit(0); }
    };
    tick();
}

async function submitAuth(ev) {
    ev.preventDefault();
    hideError();
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;

    if (authMode === 'register') {
        const nickname = document.getElementById('nickname').value.trim();
        const confirm = document.getElementById('passwordConfirm').value;
        if (!nickname) { showError('请填写昵称'); return; }
        if (password.length < 8) { showError('密码至少 8 位'); return; }
        if (password !== confirm) { showError('两次输入的密码不一致'); return; }
    }

    const payload = {
        email,
        password,
        session_id: localStorage.getItem('graphrag_session_id') || null,
    };
    if (authMode === 'register') {
        payload.nickname = document.getElementById('nickname').value.trim();
    }

    const btn = document.getElementById('authSubmitBtn');
    btn.disabled = true;
    try {
        const resp = await fetch(`/api/auth/${authMode}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showError(data.detail || '请求失败，请稍后再试');
            if (resp.status === 429) {
                const retry = parseInt(resp.headers.get('Retry-After'), 10);
                if (retry > 0) disableSubmit(Math.min(retry, 60));
            }
            return;
        }
        // 服务端返回账号接续会话（跨设备找回）；null 表示沿用/新建当前会话
        if (data.session_id) {
            localStorage.setItem('graphrag_session_id', data.session_id);
        }
        window.location.href = safeNext();
    } catch (e) {
        showError('网络错误，请检查连接后重试');
    } finally {
        btn.disabled = false;
    }
}

// 已登录用户直接回首页
(async function init() {
    const urlMode = new URLSearchParams(window.location.search).get('mode');
    if (urlMode === 'register') switchMode('register');
    try {
        const resp = await fetch('/api/auth/me');
        const data = await resp.json();
        if (data.user) window.location.href = safeNext();
    } catch (e) { /* 游客照常 */ }
})();
