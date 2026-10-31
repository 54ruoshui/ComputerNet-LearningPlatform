/* 全站 topbar 登录状态 — 游客显示「登录」，已登录显示昵称下拉（账号会话切换 / 退出） */

(function () {
    const SLOT_ID = 'authSlot';

    function el(tag, className) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        return node;
    }

    function renderGuest(slot) {
        const link = el('a', 'topbar-btn auth-entry');
        link.href = '/login?next=' + encodeURIComponent(location.pathname);
        link.innerHTML = '<i class="fas fa-right-to-bracket"></i><span>登录</span>';
        slot.innerHTML = '';
        slot.appendChild(link);
    }

    function fmtTime(iso) {
        try {
            const d = new Date(iso);
            return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
        } catch (e) { return ''; }
    }

    async function loadSessions(panel) {
        const list = panel.querySelector('.auth-session-list');
        list.innerHTML = '<div class="auth-session-loading">加载中…</div>';
        try {
            const resp = await fetch('/api/sessions');
            const data = await resp.json();
            const owned = (data.sessions || []).filter(s => s.user_owned);
            const current = localStorage.getItem('graphrag_session_id');
            if (!owned.length) {
                list.innerHTML = '<div class="auth-session-loading">暂无账号历史会话</div>';
                return;
            }
            list.innerHTML = '';
            for (const s of owned) {
                const item = el('button', 'auth-session-item' + (s.session_id === current ? ' current' : ''));
                const title = el('div', 'auth-session-title');
                title.textContent = s.last_question || '（无文字记录）';
                const meta = el('div', 'auth-session-meta');
                meta.textContent = `${s.message_count} 条 · ${fmtTime(s.last_active_at || '')}`;
                item.appendChild(title);
                item.appendChild(meta);
                item.addEventListener('click', () => switchSession(s.session_id));
                list.appendChild(item);
            }
        } catch (e) {
            list.innerHTML = '<div class="auth-session-loading">加载失败</div>';
        }
    }

    async function switchSession(sessionId) {
        try {
            const resp = await fetch('/api/auth/sessions/claim', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId }),
            });
            if (!resp.ok) return;
            localStorage.setItem('graphrag_session_id', sessionId);
            location.reload();
        } catch (e) { /* 静默 */ }
    }

    async function logout() {
        try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* 忽略 */ }
        // 不清 localStorage 会话：同一浏览器继续以游客身份沿用当前会话学习
        location.reload();
    }

    function renderUser(slot, user) {
        slot.innerHTML = '';

        const wrap = el('div', 'auth-user');
        const btn = el('button', 'auth-user-btn');
        const icon = el('i', 'fas fa-circle-user');
        const name = el('span', '');
        name.textContent = user.nickname;
        btn.appendChild(icon);
        btn.appendChild(name);
        wrap.appendChild(btn);

        const panel = el('div', 'auth-menu');
        const header = el('div', 'auth-menu-header');
        const email = el('div', 'auth-menu-email');
        email.textContent = user.email;
        header.appendChild(email);
        panel.appendChild(header);

        const sectionTitle = el('div', 'auth-menu-title');
        sectionTitle.textContent = '账号会话（点击切换）';
        panel.appendChild(sectionTitle);
        const list = el('div', 'auth-session-list');
        panel.appendChild(list);

        const divider = el('div', 'auth-menu-divider');
        panel.appendChild(divider);

        const logoutBtn = el('button', 'auth-logout-btn');
        logoutBtn.innerHTML = '<i class="fas fa-arrow-right-from-bracket"></i> 退出登录';
        logoutBtn.addEventListener('click', logout);
        panel.appendChild(logoutBtn);

        wrap.appendChild(panel);
        slot.appendChild(wrap);

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = wrap.classList.toggle('open');
            if (open) loadSessions(panel);
        });
        document.addEventListener('click', () => wrap.classList.remove('open'));
    }

    async function init() {
        const slot = document.getElementById(SLOT_ID);
        if (!slot) return;
        try {
            const resp = await fetch('/api/auth/me');
            const data = await resp.json();
            if (data.user) { renderUser(slot, data.user); } else { renderGuest(slot); }
        } catch (e) {
            renderGuest(slot);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
