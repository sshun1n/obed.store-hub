// Окно «Номенклатура»: предпросмотр печатного листа и печать PDF.
// Вёрстку и PDF собирает сервер (WeasyPrint), как в ka-webapp; здесь только выбор сортировок и показ.
window.LavkaSheet = (() => {
    const STORAGE_KEY = 'obed_lavka_sheet_sorts';
    let modal, frame, stage, left, right, title, printBtn;
    let period = null;      // { start, end, label, quantity }
    let sortsLoaded = false;

    function saved() {
        try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch { return {}; }
    }

    function remember() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ left: left.value, right: right.value })); } catch { /* приватный режим */ }
    }

    function query() {
        const p = new URLSearchParams({ start: period.start, end: period.end, left: left.value, right: right.value });
        return p.toString();
    }

    // Лист 297×210 мм вписываем в ширину окна, сохраняя пропорции
    function fit() {
        const sheetWidth = 297 * 96 / 25.4;
        const sheetHeight = 210 * 96 / 25.4;
        // Вписываем и по ширине, и по высоте окна: лист должен быть виден целиком
        const room = window.innerHeight - 290;
        const scale = Math.min(1, (stage.parentElement.clientWidth - 2) / sheetWidth, Math.max(room, 240) / sheetHeight);
        stage.style.width = `${sheetWidth * scale + 2}px`;
        frame.style.width = `${sheetWidth}px`;
        frame.style.height = `${sheetHeight}px`;
        frame.style.transform = `scale(${scale})`;
        stage.style.height = `${sheetHeight * scale}px`;
    }

    function load() {
        stage.classList.add('loading');
        frame.onload = () => stage.classList.remove('loading');
        frame.src = `/api/v1/lavka/sheet.html?${query()}`;
    }

    async function loadSorts() {
        if (sortsLoaded) return;
        const sorts = await Hub.api('/api/v1/lavka/sheet/sorts');
        const html = sorts.map(s => `<option value="${s.value}">${Hub.esc(s.label)}</option>`).join('');
        left.innerHTML = html;
        right.innerHTML = html;
        const remembered = saved();
        if (remembered.left && sorts.some(s => s.value === remembered.left)) left.value = remembered.left;
        if (remembered.right && sorts.some(s => s.value === remembered.right)) right.value = remembered.right;
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
        frame = document.getElementById('sheetFrame');
        stage = document.getElementById('sheetStage');
        left = document.getElementById('sortLeft');
        right = document.getElementById('sortRight');
        title = document.getElementById('sheetTitle');
        printBtn = document.getElementById('sheetPrint');

        left.addEventListener('change', () => { remember(); load(); });
        right.addEventListener('change', () => { remember(); load(); });
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
        requestAnimationFrame(fit);
        load();
    }

    return { init, open };
})();
