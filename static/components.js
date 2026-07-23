/**
 * Каркас интерфейса: сайдбар, мобильная шапка, футер.
 * Сайдбар рисуется мгновенно из кэша sessionStorage (без мерцания при переходах),
 * затем данные пользователя проверяются на сервере и кэш обновляется.
 */
(function () {
    const USER_CACHE_KEY = 'obed_hub_user';

    function getCachedUser() {
        try {
            const raw = sessionStorage.getItem(USER_CACHE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch { return null; }
    }

    function setCachedUser(user) {
        try {
            if (user) sessionStorage.setItem(USER_CACHE_KEY, JSON.stringify(user));
            else sessionStorage.removeItem(USER_CACHE_KEY);
        } catch { /* ignore */ }
    }

    function renderSidebar(currentUser) {
        const path = window.location.pathname;
        const isActive = (p) => (p === '/' ? (path === '/' || path === '/static/' || path.endsWith('/index.html')) : path.startsWith(p) || path.endsWith(p + '.html'));
        const link = (href, num, label) =>
            `<a href="${href}" class="side-link${isActive(href) ? ' active' : ''}"><span class="side-num">${num}</span>${label}</a>`;

        const roleLabel = currentUser
            ? (currentUser.role === 'admin' ? 'администратор' : 'менеджер')
            : '';

        return `
        <div class="side-overlay" id="sideOverlay"></div>
        <aside class="sidebar">
            <a href="/" class="side-logo">
                <img src="/static/img/logo.svg" alt="">
                <div>
                    <b>obed.store<span>&nbsp;hub</span></b>
                    <small>рабочая среда</small>
                </div>
            </a>
            <nav>
                <div class="side-caption">Сервисы</div>
                ${link('/', '00', 'Все сервисы')}
                ${link('/nomenclature', '01', 'Номенклатура')}
                ${link('/orders', '02', 'Заказы по точкам')}
                ${link('/management', '03', 'Управление')}
                ${currentUser && currentUser.role === 'admin' ? link('/timesheet', '04', 'Учёт времени') : ''}
                ${currentUser && currentUser.role === 'admin'
                    ? `<div class="side-caption">Система</div>${link('/admin', '05', 'Админ-панель')}`
                    : ''}
            </nav>
            <div class="side-user">
                ${currentUser ? `
                    <div class="side-user-name">${currentUser.full_name || currentUser.login}</div>
                    <div class="side-user-role">${roleLabel}</div>
                    <button id="logoutBtn" class="side-logout">выйти</button>
                ` : ''}
            </div>
        </aside>`;
    }

    function bindSidebarEvents() {
        const menuBtn = document.getElementById('menuBtn');
        const overlay = document.getElementById('sideOverlay');
        if (menuBtn) menuBtn.onclick = () => document.body.classList.toggle('side-open');
        if (overlay) overlay.onclick = () => document.body.classList.remove('side-open');

        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) {
            logoutBtn.onclick = async () => {
                await fetch('/api/v1/auth/logout', { method: 'POST' });
                setCachedUser(null);
                window.location.href = '/login';
            };
        }
    }

    function mountSidebar(currentUser) {
        // Удаляем предыдущую версию (при обновлении данных пользователя)
        document.getElementById('sideOverlay')?.remove();
        document.querySelector('aside.sidebar')?.remove();
        document.body.insertAdjacentHTML('afterbegin', renderSidebar(currentUser));
        bindSidebarEvents();
    }

    async function loadComponents() {
        const isLoginPage = window.location.pathname.includes('login');
        if (isLoginPage) return;

        // 1. Мгновенный рендер из кэша — без ожидания сети
        const cachedUser = getCachedUser();
        mountSidebar(cachedUser);

        const topbarHTML = `
        <header class="topbar">
            <button class="topbar-burger" id="menuBtn" aria-label="Меню">☰</button>
            <a href="/" class="topbar-logo">obed.store<span>&nbsp;hub</span></a>
        </header>`;
        document.querySelector('aside.sidebar').insertAdjacentHTML('afterend', topbarHTML);
        bindSidebarEvents();

        const container = document.querySelector('.container');
        if (container) container.insertAdjacentHTML('beforeend', '<footer>© 2026 obed.store — внутренняя система</footer>');

        // 2. Фоновая проверка: обновляем сайдбар, только если данные изменились
        try {
            const response = await fetch('/api/v1/users/me');
            const freshUser = response.ok ? await response.json() : null;
            if (JSON.stringify(freshUser) !== JSON.stringify(cachedUser)) {
                setCachedUser(freshUser);
                mountSidebar(freshUser);
            }
        } catch (err) {
            console.error('Auth check failed:', err);
        }
    }

    document.addEventListener('DOMContentLoaded', loadComponents);
})();
