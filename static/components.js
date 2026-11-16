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
                <div class="side-caption">Яндекс Лавка</div>
                ${link('/lavka', '03', 'Заказы')}
                ${link('/saby', '04', 'СБИС')}
                ${link('/lavka-reports', '05', 'Отчёты')}
                ${link('/lavka-forecast', '06', 'Прогноз')}
                <div class="side-caption">Самокат</div>
                ${link('/samokat', '07', 'Заказы')}
                ${link('/samokat-saby', '08', 'СБИС')}
                <div class="side-caption">Справочники</div>
                ${isAdmin ? link('/timesheet', '09', 'Учёт времени') : ''}
                ${isAdmin ? link('/employees', '10', 'Сотрудники', ['/employee']) : ''}
                ${link('/carriers', '11', 'Перевозчики')}
                ${link('/entities', '12', 'Юрлица')}
                <div class="side-caption">Система</div>
                ${link('/management', '13', 'Продукция')}
                ${isAdmin ? link('/admin', '14', 'Админ-панель') : ''}
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
    // Есть ли пункты выше или ниже видимой части меню — там оно гаснет
    function fadeNav() {
        const nav = document.querySelector('.side-nav');
        if (!nav) return;
        nav.classList.toggle('more-above', nav.scrollTop > 2);
        nav.classList.toggle('more-below', nav.scrollTop + nav.clientHeight < nav.scrollHeight - 2);
    }

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
        // Меню прокручено так же, как на прошлой странице; активный пункт — всегда в видимой части
        const nav = document.querySelector('.side-nav');
        if (nav) {
            try { nav.scrollTop = Number(sessionStorage.getItem('obed_side_scroll')) || 0; } catch { /* не критично */ }
            const active = nav.querySelector('.side-link.active');
            if (active && (active.offsetTop < nav.scrollTop || active.offsetTop + active.offsetHeight > nav.scrollTop + nav.clientHeight)) {
                nav.scrollTop = active.offsetTop - nav.clientHeight / 2;
            }
            nav.addEventListener('scroll', () => {
                fadeNav();
                try { sessionStorage.setItem('obed_side_scroll', String(nav.scrollTop)); } catch { /* не критично */ }
            }, { passive: true });
        }
        fadeNav();
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
    document.fonts?.ready.then(() => { placeMarker(); fadeNav(); });
    window.addEventListener('resize', fadeNav);

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

/**
 * Окошко хода операций с СБИС (Лавка и Самокат). Живёт на каждой странице:
 * ход держит сервер (/api/v1/saby/tasks), поэтому при переходе между разделами
 * окошко не пропадает — новая страница сразу рисует последнее известное
 * состояние из sessionStorage и продолжает опрос.
 *
 * Страницы СБИС подписываются на ход (onUpdate) и ждут конца операции (wait).
 */
window.SabyTask = (() => {
    if (window.location.pathname.includes('login')) return null;
    const STATE_KEY = 'obed_saby_tasks';
    const WATCH_KEY = 'obed_saby_watched';
    const HIDE_KEY = 'obed_saby_task_hidden';
    const DONE_SHOW_MS = 7000;
    const FOREVER = 1e15;            // «пока идёт» — в sessionStorage Infinity не сохраняется
    const NAMES = { lavka: 'СБИС', samokat: 'СБИС Самоката' };
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const read = (key, fallback) => { try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
    const write = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* приватный режим */ } };

    let box = null;
    let timer = null;
    let hideTimer = null;
    let tasks = read(STATE_KEY, { at: 0, list: [] });
    // Операции, которые этот браузер видел идущими: только про них показываем «готово»
    const watched = read(WATCH_KEY, {});
    const listeners = [];
    const waiters = [];

    const key = (t) => `${t.source}|${t.label}|${t.day}`;

    // Доля сделанного: по точкам, если операция идёт по точкам, иначе по шагам
    function percent(t) {
        const rows = Object.values(t.rows || {});
        if (t.kind && rows.length) {
            const finished = rows.filter(r => r.s === 'ok' || r.s === 'skip' || r.s === 'error').length;
            const working = rows.filter(r => r.s === 'work').length;
            return Math.min(100, ((finished + working * 0.5) / rows.length) * 100);
        }
        return t.total ? Math.min(100, (t.done / t.total) * 100) : null;
    }

    function mount() {
        if (box) return;
        box = document.createElement('div');
        box.className = 'saby-task';
        box.setAttribute('role', 'status');
        box.setAttribute('aria-live', 'polite');
        box.hidden = true;
        box.innerHTML = `
            <span class="saby-task-icon" aria-hidden="true"></span>
            <div class="saby-task-body">
                <div class="saby-task-title"></div>
                <div class="saby-task-step"></div>
                <div class="saby-task-bar"><span></span></div>
            </div>
            <button type="button" class="saby-task-close" aria-label="Скрыть">&times;</button>`;
        box.querySelector('.saby-task-close').addEventListener('click', () => {
            const t = current();
            if (t) write(HIDE_KEY, t.running ? key(t) : `${key(t)}|${t.finished_at}`);
            hide();
        });
        document.body.appendChild(box);
    }

    function hide() {
        if (!box || box.hidden) return;
        box.classList.remove('show');
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => { if (!box.classList.contains('show')) box.hidden = true; }, 220);
    }

    // Что показывать: идущую операцию, иначе только что закончившуюся из тех, что мы видели
    function current() {
        const running = tasks.list.find(t => t.running);
        if (running) return running;
        const now = Date.now();
        return tasks.list
            .filter(t => t.finished_at && watched[key(t)] && watched[key(t)] > now)
            .sort((a, b) => (a.finished_at < b.finished_at ? 1 : -1))[0] || null;
    }

    function render() {
        mount();
        const t = current();
        const off = t && [key(t), `${key(t)}|${t.finished_at}`].includes(read(HIDE_KEY, null));
        if (!t || off) { hide(); return; }
        const here = window.location.pathname === t.page;
        const bar = box.querySelector('.saby-task-bar');
        box.dataset.state = t.running ? 'run' : (t.error ? 'error' : (t.counts && t.counts.errors ? 'warn' : 'ok'));
        box.querySelector('.saby-task-title').innerHTML = `<span>${esc(t.label || 'СБИС')}</span>${here ? ''
            : `<a href="${esc(t.page)}${t.day ? `?day=${esc(t.day)}` : ''}">${esc(NAMES[t.source] || 'Открыть')} →</a>`}`;
        let step;
        if (t.running) step = t.step || 'Начинаю';
        else if (t.error) step = t.error;
        else {
            const c = t.counts || {};
            step = ['Готово', c.done ? `сделано ${c.done}` : '', c.skipped ? `пропущено ${c.skipped}` : '',
                c.errors ? `ошибок ${c.errors}` : ''].filter(Boolean).join(' · ');
        }
        const stepEl = box.querySelector('.saby-task-step');
        stepEl.textContent = step;
        stepEl.title = step;
        const pct = t.running ? percent(t) : 100;
        bar.classList.toggle('indeterminate', pct === null);
        if (pct !== null) bar.firstElementChild.style.width = `${Math.max(3, pct)}%`;
        clearTimeout(hideTimer);
        if (box.hidden) { box.hidden = false; requestAnimationFrame(() => box.classList.add('show')); }
        else box.classList.add('show');
    }

    function schedule() {
        clearTimeout(timer);
        const running = tasks.list.some(t => t.running) || waiters.length;
        timer = setTimeout(poll, running ? 900 : (document.hidden ? 60000 : 15000));
    }

    async function poll() {
        const asked = Date.now();
        try {
            const r = await fetch('/api/v1/saby/tasks');
            if (!r.ok) { schedule(); return; }
            const data = await r.json();
            const now = Date.now();
            for (const t of data.tasks) {
                const k = key(t);
                if (t.running) watched[k] = FOREVER;
                else if (watched[k] === FOREVER) watched[k] = now + DONE_SHOW_MS;
            }
            for (const k of Object.keys(watched)) if (watched[k] < now) delete watched[k];
            write(WATCH_KEY, watched);
            tasks = { at: now, list: data.tasks };
            write(STATE_KEY, tasks);
            render();
            listeners.forEach(fn => { try { fn(tasks.list); } catch (err) { console.error(err); } });
            for (let i = waiters.length - 1; i >= 0; i--) {
                const t = tasks.list.find(x => x.source === waiters[i].source);
                // Ответ на запрос, отправленный раньше запуска операции, не в счёт
                if (t && !t.running && asked >= waiters[i].since) { waiters[i].resolve(t); waiters.splice(i, 1); }
            }
            // Окошко «готово» гаснет само
            const shown = current();
            if (shown && !shown.running) setTimeout(render, Math.max(0, watched[key(shown)] - now) + 50);
        } catch { /* сеть моргнула — следующий опрос */ }
        schedule();
    }

    // Сразу — последнее известное состояние, чтобы окошко не мигало при переходе
    if (Date.now() - tasks.at < 20000) render();
    poll();
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });

    // --- Ячейки таблиц: над какой точкой сейчас работа ---
    // Строка таблицы — tr[data-order], ячейка — td[data-col] с содержимым в .cell-main

    const LIVE = {
        wait: (r) => `<span class="cell-spin"></span>${esc(r.t || 'в очереди')}`,
        work: (r) => `<span class="cell-spin"></span>${esc(r.t || 'в работе')}`,
        ok: (r) => `<span class="cell-mark">✓</span>${esc(r.t || 'готово')}`,
        skip: (r) => `<span class="cell-mark">–</span>${esc(r.t || 'пропущено')}`,
        error: (r) => `<span class="cell-mark">!</span>${esc(r.t || 'ошибка')}`,
    };
    const cellOf = (root, order, col) =>
        root.querySelector(`tr[data-order="${CSS.escape(order)}"] td[data-col="${col}"]`);

    function decorate(root, task, day) {
        if (!root) return;
        const rows = task && task.running && task.kind && task.day === day ? task.rows || {} : {};
        root.querySelectorAll('td.is-live').forEach((td) => {
            const order = td.closest('tr').dataset.order;
            if (!rows[order] || td.dataset.col !== task.kind) {
                td.classList.remove('is-live');
                td.querySelector('.cell-live')?.remove();
            }
        });
        for (const [order, r] of Object.entries(rows)) {
            const td = cellOf(root, order, task.kind);
            if (!td) continue;
            let live = td.querySelector('.cell-live');
            if (live && live.dataset.s !== r.s) { live.remove(); live = null; }
            if (!live) {
                td.classList.add('is-live');
                td.insertAdjacentHTML('beforeend', `<span class="cell-live s-${r.s}" data-s="${r.s}"></span>`);
                live = td.querySelector('.cell-live');
            }
            live.innerHTML = (LIVE[r.s] || LIVE.work)(r);
            live.title = r.t || '';
        }
    }

    // Операция кончилась, таблица перерисована: новое состояние в тронутых ячейках проявляется
    function flash(root, task, day) {
        if (!root || !task || !task.kind || task.day !== day) return;
        for (const order of Object.keys(task.rows || {})) {
            const td = cellOf(root, order, task.kind);
            if (td) td.classList.add('cell-flash');
        }
    }

    return {
        decorate,
        flash,
        // Новая операция только что запущена: показать и опрашивать часто
        poke() { write(HIDE_KEY, null); clearTimeout(timer); poll(); },
        onUpdate(fn) { listeners.push(fn); if (tasks.list.length) fn(tasks.list); },
        // Дождаться конца операции источника (lavka | samokat); результат — её ход
        wait(source) { return new Promise((resolve) => { waiters.push({ source, resolve, since: Date.now() }); clearTimeout(timer); poll(); }); },
        get(source) { return tasks.list.find(t => t.source === source) || null; },
    };
})();
