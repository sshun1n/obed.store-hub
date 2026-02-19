async function loadComponents() {
    // 0. Проверка авторизации (пропускаем на странице логина)
    const isLoginPage = window.location.pathname.includes('login.html');
    let currentUser = null;

    if (!isLoginPage) {
        try {
            const response = await fetch('/api/v1/users/me');
            if (response.status === 401) {
                window.location.href = 'login.html';
                return;
            }
            if (response.ok) {
                currentUser = await response.json();
            }
        } catch (err) {
            console.error('Auth check failed:', err);
        }
    }

    // 1. Шапка (Navbar)
    const navbarHTML = `
    <nav class="navbar">
        <div class="navbar-container">
            <a href="index.html" class="brand-logo">
                obed.store <span>hub</span>
            </a>
            <div class="nav-links" style="display: flex; align-items: center; gap: 20px;">
                ${currentUser && currentUser.role === 'admin' 
                    ? `<a href="admin.html" class="nav-link" style="color: var(--brand-yellow);">⚙️ Админ-панель</a>` 
                    : ''
                }
                ${window.location.pathname.includes('index.html') || window.location.pathname === '/' || window.location.pathname === '/static/' || window.location.pathname.endsWith('/')
                    ? `<span style="font-size: 0.9rem; font-weight: 600; color: white;">${currentUser ? currentUser.full_name : 'Пользователь'}</span>`
                    : '<a href="index.html" class="nav-link">← Вернуться к сервисам</a>'
                }
                ${currentUser ? '<button id="logoutBtn" class="nav-link" style="background: none; border: none; cursor: pointer; font-family: inherit;">Выйти</button>' : ''}
            </div>
        </div>
    </nav>
    `;

    // 2. Футер
    const footerHTML = `
    <footer>
        © 2026 obed.store Internal System
    </footer>
    `;

    // Вставляем шапку в начало body
    document.body.insertAdjacentHTML('afterbegin', navbarHTML);

    // Вставляем футер в конец body
    document.body.insertAdjacentHTML('beforeend', footerHTML);

    // Обработчик кнопки выхода
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            await fetch('/api/v1/auth/logout', { method: 'POST' });
            window.location.reload();
        });
    }
}

// Запускаем сразу, как только DOM загрузится
document.addEventListener('DOMContentLoaded', loadComponents);
