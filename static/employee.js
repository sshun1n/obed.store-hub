/**
 * Карточка сотрудника: профиль, ФИО в табелях, оплата, календарь план/факт,
 * графики работы и отсутствия.
 */
document.addEventListener('DOMContentLoaded', () => {
    const {
        esc, api, isoDate, parseIso, fmtDate, fmtHM, fmtDur, label, pad,
        WEEKDAYS, MONTHS, ABSENCE, STATUS, PUNCH, toast, openModal, closeModal, confirmDialog,
    } = Hub;
    const el = (id) => document.getElementById(id);
    const empId = parseInt(new URLSearchParams(window.location.search).get('id'), 10);
    const API = `/api/v1/timesheet/employees/${empId}`;
    const todayIso = isoDate(new Date());

    let card = null;
    let calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    let editingScheduleId = null;
    let editingAbsenceId = null;
    let mergeCandidates = [];
    let mergeSelected = null;

    // --- Сегментированные переключатели ---

    function segValue(segEl) {
        return segEl.querySelector('.seg-btn.active')?.dataset.value;
    }
    function setSeg(segEl, value) {
        segEl.querySelectorAll('.seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.value === value));
    }
    document.querySelectorAll('.seg').forEach((segEl) => {
        segEl.addEventListener('click', (ev) => {
            const btn = ev.target.closest('.seg-btn');
            if (!btn) return;
            setSeg(segEl, btn.dataset.value);
            segEl.dispatchEvent(new Event('change'));
        });
    });

    document.querySelectorAll('.modal [data-close]').forEach((b) =>
        b.addEventListener('click', () => closeModal(b.closest('.modal'))));

    // --- Загрузка ---

    async function load() {
        try {
            card = await api(API);
        } catch (e) {
            if (/не найден/i.test(e.message)) {
                el('pageContent').style.display = 'none';
                el('notFound').style.display = 'block';
                return;
            }
            throw e;
        }
        el('pageContent').style.display = '';
        renderHeader();
        fillProfile();
        fillPay();
        renderAliases();
        renderSchedules();
        renderAbsences();
        await loadCalendar();
    }

    // --- Шапка ---

    function renderHeader() {
        const e = card.employee;
        document.title = `${label(e)} | OBED.STORE Hub`;
        el('empTitle').textContent = label(e);
        const parts = [e.position, e.department].filter(Boolean).map(esc);
        if (e.display_name) parts.push(`в табеле: ${esc(e.full_name)}`);
        parts.push(card.last_work_date
            ? `последняя смена ${fmtDate(card.last_work_date)} · смен в табелях: ${card.sessions_count}`
            : 'смен в табелях пока нет');
        el('empSubtitle').innerHTML = parts.join(' · ');
        el('archiveBtn').textContent = e.is_active ? 'В архив' : 'Вернуть из архива';
        el('archivedBanner').style.display = e.is_active ? 'none' : 'block';
    }

    // --- Профиль ---

    function fillProfile() {
        const e = card.employee;
        el('fDisplayName').value = e.display_name || '';
        el('fDisplayName').placeholder = e.full_name;
        el('fDepartment').value = e.department || '';
        el('fPosition').value = e.position || '';
        el('fTabNumber').value = e.tab_number || '';
        el('fPhone').value = e.phone || '';
        el('fNotes').value = e.notes || '';
    }

    async function updateEmployee(body, okMessage = 'Сохранено') {
        card.employee = await api(API, { method: 'PUT', body });
        renderHeader();
        toast(okMessage);
    }

    el('profileForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        try {
            await updateEmployee({
                display_name: el('fDisplayName').value,
                department: el('fDepartment').value,
                position: el('fPosition').value,
                tab_number: el('fTabNumber').value,
                phone: el('fPhone').value,
                notes: el('fNotes').value,
            });
        } catch (e) { toast(e.message, 'error'); }
    });

    // --- ФИО в табелях ---

    function renderAliases() {
        const chips = [`<span class="tag tag-primary" title="Основное ФИО из табеля">${esc(card.employee.full_name)}</span>`];
        card.aliases.forEach((a) => chips.push(
            `<span class="tag">${esc(a.name)}<span class="tag-remove" data-alias="${a.id}" title="Отвязать">×</span></span>`));
        el('aliasList').innerHTML = chips.join('');
    }

    el('aliasAddBtn').addEventListener('click', async () => {
        const name = el('aliasInput').value.trim();
        if (!name) return el('aliasInput').focus();
        try {
            card.aliases.push(await api(`${API}/aliases`, { method: 'POST', body: { name } }));
            card.aliases.sort((a, b) => a.name.localeCompare(b.name));
            el('aliasInput').value = '';
            renderAliases();
            toast('Написание привязано');
        } catch (e) { toast(e.message, 'error'); }
    });
    el('aliasInput').addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); el('aliasAddBtn').click(); }
    });

    el('aliasList').addEventListener('click', async (ev) => {
        const id = ev.target.dataset.alias;
        if (!id) return;
        const alias = card.aliases.find((a) => String(a.id) === id);
        const ok = await confirmDialog({
            title: 'Отвязать написание?',
            text: `Строки табеля с ФИО «${alias.name}» больше не будут относиться к этому сотруднику — при следующей загрузке для них создастся отдельная карточка. Уже загруженные смены останутся здесь.`,
            okText: 'Отвязать',
        });
        if (!ok) return;
        try {
            await api(`${API}/aliases/${id}`, { method: 'DELETE' });
            card.aliases = card.aliases.filter((a) => String(a.id) !== id);
            renderAliases();
        } catch (e) { toast(e.message, 'error'); }
    });

    // --- Оплата ---

    function updateRateLabel() {
        el('fPayRateLabel').textContent = segValue(el('fPayType')) === 'hour' ? 'Ставка, ₽/час' : 'Ставка, ₽/смена';
    }

    function fillPay() {
        const e = card.employee;
        setSeg(el('fPayType'), e.pay_type || 'shift');
        updateRateLabel();
        el('fPayRate').value = e.pay_rate || '';
        el('fNormHours').value = e.norm_minutes ? +(e.norm_minutes / 60).toFixed(2) : '';
        el('fUnderPenalty').value = e.underwork_penalty_per_hour || '';
        el('fUnderTol').value = e.underwork_tolerance_min || '';
        el('fLatePenalty').value = e.late_penalty || '';
        el('fLateTol').value = e.late_tolerance_min || '';
    }

    el('fPayType').addEventListener('change', updateRateLabel);

    el('payForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const normHours = parseFloat(el('fNormHours').value);
        try {
            await updateEmployee({
                pay_type: segValue(el('fPayType')),
                pay_rate: parseFloat(el('fPayRate').value) || 0,
                norm_minutes: normHours > 0 ? Math.round(normHours * 60) : null,
                underwork_penalty_per_hour: parseFloat(el('fUnderPenalty').value) || 0,
                underwork_tolerance_min: parseInt(el('fUnderTol').value, 10) || 0,
                late_penalty: parseFloat(el('fLatePenalty').value) || 0,
                late_tolerance_min: parseInt(el('fLateTol').value, 10) || 0,
            });
            await loadCalendar();
        } catch (e) { toast(e.message, 'error'); }
    });

    // --- Календарь ---

    const SHORT_ABSENCE = { vacation: 'отпуск', sick: 'больн.', dayoff: 'отгул' };

    // Рабочий по графику день в прошлом, за который табель не загружен
    const isNoData = (d) => d.status === 'planned' && d.work_date < todayIso;

    function dayTooltip(d) {
        const lines = [`${fmtDate(d.work_date)} — ${STATUS[d.status]}`];
        if (d.planned_start || d.planned_end) lines.push(`План: ${d.planned_start || '?'}–${d.planned_end || '?'}`);
        if (d.norm_minutes) lines.push(`Норма: ${fmtHM(d.norm_minutes)}`);
        if (d.check_in || d.check_out) lines.push(`Факт: ${d.check_in || '?'}–${d.check_out || '?'} (${fmtHM(d.worked_minutes)})`);
        if (d.late_minutes) lines.push(`Опоздание: ${d.late_minutes} мин${d.late_counted ? '' : ' (в пределах допуска)'}`);
        if (d.early_minutes) lines.push(`Ранний уход: ${d.early_minutes} мин`);
        if (d.shortfall_minutes) lines.push(`Недоработка: ${fmtHM(d.shortfall_minutes)}`);
        if (d.absence_note) lines.push(d.absence_note);
        if (d.punch_issue) lines.push(`⚠ ${PUNCH[d.punch_issue]}`);
        if (d.source === 'timesheet') lines.push('Норма из графика табеля');
        if (isNoData(d)) lines.push('Табель за этот день ещё не загружен');
        return lines.join('\n');
    }

    function dayLine(d) {
        switch (d.status) {
            case 'work':
            case 'extra':
                return fmtHM(d.worked_minutes);
            case 'planned':
                return d.planned_start ? d.planned_start.replace(/^0/, '') : (d.norm_minutes ? fmtHM(d.norm_minutes) : 'смена');
            case 'absent':
                return 'прогул';
            case 'vacation':
            case 'sick':
            case 'dayoff':
                return SHORT_ABSENCE[d.status];
            default:
                return '';
        }
    }

    function renderSummary(days) {
        const count = (st) => days.filter((d) => d.status === st).length;
        const worked = days.filter((d) => d.status === 'work' || d.status === 'extra');
        const workedMin = worked.reduce((a, d) => a + d.worked_minutes, 0);
        const normMin = days
            .filter((d) => ['work', 'absent', 'planned'].includes(d.status))
            .reduce((a, d) => a + (d.norm_minutes || 0), 0);
        const items = [];
        items.push(`<span><b>${worked.length}</b> смен · ${fmtDur(workedMin)}</span>`);
        if (normMin) items.push(`<span>норма <b>${fmtDur(normMin)}</b></span>`);
        const noData = days.filter(isNoData).length;
        const ahead = count('planned') - noData;
        if (ahead) items.push(`<span>впереди <b>${ahead}</b> смен</span>`);
        if (noData) items.push(`<span class="text-muted">без данных табеля <b>${noData}</b> дн.</span>`);
        if (count('absent')) items.push(`<span class="ts-warn">прогулов <b>${count('absent')}</b></span>`);
        const late = days.filter((d) => d.late_counted).length;
        if (late) items.push(`<span class="ts-warn">опозданий <b>${late}</b></span>`);
        const punch = days.filter((d) => d.punch_issue).length;
        if (punch) items.push(`<span class="punch-warn" title="Нет отметки прихода или ухода — время досчитано системой учёта">неполных отметок <b>${punch}</b></span>`);
        Object.keys(ABSENCE).forEach((k) => {
            if (count(k)) items.push(`<span>${ABSENCE[k].toLowerCase()} <b>${count(k)}</b> дн.</span>`);
        });
        el('calSummary').innerHTML = items.join('');
    }

    async function loadCalendar() {
        const y = calMonth.getFullYear(), m = calMonth.getMonth();
        const first = new Date(y, m, 1), last = new Date(y, m + 1, 0);
        el('calMonth').textContent = `${MONTHS[m]} ${y}`;

        const lastWork = card.last_work_date;
        el('calLast').style.display = lastWork && !lastWork.startsWith(`${y}-${pad(m + 1)}`) ? '' : 'none';

        const cal = el('calendar');
        cal.classList.add('loading');
        let days = [];
        try {
            days = await api(`${API}/days?start=${isoDate(first)}&end=${isoDate(last)}`);
        } catch (e) {
            toast(e.message, 'error');
        }
        cal.classList.remove('loading');

        const lead = (first.getDay() + 6) % 7;
        const cells = WEEKDAYS.map((w, i) => `<div class="cal-wd${i >= 5 ? ' cal-wknd' : ''}">${w}</div>`);
        for (let i = 0; i < lead; i++) cells.push('<div class="cal-cell cal-blank"></div>');
        days.forEach((d) => {
            const num = parseInt(d.work_date.slice(8), 10);
            const flags = [];
            if (d.late_counted) flags.push('<i class="cal-flag" title="Опоздание"></i>');
            if (d.punch_issue) flags.push('<i class="cal-punch" title="Неполные отметки">!</i>');
            const shortfall = d.shortfall_minutes ? `<span class="cal-short">−${fmtHM(d.shortfall_minutes)}</span>` : '';
            cells.push(`
                <button type="button" class="cal-cell st-${d.status}${d.work_date === todayIso ? ' cal-today' : ''}${isNoData(d) ? ' cal-nodata' : ''}"
                        data-date="${d.work_date}" title="${esc(dayTooltip(d))}">
                    <span class="cal-num">${num}${flags.join('')}</span>
                    <span class="cal-line">${esc(dayLine(d))}</span>
                    ${shortfall}
                </button>`);
        });
        cal.innerHTML = cells.join('');
        renderSummary(days);
    }

    function shiftMonth(delta) {
        calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + delta, 1);
        loadCalendar();
    }
    el('calPrev').addEventListener('click', () => shiftMonth(-1));
    el('calNext').addEventListener('click', () => shiftMonth(1));
    el('calToday').addEventListener('click', () => {
        calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
        loadCalendar();
    });
    el('calLast').addEventListener('click', () => {
        const d = parseIso(card.last_work_date);
        calMonth = new Date(d.getFullYear(), d.getMonth(), 1);
        loadCalendar();
    });

    // Клик по дню — отметить отсутствие (или открыть существующее)
    el('calendar').addEventListener('click', (ev) => {
        const cell = ev.target.closest('.cal-cell[data-date]');
        if (!cell) return;
        const date = cell.dataset.date;
        const existing = card.absences.find((a) => a.date_from <= date && date <= a.date_to);
        openAbsenceModal(existing || { date_from: date, date_to: date });
    });

    // --- Графики ---

    function periodBadge(from, to) {
        if (to && to < todayIso) return '<span class="badge badge-muted">завершён</span>';
        if (from > todayIso) return '<span class="badge badge-blue">запланирован</span>';
        return '<span class="badge badge-green">действует</span>';
    }

    function renderSchedules() {
        const list = el('scheduleList');
        if (!card.schedules.length) {
            list.innerHTML = `<div class="item-empty">Своего графика нет — рабочие дни и норма берутся из табеля.
                Добавьте график, чтобы задать дни, время смены и считать опоздания по нему.</div>`;
            return;
        }
        list.innerHTML = card.schedules.map((s) => `
            <div class="item">
                <div class="item-main">
                    <div class="item-title">${esc(s.summary)}</div>
                    <div class="item-sub">${fmtDate(s.date_from)} — ${s.date_to ? fmtDate(s.date_to) : 'бессрочно'}${
                        s.kind === 'cycle' ? ` · цикл с ${fmtDate(s.cycle_start)}` : ''}${
                        s.break_minutes ? ` · перерыв ${s.break_minutes} мин` : ''}${
                        s.note ? ` · ${esc(s.note)}` : ''}</div>
                </div>
                <div class="item-side">
                    ${periodBadge(s.date_from, s.date_to)}
                    <button class="action-btn btn-edit-user" data-edit-schedule="${s.id}">изменить</button>
                    <button class="action-btn btn-delete-user" data-del-schedule="${s.id}">удалить</button>
                </div>
            </div>`).join('');
    }

    el('scheduleList').addEventListener('click', async (ev) => {
        const editId = ev.target.dataset.editSchedule;
        const delId = ev.target.dataset.delSchedule;
        if (editId) openScheduleModal(card.schedules.find((s) => String(s.id) === editId));
        if (delId) {
            const s = card.schedules.find((x) => String(x.id) === delId);
            const ok = await confirmDialog({
                title: 'Удалить график?',
                text: `«${s.summary}» с ${fmtDate(s.date_from)}. Дни этого периода будут считаться по другим графикам или по табелю.`,
            });
            if (!ok) return;
            try {
                await api(`/api/v1/timesheet/schedules/${delId}`, { method: 'DELETE' });
                toast('График удалён');
                await reloadCard();
            } catch (e) { toast(e.message, 'error'); }
        }
    });

    // Форма графика
    el('sWeekdays').innerHTML = WEEKDAYS.map((w, i) =>
        `<button type="button" class="wd-btn${i >= 5 ? ' wd-wknd' : ''}" data-wd="${i}">${w}</button>`).join('');

    function setWeekdays(list) {
        el('sWeekdays').querySelectorAll('.wd-btn').forEach((b) =>
            b.classList.toggle('on', list.includes(parseInt(b.dataset.wd, 10))));
    }
    function getWeekdays() {
        return [...el('sWeekdays').querySelectorAll('.wd-btn.on')].map((b) => parseInt(b.dataset.wd, 10));
    }

    el('sWeekdays').addEventListener('click', (ev) => {
        const b = ev.target.closest('.wd-btn');
        if (b) b.classList.toggle('on');
    });
    el('sWeeklyBlock').addEventListener('click', (ev) => {
        const chip = ev.target.closest('[data-wd]');
        if (chip && chip.classList.contains('chip')) setWeekdays(chip.dataset.wd.split(',').map(Number));
    });
    el('sCycleBlock').addEventListener('click', (ev) => {
        const chip = ev.target.closest('[data-cycle]');
        if (!chip) return;
        const [w, o] = chip.dataset.cycle.split(',');
        el('sCycleWork').value = w;
        el('sCycleOff').value = o;
        renderCyclePreview();
    });

    function toggleKindBlocks() {
        const cycle = segValue(el('sKind')) === 'cycle';
        el('sWeeklyBlock').style.display = cycle ? 'none' : '';
        el('sCycleBlock').style.display = cycle ? '' : 'none';
        if (cycle && !el('sCycleStart').value) el('sCycleStart').value = el('sFrom').value;
        renderCyclePreview();
    }
    el('sKind').addEventListener('change', toggleKindBlocks);

    function renderCyclePreview() {
        const work = parseInt(el('sCycleWork').value, 10) || 0;
        const off = parseInt(el('sCycleOff').value, 10) || 0;
        const startIso = el('sCycleStart').value || el('sFrom').value;
        if (!work || !startIso) { el('sCyclePreview').innerHTML = ''; return; }
        const start = parseIso(startIso);
        const cells = [];
        for (let i = 0; i < 14; i++) {
            const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
            const isWork = i % (work + off) < work;
            cells.push(`<span class="cp-cell${isWork ? ' cp-work' : ''}"><b>${d.getDate()}</b>${WEEKDAYS[(d.getDay() + 6) % 7]}</span>`);
        }
        el('sCyclePreview').innerHTML = `<span class="field-label-sm">Ближайшие 14 дней</span><div class="cp-row">${cells.join('')}</div>`;
    }
    ['sCycleWork', 'sCycleOff', 'sCycleStart'].forEach((id) => el(id).addEventListener('input', renderCyclePreview));
    el('sFrom').addEventListener('change', () => {
        if (segValue(el('sKind')) === 'cycle' && !editingScheduleId) el('sCycleStart').value = el('sFrom').value;
        renderCyclePreview();
    });

    function timeToMin(v) {
        if (!v) return null;
        const [h, m] = v.split(':').map(Number);
        return h * 60 + m;
    }

    function updateNormHint() {
        const duration = parseFloat(el('sDuration').value);
        let norm = null;
        if (duration > 0) {
            norm = Math.round(duration * 60);
        } else {
            const st = timeToMin(el('sStart').value), en = timeToMin(el('sEnd').value);
            if (st !== null && en !== null) {
                let d = en - st;
                if (d <= 0) d += 24 * 60;
                d -= parseInt(el('sBreak').value, 10) || 0;
                norm = d > 0 ? d : null;
            }
        }
        const def = card.employee.norm_minutes;
        el('sNormHint').innerHTML = norm
            ? `Норма смены: <b>${fmtDur(norm)}</b>`
            : def
                ? `Норма смены не задана — будет использована норма по умолчанию: <b>${fmtDur(def)}</b>`
                : 'Норма смены не задана — недоработка по этому графику считаться не будет.';
    }
    ['sStart', 'sEnd', 'sBreak', 'sDuration'].forEach((id) => el(id).addEventListener('input', updateNormHint));

    function openScheduleModal(s) {
        editingScheduleId = s ? s.id : null;
        el('scheduleModalTitle').textContent = s ? 'Изменить график' : 'Новый график';
        el('sFrom').value = s ? s.date_from : todayIso;
        el('sTo').value = s?.date_to || '';
        setSeg(el('sKind'), s ? s.kind : 'weekly');
        setWeekdays(s ? s.weekdays : [0, 1, 2, 3, 4]);
        el('sCycleWork').value = s?.cycle_work ?? 2;
        el('sCycleOff').value = s?.cycle_off ?? 2;
        el('sCycleStart').value = s?.cycle_start || '';
        el('sStart').value = s?.shift_start || '';
        el('sEnd').value = s?.shift_end || '';
        el('sBreak').value = s?.break_minutes || '';
        el('sDuration').value = s?.shift_minutes ? +(s.shift_minutes / 60).toFixed(2) : '';
        el('sNote').value = s?.note || '';
        toggleKindBlocks();
        updateNormHint();
        openModal(el('scheduleModal'));
    }
    el('addScheduleBtn').addEventListener('click', () => openScheduleModal(null));

    el('scheduleForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const kind = segValue(el('sKind'));
        const weekdays = getWeekdays();
        if (kind === 'weekly' && !weekdays.length) return toast('Выберите хотя бы один рабочий день', 'error');
        const duration = parseFloat(el('sDuration').value);
        const body = {
            date_from: el('sFrom').value,
            date_to: el('sTo').value || null,
            kind,
            weekdays,
            cycle_work: kind === 'cycle' ? parseInt(el('sCycleWork').value, 10) : null,
            cycle_off: kind === 'cycle' ? parseInt(el('sCycleOff').value, 10) : null,
            cycle_start: kind === 'cycle' ? (el('sCycleStart').value || null) : null,
            shift_start: el('sStart').value || null,
            shift_end: el('sEnd').value || null,
            break_minutes: parseInt(el('sBreak').value, 10) || 0,
            shift_minutes: duration > 0 ? Math.round(duration * 60) : null,
            note: el('sNote').value || null,
        };
        const closed = !editingScheduleId && card.schedules.find((s) => !s.date_to && s.date_from < body.date_from);
        try {
            if (editingScheduleId) {
                await api(`/api/v1/timesheet/schedules/${editingScheduleId}`, { method: 'PUT', body });
            } else {
                await api(`${API}/schedules`, { method: 'POST', body });
            }
            closeModal(el('scheduleModal'));
            const prevDay = new Date(parseIso(body.date_from).getTime() - 86400000);
            toast(closed ? `График сохранён. Предыдущий закрыт датой ${fmtDate(isoDate(prevDay))}` : 'График сохранён');
            await reloadCard();
        } catch (e) { toast(e.message, 'error'); }
    });

    // --- Отсутствия ---

    function daysBetween(fromIso, toIso) {
        return Math.round((parseIso(toIso) - parseIso(fromIso)) / 86400000) + 1;
    }

    function renderAbsences() {
        const list = el('absenceList');
        if (!card.absences.length) {
            list.innerHTML = '<div class="item-empty">Отсутствий нет. Отметить день можно и кликом по календарю.</div>';
            return;
        }
        list.innerHTML = card.absences.map((a) => {
            const n = daysBetween(a.date_from, a.date_to);
            let badge = '';
            if (a.date_from <= todayIso && todayIso <= a.date_to) badge = '<span class="badge badge-yellow">сейчас</span>';
            else if (a.date_from > todayIso) badge = '<span class="badge badge-blue">впереди</span>';
            return `
            <div class="item">
                <span class="item-dot st-${a.kind}"></span>
                <div class="item-main">
                    <div class="item-title">${ABSENCE[a.kind]} · ${n} ${n === 1 ? 'день' : n < 5 ? 'дня' : 'дней'}${a.paid ? ' <span class="badge badge-green">оплачиваемый</span>' : ''}</div>
                    <div class="item-sub">${fmtDate(a.date_from)}${a.date_to !== a.date_from ? ` — ${fmtDate(a.date_to)}` : ''}${a.note ? ` · ${esc(a.note)}` : ''}</div>
                </div>
                <div class="item-side">
                    ${badge}
                    <button class="action-btn btn-edit-user" data-edit-absence="${a.id}">изменить</button>
                    <button class="action-btn btn-delete-user" data-del-absence="${a.id}">удалить</button>
                </div>
            </div>`;
        }).join('');
    }

    function updateAbsenceHint() {
        const from = el('aFrom').value, to = el('aTo').value;
        el('aDaysHint').innerHTML = from && to && to >= from
            ? `Календарных дней: <b>${daysBetween(from, to)}</b>`
            : '';
    }

    function openAbsenceModal(a) {
        editingAbsenceId = a?.id || null;
        el('absenceModalTitle').textContent = editingAbsenceId ? 'Изменить отсутствие' : 'Новое отсутствие';
        setSeg(el('aKind'), a?.kind || 'vacation');
        el('aFrom').value = a?.date_from || todayIso;
        el('aTo').value = a?.date_to || el('aFrom').value;
        el('aPaid').checked = !!a?.paid;
        el('aNote').value = a?.note || '';
        updateAbsenceHint();
        openModal(el('absenceModal'));
    }
    el('addAbsenceBtn').addEventListener('click', () => openAbsenceModal(null));
    el('aFrom').addEventListener('change', () => {
        if (!el('aTo').value || el('aTo').value < el('aFrom').value) el('aTo').value = el('aFrom').value;
        updateAbsenceHint();
    });
    el('aTo').addEventListener('change', updateAbsenceHint);

    el('absenceForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const body = {
            kind: segValue(el('aKind')),
            date_from: el('aFrom').value,
            date_to: el('aTo').value,
            paid: el('aPaid').checked,
            note: el('aNote').value || null,
        };
        try {
            if (editingAbsenceId) {
                await api(`/api/v1/timesheet/absences/${editingAbsenceId}`, { method: 'PUT', body });
            } else {
                await api(`${API}/absences`, { method: 'POST', body });
            }
            closeModal(el('absenceModal'));
            toast('Сохранено');
            await reloadCard();
        } catch (e) { toast(e.message, 'error'); }
    });

    el('absenceList').addEventListener('click', async (ev) => {
        const editId = ev.target.dataset.editAbsence;
        const delId = ev.target.dataset.delAbsence;
        if (editId) openAbsenceModal(card.absences.find((a) => String(a.id) === editId));
        if (delId) {
            const a = card.absences.find((x) => String(x.id) === delId);
            const ok = await confirmDialog({
                title: 'Удалить запись?',
                text: `${ABSENCE[a.kind]} ${fmtDate(a.date_from)} — ${fmtDate(a.date_to)}. Дни снова будут считаться по графику.`,
            });
            if (!ok) return;
            try {
                await api(`/api/v1/timesheet/absences/${delId}`, { method: 'DELETE' });
                toast('Запись удалена');
                await reloadCard();
            } catch (e) { toast(e.message, 'error'); }
        }
    });

    async function reloadCard() {
        card = await api(API);
        renderHeader();
        renderSchedules();
        renderAbsences();
        await loadCalendar();
    }

    // --- Архив и удаление ---

    el('archiveBtn').addEventListener('click', async () => {
        const toArchive = card.employee.is_active;
        try {
            await updateEmployee({ is_active: !toArchive }, toArchive ? 'Сотрудник перенесён в архив' : 'Сотрудник снова активен');
        } catch (e) { toast(e.message, 'error'); }
    });

    el('deleteBtn').addEventListener('click', async () => {
        const ok = await confirmDialog({
            title: `Удалить «${label(card.employee)}»?`,
            text: 'Будут безвозвратно удалены все смены, графики и отсутствия. Если сотрудник есть в табелях, при следующей загрузке он появится снова как новый. Чтобы просто скрыть его — используйте архив.',
            okText: 'Удалить навсегда',
        });
        if (!ok) return;
        try {
            await api(API, { method: 'DELETE' });
            window.location.href = '/employees';
        } catch (e) { toast(e.message, 'error'); }
    });

    // --- Объединение ---

    function renderMergeList() {
        const q = el('mergeSearch').value.trim().toLowerCase();
        const rows = mergeCandidates.filter((e) =>
            !q || [e.full_name, e.display_name, ...(e.aliases || [])].filter(Boolean).join(' ').toLowerCase().includes(q));
        el('mergeList').innerHTML = rows.length ? rows.map((e) => `
            <label class="merge-item${mergeSelected === e.id ? ' selected' : ''}">
                <input type="radio" name="mergeSource" value="${e.id}"${mergeSelected === e.id ? ' checked' : ''}>
                <span class="merge-name">${esc(label(e))}${e.is_active ? '' : ' <span class="badge badge-muted">архив</span>'}</span>
                <span class="merge-sub">${e.display_name ? `в табеле: ${esc(e.full_name)} · ` : ''}${
                    e.last_work_date ? `последняя смена ${fmtDate(e.last_work_date)}` : 'смен нет'}</span>
            </label>`).join('')
            : '<div class="item-empty">Никого не нашли</div>';
    }

    el('mergeBtn').addEventListener('click', async () => {
        mergeSelected = null;
        el('mergeConfirmBtn').disabled = true;
        el('mergeSearch').value = '';
        try {
            const all = await api('/api/v1/timesheet/employees?status=all');
            mergeCandidates = all.filter((e) => e.id !== empId);
        } catch (e) { return toast(e.message, 'error'); }
        // Подсказка: сначала однофамильцы
        const surname = card.employee.full_name.split(' ')[0].toLowerCase();
        mergeCandidates.sort((a, b) =>
            (b.full_name.toLowerCase().startsWith(surname) - a.full_name.toLowerCase().startsWith(surname))
            || label(a).localeCompare(label(b)));
        renderMergeList();
        openModal(el('mergeModal'));
    });
    el('mergeSearch').addEventListener('input', renderMergeList);
    el('mergeList').addEventListener('change', (ev) => {
        mergeSelected = parseInt(ev.target.value, 10);
        el('mergeConfirmBtn').disabled = false;
        renderMergeList();
    });

    el('mergeConfirmBtn').addEventListener('click', async () => {
        if (!mergeSelected) return;
        const source = mergeCandidates.find((e) => e.id === mergeSelected);
        try {
            await api(`${API}/merge`, { method: 'POST', body: { source_id: mergeSelected } });
            closeModal(el('mergeModal'));
            toast(`«${label(source)}» объединён с этой карточкой`);
            await reloadCard();
            fillProfile();
            fillPay();
            renderAliases();
        } catch (e) { toast(e.message, 'error'); }
    });

    // --- Старт ---

    (async () => {
        try {
            if (!empId) {
                el('notFound').style.display = 'block';
                return;
            }
            if (await Hub.requireAdmin(null, el('accessDenied'))) await load();
        } catch (e) { toast(e.message, 'error'); }
    })();
});
