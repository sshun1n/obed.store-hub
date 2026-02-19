document.addEventListener('DOMContentLoaded', () => {
    const categoryTreeContainer = document.getElementById('categoryTree');
    const addCategoryForm = document.getElementById('addCategoryForm');
    const categoryNameInput = document.getElementById('categoryName');
    const parentCategorySelect = document.getElementById('parentCategory');

    const productListContainer = document.getElementById('productList');
    const productSearchInput = document.getElementById('productSearchInput');
    const productCategoryFilter = document.getElementById('productCategoryFilter');
    const productStatusMsg = document.getElementById('productStatusMsg');
    const categoryContextMenu = document.getElementById('categoryContextMenu');

    // --- State ---
    let allCategories = []; 
    let treeCategories = []; 
    let allProducts = [];
    let productSearchTerm = '';
    let productCategoryFilterId = '';
    let currentUser = null;

    // --- 1. API Communication ---

    async function createCategory(name, parentId = null) {
        const newCategory = { name, parent_id: parentId };
        const response = await fetch('/api/v1/categories/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newCategory),
        });
        if (!response.ok) throw new Error('Не удалось создать категорию.');
        await fetchAndRenderCategories();
    }

    async function deleteCategory(categoryId) {
        if (!confirm('Вы уверены, что хотите удалить эту категорию?')) return;
        const response = await fetch(`/api/v1/categories/${categoryId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('Не удалось удалить категорию.');
        await fetchAndRenderCategories();
    }

    async function syncAndFetchProducts() {
        try {
            productStatusMsg.innerText = 'Синхронизация...';
            const nomResponse = await fetch('/api/nomenclature');
            const nomData = await nomResponse.json();
            const names = nomData.data.map(item => item.full_name);

            await fetch('/api/v1/products/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(names)
            });

            const prodResponse = await fetch('/api/v1/products/');
            allProducts = await prodResponse.json();
            
            productStatusMsg.innerText = '';
            renderProductList();
        } catch (error) {
            console.error('Ошибка синхронизации продуктов:', error);
            productStatusMsg.innerText = 'Ошибка синхронизации';
        }
    }

    async function updateProductCategories(productId, categoryIds) {
        const response = await fetch(`/api/v1/products/${productId}/categories`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category_ids: categoryIds })
        });
        if (!response.ok) throw new Error('Не удалось обновить категории.');
        const updatedProduct = await response.json();
        
        const idx = allProducts.findIndex(p => p.id === productId);
        if (idx !== -1) allProducts[idx] = updatedProduct;
        
        renderProductList();
    }

    // --- 2. Rendering ---

    function getHierarchyPrefix(level) {
        if (level === 0) return "";
        return "└─ ".padStart(level * 3, "\u00A0");
    }

    function buildCategoryTreeHtml(categories) {
        if (!categories || categories.length === 0) return '';
        let html = '<ul>';
        for (const category of categories) {
            const hasChildren = category.children && category.children.length > 0;
            html += `<li data-id="${category.id}">
                <div class="category-item">
                    <span><span class="node-icon">📂</span>${category.name}</span>
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
            optionsHtml += `<option value="${category.id}">${prefix}${category.name}</option>`;
            if (category.children) optionsHtml += buildDropdownOptions(category.children, level + 1);
        }
        return optionsHtml;
    }

    async function fetchAndRenderCategories() {
        try {
            const response = await fetch('/api/v1/categories/');
            treeCategories = await response.json();
            allCategories = flattenCategories(treeCategories);

            categoryTreeContainer.innerHTML = treeCategories.length === 0 
                ? '<p class="text-muted">Категорий пока нет.</p>' 
                : buildCategoryTreeHtml(treeCategories);
            
            parentCategorySelect.innerHTML = '<option value="">-- Корневая категория --</option>' + buildDropdownOptions(treeCategories);
            parentCategorySelect.disabled = false;

            const currentFilterValue = productCategoryFilter.value;
            productCategoryFilter.innerHTML = `
                <option value="">-- Все категории --</option>
                <option value="none">⚠️ Без категории</option>
            ` + buildDropdownOptions(treeCategories);
            productCategoryFilter.value = currentFilterValue;
            
            if (allProducts.length > 0) renderProductList();
        } catch (error) {
            categoryTreeContainer.innerHTML = `<p style="color: red;">${error.message}</p>`;
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
            filtered = filtered.filter(p => p.name_from_sheet.toLowerCase().includes(term));
        }

        filtered.sort((a, b) => a.name_from_sheet.localeCompare(b.name_from_sheet));

        if (filtered.length === 0) {
            productListContainer.innerHTML = '<p class="text-muted" style="padding: 20px; text-align: center;">Ничего не найдено</p>';
            return;
        }

        productListContainer.innerHTML = filtered.map(product => {
            const { mainPart, composition } = parseDishName(product.name_from_sheet);
            const tagsHtml = product.categories.map(cat => `
                <span class="tag">
                    ${cat.name}
                    <span class="tag-remove" data-cat-id="${cat.id}" data-prod-id="${product.id}" title="Отвязать">&times;</span>
                </span>
            `).join('');

            return `
                <div class="product-row" data-id="${product.id}">
                    <div class="product-info">
                        <span class="product-name">${mainPart}</span>
                        <span class="product-composition">${composition}</span>
                    </div>
                    <div class="product-tags">
                        ${tagsHtml}
                        <button class="tag-add" data-prod-id="${product.id}">+ Привязать</button>
                    </div>
                </div>
            `;
        }).join('');

        if (window.location.hash === '#product-directory') {
            const el = document.getElementById('product-directory');
            if (el) {
                setTimeout(() => { el.scrollIntoView({ behavior: 'smooth' }); }, 100);
            }
        }
    }

    // --- 3. Context Menu Logic ---

    function showCategoryContextMenu(productId, buttonEl) {
        const product = allProducts.find(p => p.id === productId);
        const currentCatIds = product.categories.map(c => c.id);
        const available = allCategories.filter(c => !currentCatIds.includes(c.id));

        if (available.length === 0) {
            alert('Все доступные категории уже привязаны.');
            return;
        }

        categoryContextMenu.innerHTML = available.map(cat => {
            const prefix = getHierarchyPrefix(cat.level).replace(/&nbsp;/g, ' ');
            return `
                <div class="menu-item" data-cat-id="${cat.id}" data-prod-id="${productId}">
                    <span>${prefix}${cat.name}</span>
                </div>
            `;
        }).join('');

        categoryContextMenu.hidden = false;

        const rect = buttonEl.getBoundingClientRect();
        const menuHeight = categoryContextMenu.offsetHeight;
        const viewportHeight = window.innerHeight;

        let top = rect.bottom + 5;
        let left = rect.left;

        if (top + menuHeight > viewportHeight) {
            top = rect.top - menuHeight - 5;
        }

        categoryContextMenu.style.top = `${top}px`;
        categoryContextMenu.style.left = `${left}px`;
    }

    function hideCategoryContextMenu() {
        categoryContextMenu.hidden = true;
    }

    // --- 4. Event Handlers ---

    addCategoryForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = categoryNameInput.value.trim();
        const parentId = parentCategorySelect.value ? parseInt(parentCategorySelect.value, 10) : null;
        if (name) {
            try {
                await createCategory(name, parentId);
                categoryNameInput.value = '';
                parentCategorySelect.value = '';
            } catch (err) { alert(err.message); }
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
            deleteCategory(id).catch(err => alert(err.message));
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

    productListContainer.addEventListener('click', async (e) => {
        const target = e.target;
        
        if (target.classList.contains('tag-remove')) {
            const prodId = parseInt(target.dataset.prodId, 10);
            const catId = parseInt(target.dataset.catId, 10);
            const product = allProducts.find(p => p.id === prodId);
            const newCatIds = product.categories.filter(c => c.id !== catId).map(c => c.id);
            updateProductCategories(prodId, newCatIds).catch(err => alert(err.message));
        }

        if (target.classList.contains('tag-add')) {
            const prodId = parseInt(target.dataset.prodId, 10);
            showCategoryContextMenu(prodId, target);
        }
    });

    categoryContextMenu.addEventListener('click', (e) => {
        const item = e.target.closest('.menu-item');
        if (!item) return;

        const prodId = parseInt(item.dataset.prodId, 10);
        const catId = parseInt(item.dataset.catId, 10);
        const product = allProducts.find(p => p.id === prodId);
        const currentCatIds = product.categories.map(c => c.id);

        updateProductCategories(prodId, [...currentCatIds, catId])
            .then(() => hideCategoryContextMenu())
            .catch(err => alert(err.message));
    });

    document.addEventListener('click', (e) => {
        if (!categoryContextMenu.contains(e.target) && !e.target.classList.contains('tag-add')) {
            hideCategoryContextMenu();
        }
    });

    // --- 5. Initialization ---
    async function init() {
        // Проверка параметров URL для предустановки фильтра
        const urlParams = new URLSearchParams(window.location.search);
        const filterParam = urlParams.get('filter');
        if (filterParam === 'none') {
            productCategoryFilterId = 'none';
            if (productCategoryFilter) productCategoryFilter.value = 'none';
        }

        fetchAndRenderCategories();
        syncAndFetchProducts();
    }

    init();
});
