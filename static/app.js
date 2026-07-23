/**
 * ОСНОВНОЙ СКРИПТ ПРИЛОЖЕНИЯ (Страницы Номенклатуры и Заказов)
 */
document.addEventListener('DOMContentLoaded', () => {
    
    // --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

    function formatDateForInput(date) {
        const d = new Date(date);
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const year = d.getFullYear();
        return `${year}-${month}-${day}`;
    }

    function formatDateForApi(dateString) {
        const [year, month, day] = dateString.split('-');
        return `${day}.${month}.${year}`;
    }

    function setDate(type) {
        const dateInput = document.getElementById('orderDate');
        if (!dateInput) return;
        const date = new Date();
        if (type === 'tomorrow') date.setDate(date.getDate() + 1);
        dateInput.value = formatDateForInput(date);
    }

    // --- ЛОГИКА СТРАНИЦЫ НОМЕНКЛАТУРЫ ---

    let originalNomenclatureData = [];
    const defaultSort = { key: 'quantity', order: 'desc' };
    let currentSort = { ...defaultSort };
    let currentSearchTerm = '';
    let allCategories = []; 
    let excludedCategoryIds = []; 

    function renderNomenclatureTable() {
        const tbody = document.getElementById('tableBody');
        const resetBtn = document.getElementById('resetBtn');
        const alertContainer = document.getElementById('alertContainer');
        if (!tbody) return;

        // 1. Фильтрация
        let processedData = [...originalNomenclatureData];

        if (excludedCategoryIds.length > 0) {
            processedData = processedData.filter(item => {
                if (!item.categories || item.categories.length === 0) return true;
                return item.categories.some(catName => {
                    const catObj = allCategories.find(c => c.name === catName);
                    return catObj && !excludedCategoryIds.includes(catObj.id);
                });
            });
        }

        if (currentSearchTerm) {
            const term = currentSearchTerm.toLowerCase();
            processedData = processedData.filter(item => 
                item.main_name.toLowerCase().includes(term) ||
                (item.composition && item.composition.toLowerCase().includes(term)) ||
                String(item.quantity).includes(term) ||
                (item.categories && item.categories.some(c => c.toLowerCase().includes(term)))
            );
        }

        // 2. Сортировка
        const { key, order } = currentSort;
        processedData.sort((a, b) => {
            const vA = a[key], vB = b[key];
            if (typeof vA === 'string') {
                return order === 'asc' ? vA.localeCompare(vB) : vB.localeCompare(vA);
            }
            return order === 'asc' ? vA - vB : vB - vA;
        });

        // 3. Проверка на нераспределенные товары
        const unassigned = originalNomenclatureData.filter(i => !i.categories || i.categories.length === 0);
        if (unassigned.length > 0) {
            alertContainer.innerHTML = `
                <div class="alert alert-danger" onclick="location.href='/management?filter=none#product-directory'">
                    <strong>Без категории: ${unassigned.length}</strong> — эти позиции не попадут в фильтры. Нажмите, чтобы исправить.
                </div>`;
        } else { alertContainer.innerHTML = ''; }

        // 4. Отрисовка
        tbody.innerHTML = '';
        processedData.forEach(item => {
            const tr = document.createElement('tr');
            const catTags = (item.categories || []).map(c => `<span class="cat-tag">${c}</span>`).join('');
            const noCat = !item.categories || item.categories.length === 0 ? '<span class="cat-tag cat-tag-warn">нет категории</span>' : '';

            tr.innerHTML = `
                <td>
                    <div><strong>${item.main_name}</strong> ${item.composition ? `<span class="composition-toggle" role="button">[+]</span>` : ''}</div>
                    <div class="composition-text">${item.composition || ''}</div>
                    <div style="margin-top: 4px;">${catTags}${noCat}</div>
                </td>
                <td style="text-align: center;"><span class="qty-cell">${item.quantity}</span></td>
            `;
            tbody.appendChild(tr);
        });

        // 5. Индикаторы сортировки
        document.querySelectorAll('th.sortable').forEach(th => {
            const indicator = th.querySelector('.sort-indicator') || document.createElement('span');
            indicator.className = 'sort-indicator';
            indicator.textContent = th.dataset.sortKey === currentSort.key ? (currentSort.order === 'asc' ? '↑' : '↓') : '';
            if (!th.querySelector('.sort-indicator')) th.appendChild(indicator);
        });
        
        resetBtn.hidden = currentSort.key === defaultSort.key && currentSort.order === defaultSort.order && currentSearchTerm === '' && excludedCategoryIds.length === 0;
    }

    async function loadNomenclature() {
        const loading = document.getElementById('loading'), table = document.getElementById('dataTable');
        try {
            const response = await fetch('/api/nomenclature');
            const result = await response.json();
            originalNomenclatureData = result.data || [];
            if (originalNomenclatureData.length > 0) {
                loading.style.display = 'none';
                table.style.display = 'table';
                renderNomenclatureTable();
            } else {
                loading.innerText = "Данные не найдены.";
            }
        } catch (err) { loading.innerText = "Ошибка загрузки данных."; }
    }

    async function fetchCategories() {
        const resp = await fetch('/api/v1/categories/');
        if (resp.ok) allCategories = await resp.json();
    }

    function renderFilterTree(categories, container) {
        if (!categories || categories.length === 0) return '';
        let html = '<ul>';
        categories.forEach(cat => {
            const checked = !excludedCategoryIds.includes(cat.id);
            html += `<li>
                <label class="filter-item"><input type="checkbox" class="category-checkbox" data-id="${cat.id}" ${checked ? 'checked' : ''}><span>${cat.name}</span></label>
                ${renderFilterTree(cat.children)}
            </li>`;
        });
        if (container) container.innerHTML = html + '</ul>';
        return html + '</ul>';
    }

    async function downloadPDF() {
        const rawDate = document.getElementById('orderDate').value;
        if (!rawDate) return alert("Выберите дату!");
        const formattedDate = formatDateForApi(rawDate);
        const btn = document.getElementById('pdfBtn'), status = document.getElementById('statusMsg');
        
        btn.disabled = true; btn.innerText = 'Генерация...';
        try {
            const response = await fetch('/report/pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ date: formattedDate, excluded_category_ids: excludedCategoryIds })
            });
            if (!response.ok) throw new Error('Ошибка генерации');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `report_${formattedDate}.pdf`;
            a.click();
            status.innerText = 'Готово!';
            setTimeout(() => status.innerText = '', 3000);
        } catch (err) { status.innerText = err.message; }
        finally { btn.disabled = false; btn.innerText = 'Скачать PDF Отчет'; }
    }

    function initNomenclaturePage() {
        setDate('today'); loadNomenclature(); fetchCategories();
        document.getElementById('todayBtn').addEventListener('click', () => setDate('today'));
        document.getElementById('tomorrowBtn').addEventListener('click', () => setDate('tomorrow'));
        document.getElementById('pdfBtn').addEventListener('click', downloadPDF);
        document.getElementById('searchInput').addEventListener('input', (e) => { currentSearchTerm = e.target.value; renderNomenclatureTable(); });
        document.getElementById('resetBtn').addEventListener('click', () => {
            currentSort = { ...defaultSort }; currentSearchTerm = ''; excludedCategoryIds = [];
            document.getElementById('searchInput').value = ''; renderNomenclatureTable();
        });
        document.querySelector('#dataTable thead').addEventListener('click', (e) => {
            const th = e.target.closest('th.sortable');
            if (!th) return;
            const key = th.dataset.sortKey;
            currentSort.order = (currentSort.key === key && currentSort.order === 'asc') ? 'desc' : 'asc';
            currentSort.key = key;
            renderNomenclatureTable();
        });
        document.getElementById('tableBody').addEventListener('click', (e) => {
            if (e.target.classList.contains('composition-toggle')) {
                const text = e.target.parentElement.nextElementSibling;
                const visible = text.classList.toggle('visible');
                e.target.textContent = visible ? '[-]' : '[+]';
            }
        });

        // Модальное окно
        const modal = document.getElementById('categoryModal'), filterBtn = document.getElementById('filterBtn');
        filterBtn.addEventListener('click', () => { renderFilterTree(allCategories, document.getElementById('filterCategoryTree')); modal.classList.add('open'); });
        document.getElementById('cancelFilterBtn').onclick = () => modal.classList.remove('open');
        document.getElementById('filterCategoryTree').addEventListener('change', (e) => {
            if (e.target.classList.contains('category-checkbox')) {
                e.target.closest('li').querySelectorAll('.category-checkbox').forEach(cb => cb.checked = e.target.checked);
            }
        });
        document.getElementById('applyFilterBtn').onclick = () => {
            excludedCategoryIds = Array.from(document.querySelectorAll('.category-checkbox:not(:checked)')).map(cb => parseInt(cb.dataset.id, 10));
            modal.classList.remove('open');
            filterBtn.innerText = excludedCategoryIds.length > 0 ? `Категории (${allCategories.length - excludedCategoryIds.length})` : 'Категории';
            renderNomenclatureTable();
        };
    }

    // --- ЛОГИКА СТРАНИЦЫ ЗАКАЗОВ ---

    async function loadOrders() {
        const container = document.getElementById('ordersContainer'), dateInput = document.getElementById('orderDate');
        if (!dateInput.value) return;
        const apiDate = formatDateForApi(dateInput.value);
        container.innerHTML = '<div class="empty-state"><h3>Загрузка...</h3></div>';
        
        // Массив пастельных цветов из CSS
        const pointColors = [
            'var(--point-1)', 'var(--point-2)', 'var(--point-3)', 'var(--point-4)', 'var(--point-5)',
            'var(--point-6)', 'var(--point-7)', 'var(--point-8)', 'var(--point-9)', 'var(--point-10)'
        ];

        try {
            const response = await fetch(`/api/orders?date=${apiDate}`);
            const result = await response.json();
            if (!result.data || result.data.length === 0) {
                container.innerHTML = `<div class="empty-state"><h3>Нет заказов на ${apiDate}</h3></div>`;
                return;
            }
            container.innerHTML = result.data.map((order, index) => {
                // Выбираем цвет по порядку (циклично)
                const color = pointColors[index % pointColors.length];
                
                return `
                <div class="order-card">
                    <div class="order-header" style="background-color: ${color}; color: var(--brand-black);">
                        <span>${order.address}</span>
                    </div>
                    <div class="order-body">
                        ${order.items.map(item => `
                            <div class="order-item">
                                <div class="item-name-details"><span class="item-main-name">${item.main_name}</span><span class="item-composition">${item.composition || ''}</span></div>
                                <span class="item-qty">${item.quantity}</span>
                            </div>`).join('')}
                    </div>
                </div>`;
            }).join('');
        } catch (err) { container.innerHTML = 'Ошибка загрузки.'; }
    }

    function initOrdersPage() {
        setDate('today'); loadOrders();
        document.getElementById('orderDate').addEventListener('change', loadOrders);
        document.getElementById('refreshBtn').addEventListener('click', loadOrders);
        document.getElementById('todayBtn').addEventListener('click', () => { setDate('today'); loadOrders(); });
        document.getElementById('tomorrowBtn').addEventListener('click', () => { setDate('tomorrow'); loadOrders(); });
    }

    function init() {
        if (document.getElementById('dataTable')) initNomenclaturePage();
        else if (document.getElementById('ordersContainer')) initOrdersPage();
    }
    init();
});
