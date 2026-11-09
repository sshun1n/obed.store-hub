/**
 * Графики Лавки на чистом SVG: прогноз с коридором и столбики «факт против прогноза».
 * Библиотеку не тянем: графиков два, и на виртуалке каждая зависимость на счету.
 * Подсказка — один общий блок, ездит за ближайшей точкой.
 *
 *   LavkaCharts.forecast(box, { history: [{label, value, tip}], forecast: [{label, value, low, high, tip}] });
 *   LavkaCharts.bars(box, { groups: [{label, actual, predicted, tip}] });
 */
window.LavkaCharts = (() => {
    const NS = 'http://www.w3.org/2000/svg';
    const fmt = (n) => new Intl.NumberFormat('ru-RU').format(Math.round(n || 0));

    function niceTop(max) {
        if (max <= 0) return { top: 10, step: 2 };
        const raw = max / 4;
        const pow = Math.pow(10, Math.floor(Math.log10(raw)));
        const step = [1, 2, 2.5, 5, 10].map(m => m * pow).find(s => s >= raw);
        return { top: Math.ceil(max / step) * step, step };
    }

    function svg(width, height) {
        const el = document.createElementNS(NS, 'svg');
        el.setAttribute('viewBox', `0 0 ${width} ${height}`);
        el.setAttribute('class', 'lc-svg');
        el.setAttribute('role', 'img');
        return el;
    }

    function add(parent, tag, attrs, text) {
        const node = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        if (text != null) node.textContent = text;
        parent.appendChild(node);
        return node;
    }

    // Подсказка рядом с точкой; по краям прижимается внутрь, чтобы не уехать за окно
    function tooltip(box) {
        let tip = box.querySelector('.lc-tip');
        if (!tip) {
            tip = document.createElement('div');
            tip.className = 'lc-tip';
            box.appendChild(tip);
        }
        return {
            show(html, x, y) {
                tip.innerHTML = html;
                tip.classList.add('show');
                const w = tip.offsetWidth, bw = box.clientWidth;
                tip.style.left = `${Math.min(Math.max(x - w / 2, 0), bw - w)}px`;
                tip.style.top = `${Math.max(y - tip.offsetHeight - 12, 0)}px`;
            },
            hide() { tip.classList.remove('show'); },
        };
    }

    function grid(root, { left, right, top, plotH, width, scale }) {
        for (let v = 0; v <= scale.top + 1e-9; v += scale.step) {
            const y = top + plotH - plotH * v / scale.top;
            add(root, 'line', { x1: left, x2: width - right, y1: y, y2: y, class: v === 0 ? 'lc-axis' : 'lc-grid' });
            add(root, 'text', { x: left - 8, y: y + 4, class: 'lc-tick', 'text-anchor': 'end' }, fmt(v));
        }
    }

    /** Линия факта за прошлые недели и прогноз дальше пунктиром с коридором. */
    function forecast(box, { history, forecast: future }) {
        box.querySelectorAll('svg').forEach(n => n.remove());
        const width = 920, height = 280;
        const left = 52, right = 18, top = 16, bottom = 34;
        const plotW = width - left - right, plotH = height - top - bottom;
        const points = [...history.map(p => ({ ...p, kind: 'fact' })), ...future.map(p => ({ ...p, kind: 'plan' }))];
        if (!points.length) return;
        const scale = niceTop(Math.max(...points.map(p => Math.max(p.value, p.high || 0))) * 1.05);
        // Дни прогноза шире истории: их всего несколько, и подписи иначе слипаются
        const FUTURE_GAP = 2.4;
        const offsets = [];
        points.forEach((p, i) => offsets.push(i === 0 ? 0 : offsets[i - 1] + (p.kind === 'plan' ? FUTURE_GAP : 1)));
        const span = offsets[offsets.length - 1] || 1;
        const X = (i) => left + plotW * offsets[i] / span;
        const Y = (v) => top + plotH - plotH * v / scale.top;

        const root = svg(width, height);
        grid(root, { left, right, top, plotH, width, scale });

        const split = history.length - 1;
        if (future.length && history.length) {
            // Полоса «это уже прогноз» за последней известной поставкой
            add(root, 'rect', { x: X(split), y: top, width: X(points.length - 1) - X(split) + right, height: plotH, class: 'lc-future' });
            add(root, 'text', { x: X(split) + 8, y: top + 12, class: 'lc-zone' }, 'прогноз');
        }

        if (future.length) {
            // Коридор начинается из последней фактической точки: там разброса уже нет
            const start = history.length ? [{ i: split, low: history[split].value, high: history[split].value }] : [];
            const band = [...start, ...future.map((p, k) => ({ i: history.length + k, low: p.low, high: p.high }))];
            const up = band.map(b => `${X(b.i)},${Y(b.high)}`).join(' ');
            const down = band.slice().reverse().map(b => `${X(b.i)},${Y(b.low)}`).join(' ');
            add(root, 'polygon', { points: `${up} ${down}`, class: 'lc-band' });
        }
        if (history.length > 1) {
            add(root, 'polyline', { points: history.map((p, i) => `${X(i)},${Y(p.value)}`).join(' '), class: 'lc-line' });
        }
        if (future.length) {
            const from = history.length ? [`${X(split)},${Y(history[split].value)}`] : [];
            add(root, 'polyline', { points: [...from, ...future.map((p, k) => `${X(history.length + k)},${Y(p.value)}`)].join(' '), class: 'lc-line lc-line-plan' });
        }

        // Подписи оси: у истории через одну-две, у прогноза все
        const every = Math.max(1, Math.ceil(history.length / 12));
        points.forEach((p, i) => {
            const isPlan = p.kind === 'plan';
            if (!isPlan && (history.length - 1 - i) % every) return;
            const anchor = i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle';
            add(root, 'text', { x: X(i), y: height - 12, class: `lc-tick${isPlan ? ' lc-tick-plan' : ''}`, 'text-anchor': anchor }, p.label);
        });

        const dots = points.map((p, i) => add(root, 'circle', {
            cx: X(i), cy: Y(p.value), r: p.kind === 'plan' ? 5 : 3, class: p.kind === 'plan' ? 'lc-dot lc-dot-plan' : 'lc-dot',
        }));
        const halo = add(root, 'circle', { r: 10, class: 'lc-halo' });
        const guide = add(root, 'line', { y1: top, y2: top + plotH, class: 'lc-guide' });
        box.insertBefore(root, box.firstChild);

        const tip = tooltip(box);
        root.addEventListener('mousemove', (e) => {
            const r = root.getBoundingClientRect();
            const x = (e.clientX - r.left) * width / r.width;
            let i = 0;
            points.forEach((_, k) => { if (Math.abs(X(k) - x) < Math.abs(X(i) - x)) i = k; });
            const p = points[i];
            halo.setAttribute('cx', X(i)); halo.setAttribute('cy', Y(p.value));
            halo.classList.toggle('lc-halo-plan', p.kind === 'plan');
            guide.setAttribute('x1', X(i)); guide.setAttribute('x2', X(i));
            root.classList.add('lc-hover');
            dots.forEach((d, k) => d.classList.toggle('active', k === i));
            tip.show(p.tip, X(i) * r.width / width, Y(p.value) * r.height / height);
        });
        root.addEventListener('mouseleave', () => { root.classList.remove('lc-hover'); dots.forEach(d => d.classList.remove('active')); tip.hide(); });
    }

    /** Столбики по дням: сколько заказали и что обещала модель, над парой — промах в процентах. */
    function bars(box, { groups }) {
        box.querySelectorAll('svg').forEach(n => n.remove());
        if (!groups.length) return;
        const width = 640, height = 260;
        const left = 52, right = 12, top = 30, bottom = 34;
        const plotW = width - left - right, plotH = height - top - bottom;
        const scale = niceTop(Math.max(...groups.flatMap(g => [g.actual, g.predicted || 0])) * 1.05);
        const slot = plotW / groups.length;
        const barW = Math.min(46, slot * 0.28);
        const Y = (v) => top + plotH - plotH * v / scale.top;

        const root = svg(width, height);
        grid(root, { left, right, top, plotH, width, scale });
        const tip = tooltip(box);
        groups.forEach((g, i) => {
            const cx = left + slot * i + slot / 2;
            const hit = add(root, 'g', { class: 'lc-pair' });
            add(hit, 'rect', { x: left + slot * i, y: top, width: slot, height: plotH, class: 'lc-hit' });
            add(hit, 'rect', { x: cx - barW - 2, y: Y(g.actual), width: barW, height: Math.max(plotH + top - Y(g.actual), 0), rx: 3, class: 'lc-bar' });
            if (g.predicted != null) {
                add(hit, 'rect', { x: cx + 2, y: Y(g.predicted), width: barW, height: Math.max(plotH + top - Y(g.predicted), 0), rx: 3, class: 'lc-bar lc-bar-plan' });
                const err = g.actual ? (g.predicted - g.actual) / g.actual * 100 : null;
                if (err != null) {
                    const big = Math.abs(err) >= 20;
                    add(hit, 'text', { x: cx, y: Math.min(Y(g.actual), Y(g.predicted)) - 8, class: `lc-err${big ? ' lc-err-big' : ''}`, 'text-anchor': 'middle' },
                        `${err > 0 ? '+' : '−'}${Math.abs(Math.round(err))} %`);
                }
            }
            add(root, 'text', { x: cx, y: height - 12, class: 'lc-tick', 'text-anchor': 'middle' }, g.label);
            hit.addEventListener('mouseenter', () => {
                const r = root.getBoundingClientRect();
                hit.classList.add('active');
                tip.show(g.tip, cx * r.width / width, Y(Math.max(g.actual, g.predicted || 0)) * r.height / height);
            });
            hit.addEventListener('mouseleave', () => { hit.classList.remove('active'); tip.hide(); });
        });
        box.insertBefore(root, box.firstChild);
    }

    return { forecast, bars, fmt };
})();
