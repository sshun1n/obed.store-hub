// Отчёты Лавки: плитки списаний и отзывов, лента отзывов, окно списаний по позициям за период
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, openModal, closeModal } = Hub;
    const el = (id) => document.getElementById(id);
    const content = el('content');
    const WRITEOFF_WARN = 20;   // выше — подсвечиваем, как в ka-webapp
    let data = null;
    let filter = 'all';

    const fmtInt = (n) => new Intl.NumberFormat('ru-RU').format(n || 0);
    const pct = (v) => v == null ? '—' : `${String(Math.round(v * 10) / 10).replace('.', ',')} %`;
    const plural = (n, one, few, many) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
        return many;
    };
    const shortDate = (iso) => { const [y, m, d] = iso.split('-'); return `${d}.${m}.${y.slice(2)}`; };
    const longDate = (iso) => PeriodPicker.rangeText(iso, iso);
    const TABLE_ICON = `<svg class="stat-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19h16M6 16l4-5 3 3 5-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    function writeoffDelta(w) {
        if (w.pct == null || w.was == null) return '';
        const diff = w.pct - w.was;
        if (Math.abs(diff) < 0.5) return `<span class="delta delta-flat">= как неделей раньше</span>`;
        // Рост списаний — плохо, поэтому красный вверх
        const cls = diff > 0 ? 'delta-down' : 'delta-up';
        return `<span class="delta ${cls}">${diff > 0 ? '▲' : '▼'} ${pct(Math.abs(diff)).replace(' %', ' п. п.')}</span> <span class="delta-was">было ${pct(w.was)}</span>`;
    }

    function renderNotices() {
        const box = el('notices');
        if (!data.last_report) {
            box.innerHTML = '';
            return;
        }
        if (data.stale) {
            box.innerHTML = `<div class="lavka-notice"><b>Новых отчётов метрик давно нет.</b> Последний пришёл ${esc(longDate(data.last_report))}, ${data.stale_days} ${plural(data.stale_days, 'день', 'дня', 'дней')} назад. Цифры ниже — на ту дату. Отчёты по уровню сервиса при этом приходят, так что почта работает: новых метрик Лавка не присылала.</div>`;
        } else {
            box.innerHTML = '';
        }
    }

    function feedbackRows() {
        const rows = data.feedback.filter(f => filter === 'all' || (filter === 'like' ? f.liked : !f.liked));
        if (!rows.length) return '<div class="empty-state">Таких отзывов нет</div>';
        return `
        <div class="table-wrapper data-table feedback-table">
            <table>
                <thead><tr><th>Дата</th><th>Позиция</th><th>Оценка</th><th>Комментарий</th></tr></thead>
                <tbody>${rows.map(f => `
                    <tr>
                        <td class="mono-cell" title="${f.order_date ? `заказ от ${esc(shortDate(f.order_date))}` : ''}">${shortDate(f.date)}</td>
                        <td><div>${esc(f.name)}</div>${f.product ? `<div class="cell-sub">в справочнике: ${esc(f.product)}</div>` : ''}</td>
                        <td>${f.liked ? '<span class="fb-mark fb-like">👍 нравится</span>' : '<span class="fb-mark fb-dislike">👎 не нравится</span>'}</td>
                        <td class="fb-comment">${f.comment ? esc(f.comment) : '<span class="text-muted">без комментария</span>'}</td>
                    </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
    }

    function render() {
        renderNotices();
        if (!data.last_report && !data.feedback.length) {
            content.innerHTML = '<div class="empty-state"><h3>Отчётов метрик ещё нет</h3><p>Они придут с почтой. Если отчёты уже лежат в ящике, администратор может нажать «Загрузить историю» в админ-панели.</p></div>';
            return;
        }
        const w = data.writeoff, c = data.feedback_counts;
        const total = c ? c.likes + c.dislikes : 0;
        content.innerHTML = `
        <div class="stats-grid report-stats">
            ${w ? `
            <button type="button" class="stat-card stat-link stat-chart" id="writeoffBtn" title="Списания по каждой позиции за выбранный период">
                <span class="stat-label">Списания ${TABLE_ICON}</span>
                <span class="stat-value stat-big${w.pct != null && w.pct > WRITEOFF_WARN ? ' text-red' : ''}">${pct(w.pct)}</span>
                <span class="stat-delta">${writeoffDelta(w)}</span>
                <span class="stat-sub">неделя ${esc(w.label)} · нажмите — по позициям</span>
            </button>` : ''}
            ${c ? `
            <div class="stat-card">
                <span class="stat-label">Нравится</span>
                <span class="stat-value stat-big fb-like">${fmtInt(c.likes)}</span>
                <span class="stat-sub">${total ? Math.round(c.likes / total * 100) : 0} % отзывов · ${esc(c.label)}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Не нравится</span>
                <span class="stat-value stat-big fb-dislike">${fmtInt(c.dislikes)}</span>
                <span class="stat-sub">${total ? Math.round(c.dislikes / total * 100) : 0} % отзывов · ${esc(c.label)}</span>
            </div>` : ''}
        </div>

        <section class="report-card">
            <div class="report-card-head">
                <h2 class="card-title-small">Отзывы покупателей</h2>
                <span class="lavka-section-sub">${fmtInt(data.feedback.length)} ${plural(data.feedback.length, 'отзыв', 'отзыва', 'отзывов')} за всё время</span>
                <div class="seg fb-filter" role="tablist">
                    <button type="button" class="seg-btn${filter === 'all' ? ' active' : ''}" data-filter="all">Все</button>
                    <button type="button" class="seg-btn${filter === 'dislike' ? ' active' : ''}" data-filter="dislike">Не нравится</button>
                    <button type="button" class="seg-btn${filter === 'like' ? ' active' : ''}" data-filter="like">Нравится</button>
                </div>
            </div>
            <div id="feedbackList">${feedbackRows()}</div>
        </section>`;

        const btn = el('writeoffBtn');
        if (btn) btn.addEventListener('click', openWriteoffs);
        content.querySelector('.fb-filter').addEventListener('click', (e) => {
            const b = e.target.closest('[data-filter]');
            if (!b) return;
            filter = b.dataset.filter;
            content.querySelectorAll('.fb-filter .seg-btn').forEach(x => x.classList.toggle('active', x === b));
            el('feedbackList').innerHTML = feedbackRows();
        });
    }

    // --- Окно списаний ---

    const modal = el('writeoffModal');
    const picker = PeriodPicker.create({
        root: el('woPicker'), button: el('woBtn'), pop: el('woPop'),
        kindEl: el('woKind'), labelEl: el('woLabel'), prev: el('woPrev'), next: el('woNext'),
        onChange: () => loadWriteoffs(),
    });

    function bar(value) {
        if (value == null) return '<span class="text-muted">нет продаж</span>';
        const width = Math.min(value, 100);
        return `<span class="wo-bar${value > WRITEOFF_WARN ? ' wo-bar-warn' : ''}"><span style="width:${width}%"></span></span><b>${pct(value)}</b>`;
    }

    async function loadWriteoffs() {
        const p = picker.get();
        const body = el('woBody');
        body.classList.add('loading');
        try {
            const r = await api(`/api/v1/lavka/writeoffs?start=${p.start}&end=${p.end}`);
            el('woTotal').innerHTML = r.items.length
                ? `в среднем <b class="${r.total_pct > WRITEOFF_WARN ? 'text-red' : ''}">${pct(r.total_pct)}</b> · продано ${fmtInt(r.sold)} шт · ${r.weeks.length} ${plural(r.weeks.length, 'неделя', 'недели', 'недель')}`
                : '';
            body.innerHTML = !r.items.length
                ? `<div class="empty-state">За ${esc(r.label)} отчётов нет. Данные есть с ${esc(longDate(data.weeks[0]))} по неделю ${esc(data.writeoff.label)}.</div>`
                : `
                <div class="table-wrapper data-table writeoff-table">
                    <table>
                        <thead><tr><th>Позиция</th><th class="num">Списания</th><th class="num">Продано, шт</th><th class="num">Недель</th></tr></thead>
                        <tbody>${r.items.map(i => `
                            <tr title="${esc(i.by_week.map(w => `${shortDate(w.week)}: ${pct(w.pct)}, продано ${w.sold}`).join('\n'))}">
                                <td><div>${esc(i.name)}</div>${i.product ? `<div class="cell-sub">в справочнике: ${esc(i.product)}</div>` : ''}</td>
                                <td class="num wo-cell">${bar(i.pct)}</td>
                                <td class="num">${fmtInt(i.sold)}</td>
                                <td class="num text-muted">${i.weeks}</td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
                </div>`;
        } catch (err) {
            body.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        } finally {
            body.classList.remove('loading');
        }
    }

    function openWriteoffs() {
        // Отмечаем в календаре все дни недель, за которые есть отчёт
        const days = [];
        for (const w of data.weeks) for (let i = 0; i < 7; i++) days.push(PeriodPicker.addDays(w, i));
        picker.setMarked(days);
        // По умолчанию — последняя неделя, за которую есть данные
        const last = data.writeoff.week;
        picker.set('week', last, PeriodPicker.addDays(last, 6));
        openModal(modal);
        loadWriteoffs();
    }
    el('writeoffClose').addEventListener('click', () => closeModal(modal));

    (async () => {
        try {
            data = await api('/api/v1/lavka/reports');
            render();
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
        }
    })();
});
