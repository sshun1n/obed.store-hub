/**
 * Учёт рабочего времени: загрузка табеля, отчёт по зарплате, настройки сотрудников.
 */
document.addEventListener('DOMContentLoaded', () => {
    const el = (id) => document.getElementById(id);
    const periodStart = el('periodStart');
    const periodEnd = el('periodEnd');
    const showArchived = el('showArchived');
    const reportBody = el('reportBody');
    const uploadStatus = el('uploadStatus');
    const modal = el('employeeModal');

    let currentReport = null;
    let lastImport = null;
    let editingEmployee = null;
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
        const active = rows.filter((r) => r.days_worked > 0 || r.absences > 0);
        const sum = (f) => rows.reduce((a, r) => a + f(r), 0);
        el('statEmployees').textContent = `${active.length} / ${rows.length}`;
        el('statHours').textContent = fmtHours(sum((r) => r.worked_minutes)) + ' ч';
        el('statBase').textContent = fmtMoney(sum((r) => r.base_pay));
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
            const penalty = row.underwork_penalty + row.late_penalty_total;
            return `
            <tr class="ts-row${e.is_active ? '' : ' ts-archived'}" data-emp="${e.id}">
                <td>
                    <div class="ts-name">${esc(e.full_name)}</div>
                    <div class="ts-position">${esc(e.position || e.department || '')}</div>
                </td>
                <td class="ts-num">${row.days_worked || '—'}</td>
                <td class="ts-num">${row.worked_minutes ? fmtHours(row.worked_minutes) : '—'}</td>
                <td class="ts-num">${row.shortfall_minutes ? `<span class="ts-warn">−${fmtHours(row.shortfall_minutes)}</span>` : '—'}</td>
                <td class="ts-viol">${violations.join('<br>') || '—'}</td>
                <td class="ts-num">${rateLabel}</td>
                <td class="ts-num">${row.base_pay ? fmtMoney(row.base_pay) : '—'}</td>
                <td class="ts-num">${penalty ? `<span class="ts-warn">−${fmtMoney(penalty)}</span>` : '—'}</td>
                <td class="ts-num ts-total">${row.base_pay || penalty ? fmtMoney(row.total_pay) : '—'}</td>
                <td class="ts-actions">
                    <button class="action-btn btn-edit-user" data-edit="${e.id}" title="Настройки">настр.</button>
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
            const r = await fetch(`/api/v1/timesheet/employees/${empId}/sessions?${params}`);
            if (!r.ok) throw new Error('Не удалось загрузить детализацию');
            const sessions = await r.json();
            if (!sessions.length) {
                detailTr.querySelector('td').innerHTML = '<span class="text-muted">Нет записей за период.</span>';
                return;
            }
            const emp = currentReport.rows.find((x) => x.employee.id === empId)?.employee;
            const rows = sessions.map((s) => {
                const norm = emp?.norm_minutes || s.schedule_minutes;
                const absence = !s.worked_minutes && norm;
                const shortfall = s.worked_minutes && norm && norm > s.worked_minutes ? norm - s.worked_minutes : 0;
                const viol = [s.late_raw ? `опоздание ${esc(s.late_raw)}` : '', s.early_raw ? `ранний уход ${esc(s.early_raw)}` : ''].filter(Boolean).join(', ');
                return `
                <tr${absence ? ' class="ts-absence"' : ''}>
                    <td>${fmtDate(s.work_date)}</td>
                    <td>${s.check_in || '—'}</td>
                    <td>${s.check_out || '—'}</td>
                    <td>${s.worked_minutes ? fmtHours(s.worked_minutes) : (absence ? 'прогул' : '—')}</td>
                    <td>${norm ? fmtHours(norm) : '—'}</td>
                    <td>${shortfall ? `−${fmtHours(shortfall)}` : '—'}</td>
                    <td>${viol || '—'}</td>
                </tr>`;
            }).join('');
            detailTr.querySelector('td').innerHTML = `
                <div class="ts-detail-inner">
                    <table class="ts-detail-table">
                        <thead><tr><th>Дата</th><th>Приход</th><th>Уход</th><th>Наработка</th><th>Норма</th><th>Недоработка</th><th>Нарушения</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>`;
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

    // --- Модальное окно настроек ---

    function openModal(emp) {
        editingEmployee = emp;
        el('modalTitle').textContent = emp.full_name;
        el('fPayType').value = emp.pay_type || 'shift';
        el('fPayRate').value = emp.pay_rate || '';
        el('fNormHours').value = emp.norm_minutes ? (emp.norm_minutes / 60) : '';
        el('fTolerance').value = emp.underwork_tolerance_min || '';
        el('fUnderworkPenalty').value = emp.underwork_penalty_per_hour || '';
        el('fLatePenalty').value = emp.late_penalty || '';
        el('fPosition').value = emp.position || '';
        el('fIsActive').checked = emp.is_active;
        updateRateLabel();
        modal.classList.add('open');
    }

    function closeModal() {
        modal.classList.remove('open');
        editingEmployee = null;
    }

    function updateRateLabel() {
        el('fPayRateLabel').textContent = el('fPayType').value === 'hour' ? 'Ставка, ₽/час' : 'Ставка, ₽/смена';
    }

    async function saveEmployee(ev) {
        ev.preventDefault();
        if (!editingEmployee) return;
        const normHours = parseFloat(el('fNormHours').value);
        const payload = {
            pay_type: el('fPayType').value,
            pay_rate: parseFloat(el('fPayRate').value) || 0,
            norm_minutes: isNaN(normHours) || normHours <= 0 ? null : Math.round(normHours * 60),
            underwork_tolerance_min: parseInt(el('fTolerance').value) || 0,
            underwork_penalty_per_hour: parseFloat(el('fUnderworkPenalty').value) || 0,
            late_penalty: parseFloat(el('fLatePenalty').value) || 0,
            position: el('fPosition').value.trim() || null,
            is_active: el('fIsActive').checked,
        };
        try {
            const r = await fetch(`/api/v1/timesheet/employees/${editingEmployee.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!r.ok) throw new Error((await r.json()).detail || 'Не удалось сохранить');
            closeModal();
            await loadReport();
        } catch (e) {
            alert(e.message);
        }
    }

    async function deleteEmployee() {
        if (!editingEmployee) return;
        if (!confirm(`Удалить сотрудника «${editingEmployee.full_name}» вместе со всей историей смен?\nЕсли нужно просто скрыть — снимите галочку «Активный сотрудник».`)) return;
        try {
            const r = await fetch(`/api/v1/timesheet/employees/${editingEmployee.id}`, { method: 'DELETE' });
            if (!r.ok) throw new Error('Не удалось удалить');
            closeModal();
            await loadReport();
        } catch (e) {
            alert(e.message);
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
    el('fPayType').addEventListener('change', updateRateLabel);
    el('employeeForm').addEventListener('submit', saveEmployee);
    el('cancelModalBtn').addEventListener('click', closeModal);
    el('deleteEmployeeBtn').addEventListener('click', deleteEmployee);
    modal.addEventListener('click', (ev) => { if (ev.target === modal) closeModal(); });

    reportBody.addEventListener('click', (ev) => {
        const editBtn = ev.target.closest('[data-edit]');
        if (editBtn) {
            const id = parseInt(editBtn.dataset.edit);
            const row = currentReport.rows.find((x) => x.employee.id === id);
            if (row) openModal(row.employee);
            return;
        }
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
