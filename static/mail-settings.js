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
});
