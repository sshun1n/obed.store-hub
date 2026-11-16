// Админ-панель: автообновление СБИС (Лавка и Самокат) — тумблер, расписание, последний запуск
document.addEventListener('DOMContentLoaded', () => {
    const el = (id) => document.getElementById(id);
    const enabled = el('sabySyncEnabled');
    if (!enabled) return;
    const modal = el('sabySyncModal');
    const days = el('sabySyncDays');
    const interval = el('sabySyncInterval');
    const error = el('sabySyncError');
    let current = null;

    days.innerHTML = Hub.WEEKDAYS.map((name, day) =>
        `<button type="button" class="wd-btn${day >= 5 ? ' wd-wknd' : ''}" data-day="${day}" aria-pressed="false">${name}</button>`).join('');
    const setDays = (list) => days.querySelectorAll('.wd-btn').forEach((b) => {
        const on = list.includes(Number(b.dataset.day));
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', on);
    });

    function show(s) {
        current = s;
        enabled.checked = s.enabled;
        el('sabySyncState').textContent = s.enabled ? 'Включено' : 'Выключено';
        el('sabySyncSummary').textContent = s.summary;
        el('sabySyncSummary').classList.toggle('sync-auto-off', !s.enabled);
        el('sabySyncLast').textContent = s.last_at
            ? `Последний раз: ${Hub.fmtDate(s.last_at.slice(0, 10))} в ${s.last_at.slice(11, 16)}${s.last_result ? `, ${s.last_result}` : ''}`
            : '';
    }

    async function save(body) {
        show(await Hub.api('/api/v1/saby/sync-settings', { method: 'PUT', body }));
    }
    const plain = (s) => ({ enabled: s.enabled, interval_minutes: s.interval_minutes, start_time: s.start_time,
        end_time: s.end_time, weekdays: s.weekdays });

    enabled.addEventListener('change', async () => {
        const on = enabled.checked;
        enabled.disabled = true;
        try {
            await save({ ...plain(current), enabled: on });
            Hub.toast(on ? 'Автообновление СБИС включено' : 'Автообновление СБИС выключено');
        } catch (err) {
            enabled.checked = !on;
            Hub.toast(err.message, 'error');
        } finally { enabled.disabled = false; }
    });

    el('sabySyncConfigureBtn').addEventListener('click', () => {
        setDays(current.weekdays);
        el('sabySyncStart').value = current.start_time;
        el('sabySyncEnd').value = current.end_time;
        if (![...interval.options].some(o => o.value === String(current.interval_minutes))) {
            interval.add(new Option(`${current.interval_minutes} минут`, current.interval_minutes));
        }
        interval.value = String(current.interval_minutes);
        error.textContent = '';
        Hub.openModal(modal);
    });
    el('sabySyncCancel').addEventListener('click', () => Hub.closeModal(modal));
    days.addEventListener('click', (e) => {
        const b = e.target.closest('.wd-btn');
        if (!b) return;
        b.classList.toggle('on');
        b.setAttribute('aria-pressed', b.classList.contains('on'));
    });
    modal.querySelector('.sync-day-presets').addEventListener('click', (e) => {
        const chip = e.target.closest('[data-days]');
        if (chip) setDays(chip.dataset.days.split(',').map(Number));
    });
    el('sabySyncForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const weekdays = [...days.querySelectorAll('.wd-btn.on')].map(b => Number(b.dataset.day));
        if (!weekdays.length) { error.textContent = 'Выберите хотя бы один день'; return; }
        try {
            await save({ enabled: current.enabled, interval_minutes: Number(interval.value),
                start_time: el('sabySyncStart').value, end_time: el('sabySyncEnd').value, weekdays });
            Hub.closeModal(modal);
            Hub.toast('Расписание сохранено');
        } catch (err) { error.textContent = err.message; }
    });

    el('sabySyncConfigureBtn').disabled = true;
    enabled.disabled = true;
    Hub.api('/api/v1/saby/sync-settings')
        .then((s) => { show(s); el('sabySyncConfigureBtn').disabled = false; enabled.disabled = false; })
        .catch((err) => { el('sabySyncState').textContent = err.message; });
});
