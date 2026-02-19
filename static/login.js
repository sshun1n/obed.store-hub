document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('loginForm');
    const errorBox = document.getElementById('errorBox');

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorBox.innerText = '';

        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;

        // FastAPI OAuth2 эндпоинт ожидает данные в формате Form Data
        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);

        try {
            const response = await fetch('/api/v1/auth/login', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Ошибка авторизации');
            }

            const data = await response.json();
            // Перенаправляем на главную (сервер сам решит, куда пустить)
            window.location.href = '/';

        } catch (err) {
            console.error('Login error:', err);
            errorBox.innerText = err.message;
        }
    });
});
