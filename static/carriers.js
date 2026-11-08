// Справочник перевозчиков: список, карточка, скрытие
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, fmtDate, toast, openModal, closeModal } = Hub;
    const { readForm, fillForm, parseNumber, formatNumber, applyKind, currentKind, expiryState } = Parties;
    const el = (id) => document.getElementById(id);

    const listBody = el('listBody');
    const modal = el('carrierModal');
    const form = el('carrierForm');
    const kindSeg = el('kindSeg');
    const errorEl = el('carrierError');

    let carriers = [];
    let showActive = true;
    let isAdmin = false;
    let editingId = null; // null — новый перевозчик

    const KIND_LABEL = { 'ИП': 'ИП', 'ЮЛ': 'ООО', 'ФЛ': 'физлицо' };

    async function loadOptions() {
        const options = await api('/api/v1/carriers/options');
        const fill = (select, values) => { select.innerHTML = values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join(''); };
        fill(el('cVehicleType'), options.vehicle_types);
        fill(el('cBodyType'), options.body_types);
        fill(el('cLoadingType'), options.loading_types);
    }

    async function load() {
        try {
            carriers = await api('/api/v1/carriers');
            render();
        } catch (err) {
            listBody.innerHTML = `<tr><td colspan="5" class="empty-state">${esc(err.message)}</td></tr>`;
        }
    }

    function licenceCell(carrier) {
        if (!carrier.licence_to) return '<span class="text-muted">—</span>';
        const state = expiryState(carrier.licence_to);
        const date = fmtDate(carrier.licence_to);
        if (state === 'expired') return `<span class="ts-warn">${date}</span> <span class="badge badge-red">истекли</span>`;
        if (state === 'soon') return `${date} <span class="badge badge-yellow">скоро</span>`;
        return date;
    }

    function render() {
        const rows = carriers.filter(c => c.is_active === showActive);
        const hiddenCount = carriers.filter(c => !c.is_active).length;
        el('countText').textContent = hiddenCount && showActive ? `скрыто: ${hiddenCount}` : '';

        if (!rows.length) {
            const text = showActive
                ? (isAdmin ? 'Перевозчиков пока нет. Добавьте первого кнопкой «+ Перевозчик».' : 'Перевозчиков пока нет.')
                : 'Скрытых перевозчиков нет.';
            listBody.innerHTML = `<tr><td colspan="5" class="empty-state">${text}</td></tr>`;
            return;
        }

        listBody.innerHTML = rows.map((c) => {
            const contacts = [c.inn ? `ИНН ${esc(c.inn)}` : '', esc(c.phone || '')].filter(Boolean).join(' · ');
            const vehicle = [c.vehicle_model, c.vehicle_plate].filter(Boolean).map(esc).join(' ');
            const capacity = c.capacity_t != null ? `${formatNumber(c.capacity_t)} т` : '—';
            const volume = c.volume_m3 != null ? `<div class="cell-sub">${formatNumber(c.volume_m3)} м³</div>` : '';
            return `
            <tr data-id="${c.id}"${isAdmin ? ' class="click-row"' : ''}>
                <td>
                    <div class="cell-main">${esc(c.name)} <span class="badge badge-muted">${KIND_LABEL[c.kind] || esc(c.kind)}</span></div>
                    <div class="cell-sub">${contacts}</div>
                </td>
                <td>
                    <div>${vehicle || '<span class="text-muted">не указана</span>'}</div>
                    <div class="cell-sub">${esc([c.vehicle_type, c.body_type, `погрузка ${c.loading_type}`].join(', '))}</div>
                </td>
                <td class="num"><div>${capacity}</div>${volume}</td>
                <td>${licenceCell(c)}</td>
                <td class="actions-col">${isAdmin ? `
                    <button type="button" class="btn btn-secondary btn-sm" data-act="edit">Править</button>
                    <button type="button" class="btn btn-secondary btn-sm" data-act="toggle">${c.is_active ? 'Скрыть' : 'Вернуть'}</button>` : ''}
                </td>
            </tr>`;
        }).join('');
    }

    function openForm(carrier) {
        editingId = carrier ? carrier.id : null;
        el('carrierTitle').textContent = carrier ? carrier.name : 'Новый перевозчик';
        form.reset();
        // Имя, собранное из ФИО, не показываем: иначе оно не обновится при правке ФИО
        const fio = carrier ? [carrier.last_name, carrier.first_name, carrier.middle_name].filter(Boolean).join(' ') : '';
        fillForm(form, carrier ? { ...carrier, name: carrier.name === fio ? null : carrier.name } : {});
        applyKind(form, kindSeg, carrier ? carrier.kind : 'ИП');
        updateOgrnLabel();
        errorEl.textContent = '';
        el('carrierSave').disabled = false;
        openModal(modal);
    }

    function updateOgrnLabel() {
        el('cOgrnLabel').textContent = currentKind(kindSeg) === 'ЮЛ' ? 'ОГРН' : 'ОГРНИП';
    }

    function collect() {
        const data = readForm(form);
        data.kind = currentKind(kindSeg);
        for (const key of ['capacity_t', 'volume_m3']) {
            const value = parseNumber(data[key]);
            if (Number.isNaN(value) || (value !== null && value <= 0)) {
                throw new Error(key === 'capacity_t' ? 'Грузоподъёмность — положительное число тонн' : 'Объём — положительное число кубометров');
            }
            data[key] = value;
        }
        if (!data.inn) throw new Error('Укажите ИНН: по нему СБИС узнаёт перевозчика');
        if (!data.name && !(data.last_name || data.first_name)) throw new Error('Укажите фамилию и имя или название');
        if (!data.vehicle_type) delete data.vehicle_type;
        if (!data.body_type) delete data.body_type;
        if (!data.loading_type) delete data.loading_type;
        return data;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorEl.textContent = '';
        let data;
        try { data = collect(); } catch (err) { errorEl.textContent = err.message; return; }
        const saveBtn = el('carrierSave');
        saveBtn.disabled = true;
        try {
            const saved = editingId
                ? await api(`/api/v1/carriers/${editingId}`, { method: 'PUT', body: data })
                : await api('/api/v1/carriers', { method: 'POST', body: data });
            const idx = carriers.findIndex(c => c.id === saved.id);
            if (idx === -1) carriers.push(saved); else carriers[idx] = saved;
            closeModal(modal);
            render();
            toast(editingId ? 'Карточка сохранена' : `Перевозчик «${saved.name}» добавлен`);
        } catch (err) {
            errorEl.textContent = err.message;
            saveBtn.disabled = false;
        }
    });

    kindSeg.addEventListener('click', (e) => {
        const btn = e.target.closest('.seg-btn');
        if (!btn) return;
        applyKind(form, kindSeg, btn.dataset.kind);
        updateOgrnLabel();
    });

    modal.addEventListener('click', (e) => {
        if (e.target.closest('[data-act="cancel"]')) closeModal(modal);
    });

    el('addBtn').addEventListener('click', () => openForm(null));

    el('activeFilter').addEventListener('click', (e) => {
        const btn = e.target.closest('.seg-btn');
        if (!btn) return;
        el('activeFilter').querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('active', b === btn));
        showActive = btn.dataset.active === '1';
        render();
    });

    listBody.addEventListener('click', async (e) => {
        const row = e.target.closest('tr[data-id]');
        if (!row || !isAdmin) return;
        const carrier = carriers.find(c => c.id === Number(row.dataset.id));
        const act = e.target.closest('[data-act]')?.dataset.act;
        if (act === 'toggle') {
            try {
                const saved = await api(`/api/v1/carriers/${carrier.id}/active`, { method: 'PUT', body: { active: !carrier.is_active } });
                carriers[carriers.indexOf(carrier)] = saved;
                render();
                toast(saved.is_active ? `«${saved.name}» возвращён` : `«${saved.name}» скрыт`);
            } catch (err) { toast(err.message, 'error'); }
            return;
        }
        openForm(carrier);
    });

    async function init() {
        try {
            const user = await api('/api/v1/users/me');
            isAdmin = user.role === 'admin';
            el('addBtn').hidden = !isAdmin;
            if (isAdmin) await loadOptions();
        } catch (err) {
            console.error('Не удалось определить роль:', err);
        }
        await load();
    }

    init();
});
