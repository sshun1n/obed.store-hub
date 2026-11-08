// Юрлица: мы (продавец, грузоотправитель) и покупатели
document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, toast, openModal, closeModal } = Hub;
    const { readForm, fillForm, applyKind, currentKind } = Parties;
    const el = (id) => document.getElementById(id);

    const modal = el('entityModal');
    const form = el('entityForm');
    const kindSeg = el('kindSeg');
    const errorEl = el('entityError');

    let entities = [];
    let isAdmin = false;
    let editing = null; // { id, role }; id null — новое юрлицо

    const EMPTY_TEXT = {
        own: 'Добавьте своё юрлицо: от его имени пойдут реализации, УПД и накладные.',
        buyer: 'Покупателей нет.',
    };

    async function load() {
        try {
            entities = await api('/api/v1/legal-entities');
            render();
        } catch (err) {
            document.querySelectorAll('[data-list]').forEach((tbody) => {
                tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${esc(err.message)}</td></tr>`;
            });
        }
    }

    function requisites(e) {
        const rows = [`ИНН ${esc(e.inn)}`];
        if (e.kpp) rows.push(`КПП ${esc(e.kpp)}`);
        if (e.ogrn) rows.push(`${e.kind === 'ИП' ? 'ОГРНИП' : 'ОГРН'} ${esc(e.ogrn)}`);
        return rows.map(r => `<div class="mono-cell">${r}</div>`).join('');
    }

    function contacts(e) {
        const parts = [];
        parts.push(e.address ? `<div class="cell-wrap">${esc(e.address)}</div>` : '<div class="text-muted">адрес не указан</div>');
        const line = [e.phone, e.email].filter(Boolean).map(esc).join(' · ');
        if (line) parts.push(`<div class="cell-sub">${line}</div>`);
        else parts.push('<div class="cell-sub ts-warn">нет телефона</div>');
        if (e.edo_id) parts.push(`<div class="cell-sub mono-cell">ЭДО ${esc(e.edo_id)}</div>`);
        return parts.join('');
    }

    function renderRole(role) {
        const tbody = document.querySelector(`[data-list="${role}"]`);
        const rows = entities.filter(e => e.role === role);
        if (!rows.length) {
            tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${EMPTY_TEXT[role]}</td></tr>`;
            return;
        }
        tbody.innerHTML = rows.map((e) => {
            const badges = [
                `<span class="badge badge-muted">${e.kind === 'ЮЛ' ? 'ООО' : 'ИП'}</span>`,
                e.is_default ? '<span class="badge badge-green">по умолчанию</span>' : '',
                e.is_active ? '' : '<span class="badge badge-muted">скрыто</span>',
            ].join(' ');
            const sub = e.kind === 'ЮЛ' && e.full_name && e.full_name !== e.name ? `<div class="cell-sub">${esc(e.full_name)}</div>` : '';
            return `
            <tr data-id="${e.id}" class="${isAdmin ? 'click-row' : ''}${e.is_active ? '' : ' row-archived'}">
                <td><div class="cell-main">${esc(e.name)} ${badges}</div>${sub}</td>
                <td>${requisites(e)}</td>
                <td>${contacts(e)}</td>
                <td class="actions-col">${isAdmin ? `
                    ${e.is_active && !e.is_default ? '<button type="button" class="btn btn-secondary btn-sm" data-act="default">По умолчанию</button>' : ''}
                    <button type="button" class="btn btn-secondary btn-sm" data-act="edit">Править</button>
                    <button type="button" class="btn btn-secondary btn-sm" data-act="toggle">${e.is_active ? 'Скрыть' : 'Вернуть'}</button>` : ''}
                </td>
            </tr>`;
        }).join('');
    }

    function render() {
        renderRole('own');
        renderRole('buyer');
    }

    function updateOgrnLabel() {
        el('eOgrnLabel').textContent = currentKind(kindSeg) === 'ЮЛ' ? 'ОГРН' : 'ОГРНИП';
    }

    function openForm(entity, role) {
        editing = { id: entity ? entity.id : null, role: entity ? entity.role : role };
        el('entityTitle').textContent = entity ? entity.name : (role === 'own' ? 'Новое юрлицо' : 'Новый покупатель');
        form.reset();
        // Имя, собранное автоматически, не показываем: иначе оно не обновится при правке ФИО или наименования
        let shownName = entity ? entity.name : null;
        if (entity) {
            const fio = [entity.last_name, entity.first_name, entity.middle_name].filter(Boolean).join(' ');
            if (entity.name === `ИП ${fio}` || entity.name === entity.full_name) shownName = null;
        }
        fillForm(form, entity ? { ...entity, name: shownName } : {});
        applyKind(form, kindSeg, entity ? entity.kind : (role === 'own' ? 'ИП' : 'ЮЛ'));
        updateOgrnLabel();
        errorEl.textContent = '';
        el('entitySave').disabled = false;
        openModal(modal);
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorEl.textContent = '';
        const data = { ...readForm(form), kind: currentKind(kindSeg), role: editing.role };
        if (!data.inn) { errorEl.textContent = 'Укажите ИНН'; return; }
        const saveBtn = el('entitySave');
        saveBtn.disabled = true;
        try {
            await (editing.id
                ? api(`/api/v1/legal-entities/${editing.id}`, { method: 'PUT', body: data })
                : api('/api/v1/legal-entities', { method: 'POST', body: data }));
            closeModal(modal);
            toast(editing.id ? 'Реквизиты сохранены' : 'Юрлицо добавлено');
            await load(); // могло смениться юрлицо по умолчанию
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

    document.querySelectorAll('.party-section').forEach((section) => {
        section.querySelector('[data-act="add"]').addEventListener('click', () => openForm(null, section.dataset.role));
    });

    document.querySelectorAll('[data-list]').forEach((tbody) => {
        tbody.addEventListener('click', async (e) => {
            const row = e.target.closest('tr[data-id]');
            if (!row || !isAdmin) return;
            const entity = entities.find(x => x.id === Number(row.dataset.id));
            const act = e.target.closest('[data-act]')?.dataset.act;
            try {
                if (act === 'toggle') {
                    const saved = await api(`/api/v1/legal-entities/${entity.id}/active`, { method: 'PUT', body: { active: !entity.is_active } });
                    toast(saved.is_active ? `«${saved.name}» возвращено` : `«${saved.name}» скрыто`);
                    await load();
                } else if (act === 'default') {
                    const saved = await api(`/api/v1/legal-entities/${entity.id}/default`, { method: 'PUT' });
                    toast(`«${saved.name}» теперь по умолчанию`);
                    await load();
                } else {
                    openForm(entity);
                }
            } catch (err) {
                toast(err.message, 'error');
            }
        });
    });

    async function init() {
        try {
            const user = await api('/api/v1/users/me');
            isAdmin = user.role === 'admin';
            document.querySelectorAll('.party-section [data-act="add"]').forEach((btn) => { btn.hidden = !isAdmin; });
        } catch (err) {
            console.error('Не удалось определить роль:', err);
        }
        await load();
    }

    init();
});
