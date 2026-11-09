/**
 * Выбор дня, недели, месяца или произвольного периода.
 * Кнопка показывает выбранное, по нажатию рядом с ней открывается панель с календарём.
 * Дни, в которые есть данные (например, поставки Лавки), отмечены точкой.
 *
 *   const picker = PeriodPicker.create({ root, button, pop, kindEl, labelEl, prev, next, onChange });
 *   picker.setMarked(['2026-11-10', …]);  picker.set('day', '2026-11-10', '2026-11-10');
 *   kinds: ['day'] — только дни: без вкладок и без быстрых кнопок недель и месяцев.
 */
window.PeriodPicker = (() => {
    const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
    const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
    const MONTHS_SHORT = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
    const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const WD_LOWER = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
    const KINDS = { day: 'День', week: 'Неделя', month: 'Месяц', range: 'Период' };

    const pad = (n) => String(n).padStart(2, '0');
    const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
    const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
    const monday = (s) => { const d = parse(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
    const monthStart = (s) => { const d = parse(s); return iso(new Date(d.getFullYear(), d.getMonth(), 1)); };
    const monthEnd = (s) => { const d = parse(s); return iso(new Date(d.getFullYear(), d.getMonth() + 1, 0)); };
    const todayIso = () => iso(new Date());

    function rangeText(start, end) {
        const a = parse(start), b = parse(end);
        if (start === end) return `${a.getDate()} ${MONTHS_GEN[a.getMonth()]} ${a.getFullYear()}, ${WD_LOWER[a.getDay()]}`;
        if (a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()) {
            return `${a.getDate()}–${b.getDate()} ${MONTHS_GEN[b.getMonth()]} ${b.getFullYear()}`;
        }
        if (a.getFullYear() === b.getFullYear()) {
            return `${a.getDate()} ${MONTHS_GEN[a.getMonth()]} – ${b.getDate()} ${MONTHS_GEN[b.getMonth()]} ${b.getFullYear()}`;
        }
        return `${a.getDate()} ${MONTHS_GEN[a.getMonth()]} ${a.getFullYear()} – ${b.getDate()} ${MONTHS_GEN[b.getMonth()]} ${b.getFullYear()}`;
    }

    function create({ root, button, pop, kindEl, labelEl, prev, next, onChange, kinds = Object.keys(KINDS) }) {
        let kind = 'day';
        let start = todayIso();
        let end = start;
        let marked = [];              // отсортированные даты с данными
        let markedSet = new Set();
        let draftKind = 'day';        // вкладка в открытой панели
        let view = parse(start);      // какой месяц (или год) показывает календарь
        let rangeFirst = null;        // первая точка произвольного периода

        function label() {
            kindEl.textContent = KINDS[kind];
            if (kind === 'month') {
                const d = parse(start);
                labelEl.textContent = `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
            } else {
                labelEl.textContent = rangeText(start, end);
            }
        }

        function apply(newKind, s, e, silent = false) {
            kind = newKind;
            start = s <= e ? s : e;
            end = s <= e ? e : s;
            label();
            if (!silent) onChange({ kind, start, end });
        }

        // --- Панель ---

        function dayCell(d, inMonth) {
            const s = iso(d);
            const classes = ['pp-day'];
            if (!inMonth) classes.push('pp-out');
            if (markedSet.has(s)) classes.push('pp-has');
            if (s === todayIso()) classes.push('pp-today');
            const selStart = rangeFirst || start;
            const selEnd = rangeFirst || end;
            if (draftKind === kind || rangeFirst) {
                if (s >= selStart && s <= selEnd) classes.push('pp-in');
                if (s === selStart) classes.push('pp-start');
                if (s === selEnd) classes.push('pp-end');
            }
            return `<button type="button" class="${classes.join(' ')}" data-date="${s}">${d.getDate()}</button>`;
        }

        function renderCalendar() {
            const year = view.getFullYear(), month = view.getMonth();
            const first = new Date(year, month, 1);
            const lead = (first.getDay() + 6) % 7;
            const cursor = new Date(year, month, 1 - lead);
            let rows = '';
            for (let r = 0; r < 6; r++) {
                let cells = '';
                for (let c = 0; c < 7; c++) {
                    cells += dayCell(cursor, cursor.getMonth() === month);
                    cursor.setDate(cursor.getDate() + 1);
                }
                rows += `<div class="pp-row">${cells}</div>`;
                if (cursor.getMonth() !== month && r >= 3) break;
            }
            return `
                <div class="pp-nav">
                    <button type="button" class="pp-arrow" data-nav="-1" aria-label="Предыдущий месяц">‹</button>
                    <span class="pp-title">${MONTHS[month]} ${year}</span>
                    <button type="button" class="pp-arrow" data-nav="1" aria-label="Следующий месяц">›</button>
                </div>
                <div class="pp-week-head">${WD.map(w => `<span>${w}</span>`).join('')}</div>
                <div class="pp-grid${draftKind === 'week' ? ' pp-week-mode' : ''}">${rows}</div>`;
        }

        function renderMonths() {
            const year = view.getFullYear();
            const counts = {};
            for (const s of marked) if (s.startsWith(`${year}-`)) counts[Number(s.slice(5, 7)) - 1] = (counts[Number(s.slice(5, 7)) - 1] || 0) + 1;
            const current = kind === 'month' ? start.slice(0, 7) : '';
            const tiles = MONTHS_SHORT.map((m, i) => {
                const key = `${year}-${pad(i + 1)}`;
                const classes = ['pp-month'];
                if (key === current) classes.push('pp-in', 'pp-start', 'pp-end');
                if (counts[i]) classes.push('pp-has');
                return `<button type="button" class="${classes.join(' ')}" data-month="${key}"><span>${m}</span>${counts[i] ? `<small>${counts[i]} дн.</small>` : ''}</button>`;
            }).join('');
            return `
                <div class="pp-nav">
                    <button type="button" class="pp-arrow" data-nav-year="-1" aria-label="Предыдущий год">‹</button>
                    <span class="pp-title">${year}</span>
                    <button type="button" class="pp-arrow" data-nav-year="1" aria-label="Следующий год">›</button>
                </div>
                <div class="pp-months">${tiles}</div>`;
        }

        function quickChips() {
            const last = marked.length ? marked[marked.length - 1] : null;
            const today = todayIso();
            const chips = [];
            if (last) chips.push(['last', 'Последняя поставка']);
            chips.push(['week', 'Эта неделя'], ['prevweek', 'Прошлая неделя'], ['month', 'Этот месяц']);
            if (today !== last) chips.unshift(['today', 'Сегодня']);
            const allowed = { last: 'day', today: 'day', week: 'week', prevweek: 'week', month: 'month' };
            return chips.filter(([k]) => kinds.includes(allowed[k])).map(([k, t]) => `<button type="button" class="chip" data-quick="${k}">${t}</button>`).join('');
        }

        function hint() {
            if (draftKind === 'range') return rangeFirst ? `Начало: ${rangeText(rangeFirst, rangeFirst)}. Выберите конец` : 'Выберите начало и конец периода';
            if (draftKind === 'week') return 'Нажмите на любой день недели';
            return '';
        }

        function render() {
            const tabs = kinds.length > 1 ? `<div class="seg pp-tabs">${Object.entries(KINDS).filter(([k]) => kinds.includes(k)).map(([k, t]) =>
                    `<button type="button" class="seg-btn${k === draftKind ? ' active' : ''}" data-kind="${k}">${t}</button>`).join('')}</div>` : '';
            pop.innerHTML = `
                ${tabs}
                <div class="pp-body">${draftKind === 'month' ? renderMonths() : renderCalendar()}</div>
                <p class="pp-hint">${hint()}</p>
                <div class="pp-quick">${quickChips()}</div>`;
        }

        function open() {
            draftKind = kind;
            rangeFirst = null;
            view = parse(start);
            render();
            pop.hidden = false;
            requestAnimationFrame(() => pop.classList.add('open'));
            button.setAttribute('aria-expanded', 'true');
        }

        function close() {
            pop.classList.remove('open');
            button.setAttribute('aria-expanded', 'false');
            rangeFirst = null;
            setTimeout(() => { if (!pop.classList.contains('open')) pop.hidden = true; }, 160);
        }

        function pickDay(s) {
            if (draftKind === 'day') { apply('day', s, s); close(); return; }
            if (draftKind === 'week') { const m = monday(s); apply('week', m, addDays(m, 6)); close(); return; }
            if (draftKind === 'range') {
                if (!rangeFirst) { rangeFirst = s; render(); return; }
                apply('range', rangeFirst, s);
                close();
            }
        }

        function quick(k) {
            const today = todayIso();
            if (k === 'last') { const d = marked[marked.length - 1]; apply('day', d, d); }
            if (k === 'today') apply('day', today, today);
            if (k === 'week') { const m = monday(today); apply('week', m, addDays(m, 6)); }
            if (k === 'prevweek') { const m = addDays(monday(today), -7); apply('week', m, addDays(m, 6)); }
            if (k === 'month') apply('month', monthStart(today), monthEnd(today));
            close();
        }

        pop.addEventListener('click', (e) => {
            const t = e.target.closest('button');
            if (!t) return;
            if (t.dataset.kind) { draftKind = t.dataset.kind; rangeFirst = null; render(); return; }
            if (t.dataset.nav) { view = new Date(view.getFullYear(), view.getMonth() + Number(t.dataset.nav), 1); render(); return; }
            if (t.dataset.navYear) { view = new Date(view.getFullYear() + Number(t.dataset.navYear), view.getMonth(), 1); render(); return; }
            if (t.dataset.date) { pickDay(t.dataset.date); return; }
            if (t.dataset.month) { const s = `${t.dataset.month}-01`; apply('month', s, monthEnd(s)); close(); return; }
            if (t.dataset.quick) quick(t.dataset.quick);
        });

        button.addEventListener('click', () => (pop.hidden ? open() : close()));
        document.addEventListener('mousedown', (e) => { if (!pop.hidden && !root.contains(e.target)) close(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { close(); button.focus(); } });

        // Стрелки: день — к соседней дате с данными, неделя и месяц — на шаг, период — на свою длину
        function step(dir) {
            if (kind === 'day') {
                const candidates = dir < 0 ? marked.filter(d => d < start) : marked.filter(d => d > start);
                const target = candidates.length ? (dir < 0 ? candidates[candidates.length - 1] : candidates[0]) : addDays(start, dir);
                apply('day', target, target);
            } else if (kind === 'week') {
                apply('week', addDays(start, 7 * dir), addDays(end, 7 * dir));
            } else if (kind === 'month') {
                const d = parse(start);
                const s = iso(new Date(d.getFullYear(), d.getMonth() + dir, 1));
                apply('month', s, monthEnd(s));
            } else {
                const span = Math.round((parse(end) - parse(start)) / 86400000) + 1;
                apply('range', addDays(start, span * dir), addDays(end, span * dir));
            }
        }
        prev.addEventListener('click', () => step(-1));
        next.addEventListener('click', () => step(1));

        return {
            set(newKind, s, e) { apply(newKind, s, e, true); },
            setMarked(dates) { marked = [...dates].sort(); markedSet = new Set(marked); },
            get() { return { kind, start, end }; },
        };
    }

    return { create, rangeText, iso, parse, monday, monthEnd, addDays };
})();
