// Самокат → СБИС: проверка УПД и черновики реализаций за выбранный день. Только админ, ничего не отправляет.
// День берётся из адреса страницы: его пишет lavka.js при выборе периода.
document.addEventListener('DOMContentLoaded', async () => {
    const { esc, api, toast } = Hub;
    const el = (id) => document.getElementById(id);
    const panel = el('sabyPanel');
    const state = el('sabyState');
    const out = el('sabyResult');
    const buttons = [el('sabyCheck'), el('sabyCreate')];

    try {
        const user = await api('/api/v1/users/me');
        if (user.role !== 'admin') return;
    } catch { return; }
    panel.hidden = false;

    function chosenDay() {
        const q = new URLSearchParams(location.search);
        return q.get('period') === 'day' ? q.get('start') : null;
    }

    function orderNo(label) {
        const m = /УР-\d{8}/.exec(label || '');
        return m ? m[0] : '';
    }

    function render(p) {
        const r = p.result;
        if (!r) { out.innerHTML = ''; return; }
        const parts = [];
        const list = (title, items, cls, withFile) => items.length ? `
            <div class="lavka-notice ${cls}"><b>${title}: ${items.length}</b>
                <ul class="samokat-list">${items.map(x => `<li>${esc(x)}${withFile && orderNo(x)
                    ? ` · <a href="/api/v1/samokat/shipments/preview?order_no=${encodeURIComponent(orderNo(x))}">УПД</a>` : ''}</li>`).join('')}</ul>
            </div>` : '';
        parts.push(list(r.dry_run ? 'Собрано без создания' : 'Создано черновиков', r.done, 'lavka-notice-info', r.dry_run));
        const errors = Object.entries(r.errors || {}).map(([k, v]) => `${k}: ${v}`);
        parts.push(list('Ошибки', errors, '', false));
        parts.push(list('Пропущено, реализация уже есть', r.skipped || [], 'lavka-notice-info', false));
        parts.push(list('Заметки', r.notes || [], 'lavka-notice-info', false));
        out.innerHTML = parts.join('');
    }

    function show(p) {
        const busy = p.running;
        buttons.forEach(b => { b.disabled = busy; });
        if (busy) {
            state.textContent = `${p.label}: ${p.step}${p.total ? ` (${p.done} из ${p.total})` : ''}`;
        } else if (p.error) {
            state.innerHTML = `<span class="ts-warn">${esc(p.error)}</span>`;
        } else if (p.finished_at) {
            state.textContent = `${p.label} за ${p.day}: готово`;
        } else {
            state.textContent = '';
        }
        render(p);
    }

    async function poll() {
        try {
            const p = await api('/api/v1/samokat/shipments/status');
            show(p);
            if (p.running) setTimeout(poll, 1500);
            else if (p.result && !p.result.dry_run) document.dispatchEvent(new Event('samokat:changed'));
        } catch (err) {
            state.textContent = err.message;
        }
    }

    async function run(dryRun) {
        const day = chosenDay();
        if (!day) { toast('Выберите один день поставки', 'error'); return; }
        if (!dryRun) {
            const ok = await Hub.confirmDialog({
                title: 'Создать черновики в СБИС?',
                text: `По заказам Самоката на ${day.split('-').reverse().join('.')} в кабинете появятся реализации с УПД — черновиками. `
                    + 'Ничего не отправляется: перед отправкой их нужно проверить и подписать. Номера — из реестра 1С за этот день.',
                okText: 'Создать',
                danger: false,
            });
            if (!ok) return;
        }
        try {
            await api('/api/v1/samokat/shipments', { method: 'POST', body: { day, dry_run: dryRun } });
            poll();
        } catch (err) {
            toast(err.message, 'error');
        }
    }

    el('sabyCheck').addEventListener('click', () => run(true));
    el('sabyCreate').addEventListener('click', () => run(false));
    poll();
});
