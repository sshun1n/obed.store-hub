// СБИС Самоката по дню поставки: реестр 1С, черновики реализаций с УПД, дальше — подпись и ЭТрН.
// Данные дня — из базы (/samokat/day), операции с кабинетом — фоном (/samokat/shipments).
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtMoney, toast } = Hub;
    const el = (id) => document.getElementById(id);
    const content = el('content');
    const resultBox = el('result');
    const taskState = el('taskState');
    const fmtInt = (n) => new Intl.NumberFormat('ru-RU').format(n || 0);
    const CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.4 6.6 11.3 12.5 4.9" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    let data = null;
    let busy = false;
    let dates = [];

    const picker = PeriodPicker.create({
        root: el('periodPicker'), button: el('periodBtn'), pop: el('periodPop'),
        kindEl: el('periodKind'), labelEl: el('periodLabel'), prev: el('prevPeriod'), next: el('nextPeriod'),
        kinds: ['day'],
        onChange: () => { resultBox.innerHTML = ''; load(); },
    });
    const day = () => picker.get().start;

    const btn = (act, text, { primary = false, disabled = false, title = '', extra = '' } = {}) =>
        `<button type="button" class="btn ${primary ? 'btn-primary' : 'btn-secondary'} btn-sm" data-act="${act}"${extra}${disabled || busy ? ' disabled' : ''}${title ? ` title="${esc(title)}"` : ''}>${text}</button>`;

    function stepCard(n, title, state, body, done = false, locked = false) {
        return `<article class="saby-step${done ? ' is-done' : ''}${locked ? ' is-locked' : ''}">
            <header><span class="saby-step-n">${done ? CHECK : n}</span><h3>${title}</h3></header>
            <p class="saby-step-state">${state}</p>
            ${body ? `<div class="saby-step-actions">${body}</div>` : ''}
        </article>`;
    }

    function regMark(p) {
        if (!p.registry) return data.registry_rows
            ? '<span class="saby-reg saby-reg-none" title="В реестре 1С за день нет строки для этой точки">нет в реестре 1С</span>'
            : '<span class="text-muted">реестр не загружен</span>';
        if (!p.registry.ok) return `<span class="saby-reg saby-reg-bad">в 1С № ${esc(p.registry.number)} на ${fmtMoney(p.registry.amount)}</span>`;
        return `<span class="saby-reg saby-reg-ok">✓ в 1С № ${esc(p.registry.number)}</span>`;
    }

    function render() {
        const d = data, s = d.stats, admin = d.is_admin, n = s.points;
        if (!n) {
            content.innerHTML = '<div class="empty-state"><h3>На этот день заказов Самоката нет</h3>Они приходят письмом и в СБИС как ORDERS.</div>';
            return;
        }
        const ready = d.points.filter(p => !p.shipment && p.registry && p.order_no).length;
        const step1 = stepCard(1, 'Реестр 1С',
            d.registry_rows ? `Строк для точек: ${s.registry} из ${n}` : 'Не загружен — без него не будет номеров реализаций',
            admin ? btn('registry', d.registry_rows ? 'Заменить реестр' : 'Загрузить реестр', { primary: !d.registry_rows }) : '',
            s.registry === n);
        const step2 = stepCard(2, 'Реализации и УПД',
            `Черновиков ${s.shipments} из ${n}${s.orders < n ? ` · заказов из СБИС сопоставлено ${s.orders}` : ''}`,
            admin ? [
                btn('check', 'Проверить', { title: 'Собрать УПД по всем точкам, ничего не создавая' }),
                btn('create', 'Создать черновики', { primary: true, disabled: !ready && s.orders === n, title: 'Реализации с УПД черновиками, без отправки' }),
            ].join('') : '',
            s.shipments === n);
        const step3 = stepCard(3, 'Подпись и отправка', 'Пока не сделано: УПД подписывает бухгалтер Рутокеном', '', false, true);
        const step4 = stepCard(4, 'ЭТрН', 'Пока не сделано', '', false, true);

        let notices = '';
        if (d.registry_free.length) notices += `<div class="lavka-notice lavka-notice-info">В реестре 1С за день есть строки без точки Самоката: ${d.registry_free.map(r => `№ ${esc(r.number)} ${esc(r.address || '')}`).join('; ')}. Это могут быть отгрузки Лавки.</div>`;

        content.innerHTML = `
            <section class="saby-steps samokat-steps">${step1}${step2}${step3}${step4}</section>
            ${notices}
            <div class="table-wrapper data-table saby-table">
                <table>
                    <thead><tr><th>Точка</th><th>Заказ</th><th class="num">Штук</th><th class="num">Сумма с НДС</th><th>Реестр 1С</th><th>Реализация</th>${admin ? '<th></th>' : ''}</tr></thead>
                    <tbody>${d.points.map(p => `
                        <tr>
                            <td><div title="${esc(p.address)}">${esc(p.address_short)}</div>${p.gln ? `<div class="cell-sub mono-cell">GLN ${esc(p.gln)}</div>` : ''}</td>
                            <td class="mono-cell">${p.order_no ? esc(p.order_no) : '<span class="text-muted" title="Номер появится после «Проверить»: заказ сопоставляется по адресу">—</span>'}</td>
                            <td class="num">${fmtInt(p.quantity)}</td>
                            <td class="num">${fmtMoney(p.gross)}</td>
                            <td>${regMark(p)}</td>
                            <td>${p.shipment ? `№ ${esc(p.shipment.number)} <span class="st st-info">черновик</span>` : '<span class="text-muted">нет</span>'}</td>
                            ${admin ? `<td class="num">${!p.shipment && p.order_no && p.registry
                                ? btn('create-one', 'Создать', { extra: ` data-order="${esc(p.order_no)}"`, title: 'Черновик только по этой точке' }) : ''}</td>` : ''}
                        </tr>`).join('')}
                    </tbody>
                    <tfoot><tr><td colspan="2">Итого · точек ${n}</td><td class="num"><b>${fmtInt(d.points.reduce((a, p) => a + p.quantity, 0))}</b></td>
                        <td class="num">${fmtMoney(d.points.reduce((a, p) => a + p.gross, 0))}</td><td colspan="${admin ? 3 : 2}"></td></tr></tfoot>
                </table>
            </div>`;
    }

    async function load() {
        try {
            data = await api(`/api/v1/samokat/day?day=${day()}`);
            render();
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    }

    // --- Фоновая операция ---

    function orderNo(label) {
        const m = /УР-\d{8}/.exec(label || '');
        return m ? m[0] : '';
    }

    function renderResult(p) {
        const r = p.result;
        if (!r) { resultBox.innerHTML = ''; return; }
        const list = (title, items, info, withFile) => items.length ? `
            <div class="lavka-notice${info ? ' lavka-notice-info' : ''}"><b>${title}: ${items.length}</b>
                <ul class="samokat-list">${items.map(x => `<li>${esc(x)}${withFile && orderNo(x)
                    ? ` · <a href="/api/v1/samokat/shipments/preview?order_no=${encodeURIComponent(orderNo(x))}">файл УПД</a>` : ''}</li>`).join('')}</ul>
            </div>` : '';
        resultBox.innerHTML = [
            list(r.dry_run ? 'УПД собран, ничего не создано' : 'Создано черновиков', r.done, true, r.dry_run),
            list('Ошибки', Object.entries(r.errors || {}).map(([k, v]) => `${k}: ${v}`), false, false),
            list('Пропущено: реализация уже есть', r.skipped || [], true, false),
            list('Заметки', r.notes || [], true, false),
        ].join('');
    }

    async function poll(after = false) {
        try {
            const p = await api('/api/v1/samokat/shipments/status');
            busy = p.running;
            if (busy) taskState.textContent = `${p.label}: ${p.step}${p.total ? ` (${p.done} из ${p.total})` : ''}`;
            else if (p.error) taskState.innerHTML = `<span class="ts-warn">${esc(p.error)}</span>`;
            else taskState.textContent = p.finished_at && after ? `${p.label}: готово` : '';
            if (busy) { if (data) render(); setTimeout(() => poll(true), 1500); return; }
            if (after) { renderResult(p); await load(); }
        } catch (err) {
            taskState.textContent = err.message;
        }
    }

    async function run(dryRun, orders = []) {
        if (!dryRun) {
            const what = orders.length ? `по точке ${orders[0]}` : 'по всем точкам дня, где их ещё нет';
            const ok = await Hub.confirmDialog({
                title: 'Создать черновики в СБИС?',
                text: `Реализации с УПД ${what} появятся в кабинете черновиками. Ничего не отправляется: перед отправкой их проверяют и подписывают. Номера — из реестра 1С.`,
                okText: 'Создать', danger: false,
            });
            if (!ok) return;
        }
        try {
            await api('/api/v1/samokat/shipments', { method: 'POST', body: { day: day(), dry_run: dryRun, orders } });
            busy = true;
            render();
            poll(true);
        } catch (err) { toast(err.message, 'error'); }
    }

    content.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act]');
        if (!b || b.disabled) return;
        const act = b.dataset.act;
        if (act === 'registry') el('registryInput').click();
        else if (act === 'check') run(true);
        else if (act === 'create') run(false);
        else if (act === 'create-one') run(false, [b.dataset.order]);
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

    async function init() {
        try {
            dates = (await api('/api/v1/samokat/dates')).map(d => d.date);
            picker.setMarked(dates);
        } catch { /* без точек в календаре тоже можно */ }
        const q = new URLSearchParams(location.search).get('day');
        const today = Hub.isoDate(new Date());
        const first = /^\d{4}-\d{2}-\d{2}$/.test(q || '') ? q : (dates.find(d => d >= today) || dates[dates.length - 1] || today);
        picker.set('day', first, first);
        await load();
        poll(false);
    }

    init();
});
