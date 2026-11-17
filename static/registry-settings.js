// Админ-панель: реестр 1С — наши ответственные (отличают наши накладные от кондитерки),
// названия покупателей в 1С и разбор последней загрузки
document.addEventListener('DOMContentLoaded', () => {
    const el = (id) => document.getElementById(id);
    const list = el('regResp');
    if (!list) return;
    const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const key = (s) => String(s || '').toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ').trim();
    let current = null;

    function seen(s) {
        const names = new Map();
        for (const n of s.responsibles) names.set(key(n), n);
        for (const g of (s.last && s.last.groups) || []) {
            if (g.responsible && !names.has(key(g.responsible))) names.set(key(g.responsible), g.responsible);
        }
        return [...names.values()].sort((a, b) => a.localeCompare(b, 'ru'));
    }

    function show(s) {
        current = s;
        const ours = new Set(s.responsibles.map(key));
        const names = seen(s);
        list.innerHTML = names.length
            ? names.map((n) => `<label class="reg-resp-item"><input type="checkbox" value="${esc(n)}"${ours.has(key(n)) ? ' checked' : ''}> ${esc(n)}</label>`).join('')
            : '<p class="field-hint">Пока никого: загрузите реестр с колонкой «Ответственный» или добавьте вручную.</p>';
        el('regRespHint').textContent = s.responsibles.length
            ? 'Строки остальных ответственных не загружаются. Изменение применится при следующей загрузке реестра.'
            : 'Не задано — загружаются строки всех ответственных, и накладные кондитерки смешаются с нашими.';
        el('regBuyers').innerHTML = s.buyers.map((b) =>
            `<div class="reg-buyer"><span>${esc(b.name)}</span><span class="${b.name_1c ? 'mono-cell' : 'reg-skip'}">${b.name_1c ? esc(b.name_1c) : 'не задано'}</span></div>`).join('')
            + '<p class="field-hint">Меняется в <a href="/static/entities.html">«Юрлицах»</a> → покупатель → «Наименование в 1С».</p>';
        const last = s.last;
        el('regLast').innerHTML = !last ? '<p class="field-hint">Реестр ещё не загружали.</p>' : `
            <p class="field-hint">${esc(last.label)} · загружено ${last.at ? `${Hub.fmtDate(last.at.slice(0, 10))} в ${last.at.slice(11, 16)}` : ''} · строк ${last.kept} из ${last.total}${last.with_responsible ? '' : ' · в файле нет колонки «Ответственный»'}</p>
            <div class="table-wrapper reg-table"><table>
                <thead><tr><th>Контрагент</th><th>Ответственный</th><th>Строк</th><th>Куда</th></tr></thead>
                <tbody>${last.groups.map((g) => `<tr class="${g.skip ? 'reg-row-skip' : ''}">
                    <td>${esc(g.party || '—')}</td><td>${esc(g.responsible || '—')}</td><td class="mono-cell">${g.rows}</td>
                    <td>${g.skip ? `<span class="reg-skip">пропущено: ${esc(g.skip)}</span>` : `<span class="badge badge-green">${esc(g.to)}</span>`}</td>
                </tr>`).join('')}</tbody>
            </table></div>`;
    }

    async function save(names) {
        show(await Hub.api('/api/v1/saby/registry-settings', { method: 'PUT', body: { responsibles: names } }));
    }

    list.addEventListener('change', async () => {
        const names = [...list.querySelectorAll('input:checked')].map((i) => i.value);
        list.querySelectorAll('input').forEach((i) => { i.disabled = true; });
        try {
            await save(names);
            Hub.toast('Ответственные сохранены');
        } catch (err) {
            Hub.toast(err.message, 'error');
            show(current);
        }
    });

    el('regAddForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = el('regAddName').value.trim();
        if (!name) return;
        try {
            await save([...current.responsibles, name]);
            el('regAddName').value = '';
            Hub.toast('Ответственный добавлен');
        } catch (err) { Hub.toast(err.message, 'error'); }
    });

    Hub.api('/api/v1/saby/registry-settings').then(show)
        .catch((err) => { list.innerHTML = `<p class="field-hint">${esc(err.message)}</p>`; });
});
