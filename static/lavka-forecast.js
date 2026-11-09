// Прогноз заказов Лавки: плитки, график недели с историей, прогноз по позициям, окно точности
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtMoney, openModal, closeModal } = Hub;
    const el = (id) => document.getElementById(id);
    const fmt = LavkaCharts.fmt;
    const content = el('content');
    let data = null;

    const plural = (n, one, few, many) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
        return many;
    };
    const dateText = (iso) => PeriodPicker.rangeText(iso, iso).replace(/ \d{4},/, ',');
    const pctText = (v) => `${String(Math.abs(Math.round(v * 10) / 10)).replace('.', ',')} %`;

    // Иконка графика на плитке: подсказывает, что плитка открывает окно
    const CHART_ICON = `<svg class="stat-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19h16M6 16l4-5 3 3 5-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    function help(title, body) {
        return `<details class="chart-help"><summary><span class="chart-help-icon" aria-hidden="true">?</span>${esc(title)}</summary><div class="chart-help-body">${body}</div></details>`;
    }

    function trendCard(t, week) {
        if (t.pct == null) return '';
        const pct = t.pct;
        const flat = Math.abs(pct) < 3;
        const cls = flat ? 'delta-flat' : pct > 0 ? 'delta-up' : 'delta-down';
        const word = flat ? 'Ровно' : pct > 0 ? 'Растёт' : 'Падает';
        const arrow = flat ? '=' : pct > 0 ? '▲' : '▼';
        const vsLast = t.last_week ? (week.quantity - t.last_week) / t.last_week * 100 : null;
        return `
            <div class="stat-card">
                <span class="stat-label">Общий тренд</span>
                <span class="stat-value"><span class="delta ${cls} trend-value">${arrow} ${word} ${flat ? '' : pctText(pct)}</span></span>
                <span class="stat-sub">к средней неделе за ${t.weeks} нед.: ${fmt(t.avg)} шт</span>
                ${vsLast == null ? '' : `<span class="stat-sub">к последней неделе (${fmt(t.last_week)} шт): ${vsLast > 0 ? '+' : '−'}${pctText(vsLast)}</span>`}
            </div>`;
    }

    function render() {
        const d = data;
        el('subtitle').innerHTML = `Неделя <b>${esc(d.horizon.label)}</b>. Посчитано по ${d.history_days} ${plural(d.history_days, 'дню', 'дням', 'дням')} поставки с ${esc(dateText(d.first))} по ${esc(dateText(d.last))}. Дни отгрузки модель определила сама: <b>${esc(d.working.join(', '))}</b>.`;
        const w = d.week, a = d.accuracy;
        const cols = d.days.length;
        content.innerHTML = `
        <div class="stats-grid forecast-stats">
            <div class="stat-card">
                <span class="stat-label">Ждём за неделю</span>
                <span class="stat-value stat-big">${fmt(w.quantity)} шт</span>
                <span class="stat-sub">≈ ${fmtMoney(Math.round(w.gross))} с НДС</span>
                <span class="stat-sub">скорее всего от ${fmt(w.low)} до ${fmt(w.high)} шт</span>
            </div>
            ${trendCard(d.trend, w)}
            <button type="button" class="stat-card stat-link stat-chart" id="errorBtn" title="Показать прогноз против факта за прошлую неделю">
                <span class="stat-label">Средняя ошибка ${CHART_ICON}</span>
                <span class="stat-value stat-big">${a.mape == null ? '—' : pctText(a.mape)}</span>
                <span class="stat-sub">по дням, проверено на ${a.checked} ${plural(a.checked, 'дне', 'днях', 'днях')} за ${a.weeks} нед.</span>
                ${a.week_mape == null ? '' : `<span class="stat-sub">за неделю целиком: ${pctText(a.week_mape)}</span>`}
            </button>
        </div>

        <section class="report-card">
            <div class="report-card-head">
                <h2 class="card-title-small">Прогноз на неделю</h2>
                <div class="lc-legend"><span class="lc-key lc-key-fact"></span>заказали <span class="lc-key lc-key-plan"></span>прогноз <span class="lc-key lc-key-band"></span>коридор</div>
            </div>
            <div class="lc-box" id="forecastChart"></div>
            <div class="table-wrapper data-table forecast-days">
                <table>
                    <thead><tr><th>Дата</th><th>День</th><th class="num">Ждём, шт</th><th class="num">Коридор</th><th class="num">Дней в расчёте</th></tr></thead>
                    <tbody>${d.days.map(x => `
                        <tr><td class="mono-cell">${x.label}</td><td>${esc(x.weekday_full)}</td>
                            <td class="num"><b>${fmt(x.value)}</b></td><td class="num text-muted">${fmt(x.low)} – ${fmt(x.high)}</td><td class="num text-muted">${x.basis}</td></tr>`).join('')}
                    </tbody>
                    <tfoot><tr><td colspan="2">Итого за неделю</td><td class="num"><b>${fmt(w.quantity)}</b></td><td class="num text-muted">${fmt(w.low)} – ${fmt(w.high)}</td><td></td></tr></tfoot>
                </table>
            </div>
            ${help('Как считается прогноз', `
                <p>Чтобы предсказать четверг, берём восемь прошлых четвергов и усредняем, причём свежая неделя весит больше старой. Так прогноз успевает за спросом, но не дёргается от одного случайного дня.</p>
                <p><b>Дни отгрузки</b> модель находит сама по последним восьми неделям: день считается рабочим, если поставки в нём были хотя бы в половине недель. Разовая поставка из-за праздника рабочим днём не станет, а если график сменится, прогноз перестроится сам.</p>
                <p><b>Коридор</b> — насколько этот день недели обычно скачет. Чем он шире, тем меньше можно верить точной цифре.</p>
                <p>Слева на графике — сколько заказывали за последние восемь недель, справа пунктиром — прогноз. Наведите на точку, чтобы увидеть цифры.</p>`)}
        </section>

        <section class="report-card">
            <div class="report-card-head">
                <h2 class="card-title-small">Прогноз по позициям</h2>
                <span class="lavka-section-sub">${d.items.length} ${plural(d.items.length, 'позиция', 'позиции', 'позиций')} · ${fmt(w.items_quantity)} шт · ${fmtMoney(Math.round(w.gross))}</span>
            </div>
            <div class="table-wrapper data-table forecast-items">
                <table>
                    <thead><tr>
                        <th>Позиция</th>
                        ${d.days.map(x => `<th class="num">${x.label}<span class="th-sub">${x.weekday}</span></th>`).join('')}
                        <th class="num">За неделю</th><th class="num">Сумма с НДС</th>
                        <th class="num" title="${esc(d.trend.last_week_label || '')}">Было</th>
                    </tr></thead>
                    <tbody>${d.items.map(r => `
                        <tr>
                            <td><div>${esc(r.name)}</div>${r.product ? `<div class="cell-sub">в справочнике: ${esc(r.product)}</div>` : ''}</td>
                            ${r.by_day.map(v => `<td class="num${Math.round(v) ? '' : ' text-muted'}">${Math.round(v) ? fmt(v) : '—'}</td>`).join('')}
                            <td class="num"><b>${fmt(r.total)}</b></td>
                            <td class="num">${fmtMoney(Math.round(r.gross))}</td>
                            <td class="num text-muted">${r.was ? fmt(r.was) : '—'}</td>
                        </tr>`).join('')}
                    </tbody>
                    <tfoot><tr>
                        <td>Итого</td>
                        ${d.days.map((x, i) => `<td class="num">${fmt(d.items.reduce((s, r) => s + r.by_day[i], 0))}</td>`).join('')}
                        <td class="num"><b>${fmt(w.items_quantity)}</b></td>
                        <td class="num">${fmtMoney(Math.round(w.gross))}</td>
                        <td class="num text-muted">${fmt(d.trend.last_week)}</td>
                    </tr></tfoot>
                </table>
            </div>
            ${help('Почему сумма по позициям не равна прогнозу недели', `
                <p>Каждая позиция считается сама по себе, по своим прошлым вторникам, четвергам и субботам. Дни, когда позицию не заказали, считаются нулём. Прогноз недели сверху считается по общему итогу дня, поэтому цифры немного расходятся.</p>
                <p>Позиций, которых не было в трёх последних поставках, здесь нет: скорее всего, их сняли с продажи. «Было» — сколько заказали за последние семь дней, ${esc(d.trend.last_week_label || '')}. Суммы — по последним ценам Лавки.</p>`)}
        </section>`;

        LavkaCharts.forecast(el('forecastChart'), {
            history: d.history.map(p => ({
                label: p.label, value: p.value,
                tip: `<b>${p.label}, ${esc(p.weekday_full)}</b><br>заказали ${fmt(p.value)} шт`,
            })),
            forecast: d.days.map(p => ({
                label: p.label, value: p.value, low: p.low, high: p.high,
                tip: `<b>${p.label}, ${esc(p.weekday_full)}</b><br>ждём ${fmt(p.value)} шт<br><span class="lc-tip-sub">скорее всего ${fmt(p.low)} – ${fmt(p.high)}, по ${p.basis} таким же дням</span>`,
            })),
        });
        el('errorBtn').addEventListener('click', openError);
    }

    // --- Окно точности: прогноз против факта за прошлую неделю ---

    function openError() {
        const lw = data.last_week, a = data.accuracy;
        el('errorTitle').textContent = `Прошлая неделя, ${lw.label}`;
        const err = lw.predicted != null && lw.actual ? (lw.predicted - lw.actual) / lw.actual * 100 : null;
        const items = lw.items.filter(r => r.predicted != null);
        el('errorBody').innerHTML = `
            <div class="error-summary">
                <div><span class="stat-label">Заказали</span><b>${fmt(lw.actual)} шт</b></div>
                <div><span class="stat-label">Модель обещала</span><b>${lw.predicted == null ? '—' : `${fmt(lw.predicted)} шт`}</b></div>
                <div><span class="stat-label">Промах за неделю</span><b class="${err != null && Math.abs(err) >= 20 ? 'text-red' : ''}">${err == null ? '—' : `${err > 0 ? '+' : '−'}${pctText(err)}`}</b></div>
                <div><span class="stat-label">Средняя ошибка</span><b>${a.mape == null ? '—' : pctText(a.mape)}</b><small>по дням за ${a.weeks} нед.</small></div>
            </div>
            <div class="lc-legend"><span class="lc-key lc-key-fact"></span>заказали <span class="lc-key lc-key-plan lc-key-bar"></span>что обещала модель <span class="lc-err-key">±%</span> промах</div>
            <div class="lc-box lc-box-bars" id="errorChart"></div>
            <p class="field-hint">Модель предсказывала каждый день вслепую: ей давали только то, что было до этого дня. «+» — обещала больше, чем заказали, «−» — меньше.</p>
            <h3 class="report-sub-title">По позициям</h3>
            <div class="table-wrapper data-table error-items">
                <table>
                    <thead><tr><th>Позиция</th><th class="num">Прогноз</th><th class="num">Заказали</th><th class="num">Промах</th></tr></thead>
                    <tbody>${items.map(r => {
                        const e = r.actual ? (r.predicted - r.actual) / r.actual * 100 : null;
                        const diff = Math.round(r.predicted) - r.actual;
                        return `<tr>
                            <td>${esc(r.name)}</td>
                            <td class="num">${fmt(r.predicted)}</td>
                            <td class="num"><b>${fmt(r.actual)}</b></td>
                            <td class="num ${e != null && Math.abs(e) >= 30 ? 'text-red' : 'text-muted'}">${diff > 0 ? '+' : diff < 0 ? '−' : ''}${fmt(Math.abs(diff))} шт${e == null ? '' : ` · ${pctText(e)}`}</td>
                        </tr>`;
                    }).join('')}</tbody>
                </table>
            </div>`;
        LavkaCharts.bars(el('errorChart'), {
            groups: lw.days.map(p => ({
                label: `${p.label} ${p.weekday}`, actual: p.actual, predicted: p.predicted,
                tip: `<b>${p.label}, ${esc(p.weekday_full)}</b><br>заказали ${fmt(p.actual)} шт<br>модель обещала ${p.predicted == null ? '—' : `${fmt(p.predicted)} шт`}`,
            })),
        });
        openModal(el('errorModal'));
    }
    el('errorClose').addEventListener('click', () => closeModal(el('errorModal')));

    (async () => {
        try {
            data = await api('/api/v1/lavka/forecast');
        } catch (err) {
            content.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
            return;
        }
        if (!data.ready) {
            content.innerHTML = '<div class="empty-state"><h3>Прогноз строить пока не по чему</h3><p>Нужно хотя бы несколько недель заказов. Загрузите историю почты на странице «Заказы Лавки».</p></div>';
            return;
        }
        render();
    })();
});
