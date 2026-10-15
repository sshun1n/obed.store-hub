/**
 * Общие хелперы интерфейса: форматирование, запросы к API, уведомления, диалоги.
 */
window.Hub = (() => {
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const pad = (n) => String(n).padStart(2, '0');
    const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const parseIso = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
    const fmtDate = (iso) => { if (!iso) return '—'; const [y, m, d] = iso.split('-'); return `${d}.${m}.${y}`; };
    const fmtHM = (min) => { if (!min) return '0:00'; return `${Math.floor(min / 60)}:${pad(min % 60)}`; };
    const fmtDur = (min) => {
        if (!min) return '0 ч';
        const h = Math.floor(min / 60), m = min % 60;
        return m ? `${h} ч ${m} мин` : `${h} ч`;
    };
    const fmtMoney = (v) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(v || 0) + ' ₽';
    const label = (e) => e.display_name || e.full_name;

    const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
    const ABSENCE = { vacation: 'Отпуск', sick: 'Больничный', dayoff: 'Отгул' };
    const PUNCH = {
        no_exit: 'Нет отметки ухода — время досчитано системой до полуночи',
        no_entry: 'Нет отметки прихода — продолжение незакрытой смены с прошлого дня',
        no_marks: 'Нет отметок за день — система засчитала 24 часа',
    };
    const STATUS = {
        work: 'Отработано', extra: 'Вне графика', absent: 'Прогул', off: 'Выходной',
        planned: 'По графику', vacation: 'Отпуск', sick: 'Больничный', dayoff: 'Отгул', none: '—',
    };

    async function api(url, { method = 'GET', body } = {}) {
        const opts = { method, headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const r = await fetch(url, opts);
        let data = null;
        try { data = await r.json(); } catch { /* пустой ответ */ }
        if (r.status === 401) { window.location.href = '/login'; throw new Error('Требуется вход'); }
        if (!r.ok) {
            const detail = data && data.detail;
            const msg = typeof detail === 'string' ? detail
                : Array.isArray(detail) ? 'Проверьте введённые значения — некоторые поля заполнены некорректно'
                : 'Ошибка запроса';
            throw new Error(msg);
        }
        return data;
    }

    async function requireAdmin(contentEl, deniedEl) {
        const user = await api('/api/v1/users/me');
        if (user.role !== 'admin') {
            if (deniedEl) deniedEl.style.display = 'block';
            return false;
        }
        if (contentEl) contentEl.style.display = '';
        return true;
    }

    // --- Уведомления ---

    let toastHost = null;
    function toast(message, kind = 'ok') {
        if (!toastHost) {
            toastHost = document.createElement('div');
            toastHost.className = 'toast-host';
            document.body.appendChild(toastHost);
        }
        const t = document.createElement('div');
        t.className = `toast toast-${kind}`;
        t.textContent = message;
        toastHost.appendChild(t);
        requestAnimationFrame(() => t.classList.add('show'));
        setTimeout(() => {
            t.classList.remove('show');
            setTimeout(() => t.remove(), 250);
        }, kind === 'error' ? 4500 : 2200);
    }

    // --- Модальные окна ---

    function openModal(modal) {
        modal.classList.add('open');
        const first = modal.querySelector('input:not([type=hidden]):not([disabled]), select, textarea');
        if (first) setTimeout(() => first.focus(), 30);
    }

    function closeModal(modal) {
        modal.classList.remove('open');
    }

    document.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Escape') return;
        const open = [...document.querySelectorAll('.modal.open')].pop();
        if (open) closeModal(open);
    });

    document.addEventListener('mousedown', (ev) => {
        if (ev.target.classList && ev.target.classList.contains('modal')) closeModal(ev.target);
    });

    function confirmDialog({ title, text, okText = 'Удалить', danger = true }) {
        return new Promise((resolve) => {
            const modal = document.createElement('div');
            modal.className = 'modal open';
            modal.innerHTML = `
                <div class="modal-content confirm-modal">
                    <h2 class="card-title-small">${esc(title)}</h2>
                    <p class="confirm-text">${esc(text)}</p>
                    <div class="modal-actions">
                        <button type="button" class="btn btn-secondary" data-act="no">Отмена</button>
                        <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="yes">${esc(okText)}</button>
                    </div>
                </div>`;
            const finish = (val) => { modal.remove(); document.removeEventListener('keydown', onKey); resolve(val); };
            const onKey = (ev) => { if (ev.key === 'Escape') finish(false); };
            modal.addEventListener('click', (ev) => {
                if (ev.target === modal) return finish(false);
                const act = ev.target.closest('[data-act]')?.dataset.act;
                if (act) finish(act === 'yes');
            });
            document.addEventListener('keydown', onKey);
            document.body.appendChild(modal);
            modal.querySelector('[data-act="yes"]').focus();
        });
    }

    return {
        esc, pad, isoDate, parseIso, fmtDate, fmtHM, fmtDur, fmtMoney, label,
        WEEKDAYS, MONTHS, ABSENCE, STATUS, PUNCH,
        api, requireAdmin, toast, openModal, closeModal, confirmDialog,
    };
})();
