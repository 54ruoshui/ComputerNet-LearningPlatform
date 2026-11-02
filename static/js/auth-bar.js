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

    function avatarEl(nickname, className) {
        const av = el('div', className);
        av.textContent = (nickname || '?').trim().charAt(0).toUpperCase() || '?';
        return av;
    }

    async function loadSessions(panel) {
        const list = panel.querySelector('.auth-session-list');
        const countEl = panel.querySelector('.auth-menu-count');
        list.innerHTML = '<div class="auth-session-loading"><i class="fas fa-spinner fa-spin"></i>加载中…</div>';
        try {
            const resp = await fetch('/api/sessions');
            const data = await resp.json();
            const owned = (data.sessions || []).filter(s => s.user_owned);
            const current = localStorage.getItem('graphrag_session_id');
            if (countEl) countEl.textContent = owned.length ? String(owned.length) : '';
            if (!owned.length) {
                list.innerHTML = ''
                    + '<div class="auth-session-empty">'
                    + '<div class="auth-session-empty-icon"><i class="far fa-comments"></i></div>'
                    + '<div class="auth-session-empty-text">暂无账号会话</div>'
                    + '<div class="auth-session-empty-sub">在本站提问产生的会话会自动同步到此账号</div>'
                    + '</div>';
                return;
            }
            list.innerHTML = '';
            for (const s of owned) {
                const isCurrent = s.session_id === current;
                const item = el('button', 'auth-session-item' + (isCurrent ? ' current' : ''));
                const icon = el('div', 'auth-session-icon');
                icon.innerHTML = '<i class="far fa-comment-dots"></i>';
                const body = el('div', 'auth-session-body');
                const top = el('div', 'auth-session-top');
                const title = el('div', 'auth-session-title');
                title.textContent = s.last_question || '（无文字记录）';
                top.appendChild(title);
                if (isCurrent) {
                    const badge = el('span', 'auth-session-badge');
                    badge.innerHTML = '<i class="fas fa-check"></i>当前';
                    top.appendChild(badge);
                }
                const meta = el('div', 'auth-session-meta');
                meta.innerHTML = '<i class="far fa-comment"></i>';
                const count = el('span');
                count.textContent = `${s.message_count} 条对话`;
                meta.appendChild(count);
                body.appendChild(top);
                body.appendChild(meta);
                item.appendChild(icon);
                item.appendChild(body);
                if (!isCurrent) item.addEventListener('click', () => switchSession(s.session_id));
                list.appendChild(item);
            }
        } catch (e) {
            list.innerHTML = '<div class="auth-session-empty"><div class="auth-session-empty-text">会话加载失败</div></div>';
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
        btn.appendChild(avatarEl(user.nickname, 'auth-avatar'));
        const name = el('span', 'auth-user-name');
        name.textContent = user.nickname;
        btn.appendChild(name);
        btn.appendChild(el('i', 'fas fa-chevron-down auth-chevron'));
        wrap.appendChild(btn);

        const panel = el('div', 'auth-menu');

        const header = el('div', 'auth-menu-header');
        header.appendChild(avatarEl(user.nickname, 'auth-avatar auth-avatar-lg'));
        const idBlock = el('div', 'auth-menu-id');
        const nick = el('div', 'auth-menu-nick');
        nick.textContent = user.nickname;
        const email = el('div', 'auth-menu-email');
        email.textContent = user.email;
        idBlock.appendChild(nick);
        idBlock.appendChild(email);
        header.appendChild(idBlock);
        panel.appendChild(header);

        const sectionTitle = el('div', 'auth-menu-title');
        const titleText = el('span');
        titleText.textContent = '账号会话';
        const countEl = el('span', 'auth-menu-count');
        sectionTitle.appendChild(titleText);
        sectionTitle.appendChild(countEl);
        panel.appendChild(sectionTitle);
        const list = el('div', 'auth-session-list');
        panel.appendChild(list);

        const divider = el('div', 'auth-menu-divider');
        panel.appendChild(divider);

        const logoutBtn = el('button', 'auth-logout-btn');
        const logoutIcon = el('i', 'fas fa-arrow-right-from-bracket');
        const logoutText = el('span');
        logoutText.textContent = '退出登录';
        logoutBtn.appendChild(logoutIcon);
        logoutBtn.appendChild(logoutText);
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
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') wrap.classList.remove('open');
        });
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
