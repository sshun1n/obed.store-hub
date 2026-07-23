/**
 * Каркас интерфейса: сайдбар, мобильная шапка, футер.
 */
async function loadComponents() {
    const isLoginPage = window.location.pathname.includes('login');
    if (isLoginPage) return;

    let currentUser = null;
    try {
        const response = await fetch('/api/v1/users/me');
        if (response.ok) currentUser = await response.json();
    } catch (err) {
        console.error('Auth check failed:', err);
    }

    const path = window.location.pathname;
    const isActive = (p) => (p === '/' ? (path === '/' || path === '/static/' || path.endsWith('/index.html')) : path.startsWith(p) || path.endsWith(p + '.html'));
    const link = (href, num, label) =>
        `<a href="${href}" class="side-link${isActive(href) ? ' active' : ''}"><span class="side-num">${num}</span>${label}</a>`;

    const roleLabel = currentUser
        ? (currentUser.role === 'admin' ? 'администратор' : 'менеджер')
        : '';

    const sidebarHTML = `
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

    const topbarHTML = `
    <header class="topbar">
        <button class="topbar-burger" id="menuBtn" aria-label="Меню">☰</button>
        <a href="/" class="topbar-logo">obed.store<span>&nbsp;hub</span></a>
    </header>`;

    document.body.insertAdjacentHTML('afterbegin', sidebarHTML + topbarHTML);

    const container = document.querySelector('.container');
    if (container) container.insertAdjacentHTML('beforeend', '<footer>© 2026 obed.store — внутренняя система</footer>');

    const menuBtn = document.getElementById('menuBtn');
    const overlay = document.getElementById('sideOverlay');
    if (menuBtn) menuBtn.addEventListener('click', () => document.body.classList.toggle('side-open'));
    if (overlay) overlay.addEventListener('click', () => document.body.classList.remove('side-open'));

    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            await fetch('/api/v1/auth/logout', { method: 'POST' });
            window.location.href = '/login';
        });
    }
}

document.addEventListener('DOMContentLoaded', loadComponents);
