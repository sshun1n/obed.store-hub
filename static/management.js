document.addEventListener('DOMContentLoaded', () => {
    const { esc, api, toast, openModal, closeModal, confirmDialog } = window.Hub;

    const categoryTreeContainer = document.getElementById('categoryTree');
    const addCategoryForm = document.getElementById('addCategoryForm');
    const categoryNameInput = document.getElementById('categoryName');
    const parentCategorySelect = document.getElementById('parentCategory');

    const productListContainer = document.getElementById('productList');
    const productSearchInput = document.getElementById('productSearchInput');
    const productCategoryFilter = document.getElementById('productCategoryFilter');
    const categoryContextMenu = document.getElementById('categoryContextMenu');

    const productModal = document.getElementById('productModal');
    const productCard = document.getElementById('productCard');

    // --- Состояние ---
    let allCategories = [];
    let treeCategories = [];
    let allProducts = [];
    let productSearchTerm = '';
    let productCategoryFilterId = '';
    let isAdmin = false;

    // Открытая карточка: товар, режим и черновик правок
    let cardProduct = null;
    let cardEditing = false;
    let draft = null;

    // --- 1. Запросы к API ---

    async function createCategory(name, parentId = null) {
        await api('/api/v1/categories/', { method: 'POST', body: { name, parent_id: parentId } });
        await fetchAndRenderCategories();
    }

    async function deleteCategory(categoryId) {
        if (!confirm('Вы уверены, что хотите удалить эту категорию?')) return;
        await api(`/api/v1/categories/${categoryId}`, { method: 'DELETE' });
        await fetchAndRenderCategories();
        await loadProducts();
    }

    async function loadProducts() {
        allProducts = await api('/api/v1/products/');
        renderProductList();
    }

    // --- 2. Категории и список товаров ---

    function getHierarchyPrefix(level) {
        if (level === 0) return "";
        return "└─ ".padStart(level * 3, " ");
    }

    function buildCategoryTreeHtml(categories) {
        if (!categories || categories.length === 0) return '';
        let html = '<ul>';
        for (const category of categories) {
            const hasChildren = category.children && category.children.length > 0;
            html += `<li data-id="${category.id}">
                <div class="category-item">
                    <span>${esc(category.name)}</span>
                    <div class="category-actions">
                        <button class="action-btn btn-add-child" title="Добавить подкатегорию">[+]</button>
                        <button class="action-btn btn-delete-category" title="Удалить категорию" ${hasChildren ? 'disabled' : ''}>[x]</button>
                    </div>
                </div>`;
            if (hasChildren) html += buildCategoryTreeHtml(category.children);
            html += `</li>`;
        }
        return html + '</ul>';
    }

    function flattenCategories(categories, level = 0) {
        let flat = [];
        for (const c of categories) {
            flat.push({ id: c.id, name: c.name, level: level });
            if (c.children) flat = flat.concat(flattenCategories(c.children, level + 1));
        }
        return flat;
    }

    function buildDropdownOptions(categories, level = 0) {
        let optionsHtml = '';
        for (const category of categories) {
            const prefix = getHierarchyPrefix(level);
            optionsHtml += `<option value="${category.id}">${prefix}${esc(category.name)}</option>`;
            if (category.children) optionsHtml += buildDropdownOptions(category.children, level + 1);
        }
        return optionsHtml;
    }

    async function fetchAndRenderCategories() {
        try {
            treeCategories = await api('/api/v1/categories/');
            allCategories = flattenCategories(treeCategories);

            categoryTreeContainer.innerHTML = treeCategories.length === 0
                ? '<p class="text-muted">Категорий пока нет.</p>'
                : buildCategoryTreeHtml(treeCategories);

            parentCategorySelect.innerHTML = '<option value="">Корневая</option>' + buildDropdownOptions(treeCategories);

            const currentFilterValue = productCategoryFilter.value;
            productCategoryFilter.innerHTML = `
                <option value="">Все категории</option>
                <option value="none">Без категории</option>
            ` + buildDropdownOptions(treeCategories);
            productCategoryFilter.value = currentFilterValue;

            if (allProducts.length > 0) renderProductList();
        } catch (error) {
            categoryTreeContainer.innerHTML = `<p class="ts-warn">${esc(error.message)}</p>`;
        }
    }

    function parseDishName(fullName) {
        let mainPart = fullName;
        let composition = "";
        const match = fullName.match(/(\d+\s*\/\s*\d+)|(\d+\s*гр\.?)/i);
        if (match) {
            const endIdx = match.index + match[0].length;
            mainPart = fullName.substring(0, endIdx).trim();
            composition = fullName.substring(endIdx).trim();
        }
        return { mainPart, composition };
    }

    // Что показывать: правка из карточки, а если её нет — разбор названия из таблицы
    function productLabel(product) {
        const parsed = parseDishName(product.name_from_sheet);
        return {
            name: product.display_name || parsed.mainPart,
            composition: product.composition || parsed.composition,
        };
    }

    function renderProductList() {
        let filtered = [...allProducts];

        if (productCategoryFilterId === 'none') {
            filtered = filtered.filter(p => p.categories.length === 0);
        } else if (productCategoryFilterId) {
            const catId = parseInt(productCategoryFilterId, 10);
            filtered = filtered.filter(p => p.categories.some(c => c.id === catId));
        }

        if (productSearchTerm) {
            const term = productSearchTerm.toLowerCase();
            filtered = filtered.filter(p =>
                p.name_from_sheet.toLowerCase().includes(term)
                || (p.display_name || '').toLowerCase().includes(term)
                || (p.barcode || '').includes(term));
        }

        filtered.sort((a, b) => productLabel(a).name.localeCompare(productLabel(b).name));

        if (filtered.length === 0) {
            productListContainer.innerHTML = '<p class="text-muted product-list-empty">Ничего не найдено</p>';
            return;
        }

        productListContainer.innerHTML = filtered.map(product => `
            <div class="product-row" data-id="${product.id}" role="button" tabindex="0">
                <span class="product-name">${esc(productLabel(product).name)}</span>
                <span class="product-barcode">${esc(product.barcode || '')}</span>
            </div>
        `).join('');
    }

    // --- 3. Карточка товара ---

    function barcodeUrl(product, download = false) {
        // Код в адресе — чтобы браузер не показал старую картинку после замены
        return `/api/v1/products/${product.id}/barcode.png?code=${product.barcode}${download ? '&download=true' : ''}`;
    }

    function renderCategoryTags(categories, editable) {
        const tags = categories.map(cat => `
            <span class="tag">${esc(cat.name)}${editable
                ? `<span class="tag-remove" data-cat-id="${cat.id}" role="button" title="Отвязать">&times;</span>`
                : ''}</span>
        `).join('');
        const add = editable ? '<button type="button" class="tag-add" data-act="add-category">+ Привязать</button>' : '';
        if (!tags && !add) return '<span class="text-muted">Не привязан</span>';
        return tags + add;
    }

    function renderBarcodeView(product) {
        if (!product.barcode) {
            return `
                <div class="pc-barcode-empty">
                    <span class="pc-barcode-glyph" aria-hidden="true">▮▯▮</span>
                    <span>Штрих-код не задан</span>
                    ${isAdmin ? '<button type="button" class="btn btn-secondary btn-sm" data-act="edit">Добавить</button>' : ''}
                </div>`;
        }
        return `
            <div class="pc-barcode-image">
                <img src="${barcodeUrl(product)}" alt="EAN-13 ${esc(product.barcode)}">
            </div>
            <a class="btn btn-secondary pc-download" href="${barcodeUrl(product, true)}">Скачать PNG для этикетки</a>`;
    }

    function renderBarcodeEdit() {
        return `
            <div class="form-group">
                <label for="pcBarcode" class="control-label">EAN-13</label>
                <input type="text" id="pcBarcode" class="styled-text-input pc-barcode-input" inputmode="numeric" maxlength="14" autocomplete="off" value="${esc(draft.barcode)}" placeholder="13 цифр">
                <span class="field-hint">Введите код с упаковки или сгенерируйте внутренний. Из 12 цифр контрольная будет дописана сама.</span>
            </div>
            <button type="button" class="btn btn-secondary btn-sm pc-generate" data-act="generate">Сгенерировать</button>`;
    }

    function renderCard() {
        const product = cardProduct;
        const label = productLabel(product);
        const parsed = parseDishName(product.name_from_sheet);
        const draftCategories = cardEditing
            ? allCategories.filter(c => draft.categoryIds.includes(c.id))
            : product.categories;

        const mainHtml = cardEditing ? `
            <div class="form-group">
                <label for="pcName" class="control-label">Название</label>
                <input type="text" id="pcName" class="styled-text-input" value="${esc(draft.name)}" placeholder="${esc(parsed.mainPart)}">
            </div>
            <div class="form-group">
                <label for="pcComposition" class="control-label">Состав</label>
                <textarea id="pcComposition" class="styled-text-input" rows="6" placeholder="${esc(parsed.composition)}">${esc(draft.composition)}</textarea>
                <span class="field-hint">Пустое поле вернёт значение из таблицы.</span>
            </div>
            <div class="form-group">
                <span class="control-label">Категории</span>
                <div class="pc-tags" id="pcTags">${renderCategoryTags(draftCategories, true)}</div>
            </div>
        ` : `
            <div class="pc-field">
                <span class="control-label">Состав</span>
                <p class="pc-text">${label.composition ? esc(label.composition) : '<span class="text-muted">Не указан</span>'}</p>
            </div>
            <div class="pc-field">
                <span class="control-label">Категории</span>
                <div class="pc-tags">${renderCategoryTags(draftCategories, false)}</div>
            </div>
        `;

        productCard.innerHTML = `
            <header class="pc-head">
                <div class="pc-title">
                    <span class="eyebrow">Карточка товара</span>
                    <h2>${esc(label.name)}</h2>
                </div>
                <div class="pc-head-actions">
                    ${isAdmin && !cardEditing ? '<button type="button" class="btn btn-secondary btn-sm" data-act="edit">Редактировать</button>' : ''}
                    <button type="button" class="icon-btn" data-act="close" aria-label="Закрыть">&times;</button>
                </div>
            </header>
            <div class="pc-body">
                <div class="pc-main">
                    ${mainHtml}
                    <div class="pc-field pc-source">
                        <span class="control-label">Название в таблице</span>
                        <p>${esc(product.name_from_sheet)}</p>
                    </div>
                </div>
                <aside class="pc-side">
                    <h3 class="pc-side-title">Штрих-код</h3>
                    ${cardEditing ? renderBarcodeEdit() : renderBarcodeView(product)}
                </aside>
            </div>
            ${cardEditing ? `
                <footer class="pc-footer">
                    <span class="error-msg pc-error" id="pcError"></span>
                    <button type="button" class="btn btn-secondary" data-act="cancel">Отмена</button>
                    <button type="button" class="btn btn-primary" data-act="save">Сохранить</button>
                </footer>` : ''}
        `;

        // Пока идёт правка, окно не закрывается от Esc и клика мимо
        productModal.toggleAttribute('data-keep-open', cardEditing);
    }

    function openCard(productId) {
        cardProduct = allProducts.find(p => p.id === productId);
        if (!cardProduct) return;
        cardEditing = false;
        draft = null;
        renderCard();
        openModal(productModal);
        productCard.scrollTop = 0;
        productCard.focus(); // клавиатура работает с окном, а не со списком под ним
    }

    function startEdit() {
        const product = cardProduct;
        const label = productLabel(product);
        draft = {
            name: label.name,
            composition: label.composition,
            barcode: product.barcode || '',
            categoryIds: product.categories.map(c => c.id),
        };
        cardEditing = true;
        renderCard();
        document.getElementById('pcName').focus();
    }

    // Поля ввода перерисовываются при правке категорий, поэтому черновик держим в draft
    function readDraftFields() {
        draft.name = document.getElementById('pcName').value;
        draft.composition = document.getElementById('pcComposition').value;
        draft.barcode = document.getElementById('pcBarcode').value;
    }

    function hasUnsavedChanges() {
        readDraftFields();
        const product = cardProduct;
        const label = productLabel(product);
        const sameCategories = [...draft.categoryIds].sort().join() === product.categories.map(c => c.id).sort().join();
        return draft.name.trim() !== label.name
            || draft.composition.trim() !== label.composition
            || draft.barcode.trim() !== (product.barcode || '')
            || !sameCategories;
    }

    async function leaveEdit(thenClose) {
        if (hasUnsavedChanges()) {
            const ok = await confirmDialog({
                title: 'Отменить изменения?',
                text: 'Правки в карточке не сохранены и будут потеряны.',
                okText: 'Не сохранять',
            });
            if (!ok) return;
        }
        cardEditing = false;
        draft = null;
        renderCard();
        if (thenClose) closeModal(productModal);
    }

    async function saveCard(saveBtn) {
        readDraftFields();
        const parsed = parseDishName(cardProduct.name_from_sheet);
        const name = draft.name.trim();
        const composition = draft.composition.trim();
        const errorEl = document.getElementById('pcError');
        errorEl.textContent = '';
        saveBtn.disabled = true;
        try {
            const updated = await api(`/api/v1/products/${cardProduct.id}`, {
                method: 'PUT',
                body: {
                    // Совпадает с таблицей — правки нет, поле снова следует за таблицей
                    display_name: name === parsed.mainPart ? null : name,
                    composition: composition === parsed.composition ? null : composition,
                    barcode: draft.barcode,
                    category_ids: draft.categoryIds,
                },
            });
            const idx = allProducts.findIndex(p => p.id === updated.id);
            if (idx !== -1) allProducts[idx] = updated;
            cardProduct = updated;
            cardEditing = false;
            draft = null;
            renderCard();
            renderProductList();
            toast('Карточка сохранена');
        } catch (err) {
            errorEl.textContent = err.message;
            saveBtn.disabled = false;
        }
    }

    async function generateBarcode() {
        const input = document.getElementById('pcBarcode');
        if (input.value.trim()) {
            const ok = await confirmDialog({
                title: 'Заменить штрих-код?',
                text: 'Этикетки, напечатанные с прежним кодом, перестанут ему соответствовать.',
                okText: 'Заменить',
            });
            if (!ok) return;
        }
        try {
            const { code } = await api('/api/v1/products/barcode/generate');
            input.value = code;
        } catch (err) {
            toast(err.message, 'error');
        }
    }

    function showCategoryMenu(buttonEl) {
        const available = allCategories.filter(c => !draft.categoryIds.includes(c.id));
        if (available.length === 0) {
            toast('Все доступные категории уже привязаны');
            return;
        }

        categoryContextMenu.innerHTML = available.map(cat => `
            <div class="menu-item" data-cat-id="${cat.id}">
                <span>${getHierarchyPrefix(cat.level)}${esc(cat.name)}</span>
            </div>
        `).join('');
        categoryContextMenu.hidden = false;

        const rect = buttonEl.getBoundingClientRect();
        const menuHeight = categoryContextMenu.offsetHeight;
        let top = rect.bottom + 5;
        if (top + menuHeight > window.innerHeight) top = rect.top - menuHeight - 5;
        categoryContextMenu.style.top = `${top}px`;
        categoryContextMenu.style.left = `${rect.left}px`;
    }

    function hideCategoryMenu() {
        categoryContextMenu.hidden = true;
    }

    function refreshDraftTags() {
        const selected = allCategories.filter(c => draft.categoryIds.includes(c.id));
        document.getElementById('pcTags').innerHTML = renderCategoryTags(selected, true);
    }

    // --- 4. Обработчики ---

    addCategoryForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = categoryNameInput.value.trim();
        const parentId = parentCategorySelect.value ? parseInt(parentCategorySelect.value, 10) : null;
        if (name) {
            try {
                await createCategory(name, parentId);
                categoryNameInput.value = '';
                parentCategorySelect.value = '';
            } catch (err) { toast(err.message, 'error'); }
        }
    });

    categoryTreeContainer.addEventListener('click', (e) => {
        const target = e.target;
        const li = target.closest('li');
        if (!li) return;
        const id = parseInt(li.dataset.id, 10);

        if (target.classList.contains('btn-add-child')) {
            parentCategorySelect.value = id;
            categoryNameInput.focus();
        } else if (target.classList.contains('btn-delete-category')) {
            deleteCategory(id).catch(err => toast(err.message, 'error'));
        }
    });

    productSearchInput.addEventListener('input', (e) => {
        productSearchTerm = e.target.value;
        renderProductList();
    });

    productCategoryFilter.addEventListener('change', (e) => {
        productCategoryFilterId = e.target.value;
        renderProductList();
    });

    productListContainer.addEventListener('click', (e) => {
        const row = e.target.closest('.product-row');
        if (row) openCard(parseInt(row.dataset.id, 10));
    });

    productListContainer.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const row = e.target.closest('.product-row');
        if (!row) return;
        e.preventDefault();
        openCard(parseInt(row.dataset.id, 10));
    });

    productCard.addEventListener('click', (e) => {
        if (e.target.classList.contains('tag-remove')) {
            const catId = parseInt(e.target.dataset.catId, 10);
            draft.categoryIds = draft.categoryIds.filter(id => id !== catId);
            refreshDraftTags();
            return;
        }
        const button = e.target.closest('[data-act]');
        if (!button) return;
        const act = button.dataset.act;
        if (act === 'edit') startEdit();
        else if (act === 'cancel') leaveEdit(false);
        else if (act === 'save') saveCard(button);
        else if (act === 'generate') generateBarcode();
        else if (act === 'add-category') showCategoryMenu(button);
        else if (act === 'close') {
            if (cardEditing) leaveEdit(true);
            else closeModal(productModal);
        }
    });

    categoryContextMenu.addEventListener('click', (e) => {
        const item = e.target.closest('.menu-item');
        if (!item) return;
        draft.categoryIds.push(parseInt(item.dataset.catId, 10));
        refreshDraftTags();
        hideCategoryMenu();
    });

    document.addEventListener('click', (e) => {
        if (!categoryContextMenu.contains(e.target) && !e.target.classList.contains('tag-add')) {
            hideCategoryMenu();
        }
    });

    // --- 5. Запуск ---
    async function init() {
        // Ссылка с номенклатуры открывает товары без категории
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('filter') === 'none') {
            productCategoryFilterId = 'none';
            productCategoryFilter.value = 'none';
        }

        // Новые товары из таблицы добавляет синхронизация на сервере
        document.addEventListener('hub:synced', (e) => {
            loadProducts().catch(err => console.error('Ошибка загрузки справочника:', err));
            if (e.detail.added > 0) toast(`Из таблицы добавлено новых товаров: ${e.detail.added}`);
        });

        // Список не ждёт определения роли
        loadProducts().catch((err) => {
            console.error('Ошибка загрузки справочника:', err);
            productListContainer.innerHTML = '<p class="ts-warn product-list-empty">Не удалось загрузить справочник</p>';
        });
        fetchAndRenderCategories();

        try {
            const user = await api('/api/v1/users/me');
            isAdmin = user.role === 'admin';
        } catch (err) {
            console.error('Не удалось определить роль:', err);
        }
        // Роль стала известна: если карточку уже открыли, показываем кнопку правки
        if (cardProduct && !cardEditing) renderCard();
    }

    init();
});
