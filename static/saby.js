// СБИС по дню поставки: заказы, реализации, реестр 1С, перевозка и ЭТрН по нашим точкам. Только просмотр
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtMoney, toast } = Hub;
    const el = (id) => document.getElementById(id);
    const content = el('content');
    const notices = el('notices');
    const stateEl = el('sabyState');
    const refreshBtn = el('refreshBtn');
    let dates = [];
    let data = null;
    let polling = null;

    const picker = PeriodPicker.create({
        root: el('periodPicker'), button: el('periodBtn'), pop: el('periodPop'),
        kindEl: el('periodKind'), labelEl: el('periodLabel'), prev: el('prevPeriod'), next: el('nextPeriod'),
        // Документы СБИС живут по дням поставки: неделю или месяц сводим к первому дню
        onChange: (p) => { if (p.kind !== 'day') picker.set('day', p.start, p.start); load(); },
    });

    const day = () => picker.get().start;
    const fmtInt = (n) => new Intl.NumberFormat('ru-RU').format(n || 0);
    const timeText = (iso) => {
        if (!iso) return '';
        const [d, t] = iso.split('T');
        return d === Hub.isoDate(new Date()) ? `сегодня в ${t.slice(0, 5)}` : `${Hub.fmtDate(d)} в ${t.slice(0, 5)}`;
    };

    // Цвет состояния: закончено, в пути, черновик, остальное
    function stateBadge(doc) {
        if (!doc) return '<span class="badge badge-muted">нет в СБИС</span>';
        const s = doc.state || '—';
        const cls = /завершено успешно/i.test(s) ? 'badge-green'
            : /с проблемами|ошибк|отклон|удал/i.test(s) ? 'badge-red'
            : /редактируется|черновик/i.test(s) ? 'badge-yellow'
            : 'badge-blue';
        return `<span class="badge ${cls}">${esc(s)}</span>`;
    }

    function docCell(doc, { showNumber = true } = {}) {
        if (!doc) return stateBadge(null);
        const num = showNumber && doc.number ? `<b>№ ${esc(doc.number)}</b> ` : '';
        const link = doc.link ? `<a class="saby-link" href="${esc(doc.link)}" target="_blank" rel="noopener" title="Открыть в кабинете">↗</a>` : '';
        return `<div>${num}${link}</div><div class="saby-state">${stateBadge(doc)}</div>`;
    }

    function registryCell(p) {
        const r = p.registry;
        if (!r) return '<span class="text-muted">нет строки</span>';
        const warn = [];
        if (!r.sum_ok) warn.push(`сумма в 1С ${fmtMoney(r.amount)}, у нас ${fmtMoney(p.gross)}`);
        if (r.number_ok === false) warn.push(`в СБИС реализация № ${p.shipment.number}`);
        return `<div><b>№ ${esc(r.number)}</b></div>${warn.length
            ? `<div class="saby-warn">${warn.map(esc).join('<br>')}</div>`
            : '<div class="cell-sub">сумма сходится</div>'}`;
    }

    function updCell(s) {
        if (!s) return '<span class="text-muted">—</span>';
        if (s.upd_sent) return '<span class="badge badge-green">УПД отправлен</span>';
        if (s.accepted) return '<span class="badge badge-yellow">принято, ждёт УПД</span>';
        return '<span class="badge badge-muted">приёмки ещё нет</span>';
    }

    function stat(label, n, total) {
        const cls = total && n === total ? ' saby-full' : n ? '' : ' saby-none';
        return `<div class="stat-card${cls}"><span class="stat-label">${label}</span><span class="stat-value">${n} <small>из ${total}</small></span></div>`;
    }

    function render() {
        const d = data, s = d.stats;
        const parts = [];
        if (!d.configured) parts.push('<div class="lavka-notice"><b>Нет доступа к СБИС.</b> На сервере не заданы SABY_LOGIN и SABY_PASSWORD.</div>');
        if (d.loose_waybills.length) {
            parts.push(`<div class="lavka-notice">ЭТрН с грузом Обеда без нашего номера заказа: ${d.loose_waybills.map(w => `№ ${esc(w.number)}${w.address ? ` (${esc(w.address)})` : ''}`).join(', ')}.</div>`);
        }
        if (d.registry_free.length) {
            parts.push(`<div class="lavka-notice lavka-notice-info">В реестре 1С за этот день есть строки без нашей точки: ${d.registry_free.map(r => `№ ${esc(r.number)} ${esc(r.address || '')} — ${fmtMoney(r.amount)}`).join('; ')}.</div>`);
        }
        notices.innerHTML = parts.join('');
        if (!d.points.length) {
            content.innerHTML = '<div class="empty-state"><h3>В этот день наших заказов нет</h3></div>';
            return;
        }
        if (!d.checked_at) {
            content.innerHTML = `<div class="empty-state"><h3>Кабинет за ${esc(d.label)} ещё не читали</h3><p>Нажмите «Обновить из СБИС»: приложение только посмотрит, что там уже есть.</p></div>`;
            return;
        }
        const transport = d.transport.length ? d.transport.map(t => `
            <div class="saby-transport">
                <div>
                    <span class="stat-label">Заказ на перевозку</span>
                    <div class="saby-transport-main"><b>№ ${esc(t.number || '—')}</b>${t.link ? ` <a class="saby-link" href="${esc(t.link)}" target="_blank" rel="noopener">↗</a>` : ''} ${stateBadge(t)}</div>
                    <div class="cell-sub">${esc(t.party || 'перевозчик не указан')}${t.amount ? ` · ${fmtMoney(t.amount)}` : ''} · груз «${esc(t.cargo || '—')}» · ЭТрН в нём: ${t.waybills}</div>
                </div>
            </div>`).join('')
            : '<div class="saby-transport saby-transport-none"><span class="stat-label">Заказ на перевозку</span><div>За этот день заказа на перевозку с грузом Обеда в кабинете нет</div></div>';

        content.innerHTML = `
        <div class="stats-grid saby-stats">
            ${stat('Заказы в СБИС', s.orders, s.points)}
            ${stat('Реализации', s.shipments, s.points)}
            ${stat('Реестр 1С', s.registry, s.points)}
            ${stat('ЭТрН', s.waybills, s.points)}
            ${stat('Приняты Лавкой', s.accepted, s.points)}
            ${stat('УПД отправлен', s.upd_sent, s.points)}
        </div>
        ${transport}
        <div class="table-wrapper data-table saby-table">
            <table>
                <thead><tr>
                    <th>Точка</th><th>Заказ Лавки</th><th>Реализация</th><th>Реестр 1С</th><th>ЭТрН</th><th>УПД</th>
                </tr></thead>
                <tbody>${d.points.map(p => `
                    <tr>
                        <td>
                            <div title="${esc(p.address || '')}">${esc(p.address_short || p.point)}</div>
                            <div class="cell-sub">${esc(p.order_no)} · ${fmtInt(p.quantity)} шт · ${fmtMoney(p.gross)}</div>
                        </td>
                        <td>${docCell(p.order, { showNumber: false })}</td>
                        <td>${docCell(p.shipment)}</td>
                        <td>${registryCell(p)}</td>
                        <td>${docCell(p.waybill)}</td>
                        <td>${updCell(p.shipment)}</td>
                    </tr>`).join('')}
                </tbody>
            </table>
        </div>
        <p class="field-hint saby-hint">Свои документы отобраны по связи с нашими заказами ЯЛ: кабинет общий с Ателье. Реализации приложение не открывает — их номер и состояние берутся из списка документов, иначе у незавершённых ломается отправка УПД.</p>`;
    }

    async function load() {
        try { history.replaceState(null, '', `?day=${day()}`); } catch { /* не критично */ }
        content.innerHTML = '<div class="empty-state">Загрузка…</div>';
        try {
            data = await api(`/api/v1/saby/day?day=${day()}`);
            render();
            stateEl.textContent = data.checked_at ? `Прочитано ${timeText(data.checked_at)}` : '';
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    }

    function showProgress(p) {
        refreshBtn.disabled = p.running;
        if (p.running) {
            stateEl.innerHTML = `<span class="lavka-busy">${esc(p.step || 'Читаю СБИС…')}</span>`;
        }
    }

    async function poll() {
        try {
            const st = await api('/api/v1/saby/status');
            showProgress(st.progress);
            if (!st.progress.running) {
                clearInterval(polling);
                polling = null;
                if (st.progress.error) toast(st.progress.error, 'error');
                else toast('Кабинет прочитан');
                await load();
            }
        } catch { /* следующая попытка */ }
    }

    refreshBtn.addEventListener('click', async () => {
        try {
            await api(`/api/v1/saby/refresh?day=${day()}`, { method: 'POST' });
            showProgress({ running: true, step: 'Вход в СБИС' });
            if (!polling) polling = setInterval(poll, 1500);
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    el('registryInput').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        e.target.value = '';
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        try {
            const r = await fetch('/api/v1/saby/registry', { method: 'POST', body: form });
            const body = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(body.detail || 'Реестр не загрузился');
            toast(`Реестр загружен: ${body.rows} строк за ${body.label}`);
            await load();
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    (async () => {
        try {
            const [user, st] = await Promise.all([api('/api/v1/users/me'), api('/api/v1/saby/status')]);
            el('registryBtn').hidden = user.role !== 'admin';
            dates = (await api('/api/v1/lavka/dates')).map(x => x.date);
            picker.setMarked(dates);
            const q = new URLSearchParams(location.search).get('day');
            const today = Hub.isoDate(new Date());
            const start = /^\d{4}-\d{2}-\d{2}$/.test(q || '') ? q
                : ([...dates].reverse().find(x => x <= today) || dates[dates.length - 1] || today);
            picker.set('day', start, start);
            await load();
            if (st.progress.running) { showProgress(st.progress); polling = setInterval(poll, 1500); }
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    })();
});
