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
        // «Сегодня» — по Москве, как время с сервера: у компьютера может стоять другой пояс
        const msk = (ms) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow' }).format(new Date(ms));
        if (d === msk(Date.now())) return `сегодня в ${t.slice(0, 5)}`;
        if (d === msk(Date.now() + 86400000)) return `завтра в ${t.slice(0, 5)}`;
        if (d === msk(Date.now() - 86400000)) return `вчера в ${t.slice(0, 5)}`;
        return `${Hub.fmtDate(d)} в ${t.slice(0, 5)}`;
    };
    const kg = (v) => `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(v || 0)} кг`;
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

    // Сверка с реестром 1С: метка видна и до создания реализации — строка уже нашлась по адресу
    function regMark(p) {
        const r = p.registry;
        if (!r) return data.registry_rows
            ? '<span class="saby-reg saby-reg-none" title="В загруженном реестре 1С нет строки для этой точки — реализацию не создать">нет в реестре 1С</span>'
            : '';
        const why = [!r.sum_ok ? `сумма в 1С ${fmtMoney(r.amount)}, у нас ${fmtMoney(p.gross)}` : '',
            r.number_ok === false ? `в 1С № ${r.number}` : ''].filter(Boolean).join('; ');
        if (why) return `<span class="saby-reg saby-reg-bad" title="${esc(why)}">1С: ${esc(why)}</span>`;
        const ok = p.shipment ? 'Номер и сумма сходятся с реестром 1С' : `Строка реестра 1С № ${r.number} найдена по адресу, сумма сходится`;
        return `<span class="saby-reg saby-reg-ok" title="${esc(ok)}">✓ ${p.shipment ? 'сходится с 1С' : `в 1С № ${esc(r.number)}`}</span>`;
    }

    // Заказ и реализация — одной ячейкой: номер реализации, ссылка ведёт на заказ
    function shipmentCell(p) {
        const order = p.order, ship = p.shipment;
        if (!order && !ship) return '<span class="saby-none">нет в СБИС</span>';
        const reg = regMark(p);
        if (!ship) return `${link(order && order.link, 'Заказ')} ${chip(order && order.state)}<div class="saby-sub">реализации нет${reg ? ` · ${reg}` : ''}</div>`;
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
            d.registry_rows ? `Строк для точек: ${s.registry} из ${n}` : 'Не загружен',
            admin ? btn('registry', d.registry_rows ? 'Заменить реестр' : 'Загрузить реестр', { primary: !d.registry_rows }) : '',
            regDone);

        // 2. Реализации
        const toCreate = t.filter(p => !p.shipment && p.order && p.registry);
        const toProcess = t.filter(p => p.shipment && /редактируется/i.test(p.shipment.state || ''));
        const processed = d.points.filter(p => p.shipment && !/редактируется/i.test(p.shipment.state || '')).length;
        const shipDone = processed === n;
        const step2 = stepCard(2, 'Реализации',
            `Создано ${s.shipments} из ${n}, проведено ${processed}${sel}`,
            admin && !shipDone ? (!toCreate.length && toProcess.length
                ? btn('ship-process', 'Провести', { primary: true })
                : [
                    btn('ship-all', 'Создать и провести', { primary: true, disabled: !toCreate.length && !toProcess.length }),
                    set.hide_split_shipments ? '' : btn('ship-create', 'Создать', { disabled: !toCreate.length, title: toCreate.length ? '' : 'Создавать нечего: у точек уже есть реализации или нет строки реестра' }),
                    set.hide_split_shipments ? '' : btn('ship-process', 'Провести', { disabled: !toProcess.length, title: toProcess.length ? '' : 'Нет созданных и непроведённых реализаций' }),
                ].join('')) : '',
            shipDone);

        // 3. Заказ на перевозку
        const tr = d.our_transport;
        let step3;
        if (tr) {
            // Номер и перевозчик — ссылкой в кабинет, под ними состояние. Подпись перевозчика
            // проверяет общее автообновление, отдельной кнопки нет
            const text = `№ ${esc(tr.number || '—')} · ${esc(tr.party || 'перевозчик не указан')}`;
            const doc = tr.link ? `<a class="saby-step-doc" href="${esc(tr.link)}" target="_blank" rel="noopener" title="Открыть в кабинете">${text} <span aria-hidden="true">↗</span></a>` : text;
            const status = tr.waiting ? '<span class="st st-draft" title="Заказ отправлен, перевозчик ещё не подписал">ждёт подписи перевозчика</span>' : chip(tr.state);
            // Стоимость рейса называет перевозчик, подписывая заказ (СБИС отдаёт её в «Сумма»)
            const cost = tr.signed_by_carrier && tr.amount != null
                ? `<span class="saby-step-status" title="Указал перевозчик при подписи заказа">стоимость: <b>${esc(Number(tr.amount).toLocaleString('ru-RU', { maximumFractionDigits: 2 }))} ₽</b></span>` : '';
            const state = `${doc}<span class="saby-step-status">${status}</span>${cost}`;
            const body3 = admin && tr.draft ? btn('transport-sign', 'Подписать', { primary: true }) : '';
            step3 = stepCard(3, 'Заказ на перевозку', state, body3, !tr.draft);
        } else {
            step3 = stepCard(3, 'Заказ на перевозку', `Ещё не создан · точек в рейсе: ${t.length}, ${kg(t.reduce((a, p) => a + p.weight_kg, 0))}`,
                admin ? (d.carriers.length ? `${btn('transport-all', 'Создать и подписать', { primary: true, disabled: !t.length })}
                    ${set.hide_split_transport ? '' : btn('transport-create', 'Создать', { disabled: !t.length })}
                    ${set.hide_split_transport ? '' : btn('transport-sign', 'Подписать', { disabled: true, title: 'Сначала создайте заказ' })}`
                    : '<span class="saby-none">Нет перевозчиков — добавьте в разделе «Перевозчики»</span>') : '');
        }

        // 4. ЭТрН
        // ЭТрН можно делать, как только мы подписали заказ — подпись перевозчика не ждём
        const ready = tr && !tr.draft;
        const drafts = new Set(d.draft_waybills);
        const toWaybill = t.filter(p => !p.waybill);
        const toSign = t.filter(p => drafts.has(p.order_no));
        let state4;
        if (!tr) state4 = 'После заказа на перевозку';
        else if (!ready) state4 = 'После подписи заказа на перевозку';
        else state4 = `Создано ${s.waybills} из ${n}${drafts.size ? `, не подписано ${drafts.size}` : ''}${sel}`;
        let body4 = '';
        if (admin && ready && !toWaybill.length && toSign.length) {
            body4 = btn('waybill-sign', 'Подписать', { primary: true });
        } else if (admin && ready && (toWaybill.length || toSign.length)) {
            // Всё создано и подписано — кнопок нет: делать нечего
            body4 = [
                btn('waybill-all', 'Создать и подписать', { primary: true, disabled: !toWaybill.length && !toSign.length }),
                set.hide_split_waybills ? '' : btn('waybill-create', 'Создать', { disabled: !toWaybill.length }),
                set.hide_split_waybills ? '' : btn('waybill-sign', 'Подписать', { disabled: !toSign.length, title: toSign.length ? '' : 'Нет неподписанных ЭТрН' }),
            ].join('');
        }
        const step4 = stepCard(4, 'ЭТрН', state4, body4, s.waybills === n && !drafts.size, !ready);

        // 5. УПД: Лавка прислала акт приёмки — подписываем и отправляем УПД, срок 5 дней
        const updReady = new Set(d.upd_ready);
        const toUpd = t.filter(p => updReady.has(p.order_no));
        const accepted = s.accepted;
        const deadline = d.upd_deadline ? new Date(d.upd_deadline + 'T00:00:00') : null;
        const daysLeft = deadline ? Math.round((deadline - new Date(new Date().toDateString())) / 86400000) : null;
        const due = deadline && updReady.size
            ? ` · отправить до ${deadline.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}${daysLeft < 0 ? ' — просрочено' : daysLeft === 0 ? ' — сегодня' : ''}`
            : '';
        let state5;
        if (!accepted && !s.upd_sent) state5 = 'После акта приёмки от Лавки';
        else state5 = `Отправлено ${s.upd_sent} из ${n}${updReady.size ? `, ждут ${updReady.size}` : ''}${due}${sel}`;
        const body5 = admin && updReady.size
            ? btn('upd-sign', 'Подписать и отправить', { primary: true, disabled: !toUpd.length, title: toUpd.length ? '' : 'Среди выбранных точек УПД не ждёт никто' })
            : '';
        const step5 = stepCard(5, 'УПД', state5, body5, n > 0 && s.upd_sent === n, !accepted && !s.upd_sent);

        return `<section class="saby-steps">${step1}${step2}${step3}${step4}${step5}</section>`;
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
                            <div class="saby-sub">${esc(p.order_no)} · ${fmtInt(p.quantity)} шт · ${kg(p.weight_kg)} · ${fmtMoney(p.gross)}</div>
                        </td>
                        <td data-col="shipment"><div class="cell-main">${shipmentCell(p)}</div></td>
                        <td data-col="waybill"><div class="cell-main">${waybillCell(p.waybill)}</div></td>
                        <td data-col="upd"><div class="cell-main">${updCell(p.shipment)}</div></td>
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
        if ((d.unknown_weight || []).length) parts.push(`<div class="lavka-notice">Вес не определился — в перевозке посчитан нулём: ${d.unknown_weight.map(esc).join(', ')}. Укажите вес в карточке товара.</div>`);
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
        if (window.SabyTask) SabyTask.decorate(content, SabyTask.get('lavka'), day());
    }

    // --- Загрузка и операции ---

    async function load() {
        try { history.replaceState(null, '', `?day=${day()}`); } catch { /* не критично */ }
        try {
            data = await api(`/api/v1/saby/day?day=${day()}`);
            if (selecting) selected = new Set([...selected].filter(o => data.points.some(p => p.order_no === o)));
            render();
            if (finished) { SabyTask.flash(content, finished, day()); finished = null; }
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
        else if (data) stateEl.textContent = data.checked_at ? `Прочитано ${timeText(data.checked_at)}` : '';
    }

    // Ждём фоновую операцию до конца; результат — то, что вернуло действие.
    // Ход показывает окошко СБИС в углу, а ячейки строк — над какой точкой работа
    async function waitTask() {
        polling = true;
        const p = await SabyTask.wait('lavka');
        polling = null;
        // Последнее состояние точек — в ячейках, пока таблица не перерисована
        SabyTask.decorate(content, { ...p, running: true }, day());
        finished = p;
        return p;
    }
    let finished = null;     // только что закончившаяся операция: её ячейки проявятся после перерисовки

    async function runTask(body) {
        await api('/api/v1/saby/action', { method: 'POST', body: { day: day(), orders: ordersParam(), ...body } });
        SabyTask.poke();
        setBusy(true, 'Начинаю…');
        const p = await waitTask();
        lastResult = p.result;
        if (p.error) toast(p.error, 'error');
        else if (p.result) {
            const ok = (p.result.done || []).length, bad = Object.keys(p.result.errors || {}).length;
            const skip = (p.result.skipped || []).length;
            toast(`${p.label}: готово ${ok}${skip ? `, пропущено ${skip} — уже были` : ''}${bad ? `, ошибок ${bad}` : ''}`, bad ? 'error' : 'ok');
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

    // Окно подписи. extra.create — сначала создать документ в этом же окне (extra.sign: false —
    // только создать), extra.carriers — выбор перевозчика там же
    async function sign(kind, extra = {}) {
        const tr = data.our_transport;
        const pool = kind === 'waybill' ? data.draft_waybills : kind === 'upd' ? data.upd_ready : [];
        // Документов ещё нет — подписываем то, что создастся по выбранным точкам
        const orders = targets().map(p => p.order_no).filter(o => extra.create || pool.includes(o));
        const count = selecting || extra.create ? orders.length : pool.length;
        const signNow = extra.sign !== false;
        const ok = await SabySign.sign({
            day: day(), kind, orders: selecting ? orders : [],
            create: extra.create || null, carriers: extra.carriers || null, carrierId: extra.carrierId || null, sign: signNow,
            title: extra.title || (kind === 'transport' ? `Заказ на перевозку № ${tr ? tr.number : ''}` : kind === 'upd' ? 'УПД' : 'ЭТрН'),
            what: extra.what || (kind === 'transport' ? 'Подпись отправит заказ перевозчику.'
                : kind === 'upd' ? `СБИС сформирует УПД из реализаций и отправит их Лавке: ${count}. Сначала внесите в СБИС изменения по бумажным накладным — отправленный УПД не отзывается, только исправляется корректировкой.`
                : `Подпись отметит погрузку и отправит накладные Лавке: ${count}.`),
        });
        if (ok && signNow) { setBusy(true, 'Перечитываю день'); await waitTask(); setBusy(false); await load(); }
        else if (extra.create) await load();
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
                if (name === 'ship-all') {
                    const drafts = targets().filter(p => p.shipment && /редактируется/i.test(p.shipment.state || '')).map(p => p.order_no);
                    if (drafts.length) await runTask({ action: 'process_shipments', orders: drafts });
                }
                return;
            }
            if (name === 'ship-process') {
                if (!(await Hub.confirmDialog({ title: 'Провести реализации?', text: 'Реализации уйдут Лавке, заказы утвердятся. Отправленное не отзывается.', okText: 'Провести', danger: false }))) return;
                await runTask({ action: 'process_shipments' });
                return;
            }
            if (name === 'transport-create' || name === 'transport-all') {
                const signNow = name === 'transport-all';
                await sign('transport', {
                    sign: signNow, carriers: data.carriers, carrierId: carrierId || data.last_carrier_id,
                    title: 'Заказ на перевозку',
                    what: `Один рейс на день: точек ${t.length}, ${kg(t.reduce((a, p) => a + p.weight_kg, 0))}. Это настоящий документ в общем кабинете${signNow ? '; подпись сразу отправит его перевозчику' : ''}.`,
                    create: (carrier) => { carrierId = carrier; return runTask({ action: 'create_transport', carrier_id: carrier }); },
                });
                return;
            }
            if (name === 'transport-sign') { await sign('transport'); return; }
            if (name === 'waybill-create' || name === 'waybill-all') {
                const create = t.filter(p => !p.waybill);
                if (create.length && name === 'waybill-all') {
                    // Создать и подписать — одним окном: сертификат выбирается до создания
                    await sign('waybill', {
                        create: () => runTask({ action: 'create_waybills' }), title: 'ЭТрН',
                        what: `Создам накладные по точкам (${create.length}) и подпишу: погрузка отметится, накладные уйдут Лавке.`,
                    });
                    return;
                }
                if (create.length) {
                    if (!(await confirmCreate('ЭТрН', create.length))) return;
                    if (!(await runTask({ action: 'create_waybills' }))) return;
                } else if (name === 'waybill-create') return;
                if (name === 'waybill-all' && data.draft_waybills.length) await sign('waybill');
                return;
            }
            if (name === 'waybill-sign') { await sign('waybill'); return; }
            if (name === 'upd-sign') { await sign('upd'); }
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
            SabyTask.poke();
            setBusy(true, 'Читаю кабинет');
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
            const other = (body.skipped || []).reduce((n, s) => n + s.rows, 0);
            toast(`Реестр загружен: ${body.rows} строк за ${body.label}${other ? ` · других контрагентов пропущено: ${other}` : ''}`);
            await load();
        } catch (err) { toast(err.message, 'error'); }
    });

    // --- Настройки: общее окно, у Лавки свои значения ---
    SabySettings.init({ scope: 'lavka', button: el('settingsBtn'), onSaved: () => load() });

    // Ход операции — в ячейках строк этого дня; шаги приглушены, пока она идёт
    SabyTask.onUpdate((tasks) => {
        const t = tasks.find(x => x.source === 'lavka');
        if (!data || !t) return;
        if (t.running) {
            if (t.day === day()) SabyTask.decorate(content, t, day());
            if (!content.classList.contains('saby-busy')) setBusy(true, t.label);
        } else if (!polling && content.classList.contains('saby-busy')) {
            // Операция, запущенная не с этой страницы (автообновление, другой человек), кончилась
            setBusy(false);
            load();
        }
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
