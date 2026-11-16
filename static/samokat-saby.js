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
        const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
        if (d === Hub.isoDate(new Date())) return `сегодня в ${t.slice(0, 5)}`;
        if (d === Hub.isoDate(tomorrow)) return `завтра в ${t.slice(0, 5)}`;
        return `${Hub.fmtDate(d)} в ${t.slice(0, 5)}`;
    };
    const isDraft = (s) => !s || /редактируется|черновик/i.test(s);

    // --- Короткие состояния: те же, что у Лавки ---

    const STATES = [
        [/завершено успешно/i, 'Завершён', 'ok'],
        [/с проблемами/i, 'С проблемами', 'bad'],
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
        const upd = ship.upd ? ' <span class="st st-ok">УПД ушёл</span>' : '';
        return `${link(ship.link, `№ ${esc(ship.number || '—')}`)} ${chip(ship.state || 'Черновик')}${upd}${reg ? `<div class="saby-sub">${reg}</div>` : ''}`;
    }
    function waybillCell(w) {
        if (!w) return '<span class="saby-none">—</span>';
        return `${link(w.link, `№ ${esc(w.number || '—')}`)} ${chip(w.state)}`;
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
        const step1 = stepCard(1, 'Ответ на заказ',
            `Утверждено ${s.approved} из ${n}${s.orders < n ? ` · в кабинете найдено ${s.orders}` : ''}`,
            s.approved < n ? soon('Пока — кнопкой «Утвердить» в кабинете') : '',
            n > 0 && s.approved === n);

        // 2. Реестр 1С
        const step2 = stepCard(2, 'Реестр 1С',
            d.registry_rows ? `Строк для точек: ${s.registry} из ${n}` : 'Не загружен — без него не будет номеров реализаций',
            admin ? btn('registry', d.registry_rows ? 'Заменить реестр' : 'Загрузить реестр', { primary: !d.registry_rows }) : '',
            s.registry === n);

        // 3. Реализации и УПД: у Самоката УПД уходит вместе с реализацией, а не после приёмки
        const toCreate = t.filter(p => !p.shipment && p.registry && p.order_no);
        const step3 = stepCard(3, 'Реализации и УПД',
            `Создано ${s.shipments} из ${n}, отправлено ${s.sent}${sel}`,
            admin && s.sent < n ? [
                btn('create', 'Создать черновики', { primary: true, disabled: !toCreate.length,
                    title: toCreate.length ? 'Реализации с УПД черновиками, без отправки' : 'Создавать нечего: у точек уже есть реализации, нет строки реестра или заказ не найден в кабинете' }),
                btn('check', 'Проверить', { title: 'Собрать УПД по точкам, ничего не создавая — можно скачать файлы' }),
                btn('send', 'Подписать и отправить', { disabled: true, title: 'Скоро: отправку включим, когда черновики проверены' }),
            ].join('') : '',
            n > 0 && s.sent === n);

        // 4. Заказ на перевозку и 5. ЭТрН — как у Лавки, следующим этапом
        const step4 = stepCard(4, 'Заказ на перевозку', 'После отправки реализаций', soon('Скоро'), false, true);
        const step5 = stepCard(5, 'ЭТрН', s.waybills ? `В кабинете ${s.waybills} из ${n}` : 'После заказа на перевозку',
            soon('Скоро'), false, true);

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
                            <div class="saby-sub">${fmtInt(p.quantity)} шт · ${fmtMoney(p.gross)}${p.gln ? ` · GLN ${esc(p.gln)}` : ''}</div>
                        </td>
                        <td data-col="order"><div class="cell-main">${orderCell(p)}</div></td>
                        <td data-col="shipment"><div class="cell-main">${shipmentCell(p)}</div></td>
                        <td data-col="waybill"><div class="cell-main">${waybillCell(p.waybill)}</div></td>
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
            if (r.dry_run && (r.done || []).length) parts.push(`<div class="lavka-notice lavka-notice-info"><details>
                <summary><b>УПД собраны, ничего не создано: ${r.done.length}.</b> Файлы — как ушли бы в СБИС</summary>
                <ul class="samokat-files">${r.done.map(x => `<li>${esc(x)}${orderNo(x)
                    ? ` · <a href="/api/v1/samokat/shipments/preview?order_no=${encodeURIComponent(orderNo(x))}">файл УПД</a>` : ''}</li>`).join('')}</ul>
            </details></div>`);
            if ((r.notes || []).length) parts.push(`<div class="lavka-notice lavka-notice-info">${r.notes.map(esc).join('<br>')}</div>`);
        }
        if (d.registry_free.length) parts.push(`<div class="lavka-notice lavka-notice-info">В реестре 1С за день есть строки без точки Самоката: ${d.registry_free.map(x => `№ ${esc(x.number)} ${esc(x.address || '')}`).join('; ')}. Это могут быть отгрузки Лавки.</div>`);
        notices.innerHTML = parts.join('');
    }

    function render() {
        const d = data;
        renderNotices();
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
        if (p.result) lastResult = p.result;
        if (p.error) toast(p.error, 'error');
        else if (p.result) {
            const r = p.result, ok = (r.done || []).length, bad = Object.keys(r.errors || {}).length, skip = (r.skipped || []).length;
            toast(`${r.dry_run ? 'Проверка' : 'Черновики'}: готово ${ok}${skip ? `, пропущено ${skip} — уже есть` : ''}${bad ? `, ошибок ${bad}` : ''}`, bad ? 'error' : 'ok');
        } else toast('Кабинет прочитан');
        setBusy(false);
        await load();
    }

    async function act(name) {
        try {
            if (name === 'registry') { el('registryInput').click(); return; }
            const orders = selecting ? targets().map(p => p.order_no).filter(Boolean) : [];
            if (name === 'check') { await run('/api/v1/samokat/shipments', { day: day(), dry_run: true, orders }); return; }
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
            toast(`Реестр загружен: ${body.rows} строк за ${body.label}`);
            await load();
        } catch (err) { toast(err.message, 'error'); }
    });

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
