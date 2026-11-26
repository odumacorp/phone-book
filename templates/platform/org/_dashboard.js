/* Organisation dashboard: cards, filters, menus, "New dashboard" (saved in this browser), charts in plain SVG. */
(() => {
  const D = window.DASHBOARD;
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    }
    kids.flat().forEach((c) => c != null && n.append(c.nodeType ? c : document.createTextNode(String(c))));
    return n;
  };
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs = {}) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
  const DASHES = ['', '6 4', '2 4', '10 4 2 4']; // secondary encoding: colour is never the only difference
  const nf = new Intl.NumberFormat('en-KE');
  const kes = (cents) => (cents == null ? '-' : `KES ${nf.format(Math.round(cents / 100))}`);
  const day = new Intl.DateTimeFormat('en-KE', { timeZone: 'Africa/Nairobi', day: 'numeric', month: 'short', year: 'numeric' });
  const fmtDate = (iso) => (iso ? day.format(new Date(`${iso}T00:00:00+03:00`)) : '-');

  // ---------------------------------------------------------------- layout (saved in this browser)
  const KEY = D.storage;
  const defaultCards = () => D.cards.map(([key, title]) => ({ uid: key, key, title }));
  const fresh = () => ({ active: 'default', dashboards: [{ id: 'default', name: 'Overview', cards: defaultCards() }] });
  let layout = fresh();
  if (D.isAdmin) {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (saved && Array.isArray(saved.dashboards) && saved.dashboards.length) layout = saved;
    } catch (e) { /* private window or blocked storage: use the default */ }
  }
  const allowed = new Set(D.cards.map(([k]) => k));
  layout.dashboards.forEach((d) => { d.cards = d.cards.filter((c) => allowed.has(c.key)); });
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(layout)); } catch (e) { /* ignore */ } };
  const current = () => layout.dashboards.find((d) => d.id === layout.active) || layout.dashboards[0];

  // ---------------------------------------------------------------- filters
  const range = $('#range'), from = $('#from'), to = $('#to'), fGroup = $('#f-group'), fChannel = $('#f-channel'), fOrg = $('#f-org');
  const params = () => {
    const p = new URLSearchParams({ range: range.value });
    if (range.value === 'custom') { if (!from.value || !to.value) return null; p.set('from', from.value); p.set('to', to.value); }
    if (fOrg && fOrg.value) p.set('tenant', fOrg.value);
    if (fGroup.value) p.set('group', fGroup.value);
    if (fChannel.value) p.set('channel', fChannel.value);
    return p;
  };
  const chips = () => {
    const box = $('#filter-chips');
    box.replaceChildren();
    const add = (label, clear) => box.append(el('span', { class: 'filter-chip' }, label,
      el('button', { type: 'button', 'aria-label': `Remove filter ${label}`, onclick: () => { clear(); refresh(); } }, '×')));
    if (fOrg && fOrg.value) add(fOrg.selectedOptions[0].text, () => { fOrg.value = ''; });
    if (fGroup.value) add(fGroup.selectedOptions[0].text, () => { fGroup.value = ''; });
    if (fChannel.value) add(fChannel.selectedOptions[0].text, () => { fChannel.value = ''; });
  };
  range.addEventListener('change', () => { $('#custom-dates').hidden = range.value !== 'custom'; if (range.value !== 'custom') refresh(); });
  [from, to].forEach((i) => i.addEventListener('change', () => params() && refresh()));
  [fGroup, fChannel, fOrg].filter(Boolean).forEach((s) => s.addEventListener('change', refresh));

  // ---------------------------------------------------------------- menus (keyboard friendly)
  const closeMenus = (except) => document.querySelectorAll('.menu').forEach((m) => {
    if (m !== except && !m.hidden) {
      m.hidden = true;
      const b = document.querySelector(`[data-menu="${m.id}"]`) || m.previousElementSibling;
      if (b) b.setAttribute('aria-expanded', 'false');
    }
  });
  const openMenu = (btn, menu) => {
    closeMenus(menu);
    const open = menu.hidden;
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open) { const first = menu.querySelector('[role=menuitem], select, a, button'); if (first) first.focus(); }
  };
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-menu]');
    if (btn) { openMenu(btn, document.getElementById(btn.dataset.menu)); return; }
    if (!e.target.closest('.menu')) closeMenus();
  });
  document.addEventListener('keydown', (e) => {
    const menu = e.target.closest && e.target.closest('.menu');
    if (e.key === 'Escape') {
      const open = [...document.querySelectorAll('.menu')].find((m) => !m.hidden);
      if (open) { closeMenus(); const b = document.querySelector(`[data-menu="${open.id}"]`); if (b) b.focus(); }
    }
    if (menu && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      const items = [...menu.querySelectorAll('[role=menuitem]')];
      const i = items.indexOf(document.activeElement);
      if (i >= 0) { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus(); }
    }
  });

  // ---------------------------------------------------------------- charts
  const niceMax = (v) => {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  };
  function smooth(points, lo, hi) { // Catmull-Rom -> cubic Bézier, kept inside the plot; null values break the line
    const clamp = (v) => Math.max(lo, Math.min(hi, v));
    let d = '', run = [];
    const flush = () => {
      if (run.length === 1) d += `M${run[0][0]},${run[0][1]}h0.1`;
      for (let i = 0; i < run.length - 1 && run.length > 1; i++) {
        const [p0, p1, p2, p3] = [run[i - 1] || run[i], run[i], run[i + 1], run[i + 2] || run[i + 1]];
        if (i === 0) d += `M${p1[0]},${p1[1]}`;
        const c1 = [p1[0] + (p2[0] - p0[0]) / 6, clamp(p1[1] + (p2[1] - p0[1]) / 6)];
        const c2 = [p2[0] - (p3[0] - p1[0]) / 6, clamp(p2[1] - (p3[1] - p1[1]) / 6)];
        d += `C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
      }
      run = [];
    };
    points.forEach((p) => (p ? run.push(p) : flush()));
    flush();
    return d;
  }
  function lineChart(card) {
    // drawn at the card's real width, so the axis labels stay readable on phones
    const cardWidth = (grid.firstElementChild && grid.firstElementChild.clientWidth) || 600;
    const W = Math.round(Math.max(300, Math.min(640, cardWidth - 40))), H = W < 420 ? 190 : 210, L = 40, R = 10, T = 10, B = 26;
    const n = card.labels.length, series = card.series;
    const peak = Math.max(0, ...series.flatMap((s) => s.values.filter((v) => v != null)));
    const max = card.data.max || niceMax(card.data.decimals ? Math.max(0.05, peak) : Math.max(1, peak));
    const x = (i) => L + (n <= 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
    const y = (v) => T + (H - T - B) * (1 - v / max);
    const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', tabindex: '0',
      'aria-label': `${card.title}: line chart. Use the left and right arrow keys to read each point.` });
    for (let k = 0; k <= 4; k++) {
      const v = (max * k) / 4, yy = y(v);
      root.append(svg('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: k ? 'grid' : 'axis' }));
      const t = svg('text', { x: L - 6, y: yy + 4, class: 'tick', 'text-anchor': 'end' });
      t.textContent = card.unit === '%' ? `${Math.round(v)}%` : card.data.decimals && max < 5 ? v.toFixed(2).replace(/\.?0+$/, '') : nf.format(Math.round(v));
      root.append(t);
    }
    const step = Math.max(1, Math.ceil(n / 6));
    card.labels.forEach((lab, i) => {
      if (i % step && i !== n - 1) return;
      if (i !== n - 1 && n - 1 - i < step / 2) return;
      const t = svg('text', { x: x(i), y: H - 6, class: 'tick', 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' });
      t.textContent = lab;
      root.append(t);
    });
    series.forEach((s, si) => root.append(svg('path', { d: smooth(s.values.map((v, i) => (v == null ? null : [x(i), y(v)])), T, H - B),
      class: 'line', stroke: COLORS[si % 4], 'stroke-dasharray': DASHES[si % 4] })));
    // hover / keyboard: crosshair and a tooltip with every series at that point
    const cross = svg('line', { y1: T, y2: H - B, class: 'cross', visibility: 'hidden' });
    const dots = series.map((s, si) => svg('circle', { r: 4, fill: COLORS[si % 4], class: 'dot', visibility: 'hidden' }));
    root.append(cross, ...dots);
    const tip = el('div', { class: 'tip', hidden: true, role: 'status' });
    let at = -1;
    const show = (i) => {
      at = Math.max(0, Math.min(n - 1, i));
      cross.setAttribute('x1', x(at)); cross.setAttribute('x2', x(at)); cross.setAttribute('visibility', 'visible');
      series.forEach((s, si) => {
        const v = s.values[at];
        dots[si].setAttribute('visibility', v == null ? 'hidden' : 'visible');
        if (v != null) { dots[si].setAttribute('cx', x(at)); dots[si].setAttribute('cy', y(v)); }
      });
      tip.replaceChildren(el('b', { text: card.labels[at] }), ...series.map((s, si) => el('div', {},
        el('span', { class: 'sw', style: `background:${COLORS[si % 4]}` }), ` ${s.name}: `,
        el('b', { text: s.values[at] == null ? '–' : card.unit === '%' ? `${s.values[at]}%` : nf.format(s.values[at]) }))));
      tip.hidden = false;
      const pct = x(at) / W;
      tip.style.left = `${Math.min(70, Math.max(0, pct * 100 - 15))}%`;
    };
    const hide = () => { cross.setAttribute('visibility', 'hidden'); dots.forEach((d) => d.setAttribute('visibility', 'hidden')); tip.hidden = true; };
    root.addEventListener('mousemove', (e) => {
      const r = root.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W;
      show(Math.round(((px - L) / (W - L - R)) * (n - 1)));
    });
    root.addEventListener('mouseleave', hide);
    root.addEventListener('blur', hide);
    root.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); show((at < 0 ? 0 : at) + (e.key === 'ArrowRight' ? 1 : -1)); }
    });
    const legend = el('div', { class: 'legend' }, series.map((s, si) => el('span', {},
      el('i', { class: 'key', style: `border-top:3px ${DASHES[si % 4] ? 'dashed' : 'solid'} ${COLORS[si % 4]}` }), s.name)));
    return el('div', { class: 'chart-wrap' }, root, tip, legend);
  }
  function donut(card) {
    const vals = card.data.values, labels = card.data.labels, total = vals.reduce((a, b) => a + b, 0);
    const R = 70, r = 46, C = 90;
    const root = svg('svg', { viewBox: '0 0 180 180', class: 'donut', role: 'img',
      'aria-label': `${card.title}: ` + labels.map((l, i) => `${l} ${total ? Math.round((vals[i] * 100) / total) : 0}%`).join(', ') });
    let a0 = -Math.PI / 2;
    vals.forEach((v, i) => {
      if (!v) return;
      const a1 = a0 + (2 * Math.PI * v) / total - (vals.filter(Boolean).length > 1 ? 0.02 : 0);
      const big = a1 - a0 > Math.PI ? 1 : 0;
      const p = (rad, a) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`;
      const d = v === total
        ? `M${C},${C - R}A${R},${R} 0 1 1 ${C - 0.01},${C - R}L${C - 0.01},${C - r}A${r},${r} 0 1 0 ${C},${C - r}Z`
        : `M${p(R, a0)}A${R},${R} 0 ${big} 1 ${p(R, a1)}L${p(r, a1)}A${r},${r} 0 ${big} 0 ${p(r, a0)}Z`;
      const seg = svg('path', { d, fill: COLORS[i % 4] });
      const title = svg('title'); title.textContent = `${labels[i]}: ${nf.format(v)} (${Math.round((v * 100) / total)}%)`;
      seg.append(title);
      root.append(seg);
      a0 = a1 + (vals.filter(Boolean).length > 1 ? 0.02 : 0);
    });
    const t = svg('text', { x: C, y: C + 6, 'text-anchor': 'middle', class: 'donut-total' }); t.textContent = nf.format(total);
    root.append(t);
    const legend = el('ul', { class: 'donut-legend' }, labels.map((l, i) => el('li', {},
      el('span', { class: 'sw', style: `background:${COLORS[i % 4]}` }), l,
      el('b', { text: `${total ? Math.round((vals[i] * 100) / total) : 0}%` }), el('span', { class: 'muted', text: nf.format(vals[i]) }))));
    return el('div', { class: 'donut-wrap' }, root, legend);
  }
  function kpis(card) {
    return el('div', { class: 'kpi-row' }, card.data.tiles.map((t) => el('div', { class: 'kpi-tile' },
      el('b', { text: nf.format(t.value) }), el('span', { class: 'kpi-label', text: t.label }),
      el('span', { class: 'kpi-delta' }, t.dir === 'same' ? '= no change' : t.pct == null ? `${t.dir === 'up' ? '▲' : '▼'} from 0`
        : `${t.dir === 'up' ? '▲' : '▼'} ${Math.abs(t.pct)}% vs previous period`))));
  }
  function table(card) {
    return el('table', { class: 'dash-table' },
      el('thead', {}, el('tr', {}, el('th', { text: '#' }), el('th', { text: card.data.first || 'Group' }), el('th', { class: 'num', text: 'Members' }),
        el('th', { class: 'num', text: 'Lookups' }), el('th', { text: 'Notice read rate' }))),
      el('tbody', {}, card.data.rows.map((r, i) => el('tr', {}, el('td', { class: 'muted', text: i + 1 }),
        el('td', {}, card.data.link && r.id ? el('a', { href: card.data.link.replace('{id}', r.id), text: r.name }) : r.name),
        el('td', { class: 'num', text: nf.format(r.members) }), el('td', { class: 'num', text: nf.format(r.lookups) }),
        el('td', {}, r.read_rate == null ? el('span', { class: 'muted', text: 'no notices yet' })
          : el('span', { class: 'rate' }, el('span', { class: 'rate-bar' }, el('i', { style: `width:${r.read_rate}%` })), `${r.read_rate}%`))))));
  }
  function billing(card) {
    const b = card.data;
    const status = { paid: ['on', 'Paid'], due: ['warn', 'Due'], overdue: ['bad', 'Overdue'], trial: ['off', 'Free trial'] }[b.status] || ['off', b.status];
    return el('div', { class: 'billing' },
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Plan' }), el('b', { text: b.plan })),
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Amount' }), el('b', { class: 'bill-amount', text: `${kes(b.amount_cents)}${b.per ? ` / ${b.per}` : ''}` })),
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Members' }), el('b', { text: nf.format(b.members) })),
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Next M-Pesa payment' }), el('b', { text: fmtDate(b.next_payment) })),
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Status' }), el('span', { class: `pill ${status[0]}`, text: status[1] })),
      b.last_payment ? el('p', { class: 'muted', style: 'margin:10px 0 0' },
        `Last payment ${kes(b.last_payment.amount_cents)} on ${fmtDate(b.last_payment.paid_on)}, M-Pesa ref ${b.last_payment.mpesa_ref}`) : null);
  }
  function revenue(card) {
    const d = card.data;
    const pill = { paid: 'on', due: 'warn', overdue: 'bad', trial: 'off' };
    return el('div', { class: 'billing' },
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Monthly recurring revenue' }), el('b', { class: 'bill-amount', text: kes(d.mrr_cents) })),
      el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: 'Paying organisations' }), el('b', { text: nf.format(d.paying) })),
      d.by_plan.map(([name, cents]) => el('div', { class: 'bill-row' }, el('span', { class: 'muted', text: name }), el('b', { text: kes(cents) }))),
      d.attention.length ? el('h3', { style: 'margin:14px 0 4px;font-size:14px', text: 'Needs a decision' }) : null,
      d.attention.map((o) => el('div', { class: 'bill-row' },
        D.orgLink ? el('a', { href: D.orgLink.replace('{id}', o.id), text: o.name }) : el('span', { text: o.name }),
        el('span', {}, el('span', { class: `pill ${pill[o.status] || 'off'}`, text: o.status }), ' ', kes(o.amount_cents)))));
  }
  function asTable(card) { // the same numbers as a table (for screen readers and colour-blind readers)
    const rows = csvRows(card);
    return el('table', { class: 'dash-table' }, el('thead', {}, el('tr', {}, rows[0].map((h) => el('th', { text: h })))),
      el('tbody', {}, rows.slice(1, 400).map((r) => el('tr', {}, r.map((c) => el('td', { text: c }))))));
  }
  function csvRows(card) {
    if (card.kind === 'line' && card.data.per_minute) { // failures: every minute in the file
      const names = Object.keys(card.data.per_minute), first = card.labels[0];
      const [h, m] = first.split(':').map(Number);
      return [['Minute', ...names], ...card.data.per_minute[names[0]].map((_v, i) => {
        const t = (h * 60 + m + i) % 1440;
        return [`${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`, ...names.map((n) => card.data.per_minute[n][i])];
      })];
    }
    if (card.kind === 'line') return [['Period', ...card.series.map((s) => s.name)], ...card.labels.map((l, i) => [l, ...card.series.map((s) => s.values[i] ?? '')])];
    if (card.kind === 'donut') return [['Channel', 'Lookups'], ...card.data.labels.map((l, i) => [l, card.data.values[i]])];
    if (card.kind === 'table') return [['Group', 'Members', 'Lookups', 'Notice read rate %'], ...card.data.rows.map((r) => [r.name, r.members, r.lookups, r.read_rate ?? ''])];
    if (card.kind === 'kpis') return [['Measure', 'Value', 'Previous period', 'Change %'], ...card.data.tiles.map((t) => [t.label, t.value, t.before, t.pct ?? ''])];
    const b = card.data;
    if (card.kind === 'revenue') return [['Plan', 'Monthly revenue (KES)'], ...b.by_plan.map(([n, c]) => [n, c / 100]), ['Total', b.mrr_cents / 100]];
    return [['Plan', 'Amount (KES)', 'Per', 'Members', 'Next payment', 'Status'], [b.plan, b.amount_cents == null ? '' : b.amount_cents / 100, b.per || '', b.members, b.next_payment, b.status]];
  }
  function exportCsv(card, title) {
    const text = csvRows(card).map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const a = el('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: `${title.replace(/[^\w]+/g, '-').toLowerCase()}.csv` });
    document.body.append(a); a.click(); a.remove();
  }

  // ---------------------------------------------------------------- cards
  const grid = $('#dash-grid');
  let seq = 0;
  function renderCard(spec) {
    const id = `card-${spec.uid}`;
    const body = el('div', { class: 'card-body' });
    const sub = el('p', { class: 'card-sub', text: ' ' });
    const menuId = `${id}-menu`;
    const items = [];
    if (D.isAdmin) items.push(['Edit', () => edit(spec)], ['Duplicate', () => duplicate(spec)]);
    items.push(['View as table', () => { spec._table = !spec._table; load(spec, node); }], ['Export CSV', () => node._data && exportCsv(node._data, spec.title)]);
    if (D.isAdmin) items.push(['Remove', () => remove(spec)]);
    const menu = el('div', { class: 'menu right', id: menuId, role: 'menu', hidden: true },
      items.map(([label, fn]) => el('button', { type: 'button', role: 'menuitem', onclick: () => { closeMenus(); fn(); } }, label)));
    const node = el('section', { class: 'dash-card card', id, 'aria-labelledby': `${id}-t` },
      el('header', { class: 'card-head' }, el('div', {}, el('h2', { id: `${id}-t`, text: spec.title }), sub),
        el('div', { class: 'menu-wrap' }, el('button', { type: 'button', class: 'icon-btn', 'data-menu': menuId,
          'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-label': `Options for ${spec.title}` }, '⋮'), menu)),
      body);
    node._sub = sub; node._body = body;
    load(spec, node);
    return node;
  }
  async function load(spec, node) {
    const p = params();
    if (!p) return;
    const mine = ++seq;
    node._seq = mine;
    node._body.replaceChildren(el('div', { class: 'skeleton', 'aria-label': 'Loading', role: 'progressbar' }));
    try {
      const res = await fetch(D.cardUrl + spec.key + '?' + p.toString(), { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      const card = await res.json().catch(() => ({}));
      if (node._seq !== mine) return;
      if (!res.ok) throw new Error(card.error || 'This card could not load.');
      node._data = card;
      node._sub.textContent = card.subtitle + (card.note ? ` · ${card.note}` : '');
      if (card.source === 'demo') node._sub.append(' ', el('span', { class: 'pill warn', text: 'demo data' }));
      if (card.empty) {
        node._body.replaceChildren(el('div', { class: 'empty' }, el('b', { text: 'Nothing yet for this period' }),
          card.key === 'delivery' ? 'Notices and reminders you send are counted from now on.'
            : card.key === 'channel_mix' ? 'Each time a member opens their contacts, it is counted here.'
            : 'Members using USSD are counted from now on. Try a longer period.'));
        return;
      }
      const view = spec._table && !['billing', 'revenue'].includes(card.kind) ? asTable(card)
        : { line: lineChart, donut, kpis, table, billing, revenue }[card.kind](card);
      node._body.replaceChildren(view);
    } catch (e) {
      if (node._seq !== mine) return;
      node._body.replaceChildren(el('div', { class: 'empty' }, el('b', { text: 'This card could not load' }), e.message || '',
        el('div', {}, el('button', { type: 'button', class: 'secondary small', style: 'margin-top:8px', onclick: () => load(spec, node) }, 'Try again'))));
    }
  }
  function refresh() {
    chips();
    grid.replaceChildren();
    const cards = current().cards;
    if (!cards.length) {
      grid.append(el('div', { class: 'empty card', style: 'grid-column:1/-1' }, el('b', { text: 'This dashboard has no cards' }),
        D.isAdmin ? 'Use ⋮ › Reset, or create a new dashboard.' : ''));
    }
    cards.forEach((spec) => grid.append(renderCard(spec)));
    tabs();
  }
  function tabs() {
    const box = $('#dash-tabs');
    box.replaceChildren(...layout.dashboards.map((d) => el('button', { type: 'button', role: 'tab', class: `dtab ${d.id === layout.active ? 'on' : ''}`,
      'aria-selected': String(d.id === layout.active), onclick: () => { layout.active = d.id; save(); refresh(); } }, d.name)));
    box.hidden = layout.dashboards.length < 2;
  }
  const edit = (spec) => {
    const name = prompt('Card title', spec.title);
    if (name && name.trim()) { spec.title = name.trim().slice(0, 60); save(); refresh(); }
  };
  const duplicate = (spec) => {
    const list = current().cards, i = list.indexOf(spec);
    list.splice(i + 1, 0, { ...spec, uid: `${spec.key}-${Date.now()}`, title: `${spec.title} (copy)`, _table: false });
    save(); refresh();
  };
  const remove = (spec) => {
    if (!confirm(`Remove "${spec.title}" from this dashboard?`)) return;
    current().cards = current().cards.filter((c) => c !== spec);
    save(); refresh();
  };

  // ---------------------------------------------------------------- page menu and "New dashboard"
  document.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => {
    closeMenus();
    const d = current();
    if (b.dataset.page === 'rename') { const n = prompt('Dashboard name', d.name); if (n && n.trim()) d.name = n.trim().slice(0, 40); }
    if (b.dataset.page === 'reset' && confirm('Put the default cards back on this dashboard?')) d.cards = defaultCards();
    if (b.dataset.page === 'delete') {
      if (layout.dashboards.length === 1) { alert('This is your only dashboard. Reset it instead.'); return; }
      if (!confirm(`Delete "${d.name}"?`)) return;
      layout.dashboards = layout.dashboards.filter((x) => x !== d);
      layout.active = layout.dashboards[0].id;
    }
    save(); refresh();
  }));
  const dialog = $('#dash-dialog');
  if (dialog) {
    $('#new-dash').addEventListener('click', () => { $('#dash-name').value = ''; dialog.showModal(); $('#dash-name').focus(); });
    $('#dash-cancel').addEventListener('click', () => dialog.close());
    $('#dash-form').addEventListener('submit', (e) => {
      const name = $('#dash-name').value.trim();
      const picked = [...dialog.querySelectorAll('input[name=cards]:checked')].map((i) => i.value);
      if (!name || !picked.length) { e.preventDefault(); alert(!name ? 'Give the dashboard a name.' : 'Choose at least one card.'); return; }
      const titles = Object.fromEntries(D.cards);
      const id = `d${Date.now()}`;
      layout.dashboards.push({ id, name: name.slice(0, 40), cards: picked.map((k) => ({ uid: `${k}-${id}`, key: k, title: titles[k] })) });
      layout.active = id;
      save(); refresh();
    });
  }

  refresh();
  // "Failed sessions in real time": reload that card every minute while the page is open and visible
  setInterval(() => {
    if (document.hidden) return;
    current().cards.filter((c) => c.key === 'failures').forEach((spec) => {
      const node = document.getElementById(`card-${spec.uid}`);
      if (node) load(spec, node);
    });
  }, 60000);
})();
