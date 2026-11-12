/**
 * Подпись документов СБИС Рутокеном через «КриптоПро ЭЦП Browser plug-in».
 * Перенесено из ka-webapp (initSignBox). Ключ не извлекается, поэтому подписывает
 * браузер: сервер готовит документы, браузер подписывает файлы, сервер отправляет подписи.
 * Плагин работает только на https:// и localhost.
 *
 *   await SabySign.sign({ day, kind: 'transport' | 'waybill' | 'upd', orders: [...], title })  // true — отправлено
 */
window.SabySign = (() => {
    const REMEMBER = 'obed.sign.thumbprint';
    const BASE64_TO_BINARY = 1;
    const CADES_BES = 1;
    const el = (id) => document.getElementById(id);

    const reason = (err) => {
        try {
            const text = window.cadesplugin?.getLastError?.(err);
            if (text) return text;
        } catch { /* не у всех ошибок есть расшифровка */ }
        return err?.message || String(err);
    };

    const loadScript = () => new Promise((resolve, reject) => {
        if (window.cadesplugin) return resolve();
        const script = document.createElement('script');
        script.src = '/static/cadesplugin_api.js?v=1.19.0';
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('не загрузился скрипт КриптоПро'));
        document.head.appendChild(script);
    });

    // cadesplugin — обещание готовности с методами на нём самом: из async-функции его
    // не возвращаем, иначе вместо объекта придёт пустое значение. Только ждём
    async function ready() {
        if (!window.isSecureContext) {
            throw new Error('подпись работает только по https:// или на localhost, а страница открыта по http');
        }
        await loadScript();
        const timeout = new Promise((_, reject) => setTimeout(
            () => reject(new Error('плагин КриптоПро не ответил — проверьте, что расширение включено в браузере')), 20000));
        await Promise.race([window.cadesplugin, timeout]);
    }

    async function listCerts(plugin) {
        const store = await plugin.CreateObjectAsync('CAdESCOM.Store');
        await store.Open(2, 'My', 2);
        const all = await store.Certificates;
        const count = await all.Count;
        const now = new Date();
        const found = [];
        for (let i = 1; i <= count; i += 1) {
            const cert = await all.Item(i);
            const validTo = new Date(await cert.ValidToDate);
            if (validTo < now || !(await cert.HasPrivateKey())) continue;
            const subject = await cert.SubjectName;
            const name = ((subject.match(/CN=("[^"]*"|[^,]+)/) || [])[1] || subject).replace(/"/g, '');
            found.push({ cert, name, validTo, thumbprint: (await cert.Thumbprint).toUpperCase() });
        }
        return found;
    }

    async function signFile(plugin, cert, base64) {
        const signer = await plugin.CreateObjectAsync('CAdESCOM.CPSigner');
        await signer.propset_Certificate(cert);
        const data = await plugin.CreateObjectAsync('CAdESCOM.CadesSignedData');
        await data.propset_ContentEncoding(plugin.CADESCOM_BASE64_TO_BINARY ?? BASE64_TO_BINARY);
        await data.propset_Content(base64);
        // Отсоединённая CAdES-BES — такую СБИС и ждёт
        const signature = await data.SignCades(signer, plugin.CADESCOM_CADES_BES ?? CADES_BES, true);
        return signature.replace(/\s+/g, '');
    }

    function sign({ day, kind, orders = [], title, what = '' }) {
        const modal = el('signModal');
        const status = el('signStatus');
        const select = el('signCert');
        const go = el('signGo');
        const cancel = el('signCancel');
        let certs = [];
        let busy = false;
        const say = (text, bad = false) => { status.textContent = text; status.classList.toggle('saby-bad', bad); };

        el('signTitle').textContent = title;
        el('signWhat').textContent = what;
        el('signCertBox').hidden = true;
        go.disabled = true;
        cancel.disabled = false;
        Hub.openModal(modal);

        return new Promise((resolve) => {
            const finish = (value) => {
                go.onclick = null;
                cancel.onclick = null;
                Hub.closeModal(modal);
                resolve(value);
            };
            cancel.onclick = () => { if (!busy) finish(false); };

            (async () => {
                say('Подключаюсь к плагину КриптоПро…');
                let pluginOk = false;
                try {
                    await ready();
                    pluginOk = true;
                    certs = await listCerts(window.cadesplugin);
                    if (!certs.length) throw new Error('не нашлось действующих сертификатов с закрытым ключом — вставьте Рутокен и откройте окно снова');
                    let remembered = '';
                    try { remembered = localStorage.getItem(REMEMBER) || ''; } catch { /* недоступно */ }
                    select.innerHTML = certs.map(c => `<option value="${c.thumbprint}"${c.thumbprint === remembered ? ' selected' : ''}>${Hub.esc(c.name)} — до ${c.validTo.toLocaleDateString('ru-RU')}</option>`).join('');
                    el('signCertBox').hidden = false;
                    go.disabled = false;
                    say(certs.length > 1 ? 'Выберите сертификат.' : '');
                } catch (err) {
                    const hint = pluginOk ? '' : ' Проверьте, что установлен «КриптоПро ЭЦП Browser plug-in», расширение включено и сайту разрешён доступ.';
                    say(`Не получилось: ${reason(err)}.${hint}`.replace('..', '.'), true);
                }
            })();

            go.onclick = async () => {
                const chosen = certs.find(c => c.thumbprint === select.value);
                if (!chosen || busy) return;
                busy = true;
                go.disabled = true;
                cancel.disabled = true;
                try {
                    say('Готовлю документы в СБИС…');
                    const prepared = await Hub.api('/api/v1/saby/sign/prepare', { method: 'POST', body: { day, kind, orders, thumbprint: chosen.thumbprint } });
                    const files = prepared.documents.flatMap(d => d.files);
                    const skipped = Object.entries(prepared.skipped || {});
                    const note = skipped.length ? ` Пропущено ${skipped.length}: ${skipped.map(([n, w]) => `${n} — ${w}`).join('; ')}.` : '';
                    const signatures = [];
                    for (const [i, file] of files.entries()) {
                        say(`Подписываю ${i + 1} из ${files.length} — КриптоПро может спросить пин-код Рутокена…${note}`);
                        signatures.push({ id: file.id, signature: await signFile(window.cadesplugin, chosen.cert, file.data) });
                    }
                    say(`Отправляю…${note}`);
                    const sent = await Hub.api('/api/v1/saby/sign/send', { method: 'POST', body: { token: prepared.token, signatures } });
                    try { localStorage.setItem(REMEMBER, chosen.thumbprint); } catch { /* не критично */ }
                    const errors = Object.entries(sent.errors || {});
                    Hub.toast(`Отправлено: ${sent.sent.length}${errors.length ? `, ошибок: ${errors.length}` : ''}`, errors.length ? 'error' : 'ok');
                    busy = false;
                    finish(true);
                } catch (err) {
                    say(`Не получилось: ${reason(err)}`, true);
                    busy = false;
                    go.disabled = false;
                    cancel.disabled = false;
                }
            };
        });
    }

    return { sign };
})();
