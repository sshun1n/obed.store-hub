// СБИС по дню поставки: точки, их документы и следующий шаг — реестр, реализации, перевозка, ЭТрН
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtMoney, toast } = Hub;
    const el = (id) => document.getElementById(id);
    const content = el('content');
    const notices = el('notices');
    const stateEl = el('sabyState');
    const refreshBtn = el('refreshBtn');
    let data = null;
    let selecting = false;
    let selected = new Set();
    let polling = null;
    let lastResult = null;
    let carrierId = null;

    const picker = PeriodPicker.create({
        root: el('periodPicker'), button: el('periodBtn'), pop: el('periodPop'),
        kindEl: el('periodKind'), labelEl: el('periodLabel'), prev: el('prevPeriod'), next: el('nextPeriod'),
        // Документы СБИС живут по дням поставки: выбирается только день
        kinds: ['day'],
        onChange: () => { selected.clear(); lastResult = null; load(); },
    });
    const day = () => picker.get().start;
    const fmtInt = (n) => new Intl.NumberFormat('ru-RU').format(n || 0);
    const timeText = (iso) => {
        if (!iso) return '';
        const [d, t] = iso.split('T');
        const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
        if (d === Hub.isoDate(new Date())) return `сегодня в ${t.slice(0, 5)}`;
        if (d === Hub.isoDate(tomorrow)) return `завтра в ${t.slice(0, 5)}`;
        return `${Hub.fmtDate(d)} в ${t.slice(0, 5)}`;
    };
    const plural = (n, one, few, many) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
        return many;
    };

    // --- Короткие состояния ---

    const STATES = [
        [/завершено успешно/i, 'Завершён', 'ok'],
        [/с проблемами/i, 'С проблемами', 'bad'],
        [/не определено/i, 'Расхождения', 'bad'],
        [/редактируется|черновик/i, 'Черновик', 'draft'],
        [/удал/i, 'Удалён', 'bad'],
        [/отклон|ошибк|аннулир/i, null, 'bad'],
        [/в обработке/i, 'В обработке', 'muted'],
    ];
    function chip(state) {
        if (!state) return '';
        const hit = STATES.find(([re]) => re.test(state));
        const text = hit && hit[1] ? hit[1] : state;
        const tone = hit ? hit[2] : 'info';
        return `<span class="st st-${tone}" title="${esc(state)}">${esc(text)}</span>`;
    }
    const link = (href, text) => href
        ? `<a class="saby-doc" href="${esc(href)}" target="_blank" rel="noopener" title="Открыть в кабинете">${text}<span aria-hidden="true">↗</span></a>`
        : `<span class="saby-doc">${text}</span>`;

    // Заказ и реализация — одной ячейкой: номер реализации, ссылка ведёт на заказ
    function shipmentCell(p) {
        const order = p.order, ship = p.shipment;
        if (!order && !ship) return '<span class="saby-none">нет в СБИС</span>';
        if (!ship) return `${link(order && order.link, 'Заказ')} ${chip(order && order.state)}<div class="saby-sub">реализации нет</div>`;
        const r = p.registry;
        let reg = '';
        if (r) {
            const bad = !r.sum_ok || r.number_ok === false;
            const why = [!r.sum_ok ? `сумма в 1С ${fmtMoney(r.amount)}, у нас ${fmtMoney(p.gross)}` : '',
                r.number_ok === false ? `в 1С № ${r.number}` : ''].filter(Boolean).join('; ');
            reg = bad ? `<span class="saby-reg saby-reg-bad" title="${esc(why)}">1С: ${esc(why)}</span>`
                : '<span class="saby-reg" title="Номер и сумма сходятся с реестром 1С">1С ✓</span>';
        }
        return `${link(order ? order.link : ship.link, `№ ${esc(ship.number || '—')}`)} ${chip(ship.state)}${reg ? `<div class="saby-sub">${reg}</div>` : ''}`;
    }
    function waybillCell(w) {
        if (!w) return '<span class="saby-none">—</span>';
        return `${link(w.link, `№ ${esc(w.number || '—')}`)} ${chip(w.state)}`;
    }
    function updCell(s) {
        if (!s) return '<span class="saby-none">—</span>';
        if (s.upd_sent) return '<span class="st st-ok">Отправлен</span>';
        if (s.accepted) return '<span class="st st-draft">Принято, ждёт УПД</span>';
        return '<span class="saby-none">приёмки нет</span>';
    }

    // --- Шаги ---

    const CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.4 6.6 11.3 12.5 4.9" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    const targets = () => data.points.filter(p => !selecting || selected.has(p.order_no));
    const ordersParam = () => (selecting ? [...selected] : []);

    function stepCard(n, title, state, body, done = false, locked = false) {
        return `<article class="saby-step${done ? ' is-done' : ''}${locked ? ' is-locked' : ''}">
            <header><span class="saby-step-n">${done ? CHECK : n}</span><h3>${title}</h3></header>
            <p class="saby-step-state">${state}</p>
            ${body ? `<div class="saby-step-actions">${body}</div>` : ''}
        </article>`;
    }
    const btn = (act, text, { primary = false, disabled = false, title = '' } = {}) =>
        `<button type="button" class="btn ${primary ? 'btn-primary' : 'btn-secondary'} btn-sm" data-act="${act}"${disabled ? ' disabled' : ''}${title ? ` title="${esc(title)}"` : ''}>${text}</button>`;

    function renderSteps() {
        const d = data, s = d.stats, admin = d.is_admin, set = d.settings;
        const t = targets();
        const sel = selecting ? ` · выбрано ${t.length}` : '';
        const n = d.points.length;

        // 1. Реестр
        const regDone = s.registry === n;
        const step1 = stepCard(1, 'Реестр 1С',
            d.registry_rows ? `Строк для точек: ${s.registry} из ${n}` : 'Не загружен — без него не будет номеров УПД',
            admin ? btn('registry', d.registry_rows ? 'Заменить реестр' : 'Загрузить реестр', { primary: !d.registry_rows }) : '',
            regDone);

        // 2. Реализации
        const toCreate = t.filter(p => !p.shipment && p.order && p.registry);
        const toProcess = t.filter(p => p.shipment && /редактируется/i.test(p.shipment.state || ''));
        const processed = d.points.filter(p => p.shipment && !/редактируется/i.test(p.shipment.state || '')).length;
        const shipDone = processed === n;
        const step2 = stepCard(2, 'Реализации',
            `Создано ${s.shipments} из ${n}, проведено ${processed}${sel}`,
            admin && !shipDone ? [
                btn('ship-all', 'Создать и провести', { primary: true, disabled: !toCreate.length && !toProcess.length }),
                set.hide_split_shipments ? '' : btn('ship-create', 'Создать', { disabled: !toCreate.length, title: toCreate.length ? '' : 'Создавать нечего: у точек уже есть реализации или нет строки реестра' }),
                set.hide_split_shipments ? '' : btn('ship-process', 'Провести', { disabled: !toProcess.length, title: toProcess.length ? '' : 'Нет созданных и непроведённых реализаций' }),
            ].join('') : '',
            shipDone);

        // 3. Заказ на перевозку
        const tr = d.our_transport;
        let step3;
        if (tr) {
            const state = `№ ${esc(tr.number || '—')} ${chip(tr.state)} · ${esc(tr.party || 'перевозчик не указан')}`;
            step3 = stepCard(3, 'Заказ на перевозку', state,
                admin && tr.draft ? btn('transport-sign', 'Подписать', { primary: true }) : '', !tr.draft);
        } else {
            const options = d.carriers.map(c => `<option value="${c.id}"${c.id === (carrierId || d.last_carrier_id) ? ' selected' : ''}>${esc(c.name)}</option>`).join('');
            step3 = stepCard(3, 'Заказ на перевозку', `Ещё не создан · точек в рейсе: ${t.length}`,
                admin ? (d.carriers.length ? `<select class="styled-text-input saby-carrier" id="carrierSelect" aria-label="Перевозчик">${options}</select>
                    ${btn('transport-all', 'Создать и подписать', { primary: true, disabled: !t.length })}
                    ${set.hide_split_transport ? '' : btn('transport-create', 'Создать', { disabled: !t.length })}
                    ${set.hide_split_transport ? '' : btn('transport-sign', 'Подписать', { disabled: true, title: 'Сначала создайте заказ' })}`
                    : '<span class="saby-none">Нет перевозчиков — добавьте в разделе «Перевозчики»</span>') : '');
        }

        // 4. ЭТрН
        const signed = tr && tr.signed_by_carrier;
        const drafts = new Set(d.draft_waybills);
        const toWaybill = t.filter(p => !p.waybill);
        const toSign = t.filter(p => drafts.has(p.order_no));
        let state4;
        if (!tr) state4 = 'После заказа на перевозку';
        else if (!signed && tr.waiting) state4 = `Ждём подпись перевозчика${tr.next_check_at ? ` · проверю ${timeText(tr.next_check_at).replace('сегодня ', '')}` : ''}`;
        else if (!signed) state4 = 'Заказ на перевозку ещё не отправлен перевозчику';
        else state4 = `Создано ${s.waybills} из ${n}${drafts.size ? `, не подписано ${drafts.size}` : ''}${sel}`;
        let body4 = '';
        if (tr && !signed && tr.waiting) body4 = btn('transport-check', 'Проверить');
        else if (admin && signed) {
            body4 = [
                btn('waybill-all', 'Создать и подписать', { primary: true, disabled: !toWaybill.length && !toSign.length }),
                set.hide_split_waybills ? '' : btn('waybill-create', 'Создать', { disabled: !toWaybill.length }),
                set.hide_split_waybills ? '' : btn('waybill-sign', 'Подписать', { disabled: !toSign.length, title: toSign.length ? '' : 'Нет неподписанных ЭТрН' }),
            ].join('');
        }
        const step4 = stepCard(4, 'ЭТрН', state4, body4, s.waybills === n && !drafts.size, !signed);

        return `<section class="saby-steps">${step1}${step2}${step3}${step4}</section>`;
    }

    // --- Таблица ---

    function renderTable() {
        const d = data;
        const all = d.points.length && d.points.every(p => selected.has(p.order_no));
        return `
        <div class="saby-table-head">
            <h2 class="card-title-small">Точки</h2>
            <span class="lavka-section-sub">${d.points.length} · ${fmtInt(d.points.reduce((a, p) => a + p.quantity, 0))} шт</span>
            ${d.is_admin ? `<button type="button" class="saby-select-toggle${selecting ? ' on' : ''}" id="selectToggle" aria-pressed="${selecting}">
                <span class="saby-select-dot" aria-hidden="true"></span>${selecting ? `Выбрано ${selected.size} · готово` : 'Выбрать точки'}</button>` : ''}
        </div>
        <div class="table-wrapper data-table saby-table${selecting ? ' selecting' : ''}">
            <table>
                <thead><tr>
                    <th class="sel-col"><span class="sel-box"><input type="checkbox" id="selAll" aria-label="Выбрать все"${all ? ' checked' : ''}></span></th>
                    <th>Точка</th><th>Реализация</th><th>ЭТрН</th><th>УПД</th>
                </tr></thead>
                <tbody>${d.points.map(p => `
                    <tr class="${selecting && selected.has(p.order_no) ? 'is-selected' : ''}" data-order="${esc(p.order_no)}">
                        <td class="sel-col"><span class="sel-box"><input type="checkbox" data-pick="${esc(p.order_no)}" aria-label="Выбрать точку"${selected.has(p.order_no) ? ' checked' : ''}></span></td>
                        <td>
                            <div title="${esc(p.address || '')}">${esc(p.address_short || p.point)}</div>
                            <div class="saby-sub">${esc(p.order_no)} · ${fmtInt(p.quantity)} шт · ${fmtMoney(p.gross)}</div>
                        </td>
                        <td>${shipmentCell(p)}</td>
                        <td>${waybillCell(p.waybill)}</td>
                        <td>${updCell(p.shipment)}</td>
                    </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
    }

    function renderNotices() {
        const d = data, parts = [];
        if (!d.configured) parts.push('<div class="lavka-notice"><b>Нет доступа к СБИС.</b> На сервере не заданы SABY_LOGIN и SABY_PASSWORD.</div>');
        if (lastResult) {
            const errs = Object.entries(lastResult.errors || {});
            if (errs.length) parts.push(`<div class="lavka-notice"><b>Не вышло:</b> ${errs.map(([k, v]) => `${esc(k)} — ${esc(v)}`).join('; ')}</div>`);
            if ((lastResult.notes || []).length) parts.push(`<div class="lavka-notice lavka-notice-info">${lastResult.notes.map(esc).join('<br>')}</div>`);
        }
        if (d.loose_waybills.length) parts.push(`<div class="lavka-notice">ЭТрН с грузом Обеда без нашего номера заказа: ${d.loose_waybills.map(w => `№ ${esc(w.number)}`).join(', ')}.</div>`);
        if (d.registry_free.length) parts.push(`<div class="lavka-notice lavka-notice-info">В реестре 1С за этот день есть строки без нашей точки: ${d.registry_free.map(r => `№ ${esc(r.number)} ${esc(r.address || '')}`).join('; ')}.</div>`);
        notices.innerHTML = parts.join('');
    }

    function render() {
        const d = data;
        renderNotices();
        el('settingsBtn').hidden = !d.is_admin;
        if (!d.points.length) { content.innerHTML = '<div class="empty-state"><h3>В этот день наших заказов нет</h3></div>'; return; }
        if (!d.checked_at) {
            content.innerHTML = `<div class="empty-state"><h3>Кабинет за ${esc(d.label)} ещё не читали</h3><p>Нажмите «Обновить из СБИС»: приложение посмотрит, что там уже есть.</p></div>`;
            return;
        }
        content.innerHTML = renderSteps() + renderTable();
    }

    // --- Загрузка и операции ---

    async function load() {
        try { history.replaceState(null, '', `?day=${day()}`); } catch { /* не критично */ }
        try {
            data = await api(`/api/v1/saby/day?day=${day()}`);
            if (selecting) selected = new Set([...selected].filter(o => data.points.some(p => p.order_no === o)));
            render();
            if (!polling) stateEl.textContent = data.checked_at ? `Прочитано ${timeText(data.checked_at)}` : '';
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    }

    function setBusy(busy, text = '') {
        refreshBtn.disabled = busy;
        content.querySelectorAll('[data-act]').forEach(b => { if (busy) b.disabled = true; });
        content.classList.toggle('saby-busy', busy);
        if (busy) stateEl.innerHTML = `<span class="lavka-busy">${esc(text || 'Работаю с СБИС…')}</span>`;
    }

    // Ждём фоновую операцию до конца; результат — то, что вернуло действие
    function waitTask() {
        return new Promise((resolve) => {
            const tick = async () => {
                try {
                    const st = await api('/api/v1/saby/status');
                    const p = st.progress;
                    if (p.running) { setBusy(true, `${p.label ? p.label + ': ' : ''}${p.step || ''}`); polling = setTimeout(tick, 1200); return; }
                    polling = null;
                    resolve(p);
                } catch { polling = setTimeout(tick, 2500); }
            };
            tick();
        });
    }

    async function runTask(body) {
        await api('/api/v1/saby/action', { method: 'POST', body: { day: day(), orders: ordersParam(), ...body } });
        setBusy(true, 'Начинаю…');
        const p = await waitTask();
        lastResult = p.result;
        if (p.error) toast(p.error, 'error');
        else if (p.result) {
            const ok = (p.result.done || []).length, bad = Object.keys(p.result.errors || {}).length;
            toast(`${p.label}: готово ${ok}${bad ? `, ошибок ${bad}` : ''}`, bad ? 'error' : 'ok');
        }
        setBusy(false);
        await load();
        return !p.error;
    }

    async function confirmCreate(what, count) {
        return Hub.confirmDialog({
            title: `Создать в СБИС: ${what}?`,
            text: `Это настоящие документы в общем кабинете${count ? `, точек: ${count}` : ''}. Отправленное не отзывается.`,
            okText: 'Создать', danger: false,
        });
    }

    async function sign(kind) {
        const tr = data.our_transport;
        const orders = kind === 'waybill' ? targets().map(p => p.order_no).filter(o => data.draft_waybills.includes(o)) : [];
        const ok = await SabySign.sign({
            day: day(), kind, orders: selecting ? orders : [],
            title: kind === 'transport' ? `Заказ на перевозку № ${tr ? tr.number : ''}` : 'ЭТрН',
            what: kind === 'transport' ? 'Подпись отправит заказ перевозчику.' : `Подпись отметит погрузку и отправит накладные Лавке: ${selecting ? orders.length : data.draft_waybills.length}.`,
        });
        if (ok) { setBusy(true, 'Перечитываю день'); await waitTask(); setBusy(false); await load(); }
    }

    async function act(name) {
        const t = targets();
        try {
            if (name === 'registry') { el('registryInput').click(); return; }
            if (name === 'transport-check') {
                setBusy(true, 'Спрашиваю СБИС');
                const r = await api(`/api/v1/saby/check-transport?day=${day()}`, { method: 'POST' });
                toast(r.signed ? 'Перевозчик подписал заказ' : `Ещё не подписан: ${r.state || '—'}`, r.signed ? 'ok' : 'error');
                setBusy(false);
                await load();
                return;
            }
            if (name === 'ship-create' || name === 'ship-all') {
                const create = t.filter(p => !p.shipment && p.order && p.registry);
                if (create.length) {
                    if (!(await confirmCreate('реализации', create.length))) return;
                    if (!(await runTask({ action: 'create_shipments' }))) return;
                } else if (name === 'ship-create') return;
                if (name === 'ship-all') await runTask({ action: 'process_shipments' });
                return;
            }
            if (name === 'ship-process') {
                if (!(await Hub.confirmDialog({ title: 'Провести реализации?', text: 'Реализации уйдут Лавке, заказы утвердятся. Отправленное не отзывается.', okText: 'Провести', danger: false }))) return;
                await runTask({ action: 'process_shipments' });
                return;
            }
            if (name === 'transport-create' || name === 'transport-all') {
                const carrier = Number(el('carrierSelect')?.value || 0);
                if (!carrier) { toast('Выберите перевозчика', 'error'); return; }
                carrierId = carrier;
                if (!(await confirmCreate('заказ на перевозку', t.length))) return;
                if (!(await runTask({ action: 'create_transport', carrier_id: carrier }))) return;
                if (name === 'transport-all' && data.our_transport && data.our_transport.draft) await sign('transport');
                return;
            }
            if (name === 'transport-sign') { await sign('transport'); return; }
            if (name === 'waybill-create' || name === 'waybill-all') {
                const create = t.filter(p => !p.waybill);
                if (create.length) {
                    if (!(await confirmCreate('ЭТрН', create.length))) return;
                    if (!(await runTask({ action: 'create_waybills' }))) return;
                } else if (name === 'waybill-create') return;
                if (name === 'waybill-all' && data.draft_waybills.length) await sign('waybill');
                return;
            }
            if (name === 'waybill-sign') { await sign('waybill'); }
        } catch (err) {
            setBusy(false);
            toast(err.message, 'error');
        }
    }

    content.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act]');
        if (b && !b.disabled) { act(b.dataset.act); return; }
        if (e.target.closest('#selectToggle')) {
            selecting = !selecting;
            if (selecting && !selected.size) selected = new Set(data.points.map(p => p.order_no));
            render();
            return;
        }
        // В режиме выбора строка выбирается кликом по ней, ссылки работают как обычно
        const row = e.target.closest('tr[data-order]');
        if (selecting && row && !e.target.closest('a') && !e.target.matches('input')) {
            const box = row.querySelector('[data-pick]');
            box.checked = !box.checked;
            box.dispatchEvent(new Event('change', { bubbles: true }));
        }
    });
    content.addEventListener('change', (e) => {
        if (e.target.id === 'carrierSelect') { carrierId = Number(e.target.value); return; }
        if (e.target.id === 'selAll') {
            selected = e.target.checked ? new Set(data.points.map(p => p.order_no)) : new Set();
            render();
            return;
        }
        const pick = e.target.dataset.pick;
        if (!pick) return;
        e.target.checked ? selected.add(pick) : selected.delete(pick);
        // Перерисовываем шаги и счётчик, а таблицу не трогаем — галочки не мигают
        e.target.closest('tr').classList.toggle('is-selected', e.target.checked);
        const steps = content.querySelector('.saby-steps');
        steps.outerHTML = renderSteps();
        const toggle = el('selectToggle');
        if (toggle) toggle.lastChild.textContent = `Выбрано ${selected.size} · готово`;
        const all = el('selAll');
        if (all) all.checked = selected.size === data.points.length;
    });

    refreshBtn.addEventListener('click', async () => {
        try {
            await api(`/api/v1/saby/refresh?day=${day()}`, { method: 'POST' });
            setBusy(true, 'Вход в СБИС');
            const p = await waitTask();
            if (p.error) toast(p.error, 'error'); else toast('Кабинет прочитан');
            setBusy(false);
            await load();
        } catch (err) { setBusy(false); toast(err.message, 'error'); }
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
        } catch (err) { toast(err.message, 'error'); }
    });

    // --- Настройки ---

    const settingsModal = el('settingsModal');
    const form = el('settingsForm');
    el('settingsBtn').addEventListener('click', async () => {
        el('settingsError').textContent = '';
        try {
            const s = await api('/api/v1/saby/settings');
            for (const [k, v] of Object.entries(s)) {
                const input = form.elements[k];
                if (!input) continue;
                if (input.type === 'checkbox') input.checked = !!v; else input.value = v ?? '';
            }
            Hub.openModal(settingsModal);
        } catch (err) { toast(err.message, 'error'); }
    });
    settingsModal.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) Hub.closeModal(settingsModal); });
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {};
        for (const input of form.elements) {
            if (!input.name) continue;
            body[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
        }
        try {
            await api('/api/v1/saby/settings', { method: 'PUT', body });
            Hub.closeModal(settingsModal);
            toast('Настройки сохранены');
            await load();
        } catch (err) { el('settingsError').textContent = err.message; }
    });

    (async () => {
        try {
            const [st, datesList] = await Promise.all([api('/api/v1/saby/status'), api('/api/v1/lavka/dates')]);
            const dates = datesList.map(x => x.date);
            picker.setMarked(dates);
            const q = new URLSearchParams(location.search).get('day');
            const today = Hub.isoDate(new Date());
            const start = /^\d{4}-\d{2}-\d{2}$/.test(q || '') ? q
                : (dates.find(x => x >= today) || dates[dates.length - 1] || today);
            picker.set('day', start, start);
            await load();
            if (st.progress.running) {
                const p = await waitTask();
                lastResult = p.result;
                setBusy(false);
                await load();
            }
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    })();
});
