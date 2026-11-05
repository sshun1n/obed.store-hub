/**
 * Плашка синхронизации с Google Таблицей: время обновления и кнопка.
 * Вставляется в элемент #syncBar. Когда данные обновились (по кнопке или по
 * расписанию на сервере), шлёт событие hub:synced, и страница перечитывает свои данные.
 */
(function () {
    const POLL_MS = 60000;

    function formatSyncedAt(iso) {
        if (!iso) return 'Ещё не обновлялось';
        const d = new Date(iso);
        const pad = (n) => String(n).padStart(2, '0');
        const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
        const today = new Date();
        const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
        if (d.toDateString() === today.toDateString()) return `Обновлено сегодня в ${time}`;
        if (d.toDateString() === yesterday.toDateString()) return `Обновлено вчера в ${time}`;
        return `Обновлено ${pad(d.getDate())}.${pad(d.getMonth() + 1)} в ${time}`;
    }

    function autoHint(auto) {
        if (!auto.enabled) return 'Автосинхронизация выключена';
        return `Автосинхронизация: каждые ${auto.interval_minutes} мин, ${auto.start_time}–${auto.end_time}`;
    }

    function mount(bar) {
        bar.innerHTML = `
            <span class="sync-text">
                <span class="sync-time"></span>
                <span class="sync-error"></span>
            </span>
            <button type="button" class="btn btn-secondary btn-sm sync-btn">Синхронизировать</button>`;
        const timeEl = bar.querySelector('.sync-time');
        const errorEl = bar.querySelector('.sync-error');
        const button = bar.querySelector('.sync-btn');
        let lastSyncedAt;
        let syncing = false;

        function show(status) {
            timeEl.textContent = status.configured ? formatSyncedAt(status.synced_at) : 'Таблица не подключена';
            timeEl.title = autoHint(status.auto);
            errorEl.textContent = status.last_error ? 'Не удалось обновить из таблицы' : '';
            errorEl.title = status.last_error || '';
        }

        // Первый ответ только запоминаем: страница и так загружает свои данные при открытии
        function notifyIfChanged(status, added = 0) {
            const changed = lastSyncedAt !== undefined && status.synced_at !== lastSyncedAt;
            lastSyncedAt = status.synced_at;
            if (changed) document.dispatchEvent(new CustomEvent('hub:synced', { detail: { added } }));
        }

        async function refreshStatus() {
            if (syncing || document.hidden) return;
            try {
                const response = await fetch('/api/v1/sync/status');
                if (!response.ok) return;
                const status = await response.json();
                show(status);
                notifyIfChanged(status);
            } catch { /* сеть пропала: покажем при следующей проверке */ }
        }

        button.addEventListener('click', async () => {
            syncing = true;
            button.disabled = true;
            button.textContent = 'Синхронизация…';
            errorEl.textContent = '';
            try {
                const response = await fetch('/api/v1/sync', { method: 'POST' });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Ошибка синхронизации');
                show(result);
                notifyIfChanged(result, result.added);
            } catch (err) {
                errorEl.textContent = err.message;
            } finally {
                syncing = false;
                button.disabled = false;
                button.textContent = 'Синхронизировать';
            }
        });

        refreshStatus();
        setInterval(refreshStatus, POLL_MS);
        document.addEventListener('visibilitychange', refreshStatus);
    }

    document.addEventListener('DOMContentLoaded', () => {
        const bar = document.getElementById('syncBar');
        if (bar) mount(bar);
    });
})();
