// Админ-панель: ящик Яндекс.Почты, куда Лавка присылает заказы и отчёты
document.addEventListener('DOMContentLoaded', () => {
    const { api, toast, confirmDialog } = window.Hub;

    const form = document.getElementById('mailForm');
    if (!form) return;
    const username = document.getElementById('mailUsername');
    const password = document.getElementById('mailPassword');
    const passwordHint = document.getElementById('mailPasswordHint');
    const clearBtn = document.getElementById('mailClearPassword');
    const host = document.getElementById('mailHost');
    const port = document.getElementById('mailPort');
    const folder = document.getElementById('mailFolder');
    const statusEl = document.getElementById('mailStatus');
    const checkBtn = document.getElementById('mailCheckBtn');
    const saveBtn = document.getElementById('mailSaveBtn');

    let saved = null; // настройки с сервера

    function formatDateTime(iso) {
        if (!iso) return '';
        // Сервер хранит время в UTC без указания пояса
        return new Date(`${iso}Z`).toLocaleString('ru-RU', {
            timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
        });
    }

    function showStatus(ok, text) {
        statusEl.hidden = !text;
        statusEl.textContent = text || '';
        statusEl.classList.toggle('mail-status-ok', ok === true);
        statusEl.classList.toggle('mail-status-error', ok === false);
    }

    function showSaved(settings) {
        saved = settings;
        username.value = settings.username;
        host.value = settings.host;
        port.value = settings.port;
        folder.value = settings.folder;
        password.value = '';
        clearBtn.hidden = !settings.password_set && !settings.password_unreadable;
        if (settings.password_unreadable) {
            password.placeholder = 'Введите заново';
            passwordHint.textContent = 'Сохранённый пароль не читается: база перенесена с другого стенда или сменился ключ. Введите его снова.';
        } else if (settings.password_set) {
            password.placeholder = '•••••••• сохранён';
            passwordHint.textContent = 'Оставьте пустым, чтобы не менять.';
        } else {
            password.placeholder = 'Пароль приложения';
            passwordHint.textContent = 'Обычный пароль от почты не подойдёт. Нужен пароль приложения: Яндекс ID → Безопасность → Пароли приложений → Почта.';
        }
        if (settings.checked_at) {
            showStatus(settings.check_ok, `${settings.check_message} · проверено ${formatDateTime(settings.checked_at)}`);
        } else {
            showStatus(null, settings.password_set ? 'Подключение ещё не проверялось' : '');
        }
    }

    function formBody(extra = {}) {
        return {
            host: host.value.trim(),
            port: parseInt(port.value, 10),
            username: username.value.trim(),
            folder: folder.value.trim(),
            password: password.value || null,
            ...extra,
        };
    }

    function validate() {
        if (!username.value.trim()) return 'Укажите адрес ящика';
        if (!host.value.trim()) return 'Укажите сервер IMAP';
        const portValue = parseInt(port.value, 10);
        if (!(portValue >= 1 && portValue <= 65535)) return 'Порт — число от 1 до 65535';
        if (!folder.value.trim()) return 'Укажите папку';
        return '';
    }

    async function load() {
        try {
            showSaved(await api('/api/v1/mail/settings'));
        } catch (err) {
            showStatus(false, err.message);
        }
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const error = validate();
        if (error) { showStatus(false, error); return; }
        saveBtn.disabled = true;
        try {
            showSaved(await api('/api/v1/mail/settings', { method: 'PUT', body: formBody() }));
            toast('Настройки почты сохранены');
        } catch (err) {
            showStatus(false, err.message);
        } finally {
            saveBtn.disabled = false;
        }
    });

    checkBtn.addEventListener('click', async () => {
        const error = validate();
        if (error) { showStatus(false, error); return; }
        checkBtn.disabled = true;
        const label = checkBtn.textContent;
        checkBtn.textContent = 'Проверяю…';
        showStatus(null, 'Подключаюсь к ящику…');
        try {
            const result = await api('/api/v1/mail/check', { method: 'POST', body: formBody() });
            showStatus(result.ok, result.saved ? result.message : `${result.message}. Не забудьте сохранить`);
        } catch (err) {
            showStatus(false, err.message);
        } finally {
            checkBtn.disabled = false;
            checkBtn.textContent = label;
        }
    });

    clearBtn.addEventListener('click', async () => {
        const ok = await confirmDialog({
            title: 'Удалить пароль?',
            text: 'Приложение перестанет читать почту, пока пароль не введут снова.',
            okText: 'Удалить',
        });
        if (!ok) return;
        try {
            showSaved(await api('/api/v1/mail/settings', {
                method: 'PUT',
                body: { ...formBody(), password: null, clear_password: true, username: saved.username, host: saved.host, port: saved.port, folder: saved.folder },
            }));
            toast('Пароль удалён');
        } catch (err) {
            showStatus(false, err.message);
        }
    });

    load();

    // --- Опрос почты по расписанию ---
    const pollEnabled = document.getElementById('pollEnabled');
    const pollModal = document.getElementById('pollSettingsModal');
    const pollForm = document.getElementById('pollSettingsForm');
    const pollDays = document.getElementById('pollDays');
    const pollError = document.getElementById('pollSettingsError');
    const historyBtn = document.getElementById('historyBtn');
    let poll = null; // статус с сервера

    pollDays.innerHTML = Hub.WEEKDAYS.map((name, day) =>
        `<button type="button" class="wd-btn${day >= 5 ? ' wd-wknd' : ''}" data-day="${day}" aria-pressed="false">${name}</button>`
    ).join('');

    function setPollDays(days) {
        pollDays.querySelectorAll('.wd-btn').forEach((btn) => {
            const on = days.includes(Number(btn.dataset.day));
            btn.classList.toggle('on', on);
            btn.setAttribute('aria-pressed', on);
        });
    }

    function showPoll(status) {
        poll = status;
        const auto = status.auto;
        pollEnabled.checked = auto.enabled;
        document.getElementById('pollState').textContent = auto.enabled ? 'Включён' : 'Выключен';
        const summary = document.getElementById('pollSummary');
        summary.textContent = auto.summary;
        summary.classList.toggle('sync-auto-off', !auto.enabled);
        const last = document.getElementById('pollLast');
        if (status.history.running) {
            last.textContent = `Загружается история: порций ${status.history.done}${status.history.remaining ? `, осталось писем ${status.history.remaining}` : ''}…`;
        } else if (status.last_poll_at) {
            const [day, time] = status.last_poll_at.split('T');
            last.textContent = `Последняя проверка ${Hub.fmtDate(day)} в ${time.slice(0, 5)}: ${status.last_poll_result || '—'}`
                + (status.last_poll_error ? `. Ошибка: ${status.last_poll_error}` : '');
        } else {
            last.textContent = 'Почту ещё не проверяли.';
        }
        historyBtn.disabled = status.history.running || !status.configured;
    }

    async function loadPoll() {
        try {
            showPoll(await api('/api/v1/lavka/status'));
        } catch (err) {
            document.getElementById('pollLast').textContent = err.message;
        }
    }

    async function savePoll(values) {
        showPoll(await api('/api/v1/lavka/schedule', { method: 'PUT', body: values }));
    }

    function pollValues(overrides = {}) {
        return {
            enabled: poll.auto.enabled,
            interval_minutes: poll.auto.interval_minutes,
            start_time: poll.auto.start_time,
            end_time: poll.auto.end_time,
            weekdays: poll.auto.weekdays,
            ...overrides,
        };
    }

    pollEnabled.addEventListener('change', async () => {
        const enabled = pollEnabled.checked;
        pollEnabled.disabled = true;
        try {
            await savePoll(pollValues({ enabled }));
            toast(enabled ? 'Опрос почты включён' : 'Опрос почты выключен');
        } catch (err) {
            pollEnabled.checked = !enabled;
            toast(err.message, 'error');
        } finally {
            pollEnabled.disabled = false;
        }
    });

    document.getElementById('pollConfigureBtn').addEventListener('click', () => {
        const auto = poll.auto;
        setPollDays(auto.weekdays);
        const interval = document.getElementById('pollInterval');
        if (![...interval.options].some(o => o.value === String(auto.interval_minutes))) {
            interval.add(new Option(`${auto.interval_minutes} минут`, auto.interval_minutes));
        }
        interval.value = String(auto.interval_minutes);
        document.getElementById('pollStart').value = auto.start_time;
        document.getElementById('pollEnd').value = auto.end_time;
        document.getElementById('pollSenders').value = poll.senders.join(', ');
        pollError.textContent = '';
        Hub.openModal(pollModal);
    });

    document.getElementById('pollSettingsCancel').addEventListener('click', () => Hub.closeModal(pollModal));

    pollDays.addEventListener('click', (e) => {
        const btn = e.target.closest('.wd-btn');
        if (!btn) return;
        btn.classList.toggle('on');
        btn.setAttribute('aria-pressed', btn.classList.contains('on'));
    });

    pollModal.querySelector('.poll-day-presets').addEventListener('click', (e) => {
        const chip = e.target.closest('[data-days]');
        if (chip) setPollDays(chip.dataset.days.split(',').map(Number));
    });

    pollForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const weekdays = [...pollDays.querySelectorAll('.wd-btn.on')].map(btn => Number(btn.dataset.day));
        if (!weekdays.length) { pollError.textContent = 'Выберите хотя бы один день'; return; }
        pollError.textContent = '';
        try {
            await savePoll(pollValues({
                interval_minutes: Number(document.getElementById('pollInterval').value),
                start_time: document.getElementById('pollStart').value,
                end_time: document.getElementById('pollEnd').value,
                weekdays,
                senders: document.getElementById('pollSenders').value,
            }));
            Hub.closeModal(pollModal);
            toast('Расписание опроса сохранено');
        } catch (err) {
            pollError.textContent = err.message;
        }
    });

    historyBtn.addEventListener('click', async () => {
        const ok = await confirmDialog({
            title: 'Загрузить историю почты?',
            text: 'Приложение перечитает весь ящик с начала и загрузит заказы Лавки, которых ещё нет. Это может занять несколько минут. Уже загруженное не задвоится.',
            okText: 'Загрузить',
            danger: false,
        });
        if (!ok) return;
        try {
            await api('/api/v1/lavka/history', { method: 'POST' });
            toast('Загрузка истории началась');
            loadPoll();
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    loadPoll();
    // Пока идёт история, показываем ход
    setInterval(() => { if (!document.hidden && poll && poll.history.running) loadPoll(); }, 4000);
});
