// Окно «Номенклатура»: предпросмотр печатного листа и печать PDF.
// Вёрстку и PDF собирает сервер (WeasyPrint), как в ka-webapp; здесь только выбор сортировки и показ.
// Обе половины листа сортируются одинаково.
window.LavkaSheet = (() => {
    const STORAGE_KEY = 'obed_lavka_sheet_sort';
    const OLD_KEY = 'obed_lavka_sheet_sorts';   // было: отдельно левая и правая половины
    const SHEET_W = 297 * 96 / 25.4;            // A4 альбомный в CSS-пикселях
    const SHEET_H = 210 * 96 / 25.4;
    let modal, box, frame, stage, sort, title, printBtn;
    let period = null;      // { start, end, label, quantity }
    let sortsLoaded = false;

    function saved() {
        try {
            const value = localStorage.getItem(STORAGE_KEY);
            if (value) return value;
            return (JSON.parse(localStorage.getItem(OLD_KEY)) || {}).left || null;
        } catch { return null; }
    }

    function remember() {
        try { localStorage.setItem(STORAGE_KEY, sort.value); } catch { /* приватный режим */ }
    }

    function query() {
        return new URLSearchParams({ start: period.start, end: period.end, left: sort.value, right: sort.value }).toString();
    }

    // Окно подстраивается под лист: лист занимает всю ширину между отступами окна,
    // а ширина окна выбирается так, чтобы лист целиком помещался и по высоте экрана
    function fit() {
        const css = getComputedStyle(box);
        const padX = parseFloat(css.paddingLeft) + parseFloat(css.paddingRight);
        const chrome = box.offsetHeight - stage.offsetHeight;   // шапка, панель, подсказка, отступы
        const byHeight = (window.innerHeight - 48 - chrome) * SHEET_W / SHEET_H;
        const byWidth = window.innerWidth - 48 - padX;
        const inner = Math.max(320, Math.min(byWidth, byHeight, SHEET_W));
        box.style.width = `${inner + padX}px`;
        const scale = inner / SHEET_W;
        frame.style.width = `${SHEET_W}px`;
        frame.style.height = `${SHEET_H}px`;
        frame.style.transform = `scale(${scale})`;
        stage.style.height = `${SHEET_H * scale}px`;
    }

    function load() {
        stage.classList.add('loading');
        frame.onload = () => stage.classList.remove('loading');
        frame.src = `/api/v1/lavka/sheet.html?${query()}`;
    }

    async function loadSorts() {
        if (sortsLoaded) return;
        const sorts = await Hub.api('/api/v1/lavka/sheet/sorts');
        sort.innerHTML = sorts.map(s => `<option value="${s.value}">${Hub.esc(s.label)}</option>`).join('');
        const remembered = saved();
        if (remembered && sorts.some(s => s.value === remembered)) sort.value = remembered;
        sortsLoaded = true;
    }

    // Печать: PDF грузится в скрытую рамку и уходит в системное окно печати.
    // Если браузер не даёт печатать из рамки, PDF открывается во вкладке
    async function print() {
        printBtn.disabled = true;
        const text = printBtn.textContent;
        printBtn.textContent = 'Готовлю PDF…';
        const url = `/api/v1/lavka/sheet.pdf?${query()}`;
        try {
            const r = await fetch(url);
            if (!r.ok) {
                const data = await r.json().catch(() => ({}));
                throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось собрать PDF');
            }
            const blob = URL.createObjectURL(await r.blob());
            const holder = document.createElement('iframe');
            holder.className = 'print-holder';
            holder.src = blob;
            holder.onload = () => {
                try {
                    holder.contentWindow.focus();
                    holder.contentWindow.print();
                } catch {
                    window.open(url, '_blank');
                }
                setTimeout(() => { holder.remove(); URL.revokeObjectURL(blob); }, 60000);
            };
            document.body.appendChild(holder);
        } catch (err) {
            Hub.toast(err.message, 'error');
        } finally {
            printBtn.disabled = false;
            printBtn.textContent = text;
        }
    }

    function init() {
        modal = document.getElementById('sheetModal');
        box = modal.querySelector('.sheet-modal');
        frame = document.getElementById('sheetFrame');
        stage = document.getElementById('sheetStage');
        sort = document.getElementById('sheetSort');
        title = document.getElementById('sheetTitle');
        printBtn = document.getElementById('sheetPrint');

        sort.addEventListener('change', () => { remember(); load(); });
        printBtn.addEventListener('click', print);
        document.getElementById('sheetClose').addEventListener('click', () => Hub.closeModal(modal));
        window.addEventListener('resize', () => { if (modal.classList.contains('open')) fit(); });
    }

    async function open(p) {
        period = p;
        title.textContent = `Номенклатура · ${p.label} · ${p.quantity} шт.`;
        try {
            await loadSorts();
        } catch (err) {
            Hub.toast(err.message, 'error');
            return;
        }
        Hub.openModal(modal);
        fit();
        load();
    }

    return { init, open };
})();
