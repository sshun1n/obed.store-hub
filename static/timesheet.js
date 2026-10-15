/**
 * Учёт рабочего времени: загрузка табеля и отчёт по зарплате.
 * Настройки сотрудника — на его карточке (/employee?id=…).
 */
document.addEventListener('DOMContentLoaded', () => {
    const el = (id) => document.getElementById(id);
    const periodStart = el('periodStart');
    const periodEnd = el('periodEnd');
    const showArchived = el('showArchived');
    const reportBody = el('reportBody');
    const uploadStatus = el('uploadStatus');

    let currentReport = null;
    let lastImport = null;
    let expandedRowId = null;

    // --- Форматирование ---

    const fmtMoney = (v) => new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(v) + ' ₽';
    const fmtHours = (min) => {
        if (!min) return '0:00';
        const h = Math.floor(min / 60), m = min % 60;
        return `${h}:${String(m).padStart(2, '0')}`;
    };
    const fmtDate = (iso) => {
        const [y, m, d] = iso.split('-');
        return `${d}.${m}.${y}`;
    };
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    // --- Период ---

    function setPeriod(start, end) {
        periodStart.value = start;
        periodEnd.value = end;
    }

    function setMonth(offset) {
        const now = new Date();
        const d = new Date(now.getFullYear(), now.getMonth() + offset, 1);
        const last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
        const iso = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
        setPeriod(iso(d), iso(last));
        loadReport();
    }

    // --- API ---

    async function checkAccess() {
        const r = await fetch('/api/v1/users/me');
        if (!r.ok) { window.location.href = '/login'; return false; }
        const user = await r.json();
        if (user.role !== 'admin') {
            el('accessDenied').style.display = 'block';
            return false;
        }
        el('tsContent').style.display = 'block';
        return true;
    }

    async function loadImports() {
        try {
            const r = await fetch('/api/v1/timesheet/imports');
            if (!r.ok) return;
            const imports = await r.json();
            lastImport = imports[0] || null;
            const info = el('lastImportInfo');
            const chip = el('chipLastImport');
            if (lastImport) {
                const dt = new Date(lastImport.created_at + 'Z');
                info.textContent = `Последняя загрузка: ${lastImport.filename} · ${dt.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })} · сотрудников: ${lastImport.employees_count}`;
                if (lastImport.period_start && lastImport.period_end) chip.style.display = '';
            } else {
                info.textContent = '';
                chip.style.display = 'none';
            }
        } catch (e) { console.error(e); }
    }

    async function loadReport() {
        if (!periodStart.value || !periodEnd.value) return;
        expandedRowId = null;
        reportBody.innerHTML = '<tr><td colspan="10" class="empty-state">Загрузка…</td></tr>';
        try {
            const params = new URLSearchParams({
                start: periodStart.value,
                end: periodEnd.value,
                include_archived: showArchived.checked,
            });
            const r = await fetch(`/api/v1/timesheet/report?${params}`);
            if (!r.ok) throw new Error((await r.json()).detail || 'Ошибка загрузки отчёта');
            currentReport = await r.json();
            renderReport();
        } catch (e) {
            reportBody.innerHTML = `<tr><td colspan="10" class="empty-state">${esc(e.message)}</td></tr>`;
        }
    }

    // --- Рендер ---

    function renderStats() {
        const rows = currentReport.rows;
        const sum = (f) => rows.reduce((a, r) => a + f(r), 0);
        const totalMin = sum((r) => r.worked_minutes);
        const h = Math.floor(totalMin / 60), m = totalMin % 60;
        el('statEmployees').textContent = String(rows.length);
        el('statHours').textContent = totalMin
            ? `${new Intl.NumberFormat('ru-RU').format(h)} ч ${m} мин`
            : '0 ч';
        el('statBase').textContent = fmtMoney(sum((r) => r.base_pay + r.absence_pay));
        el('statPenalty').textContent = fmtMoney(sum((r) => r.underwork_penalty + r.late_penalty_total));
        el('statTotal').textContent = fmtMoney(sum((r) => r.total_pay));
    }

    function renderReport() {
        renderStats();
        const rows = currentReport.rows;
        if (!rows.length) {
            reportBody.innerHTML = '<tr><td colspan="10" class="empty-state"><h3>Нет данных</h3>Загрузите табель за нужный период.</td></tr>';
            return;
        }
        reportBody.innerHTML = rows.map((row) => {
            const e = row.employee;
            const rateLabel = e.pay_rate
                ? `${fmtMoney(e.pay_rate)}<span class="ts-rate-type">/${e.pay_type === 'hour' ? 'час' : 'смена'}</span>`
                : '<span class="ts-warn">не задана</span>';
            const violations = [];
            if (row.late_count) violations.push(`опозд. ×${row.late_count}`);
            if (row.early_count) violations.push(`ран. уход ×${row.early_count}`);
            if (row.absences) violations.push(`<span class="ts-warn">прогул ×${row.absences}</span>`);
            if (row.punch_issues) violations.push(`<span class="punch-warn" title="Дни без отметки прихода или ухода: система учёта досчитала время сама. Проверьте наработку.">неполн. отметки ×${row.punch_issues}</span>`);
            const excused = [];
            if (row.vacation_days) excused.push(`отпуск ×${row.vacation_days}`);
            if (row.sick_days) excused.push(`больн. ×${row.sick_days}`);
            if (row.dayoff_days) excused.push(`отгул ×${row.dayoff_days}`);
            if (row.extra_days) excused.push(`вне графика ×${row.extra_days}`);
            const accrued = row.base_pay + row.absence_pay;
            const penalty = row.underwork_penalty + row.late_penalty_total;
            const sub = [e.position || e.department, row.has_schedule ? null : 'график из табеля']
                .filter(Boolean).map(esc).join(' · ');
            return `
            <tr class="ts-row${e.is_active ? '' : ' ts-archived'}" data-emp="${e.id}">
                <td>
                    <div class="ts-name">${esc(e.display_name || e.full_name)}</div>
                    <div class="ts-position">${sub}</div>
                </td>
                <td class="ts-num">${row.days_worked || '—'}</td>
                <td class="ts-num">${row.worked_minutes ? fmtHours(row.worked_minutes) : '—'}</td>
                <td class="ts-num">${row.shortfall_minutes ? `<span class="ts-warn">−${fmtHours(row.shortfall_minutes)}</span>` : '—'}</td>
                <td class="ts-viol">${[...violations, ...excused.map((x) => `<span class="text-muted">${x}</span>`)].join('<br>') || '—'}</td>
                <td class="ts-num">${rateLabel}</td>
                <td class="ts-num"${row.absence_pay ? ` title="В т.ч. оплата отсутствий: ${fmtMoney(row.absence_pay)}"` : ''}>${accrued ? fmtMoney(accrued) : '—'}</td>
                <td class="ts-num">${penalty ? `<span class="ts-warn">−${fmtMoney(penalty)}</span>` : '—'}</td>
                <td class="ts-num ts-total">${accrued || penalty ? fmtMoney(row.total_pay) : '—'}</td>
                <td class="ts-actions">
                    <a class="action-btn btn-edit-user" href="/employee?id=${e.id}" title="Карточка сотрудника">настр.</a>
                </td>
            </tr>`;
        }).join('');
    }

    // --- Детализация по дням ---

    async function toggleDetail(tr, empId) {
        const existing = reportBody.querySelector('.ts-detail-row');
        if (existing) existing.remove();
        reportBody.querySelectorAll('.ts-row.ts-open').forEach((r) => r.classList.remove('ts-open'));
        if (expandedRowId === empId) { expandedRowId = null; return; }
        expandedRowId = empId;
        tr.classList.add('ts-open');

        const detailTr = document.createElement('tr');
        detailTr.className = 'ts-detail-row';
        detailTr.innerHTML = '<td colspan="10" class="ts-detail-cell">Загрузка…</td>';
        tr.after(detailTr);

        try {
            const params = new URLSearchParams({ start: periodStart.value, end: periodEnd.value });
            const days = (await Hub.api(`/api/v1/timesheet/employees/${empId}/days?${params}`))
                .filter((d) => d.status !== 'none');
            if (!days.length) {
                detailTr.querySelector('td').innerHTML = '<span class="text-muted">Нет записей за период.</span>';
                return;
            }
            const rows = days.map((d) => {
                const wd = Hub.WEEKDAYS[(Hub.parseIso(d.work_date).getDay() + 6) % 7];
                const plan = d.planned_start || d.planned_end
                    ? `${d.planned_start || '?'}–${d.planned_end || '?'}`
                    : (d.norm_minutes ? fmtHours(d.norm_minutes) : '');
                const viol = [];
                if (d.late_minutes) viol.push(`опоздание ${d.late_minutes} мин${d.late_counted ? '' : ' (допуск)'}`);
                if (d.early_minutes) viol.push(`ранний уход ${d.early_minutes} мин`);
                if (d.absence_note) viol.push(esc(d.absence_note));
                if (d.punch_issue) viol.unshift(`<span class="punch-warn">${Hub.PUNCH[d.punch_issue]}</span>`);
                const muted = d.status === 'off' || d.status === 'planned';
                return `
                <tr class="${d.status === 'absent' ? 'ts-absence' : ''}${muted ? ' ts-muted-row' : ''}${d.punch_issue ? ' ts-punch-row' : ''}">
                    <td>${fmtDate(d.work_date)} <span class="ts-wd">${wd}</span></td>
                    <td><span class="day-status st-${d.status}">${Hub.STATUS[d.status]}</span></td>
                    <td>${plan || '—'}</td>
                    <td>${d.check_in || '—'}</td>
                    <td>${d.check_out || '—'}</td>
                    <td>${d.worked_minutes ? fmtHours(d.worked_minutes) : '—'}</td>
                    <td>${d.shortfall_minutes ? `−${fmtHours(d.shortfall_minutes)}` : '—'}</td>
                    <td>${viol.join(', ') || '—'}</td>
                </tr>`;
            }).join('');
            const source = days.some((d) => d.source === 'schedule')
                ? 'Норма и выходные — по графику из карточки сотрудника'
                : 'Своего графика нет — норма из табеля';
            detailTr.querySelector('td').innerHTML = `
                <div class="ts-detail-inner">
                    <table class="ts-detail-table">
                        <thead><tr><th>Дата</th><th>Статус</th><th>План</th><th>Приход</th><th>Уход</th><th>Наработка</th><th>Недоработка</th><th>Нарушения</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div class="ts-detail-foot">${source} · <a href="/employee?id=${empId}">открыть карточку →</a></div>`;
        } catch (e) {
            detailTr.querySelector('td').innerHTML = `<span class="ts-warn">${esc(e.message)}</span>`;
        }
    }

    // --- Загрузка файла ---

    function showUploadStatus(text, isError) {
        uploadStatus.style.display = 'block';
        uploadStatus.classList.toggle('ts-upload-error', !!isError);
        uploadStatus.innerHTML = text;
    }

    async function uploadFile(file) {
        const btn = el('uploadBtn');
        btn.disabled = true;
        btn.textContent = 'Загрузка…';
        showUploadStatus(`Обработка файла <b>${esc(file.name)}</b>…`);
        try {
            const fd = new FormData();
            fd.append('file', file);
            const r = await fetch('/api/v1/timesheet/upload', { method: 'POST', body: fd });
            const data = await r.json();
            if (!r.ok) throw new Error(data.detail || 'Ошибка загрузки');
            showUploadStatus(
                `Табель загружен: сотрудников — <b>${data.employees_total}</b> (новых: ${data.employees_new}), ` +
                `смен — <b>${data.sessions_saved}</b>, период <b>${fmtDate(data.period_start)} — ${fmtDate(data.period_end)}</b>. ` +
                `Файл удалён с диска после обработки.`
            );
            if (data.period_start && data.period_end) setPeriod(data.period_start, data.period_end);
            await loadImports();
            await loadReport();
        } catch (e) {
            showUploadStatus(`Ошибка: ${esc(e.message)}`, true);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Загрузить табель';
        }
    }

    // --- События ---

    el('uploadBtn').addEventListener('click', () => el('fileInput').click());
    el('fileInput').addEventListener('change', (ev) => {
        if (ev.target.files.length) uploadFile(ev.target.files[0]);
        ev.target.value = '';
    });
    el('chipThisMonth').addEventListener('click', () => setMonth(0));
    el('chipPrevMonth').addEventListener('click', () => setMonth(-1));
    el('chipLastImport').addEventListener('click', () => {
        if (lastImport?.period_start) {
            setPeriod(lastImport.period_start, lastImport.period_end);
            loadReport();
        }
    });
    periodStart.addEventListener('change', loadReport);
    periodEnd.addEventListener('change', loadReport);
    showArchived.addEventListener('change', loadReport);

    reportBody.addEventListener('click', (ev) => {
        if (ev.target.closest('a')) return;
        const tr = ev.target.closest('tr.ts-row');
        if (tr) toggleDetail(tr, parseInt(tr.dataset.emp));
    });

    // --- Старт ---

    (async () => {
        if (!await checkAccess()) return;
        setMonth(0);
        await loadImports();
        // Если табель уже загружался — сразу показываем его период
        if (lastImport?.period_start && lastImport?.period_end) {
            setPeriod(lastImport.period_start, lastImport.period_end);
            loadReport();
        }
    })();
});
