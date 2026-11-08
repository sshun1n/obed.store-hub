// Общее для страниц «Перевозчики» и «Юрлица»: форма с выбором формы собственности
window.Parties = (() => {
    // Поля формы по атрибуту name. Пустая строка — null, как ждёт сервер
    function readForm(form) {
        const data = {};
        for (const field of form.elements) {
            if (!field.name || field.disabled || field.closest('[hidden]')) continue;
            data[field.name] = field.value.trim() || null;
        }
        return data;
    }

    function fillForm(form, item) {
        for (const field of form.elements) {
            if (!field.name) continue;
            const value = item[field.name];
            if (field.tagName === 'SELECT') {
                if (value != null) field.value = value;
                else field.selectedIndex = 0;
            } else {
                field.value = value == null ? '' : String(value).replace(/^(\d+)\.(\d+)$/, '$1,$2');
            }
        }
    }

    // Запятая допустима. Пусто — null, не число — NaN
    function parseNumber(value) {
        const text = String(value ?? '').trim().replace(',', '.');
        if (!text) return null;
        const number = Number(text);
        return Number.isFinite(number) ? number : NaN;
    }

    function formatNumber(value) {
        return value == null ? '' : String(value).replace('.', ',');
    }

    // Поля с data-only="ЮЛ" видны только у этой формы, с data-hide="ЮЛ" — у всех, кроме неё
    function applyKind(form, seg, kind) {
        seg.querySelectorAll('.seg-btn').forEach((btn) => {
            const on = btn.dataset.kind === kind;
            btn.classList.toggle('active', on);
            btn.setAttribute('aria-checked', on);
        });
        form.querySelectorAll('[data-only]').forEach((el) => { el.hidden = !el.dataset.only.split(' ').includes(kind); });
        form.querySelectorAll('[data-hide]').forEach((el) => { el.hidden = el.dataset.hide.split(' ').includes(kind); });
    }

    function currentKind(seg) {
        return seg.querySelector('.seg-btn.active')?.dataset.kind;
    }

    // Срок прав: истёк, скоро истечёт или в порядке
    function expiryState(isoDate, warnDays = 30) {
        if (!isoDate) return null;
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const days = Math.round((Hub.parseIso(isoDate) - today) / 86400000);
        if (days < 0) return 'expired';
        if (days <= warnDays) return 'soon';
        return 'ok';
    }

    return { readForm, fillForm, parseNumber, formatNumber, applyKind, currentKind, expiryState };
})();
