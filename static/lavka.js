// Заказы Яндекс.Лавки: период, итоги со сравнением, номенклатура по товарам, лист на производство
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtDate, fmtMoney, toast } = Hub;
    const el = (id) => document.getElementById(id);

    const content = el('content');
    const notices = el('notices');
    const mailState = el('mailState');
    const checkBtn = el('checkBtn');

    let dates = [];          // даты поставки с заказами, по возрастанию
    let current = null;      // ответ /lavka/summary
    let isAdmin = false;

    LavkaSheet.init();

    const picker = PeriodPicker.create({
        root: el('periodPicker'),
        button: el('periodBtn'),
        pop: el('periodPop'),
        kindEl: el('periodKind'),
        labelEl: el('periodLabel'),
        prev: el('prevPeriod'),
        next: el('nextPeriod'),
        onChange: () => loadSummary(),
    });

    const fmtInt = (n) => new Intl.NumberFormat('ru-RU').format(n || 0);

    function formatDateTime(iso) {
        if (!iso) return '';
        const [day, time] = iso.split('T');
        return day === Hub.isoDate(new Date()) ? `сегодня в ${time.slice(0, 5)}` : `${fmtDate(day)} в ${time.slice(0, 5)}`;
    }

    // --- Почта ---

    function showMailStatus(status) {
        if (!status.configured) {
            mailState.innerHTML = isAdmin ? 'Почта не настроена: <a href="/admin">админ-панель</a>' : 'Почта не настроена';
            checkBtn.hidden = true;
            return;
        }
        checkBtn.hidden = false;
        const parts = [];
        if (status.history && status.history.running) parts.push('<span class="lavka-busy">Загружается история почты…</span>');
        else if (status.last_poll_at) parts.push(`Почта проверена ${formatDateTime(status.last_poll_at)}`);
        else parts.push('Почту ещё не проверяли');
        if (status.last_poll_error) parts.push(`<span class="ts-warn" title="${esc(status.last_poll_error)}">ошибка</span>`);
        mailState.innerHTML = parts.join(' · ');
        mailState.title = status.last_poll_result || '';
    }

    async function loadStatus() {
        try { showMailStatus(await api('/api/v1/lavka/status')); } catch (err) { mailState.textContent = err.message; }
    }

    checkBtn.addEventListener('click', async () => {
        checkBtn.disabled = true;
        const text = checkBtn.textContent;
        checkBtn.textContent = 'Проверяю…';
        try {
            const result = await api('/api/v1/lavka/check', { method: 'POST' });
            toast(result.summary, result.ok && !result.errors.length ? 'ok' : 'error');
            if (result.created || result.revised) { await loadDates(); await loadSummary(); }
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            checkBtn.disabled = false;
            checkBtn.textContent = text;
            loadStatus();
        }
    });

    // --- Период и данные ---

    async function loadDates() {
        dates = (await api('/api/v1/lavka/dates')).map(d => d.date);
        picker.setMarked(dates);
    }

    // Ближайшая поставка от сегодня, иначе последняя
    function defaultDay() {
        const today = Hub.isoDate(new Date());
        return dates.find(d => d >= today) || dates[dates.length - 1] || today;
    }

    function saveUrl({ kind, start, end }) {
        try { history.replaceState(null, '', `?period=${kind}&start=${start}&end=${end}`); } catch { /* не критично */ }
    }

    function restoreFromUrl() {
        const q = new URLSearchParams(location.search);
        const kind = q.get('period');
        const start = q.get('start');
        const end = q.get('end') || start;
        const ok = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '');
        if (['day', 'week', 'month', 'range'].includes(kind) && ok(start) && ok(end)) {
            picker.set(kind, start, end);
            return true;
        }
        const day = q.get('day');
        if (ok(day)) { picker.set('day', day, day); return true; }
        return false;
    }

    async function loadSummary() {
        const p = picker.get();
        saveUrl(p);
        content.innerHTML = '<div class="empty-state">Загрузка…</div>';
        try {
            current = await api(`/api/v1/lavka/summary?start=${p.start}&end=${p.end}`);
            render();
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    }

    function delta(now, was, money = false) {
        if (!was) return now ? '<span class="delta delta-flat">не с чем сравнить</span>' : '';
        const pct = (now - was) / was * 100;
        const cls = pct > 0 ? 'delta-up' : pct < 0 ? 'delta-down' : 'delta-flat';
        const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '=';
        const wasText = money ? fmtMoney(was) : fmtInt(was);
        return `<span class="delta ${cls}">${arrow} ${Math.abs(Math.round(pct))} %</span> <span class="delta-was">было ${wasText}</span>`;
    }

    function renderStats() {
        const t = current.totals;
        const c = current.compare.totals;
        el('stats').hidden = false;
        el('statOrders').textContent = fmtInt(t.orders);
        el('statPoints').textContent = fmtInt(t.points);
        el('statQty').textContent = fmtInt(t.quantity);
        el('statGross').textContent = fmtMoney(t.gross);
        el('dOrders').innerHTML = delta(t.orders, c.orders);
        el('dPoints').innerHTML = delta(t.points, c.points);
        el('dQty').innerHTML = delta(t.quantity, c.quantity);
        el('dGross').innerHTML = delta(t.gross, c.gross, true);
        const cmp = el('compareLabel');
        cmp.hidden = false;
        cmp.textContent = `Сравнение: ${current.compare.label}.`;
    }

    function plural(n, one, few, many) {
        const mod10 = n % 10, mod100 = n % 100;
        if (mod10 === 1 && mod100 !== 11) return one;
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
        return many;
    }

    // Уведомления над таблицей: уходят сами, когда причина устранена
    function renderNotices() {
        const parts = [];
        if (current.unmatched) {
            const n = current.unmatched;
            parts.push(`<div class="lavka-notice">
                <b>${n} ${plural(n, 'товар', 'товара', 'товаров')} из заказов не ${n === 1 ? 'связан' : 'связаны'} со справочником.</b>
                ${isAdmin ? 'Нажмите «Связать» у товара: штрих-код Лавки запишется в карточку, и дальше сверка пойдёт сама.' : 'Привязать их может администратор.'}
            </div>`);
        }
        if (current.revised) {
            parts.push(`<div class="lavka-notice lavka-notice-info">Лавка прислала исправленные версии заказов: ${current.revised}. В таблице уже последняя версия.</div>`);
        }
        notices.innerHTML = parts.join('');
    }

    function nameCell(item) {
        if (item.product) {
            return `<div>${esc(item.name)}</div><div class="cell-sub">в справочнике: ${esc(item.product.title)}</div>`;
        }
        const link = isAdmin
            ? ` <button type="button" class="btn btn-secondary btn-sm lavka-link-btn" data-act="link" data-barcode="${esc(item.barcode)}">Связать</button>`
            : '';
        return `<div>${esc(item.name)}</div><div class="cell-sub lavka-unmatched"><span class="badge badge-yellow" title="Штрих-кода ${esc(item.barcode)} нет в справочнике">нет в справочнике</span>${link}</div>`;
    }

    function pctBadge(now, was) {
        if (!was) return '<span class="delta delta-flat">новая</span>';
        const pct = (now - was) / was * 100;
        const cls = pct > 0 ? 'delta-up' : pct < 0 ? 'delta-down' : 'delta-flat';
        const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '=';
        return `<span class="delta ${cls}">${arrow} ${Math.abs(Math.round(pct))} %</span>`;
    }

    function render() {
        renderNotices();
        const sheetBtn = el('sheetBtn');
        if (!current.items.length) {
            el('stats').hidden = true;
            el('compareLabel').hidden = true;
            content.innerHTML = '<div class="empty-state"><h3>За этот период заказов нет</h3></div>';
            sheetBtn.disabled = true;
            return;
        }
        sheetBtn.disabled = false;
        renderStats();
        const t = current.totals, c = current.compare.totals;
        content.innerHTML = `
        <div class="table-wrapper data-table lavka-items">
            <table>
                <thead><tr>
                    <th>Товар</th><th>Штрих-код</th><th class="num">Точек</th><th class="num">Штук</th>
                    <th class="num" title="${esc(current.compare.label)}">Было</th><th class="num">Изменение</th><th class="num">Сумма с НДС</th>
                </tr></thead>
                <tbody>${current.items.map(item => `
                    <tr>
                        <td>${nameCell(item)}</td>
                        <td class="mono-cell">${esc(item.barcode)}</td>
                        <td class="num">${item.points}</td>
                        <td class="num"><b>${fmtInt(item.quantity)}</b></td>
                        <td class="num text-muted">${item.was == null ? '—' : fmtInt(item.was)}</td>
                        <td class="num">${pctBadge(item.quantity, item.was)}</td>
                        <td class="num">${fmtMoney(item.gross)}</td>
                    </tr>`).join('')}
                </tbody>
                <tfoot><tr>
                    <td colspan="3">Итого · позиций ${current.items.length}</td>
                    <td class="num"><b>${fmtInt(t.quantity)}</b></td>
                    <td class="num text-muted">${c.quantity ? fmtInt(c.quantity) : '—'}</td>
                    <td class="num">${c.quantity ? pctBadge(t.quantity, c.quantity) : ''}</td>
                    <td class="num">${fmtMoney(t.gross)}</td>
                </tr></tfoot>
            </table>
        </div>`;
    }

    // --- Лист номенклатуры ---

    el('sheetBtn').addEventListener('click', () => {
        if (!current || !current.items.length) return;
        const p = picker.get();
        const a = PeriodPicker.parse(p.start), b = PeriodPicker.parse(p.end);
        const short = (d) => `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`;
        const label = p.start === p.end
            ? `Яндекс ${short(a)}.${String(a.getFullYear()).slice(2)}`
            : `Яндекс ${short(a)}–${short(b)}.${String(b.getFullYear()).slice(2)}`;
        LavkaSheet.open({ start: p.start, end: p.end, label, quantity: fmtInt(current.totals.quantity) });
    });

    // --- Ручная загрузка файла (админ) ---

    el('uploadBtn').addEventListener('click', () => el('uploadInput').click());
    el('uploadInput').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        e.target.value = '';
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        try {
            const r = await fetch('/api/v1/lavka/upload', { method: 'POST', body: form });
            const data = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось загрузить файл');
            const parts = [];
            if (data.created.length) parts.push(`новых заказов: ${data.created.length}`);
            if (data.revised.length) parts.push(`исправлено: ${data.revised.length}`);
            if (data.unchanged.length) parts.push(`без изменений: ${data.unchanged.length}`);
            toast(`Файл загружен: ${parts.join(', ')}`);
            await loadDates();
            await loadSummary();
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    // --- Привязка штрих-кода Лавки к товару (админ) ---

    const linkModal = el('linkModal');
    const linkList = el('linkList');
    const linkSearch = el('linkSearch');
    const linkError = el('linkError');
    let linkItem = null;
    let suggestions = [];
    let allProducts = null;

    function productRow(p) {
        const code = p.barcode ? `<span class="mono-cell">${esc(p.barcode)}</span>` : '<span class="text-muted">без кода</span>';
        return `
            <button type="button" class="link-option" role="option" data-id="${p.id}">
                <span class="link-option-title">${esc(p.title)}${p.is_hidden ? ' <span class="badge badge-muted">скрыт</span>' : ''}</span>
                <span class="link-option-meta">${code}</span>
            </button>`;
    }

    function renderLinkList() {
        const term = linkSearch.value.trim().toLowerCase();
        if (!term) {
            linkList.innerHTML = suggestions.length
                ? '<div class="link-caption">Похожие по названию</div>' + suggestions.map(productRow).join('')
                : '<div class="link-empty">Похожих не нашлось. Найдите блюдо поиском или создайте товар.</div>';
            return;
        }
        const found = (allProducts || []).filter(p => p.title.toLowerCase().includes(term) || (p.barcode || '').includes(term)).slice(0, 30);
        linkList.innerHTML = found.length ? found.map(productRow).join('') : '<div class="link-empty">Ничего не найдено</div>';
    }

    async function openLink(barcode) {
        linkItem = current.items.find(i => i.barcode === barcode);
        if (!linkItem) return;
        el('linkName').textContent = linkItem.name;
        el('linkBarcode').textContent = `штрих-код Лавки ${linkItem.barcode}`;
        linkSearch.value = '';
        linkError.textContent = '';
        suggestions = [];
        linkList.innerHTML = '<div class="link-empty">Подбираю похожие…</div>';
        Hub.openModal(linkModal);
        try {
            suggestions = await api(`/api/v1/lavka/suggest?name=${encodeURIComponent(linkItem.name)}`);
        } catch (err) {
            linkError.textContent = err.message;
        }
        renderLinkList();
    }

    async function sendLink(body) {
        try {
            return await api('/api/v1/lavka/link', { method: 'PUT', body });
        } catch (err) {
            // Код уже у другого товара или у товара свой код — решает человек
            if (!body.replace && !body.create_name && /уже/.test(err.message)) {
                const ok = await Hub.confirmDialog({
                    title: 'Заменить штрих-код?',
                    text: `${err.message}. Записать код Лавки ${body.barcode} в выбранный товар?`,
                    okText: 'Заменить',
                });
                return ok ? sendLink({ ...body, replace: true }) : null;
            }
            throw err;
        }
    }

    async function finishLink(result) {
        if (!result) return;
        Hub.closeModal(linkModal);
        toast(result.created ? `Создан товар «${result.product.title}»` : `Связано с «${result.product.title}»`);
        allProducts = null;
        await loadSummary();
    }

    linkList.addEventListener('click', async (e) => {
        const option = e.target.closest('.link-option');
        if (!option) return;
        linkError.textContent = '';
        try {
            await finishLink(await sendLink({ barcode: linkItem.barcode, product_id: Number(option.dataset.id) }));
        } catch (err) {
            linkError.textContent = err.message;
        }
    });

    linkSearch.addEventListener('input', async () => {
        if (allProducts === null) {
            allProducts = [];
            try {
                const products = await api('/api/v1/products/');
                allProducts = products.map(p => ({
                    id: p.id,
                    title: p.display_name || (p.name_from_sheet || '').split(/\s\d/)[0] || p.name_from_sheet || '',
                    barcode: p.barcode,
                    is_hidden: p.is_hidden,
                }));
            } catch (err) {
                linkError.textContent = err.message;
            }
        }
        renderLinkList();
    });

    el('linkCreate').addEventListener('click', async () => {
        const ok = await Hub.confirmDialog({
            title: 'Создать товар?',
            text: `В справочнике появится «${linkItem.name}» со штрих-кодом Лавки ${linkItem.barcode}. Состав и КБЖУ заполните потом в карточке.`,
            okText: 'Создать',
            danger: false,
        });
        if (!ok) return;
        try {
            await finishLink(await sendLink({ barcode: linkItem.barcode, create_name: linkItem.name }));
        } catch (err) {
            linkError.textContent = err.message;
        }
    });

    el('linkCancel').addEventListener('click', () => Hub.closeModal(linkModal));

    content.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-act="link"]');
        if (btn) openLink(btn.dataset.barcode);
    });

    // --- Запуск ---

    async function init() {
        try {
            const user = await api('/api/v1/users/me');
            isAdmin = user.role === 'admin';
            el('uploadBtn').hidden = !isAdmin;
        } catch (err) {
            console.error('Не удалось определить роль:', err);
        }
        loadStatus();
        try {
            await loadDates();
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
            return;
        }
        if (!restoreFromUrl()) {
            const day = defaultDay();
            picker.set('day', day, day);
        }
        if (!dates.length) {
            content.innerHTML = `<div class="empty-state"><h3>Заказов пока нет</h3>Они появятся после проверки почты${isAdmin ? ' или загрузки файла' : ''}.</div>`;
            el('sheetBtn').disabled = true;
            return;
        }
        await loadSummary();
        setInterval(() => { if (!document.hidden) loadStatus(); }, 60000);
    }

    init();
});
