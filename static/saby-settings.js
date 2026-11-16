// Окно «Настройки» страницы СБИС — общее для Лавки и Самоката, у каждой свои значения
// (/api/v1/saby/settings?scope=lavka|samokat). Автообновление — в админ-панели, оно общее.
//   SabySettings.init({ scope: 'samokat', button: el, onSaved: () => load() })
window.SabySettings = (() => {
    const field = (label, name, attrs = '', wide = false) =>
        `<label class="form-group${wide ? ' saby-wide' : ''}"><span>${label}</span><input class="styled-text-input" name="${name}" ${attrs}></label>`;
    const toggle = (text, name) =>
        `<label class="saby-toggle"><span>${text}</span><span class="switch"><input type="checkbox" name="${name}"><span class="switch-track"></span></span></label>`;

    function markup(scope) {
        const buyer = scope === 'samokat' ? 'Самокат' : 'Лавка';
        return `
        <div class="modal" id="sabySettingsModal">
            <div class="modal-content saby-settings" role="dialog" aria-modal="true" aria-labelledby="sabySettingsTitle">
                <header class="sheet-modal-head">
                    <div>
                        <span class="eyebrow">СБИС · ${buyer}</span>
                        <h2 class="card-title-small" id="sabySettingsTitle">Настройки</h2>
                    </div>
                    <button type="button" class="icon-btn" data-close aria-label="Закрыть">&times;</button>
                </header>
                <form class="saby-settings-form">
                    <section>
                        <h3>Кнопки</h3>
                        <p class="field-hint">Рядом с «Создать и подписать» есть кнопки для отдельных шагов. Их можно спрятать.</p>
                        ${toggle(scope === 'lavka' ? 'Реализации: прятать «Создать» и «Провести»' : 'Реализации: прятать «Создать» и «Подписать»', 'hide_split_shipments')}
                        ${toggle('Заказ на перевозку: прятать «Создать» и «Подписать»', 'hide_split_transport')}
                        ${toggle('ЭТрН: прятать «Создать» и «Подписать»', 'hide_split_waybills')}
                        ${scope === 'samokat' ? toggle('Прятать «Проверить» — сборку документов без создания', 'hide_check') : ''}
                    </section>
                    <section>
                        <h3>Погрузка и выгрузка</h3>
                        <div class="saby-grid">
                            ${field('Адрес погрузки', 'pickup_address', 'required', true)}
                            ${field('Подача машины', 'pickup_time', 'type="time" required')}
                            ${field('Убытие', 'departure_time', 'type="time" required')}
                            ${field('Выгрузка в точках', 'unload_time', 'type="time" required')}
                        </div>
                    </section>
                    <section>
                        <h3>Груз</h3>
                        <div class="saby-grid">
                            ${field('Наименование', 'cargo_name', 'required')}
                            ${field('Состояние', 'cargo_state', 'required', true)}
                            ${field('Масса тары на точку, кг', 'bag_weight_kg', 'type="number" step="0.01" min="0" required')}
                            ${field('Объём места, м³', 'place_volume_m3', 'type="number" step="0.001" required')}
                            <label class="form-group"><span>Термосумка Д×Ш×В, см</span>
                                <span class="saby-dims"><input class="styled-text-input" type="number" name="bag_length_cm" required><input class="styled-text-input" type="number" name="bag_width_cm" required><input class="styled-text-input" type="number" name="bag_height_cm" required></span></label>
                            <label class="form-group"><span>Температура, °C</span>
                                <span class="saby-dims"><input class="styled-text-input" type="number" name="temp_min" required><input class="styled-text-input" type="number" name="temp_max" required></span></label>
                            ${field('Санитарные нормы', 'sanitary_norms', '', true)}
                        </div>
                        <p class="field-hint">Вес точки — позиции заказа (штуки × вес штуки) плюс тара; вес рейса — сумма по точкам. Вес штуки берётся из карточки товара, иначе из названия («…, 250 г»).</p>
                    </section>
                    <section>
                        <h3>Требования к машине</h3>
                        <div class="saby-grid">
                            ${field('Тип', 'vehicle_type', 'required')}
                            ${field('Кузов', 'body_type', 'required')}
                            ${field('Погрузка', 'loading_type', 'required')}
                            ${field('Грузоподъёмность, т', 'capacity_t', 'type="number" step="0.01" required')}
                            ${field('Объём, м³', 'volume_m3', 'type="number" step="0.01" required')}
                        </div>
                        <p class="field-hint">Реквизиты сторон берутся из «Юрлиц», машина и водитель — из карточки перевозчика. Автообновление из кабинета — в админ-панели.</p>
                    </section>
                    <span class="error-msg" data-error></span>
                    <div class="modal-actions">
                        <button type="button" class="btn btn-secondary" data-close>Отмена</button>
                        <button type="submit" class="btn btn-primary">Сохранить</button>
                    </div>
                </form>
            </div>
        </div>`;
    }

    function init({ scope, button, onSaved }) {
        document.body.insertAdjacentHTML('beforeend', markup(scope));
        const modal = document.getElementById('sabySettingsModal');
        const form = modal.querySelector('form');
        const error = modal.querySelector('[data-error]');
        const url = `/api/v1/saby/settings?scope=${scope}`;

        button.addEventListener('click', async () => {
            error.textContent = '';
            try {
                const s = await Hub.api(url);
                for (const [k, v] of Object.entries(s)) {
                    const input = form.elements[k];
                    if (!input) continue;
                    if (input.type === 'checkbox') input.checked = !!v; else input.value = v ?? '';
                }
                Hub.openModal(modal);
            } catch (err) { Hub.toast(err.message, 'error'); }
        });
        modal.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) Hub.closeModal(modal); });
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const body = {};
            for (const input of form.elements) {
                if (!input.name) continue;
                body[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
            }
            try {
                await Hub.api(url, { method: 'PUT', body });
                Hub.closeModal(modal);
                Hub.toast('Настройки сохранены');
                if (onSaved) onSaved();
            } catch (err) { error.textContent = err.message; }
        });
    }

    return { init };
})();
