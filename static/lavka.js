// Заказы Яндекс.Лавки: выбор даты поставки, вид по точкам и по товарам, проверка почты
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtDate, fmtMoney, toast } = Hub;
    const el = (id) => document.getElementById(id);

    const content = el('content');
    const daySelect = el('daySelect');
    const mailState = el('mailState');
    const checkBtn = el('checkBtn');

    let dates = [];          // [{date, orders, quantity, gross}], новые сверху
    let current = null;      // ответ /lavka/orders
    let view = 'points';
    let isAdmin = false;
    const STORAGE_KEY = 'obed_lavka_view';

    try { view = localStorage.getItem(STORAGE_KEY) || 'points'; } catch { /* приватный режим */ }

    const WEEKDAY = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
    function dayLabel(iso) {
        const d = Hub.parseIso(iso);
        return `${fmtDate(iso)}, ${WEEKDAY[d.getDay()]}`;
    }

    function formatDateTime(iso) {
        if (!iso) return '';
        const [day, time] = iso.split('T');
        const today = Hub.isoDate(new Date());
        return day === today ? `сегодня в ${time.slice(0, 5)}` : `${fmtDate(day)} в ${time.slice(0, 5)}`;
    }

    // --- Почта ---

    function showMailStatus(status) {
        if (!status.configured) {
            mailState.innerHTML = isAdmin
                ? 'Почта не настроена: <a href="/admin">админ-панель</a>'
                : 'Почта не настроена';
            checkBtn.hidden = true;
            return;
        }
        checkBtn.hidden = false;
        const parts = [];
        if (status.history && status.history.running) {
            parts.push('<span class="lavka-busy">Загружается история почты…</span>');
        } else if (status.last_poll_at) {
            parts.push(`Почта проверена ${formatDateTime(status.last_poll_at)}`);
        } else {
            parts.push('Почту ещё не проверяли');
        }
        if (status.last_poll_error) {
            parts.push(`<span class="ts-warn" title="${esc(status.last_poll_error)}">ошибка</span>`);
        }
        mailState.innerHTML = parts.join(' · ');
        mailState.title = status.last_poll_result || '';
    }

    async function loadStatus() {
        try {
            showMailStatus(await api('/api/v1/lavka/status'));
        } catch (err) {
            mailState.textContent = err.message;
        }
    }

    checkBtn.addEventListener('click', async () => {
        checkBtn.disabled = true;
        const text = checkBtn.textContent;
        checkBtn.textContent = 'Проверяю…';
        try {
            const result = await api('/api/v1/lavka/check', { method: 'POST' });
            toast(result.summary, result.ok && !result.errors.length ? 'ok' : 'error');
            if (result.created || result.revised) await loadDates(true);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            checkBtn.disabled = false;
            checkBtn.textContent = text;
            loadStatus();
        }
    });

    // --- Даты ---

    function renderDateOptions(selected) {
        if (!dates.length) {
            daySelect.innerHTML = '<option value="">Заказов нет</option>';
            daySelect.disabled = true;
            return;
        }
        daySelect.disabled = false;
        daySelect.innerHTML = dates.map(d =>
            `<option value="${d.date}">${dayLabel(d.date)} · ${d.orders} ${plural(d.orders, 'заказ', 'заказа', 'заказов')}</option>`
        ).join('');
        daySelect.value = selected;
    }

    function plural(n, one, few, many) {
        const mod10 = n % 10, mod100 = n % 100;
        if (mod10 === 1 && mod100 !== 11) return one;
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
        return many;
    }

    // Ближайшая поставка от сегодня, иначе последняя
    function defaultDate() {
        const today = Hub.isoDate(new Date());
        const upcoming = dates.filter(d => d.date >= today);
        return (upcoming.length ? upcoming[upcoming.length - 1] : dates[0]).date;
    }

    async function loadDates(keepSelection = false) {
        const previous = daySelect.value;
        dates = await api('/api/v1/lavka/dates');
        if (!dates.length) {
            renderDateOptions('');
            el('stats').hidden = true;
            content.innerHTML = `<div class="empty-state"><h3>Заказов пока нет</h3>Они появятся после проверки почты${isAdmin ? ' или загрузки файла' : ''}.</div>`;
            return;
        }
        const urlDay = new URLSearchParams(location.search).get('day');
        let day = keepSelection && dates.some(d => d.date === previous) ? previous : null;
        if (!day && urlDay && dates.some(d => d.date === urlDay)) day = urlDay;
        renderDateOptions(day || defaultDate());
        await loadDay();
    }

    function stepDay(delta) {
        // Список новые сверху: «вперёд» по времени — к началу списка
        const idx = dates.findIndex(d => d.date === daySelect.value);
        const next = dates[idx - delta];
        if (next) {
            daySelect.value = next.date;
            loadDay();
        }
    }

    // --- Заказы дня ---

    async function loadDay() {
        const day = daySelect.value;
        if (!day) return;
        el('prevDay').disabled = dates.findIndex(d => d.date === day) >= dates.length - 1;
        el('nextDay').disabled = dates.findIndex(d => d.date === day) <= 0;
        try {
            history.replaceState(null, '', `?day=${day}`);
        } catch { /* не критично */ }
        content.innerHTML = '<div class="empty-state">Загрузка…</div>';
        try {
            current = await api(`/api/v1/lavka/orders?day=${day}`);
            render();
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    }

    function productCell(item) {
        if (item.product) {
            return `<div>${esc(item.name)}</div><div class="cell-sub">в справочнике: ${esc(item.product.title)}</div>`;
        }
        const link = isAdmin
            ? ` <button type="button" class="btn btn-secondary btn-sm lavka-link-btn" data-act="link" data-barcode="${esc(item.barcode)}">Связать</button>`
            : '';
        return `<span>${esc(item.name)}</span> <span class="badge badge-yellow" title="Штрих-кода ${esc(item.barcode)} нет в справочнике">нет в справочнике</span>${link}`;
    }

    function renderStats() {
        const t = current.totals;
        el('stats').hidden = false;
        el('statOrders').textContent = t.orders;
        el('statQty').textContent = t.quantity;
        el('statGross').textContent = fmtMoney(t.gross);
        el('statUnmatched').textContent = t.unmatched;
        el('statUnmatchedCard').classList.toggle('stat-warn', t.unmatched > 0);
    }

    function renderPoints() {
        return `<div class="orders-grid">${current.orders.map(order => `
            <div class="order-card lavka-order">
                <div class="order-header lavka-order-head">
                    <div>
                        <div class="lavka-point">${esc(order.point.name)}</div>
                        <div class="cell-sub">${esc(order.point.address || '')}</div>
                    </div>
                    <div class="lavka-order-meta">
                        ${order.revised_at ? `<span class="badge badge-yellow" title="Лавка прислала исправленный заказ">исправлен</span>` : ''}
                        <span class="mono-cell">${esc(order.order_no)}</span>
                        <span class="lavka-order-total">${order.total_quantity} шт · ${fmtMoney(order.total_gross)}</span>
                    </div>
                </div>
                <div class="order-body">
                    ${order.lines.map(line => `
                        <div class="order-item">
                            <div class="item-name-details">
                                <span class="item-main-name">${esc(line.name)}${line.product ? '' : ' <span class="badge badge-yellow" title="Штрих-кода нет в справочнике">?</span>'}</span>
                                <span class="item-composition mono-cell">${esc(line.barcode)}</span>
                            </div>
                            <span class="item-qty">${line.quantity}</span>
                        </div>`).join('')}
                </div>
            </div>`).join('')}</div>`;
    }

    function renderItems() {
        return `
        <div class="table-wrapper data-table lavka-items">
            <table>
                <thead><tr><th>Товар</th><th>Штрих-код</th><th class="num">Точек</th><th class="num">Штук</th><th class="num">Сумма с НДС</th></tr></thead>
                <tbody>${current.items.map(item => `
                    <tr>
                        <td>${productCell(item)}</td>
                        <td class="mono-cell">${esc(item.barcode)}</td>
                        <td class="num">${item.points}</td>
                        <td class="num"><b>${item.quantity}</b></td>
                        <td class="num">${fmtMoney(item.gross)}</td>
                    </tr>`).join('')}
                </tbody>
                <tfoot><tr><td colspan="3">Итого</td><td class="num"><b>${current.totals.quantity}</b></td><td class="num">${fmtMoney(current.totals.gross)}</td></tr></tfoot>
            </table>
        </div>`;
    }

    function render() {
        if (!current.orders.length) {
            el('stats').hidden = true;
            content.innerHTML = '<div class="empty-state"><h3>На эту дату заказов нет</h3></div>';
            return;
        }
        renderStats();
        content.innerHTML = view === 'items' ? renderItems() : renderPoints();
    }

    el('viewSeg').addEventListener('click', (e) => {
        const btn = e.target.closest('.seg-btn');
        if (!btn) return;
        view = btn.dataset.view;
        try { localStorage.setItem(STORAGE_KEY, view); } catch { /* приватный режим */ }
        el('viewSeg').querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('active', b === btn));
        if (current) render();
    });
    el('viewSeg').querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('active', b.dataset.view === view));

    // Несопоставленные товары видны в виде «По товарам»
    el('statUnmatchedCard').addEventListener('click', () => {
        el('viewSeg').querySelector('[data-view="items"]').click();
    });

    // --- Привязка штрих-кода Лавки к товару (админ) ---

    const linkModal = el('linkModal');
    const linkList = el('linkList');
    const linkSearch = el('linkSearch');
    const linkError = el('linkError');
    let linkItem = null;       // строка сводки по товарам
    let suggestions = [];      // похожие по названию
    let allProducts = null;    // справочник целиком, грузится при первом поиске

    function productRow(p, hint = '') {
        const code = p.barcode ? `<span class="mono-cell">${esc(p.barcode)}</span>` : '<span class="text-muted">без кода</span>';
        return `
            <button type="button" class="link-option" role="option" data-id="${p.id}">
                <span class="link-option-title">${esc(p.title)}${p.is_hidden ? ' <span class="badge badge-muted">скрыт</span>' : ''}</span>
                <span class="link-option-meta">${hint}${code}</span>
            </button>`;
    }

    function renderLinkList() {
        const term = linkSearch.value.trim().toLowerCase();
        if (!term) {
            linkList.innerHTML = suggestions.length
                ? '<div class="link-caption">Похожие по названию</div>' + suggestions.map(p => productRow(p)).join('')
                : '<div class="link-empty">Похожих не нашлось. Найдите блюдо поиском или создайте товар.</div>';
            return;
        }
        const found = (allProducts || []).filter(p => p.title.toLowerCase().includes(term) || (p.barcode || '').includes(term)).slice(0, 30);
        linkList.innerHTML = found.length
            ? found.map(p => productRow(p)).join('')
            : '<div class="link-empty">Ничего не найдено</div>';
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
            // Конфликт: код уже у другого товара или у товара свой код. Решает человек
            if (!body.replace && /уже/.test(err.message) && !body.create_name) {
                const ok = await Hub.confirmDialog({
                    title: 'Заменить штрих-код?',
                    text: `${err.message}. Записать код Лавки ${body.barcode} в выбранный товар?`,
                    okText: 'Заменить',
                });
                if (ok) return sendLink({ ...body, replace: true });
                return null;
            }
            throw err;
        }
    }

    async function finishLink(result) {
        if (!result) return;
        Hub.closeModal(linkModal);
        toast(result.created ? `Создан товар «${result.product.title}»` : `Связано с «${result.product.title}»`);
        allProducts = null;
        await loadDay();
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

    daySelect.addEventListener('change', loadDay);
    el('prevDay').addEventListener('click', () => stepDay(-1));
    el('nextDay').addEventListener('click', () => stepDay(1));

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
            await loadDates(true);
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    async function init() {
        try {
            const user = await api('/api/v1/users/me');
            isAdmin = user.role === 'admin';
            el('adminActions').hidden = !isAdmin;
        } catch (err) {
            console.error('Не удалось определить роль:', err);
        }
        loadStatus();
        try {
            await loadDates();
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
        // Пока открыта страница, статус почты обновляется: опрос идёт по расписанию
        setInterval(() => { if (!document.hidden) loadStatus(); }, 60000);
    }

    init();
});
