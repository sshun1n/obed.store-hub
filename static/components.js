/**
 * Каркас интерфейса: сайдбар, мобильная шапка, футер.
 * Скрипт подключён первым в <body> и рисует сайдбар сразу, из кэша sessionStorage:
 * браузер не успевает показать страницу без меню. Затем данные пользователя
 * проверяются на сервере и кэш обновляется.
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
        const isActive = (p) => (p === '/' ? (path === '/' || path === '/static/' || path.endsWith('/index.html')) : path === p || path.endsWith(p + '.html'));
        // extra — страницы, при которых пункт тоже подсвечен (карточка сотрудника → «Сотрудники»)
        const link = (href, num, label, extra = []) =>
            `<a href="${href}" class="side-link${[href, ...extra].some(isActive) ? ' active' : ''}"><span class="side-num">${num}</span>${label}</a>`;
        const isAdmin = currentUser && currentUser.role === 'admin';

        const roleLabel = currentUser
            ? (currentUser.role === 'admin' ? 'администратор' : 'менеджер')
            : '';

        // Логотип встроен в разметку: картинка файлом появлялась бы на каждой странице с задержкой
        return `
        <div class="side-overlay" id="sideOverlay"></div>
        <aside class="sidebar">
            <a href="/" class="side-logo">
                <svg viewBox="0 0 512 512" aria-hidden="true">
                    <rect width="512" height="512" rx="128" fill="#EE4B4B"/>
                    <rect x="160" y="350" width="192" height="70" rx="20" fill="#fff"/>
                    <circle cx="180" cy="260" r="75" fill="#fff"/>
                    <circle cx="332" cy="260" r="75" fill="#fff"/>
                    <circle cx="256" cy="190" r="95" fill="#fff"/>
                    <circle cx="256" cy="190" r="30" fill="#EE4B4B"/>
                </svg>
                <div>
                    <b>obed.store<span>&nbsp;hub</span></b>
                    <small>рабочая среда</small>
                </div>
            </a>
            <nav class="side-nav">
                <div class="side-marker" id="sideMarker"></div>
                <div class="side-caption">Сервисы</div>
                ${link('/', '00', 'Все сервисы')}
                ${link('/nomenclature', '01', 'Номенклатура')}
                ${link('/orders', '02', 'Заказы по точкам')}
                ${isAdmin ? link('/timesheet', '03', 'Учёт времени') : ''}
                ${isAdmin ? link('/employees', '04', 'Сотрудники', ['/employee']) : ''}
                <div class="side-caption">Система</div>
                ${link('/management', '05', 'Продукция')}
                ${isAdmin ? link('/admin', '06', 'Админ-панель') : ''}
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

    // Выделение пункта — отдельный элемент: он один и плавно ездит между пунктами
    function placeMarker() {
        const marker = document.getElementById('sideMarker');
        if (!marker) return;
        const active = document.querySelector('.side-link.active');
        marker.classList.toggle('visible', Boolean(active));
        if (!active) return;
        marker.style.top = `${active.offsetTop}px`;
        marker.style.height = `${active.offsetHeight}px`;
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

        // Выделение едет сразу по нажатию, не дожидаясь загрузки новой страницы
        document.querySelector('.side-nav').onclick = (ev) => {
            const target = ev.target.closest('.side-link');
            if (!target || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
            document.querySelectorAll('.side-link.active').forEach((el) => el.classList.remove('active'));
            target.classList.add('active');
            placeMarker();
        };
    }

    function mountSidebar(currentUser) {
        // Удаляем предыдущую версию (при обновлении данных пользователя)
        document.getElementById('sideOverlay')?.remove();
        document.querySelector('aside.sidebar')?.remove();
        document.body.insertAdjacentHTML('afterbegin', renderSidebar(currentUser));
        bindSidebarEvents();
        placeMarker();
    }

    // Страницы подгружаются заранее, при наведении на пункт меню: переход не ждёт сети
    function addPrefetch() {
        if (!HTMLScriptElement.supports || !HTMLScriptElement.supports('speculationrules')) return;
        const rules = document.createElement('script');
        rules.type = 'speculationrules';
        rules.textContent = JSON.stringify({
            prefetch: [{ where: { selector_matches: '.side-link' }, eagerness: 'moderate' }],
        });
        document.head.appendChild(rules);
    }

    if (window.location.pathname.includes('login')) return;

    // Браузер без плавной смены страниц: контент просто проявляется (см. .no-vt в style.css)
    if (!('onpagereveal' in window)) document.documentElement.classList.add('no-vt');

    // 1. Мгновенный рендер из кэша — без ожидания сети и до первой отрисовки
    const cachedUser = getCachedUser();
    mountSidebar(cachedUser);
    document.querySelector('aside.sidebar').insertAdjacentHTML('afterend', `
        <header class="topbar">
            <button class="topbar-burger" id="menuBtn" aria-label="Меню">☰</button>
            <a href="/" class="topbar-logo">obed.store<span>&nbsp;hub</span></a>
        </header>`);
    bindSidebarEvents();
    addPrefetch();

    // Шрифт мог изменить высоту пунктов
    document.fonts?.ready.then(placeMarker);

    // Возврат кнопкой «назад» показывает страницу из памяти, с выделением на другом пункте
    window.addEventListener('pageshow', (ev) => {
        if (ev.persisted) mountSidebar(getCachedUser());
    });

    document.addEventListener('DOMContentLoaded', async () => {
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
    });
})();
