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
    const addProductBtn = document.getElementById('addProductBtn');

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
    let cardIsNew = false;
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

    function parseDishName(sheetName) {
        const fullName = sheetName || ''; // у ручных товаров названия из таблицы нет
        let mainPart = fullName;
        let composition = "";
        const match = fullName.match(/\d+(?:\s*\/\s*\d+)+|\d+\s*гр\.?(?![а-яё])/i);
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

    // Вес из карточки, а если его нет — из названия
    function productWeight(product) {
        return product.weight_grams ?? product.weight_from_name ?? null;
    }

    function formatWeight(grams) {
        return grams === null ? '' : `${grams} г`;
    }

    // Запятая допустима. Пустое поле — null, не число — NaN
    function parseNumber(value) {
        const text = String(value ?? '').trim().replace(',', '.');
        if (!text) return null;
        const number = Number(text);
        return Number.isFinite(number) ? number : NaN;
    }

    function formatNumber(value, digits = 1) {
        return String(Number(value.toFixed(digits))).replace('.', ',');
    }

    // Энергия на 100 г по ТР ТС 022/2011. Считаем, только когда известны все три значения
    function calcEnergy(protein, fat, carbs) {
        if (![protein, fat, carbs].every(v => typeof v === 'number' && Number.isFinite(v))) return null;
        return {
            kcal: 4 * protein + 9 * fat + 4 * carbs,
            kj: 17 * protein + 37 * fat + 17 * carbs,
        };
    }

    function formatDate(isoDate) {
        if (!isoDate) return '';
        // Сервер хранит время в UTC без указания пояса
        return new Date(`${isoDate}Z`).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' });
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
                (p.name_from_sheet || '').toLowerCase().includes(term)
                || (p.display_name || '').toLowerCase().includes(term)
                || (p.barcode || '').includes(term));
        }

        filtered.sort((a, b) => productLabel(a).name.localeCompare(productLabel(b).name));

        if (filtered.length === 0) {
            productListContainer.innerHTML = '<p class="text-muted product-list-empty">Ничего не найдено</p>';
            return;
        }

        productListContainer.innerHTML = `
            <div class="product-list-head">
                <span>Наименование</span>
                <span>Штрих-код</span>
                <span class="product-weight">Вес</span>
            </div>
        ` + filtered.map(product => `
            <div class="product-row" data-id="${product.id}" role="button" tabindex="0">
                <span class="product-name">${esc(productLabel(product).name)}</span>
                <span class="product-barcode">${esc(product.barcode || '—')}</span>
                <span class="product-weight">${formatWeight(productWeight(product)) || '—'}</span>
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

    function renderOrigin(product) {
        if (cardIsNew) return '';
        const date = formatDate(product.created_at);
        if (product.source === 'manual') {
            const who = product.created_by ? `: ${esc(product.created_by)}` : '';
            return `<p class="pc-origin">Добавлен вручную${who}${date ? `, ${date}` : ''}</p>`;
        }
        return `<p class="pc-origin"><span class="pc-origin-mark">Авто</span> Создан программой: найден в Google Таблице${date ? `, ${date}` : ''}</p>`;
    }

    function renderNutritionView(product) {
        const { protein, fat, carbs } = product;
        if (protein === null && fat === null && carbs === null) {
            return '<p class="pc-text"><span class="text-muted">Не указано</span></p>';
        }
        const weight = productWeight(product);
        const cell = (value, factor) => (value === null ? '—' : formatNumber(value * factor));
        const row = (title, factor) => {
            const energy = calcEnergy(protein, fat, carbs);
            return `
                <tr>
                    <th scope="row">${title}</th>
                    <td>${cell(protein, factor)}</td>
                    <td>${cell(fat, factor)}</td>
                    <td>${cell(carbs, factor)}</td>
                    <td>${energy ? formatNumber(energy.kcal * factor, 0) : '—'}</td>
                    <td>${energy ? formatNumber(energy.kj * factor, 0) : '—'}</td>
                </tr>`;
        };
        return `
            <div class="pc-nutrition-wrap">
                <table class="pc-nutrition">
                    <thead><tr><th></th><th>Белки, г</th><th>Жиры, г</th><th>Углев., г</th><th>ккал</th><th>кДж</th></tr></thead>
                    <tbody>
                        ${row('На 100 г', 1)}
                        ${weight ? row(`На порцию, ${weight} г`, weight / 100) : ''}
                    </tbody>
                </table>
            </div>`;
    }

    function energyText() {
        const energy = calcEnergy(parseNumber(draft.protein), parseNumber(draft.fat), parseNumber(draft.carbs));
        return energy
            ? `На 100 г: ${formatNumber(energy.kcal, 0)} ккал, ${formatNumber(energy.kj, 0)} кДж`
            : 'Заполните все три значения, и калорийность посчитается сама.';
    }

    function renderCard() {
        const product = cardProduct;
        const label = productLabel(product);
        const parsed = parseDishName(product.name_from_sheet);
        const fromSheet = product.source !== 'manual';
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
                ${fromSheet ? '<span class="field-hint">Пустое поле вернёт значение из таблицы.</span>' : ''}
            </div>
            <div class="form-group">
                <label for="pcWeight" class="control-label">Вес порции, г</label>
                <input type="text" id="pcWeight" class="styled-text-input pc-weight-input" inputmode="numeric" autocomplete="off" value="${esc(draft.weight)}" placeholder="${product.weight_from_name ?? ''}">
                ${fromSheet ? '<span class="field-hint">Пустое поле вернёт вес из названия.</span>' : ''}
            </div>
            <div class="form-group">
                <span class="control-label">КБЖУ на 100 г</span>
                <div class="pc-nutrition-inputs">
                    <label>Белки, г<input type="text" id="pcProtein" class="styled-text-input" inputmode="decimal" autocomplete="off" value="${esc(draft.protein)}"></label>
                    <label>Жиры, г<input type="text" id="pcFat" class="styled-text-input" inputmode="decimal" autocomplete="off" value="${esc(draft.fat)}"></label>
                    <label>Углеводы, г<input type="text" id="pcCarbs" class="styled-text-input" inputmode="decimal" autocomplete="off" value="${esc(draft.carbs)}"></label>
                </div>
                <span class="field-hint" id="pcEnergy" aria-live="polite">${energyText()}</span>
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
                <span class="control-label">Вес порции</span>
                <p class="pc-text">${formatWeight(productWeight(product)) || '<span class="text-muted">Не указан</span>'}</p>
            </div>
            <div class="pc-field">
                <span class="control-label">КБЖУ</span>
                ${renderNutritionView(product)}
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
                    <h2>${cardIsNew ? 'Новый товар' : esc(label.name)}</h2>
                    ${renderOrigin(product)}
                </div>
                <div class="pc-head-actions">
                    ${isAdmin && !cardEditing ? '<button type="button" class="btn btn-secondary btn-sm" data-act="edit">Редактировать</button>' : ''}
                    <button type="button" class="icon-btn" data-act="close" aria-label="Закрыть">&times;</button>
                </div>
            </header>
            <div class="pc-body">
                <div class="pc-main">
                    ${mainHtml}
                    ${fromSheet ? `
                        <div class="pc-field pc-source">
                            <span class="control-label">Название в таблице</span>
                            <p>${esc(product.name_from_sheet)}</p>
                        </div>` : ''}
                </div>
                <aside class="pc-side">
                    <h3 class="pc-side-title">Штрих-код</h3>
                    ${cardEditing ? renderBarcodeEdit() : renderBarcodeView(product)}
                </aside>
            </div>
            ${cardEditing ? `
                <footer class="pc-footer">
                    ${cardIsNew ? '' : '<button type="button" class="btn btn-danger-ghost" data-act="delete">Удалить</button>'}
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
        cardIsNew = false;
        cardEditing = false;
        draft = null;
        renderCard();
        openModal(productModal);
        productCard.scrollTop = 0;
        productCard.focus(); // клавиатура работает с окном, а не со списком под ним
    }

    function openNewProduct() {
        cardProduct = {
            id: null, name_from_sheet: '', display_name: null, composition: null, barcode: null,
            weight_grams: null, weight_from_name: null, protein: null, fat: null, carbs: null,
            source: 'manual', categories: [],
        };
        cardIsNew = true;
        openModal(productModal);
        startEdit();
        productCard.scrollTop = 0;
    }

    function draftFromProduct(product) {
        const label = productLabel(product);
        const weight = productWeight(product);
        const asText = value => (value === null ? '' : String(value).replace('.', ','));
        return {
            name: label.name,
            composition: label.composition,
            barcode: product.barcode || '',
            weight: asText(weight),
            protein: asText(product.protein),
            fat: asText(product.fat),
            carbs: asText(product.carbs),
            categoryIds: product.categories.map(c => c.id),
        };
    }

    function startEdit() {
        draft = draftFromProduct(cardProduct);
        cardEditing = true;
        renderCard();
        document.getElementById('pcName').focus();
    }

    // Поля ввода перерисовываются при правке категорий, поэтому черновик держим в draft
    function readDraftFields() {
        draft.name = document.getElementById('pcName').value;
        draft.composition = document.getElementById('pcComposition').value;
        draft.barcode = document.getElementById('pcBarcode').value;
        draft.weight = document.getElementById('pcWeight').value;
        draft.protein = document.getElementById('pcProtein').value;
        draft.fat = document.getElementById('pcFat').value;
        draft.carbs = document.getElementById('pcCarbs').value;
    }

    function hasUnsavedChanges() {
        readDraftFields();
        const saved = draftFromProduct(cardProduct);
        const sameCategories = [...draft.categoryIds].sort().join() === [...saved.categoryIds].sort().join();
        const textFields = ['name', 'composition', 'barcode', 'weight', 'protein', 'fat', 'carbs'];
        return !sameCategories || textFields.some(field => draft[field].trim() !== saved[field]);
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
        // У нового товара нечего показывать без формы
        if (cardIsNew) {
            productModal.removeAttribute('data-keep-open');
            closeModal(productModal);
            cardProduct = null;
            cardIsNew = false;
            return;
        }
        // Закрываем без перерисовки: форма растворяется, а не сменяется карточкой
        if (thenClose) {
            productModal.removeAttribute('data-keep-open');
            closeModal(productModal);
            return;
        }
        renderCard();
    }

    // Числовые поля черновика. Ошибка ввода — исключение с понятным текстом
    function readDraftNumbers() {
        const weight = parseNumber(draft.weight);
        if (weight !== null && !(Number.isInteger(weight) && weight > 0)) {
            throw new Error('Вес укажите целым числом граммов');
        }
        const protein = parseNumber(draft.protein);
        const fat = parseNumber(draft.fat);
        const carbs = parseNumber(draft.carbs);
        if ([protein, fat, carbs].some(Number.isNaN)) {
            throw new Error('Белки, жиры и углеводы укажите числами');
        }
        return { weight, protein, fat, carbs };
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
            const { weight, protein, fat, carbs } = readDraftNumbers();
            const common = {
                barcode: draft.barcode,
                protein, fat, carbs,
                category_ids: draft.categoryIds,
            };
            const wasNew = cardIsNew;
            const updated = wasNew
                ? await api('/api/v1/products/', {
                    method: 'POST',
                    body: { ...common, name, composition, weight_grams: weight },
                })
                : await api(`/api/v1/products/${cardProduct.id}`, {
                    method: 'PUT',
                    body: {
                        ...common,
                        // Совпадает с таблицей — правки нет, поле снова следует за таблицей
                        display_name: name === parsed.mainPart ? null : name,
                        composition: composition === parsed.composition ? null : composition,
                        weight_grams: weight === cardProduct.weight_from_name ? null : weight,
                    },
                });
            const idx = allProducts.findIndex(p => p.id === updated.id);
            if (idx !== -1) allProducts[idx] = updated;
            else allProducts.push(updated);
            cardProduct = updated;
            cardIsNew = false;
            cardEditing = false;
            draft = null;
            renderCard();
            renderProductList();
            toast(wasNew ? 'Товар добавлен' : 'Карточка сохранена');
        } catch (err) {
            errorEl.textContent = err.message;
            saveBtn.disabled = false;
        }
    }

    async function deleteCardProduct() {
        const product = cardProduct;
        const ok = await confirmDialog({
            title: 'Удалить товар?',
            text: product.name_from_sheet
                ? 'Штрих-код, вес и КБЖУ будут потеряны. Если товар ещё есть в Google Таблице, при следующей синхронизации он появится снова, но уже пустым.'
                : 'Товар будет удалён вместе со штрих-кодом, весом и КБЖУ. Вернуть его нельзя.',
        });
        if (!ok) return;
        try {
            await api(`/api/v1/products/${product.id}`, { method: 'DELETE' });
        } catch (err) {
            toast(err.message, 'error');
            return;
        }
        allProducts = allProducts.filter(p => p.id !== product.id);
        cardEditing = false;
        draft = null;
        cardProduct = null;
        productModal.removeAttribute('data-keep-open');
        closeModal(productModal);
        renderProductList();
        toast('Товар удалён');
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
        else if (act === 'delete') deleteCardProduct();
        else if (act === 'generate') generateBarcode();
        else if (act === 'add-category') showCategoryMenu(button);
        else if (act === 'close') {
            if (cardEditing) leaveEdit(true);
            else closeModal(productModal);
        }
    });

    // Калорийность пересчитывается прямо при вводе
    productCard.addEventListener('input', (e) => {
        if (!['pcProtein', 'pcFat', 'pcCarbs'].includes(e.target.id)) return;
        readDraftFields();
        document.getElementById('pcEnergy').textContent = energyText();
    });

    addProductBtn.addEventListener('click', openNewProduct);

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
            addProductBtn.hidden = !isAdmin;
        } catch (err) {
            console.error('Не удалось определить роль:', err);
        }
        // Роль стала известна: если карточку уже открыли, показываем кнопку правки
        if (cardProduct && !cardEditing) renderCard();
    }

    init();
});
