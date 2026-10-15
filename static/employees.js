/**
 * Справочник сотрудников: поиск, фильтр по статусу, ручное добавление.
 */
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtDate, fmtMoney, label, toast, openModal, closeModal } = Hub;
    const el = (id) => document.getElementById(id);
    const listBody = el('listBody');
    const addModal = el('addModal');

    let employees = [];
    let status = 'active';
    let search = '';

    async function load() {
        listBody.innerHTML = '<tr><td colspan="6" class="empty-state">Загрузка…</td></tr>';
        try {
            employees = await api(`/api/v1/timesheet/employees?status=${status}`);
            render();
        } catch (e) {
            listBody.innerHTML = `<tr><td colspan="6" class="empty-state">${esc(e.message)}</td></tr>`;
        }
    }

    function matches(e) {
        if (!search) return true;
        const hay = [e.full_name, e.display_name, e.position, e.department, ...(e.aliases || [])]
            .filter(Boolean).join(' ').toLowerCase();
        return hay.includes(search);
    }

    function render() {
        const rows = employees.filter(matches);
        el('countText').textContent = rows.length === employees.length
            ? `${employees.length} чел.`
            : `${rows.length} из ${employees.length}`;

        if (!rows.length) {
            listBody.innerHTML = `<tr><td colspan="6" class="empty-state"><h3>${employees.length ? 'Никого не нашли' : 'Список пуст'}</h3>${
                employees.length ? 'Попробуйте изменить запрос.' : 'Сотрудники появятся после загрузки табеля в разделе «Учёт времени».'}</td></tr>`;
            return;
        }

        listBody.innerHTML = rows.map((e) => {
            const sub = [];
            if (e.display_name) sub.push(`в табеле: ${esc(e.full_name)}`);
            if (e.aliases.length) sub.push(`+${e.aliases.length} ${e.aliases.length === 1 ? 'написание' : 'написания'}`);
            const role = [e.department, e.position].filter(Boolean).map(esc).join(' · ') || '<span class="text-muted">—</span>';
            const schedule = e.schedule_summary
                ? esc(e.schedule_summary)
                : '<span class="text-muted">из табеля</span>';
            const pay = e.pay_rate
                ? `${fmtMoney(e.pay_rate)}<span class="ts-rate-type">/${e.pay_type === 'hour' ? 'час' : 'смена'}</span>`
                : '<span class="ts-warn">не задана</span>';
            let badge;
            if (!e.is_active) badge = '<span class="badge badge-muted">архив</span>';
            else if (e.absence_now) badge = `<span class="badge badge-yellow">${esc(e.absence_now)}</span>`;
            else badge = '<span class="badge badge-green">работает</span>';
            return `
            <tr class="click-row${e.is_active ? '' : ' row-archived'}" data-id="${e.id}">
                <td>
                    <a class="ts-name row-link" href="/employee?id=${e.id}">${esc(label(e))}</a>
                    ${sub.length ? `<div class="ts-position">${sub.join(' · ')}</div>` : ''}
                </td>
                <td class="cell-soft">${role}</td>
                <td class="ts-num">${schedule}</td>
                <td class="ts-num">${pay}</td>
                <td class="ts-num">${e.last_work_date ? fmtDate(e.last_work_date) : '<span class="text-muted">—</span>'}</td>
                <td>${badge}</td>
            </tr>`;
        }).join('');
    }

    // --- События ---

    el('searchInput').addEventListener('input', (ev) => {
        search = ev.target.value.trim().toLowerCase();
        render();
    });

    el('statusFilter').addEventListener('click', (ev) => {
        const btn = ev.target.closest('.seg-btn');
        if (!btn || btn.dataset.status === status) return;
        el('statusFilter').querySelectorAll('.seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
        status = btn.dataset.status;
        load();
    });

    listBody.addEventListener('click', (ev) => {
        if (ev.target.closest('a')) return;
        const tr = ev.target.closest('tr[data-id]');
        if (tr) window.location.href = `/employee?id=${tr.dataset.id}`;
    });

    el('addBtn').addEventListener('click', () => {
        el('addForm').reset();
        openModal(addModal);
    });
    addModal.querySelector('[data-close]').addEventListener('click', () => closeModal(addModal));

    el('addForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        try {
            const emp = await api('/api/v1/timesheet/employees', {
                method: 'POST',
                body: {
                    full_name: el('aFullName').value,
                    display_name: el('aDisplayName').value || null,
                    department: el('aDepartment').value || null,
                    position: el('aPosition').value || null,
                },
            });
            window.location.href = `/employee?id=${emp.id}`;
        } catch (e) {
            toast(e.message, 'error');
        }
    });

    (async () => {
        try {
            if (await Hub.requireAdmin(el('pageContent'), el('accessDenied'))) load();
        } catch (e) { toast(e.message, 'error'); }
    })();
});
