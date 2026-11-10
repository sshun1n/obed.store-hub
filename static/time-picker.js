/**
 * Выбор времени вместо системного <input type="time">: всегда 24 часа, без AM/PM,
 * в стиле приложения. Подключается одной строкой и сам находит все поля времени.
 *
 * Поле остаётся тем же элементом с теми же id и name, только становится текстовым:
 * .value — строка «ЧЧ:ММ» или пустая, события input и change приходят как раньше.
 * Время можно набрать с клавиатуры («730», «7:3», «19»), а по нажатию открывается
 * панель: сетка часов и сетка минут через пять.
 */
window.TimePicker = (() => {
    const pad = (n) => String(n).padStart(2, '0');
    const VALID = /^([01]\d|2[0-3]):[0-5]\d$/;
    const CLOCK = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 4.6V8l2.4 1.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
    let pop = null;       // одна панель на страницу
    let current = null;   // поле, для которого она открыта

    // «7» → 07:00, «730» → 07:30, «7:5» → 07:05, «1930» → 19:30. Не время — null
    function normalize(text) {
        const raw = String(text || '').trim();
        if (!raw) return '';
        let h, m;
        const parts = raw.split(/[:.\s]/).filter(Boolean);
        if (parts.length >= 2) { h = parts[0]; m = parts[1]; }
        else {
            const digits = raw.replace(/\D/g, '');
            if (!digits || digits.length > 4) return null;
            if (digits.length <= 2) { h = digits; m = '0'; }
            else { h = digits.slice(0, digits.length - 2); m = digits.slice(-2); }
        }
        if (!/^\d{1,2}$/.test(h) || !/^\d{1,2}$/.test(m)) return null;
        const hh = Number(h), mm = Number(m);
        if (hh > 23 || mm > 59) return null;
        return `${pad(hh)}:${pad(mm)}`;
    }

    function set(input, value) {
        if (input.value === value) return;
        input.value = value;
        input.dataset.tpLast = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function render() {
        const value = current.value && VALID.test(current.value) ? current.value : '';
        const [h, m] = value ? value.split(':').map(Number) : [null, null];
        const hours = Array.from({ length: 24 }, (_, i) =>
            `<button type="button" class="tp-cell${i === h ? ' on' : ''}" data-h="${i}">${pad(i)}</button>`).join('');
        const minutes = Array.from({ length: 12 }, (_, i) => i * 5).map(mm =>
            `<button type="button" class="tp-cell${mm === m ? ' on' : ''}" data-m="${mm}">${pad(mm)}</button>`).join('');
        pop.innerHTML = `
            <div class="tp-col"><span class="tp-caption">Часы</span><div class="tp-grid tp-hours">${hours}</div></div>
            <div class="tp-col"><span class="tp-caption">Минуты</span><div class="tp-grid tp-minutes">${minutes}</div></div>`;
    }

    // Панель — у самого поля, но в body и с position: fixed: внутри окна с прокруткой
    // она иначе обрезалась бы краем окна
    function place() {
        const r = current.getBoundingClientRect();
        const w = pop.offsetWidth, hgt = pop.offsetHeight;
        let left = Math.min(r.left, window.innerWidth - w - 8);
        let top = r.bottom + 6;
        if (top + hgt > window.innerHeight - 8) top = Math.max(8, r.top - hgt - 6);
        pop.style.left = `${Math.max(8, left)}px`;
        pop.style.top = `${top}px`;
    }

    function open(input) {
        if (input.disabled || input.readOnly) return;
        if (!pop) {
            pop = document.createElement('div');
            pop.className = 'tp-pop';
            pop.setAttribute('role', 'dialog');
            pop.setAttribute('aria-label', 'Выбор времени');
            // mousedown не должен уводить фокус из поля и закрывать панель
            pop.addEventListener('mousedown', (e) => e.preventDefault());
            pop.addEventListener('click', onPick);
            document.body.appendChild(pop);
        }
        current = input;
        render();
        pop.hidden = false;
        place();
        requestAnimationFrame(() => pop.classList.add('open'));
        input.closest('.tp-wrap')?.classList.add('tp-active');
    }

    function close() {
        if (!pop || !current) return;
        pop.classList.remove('open');
        current.closest('.tp-wrap')?.classList.remove('tp-active');
        current = null;
        setTimeout(() => { if (pop && !current) pop.hidden = true; }, 140);
    }

    function onPick(e) {
        const cell = e.target.closest('.tp-cell');
        if (!cell || !current) return;
        const value = normalize(current.value) || '';
        let [h, m] = value ? value.split(':').map(Number) : [null, 0];
        if (cell.dataset.h !== undefined) {
            h = Number(cell.dataset.h);
            set(current, `${pad(h)}:${pad(m || 0)}`);
            render();
            return;   // час выбран — ждём минуты
        }
        m = Number(cell.dataset.m);
        set(current, `${pad(h ?? 0)}:${pad(m)}`);
        close();
    }

    function enhance(input) {
        if (input.dataset.tpReady) return;
        input.dataset.tpReady = '1';
        const value = input.value;
        input.type = 'text';
        input.value = value;
        input.inputMode = 'numeric';
        input.autocomplete = 'off';
        input.maxLength = 5;
        input.placeholder = input.placeholder || 'ЧЧ:ММ';
        input.pattern = '([01]\\d|2[0-3]):[0-5]\\d';
        input.title = input.title || 'Время в формате ЧЧ:ММ, 24 часа';
        input.classList.add('tp-input');
        input.dataset.tpLast = value;

        const wrap = document.createElement('span');
        wrap.className = 'tp-wrap';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        const icon = document.createElement('button');
        icon.type = 'button';
        icon.className = 'tp-icon';
        icon.tabIndex = -1;
        icon.setAttribute('aria-label', 'Выбрать время');
        icon.innerHTML = CLOCK;
        wrap.appendChild(icon);

        icon.addEventListener('mousedown', (e) => e.preventDefault());
        icon.addEventListener('click', () => { input.focus(); current === input ? close() : open(input); });
        input.addEventListener('click', () => { if (current !== input) open(input); });
        input.addEventListener('focus', () => {
            // Значение могла поставить страница: его и считаем последним верным
            input.dataset.tpLast = VALID.test(input.value) ? input.value : (input.value ? input.dataset.tpLast : '');
            open(input);
        });
        // Набор с клавиатуры: только цифры и двоеточие, двоеточие подставляется само
        input.addEventListener('input', (e) => {
            if (!e.isTrusted) return;
            let v = input.value.replace(/[^\d:]/g, '');
            if (/^\d{3,4}$/.test(v)) v = `${v.slice(0, 2)}:${v.slice(2)}`;
            if (v !== input.value) input.value = v;
            if (current === input) render();
        });
        input.addEventListener('blur', () => {
            const v = normalize(input.value);
            if (v === null) input.value = input.dataset.tpLast || '';   // не время — возвращаем как было
            else if (v !== input.value || v !== input.dataset.tpLast) set(input, v);
            close();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && current === input) {
                e.stopPropagation();   // закрываем панель, а не окно вокруг
                close();
            } else if (e.key === 'Enter' && current === input) {
                const v = normalize(input.value);
                if (v) set(input, v);
                close();
            }
        });
    }

    function enhanceAll(root = document) {
        root.querySelectorAll('input[type="time"]').forEach(enhance);
    }

    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && current) { e.stopImmediatePropagation(); close(); } }, true);
    document.addEventListener('mousedown', (e) => {
        if (current && !e.target.closest('.tp-pop') && !e.target.closest('.tp-wrap')) close();
    });
    window.addEventListener('resize', () => current && place());
    document.addEventListener('scroll', () => current && place(), true);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhanceAll());
    else enhanceAll();

    return { enhance, enhanceAll, normalize };
})();
