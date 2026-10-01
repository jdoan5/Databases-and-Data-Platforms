// Tagline dashboard: renders data/snapshot.json (written by make dashboard-snapshot).
//
// No framework and no chart library: the charts are SVG built here, so they take the page's colour tokens in light
// and dark mode, every mark can be reached from the keyboard, and the page makes no request but its own files.
// Every number on the page comes from the snapshot; the figures from the project's write-ups sit in its `cited`
// section and are shown with a link to the document they come from. Text from the snapshot goes in with
// textContent only.

const SVGNS = 'http://www.w3.org/2000/svg';
const MODEL_LABEL = {
  last_click: 'Last click', last_non_direct: 'Last non-direct', first_click: 'First click',
  linear: 'Linear', time_decay: 'Time decay', position_based: 'Position-based',
};
const MODEL_SHORT = {
  last_click: 'Last', last_non_direct: 'Last n-d', first_click: 'First', linear: 'Lin.', time_decay: 'Decay', position_based: 'Pos.',
};
const SOURCE_SERIES = { ga4_sample: 's1', tagline_site: 's2' };
const STATUS = {
  pass: { label: 'pass', cls: 'good' },
  expected: { label: 'expected (a documented quirk of the source)', cls: 'expected' },
  violation: { label: 'violation', cls: 'critical' },
};

// --- DOM helpers ---------------------------------------------------------------------------------------------------

function add(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function setAttrs(node, attrs, isSvg) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'class' && !isSvg) node.className = v;
    else if (k === 'style' && typeof v === 'object') Object.entries(v).forEach(([p, val]) => node.style.setProperty(p, val));
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  return node;
}

function h(tag, attrs, ...children) {
  return add(setAttrs(document.createElement(tag), attrs, false), children);
}

function sv(tag, attrs, ...children) {
  return add(setAttrs(document.createElementNS(SVGNS, tag), attrs, true), children);
}

const $ = (sel) => document.querySelector(sel);

// --- formatting ----------------------------------------------------------------------------------------------------

const nf = (opts) => new Intl.NumberFormat('en-US', opts);
const int = (n) => nf({ maximumFractionDigits: 0 }).format(n);
const usd = (n, digits = 0) => nf({ style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
const usdAuto = (n) => usd(n, Math.abs(n) < 1 ? (String(n).split('.')[1] || '').length || 2 : 2);
const pct = (x, d = 1) => (x === null || x === undefined ? '–' : `${(x * 100).toFixed(d)}%`);
const pts = (x) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)} points`;
const compactInt = (v) => (v >= 1000 ? `${nf({ maximumFractionDigits: 1 }).format(v / 1000)}k` : int(v));
const compactUsd = (v) => (v >= 1000 ? `$${nf({ maximumFractionDigits: 1 }).format(v / 1000)}k` : usd(v));
const utcDate = (iso) => new Date(`${iso}T00:00:00Z`);
const fmtDate = (iso) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(utcDate(iso));
const fmtDay = (iso) => new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(utcDate(iso));
const fmtRange = (a, b) => (a === b ? fmtDate(a) : `${fmtDate(a)} to ${fmtDate(b)}`);
const plural = (n, one, many = `${one}s`) => `${int(n)} ${n === 1 ? one : many}`;
const bytesMiB = (b) => `${nf({ maximumFractionDigits: 0 }).format(b / 1024 ** 2)} MiB`;

function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw);
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks = [];
  for (let i = 0; i * step <= top + step * 1e-6; i++) ticks.push(+(i * step).toFixed(10));
  return ticks;
}

function hbarPath(x, y, w, hgt, r = 4) {
  if (!(w > 0)) return '';
  const rr = Math.min(r, w, hgt / 2);
  return `M${x},${y}H${x + w - rr}A${rr},${rr} 0 0 1 ${x + w},${y + rr}V${y + hgt - rr}A${rr},${rr} 0 0 1 ${x + w - rr},${y + hgt}H${x}Z`;
}

function vbarPath(x, y, w, hgt, r = 4) {
  if (!(hgt > 0)) return '';
  const rr = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + rr}A${rr},${rr} 0 0 1 ${x + rr},${y}H${x + w - rr}A${rr},${rr} 0 0 1 ${x + w},${y + rr}V${y + hgt}Z`;
}

function statusIcon(kind) {
  const cls = `fill-${STATUS[kind]?.cls || kind}`;
  const svg = sv('svg', { class: 'status-icon', viewBox: '0 0 14 14', 'aria-hidden': 'true' });
  if (kind === 'pass') {
    svg.append(sv('circle', { cx: 7, cy: 7, r: 7, class: cls }), sv('path', { d: 'M3.8 7.2l2.1 2.1 4.3-4.5', fill: 'none', stroke: '#fff', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  } else if (kind === 'violation' || kind === 'critical') {
    svg.append(sv('path', { d: 'M7 0.6L13.4 7 7 13.4 0.6 7z', class: 'fill-critical' }), sv('path', { d: 'M7 3.8v3.9M7 9.6v.3', stroke: '#fff', 'stroke-width': 1.8, 'stroke-linecap': 'round' }));
  } else if (kind === 'warning') {
    svg.append(sv('path', { d: 'M7 1L13.3 12.6H0.7z', class: 'fill-warning' }), sv('path', { d: 'M7 5.2v3.4M7 10.4v.2', stroke: '#1b2a2f', 'stroke-width': 1.6, 'stroke-linecap': 'round' }));
  } else {
    svg.append(sv('circle', { cx: 7, cy: 7, r: 7, class: cls }), sv('path', { d: 'M4 7h6', stroke: '#fff', 'stroke-width': 1.8, 'stroke-linecap': 'round' }));
  }
  return svg;
}

// The alert strip's marks on narrow screens: a tall bar for a critical day, a short one for a day of warnings only.
function barIcon(kind) {
  const svg = sv('svg', { class: 'status-icon', viewBox: '0 0 14 14', 'aria-hidden': 'true' });
  svg.append(kind === 'critical'
    ? sv('path', { d: 'M4.5 0.5h5v13h-5z', class: 'fill-critical' })
    : sv('path', { d: 'M4.5 7.5h5v6h-5z', class: 'fill-warning edge' }));
  return svg;
}

function sourceLink(src, prefix = 'Source: ') {
  if (!src) return null;
  return h('p', { class: 'source-link' }, prefix, h('a', { href: src.url, text: src.label }));
}

function metricLabel(metric, rule) {
  const kpi = {
    sessions: 'sessions', engaged_session_rate: 'engaged-session rate', conversion_rate: 'conversion rate',
    add_to_cart_rate: 'add-to-cart rate', checkout_to_purchase_rate: 'checkout-to-purchase rate', orders: 'orders',
    revenue_usd: 'revenue', aov_usd: 'average order value', cookieless_order_share: 'cookieless-order share',
    consent_accept_share: 'consent-accept share',
  };
  let text;
  if (metric.startsWith('tag_health.')) {
    const [, event, ...rest] = metric.split('.');
    const [kind, field] = rest.join('.').split(':');
    const what = {
      required: `${field} missing`, format: `${field} malformed`, value_math: 'value ≠ Σ price × quantity',
      pii: 'email-like string', attribution: `session source: ${field}`,
      dedupe: field === 'duplicate_transaction_id' ? 'transaction_id repeated' : 'transaction_id on two devices',
    }[kind] || rest.join('.');
    text = `${event}: ${what}`;
  } else if (metric.startsWith('events.')) {
    text = `${metric.slice(7)} stopped firing`;
  } else if (metric === 'day') {
    text = 'no data for the day';
  } else {
    text = kpi[metric] || metric;
  }
  return rule === 'floor' ? `${text} (below the floor)` : text;
}

// A tooltip that lives in a chart container (position: relative) and survives redraws.
function makeTooltip(container) {
  const el = h('div', { class: 'tooltip', hidden: true, 'aria-hidden': 'true' });
  return {
    el,
    show(nodes, x, y) {
      el.replaceChildren(...nodes);
      el.hidden = false;
      const cw = container.clientWidth;
      const tw = el.offsetWidth;
      const th = el.offsetHeight;
      let left = x + 14;
      if (left + tw > cw) left = Math.max(0, Math.min(cw - tw, x - tw - 14));
      let top = y - th - 12;
      if (top < -8) top = y + 16;
      el.style.left = `${left}px`;
      el.style.top = `${top}px`;
    },
    hide() { el.hidden = true; },
  };
}

// Draw now and again whenever the container's width changes.
function responsive(container, draw) {
  let last = 0;
  const redraw = (w) => {
    if (!w || w === last) return;
    last = w;
    draw(w);
  };
  new ResizeObserver((entries) => redraw(Math.floor(entries[0].contentRect.width))).observe(container);
  redraw(Math.floor(container.clientWidth));
  return () => { const w = last; last = 0; redraw(w || Math.floor(container.clientWidth)); };
}

function markEvents(mark, tip, container, content) {
  const place = (evt) => {
    const box = container.getBoundingClientRect();
    if (evt && evt.clientX !== undefined) return [evt.clientX - box.left, evt.clientY - box.top];
    const r = mark.getBoundingClientRect();
    return [r.left - box.left + r.width / 2, r.top - box.top];
  };
  const show = (evt) => { mark.classList.add('active'); tip.show(content(), ...place(evt)); };
  const hide = () => { mark.classList.remove('active'); tip.hide(); };
  mark.addEventListener('pointerenter', show);
  mark.addEventListener('pointermove', show);
  mark.addEventListener('pointerleave', hide);
  mark.addEventListener('focus', () => show());
  mark.addEventListener('blur', hide);
}

function tableView(details, headers, rows, { scroll = false, numericFrom = 1 } = {}) {
  const table = h('table', {},
    h('thead', {}, h('tr', {}, headers.map((t) => h('th', { scope: 'col', text: t })))),
    h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c, i) => (i === 0 ? h('th', { scope: 'row', text: c }) : h('td', { class: i >= numericFrom ? 'num' : '', text: c })))))));
  const wrap = h('div', { class: scroll ? 'table-wrap table-scroll' : 'table-wrap', tabindex: scroll ? '0' : null, role: scroll ? 'region' : null, 'aria-label': scroll ? headers.join(', ') : null }, table);
  details.append(wrap);
  return table;
}

// --- sections ------------------------------------------------------------------------------------------------------

function renderStamp(snap) {
  const s = snap.sources;
  const stamp = $('#stamp');
  const when = new Date(snap.generated_at);
  const whenText = `${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }).format(when)} UTC`;
  stamp.replaceChildren(
    h('span', {}, h('strong', { text: 'Snapshot ' }), h('time', { datetime: snap.generated_at, text: whenText })),
    h('span', { text: `GA4 sample: ${fmtRange(s.ga4_sample.first_date, s.ga4_sample.last_date)} (${plural(s.ga4_sample.days, 'day')})` }),
    h('span', { text: `Site: ${fmtRange(s.tagline_site.first_date, s.tagline_site.last_date)} (${plural(s.tagline_site.days, 'day')}, synthetic)` }),
  );
}

function renderSources(snap) {
  const box = $('#sources');
  for (const key of ['ga4_sample', 'tagline_site']) {
    const s = snap.sources[key];
    box.append(h('article', { class: 'card source-card', 'aria-labelledby': `src-${key}` },
      h('h3', { id: `src-${key}` }, h('span', { class: `swatch ${SOURCE_SERIES[key]}`, 'aria-hidden': 'true' }), s.label,
        s.synthetic ? h('span', { class: 'badge synthetic', text: 'synthetic traffic' }) : h('span', { class: 'badge', text: 'real traffic, obfuscated' })),
      h('p', { class: 'range', text: `${fmtRange(s.first_date, s.last_date)}: ${plural(s.days, 'day')} of data` }),
      h('p', { class: 'small', text: s.description })));
  }
}

function tile(label, value, sub) {
  return h('div', { class: 'tile' }, h('dt', { text: label }), h('dd', {}, h('span', { class: 'value', text: value }), sub ? h('span', { class: 'sub', text: sub }) : null));
}

function renderKpis(snap) {
  const box = $('#kpi-groups');
  const k = snap.kpis;
  const s = k.ga4_sample;
  const t = k.tagline_site;
  box.append(
    h('div', { class: 'card kpi-group' },
      h('h3', {}, h('span', { class: 'swatch s1', 'aria-hidden': 'true' }), snap.sources.ga4_sample.short, h('span', { class: 'badge', text: plural(s.days, 'day') })),
      h('dl', { class: 'tiles' },
        tile('Sessions', int(s.sessions), `${pct(s.engaged_session_rate)} engaged`),
        tile('Orders', int(s.orders), `in ${int(s.converted_sessions)} sessions`),
        tile('Revenue', usd(s.revenue_usd), 'obfuscated by Google'),
        tile('Conversion rate', pct(s.conversion_rate, 2), 'sessions with an order'),
        tile('Average order', usd(s.aov_usd, 2), 'revenue ÷ orders'),
        tile('Add-to-cart rate', pct(s.add_to_cart_rate), 'of sessions'))),
    h('div', { class: 'card kpi-group' },
      h('h3', {}, h('span', { class: 'swatch s2', 'aria-hidden': 'true' }), snap.sources.tagline_site.short, h('span', { class: 'badge synthetic', text: plural(t.days, 'day') })),
      h('dl', { class: 'tiles' },
        tile('Sessions', int(t.sessions), `${pct(t.engaged_session_rate)} engaged`),
        tile('Orders', int(t.orders), `${int(t.cookieless_orders)} with consent denied`),
        tile('Revenue', usd(t.revenue_usd, 2), 'synthetic orders'),
        tile('Conversion rate', pct(t.conversion_rate), 'sessions with an order'),
        tile('Consent accepted', pct(t.consent_accept_share, 0), `of ${int(t.events)} exported events`),
        tile('Cookieless orders', pct(t.cookieless_order_share, 0), 'no device or session id'))));
}

// Daily sessions and revenue for the sample, two panels on one time axis (no second y-scale), alert days marked.
function renderDaily(snap) {
  const rows = snap.daily.ga4_sample || [];
  const alertDays = new Map((snap.alerts.days || []).filter((d) => d.source === 'ga4_sample').map((d) => [d.date, d]));
  const container = $('#daily-chart');
  const live = $('#daily-live');
  const tip = makeTooltip(container);
  const n = rows.length;
  const state = { idx: null, band: null, geom: null, keyboard: false };

  const legendCritical = h('li', {}, statusIcon('critical'), 'Alert day (at least one critical alert)');
  const legendWarning = h('li', {}, statusIcon('warning'), 'Alert day (warnings only)');
  $('#daily-legend').append(
    h('li', {}, h('span', { class: 'linekey', 'aria-hidden': 'true' }), 'GA4 sample, per day'),
    legendCritical,
    legendWarning,
  );

  container.setAttribute('tabindex', '0');
  container.setAttribute('role', 'group');
  container.setAttribute('aria-roledescription', 'chart');
  container.setAttribute('aria-label', `Sessions and revenue for each of the sample's ${n} days, with alert days marked. Use the left and right arrow keys to move between days, Home and End for the first and last day.`);

  const panels = [
    { key: 'sessions', title: 'Sessions', fmt: int, tick: compactInt },
    { key: 'revenue_usd', title: 'Revenue (items subtotal)', fmt: (v) => usd(v), tick: compactUsd },
  ];

  function draw(w) {
    const narrow = w < 560;
    const m = { l: narrow ? 42 : 54, r: 10, t: 20 };
    const ph = narrow ? 104 : 136;
    const gap = 30;
    const stripY = m.t + ph * 2 + gap + 12;
    const stripH = 26;
    const axisY = stripY + stripH;
    const H = axisY + 22;
    const step = (w - m.l - m.r) / Math.max(1, n - 1);
    const x = (i) => m.l + i * step;
    const svg = sv('svg', { viewBox: `0 0 ${w} ${H}`, width: w, height: H, 'aria-hidden': 'true', focusable: 'false' });
    const bandLayer = sv('g');
    svg.append(bandLayer);
    const scales = panels.map((p, pi) => {
      const y0 = m.t + pi * (ph + gap);
      const max = Math.max(...rows.map((r) => r[p.key] || 0));
      const ticks = niceTicks(max, narrow ? 3 : 4);
      const top = ticks[ticks.length - 1];
      const y = (v) => y0 + ph - (v / top) * ph;
      const g = sv('g');
      for (const t of ticks) {
        g.append(sv('line', { class: t === 0 ? 'baseline' : 'gridline', x1: m.l, x2: w - m.r, y1: y(t), y2: y(t) }));
        g.append(sv('text', { class: 'tick', x: m.l - 6, y: y(t) + 4, 'text-anchor': 'end', text: p.tick(t) }));
      }
      g.append(sv('text', { class: 'axis-label', x: m.l, y: y0 - 8, text: p.title }));
      const pts = rows.map((r, i) => `${x(i).toFixed(1)},${y(r[p.key] || 0).toFixed(1)}`);
      g.append(sv('path', { class: 'area-s1', d: `M${x(0)},${y(0)}L${pts.join('L')}L${x(n - 1)},${y(0)}Z` }));
      g.append(sv('path', { class: 'line-s1', d: `M${pts.join('L')}` }));
      svg.append(g);
      return { y, y0 };
    });
    // alert strip
    const strip = sv('g');
    strip.append(sv('text', { class: 'strip-label', x: m.l - 12, y: stripY + stripH / 2 + 4, 'text-anchor': 'end', text: 'Alerts' }));
    strip.append(sv('line', { class: 'gridline', x1: m.l, x2: w - m.r, y1: stripY + stripH / 2, y2: stripY + stripH / 2 }));
    const cy = stripY + stripH / 2;
    // A diamond (critical) or a triangle (warnings only) per alert day, never wider than a day. When days sit closer
    // than 7 px (a phone), a shape that small cannot be told apart, so each alert day is a bar one day wide instead:
    // tall for critical, short for warnings only. The legend follows.
    const shapes = step >= 7;
    const ms = Math.min(narrow ? 4.5 : 6, step / 2 - 0.5); // marker half-size
    const bw = Math.max(1, step - 0.8); // bar width, leaving a hairline between consecutive days
    legendCritical.firstChild.replaceWith(shapes ? statusIcon('critical') : barIcon('critical'));
    legendWarning.firstChild.replaceWith(shapes ? statusIcon('warning') : barIcon('warning'));
    rows.forEach((r, i) => {
      const a = alertDays.get(r.date);
      if (!a) return;
      const cx = x(i);
      const critical = a.max_severity === 'critical';
      if (!shapes) {
        const top = critical ? cy - 9 : cy + 1;
        const d = `M${(cx - bw / 2).toFixed(2)},${top}h${bw.toFixed(2)}V${cy + 9}h${(-bw).toFixed(2)}Z`;
        strip.append(sv('path', { class: critical ? 'fill-critical' : 'fill-warning edge thin', d }));
      } else if (critical) {
        strip.append(sv('path', { class: 'fill-critical ring', d: `M${cx},${cy - ms}L${cx + ms},${cy}L${cx},${cy + ms}L${cx - ms},${cy}Z` }));
      } else {
        strip.append(sv('path', { class: 'fill-warning edge', d: `M${cx},${cy - ms}L${cx + ms},${cy + ms * 0.85}L${cx - ms},${cy + ms * 0.85}Z` }));
      }
    });
    svg.append(strip);
    // x axis: the first of each month
    const axis = sv('g');
    axis.append(sv('line', { class: 'baseline', x1: m.l, x2: w - m.r, y1: axisY, y2: axisY }));
    rows.forEach((r, i) => {
      if (!r.date.endsWith('-01')) return;
      const d = utcDate(r.date);
      const label = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' }).format(d) + (i === 0 || d.getUTCMonth() === 0 ? ` ${d.getUTCFullYear()}` : '');
      axis.append(sv('line', { class: 'baseline', x1: x(i), x2: x(i), y1: axisY, y2: axisY + 4 }));
      axis.append(sv('text', { class: 'tick', x: x(i), y: axisY + 16, 'text-anchor': i === 0 ? 'start' : 'middle', text: label }));
    });
    svg.append(axis);
    // crosshair
    const cross = sv('g', { visibility: 'hidden' });
    const vline = sv('line', { class: 'crosshair', y1: m.t - 4, y2: axisY });
    const dots = panels.map(() => sv('circle', { r: 4, class: 'fill-s1 ring' }));
    cross.append(vline, ...dots);
    svg.append(cross);
    const hit = sv('rect', { class: 'hit', x: m.l - step / 2, y: m.t - 4, width: w - m.l - m.r + step, height: axisY - m.t + 4 });
    svg.append(hit);
    hit.addEventListener('pointermove', (evt) => {
      const box = svg.getBoundingClientRect();
      const px = ((evt.clientX - box.left) / box.width) * w;
      state.keyboard = false;
      update(Math.max(0, Math.min(n - 1, Math.round((px - m.l) / step))), evt);
    });
    hit.addEventListener('pointerleave', () => { if (!state.keyboard) hide(); });
    state.geom = { x, step, m, scales, cross, vline, dots, bandLayer, top: m.t - 4, bottom: axisY };
    container.replaceChildren(svg, tip.el);
    drawBand();
    if (state.idx !== null) update(state.idx);
  }

  function drawBand() {
    const g = state.geom;
    if (!g) return;
    g.bandLayer.replaceChildren();
    if (!state.band) return;
    const i0 = rows.findIndex((r) => r.date >= state.band.start);
    let i1 = rows.findIndex((r) => r.date > state.band.end);
    i1 = (i1 === -1 ? n : i1) - 1;
    if (i0 < 0 || i1 < i0) return;
    g.bandLayer.append(sv('rect', { class: 'band', x: g.x(i0) - g.step / 2, y: g.top, width: (i1 - i0 + 1) * g.step, height: g.bottom - g.top }));
  }

  function tooltipNodes(r) {
    const a = alertDays.get(r.date);
    const nodes = [
      h('div', { class: 'tt-head', text: fmtDay(r.date) }),
      h('div', { class: 'tt-row' }, h('strong', { text: int(r.sessions) }), h('span', { text: 'sessions' })),
      h('div', { class: 'tt-row' }, h('strong', { text: usd(r.revenue_usd) }), h('span', { text: `revenue, ${plural(r.orders, 'order')}` })),
    ];
    if (a) {
      const items = [...new Map(a.items.map((i) => [metricLabel(i.metric, i.rule), i])).keys()];
      nodes.push(h('div', { class: 'tt-alerts' },
        h('div', { class: 'tt-row' }, statusIcon(a.max_severity), h('strong', { text: plural(a.count, 'alert') }),
          h('span', { text: a.critical ? `${int(a.critical)} critical` : 'warnings' })),
        h('ul', {}, items.slice(0, 4).map((t) => h('li', { text: t })), items.length > 4 ? h('li', { text: `and ${items.length - 4} more` }) : null)));
    }
    return nodes;
  }

  function update(i, evt) {
    const g = state.geom;
    state.idx = i;
    const r = rows[i];
    g.cross.setAttribute('visibility', 'visible');
    g.vline.setAttribute('x1', g.x(i));
    g.vline.setAttribute('x2', g.x(i));
    panels.forEach((p, pi) => { g.dots[pi].setAttribute('cx', g.x(i)); g.dots[pi].setAttribute('cy', g.scales[pi].y(r[p.key] || 0)); });
    const box = container.getBoundingClientRect();
    const svgEl = container.querySelector('svg');
    const svgBox = svgEl.getBoundingClientRect();
    const k = svgBox.width / Number(svgEl.getAttribute('width') || svgBox.width || 1);
    const px = evt ? evt.clientX - box.left : svgBox.left - box.left + g.x(i) * k;
    const py = evt ? evt.clientY - box.top : svgBox.top - box.top + (g.scales[0].y0 + 20) * k;
    tip.show(tooltipNodes(r), px, py);
  }

  function hide() {
    state.idx = null;
    if (state.geom) state.geom.cross.setAttribute('visibility', 'hidden');
    tip.hide();
  }

  container.addEventListener('keydown', (evt) => {
    const moves = { ArrowRight: 1, ArrowLeft: -1, PageDown: 7, PageUp: -7 };
    let i = state.idx ?? 0;
    if (evt.key in moves) i = Math.max(0, Math.min(n - 1, (state.idx === null ? 0 : i + moves[evt.key])));
    else if (evt.key === 'Home') i = 0;
    else if (evt.key === 'End') i = n - 1;
    else if (evt.key === 'Escape') { hide(); return; } else return;
    evt.preventDefault();
    state.keyboard = true;
    update(i);
    const r = rows[i];
    const a = alertDays.get(r.date);
    live.textContent = `${fmtDay(r.date)}: ${int(r.sessions)} sessions, ${usd(r.revenue_usd)} revenue, ${plural(r.orders, 'order')}${a ? `, ${plural(a.count, 'alert')}` : ''}.`;
  });
  container.addEventListener('focus', () => { state.keyboard = true; update(state.idx ?? 0); });
  container.addEventListener('blur', () => { state.keyboard = false; hide(); });

  responsive(container, draw);

  // table view
  tableView($('#daily-table'), ['Date', 'Sessions', 'Orders', 'Revenue', 'Alerts'], rows.map((r) => {
    const a = alertDays.get(r.date);
    return [r.date, int(r.sessions), int(r.orders), usd(r.revenue_usd), a ? `${a.count} (${a.max_severity})` : ''];
  }), { scroll: true });

  // the backtest's judged incidents: pick one to shade its days on the charts
  const bt = snap.cited.backtest;
  const al = snap.alerts;
  const card = $('#backtest');
  const list = h('ul', { class: 'incidents' });
  for (const j of bt.judged) {
    const btn = h('button', { type: 'button', 'aria-pressed': 'false' },
      h('span', { class: 'num-badge', text: j.kind === 'noise' ? '–' : String(j.problem) }),
      h('span', { class: 'when', text: fmtRange(j.start, j.end).replace(/, 20\d\d/g, '') }),
      h('span', {}, j.label, j.kind === 'noise' ? h('span', { class: 'badge', text: 'noise' }) : null));
    btn.addEventListener('click', () => {
      const on = btn.getAttribute('aria-pressed') !== 'true';
      list.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', 'false'));
      btn.setAttribute('aria-pressed', String(on));
      state.band = on ? j : null;
      drawBand();
    });
    list.append(h('li', {}, btn));
  }
  const rules = Object.entries(al.by_rule).map(([r, c]) => `${int(c)} ${r === 'mad' ? 'outside the robust band' : r === 'floor' ? 'below an absolute floor' : r}`).join(', ');
  card.append(
    h('h3', { text: 'What the backtest found' }),
    h('p', {}, `The rules raised ${plural(al.total, 'alert')} on ${plural(al.source_days, 'source-day')} of the sample (${rules}; ${int(al.by_severity.critical || 0)} critical). `,
      bt.text, ` Select one to shade its days on the charts.`),
    list,
    sourceLink(bt.source, 'Judgement: '));
}

function renderFunnel(snap) {
  const f = snap.funnel;
  const sources = ['ga4_sample', 'tagline_site'].filter((s) => f[s]);
  const steps = [
    ['view_item', 'sessions', 'Session → view_item'],
    ['add_to_cart', 'view_item', 'view_item → add_to_cart'],
    ['begin_checkout', 'add_to_cart', 'add_to_cart → begin_checkout'],
    ['purchase', 'begin_checkout', 'begin_checkout → purchase'],
  ];
  $('#funnel-caption').textContent = sources.map((s) => `${snap.sources[s].short}: ${int(f[s].sessions)} sessions over ${plural(f[s].days, 'day')}`).join('. ') + '.'
    + (f.tagline_site ? " The site's rates come from the simulator's planned journeys, not observed shoppers, so they are not comparable with the sample's." : '');
  $('#funnel-legend').append(...sources.map((s) => h('li', {}, h('span', { class: `swatch ${SOURCE_SERIES[s]}`, 'aria-hidden': 'true' }), snap.sources[s].short)));
  const container = $('#funnel-chart');
  const tip = makeTooltip(container);
  responsive(container, (w) => {
    const barH = 16;
    const labelH = 22;
    const groupGap = 14;
    const right = 56;
    const axisH = 20;
    const plotW = w - right;
    const groupH = labelH + sources.length * barH + (sources.length - 1) * 2;
    const H = steps.length * groupH + (steps.length - 1) * groupGap + axisH + 4;
    const svg = sv('svg', { viewBox: `0 0 ${w} ${H}`, width: w, height: H, role: 'group', 'aria-label': 'Funnel step rates by source' });
    const plotBottom = H - axisH;
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      svg.append(sv('line', { class: t === 0 ? 'baseline' : 'gridline', x1: t * plotW, x2: t * plotW, y1: labelH - 4, y2: plotBottom }));
      svg.append(sv('text', { class: 'tick', x: t * plotW, y: H - 4, 'text-anchor': t === 0 ? 'start' : t === 1 ? 'end' : 'middle', text: `${t * 100}%` }));
    }
    steps.forEach(([key, prev, label], si) => {
      const y0 = si * (groupH + groupGap);
      svg.append(sv('text', { class: 'axis-label', x: 0, y: y0 + 14, text: label }));
      sources.forEach((s, k) => {
        const rate = f[s].step_rates[key];
        const y = y0 + labelH + k * (barH + 2);
        const bw = Math.max(0, (rate || 0) * plotW);
        const desc = `${snap.sources[s].short}, ${label}: ${pct(rate)} (${int(f[s][key])} of ${int(f[s][prev])} sessions)`;
        const mark = sv('path', { class: `mark fill-${SOURCE_SERIES[s]}`, d: hbarPath(0, y, bw, barH) || `M0,${y}h1v${barH}h-1z`, tabindex: 0, role: 'img', 'aria-label': desc });
        svg.append(mark);
        svg.append(sv('text', { class: 'value-label', x: bw + 6, y: y + barH - 3, text: pct(rate) }));
        markEvents(mark, tip, container, () => [
          h('div', { class: 'tt-head', text: label }),
          h('div', { class: 'tt-row' }, h('span', { class: `swatch ${SOURCE_SERIES[s]}`, 'aria-hidden': 'true' }), h('strong', { text: pct(rate) }), h('span', { text: snap.sources[s].short })),
          h('div', { class: 'small muted', text: `${int(f[s][key])} of ${int(f[s][prev])} sessions` }),
        ]);
      });
    });
    container.replaceChildren(svg, tip.el);
  });
  const head = ['Step'];
  sources.forEach((s) => head.push(`${snap.sources[s].short}: sessions`, `${snap.sources[s].short}: step rate`));
  const rows = [['Sessions', ...sources.flatMap((s) => [int(f[s].sessions), ''])]];
  steps.forEach(([key, , label]) => rows.push([label, ...sources.flatMap((s) => [int(f[s][key]), pct(f[s].step_rates[key])])]));
  tableView($('#funnel-table'), head, rows);
}

function renderAttribution(snap) {
  const a = snap.attribution;
  const models = a.models;
  const sample = a.ga4_sample;
  const look = a.lookback.ga4_sample;
  const named = sample.channels.filter((c) => !c.other);
  const maxShare = Math.max(...named.flatMap((c) => models.map((m) => c.share[m] || 0)));
  const selfRef = snap.cited.self_referral;
  let selected = named.find((c) => c.channel === selfRef.channel) || named[0];

  const table = h('table', { class: 'heat' },
    h('caption', { text: `Share of attributed revenue (%) by channel (source / medium), GA4 sample, ${int(sample.orders)} complete-lookback orders (${usd(sample.revenue_usd)})` }),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col', text: 'Channel' }),
      models.map((m) => h('th', { scope: 'col' }, h('span', { class: 'long', text: MODEL_LABEL[m] }), h('span', { class: 'short', 'aria-hidden': 'true', text: MODEL_SHORT[m] }))))));
  const tbody = h('tbody');
  const buttons = [];
  for (const c of sample.channels) {
    const isSelf = c.channel === selfRef.channel;
    const head = h('th', { scope: 'row' });
    if (c.other) head.append(c.channel);
    else {
      const b = h('button', { type: 'button', class: 'pick', 'aria-pressed': String(c === selected), text: c.channel });
      b.addEventListener('click', () => { selected = c; sync(); });
      buttons.push([b, c]);
      head.append(b);
      if (isSelf) head.append(' ', h('span', { class: 'badge', text: 'self-referral' }));
    }
    const tr = h('tr', { class: [c.other ? 'other' : '', c === selected ? 'selected' : ''].join(' ').trim() }, head,
      models.map((m) => h('td', { style: { '--t': ((0.58 * (c.share[m] || 0)) / maxShare).toFixed(3) } }, ((c.share[m] || 0) * 100).toFixed(1), h('span', { class: 'pct', text: '%' }))));
    tr.dataset.channel = c.channel;
    tbody.append(tr);
  }
  table.append(tbody);
  $('#heat-wrap').append(table);
  $('#heat-max').textContent = pct(maxShare);
  $('#heat-note').textContent = `Complete lookback: the order's 30-day window lies inside the sample. The ${int(look.incomplete_orders)} orders (${usd(look.incomplete_revenue_usd)}) placed in its first 30 days are left out here, because their journeys are cut short. Each column sums to 100%; cell shading is the share.`;

  // the biggest moves from last click to first click, computed from the table
  const moves = named.map((c) => ({ c, d: (c.share.first_click || 0) - (c.share.last_click || 0) })).sort((p, q) => q.d - p.d);
  const up = moves[0];
  const down = moves[moves.length - 1];
  const sr = named.find((c) => c.channel === selfRef.channel);
  const callout = $('#self-referral');
  if (sr) {
    callout.append(h('div', { class: 'callout' },
      h('p', {}, h('strong', { text: selfRef.channel }), ` has ${pct(sr.share.last_click)} of last-click revenue and ${pct(sr.share.first_click)} of first-click. `, selfRef.text),
      h('p', { class: 'small', text: `Largest moves from last click to first click: ${up.c.channel} ${pts(up.d)}; ${down.c.channel} ${pts(down.d)}.` }),
      sourceLink(selfRef.source, 'Finding: ')));
  }

  const container = $('#channel-chart');
  const tip = makeTooltip(container);
  const ticks = niceTicks(maxShare, 3);
  const top = ticks[ticks.length - 1];
  const details = $('#channel-table');
  let redraw = () => {};

  function sync() {
    for (const [b, c] of buttons) b.setAttribute('aria-pressed', String(c === selected));
    tbody.querySelectorAll('tr').forEach((tr) => tr.classList.toggle('selected', tr.dataset.channel === selected.channel));
    $('#channel-title').textContent = selected.channel;
    redraw();
    details.querySelector('.table-wrap')?.remove();
    tableView(details, ['Model', 'Share of revenue', 'Attributed revenue'], models.map((m) => [MODEL_LABEL[m], pct(selected.share[m]), usd(selected.revenue_usd[m])]));
  }

  redraw = responsive(container, (fullW) => {
    const w = Math.min(fullW, 680);
    const m = { l: 44, r: 8, t: 22, b: 40 };
    const ph = 170;
    const H = m.t + ph + m.b;
    const band = (w - m.l - m.r) / models.length;
    const bw = Math.min(24, band * 0.5);
    const y = (v) => m.t + ph - (v / top) * ph;
    const svg = sv('svg', { viewBox: `0 0 ${w} ${H}`, width: w, height: H, role: 'group', 'aria-label': `${selected.channel}: share of revenue by model`, style: { 'max-width': `${w}px` } });
    for (const t of ticks) {
      svg.append(sv('line', { class: t === 0 ? 'baseline' : 'gridline', x1: m.l, x2: w - m.r, y1: y(t), y2: y(t) }));
      svg.append(sv('text', { class: 'tick', x: m.l - 6, y: y(t) + 4, 'text-anchor': 'end', text: pct(t, 0) }));
    }
    models.forEach((mo, i) => {
      const v = selected.share[mo] || 0;
      const cx = m.l + band * i + band / 2;
      const mark = sv('path', { class: 'mark fill-s1', d: vbarPath(cx - bw / 2, y(v), bw, y(0) - y(v)) || `M${cx - bw / 2},${y(0) - 1}h${bw}v1h-${bw}z`, tabindex: 0, role: 'img', 'aria-label': `${MODEL_LABEL[mo]}: ${pct(v)}, ${usd(selected.revenue_usd[mo])}` });
      svg.append(mark);
      svg.append(sv('text', { class: 'value-label', x: cx, y: y(v) - 6, 'text-anchor': 'middle', text: pct(v) }));
      const words = MODEL_LABEL[mo].split(/[ -]/);
      const label = sv('text', { class: 'tick', x: cx, y: y(0) + 15, 'text-anchor': 'middle' });
      const lines = band < 90 && words.length > 1 ? [words[0], words.slice(1).join(' ')] : [MODEL_LABEL[mo]];
      lines.forEach((ln, li) => label.append(sv('tspan', { x: cx, dy: li === 0 ? 0 : 13, text: ln })));
      svg.append(label);
      markEvents(mark, tip, container, () => [
        h('div', { class: 'tt-head', text: MODEL_LABEL[mo] }),
        h('div', { class: 'tt-row' }, h('strong', { text: pct(v) }), h('span', { text: 'of attributed revenue' })),
        h('div', { class: 'small muted', text: `${usd(selected.revenue_usd[mo])} of ${usd(sample.totals_by_model[mo].revenue_usd)}` }),
      ]);
    });
    container.replaceChildren(svg, tip.el);
  });
  sync();

  // the site: every attributed order, in dollars
  const site = a.tagline_site;
  const siteK = snap.kpis.tagline_site;
  const card = $('#site-attribution');
  if (!site) return;
  const deniedOrders = siteK.orders - site.orders;
  const deniedRevenue = siteK.revenue_usd - site.revenue_usd;
  const siteChannels = new Set(site.channels.map((c) => c.channel));
  const untouched = (snap.campaigns.tagline_site?.rows || []).map((r) => r.channel).filter((c) => !siteChannels.has(c));
  card.append(
    h('h3', {}, 'The site\'s orders under the six models ', h('span', { class: 'badge synthetic', text: 'synthetic, one day' })),
    h('p', { class: 'small', text: `${plural(site.orders, 'order')} attributed (${usd(site.revenue_usd, 2)}), none with a complete lookback: the site's data starts on its first day. The other ${plural(deniedOrders, 'order')} (${usd(deniedRevenue, 2)}) were sent with consent denied. ${snap.cited.consent_denied_orders.text}` }),
  );
  const tbl = h('table', {},
    h('caption', { text: 'Attributed revenue by channel (source / medium / campaign), the site' }),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col', text: 'Channel' }), models.map((m) => h('th', { scope: 'col', text: MODEL_LABEL[m] })))),
    h('tbody', {}, site.channels.map((c) => h('tr', {}, h('th', { scope: 'row', text: c.channel }), models.map((m) => h('td', { text: usd(c.revenue_usd[m], 2) }))))),
    h('tfoot', {}, h('tr', {}, h('th', { scope: 'row', text: 'Total' }), models.map((m) => h('td', { text: usd(site.totals_by_model[m].revenue_usd, 2) })))));
  card.append(h('div', { class: 'table-wrap' }, tbl));
  if (untouched.length) card.append(h('p', { class: 'note', text: `Not in the table: ${untouched.join(', ')}, which no attributed order's journey includes.` }));
  card.append(sourceLink(snap.cited.consent_denied_orders.source));
}

function renderTagHealth(snap) {
  const th = snap.tag_health;
  const qa = snap.cited.tag_qa;
  const al = snap.alerts;
  const hist = snap.cited.alert_history;
  const sample = th.by_source.ga4_sample;
  const site = th.by_source.tagline_site;
  const siteDays = snap.sources.tagline_site.days;
  $('#layers').append(
    h('article', { class: 'card layer' },
      h('h3', { text: '1. Before a release: tag QA' }),
      h('span', { class: 'big', text: `${int(qa.mutations_caught)} of ${int(qa.mutations)}` }),
      h('p', { text: `deliberate breaks of the site's tags failed the suite: ${int(qa.tests)} tests over ${int(qa.journeys)} journeys and ${int(qa.rules)} rules. ${qa.text}` }),
      sourceLink(qa.source)),
    h('article', { class: 'card layer' },
      h('h3', { text: '2. On the collected data: tag health' }),
      h('span', { class: 'big', text: `${int(site.pass)} of ${int(site.rows)}` }),
      h('p', { text: `site check rows pass (${plural(site.days, 'day')}). The sample: ${plural(sample.violation, 'violation')} in ${int(sample.rows)} rows over ${plural(sample.days, 'day')}; ${int(sample.expected)} are expected, documented quirks of Google's tagging.` })),
    h('article', { class: 'card layer' },
      h('h3', { text: '3. On the KPIs: anomaly alerts' }),
      h('span', { class: 'big', text: plural(al.total, 'alert') }),
      h('p', { text: `on ${plural(al.source_days, 'day')} of the sample; ${plural(al.by_source.tagline_site || 0, 'alert')} on the site. ${hist.text} The site has ${plural(siteDays, 'day')}.` }),
      sourceLink(hist.source)),
  );

  const kinds = ['pass', 'expected', 'violation'];
  $('#health-legend').append(...kinds.map((k) => h('li', {}, statusIcon(k), STATUS[k].label)));
  const container = $('#health-chart');
  const tip = makeTooltip(container);
  const sources = ['ga4_sample', 'tagline_site'].filter((s) => th.by_source[s]);
  responsive(container, (w) => {
    const barH = 22;
    const labelH = 22;
    const gap = 16;
    const H = sources.length * (labelH + barH) + (sources.length - 1) * gap + 2;
    const svg = sv('svg', { viewBox: `0 0 ${w} ${H}`, width: w, height: H, role: 'group', 'aria-label': 'Tag health check rows by status, per source' });
    sources.forEach((s, si) => {
      const d = th.by_source[s];
      const y0 = si * (labelH + barH + gap);
      const when = w < 560 ? plural(d.days, 'day') : fmtRange(d.first_date, d.last_date);
      svg.append(sv('text', { class: 'axis-label', x: 0, y: y0 + 14, text: `${snap.sources[s].short}: ${int(d.rows)} check rows, ${when}` }));
      let x = 0;
      const parts = kinds.filter((k) => d[k] > 0);
      parts.forEach((k, ki) => {
        const full = (d[k] / d.rows) * w;
        const last = ki === parts.length - 1;
        const segW = Math.max(1, full - (last ? 0 : 2));
        const y = y0 + labelH;
        const r = last ? 4 : 0;
        const path = r ? hbarPath(x, y, segW, barH, 4) : `M${x},${y}h${segW}v${barH}h-${segW}z`;
        const desc = `${snap.sources[s].short}: ${int(d[k])} ${STATUS[k].label} (${pct(d[k] / d.rows)})`;
        const mark = sv('path', { class: `mark fill-${STATUS[k].cls}`, d: path, tabindex: 0, role: 'img', 'aria-label': desc });
        svg.append(mark);
        const text = `${int(d[k])} ${k}`;
        if (segW > text.length * 7 + 16) {
          // a label inside a fill takes the ink that clears contrast on it: dark on green and gray, white on red
          svg.append(sv('text', { x: x + 8, y: y + barH / 2 + 4, 'font-size': 12, fill: k === 'violation' ? '#ffffff' : '#0b1a1f', 'pointer-events': 'none', text }));
        }
        markEvents(mark, tip, container, () => [
          h('div', { class: 'tt-head', text: snap.sources[s].label }),
          h('div', { class: 'tt-row' }, statusIcon(k), h('strong', { text: int(d[k]) }), h('span', { text: STATUS[k].label })),
          h('div', { class: 'small muted', text: `${pct(d[k] / d.rows)} of ${int(d.rows)} check rows` }),
        ]);
        x += full;
      });
    });
    container.replaceChildren(svg, tip.el);
  });

  // by check kind
  const kindLabel = { required: 'Required parameters', format: 'Format', value_math: 'Value math', pii: 'No PII', dedupe: 'Purchase dedupe', attribution: 'Session source' };
  const kindTables = sources.map((s) => h('div', { class: 'table-wrap' }, h('table', {},
    h('caption', { text: `${snap.sources[s].short}: check rows by kind and status` }),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col', text: 'Check kind' }), kinds.map((k) => h('th', { scope: 'col', text: k })))),
    h('tbody', {}, th.by_source[s].by_kind.map((v) => h('tr', {}, h('th', { scope: 'row', text: kindLabel[v.kind] || v.kind }),
      kinds.map((k) => h('td', { text: int(v[k]) }))))))));
  $('#health-kinds').append(...kindTables);
  const ex = th.expectations || [];
  if (ex.length) {
    $('#health-expectations').append(
      h('h3', { class: 'small', style: { 'margin-top': '14px' }, text: 'Why the sample\'s expected rows are expected' }),
      h('ul', { class: 'small' }, ex.map((e) => h('li', {}, e.expectation, h('span', { class: 'muted', text: ` (${int(e.rows)} rows)` })))),
      h('p', { class: 'note', text: `Contract version ${th.contract_versions.join(', ')}. The expected rows' rates are still watched by the anomaly rules.` }));
  }
}

function renderCampaigns(snap) {
  const c = snap.campaigns.tagline_site;
  const k = snap.kpis.tagline_site;
  const money = (v) => (v === null || v === undefined ? '–' : usd(v, 2));
  const tbl = h('table', {},
    h('caption', { text: `The site's sessions by landing campaign, ${fmtRange(c.first_date, c.last_date)}` }),
    h('thead', {}, h('tr', {}, ['Session source / medium / campaign', 'Sessions', 'Engaged', 'Orders', 'Revenue', 'Synthetic spend', 'ROAS', 'Cost per order'].map((t) => h('th', { scope: 'col', text: t })))),
    h('tbody', {}, c.rows.map((r) => h('tr', {}, h('th', { scope: 'row', text: r.channel }),
      h('td', { text: int(r.sessions) }), h('td', { text: int(r.engaged_sessions) }), h('td', { text: int(r.orders) }),
      h('td', { text: money(r.revenue_usd) }), h('td', { text: money(r.cost_usd) }),
      h('td', { text: r.roas === null ? '–' : r.roas.toFixed(2) }), h('td', { text: money(r.cost_per_order) })))),
    h('tfoot', {}, h('tr', {}, h('th', { scope: 'row', text: 'Total' }), h('td', { text: int(c.totals.sessions) }), h('td', { text: '' }),
      h('td', { text: int(c.totals.orders) }), h('td', { text: money(c.totals.revenue_usd) }), h('td', { text: money(c.totals.cost_usd) }),
      h('td', { text: c.totals.paid_roas === null ? '–' : c.totals.paid_roas.toFixed(2), title: 'Paid campaigns only' }), h('td', { text: '' }))));
  $('#campaign-table').append(tbl);
  const outside = k.orders - c.totals.orders;
  $('#campaign-note').append(
    `ROAS is revenue ÷ synthetic spend; the total is over the paid rows only (${money(c.totals.paid_revenue_usd)} ÷ ${money(c.totals.cost_usd)}). `,
    `The site had ${plural(k.orders, 'order')} that day; the ${int(outside)} not here were sent with consent denied, so they have no session and no campaign. `,
    snap.cited.synthetic.text, ' ',
    h('a', { href: snap.cited.synthetic.source.url, text: snap.cited.synthetic.source.label }), '.');
}

function hbarChart(container, items, fmt, label) {
  const tip = makeTooltip(container);
  responsive(container, (w) => {
    const left = w < 420 ? 64 : 76;
    const right = 64;
    const barH = 20;
    const gap = 12;
    const H = items.length * (barH + gap) - gap + 22;
    const max = Math.max(...items.map((i) => i.value));
    const plotW = w - left - right;
    const svg = sv('svg', { viewBox: `0 0 ${w} ${H}`, width: w, height: H, role: 'group', 'aria-label': label });
    svg.append(sv('line', { class: 'baseline', x1: left, x2: left, y1: 0, y2: H - 22 }));
    items.forEach((it, i) => {
      const y = i * (barH + gap);
      const bw = (it.value / max) * plotW;
      svg.append(sv('text', { class: 'axis-label', x: left - 8, y: y + barH / 2 + 4, 'text-anchor': 'end', text: it.label }));
      const mark = sv('path', { class: 'mark fill-s1', d: hbarPath(left, y, bw, barH), tabindex: 0, role: 'img', 'aria-label': `${it.label}: ${fmt(it.value)}` });
      svg.append(mark);
      svg.append(sv('text', { class: 'value-label', x: left + bw + 6, y: y + barH / 2 + 4, text: fmt(it.value) }));
      markEvents(mark, tip, container, () => [h('div', { class: 'tt-head', text: it.long }), h('div', { class: 'tt-row' }, h('strong', { text: fmt(it.value) }))]);
    });
    svg.append(sv('text', { class: 'tick', x: left, y: H - 4, text: 'one measured run each' }));
    container.replaceChildren(svg, tip.el);
  });
}

function renderCost(snap) {
  const pr = snap.cited.pipeline_runs;
  const runs = pr.runs;
  hbarChart($('#wall-chart'), runs.map((r) => ({ label: r.stage, long: r.label, value: r.wall_s })), (v) => `${int(v)} s`, 'Wall time per daily DAG run');
  hbarChart($('#price-chart'), runs.map((r) => ({ label: r.stage, long: r.label, value: r.usd })), (v) => usdAuto(v), 'List price per daily DAG run');
  const card = $('#cost-detail');
  const tbl = h('table', {},
    h('caption', { text: 'One daily DAG run, measured at the end of each stage' }),
    h('thead', {}, h('tr', {}, ['Run', 'Wall time', 'BigQuery billed', 'Spark DCU-hours', 'List price', 'Source'].map((t) => h('th', { scope: 'col', text: t })))),
    h('tbody', {}, runs.map((r) => h('tr', {}, h('th', { scope: 'row', text: r.label }), h('td', { text: `${int(r.wall_s)} s` }),
      h('td', { text: `${r.bigquery_gib.toFixed(2)} GiB` }), h('td', { text: String(r.dcu_hours) }), h('td', { text: usdAuto(r.usd) }),
      h('td', {}, h('a', { href: r.source.url, text: r.source.label }))))));
  const builds = h('table', {},
    h('caption', { text: 'BigQuery billed per build, with the checks' }),
    h('thead', {}, h('tr', {}, ['Build', 'Stage 4', 'Stage 5 (+ monitoring marts, 11 checks)'].map((t) => h('th', { scope: 'col', text: t })))),
    h('tbody', {}, pr.builds.map((b) => h('tr', {}, h('th', { scope: 'row', text: b.label }), h('td', { text: `${b.stage4_gib.toFixed(2)} GiB` }), h('td', { text: `${b.stage5_gib.toFixed(2)} GiB` })))));
  const mo = pr.monthly;
  const ec = snap.export_cost;
  card.append(
    h('div', { class: 'table-wrap' }, tbl),
    h('p', { class: 'note', text: pr.note }),
    h('div', { class: 'table-wrap', style: { 'margin-top': '14px' } }, builds),
    sourceLink(pr.builds_source),
    h('p', { style: { 'margin-top': '12px' } }, h('strong', { text: `${usd(mo.before_usd, 2)} → ${usd(mo.after_usd, 2)} a month.` }), ` ${mo.text} That part: ${usd(mo.paid_before_usd, 2)} → ${usd(mo.paid_after_usd, 2)} a month.`),
    sourceLink(mo.source),
    h('p', { class: 'note', text: `This snapshot: ${plural(ec.query_jobs, 'query job')} on the marts, ${bytesMiB(ec.bytes_billed)} billed (BigQuery's 10 MiB minimum per job), each job capped at ${int(ec.max_bytes_billed / 1e6)} MB.` }));
}

function renderFooter(snap) {
  const f = $('#footer');
  f.append(
    h('p', {}, `Snapshot written ${snap.generated_at} by `, h('code', { text: 'make dashboard-snapshot' }), ' (', h('code', { text: 'tagline/dashboard/export_snapshot.py' }),
      ') from six tables in ', h('code', { text: 'tagline_marts' }), '. Aggregates only: no person, device, session or order id, and no raw event.'),
    h('ul', {}, snap.tables.map((t) => h('li', {}, h('code', { text: t.name }), ` ${plural(t.rows, 'row')}, built ${t.last_modified}`))),
    h('p', { style: { 'margin-top': '10px' } }, 'Code and write-ups: ',
      h('a', { href: 'https://github.com/jdoan5/Databases-and-Data-Platforms/tree/main/tagline', text: 'github.com/jdoan5/Databases-and-Data-Platforms/tagline' }),
      '. Portfolio: ', h('a', { href: 'https://jdoan5.github.io/projects-database.html', text: 'jdoan5.github.io' }), '.'));
}

// --- theme toggle: the portfolio's Auto → Light → Dark cycle, stored under the same key ------------------------------

function initTheme() {
  const root = document.documentElement;
  const btn = $('#theme-toggle');
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const MODES = ['auto', 'light', 'dark'];
  const LABEL = { auto: 'Auto', light: 'Light', dark: 'Dark' };
  const ICON = {
    auto: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor"/>',
    light: '<circle cx="8" cy="8" r="3.2" fill="currentColor"/><path d="M8 .8v2M8 13.2v2M.8 8h2M13.2 8h2M2.9 2.9l1.4 1.4M11.7 11.7l1.4 1.4M2.9 13.1l1.4-1.4M11.7 4.3l1.4-1.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    dark: '<path d="M13.5 10.2A6 6 0 0 1 5.8 2.5a6 6 0 1 0 7.7 7.7z" fill="currentColor"/>',
  };
  let mode = MODES.includes(root.getAttribute('data-theme-mode')) ? root.getAttribute('data-theme-mode') : 'auto';
  const apply = () => {
    const resolved = mode === 'auto' ? (media.matches ? 'dark' : 'light') : mode;
    root.setAttribute('data-theme', resolved);
    root.setAttribute('data-theme-mode', mode);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#0d2a31' : '#2d545e');
    const next = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
    btn.querySelector('.theme-label').textContent = LABEL[mode];
    btn.querySelector('svg').innerHTML = ICON[mode]; // static markup above, never data
    btn.setAttribute('aria-label', `Theme mode: ${LABEL[mode]}. Activate to switch to ${LABEL[next]}.`);
  };
  btn.addEventListener('click', () => {
    mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
    try { localStorage.setItem('theme-mode', mode); } catch { /* storage blocked: the choice lasts for this page */ }
    apply();
  });
  media.addEventListener('change', () => { if (mode === 'auto') apply(); });
  apply();
}

// A table wider than its card scrolls inside its wrapper, never the page. While it does, the wrapper is a labelled,
// focusable region so the keyboard can scroll it too.
function watchScrollRegions() {
  const ro = new ResizeObserver((entries) => {
    for (const { target } of entries) {
      if (target.scrollWidth > target.clientWidth + 1) {
        target.setAttribute('tabindex', '0');
        target.setAttribute('role', 'region');
        target.setAttribute('aria-label', target.querySelector('caption')?.textContent || 'Table');
      } else if (!target.classList.contains('table-scroll')) {
        target.removeAttribute('tabindex');
        target.removeAttribute('role');
        target.removeAttribute('aria-label');
      }
    }
  });
  document.querySelectorAll('.table-wrap').forEach((el) => ro.observe(el));
}

// --- boot ------------------------------------------------------------------------------------------------------------

async function main() {
  initTheme();
  let snap;
  try {
    const res = await fetch('data/snapshot.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    snap = await res.json();
  } catch (err) {
    const box = $('#load-error');
    box.hidden = false;
    box.textContent = `The snapshot (data/snapshot.json) could not be loaded: ${err.message}. The page needs to be served over HTTP, not opened as a file.`;
    $('#stamp').textContent = 'Snapshot not loaded.';
    return;
  }
  const sections = [renderStamp, renderSources, renderKpis, renderDaily, renderFunnel, renderAttribution, renderTagHealth, renderCampaigns, renderCost, renderFooter];
  for (const render of sections) {
    try {
      render(snap);
    } catch (err) {
      console.error(`${render.name} failed:`, err); // eslint-disable-line no-console
    }
  }
  watchScrollRegions();
  document.documentElement.dataset.ready = 'true';
}

main();
