document.addEventListener('DOMContentLoaded', () => {
    // Пользователи
    const userListContainer = document.getElementById('userList');
    const addUserForm = document.getElementById('addUserForm');
    
    // Таблицы
    const sheetUrlInput = document.getElementById('sheetUrl');
    const inspectBtn = document.getElementById('inspectBtn');
    const step2 = document.getElementById('step2');
    const serviceEmail = document.getElementById('serviceEmail');
    const worksheetSelect = document.getElementById('worksheetSelect');
    const saveConfigBtn = document.getElementById('saveConfigBtn');
    const googleConfigList = document.getElementById('googleConfigList');

    let currentInspectedId = null;
    let currentInspectedTitle = null;

    // --- 1. УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ---

    async function fetchUsers() {
        const response = await fetch('/api/v1/users/');
        return response.ok ? await response.json() : [];
    }

    async function renderUsers() {
        const users = await fetchUsers();
        userListContainer.innerHTML = users.map(user => `
            <div class="category-item" style="border-bottom: 1px solid #f3f4f6; padding: 10px 8px;">
                <div style="flex: 1;">
                    <strong style="color: var(--brand-black);">${user.login}</strong> 
                    <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 8px;">(${user.role})</span>
                    <div style="font-size: 0.85rem;">${user.full_name || '—'}</div>
                </div>
                <div class="category-actions" style="opacity: 1;">
                    <button class="action-btn btn-edit-user" data-id="${user.id}" data-login="${user.login}" title="Сменить пароль">✎</button>
                    <button class="action-btn btn-delete-user" data-id="${user.id}" ${user.login === 'admin' ? 'disabled' : ''} title="Удалить">[x]</button>
                </div>
            </div>
        `).join('');
    }

    addUserForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const userData = {
            login: document.getElementById('newUserLogin').value,
            password: document.getElementById('newUserPassword').value,
            full_name: document.getElementById('newUserFullName').value,
            role: document.getElementById('newUserRole').value
        };
        const resp = await fetch('/api/v1/users/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(userData)
        });
        if (resp.ok) { addUserForm.reset(); renderUsers(); }
        else { alert((await resp.json()).detail); }
    });

    userListContainer.addEventListener('click', async (e) => {
        const target = e.target;
        const id = target.dataset.id;
        if (!id) return;

        if (target.classList.contains('btn-delete-user')) {
            if (confirm('Удалить пользователя?')) {
                await fetch(`/api/v1/users/${id}`, { method: 'DELETE' });
                renderUsers();
            }
        }
        if (target.classList.contains('btn-edit-user')) {
            const newPass = prompt('Введите новый пароль (оставьте пустым, чтобы не менять):');
            if (newPass) {
                await fetch(`/api/v1/users/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: newPass })
                });
                alert('Пароль обновлен');
            }
        }
    });

    // --- 2. УПРАВЛЕНИЕ ТАБЛИЦАМИ ---

    serviceEmail.addEventListener('click', () => {
        navigator.clipboard.writeText(serviceEmail.innerText);
        const originalText = serviceEmail.innerText;
        serviceEmail.innerText = 'Скопировано!';
        setTimeout(() => serviceEmail.innerText = originalText, 2000);
    });

    inspectBtn.addEventListener('click', async () => {
        const url = sheetUrlInput.value.trim();
        if (!url) return;

        inspectBtn.disabled = true;
        inspectBtn.innerText = 'Проверка...';

        try {
            const response = await fetch('/api/v1/google/inspect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });

            if (!response.ok) {
                throw new Error((await response.json()).detail || 'Ошибка проверки');
            }

            const data = await response.json();
            currentInspectedId = data.spreadsheet_id;
            currentInspectedTitle = data.title;

            worksheetSelect.innerHTML = data.worksheets.map(ws => `<option value="${ws}">${ws}</option>`).join('');
            step2.style.display = 'block';
        } catch (err) {
            alert(err.message);
        } finally {
            inspectBtn.disabled = false;
            inspectBtn.innerText = 'Проверить доступ';
        }
    });

    saveConfigBtn.addEventListener('click', async () => {
        const configData = {
            title: currentInspectedTitle,
            url: sheetUrlInput.value.trim(), // Сохраняем полную ссылку
            spreadsheet_id: currentInspectedId,
            sheet_name: worksheetSelect.value
        };

        const resp = await fetch('/api/v1/google/configs/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData)
        });

        if (resp.ok) {
            const newConfig = await resp.json();
            await fetch(`/api/v1/google/configs/${newConfig.id}/activate`, { method: 'PUT' });
            window.location.reload();
        }
    });

    async function renderConfigs() {
        const resp = await fetch('/api/v1/google/configs/');
        const configs = await resp.json();
        googleConfigList.innerHTML = configs.map(c => `
            <div class="category-item" style="border-bottom: 1px solid #f3f4f6; padding: 10px 0;">
                <div style="flex: 1;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <div style="font-weight: 600;">${c.title}</div>
                        ${c.is_active ? '<span class="status-badge status-active" style="padding:0; margin:0;">Активна</span>' : ''}
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">Лист: ${c.sheet_name}</div>
                </div>
                <div class="category-actions" style="opacity: 1;">
                    ${!c.is_active ? `<button class="action-btn btn-add-child" onclick="activateConfig(${c.id})">Включить</button>` : ''}
                    <button class="action-btn btn-delete-category" onclick="deleteConfig(${c.id})">[x]</button>
                </div>
            </div>
        `).join('');
    }

    window.activateConfig = async (id) => {
        await fetch(`/api/v1/google/configs/${id}/activate`, { method: 'PUT' });
        renderConfigs();
    };

    window.deleteConfig = async (id) => {
        if (confirm('Удалить подключение?')) {
            await fetch(`/api/v1/google/configs/${id}`, { method: 'DELETE' });
            renderConfigs();
        }
    };

    renderUsers();
    renderConfigs();
});
