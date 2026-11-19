// СБИС Самоката по дню поставки — устроена как страница «СБИС» Лавки, порядок шагов свой:
// ответ на заказ → реестр 1С → реализации с УПД (УПД уходит вместе с реализацией) → заказ на перевозку → ЭТрН.
// Данные дня — из базы (/samokat/day), чтение кабинета и операции — фоном, ход — в окошке СБИС и в ячейках строк.
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
    let waiting = false;     // эта страница ждёт свою операцию
    let lastResult = null;
    let finished = null;     // только что закончившаяся операция: её ячейки проявятся после перерисовки
    let lastLabel = '';      // название последней операции — для сводки результата

    const picker = PeriodPicker.create({
        root: el('periodPicker'), button: el('periodBtn'), pop: el('periodPop'),
        kindEl: el('periodKind'), labelEl: el('periodLabel'), prev: el('prevPeriod'), next: el('nextPeriod'),
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
    let carrierId = null;

    // --- Короткие состояния: те же, что у Лавки ---

    const STATES = [
        [/завершено успешно/i, 'Завершён', 'ok'],
        [/с проблемами/i, 'С проблемами', 'bad'],
        [/проблемы при доставке/i, 'Не отправлен: ошибки', 'bad'],
        [/не определено/i, 'Расхождения', 'bad'],
        [/редактируется|черновик/i, 'Черновик', 'draft'],
        [/удал/i, 'Удалён', 'bad'],
        [/отклон|ошибк|аннулир/i, null, 'bad'],
        [/в обработке|отправлен/i, 'Отправлен', 'muted'],
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

    function regMark(p) {
        const r = p.registry;
        if (!r) return data.registry_rows
            ? '<span class="saby-reg saby-reg-none" title="В загруженном реестре 1С нет строки для этой точки — реализацию не создать">нет в реестре 1С</span>'
            : '';
        const why = [!r.ok ? `сумма в 1С ${fmtMoney(r.amount)}, у нас ${fmtMoney(p.gross)}` : '',
            r.number_ok === false ? `в 1С № ${r.number}` : ''].filter(Boolean).join('; ');
        if (why) return `<span class="saby-reg saby-reg-bad" title="${esc(why)}">1С: ${esc(why)}</span>`;
        return `<span class="saby-reg saby-reg-ok">✓ ${p.shipment ? 'сходится с 1С' : `в 1С № ${esc(r.number)}`}</span>`;
    }

    function orderCell(p) {
        if (p.order) {
            const approved = p.order.approved
                ? '<span class="st st-ok" title="Ответ на заказ отправлен">Утверждён</span>' : chip(p.order.state);
            return `${link(p.order.link, esc(p.order_no))} ${approved}`;
        }
        if (p.order_no) return `<span class="saby-doc">${esc(p.order_no)}</span><div class="saby-sub">в кабинете не найден</div>`;
        return `<span class="saby-none">${data.checked_at ? 'нет в СБИС' : '—'}</span>`;
    }
    function shipmentCell(p) {
        const ship = p.shipment, reg = regMark(p);
        if (!ship) return `<span class="saby-none">нет</span>${reg ? `<div class="saby-sub">${reg}</div>` : ''}`;
        // Вложение УПД есть и у черновика — «ушёл» только после отправки
        // «УПД ушёл» — только когда документ действительно отправлен, а не застрял на проверке
        const upd = ship.upd && !/редактируется|черновик|проблем|ошибк|отклон|удал/i.test(ship.state || 'Черновик') ? ' <span class="st st-ok">УПД ушёл</span>' : '';
        return `${link(ship.link, `№ ${esc(ship.number || '—')}`)} ${chip(ship.state || 'Черновик')}${upd}${reg ? `<div class="saby-sub">${reg}</div>` : ''}`;
    }
    function waybillCell(p) {
        const w = p.waybill;
        if (!w) return '<span class="saby-none">—</span>';
        const manual = p.waybill_ours ? '' : '<div class="saby-sub" title="Сделана в кабинете руками, не приложением">из кабинета</div>';
        return `${link(w.link, `№ ${esc(w.number || '—')}`)} ${chip(w.state)}${manual}`;
    }

    // --- Шаги ---

    const CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.4 6.6 11.3 12.5 4.9" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    const targets = () => data.points.filter(p => !selecting || selected.has(p.order_no || `#${p.id}`));
    const rowKey = (p) => p.order_no || `#${p.id}`;

    function stepCard(n, title, state, body, done = false, locked = false) {
        return `<article class="saby-step${done ? ' is-done' : ''}${locked ? ' is-locked' : ''}">
            <header><span class="saby-step-n">${done ? CHECK : n}</span><h3>${title}</h3></header>
            <p class="saby-step-state">${state}</p>
            ${body ? `<div class="saby-step-actions">${body}</div>` : ''}
        </article>`;
    }
    const btn = (act, text, { primary = false, disabled = false, title = '' } = {}) =>
        `<button type="button" class="btn ${primary ? 'btn-primary' : 'btn-secondary'} btn-sm" data-act="${act}"${disabled ? ' disabled' : ''}${title ? ` title="${esc(title)}"` : ''}>${text}</button>`;
    const soon = (text) => `<span class="saby-soon">${text}</span>`;

    function renderSteps() {
        const d = data, s = d.stats, admin = d.is_admin;
        const t = targets();
        const sel = selecting ? ` · выбрано ${t.length}` : '';
        const n = d.points.length;

        // 1. Ответ на заказ: Самокат ждёт его до отгрузки (у Лавки такого шага нет)
        // СБИС отправит ответ на заказ «подтверждён без изменений», как кнопка «Утвердить» в кабинете
        const toApprove = t.filter(p => p.order && !p.order.approved);
        const step1 = stepCard(1, 'Ответ на заказ',
            `Утверждено ${s.approved} из ${n}${s.orders < n ? ` · в кабинете найдено ${s.orders}` : ''}${sel}`,
            admin && toApprove.length ? btn('approve', 'Утвердить', { primary: true, title: 'Ответ на заказ «подтверждён без изменений» — Самокату' }) : '',
            n > 0 && s.approved === n);

        // 2. Реестр 1С
        const step2 = stepCard(2, 'Реестр 1С',
            d.registry_rows ? `Строк для точек: ${s.registry} из ${n}` : 'Не загружен',
            admin ? btn('registry', d.registry_rows ? 'Заменить реестр' : 'Загрузить реестр', { primary: !d.registry_rows }) : '',
            s.registry === n);

        // 3. Реализации и УПД: у Самоката УПД уходит вместе с реализацией, а не после приёмки.
        // «Подписать» отправляет только черновики, созданные приложением
        const set3 = d.settings || {};
        const toCreate = t.filter(p => !p.shipment && p.registry && p.order_no);
        const shipDrafts = new Set(d.draft_shipments || []);
        const toSend = t.filter(p => shipDrafts.has(p.order_no));
        const check = set3.hide_check ? '' : btn('check', 'Проверить', { title: 'Собрать УПД по точкам, ничего не создавая — можно скачать файлы' });
        let body3 = '';
        if (admin && s.sent < n) {
            body3 = !toCreate.length && !toSend.length ? check
                : !toCreate.length && toSend.length ? btn('ship-sign', 'Подписать', { primary: true }) + check : [
                btn('ship-all', 'Создать и подписать', { primary: true, disabled: !toCreate.length && !toSend.length }),
                set3.hide_split_shipments ? '' : btn('create', 'Создать', { disabled: !toCreate.length,
                    title: toCreate.length ? 'Реализации с УПД черновиками, без отправки' : 'Создавать нечего: у точек уже есть реализации, нет строки реестра или заказ не найден в кабинете' }),
                set3.hide_split_shipments ? '' : btn('ship-sign', 'Подписать', { disabled: !toSend.length,
                    title: toSend.length ? 'Подписать Рутокеном и отправить Самокату' : 'Нет черновиков, созданных приложением' }),
                check,
            ].join('');
        }
        const step3 = stepCard(3, 'Реализации и УПД',
            `Создано ${s.shipments} из ${n}, отправлено ${s.sent}${shipDrafts.size ? `, не подписано ${shipDrafts.size}` : ''}${s.failed ? `, <span class="saby-reg-bad">не ушло из-за ошибок ${s.failed}</span>` : ''}${sel}`,
            body3, n > 0 && s.sent === n);

        // 4. Заказ на перевозку: свой на день Самоката, все точки одним рейсом — как у Лавки
        const set = d.settings || {};
        const tr = d.our_transport;
        let step4;
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
            const body = admin && tr.draft ? btn('transport-sign', 'Подписать', { primary: true }) : '';
            step4 = stepCard(4, 'Заказ на перевозку', state, body, !tr.draft);
        } else {
            const withOrder = t.filter(p => p.order_no);
            const kgSum = withOrder.reduce((a, p) => a + (p.weight_kg || 0), 0);
            step4 = stepCard(4, 'Заказ на перевозку', `Ещё не создан · точек в рейсе: ${withOrder.length}, ${kg(kgSum)}`,
                admin ? (d.carriers.length ? `${btn('transport-all', 'Создать и подписать', { primary: true, disabled: !withOrder.length })}
                    ${set.hide_split_transport ? '' : btn('transport-create', 'Создать', { disabled: !withOrder.length })}
                    ${set.hide_split_transport ? '' : btn('transport-sign', 'Подписать', { disabled: true, title: 'Сначала создайте заказ' })}`
                    : '<span class="saby-none">Нет перевозчиков — добавьте в разделе «Перевозчики»</span>') : '');
        }

        // 5. ЭТрН по требованиям Самоката: после нашей подписи заказа на перевозку, одна накладная на точку
        const ready = tr && !tr.draft;
        const drafts = new Set(d.draft_waybills);
        const toWaybill = t.filter(p => p.order_no && !p.waybill_ours);
        const toSign = t.filter(p => drafts.has(p.order_no));
        let state5;
        if (!tr) state5 = 'После заказа на перевозку';
        else if (!ready) state5 = 'После подписи заказа на перевозку';
        else state5 = `Создано ${s.our_waybills} из ${n}${drafts.size ? `, не подписано ${drafts.size}` : ''}${sel}`;
        let body5 = '';
        if (admin) {
            const check = set.hide_check ? '' : btn('waybill-check', 'Проверить', { disabled: !toWaybill.length, title: 'Собрать ЭТрН и сверить с требованиями Самоката, ничего не создавая' });
            if (!ready) body5 = check;
            else if (!toWaybill.length && !toSign.length) body5 = '';     // всё создано и подписано
            else if (!toWaybill.length && toSign.length) body5 = btn('waybill-sign', 'Подписать', { primary: true });
            else body5 = [
                btn('waybill-all', 'Создать и подписать', { primary: true, disabled: !toWaybill.length && !toSign.length }),
                set.hide_split_waybills ? '' : btn('waybill-create', 'Создать', { disabled: !toWaybill.length }),
                set.hide_split_waybills ? '' : btn('waybill-sign', 'Подписать', { disabled: !toSign.length, title: toSign.length ? '' : 'Нет неподписанных ЭТрН' }),
                check,
            ].join('');
        }
        const step5 = stepCard(5, 'ЭТрН', state5, body5, n > 0 && s.our_waybills === n && !drafts.size, !tr);

        return `<section class="saby-steps">${step1}${step2}${step3}${step4}${step5}</section>`;
    }

    // --- Таблица ---

    function renderTable() {
        const d = data;
        const all = d.points.length && d.points.every(p => selected.has(rowKey(p)));
        return `
        <div class="saby-table-head">
            <h2 class="card-title-small">Точки</h2>
            <span class="lavka-section-sub">${d.points.length} · ${fmtInt(d.points.reduce((a, p) => a + p.quantity, 0))} шт · ${fmtMoney(d.points.reduce((a, p) => a + p.gross, 0))}</span>
            ${d.is_admin ? `<button type="button" class="saby-select-toggle${selecting ? ' on' : ''}" id="selectToggle" aria-pressed="${selecting}">
                <span class="saby-select-dot" aria-hidden="true"></span>${selecting ? `Выбрано ${selected.size} · готово` : 'Выбрать точки'}</button>` : ''}
        </div>
        <div class="table-wrapper data-table saby-table${selecting ? ' selecting' : ''}">
            <table>
                <thead><tr>
                    <th class="sel-col"><span class="sel-box"><input type="checkbox" id="selAll" aria-label="Выбрать все"${all ? ' checked' : ''}></span></th>
                    <th>Точка</th><th>Заказ</th><th>Реализация и УПД</th><th>ЭТрН</th>
                </tr></thead>
                <tbody>${d.points.map(p => `
                    <tr class="${selecting && selected.has(rowKey(p)) ? 'is-selected' : ''}" data-order="${esc(rowKey(p))}">
                        <td class="sel-col"><span class="sel-box"><input type="checkbox" data-pick="${esc(rowKey(p))}" aria-label="Выбрать точку"${selected.has(rowKey(p)) ? ' checked' : ''}></span></td>
                        <td>
                            <div title="${esc(p.address || '')}">${esc(p.address_short)}</div>
                            <div class="saby-sub">${fmtInt(p.quantity)} шт · ${kg(p.weight_kg)} · ${fmtMoney(p.gross)}${p.gln ? ` · GLN ${esc(p.gln)}` : ''}</div>
                        </td>
                        <td data-col="order"><div class="cell-main">${orderCell(p)}</div></td>
                        <td data-col="shipment"><div class="cell-main">${shipmentCell(p)}</div></td>
                        <td data-col="waybill"><div class="cell-main">${waybillCell(p)}</div></td>
                    </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
    }

    function orderNo(label) {
        const m = /УР-\d{8}/.exec(label || '');
        return m ? m[0] : '';
    }

    function renderNotices() {
        const d = data, parts = [];
        if (!d.configured) parts.push('<div class="lavka-notice"><b>Нет доступа к СБИС.</b> На сервере не заданы SABY_LOGIN и SABY_PASSWORD.</div>');
        const r = lastResult;
        if (r) {
            const errs = Object.entries(r.errors || {});
            if (errs.length) parts.push(`<div class="lavka-notice"><b>Не вышло:</b> ${errs.map(([k, v]) => `${esc(k)} — ${esc(v)}`).join('; ')}</div>`);
            if (r.dry_run && (r.done || []).length && /ЭТрН/.test(lastLabel)) parts.push(`<div class="lavka-notice lavka-notice-info"><b>ЭТрН собраны и сверены с требованиями Самоката, ничего не создано: ${r.done.length}.</b> Номер заказа — в ДрОГруз, грузополучатель — головное, в адресе доставки — GLN точки.</div>`);
            else if (r.dry_run && (r.done || []).length) parts.push(`<div class="lavka-notice lavka-notice-info"><details>
                <summary><b>УПД собраны, ничего не создано: ${r.done.length}.</b> Файлы — как ушли бы в СБИС</summary>
                <ul class="samokat-files">${r.done.map(x => `<li>${esc(x)}${orderNo(x)
                    ? ` · <a href="/api/v1/samokat/shipments/preview?order_no=${encodeURIComponent(orderNo(x))}">файл УПД</a>` : ''}</li>`).join('')}</ul>
            </details></div>`);
            if ((r.notes || []).length) parts.push(`<div class="lavka-notice lavka-notice-info">${r.notes.map(esc).join('<br>')}</div>`);
        }
        if (d.receiver_problem && d.our_transport) parts.push(`<div class="lavka-notice"><b>Для ЭТрН:</b> ${esc(d.receiver_problem)}.</div>`);
        if ((d.unknown_weight || []).length) parts.push(`<div class="lavka-notice">Вес не определился — в перевозке посчитан нулём: ${d.unknown_weight.map(esc).join(', ')}. Укажите вес в карточке товара.</div>`);
        if (d.registry_free.length) parts.push(`<div class="lavka-notice lavka-notice-info">В реестре 1С за день есть строки без точки Самоката: ${d.registry_free.map(x => `№ ${esc(x.number)} ${esc(x.address || '')}`).join('; ')}. Это могут быть отгрузки Лавки.</div>`);
        notices.innerHTML = parts.join('');
    }

    function render() {
        const d = data;
        renderNotices();
        el('settingsBtn').hidden = !d.is_admin;
        if (!d.points.length) {
            content.innerHTML = '<div class="empty-state"><h3>На этот день заказов Самоката нет</h3>Они приходят письмом и в СБИС как ORDERS.</div>';
            return;
        }
        if (!d.checked_at) {
            content.innerHTML = `<div class="empty-state"><h3>Кабинет за ${esc(d.label)} ещё не читали</h3><p>Нажмите «Обновить из СБИС»: приложение посмотрит, что там уже есть.</p></div>`;
            return;
        }
        content.innerHTML = renderSteps() + renderTable();
        if (window.SabyTask) SabyTask.decorate(content, SabyTask.get('samokat'), day());
    }

    // --- Загрузка и операции ---

    async function load() {
        try { history.replaceState(null, '', `?day=${day()}`); } catch { /* не критично */ }
        try {
            data = await api(`/api/v1/samokat/day?day=${day()}`);
            if (selecting) selected = new Set([...selected].filter(o => data.points.some(p => rowKey(p) === o)));
            render();
            if (finished) { SabyTask.flash(content, finished, day()); finished = null; }
            if (!waiting) stateEl.textContent = data.checked_at ? `Прочитано ${timeText(data.checked_at)}` : '';
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

    // Запустить операцию и дождаться её: ход — в окошке СБИС и в ячейках строк
    async function run(url, body) {
        await api(url, { method: 'POST', body });
        SabyTask.poke();
        setBusy(true, 'Работаю с СБИС…');
        waiting = true;
        const p = await SabyTask.wait('samokat');
        waiting = false;
        SabyTask.decorate(content, { ...p, running: true }, day());
        finished = p;
        lastLabel = p.label || '';
        if (p.result) lastResult = p.result;
        if (p.error) toast(p.error, 'error');
        else if (p.result) {
            const r = p.result, ok = (r.done || []).length, bad = Object.keys(r.errors || {}).length, skip = (r.skipped || []).length;
            toast(`${p.label || 'Готово'}: готово ${ok}${skip ? `, пропущено ${skip} — уже есть` : ''}${bad ? `, ошибок ${bad}` : ''}`, bad ? 'error' : 'ok');
        } else toast('Кабинет прочитан');
        setBusy(false);
        await load();
        // Для окна «Создать и подписать»: создано без ошибок — можно подписывать
        return !p.error && !Object.keys((p.result || {}).errors || {}).length || !!((p.result || {}).done || []).length;
    }

    async function confirmCreate(what, count) {
        return Hub.confirmDialog({
            title: `Создать в СБИС: ${what}?`,
            text: `Документы появятся в кабинете черновиками${count ? `, точек: ${count}` : ''}. Отправка — отдельно, подписью Рутокеном.`,
            okText: 'Создать', danger: false,
        });
    }

    // Подпись Рутокеном в браузере — тем же окном, что у Лавки; потом кабинет перечитывается фоном
    // Окно подписи. extra.create — сначала создать документ в этом же окне (extra.sign: false —
    // только создать), extra.carriers — выбор перевозчика там же
    async function sign(kind, extra = {}) {
        const tr = data.our_transport;
        const pool = kind === 'sm_waybill' ? data.draft_waybills : kind === 'sm_upd' ? (data.draft_shipments || []) : [];
        const orders = targets().map(p => p.order_no).filter(o => o && (extra.create || pool.includes(o)));
        const count = selecting || extra.create ? orders.length : pool.length;
        const signNow = extra.sign !== false;
        const ok = await SabySign.sign({
            day: day(), kind, orders: selecting ? orders : [],
            create: extra.create || null, carriers: extra.carriers || null, carrierId: extra.carrierId || null, sign: signNow,
            title: extra.title || (kind === 'sm_transport' ? `Заказ на перевозку № ${tr ? tr.number : ''}`
                : kind === 'sm_upd' ? 'Реализации с УПД' : 'ЭТрН Самоката'),
            what: extra.what || (kind === 'sm_transport' ? 'Подпись отправит заказ перевозчику.'
                : kind === 'sm_upd' ? `Подпись отправит реализации с УПД Самокату: ${count}. Отправленный УПД не отзывается — только исправляется корректировкой.`
                : `Подпись отметит погрузку и отправит накладные Самокату: ${count}.`),
        });
        if (!ok || !signNow) { if (extra.create) await load(); return; }
        {
            setBusy(true, 'Перечитываю день');
            waiting = true;
            const p = await SabyTask.wait('samokat');
            waiting = false;
            finished = p;
            setBusy(false);
            await load();
        }
    }

    async function act(name) {
        try {
            if (name === 'registry') { el('registryInput').click(); return; }
            const orders = selecting ? targets().map(p => p.order_no).filter(Boolean) : [];
            if (name === 'check') { await run('/api/v1/samokat/shipments', { day: day(), dry_run: true, orders }); return; }
            if (name === 'transport-check') {
                setBusy(true, 'Спрашиваю СБИС');
                const r = await api(`/api/v1/samokat/check-transport?day=${day()}`, { method: 'POST' });
                toast(r.signed ? 'Перевозчик подписал заказ' : `Ещё не подписан: ${r.state || '—'}`, r.signed ? 'ok' : 'error');
                setBusy(false);
                await load();
                return;
            }
            if (name === 'transport-create' || name === 'transport-all') {
                const signNow = name === 'transport-all';
                const stops = targets().filter(p => p.order_no);
                await sign('sm_transport', {
                    sign: signNow, carriers: data.carriers, carrierId: carrierId || data.last_carrier_id,
                    title: 'Заказ на перевозку Самоката',
                    what: `Один рейс на день: точек ${stops.length}, ${kg(stops.reduce((a, p) => a + (p.weight_kg || 0), 0))}. Это настоящий документ в общем кабинете${signNow ? '; подпись сразу отправит его перевозчику' : ''}.`,
                    create: (carrier) => { carrierId = carrier; return run('/api/v1/samokat/action', { day: day(), action: 'transport', orders, carrier_id: carrier }); },
                });
                return;
            }
            if (name === 'transport-sign') { await sign('sm_transport'); return; }
            if (name === 'waybill-check') { await run('/api/v1/samokat/action', { day: day(), action: 'waybills_check', orders }); return; }
            if (name === 'waybill-create' || name === 'waybill-all') {
                const create = targets().filter(p => p.order_no && !p.waybill_ours);
                if (create.length && name === 'waybill-all') {
                    await sign('sm_waybill', {
                        create: () => run('/api/v1/samokat/action', { day: day(), action: 'waybills', orders }), title: 'ЭТрН Самоката',
                        what: `Создам накладные по точкам (${create.length}) по требованиям Самоката и подпишу: погрузка отметится, накладные уйдут Самокату.`,
                    });
                    return;
                }
                if (create.length) {
                    if (!(await confirmCreate('ЭТрН Самоката', create.length))) return;
                    await run('/api/v1/samokat/action', { day: day(), action: 'waybills', orders });
                } else if (name === 'waybill-create') return;
                if (name === 'waybill-all' && data.draft_waybills.length) await sign('sm_waybill');
                return;
            }
            if (name === 'waybill-sign') { await sign('sm_waybill'); return; }
            if (name === 'ship-sign') { await sign('sm_upd'); return; }
            if (name === 'approve') {
                const count = targets().filter(p => p.order && !p.order.approved).length;
                const ok = await Hub.confirmDialog({
                    title: 'Утвердить заказы?',
                    text: `Самокату уйдёт ответ на заказ «подтверждён без изменений», заказов: ${count}. Утверждённые не трогаю.`,
                    okText: 'Утвердить', danger: false,
                });
                if (!ok) return;
                await run('/api/v1/samokat/action', { day: day(), action: 'approve', orders });
                // Если СБИС требует подписи ответа — то же окно подписи, что у реализаций
                const need = (lastResult && lastResult.need_sign) || [];
                if (need.length) {
                    const picked = selecting ? need : [];
                    await SabySign.sign({ day: day(), kind: 'sm_order', orders: picked, title: 'Ответ на заказ',
                        what: `Подпись утвердит заказы и отправит Самокату ответ: ${need.length}.` }).then(async (signed) => {
                        if (signed) { setBusy(true, 'Перечитываю день'); waiting = true; finished = await SabyTask.wait('samokat'); waiting = false; setBusy(false); await load(); }
                    });
                }
                return;
            }
            if (name === 'ship-all') {
                const create = targets().filter(p => !p.shipment && p.registry && p.order_no);
                if (create.length) {
                    await sign('sm_upd', {
                        create: () => run('/api/v1/samokat/shipments', { day: day(), dry_run: false, orders }), title: 'Реализации с УПД',
                        what: `Создам реализации с УПД по точкам (${create.length}) и подпишу: они уйдут Самокату. Отправленный УПД не отзывается — только исправляется корректировкой.`,
                    });
                    return;
                }
                if ((data.draft_shipments || []).length) await sign('sm_upd');
                return;
            }
            if (name === 'create') {
                const count = targets().filter(p => !p.shipment && p.registry && p.order_no).length;
                const ok = await Hub.confirmDialog({
                    title: 'Создать черновики в СБИС?',
                    text: `Реализации с УПД появятся в кабинете черновиками, точек: ${count}. Ничего не отправляется: перед отправкой их проверяют и подписывают. Номера — из реестра 1С.`,
                    okText: 'Создать', danger: false,
                });
                if (ok) await run('/api/v1/samokat/shipments', { day: day(), dry_run: false, orders });
            }
        } catch (err) {
            waiting = false;
            setBusy(false);
            toast(err.message, 'error');
        }
    }

    content.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act]');
        if (b && !b.disabled) { act(b.dataset.act); return; }
        if (e.target.closest('#selectToggle')) {
            selecting = !selecting;
            if (selecting && !selected.size) selected = new Set(data.points.map(rowKey));
            render();
            return;
        }
        const row = e.target.closest('tr[data-order]');
        if (selecting && row && !e.target.closest('a') && !e.target.matches('input')) {
            const box = row.querySelector('[data-pick]');
            box.checked = !box.checked;
            box.dispatchEvent(new Event('change', { bubbles: true }));
        }
    });
    content.addEventListener('change', (e) => {
        if (e.target.id === 'selAll') {
            selected = e.target.checked ? new Set(data.points.map(rowKey)) : new Set();
            render();
            return;
        }
        const pick = e.target.dataset.pick;
        if (!pick) return;
        e.target.checked ? selected.add(pick) : selected.delete(pick);
        e.target.closest('tr').classList.toggle('is-selected', e.target.checked);
        content.querySelector('.saby-steps').outerHTML = renderSteps();
        const toggle = el('selectToggle');
        if (toggle) toggle.lastChild.textContent = `Выбрано ${selected.size} · готово`;
        const all = el('selAll');
        if (all) all.checked = selected.size === data.points.length;
    });

    refreshBtn.addEventListener('click', async () => {
        try { await run(`/api/v1/samokat/refresh?day=${day()}`); } catch (err) { waiting = false; setBusy(false); toast(err.message, 'error'); }
    });

    // Реестр тот же, что на странице «СБИС»: один файл 1С за день, в нём и Лавка, и Самокат
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

    SabySettings.init({ scope: 'samokat', button: el('settingsBtn'), onSaved: () => load() });

    // Операция идёт (в т. ч. запущенная не отсюда): ячейки показывают ход, шаги приглушены
    SabyTask.onUpdate((tasks) => {
        const t = tasks.find(x => x.source === 'samokat');
        if (!data || !t) return;
        if (t.running) {
            if (t.day === day()) SabyTask.decorate(content, t, day());
            if (!content.classList.contains('saby-busy')) setBusy(true, t.label);
        } else if (!waiting && content.classList.contains('saby-busy')) {
            setBusy(false);
            load();
        }
    });

    (async () => {
        let dates = [];
        try {
            dates = (await api('/api/v1/samokat/dates')).map(x => x.date);
            picker.setMarked(dates);
        } catch { /* без точек в календаре тоже можно */ }
        const q = new URLSearchParams(location.search).get('day');
        const today = Hub.isoDate(new Date());
        const start = /^\d{4}-\d{2}-\d{2}$/.test(q || '') ? q : (dates.find(x => x >= today) || dates[dates.length - 1] || today);
        picker.set('day', start, start);
        await load();
    })();
});
