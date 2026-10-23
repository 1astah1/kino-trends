/* Кино-тренды Shorts — интерфейс. Без зависимостей. */
(() => {
'use strict';

// ---------- утилиты ----------
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const h = (tag, attrs, ...kids) => {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
};
const svg = (tag, attrs) => {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  return el;
};
const nf = new Intl.NumberFormat('ru-RU');
const fmtFull = n => (n == null ? '—' : nf.format(Math.round(n)));
const fmtN = n => {
  if (n == null || isNaN(n)) return '—';
  const a = Math.abs(n);
  const r = (x, d) => x.toFixed(d).replace('.', ',').replace(/,0$/, '');
  if (a >= 1e9) return r(n / 1e9, 1) + ' млрд';
  if (a >= 1e6) return r(n / 1e6, a >= 1e7 ? 0 : 1) + ' млн';
  if (a >= 1e4) return Math.round(n / 1e3) + ' тыс.';
  if (a >= 1e3) return r(n / 1e3, 1) + ' тыс.';
  return String(Math.round(n));
};
const fmtPct = (x, d = 0) => (x == null ? '—' : x.toFixed(d).replace('.', ',') + ' %');
const fmtDur = s => (s == null ? '—' : s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s} с`);
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const parseIso = iso => (iso ? new Date(iso.replace(' ', 'T').replace(/Z?$/, 'Z')) : null);
const fmtDate = iso => { const d = parseIso(iso); return d ? `${d.getDate()} ${MONTHS[d.getMonth()]}${d.getFullYear() !== new Date().getFullYear() ? ' ' + d.getFullYear() : ''}` : '—'; };
const fmtDateTime = iso => { const d = parseIso(iso); return d ? `${fmtDate(iso)}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : '—'; };
const ago = iso => {
  const d = parseIso(iso); if (!d) return '—';
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 60) return 'только что';
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} дн назад`;
  return fmtDate(iso);
};
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many; };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const ICON = {
  ext: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',
  back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>',
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5v14l11-7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  dl: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7z M14 3v5h5 M9.5 12h5M9.5 16h5"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
  film: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>',
  swap: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h13l-3-3M20 16H7l3 3"/></svg>',
  undo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14l-4-4 4-4M5 10h9a5 5 0 0 1 0 10h-3"/></svg>',
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
};
const iconBtn = (name, title, onclick, cls = '') => { const b = h('button', { class: 'ibtn ' + cls, type: 'button', title, 'aria-label': title, html: ICON[name] }); if (onclick) b.addEventListener('click', onclick); return b; };
const iconLink = (name, title, href, download = false) => h('a', { class: 'ibtn', title, 'aria-label': title, href, target: download ? null : '_blank', rel: 'noopener', download: download || null, html: ICON[name] });
const toast = msg => { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2800); };

// ---------- состояние и маршрутизация ----------
const state = {
  filters: Object.assign({ period: '90', lang: 'ru', min_conf: '0.6', min_views: '0' }, JSON.parse(localStorage.getItem('kt.filters') || '{}')),
  route: 'overview', param: null, query: {},
  sorts: JSON.parse(localStorage.getItem('kt.sorts') || '{}'),
  run: null,
};
const saveFilters = () => localStorage.setItem('kt.filters', JSON.stringify(state.filters));
const BASE = location.pathname.replace(/[^/]*$/, '');  // каталог страницы: работает и за прокси с префиксом
const abs = path => BASE + path.replace(/^\//, '');
const api = async (path, params = {}, opts = {}) => {
  const url = new URL(abs(path), location.origin);
  for (const [k, v] of Object.entries(params)) if (v != null && v !== '') url.searchParams.set(k, v);
  const r = await fetch(url, opts);
  if (!r.ok) { let msg = `${r.status}`; try { msg = (await r.json()).detail || msg; } catch (_) {} throw new Error(msg); }
  return r.json();
};
const withFilters = (p = {}) => Object.assign({}, state.filters, p);
const post = (path, body, method = 'POST') => api(path, {}, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '') || 'overview';
  const [pathPart, q] = raw.split('?');
  const [route, ...rest] = pathPart.split('/');
  state.route = route || 'overview';
  state.param = rest.length ? decodeURIComponent(rest.join('/')) : null;
  state.query = Object.fromEntries(new URLSearchParams(q || ''));
}
const go = (hash) => { location.hash = hash; };

// ---------- тема ----------
const themeBtn = $('#theme-toggle');
const applyTheme = () => { let t = null; try { t = localStorage.getItem('kt.theme'); } catch (_) {} document.documentElement.dataset.theme = t || 'dark'; };
themeBtn.addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme || 'dark';
  try { localStorage.setItem('kt.theme', cur === 'dark' ? 'light' : 'dark'); } catch (_) {}
  applyTheme();
});
applyTheme();

// ---------- фильтры ----------
function renderFilters() {
  $$('#filters [data-period]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.period === state.filters.period)));
  $$('#filters [data-lang]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === state.filters.lang)));
  $('#f-conf').value = state.filters.min_conf;
  $('#f-views').value = state.filters.min_views;
  const p = state.filters.period;
  $('#filters-note').textContent = p === 'all' ? 'все собранные ролики' : `ролики, опубликованные за последние ${p} ${plural(+p, 'день', 'дня', 'дней')}`;
}
$('#filters').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.period) state.filters.period = b.dataset.period;
  if (b.dataset.lang) state.filters.lang = b.dataset.lang;
  saveFilters(); renderFilters(); render();
});
$('#f-conf').addEventListener('change', e => { state.filters.min_conf = e.target.value; saveFilters(); render(); });
$('#f-views').addEventListener('change', e => { state.filters.min_views = e.target.value; saveFilters(); render(); });

// ---------- графики ----------
const tip = $('#tooltip');
function showTip(e, html) { tip.innerHTML = html; tip.hidden = false; moveTip(e); }
function moveTip(e) { const w = tip.offsetWidth, hgt = tip.offsetHeight; let x = e.clientX + 14, y = e.clientY + 14; if (x + w > innerWidth - 8) x = e.clientX - w - 10; if (y + hgt > innerHeight - 8) y = e.clientY - hgt - 10; tip.style.left = x + 'px'; tip.style.top = y + 'px'; }
function hideTip() { tip.hidden = true; }
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* Горизонтальные столбики: items [{label, value, sub, tip, href}] */
function hbar(items, { fmt = fmtN, maxRows = 12, labelW: labelWOpt, onClick } = {}) {
  const wrap = h('div', { class: 'hbar' });
  const rows = items.slice(0, maxRows);
  const draw = () => {
    wrap.innerHTML = '';
    if (!rows.length) { wrap.append(h('div', { class: 'empty' }, 'нет данных')); return; }
    const W = Math.max(280, wrap.clientWidth || 480), rowH = 26, pad = 6;
    const longest = Math.max(...rows.map(r => String(r.label ?? '').length), 4);
    const labelW = labelWOpt || Math.min(Math.round(longest * 6.6) + 6, Math.round(W * 0.45));
    const H = rows.length * rowH + pad * 2;
    const max = Math.max(...rows.map(r => r.value || 0), 1);
    const valW = 56, barX = labelW + 8, barW = Math.max(40, W - barX - valW - 8);
    const el = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img' });
    rows.forEach((r, i) => {
      const y = pad + i * rowH;
      const bw = Math.max(2, (r.value || 0) / max * barW);
      const g = svg('g', { class: 'hit' });
      const lbl = svg('text', { x: labelW, y: y + 17, 'text-anchor': 'end', class: 'lbl' });
      lbl.textContent = truncate(r.label, Math.floor(labelW / 6.6));
      const path = svg('path', { d: roundRight(barX, y + 5, bw, 16, 4), class: 'mark' + (r.dim ? ' dim' : ''), fill: r.color || null });
      const val = svg('text', { x: barX + bw + 6, y: y + 17, class: 'val' });
      val.textContent = fmt(r.value);
      const hit = svg('rect', { x: 0, y, width: W, height: rowH, fill: 'transparent' });
      g.append(lbl, path, val, hit);
      if (r.color) path.style.fill = r.color;
      const tipHtml = r.tip || `<b>${esc(fmt(r.value))}</b>${esc(r.label)}${r.sub ? `<br><small>${esc(r.sub)}</small>` : ''}`;
      g.addEventListener('pointermove', e => { showTip(e, tipHtml); path.classList.add('hot'); });
      g.addEventListener('pointerleave', () => { hideTip(); path.classList.remove('hot'); });
      if (onClick) { g.style.cursor = 'pointer'; g.addEventListener('click', () => onClick(r)); }
      el.append(g);
    });
    wrap.append(el);
  };
  requestAnimationFrame(draw);
  observe(wrap, draw);
  return wrap;
}
function roundRight(x, y, w, hh, r) { r = Math.min(r, w / 2, hh / 2); return `M${x},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${hh - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} z`; }
function roundTop(x, y, w, hh, r) { r = Math.min(r, w / 2, hh / 2); return `M${x},${y + hh} v-${hh - r} a${r},${r} 0 0 1 ${r},-${r} h${w - 2 * r} a${r},${r} 0 0 1 ${r},${r} v${hh - r} z`; }
const truncate = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + '…' : s; };
const ro = new ResizeObserver(entries => { for (const en of entries) { const fn = en.target._redraw; if (fn && Math.abs((en.target._w || 0) - en.contentRect.width) > 4) { en.target._w = en.contentRect.width; fn(); } } });
function observe(el, fn) { el._redraw = fn; ro.observe(el); }

/* Колонки по дням: points [{day, value, sub}] */
function columns(points, { fmt = fmtN, height = 170, tipLabel = '' } = {}) {
  const wrap = h('div', { class: 'cols' });
  const draw = () => {
    wrap.innerHTML = '';
    if (!points.length) { wrap.append(h('div', { class: 'empty' }, 'нет данных')); return; }
    const W = Math.max(280, wrap.clientWidth || 480), H = height, padL = 58, padR = 10, padT = 10, padB = 26;
    const pw = W - padL - padR, ph = H - padT - padB;
    const max = Math.max(...points.map(p => p.value || 0), 1);
    const nice = niceMax(max);
    const el = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img' });
    const grid = svg('g', { class: 'grid' });
    for (let i = 0; i <= 3; i++) {
      const y = padT + ph - ph * i / 3;
      grid.append(svg('line', { x1: padL, x2: W - padR, y1: y, y2: y }));
      const t = svg('text', { x: padL - 6, y: y + 4, 'text-anchor': 'end' }); t.textContent = fmt(nice * i / 3); el.append(t);
    }
    el.append(grid);
    const slot = pw / points.length, bw = Math.min(24, Math.max(2, slot - 2));
    let lastLabelX = -999;
    points.forEach((p, i) => {
      const x = padL + i * slot + (slot - bw) / 2;
      const bh = (p.value || 0) / nice * ph;
      const g = svg('g', { class: 'hit' });
      const path = svg('path', { d: roundTop(x, padT + ph - bh, bw, Math.max(bh, p.value ? 2 : 0), 4), class: 'mark' });
      const hit = svg('rect', { x: padL + i * slot, y: padT, width: slot, height: ph, fill: 'transparent' });
      g.append(path, hit);
      g.addEventListener('pointermove', e => { showTip(e, `<b>${esc(fmt(p.value))}</b>${esc(tipLabel)} · ${esc(fmtDate(p.day))}${p.sub ? `<br><small>${esc(p.sub)}</small>` : ''}`); path.classList.add('hot'); });
      g.addEventListener('pointerleave', () => { hideTip(); path.classList.remove('hot'); });
      el.append(g);
      const every = Math.max(1, Math.ceil(points.length / Math.floor(pw / 64)));
      const isLast = i === points.length - 1;
      if (i % every === 0 && (!isLast || true) && !(isLast && (i % every !== 0))) { const t = svg('text', { x: x + bw / 2, y: H - 8, 'text-anchor': 'middle' }); t.textContent = fmtDate(p.day); el.append(t); lastLabelX = x + bw / 2; }
      else if (isLast && x + bw / 2 - lastLabelX > 70) { const t = svg('text', { x: x + bw / 2, y: H - 8, 'text-anchor': 'middle' }); t.textContent = fmtDate(p.day); el.append(t); }
    });
    el.append(svg('line', { x1: padL, x2: W - padR, y1: padT + ph, y2: padT + ph, class: 'axis' }));
    wrap.append(el);
  };
  requestAnimationFrame(draw);
  observe(wrap, draw);
  return wrap;
}
function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; const n = m <= 1 ? 1 : m <= 2 ? 2 : m <= 3 ? 3 : m <= 5 ? 5 : m <= 6 ? 6 : 10; return n * p; }

// ---------- общие куски ----------
const facts = items => h('div', { class: 'facts' }, items.filter(Boolean).map(([label, value, sub]) => h('div', { class: 'fact' }, h('b', null, value), h('span', null, label, FACT_HELP[label] ? helpIcon(FACT_HELP[label]) : null), sub ? h('i', null, sub) : null)));
const panel = (title, body, hint, actions) => h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', null, title, PANEL_HELP[title] ? helpIcon(PANEL_HELP[title]) : null), hint ? h('span', { class: 'hint' }, hint) : null, actions || null), h('div', { class: 'panel-body' + (body?.classList?.contains('tbl-wrap') ? ' tight' : '') }, body));
const empty = (title, text) => h('div', { class: 'empty' }, h('b', null, title), text || '');
const skeleton = (rows = 6) => h('div', { class: 'panel-body' }, Array.from({ length: rows }, (_, i) => h('div', { class: 'skeleton', style: `margin:10px 0;width:${90 - (i * 13) % 40}%` })));
const extLink = (href, text) => h('a', { href, target: '_blank', rel: 'noopener', class: 'ext', title: 'Открыть на YouTube' }, text || '', h('span', { html: ICON.ext }));
const kindTag = k => k ? h('span', { class: 'tag' }, k) : null;
const confTag = v => {
  if (v.recog_manual) return h('span', { class: 'tag tag-accent', title: 'размечено оператором' }, 'вручную');
  if (v.recog_conf == null) return null;
  const c = v.recog_conf, cls = c >= 0.8 ? 'tag-good' : c >= 0.6 ? 'tag-warn' : 'tag-bad';
  const method = { cue: 'по подписи', quote: 'по кавычкам', hashtag: 'по хэштегу', segment: 'по сегменту', keyword: 'по тегу', text: 'по тексту', channel: 'по каналу', manual: 'вручную', neuro: 'по кадру, нейросеть' }[v.recog_method] || v.recog_method;
  return h('span', { class: `tag ${cls}`, title: `${method}: ${v.recog_evidence || ''}` }, `${c.toFixed(2).replace('.', ',')} · ${method}`);
};
const scoreCell = s => h('span', { class: 'score' }, h('span', { class: 'bar' }, h('i', { style: `width:${Math.max(2, s)}%` })), h('span', { class: 'num' }, s));
const inbar = (v, max, fmt = fmtN) => h('span', { class: 'inbar' }, h('i', { style: `width:${max ? Math.max(1, v / max * 100) : 0}%` }), h('b', { class: 'num', title: fmtFull(v) }, fmt(v)));

function sortableTable(cols, rows, { key, onSort, rowKey }) {
  const cur = state.sorts[key] || cols.find(c => c.default)?.id;
  const thead = h('thead', null, h('tr', null, cols.map(c => {
    const th = h('th', { class: (c.num ? 'num ' : '') + (c.sort ? 'sortable' : ''), 'aria-sort': c.sort && cur === c.id ? 'descending' : null }, c.label);
    if (c.help && HELP[c.help]) { th.addEventListener('pointerenter', e => showHelp(e, c.help)); th.addEventListener('pointermove', moveTip); th.addEventListener('pointerleave', hideHelp); }
    else if (c.title) { th.addEventListener('pointerenter', e => { tip.className = 'tooltip help-pop'; tip.innerHTML = ''; tip.append(h('p', null, c.title)); tip.hidden = false; moveTip(e); }); th.addEventListener('pointermove', moveTip); th.addEventListener('pointerleave', hideHelp); }
    if (c.sort) th.addEventListener('click', () => { state.sorts[key] = c.id; localStorage.setItem('kt.sorts', JSON.stringify(state.sorts)); onSort(c.id); });
    return th;
  })));
  const tbody = h('tbody', null, rows.map((r, i) => h('tr', { dataset: rowKey ? { key: rowKey(r) } : null }, cols.map(c => h('td', { class: c.num ? 'num' : c.cls || null }, c.cell(r, i))))));
  return h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, thead, tbody));
}

// ---------- обзор ----------
async function renderOverview(main) {
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Какие фильмы и сериалы сейчас режут на Shorts'), h('p', { class: 'lead' }, 'Произведения ранжированы по баллу: скорость набора просмотров, свежие ролики за 7 дней, медиана просмотров и число каналов. Все цифры — по роликам, где фильм опознан.'))));
  const box = h('div'); main.append(box); box.append(skeleton(8));
  const d = await api('/api/overview', withFilters());
  box.innerHTML = '';
  const p = d.period, t = d.totals, lr = d.last_run;
  const share = p.n_all ? Math.round(p.n_recognized / p.n_all * 100) : 0;
  box.append(facts([
    ['роликов за период', fmtFull(p.n_all), `всего в базе ${fmtFull(t.videos)}`],
    ['опознан фильм', fmtFull(p.n_recognized), `${share} % роликов периода`],
    ['произведений', fmtFull(p.titles), `${fmtFull(p.channels)} ${plural(p.channels, 'канал', 'канала', 'каналов')}`],
    ['просмотров у опознанных', fmtN(p.views_recognized), `медиана ${fmtN(p.views_median_recognized)}`],
    ['словарь', fmtN(t.titles_dict), `${fmtN(t.aliases_dict)} написаний`],
    ['последний сбор', lr ? ago(lr.started_at) : 'не было', lr ? `${lr.status === 'running' ? 'идёт' : lr.status === 'ok' ? 'успешно' : lr.status}, новых ${fmtFull(lr.stats.new_videos)}` : 'запусти в разделе «Сбор»'],
  ]));
  if (!p.n_recognized) {
    box.append(panel('Пока нечего показать', empty(t.videos ? 'Ролики собраны, фильмы ещё не опознаны' : 'База пустая', t.videos ? 'Распознавание идёт по мере загрузки словаря и деталей роликов. Проверь раздел «Сбор».' : 'Запусти сбор в разделе «Сбор»: хэштеги → ролики → распознавание.')));
    return;
  }
  const topTbl = sortableTable([
    { id: 'rank', label: '#', cell: (r, i) => i + 1, cls: 'rank' },
    { id: 'label', label: 'Произведение', cell: r => h('div', null, h('a', { href: `#/film/${r.title_id}`, class: 'title-link' }, r.label), h('span', { class: 'sub' }, [r.kind_ru, r.year, r.genres.slice(0, 2).join(', ')].filter(Boolean).join(' · '))) },
    { id: 'n_shorts', label: 'Роликов', num: true, cell: r => h('span', null, fmtFull(r.n_shorts), r.n_fresh7 ? h('span', { class: 'sub' }, `+${r.n_fresh7} за 7 дн`) : null) },
    { id: 'n_channels', label: 'Каналов', num: true, cell: r => fmtFull(r.n_channels) },
    { id: 'views_median', label: 'Медиана', num: true, title: 'медиана просмотров на ролик', cell: r => h('span', { title: fmtFull(r.views_median) }, fmtN(r.views_median)) },
    { id: 'vpd_sum', label: 'Просм./день', num: true, title: 'сумма просмотров в день по всем роликам (просмотры ÷ возраст)', cell: r => h('span', { title: fmtFull(r.vpd_sum) }, fmtN(r.vpd_sum)) },
    { id: 'score', label: 'Балл', num: true, cell: r => scoreCell(r.score) },
  ], d.top, { key: 'ov-top', onSort: () => {} });
  const oppList = d.opportunities.length ? h('ul', { class: 'src-list' }, d.opportunities.map(r => h('li', null, h('div', null, h('a', { href: `#/film/${r.title_id}`, class: 'title-link' }, r.label), h('span', { class: 'sub' }, `${r.n_shorts} ${plural(r.n_shorts, 'ролик', 'ролика', 'роликов')} у ${r.n_channels} ${plural(r.n_channels, 'канала', 'каналов', 'каналов')}`)), h('span', { class: 'meta num', title: 'медиана просмотров' }, fmtN(r.views_median))))) : empty('Пока нет', 'Появятся произведения с высокой медианой просмотров и не больше трёх каналов.');
  const risingList = h('ul', { class: 'src-list' }, d.rising.filter(r => r.n_fresh7).slice(0, 10).map(r => h('li', null, h('div', null, h('a', { href: `#/film/${r.title_id}`, class: 'title-link' }, r.label), h('span', { class: 'sub' }, `+${r.n_fresh7} ${plural(r.n_fresh7, 'ролик', 'ролика', 'роликов')} за 7 дней`)), h('span', { class: 'meta num', title: 'просмотров в день' }, fmtN(r.vpd_sum) + '/дн'))));
  box.append(h('div', { class: 'grid grid-2' },
    panel('Топ произведений по баллу', topTbl, `период: ${periodLabel()}`, h('a', { class: 'btn btn-sm', href: '#/films' }, 'все произведения')),
    h('div', { class: 'grid', style: 'align-content:start' },
      panel('Мало конкурентов', oppList, 'медиана выше 75-го перцентиля, каналов ≤ 3'),
      panel('Набирают на этой неделе', risingList.children.length ? risingList : empty('Нет свежих роликов', 'За последние 7 дней опознанных публикаций не найдено.'), 'новые ролики за 7 дней'),
    ),
  ));
  const daily = d.daily;
  box.append(h('div', { class: 'grid grid-halves', style: 'margin-top:18px' },
    panel('Опознанные ролики по дню публикации', columns(daily.map(x => ({ day: x.day, value: x.n, sub: `всего роликов за день: ${x.n_all}` })), { fmt: fmtFull, tipLabel: 'роликов' }), 'штук в день'),
    panel('Просмотры роликов по дню публикации', columns(daily.map(x => ({ day: x.day, value: x.views })), { tipLabel: 'просмотров' }), 'сумма на сегодня'),
  ));
  const meth = p.methods || {};
  const methodRu = { cue: 'подпись «Название: …»', quote: 'кавычки', hashtag: 'хэштег', segment: 'сегмент заголовка', keyword: 'тег ролика', text: 'название в тексте', channel: 'канал', manual: 'вручную', neuro: 'нейросеть по кадру' };
  box.append(h('div', { class: 'grid grid-halves', style: 'margin-top:18px' },
    panel('Как опознаны ролики', hbar(Object.entries(meth).map(([k, v]) => ({ label: methodRu[k] || k, value: v })).sort((a, b) => b.value - a.value), { fmt: fmtFull }), 'по методу распознавания'),
    panel('Полнота данных', h('dl', { class: 'kv' },
      h('dt', null, 'роликов в базе'), h('dd', { class: 'num' }, fmtFull(t.videos)),
      h('dt', null, 'с деталями (дата, длительность, канал)'), h('dd', { class: 'num' }, `${fmtFull(t.with_details)} · ещё ${fmtFull(t.no_details)} в очереди`),
      h('dt', null, 'опознано с уверенностью ≥ ' + state.filters.min_conf.replace('.', ',')), h('dd', { class: 'num' }, fmtFull(t.recognized)),
      h('dt', null, 'опознано, но ниже порога'), h('dd', { class: 'num' }, h('a', { href: '#/shorts?state=lowconf' }, fmtFull(t.lowconf))),
      h('dt', null, 'не опознано (есть детали)'), h('dd', { class: 'num' }, h('a', { href: '#/shorts?state=unrecognized' }, fmtFull(t.unrecognized))),
      h('dt', null, 'удалены с YouTube'), h('dd', { class: 'num' }, fmtFull(t.removed)),
      h('dt', null, 'замеров просмотров'), h('dd', { class: 'num' }, fmtFull(t.snapshots)),
      h('dt', null, 'каналов в слежении'), h('dd', { class: 'num' }, `${fmtFull(t.channels_tracked)} из ${fmtFull(t.channels)} встреченных`),
    ), 'что уже собрано'),
  ));
  api('api/rights/overview').then(rv => {
    const cal = rv.calendar || {};
    const claimList = rv.claimants.length ? h('ul', { class: 'src-list' }, rv.claimants.map(x => h('li', null, h('div', null, h('span', { class: 'title-link' }, x.name), h('span', { class: 'sub' }, x.titles.slice(0, 3).join(', '))), h('span', { class: 'meta num' }, `${x.n} ${plural(x.n, 'снос', 'сноса', 'сносов')}`)))) : empty('Сносов по жалобам не замечено', `Удалено чужих роликов всего: ${fmtFull(rv.removed.total)}. Нарезки из календаря и топа перепроверяются каждый прогон.`);
    const blockedList = rv.blocked.length ? h('ul', { class: 'src-list' }, rv.blocked.map(b => h('li', null, h('div', null, h('a', { href: `#/film/${b.title_id}`, class: 'title-link' }, b.label), h('span', { class: 'sub' }, (b.reasons || []).slice(0, 2).join('; '))), h('span', { class: 'meta num', title: 'риск' }, b.risk)))) : h('p', { class: 'muted' }, 'Ни одно произведение не отсеяно.');
    const evList = rv.events.length ? h('ul', { class: 'src-list' }, rv.events.map(e => h('li', null, eventTag(e.kind), h('div', null, e.title_id ? h('a', { href: `#/film/${e.title_id}`, class: 'title-link' }, e.label || e.title_id) : null, e.note ? h('span', { class: 'sub' }, e.note) : null), h('span', { class: 'meta' }, ago(e.ts))))) : h('p', { class: 'muted' }, 'Претензий на наши ролики не записано. Отмечайте их на странице произведения: оценка учится на этом.');
    box.append(h('div', { class: 'grid grid-halves', style: 'margin-top:18px' },
      panel('Авторские права', h('div', null,
        h('div', { class: 'status-line' }, h('span', null, 'в календаре: ', h('b', null, `${cal.ok || 0} проходит · ${cal.caution || 0} с оговорками · ${cal.block || 0} не проходит · ${cal.unknown || 0} не проверено`)), h('span', null, 'политика: ', h('b', null, rv.policy_ru)), h('span', null, 'проверено произведений: ', h('b', null, fmtFull(rv.checked)))),
        h('h3', { style: 'margin:12px 0 6px' }, 'Не проходят'), blockedList,
        h('h3', { style: 'margin:12px 0 6px' }, 'Кто сносит чужие нарезки'), claimList), rv.last_checked ? `обновлено ${ago(rv.last_checked)}` : 'ещё не проверялось', h('a', { class: 'btn btn-sm', href: '#/collect' }, 'политика')),
      panel('Наш опыт с правами', evList, 'заявки, блокировки и страйки на наших роликах')));
  }).catch(() => {});
  api('api/videos/overview').then(pv => {
    const lv = pv.levels || {};
    const list = pv.top_picks.length ? h('ul', { class: 'src-list' }, pv.top_picks.map(p => h('li', null, h('div', null, h('a', { href: `#/video/${p.video_id}`, class: 'title-link' }, p.title), h('span', { class: 'sub' }, [p.topic, p.audience_fit != null ? `аудитория ${Math.round(p.audience_fit * 100)} %` : null, p.direct_file_url ? 'mp4 готов' : null].filter(Boolean).join(' · '))), h('span', { class: 'meta num', title: 'балл чистоты' }, p.score)))) : empty('Подборка пуста', pv.total ? 'Чистых роликов с баллом от порога пока нет: посмотрите «проверить» в разделе «Видео».' : 'Нужен ключ YouTube Data API в «Сборе», потом «обновить чарты и анализ» в разделе «Видео».');
    box.append(h('div', { style: 'margin-top:18px' }, panel('Видео под перезалив', h('div', null,
      h('div', { class: 'status-line' }, h('span', null, 'в чартах: ', h('b', null, fmtFull(pv.total))), h('span', null, 'чистых: ', h('b', null, fmtFull(lv.clean || 0))), h('span', null, 'проверить: ', h('b', null, fmtFull(lv.check || 0))), h('span', null, 'не брать: ', h('b', null, fmtFull(lv.no || 0))), h('span', null, 'в подборке: ', h('b', null, fmtFull(pv.picks))), h('span', null, 'Creative Commons: ', h('b', null, fmtFull(pv.cc))), h('span', null, 'квота Data API сегодня: ', h('b', null, fmtFull(pv.quota_today)))),
      h('h3', { style: 'margin:12px 0 6px' }, 'Подборка'), list), pv.last_run ? `обновлено ${ago(pv.last_run.ts)}` : 'ещё не собиралось', h('a', { class: 'btn btn-sm', href: '#/videos' }, 'все видео'))));
  }).catch(() => {});
}
const periodLabel = () => ({ '7': '7 дней', '30': '30 дней', '90': '90 дней', all: 'всё время' })[state.filters.period];

// ---------- фильмы ----------
async function renderFilms(main) {
  const q = state.query;
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Фильмы и сериалы'), h('p', { class: 'lead' }, 'Всё, что опознано в собранных роликах. Сортируй по столбцам, кликай по названию — внутри ролики, каналы, хэштеги и динамика.')),
    h('div', { class: 'actions' }, h('a', { class: 'btn', href: abs(`api/export/films.csv?${new URLSearchParams(withFilters())}`), download: true, html: ICON.dl + ' CSV' }))));
  const search = h('input', { type: 'search', placeholder: 'поиск по названию', value: q.q || '', 'aria-label': 'Поиск по названию' });
  const kindSel = h('select', { 'aria-label': 'Тип произведения' }, [['all', 'все типы'], ['film', 'фильмы'], ['series', 'сериалы'], ['animated', 'мультфильмы'], ['animated_series', 'мультсериалы'], ['anime', 'аниме'], ['tv_show', 'телешоу']].map(([v, l]) => h('option', { value: v, selected: (q.kind || 'all') === v }, l)));
  const genreSel = h('select', { 'aria-label': 'Жанр' }, h('option', { value: '' }, 'все жанры'));
  const toolbar = h('div', { class: 'toolbar' }, h('div', { class: 'grow' }, search), kindSel, genreSel);
  main.append(toolbar);
  const box = h('div'); main.append(box);
  const apply = () => { const p = new URLSearchParams(); if (search.value) p.set('q', search.value); if (kindSel.value !== 'all') p.set('kind', kindSel.value); if (genreSel.value) p.set('genre', genreSel.value); go('#/films' + (p.toString() ? '?' + p : '')); };
  search.addEventListener('input', debounce(apply, 300)); kindSel.addEventListener('change', apply); genreSel.addEventListener('change', apply);
  api('/api/interests', withFilters()).then(d => { d.genres.forEach(g => { if (g.key !== 'жанр неизвестен') genreSel.append(h('option', { value: g.key, selected: q.genre === g.key }, `${g.key} (${g.n})`)); }); }).catch(() => {});
  box.append(skeleton(10));
  const sort = state.sorts.films || 'score';
  const d = await api('/api/films', withFilters({ sort, kind: q.kind || 'all', genre: q.genre, q: q.q, limit: 300 }));
  box.innerHTML = '';
  if (!d.items.length) { box.append(panel('Ничего не найдено', empty('Нет опознанных произведений с такими фильтрами', 'Попробуй расширить период или снизить порог уверенности.'))); return; }
  const maxViews = Math.max(...d.items.map(r => r.views_sum));
  const detailed = localStorage.getItem('kt.filmsDetail') === '1';
  const modeBtn = h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { localStorage.setItem('kt.filmsDetail', detailed ? '0' : '1'); render(); } }, detailed ? 'простой вид' : 'подробный вид');
  const DETAIL_ONLY = new Set(['fresh', 'p90', 'growth', 'like', 'dur', 'small']);
  const tbl = sortableTable([
    { id: 'rank', label: '#', cell: (r, i) => i + 1, cls: 'rank' },
    { id: 'label', label: 'Произведение', sort: true, cell: r => h('div', null, h('a', { href: `#/film/${r.title_id}`, class: 'title-link' }, r.label), r.opportunity ? h('span', { class: 'tag tag-good', title: 'высокая медиана, мало каналов' }, 'мало конкурентов') : null, h('span', { class: 'sub' }, [r.kind_ru, r.year, r.countries[0], r.genres.slice(0, 3).join(', ')].filter(Boolean).join(' · '))) },
    { id: 'shorts', label: 'Роликов', num: true, sort: true, help: 'references', cell: r => fmtFull(r.n_shorts) },
    { id: 'fresh', label: '+7 дн', num: true, sort: true, help: 'fresh', cell: r => r.n_fresh7 || '—' },
    { id: 'channels', label: 'Каналов', num: true, sort: true, help: 'channels', cell: r => fmtFull(r.n_channels) },
    { id: 'views', label: 'Просмотры', num: true, sort: true, cell: r => inbar(r.views_sum, maxViews) },
    { id: 'median', label: 'Медиана', num: true, sort: true, help: 'median', cell: r => h('span', { title: fmtFull(r.views_median) }, fmtN(r.views_median)) },
    { id: 'p90', label: 'P90', num: true, title: '90 % роликов набрали меньше', cell: r => h('span', { title: fmtFull(r.views_p90) }, fmtN(r.views_p90)) },
    { id: 'vpd', label: 'Просм./день', num: true, sort: true, help: 'vpd', cell: r => h('span', { title: fmtFull(r.vpd_sum) }, fmtN(r.vpd_sum)) },
    { id: 'growth', label: 'Прирост 7 дн', num: true, sort: true, help: 'growth', cell: r => r.growth7_measured ? h('span', { title: `замерено роликов: ${r.growth7_measured}` }, '+' + fmtN(r.growth7)) : h('span', { class: 'muted', title: 'нужно минимум два точных замера' }, '—') },
    { id: 'outliers', label: 'Аутлаеры', num: true, sort: true, help: 'outliers', cell: r => r.outlier_share != null ? h('span', { title: `${r.outliers} из ${r.outlier_rated} роликов в 3+ раза выше нормы канала, максимум ×${r.outlier_max}` }, Math.round(r.outlier_share * 100) + ' %', r.outlier_max >= 10 ? h('span', { class: 'tag tag-good', style: 'margin-left:4px' }, '×' + Math.round(r.outlier_max)) : null) : h('span', { class: 'muted' }, '—') },
    { id: 'small', label: 'Малые каналы', num: true, sort: true, help: 'small', cell: r => r.small_channel_median ? h('span', { title: `${r.small_channel_n} роликов с каналов до 100 тыс. подписчиков` }, fmtN(r.small_channel_median)) : h('span', { class: 'muted' }, '—') },
    { id: 'wiki', label: 'Интерес', num: true, sort: true, help: 'interest', cell: r => r.wiki_7d != null ? h('span', { title: `Википедия: ${fmtFull(r.wiki_7d)} просмотров статьи в день${r.trends_7d != null ? `; Google Trends ${Math.round(r.trends_7d)}/100` : ''}` }, fmtN(r.wiki_7d), r.wiki_ratio ? h('span', { class: 'tag ' + (r.wiki_ratio >= 1.5 ? 'tag-good' : r.wiki_ratio < 0.7 ? 'tag-warn' : ''), style: 'margin-left:4px' }, '×' + String(r.wiki_ratio).replace('.', ',')) : null) : h('span', { class: 'muted' }, '—') },
    ...(d.items.some(r => r.like_rate != null) ? [{ id: 'like', label: 'Лайки', num: true, title: 'лайков на 100 просмотров, где известны лайки', cell: r => r.like_rate != null ? fmtPct(r.like_rate, 1) : h('span', { class: 'muted' }, '—') }] : []),
    { id: 'dur', label: 'Длит.', num: true, title: 'средняя длительность ролика', cell: r => fmtDur(r.dur_avg) },
    { id: 'rights', label: 'Права', help: 'rights', cell: r => rightsTag(r.rights, true) },
    { id: 'score', label: 'Балл', num: true, sort: true, help: 'score', cell: r => scoreCell(r.score) },
  ].filter(c => detailed || !DETAIL_ONLY.has(c.id)), d.items, { key: 'films', onSort: () => render() });
  box.append(panel(`${fmtFull(d.total)} ${plural(d.total, 'произведение', 'произведения', 'произведений')}`, tbl, `период: ${periodLabel()}, уверенность ≥ ${state.filters.min_conf.replace('.', ',')}`, modeBtn));
}

// ---------- карточка фильма ----------
async function renderFilm(main, titleId) {
  main.append(h('a', { href: '#/films', class: 'back', html: ICON.back + ' к списку' }));
  const box = h('div'); main.append(box); box.append(skeleton(8));
  const [d, insRes, srcRes, exRes] = await Promise.all([api(`/api/films/${encodeURIComponent(titleId)}`, withFilters()), api(`api/films/${encodeURIComponent(titleId)}/insights`).catch(() => ({ insights: null })), api(`api/films/${encodeURIComponent(titleId)}/sources`).catch(() => ({ items: [] })), api(`api/films/${encodeURIComponent(titleId)}/explain`).catch(() => ({ explain: null }))]);
  box.innerHTML = '';
  const t = d.title, s = d.stats;
  const links = [];
  if (t.kp_id) links.push(h('a', { href: `https://www.kinopoisk.ru/film/${t.kp_id}/`, target: '_blank', rel: 'noopener', class: 'ext' }, 'Кинопоиск', h('span', { html: ICON.ext })));
  if (t.imdb_id) links.push(h('a', { href: `https://www.imdb.com/title/${t.imdb_id}/`, target: '_blank', rel: 'noopener', class: 'ext' }, 'IMDb', h('span', { html: ICON.ext })));
  box.append(h('div', { class: 'page-head' }, h('div', { class: 'film-head' }, poster(titleId, 'poster poster-lg'), h('div', null, h('h1', null, t.label, t.year ? h('span', { class: 'muted' }, ` (${t.year})`) : null), h('p', { class: 'lead' }, [t.kind_ru, t.original_title && t.original_title !== t.label ? t.original_title : (t.label_en !== t.label ? t.label_en : null), t.countries.join(', '), t.genres.join(', '), t.franchise ? `франшиза: ${t.franchise}` : null].filter(Boolean).join(' · ')))),
    h('div', { class: 'actions' }, links, h('a', { class: 'btn btn-sm', href: `#/shorts?title_id=${encodeURIComponent(titleId)}` }, 'все ролики в разметке'))));
  box.append(facts([
    ['роликов', fmtFull(s.n_shorts), `${fmtFull(s.n_channels)} ${plural(s.n_channels, 'канал', 'канала', 'каналов')}`],
    ['просмотров всего', fmtN(s.views_sum), `макс. ${fmtN(s.views_max)}`],
    ['медиана просмотров', fmtN(s.views_median), `P90 ${fmtN(s.views_p90)}`],
    ['просмотров в день', fmtN(s.vpd_sum), 'сумма по роликам'],
    ['прирост за 7 дней', s.growth7 ? '+' + fmtN(s.growth7) : '—', 'по точным замерам'],
    ['средняя длительность', fmtDur(s.dur_avg)],
  ]));
  const vidTbl = sortableTable([
    { id: 'thumb', label: '', cell: v => h('img', { class: 'thumb', src: abs(`thumb/${v.video_id}.jpg`), alt: '', loading: 'lazy', onerror: e => { e.target.style.visibility = 'hidden'; } }) },
    { id: 'title', label: 'Ролик', cls: 'row-title', cell: v => h('div', null, extLink(v.url, h('span', { class: 't', title: v.title || v.video_id }, v.title || v.video_id)), h('span', { class: 'sub' }, [v.channel_name ? h('a', { href: `#/channel/${v.channel_id}` }, v.channel_name) : null, ' · ', fmtDate(v.published_at || v.first_seen_at), ' · ', fmtDur(v.duration_s)])) },
    { id: 'views', label: 'Просмотры', num: true, cell: v => h('span', { title: fmtFull(v.views) }, fmtN(v.views)) },
    { id: 'vpd', label: 'В день', num: true, cell: v => fmtN(v.vpd) },
    { id: 'outx', label: '× канала', num: true, help: 'outliers', cell: v => v.outlier_x != null ? h('span', { class: v.outlier_x >= 3 ? 'tag tag-good' : '' }, '×' + v.outlier_x) : h('span', { class: 'muted' }, '—') },
    { id: 'growth', label: '+7 дн', num: true, cell: v => v.growth7 != null ? '+' + fmtN(v.growth7) : h('span', { class: 'muted' }, '—') },
    ...(d.videos.some(v => v.likes != null) ? [{ id: 'likes', label: 'Лайки', num: true, cell: v => v.likes != null ? fmtN(v.likes) : h('span', { class: 'muted' }, '—') }] : []),
    { id: 'conf', label: 'Опознан', cell: v => confTag(v) },
  ], d.videos, { key: 'film-videos', onSort: () => {} });
  const srcBody = h('div');
  const drawSources = (items) => {
    srcBody.innerHTML = '';
    if (!items.length) srcBody.append(h('p', { class: 'muted' }, 'Исходники ещё не искали.'));
    else srcBody.append(h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th', null, 'Видео'), h('th', null, 'Канал'), h('th', { class: 'num' }, 'Мин'), h('th', null, 'Что это'), h('th', null, 'Проверка'), h('th', null, 'Файл'))),
      h('tbody', null, items.map(x => h('tr', null, h('td', { class: 'row-title' }, extLink(x.url, h('span', { class: 't', title: x.label }, x.label)), h('span', { class: 'sub' }, /rutube\.ru/.test(x.url) ? 'Rutube' : 'YouTube')), h('td', null, x.channel), h('td', { class: 'num' }, x.duration_s ? Math.round(x.duration_s / 60) : '—'),
        h('td', null, x.kind === 'episode' ? `серия${x.episode ? ' ' + x.episode : ''}${x.season ? ', сезон ' + x.season : ''}` : x.kind === 'pack' ? 'все серии' : x.kind === 'film' ? 'полный фильм' : 'другое', x.language ? h('span', { class: 'sub' }, x.language) : null),
        h('td', null, x.verified ? h('span', { class: 'tag tag-good', title: x.note }, `подтверждено ${Math.round((x.trust || 0) * 100)} %`) : h('span', { class: 'tag tag-warn', title: x.note }, x.note && x.note !== 'не проверено моделью' ? 'отклонено' : 'не проверено')),
        h('td', null, /rutube\.ru/.test(x.url) ? fileButton(x, titleId) : h('span', { class: 'muted', title: 'нарезчик принимает ссылку YouTube сам' }, 'ссылка в нарезчик'))))))));
    const btn = h('button', { class: 'btn btn-sm', type: 'button', style: 'margin-top:8px' }, items.length ? 'искать заново' : 'найти исходники на YouTube и Rutube');
    btn.addEventListener('click', async () => { btn.disabled = true; btn.textContent = 'ищу…'; try { const r = await post(`api/films/${titleId}/sources`); drawSources(r.items); toast(`Найдено ${r.items.length}`); } catch (e) { toast(e.message); btn.disabled = false; } });
    srcBody.append(h('div', { class: 'actions', style: 'margin-top:8px' }, btn, copyHint(titleId), h('a', { class: 'btn btn-sm', href: abs(`api/brief/${titleId}`), target: '_blank', rel: 'noopener' }, 'бриф JSON')));
  };
  drawSources(srcRes.items || []);
  const exBody = h('div');
  exBody.append(explainBlock(exRes.explain ? exRes.explain.text : null, titleId, text => { exBody.innerHTML = ''; exBody.append(explainBlock(text, titleId)); }));
  box.append(h('div', { class: 'grid grid-halves' },
    panel('Исходник для нарезки', srcBody, 'полный фильм или серии на YouTube и Rutube, проверка нейросетью'),
    panel('Почему это стоит резать', exBody, 'нейросеть по статистике и лучшим чужим роликам')));
  box.append(h('div', { style: 'margin-top:18px' }, panel('Авторские права', rightsPanel(titleId), 'пройдёт ли в календарь и к нарезчику: сносы чужих нарезок, правообладатель, исходник, наш опыт')));
  const intBody = h('div');
  const drawInterest = (d) => {
    intBody.innerHTML = '';
    if (!d.lines.length) intBody.append(h('p', { class: 'muted' }, 'Данных ещё нет: интерес считается после прогона сбора для произведений из топа и календаря.'));
    else intBody.append(h('ul', { class: 'why' }, d.lines.map(l => h('li', null, l))));
    if (d.series.wiki && d.series.wiki.length) intBody.append(h('h3', { style: 'margin-top:8px' }, 'Просмотры статьи в Википедии по дням'), columns(d.series.wiki.slice(-45).map(x => ({ day: x.day, value: x.value })), { fmt: fmtN, tipLabel: 'просмотров статьи', height: 130 }));
    if (d.series.trends && d.series.trends.length) intBody.append(h('h3', { style: 'margin-top:8px' }, 'Google Trends, Россия (0–100)'), columns(d.series.trends.slice(-60).map(x => ({ day: x.day, value: x.value })), { fmt: v => String(Math.round(v)), tipLabel: 'из 100', height: 110 }));
    const btn = h('button', { class: 'btn btn-sm', type: 'button', style: 'margin-top:8px' }, 'обновить сейчас');
    btn.addEventListener('click', async () => { btn.disabled = true; btn.textContent = 'запрашиваю…'; try { drawInterest(await api(`api/films/${titleId}/interest`, { refresh: 1 })); toast('Интерес обновлён'); } catch (e) { toast(e.message); btn.disabled = false; } });
    intBody.append(btn);
  };
  api(`api/films/${encodeURIComponent(titleId)}/interest`).then(drawInterest).catch(() => intBody.append(h('p', { class: 'muted' }, 'не удалось загрузить')));
  box.append(h('div', { style: 'margin-top:18px' }, panel('Интерес аудитории', intBody, 'Википедия и Google Trends, независимо от наших шортсов')));
  box.append(h('div', { style: 'margin-top:18px' }, panel('Какие сцены режут и какие хуки заходят', insightsPanel(titleId, insRes.insights, d.videos.length), 'нейросеть по заголовкам и просмотрам чужих роликов; реплики для поиска по субтитрам')));
  box.append(h('div', { class: 'grid grid-2', style: 'margin-top:18px' },
    panel('Референсы: чужие нарезки, которые зашли', vidTbl, `${d.videos.length} ${plural(d.videos.length, 'ролик', 'ролика', 'роликов')} за ${periodLabel()}`),
    h('div', { class: 'grid', style: 'align-content:start' },
      d.seasons && d.seasons.length ? panel('По сезонам', hbar(d.seasons.map(x => ({ label: `${x.season} сезон`, value: x.views, sub: `${x.n} ${plural(x.n, 'ролик', 'ролика', 'роликов')}` })), { maxRows: 20 }), 'просмотры роликов с указанным сезоном') : null,
      panel('Каналы', hbar(d.channels.map(c => ({ label: c.name || c.channel_id, value: c.views, sub: `${c.n} ${plural(c.n, 'ролик', 'ролика', 'роликов')}`, id: c.channel_id })), { onClick: r => go(`#/channel/${r.id}`) }), 'просмотры по каналам'),
      panel('Хэштеги', d.hashtags.length ? h('div', { class: 'chips' }, d.hashtags.map(x => h('span', { class: 'tag' }, `#${x.tag} `, h('span', { class: 'muted' }, x.n)))) : empty('Нет хэштегов'), 'в заголовках и описаниях'),
      panel('Длительность роликов', hbar(d.durations.map(x => ({ label: x.bucket, value: x.n })), { fmt: fmtFull }), 'штук'),
      panel('Публикации по дням', columns(d.daily.map(x => ({ day: x.day, value: x.n, sub: `просмотров: ${fmtN(x.views)}` })), { fmt: fmtFull, tipLabel: 'роликов', height: 140 })),
    )));
}

// ---------- интересы ----------
async function renderInterests(main) {
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Что заходит по интересам'), h('p', { class: 'lead' }, 'Срезы по опознанным роликам: жанры, типы, страны, эпохи, оформление заголовка, длительность, время публикации. Медиана честнее суммы: сумму делают два-три вирусных ролика.'))));
  const metricSel = h('select', { 'aria-label': 'Метрика' }, [['views_median', 'медиана просмотров'], ['n', 'число роликов'], ['views_sum', 'сумма просмотров'], ['views_p75', '75-й перцентиль просмотров'], ['n_titles', 'число произведений']].map(([v, l]) => h('option', { value: v, selected: (state.query.m || 'views_median') === v }, l)));
  metricSel.addEventListener('change', () => go('#/interests?m=' + metricSel.value));
  main.append(h('div', { class: 'toolbar' }, h('span', { class: 'fld' }, 'сравнивать по ', metricSel)));
  const box = h('div'); main.append(box); box.append(skeleton(8));
  const d = await api('/api/interests', withFilters());
  box.innerHTML = '';
  if (!d.total.n) { box.append(panel('Пока пусто', empty('Нет опознанных роликов за период'))); return; }
  box.append(facts([['роликов', fmtFull(d.total.n)], ['просмотров всего', fmtN(d.total.views_sum)], ['медиана просмотров', fmtN(d.total.views_median)]]));
  const m = metricSel.value;
  const fmt = m === 'n' || m === 'n_titles' ? fmtFull : fmtN;
  const mk = (items, sortByMetric = true, maxRows = 14) => hbar((sortByMetric ? [...items].sort((a, b) => b[m] - a[m]) : items).map(x => ({ label: x.key, value: x[m], tip: `<b>${esc(fmt(x[m]))}</b>${esc(x.key)}<br><small>роликов ${fmtFull(x.n)} · медиана ${fmtN(x.views_median)} · всего ${fmtN(x.views_sum)} · произведений ${x.n_titles} · каналов ${x.n_channels}</small>` })), { fmt, maxRows });
  box.append(h('div', { class: 'grid grid-3' },
    panel('Жанры', mk(d.genres), 'из словаря произведений'),
    panel('Тип произведения', mk(d.kinds)),
    panel('Страна производства', mk(d.countries)),
    panel('Эпоха выхода', mk(d.decades, false)),
    panel('Длительность ролика', mk(d.durations, false), 'что лучше смотрят'),
    panel('Приёмы заголовка', mk(d.features), 'ролик может входить в несколько строк'),
    panel('Хэштеги', mk(d.hashtags, true, 20), 'первые 8 хэштегов ролика'),
    panel('Язык заголовка', mk(d.langs)),
    panel('День недели публикации', mk(d.weekdays, false)),
    panel('Час публикации (МСК)', mk(d.hours, false, 24)),
  ));
}

// ---------- каналы ----------
async function renderChannels(main) {
  const q = state.query;
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Каналы'), h('p', { class: 'lead' }, 'Кто режет кино и как у них это заходит. Каналы с несколькими опознанными роликами берутся в слежение автоматически: их вкладка Shorts и RSS опрашиваются каждый прогон.'))));
  const search = h('input', { type: 'search', placeholder: 'поиск по названию канала', value: q.q || '', 'aria-label': 'Поиск канала' });
  search.addEventListener('input', debounce(() => go('#/channels' + (search.value ? '?q=' + encodeURIComponent(search.value) : '')), 300));
  main.append(h('div', { class: 'toolbar' }, h('div', { class: 'grow' }, search)));
  const box = h('div'); main.append(box); box.append(skeleton(10));
  const sort = state.sorts.channels || 'views';
  const d = await api('/api/channels', withFilters({ sort, q: q.q, limit: 300 }));
  box.innerHTML = '';
  if (!d.items.length) { box.append(panel('Нет каналов', empty('Ничего не найдено'))); return; }
  const maxV = Math.max(...d.items.map(c => c.views_sum));
  const tbl = sortableTable([
    { id: 'rank', label: '#', cell: (r, i) => i + 1, cls: 'rank' },
    { id: 'name', label: 'Канал', sort: true, cell: c => h('div', null, h('a', { href: `#/channel/${c.channel_id}`, class: 'title-link' }, c.name || c.channel_id), c.tracked ? h('span', { class: 'tag tag-accent', title: 'в слежении' }, ' слежение') : null, h('span', { class: 'sub' }, c.top_titles.map(t => t.label).join(', ') || 'произведения не опознаны')) },
    { id: 'followers', label: 'Подписчики', num: true, sort: true, cell: c => c.followers != null ? fmtN(c.followers) : h('span', { class: 'muted' }, '—') },
    { id: 'shorts', label: 'Роликов', num: true, sort: true, cell: c => fmtFull(c.n_shorts) },
    { id: 'recognized', label: 'Опознано', num: true, sort: true, cell: c => `${c.n_recognized} (${c.n_shorts ? Math.round(c.n_recognized / c.n_shorts * 100) : 0} %)` },
    { id: 'titles', label: 'Произв.', num: true, title: 'разных произведений', cell: c => c.n_titles },
    { id: 'views', label: 'Просмотры', num: true, sort: true, cell: c => inbar(c.views_sum, maxV) },
    { id: 'median', label: 'Медиана', num: true, sort: true, cell: c => fmtN(c.views_median) },
    { id: 'max', label: 'Макс', num: true, cell: c => fmtN(c.views_max) },
    { id: 'week', label: 'В неделю', num: true, title: 'роликов в неделю по датам публикаций', cell: c => c.per_week != null ? String(c.per_week).replace('.', ',') : '—' },
    { id: 'dur', label: 'Длит.', num: true, cell: c => fmtDur(c.dur_avg) },
    { id: 'yt', label: '', cell: c => extLink(c.url) },
  ], d.items, { key: 'channels', onSort: () => render() });
  box.append(panel(`${fmtFull(d.total)} ${plural(d.total, 'канал', 'канала', 'каналов')}`, tbl, `ролики за ${periodLabel()}`));
}

async function renderChannel(main, channelId) {
  main.append(h('a', { href: '#/channels', class: 'back', html: ICON.back + ' к каналам' }));
  const box = h('div'); main.append(box); box.append(skeleton(8));
  const d = await api(`/api/channels/${encodeURIComponent(channelId)}`, withFilters());
  box.innerHTML = '';
  const c = d.channel, s = d.stats;
  const trackBtn = h('button', { class: 'btn btn-sm', type: 'button' }, c.tracked ? 'убрать из слежения' : 'взять в слежение');
  trackBtn.addEventListener('click', async () => {
    try {
      if (c.tracked && c.source_id) await post(`/api/sources/${c.source_id}`, { enabled: false }, 'PATCH');
      else await post('/api/sources', { kind: 'channel', value: channelId });
      toast(c.tracked ? 'Канал убран из слежения' : 'Канал добавлен в слежение'); render();
    } catch (e) { toast('Не получилось: ' + e.message); }
  });
  const defBox = assignControl({ placeholder: 'канал режет одно произведение? укажи его', onPick: async (t) => { await post(`/api/channels/${encodeURIComponent(channelId)}/default`, { title_id: t ? t.title_id : null }); toast(t ? `Для канала задано: ${t.label}` : 'Привязка канала снята'); render(); }, allowNone: !!c.default_title_id, noneLabel: 'снять привязку канала' });
  box.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, c.name || channelId), h('p', { class: 'lead' }, [c.handle, c.followers != null ? `${fmtN(c.followers)} подписчиков` : null, c.tracked ? 'в слежении' : 'не в слежении'].filter(Boolean).join(' · '))),
    h('div', { class: 'actions' }, extLink(c.url, 'YouTube'), trackBtn)));
  box.append(facts([['роликов в базе', fmtFull(s.n_shorts)], ['опознано', fmtFull(s.n_recognized), s.n_shorts ? `${Math.round(s.n_recognized / s.n_shorts * 100)} %` : null], ['просмотров всего', fmtN(s.views_sum)], ['медиана', fmtN(s.views_median)], ['максимум', fmtN(s.views_max)]]));
  const vidTbl = sortableTable([
    { id: 'thumb', label: '', cell: v => h('img', { class: 'thumb', src: abs(`thumb/${v.video_id}.jpg`), alt: '', loading: 'lazy', onerror: e => { e.target.style.visibility = 'hidden'; } }) },
    { id: 'title', label: 'Ролик', cls: 'row-title', cell: v => h('div', null, extLink(v.url, h('span', { class: 't', title: v.title || v.video_id }, v.title || v.video_id)), h('span', { class: 'sub' }, `${fmtDate(v.published_at || v.first_seen_at)} · ${fmtDur(v.duration_s)}`)) },
    { id: 'views', label: 'Просмотры', num: true, cell: v => h('span', { title: fmtFull(v.views) }, fmtN(v.views)) },
    { id: 'vpd', label: 'В день', num: true, cell: v => fmtN(v.vpd) },
    { id: 'film', label: 'Произведение', cell: v => v.title_id ? h('div', null, h('a', { href: `#/film/${v.title_id}` }, v.title_label), ' ', confTag(v)) : h('span', { class: 'muted' }, v.has_details ? 'не опознано' : 'нет деталей') },
  ], d.videos, { key: 'ch-videos', onSort: () => {} });
  box.append(h('div', { class: 'grid grid-2' },
    panel('Ролики', vidTbl, `${d.videos.length} за ${periodLabel()}`),
    h('div', { class: 'grid', style: 'align-content:start' },
      panel('Что режет', d.titles.length ? hbar(d.titles.map(t => ({ label: t.label, value: t.n, id: t.title_id })), { fmt: fmtFull, onClick: r => go(`#/film/${r.id}`) }) : empty('Ничего не опознано'), 'роликов по произведениям'),
      panel('Привязка канала', h('div', null, c.default_title_id ? h('p', { style: 'margin-bottom:8px' }, 'Сейчас: ', h('a', { href: `#/film/${c.default_title_id}` }, (d.titles.find(t => t.title_id === c.default_title_id) || {}).label || c.default_title_id)) : h('p', { class: 'muted', style: 'margin-bottom:8px' }, 'Не задана. Если канал режет одно произведение, укажи его — неопознанные ролики канала получат его с уверенностью 0,75.'), defBox)),
    )));
}

// ---------- ролики и ручная разметка ----------
function assignControl({ placeholder, onPick, allowNone = true, noneLabel = 'не фильм', allowCreate = true, channelOption = false }) {
  const input = h('input', { type: 'text', placeholder, autocomplete: 'off', 'aria-label': placeholder });
  const chanCb = channelOption ? h('input', { type: 'checkbox' }) : null;
  const wrap = h('div', { class: 'assign' }, input, channelOption ? h('label', { class: 'fld', title: 'привязать ко всем неопознанным роликам этого канала' }, chanCb, ' весь канал') : null);
  let dd = null;
  const close = () => { if (dd) { dd.remove(); dd = null; } };
  const search = debounce(async () => {
    const q = input.value.trim();
    close();
    if (q.length < 2) return;
    let items = [];
    try { items = (await api('/api/titles/search', { q, limit: 10 })).items; } catch (_) {}
    dd = h('div', { class: 'dropdown' });
    items.forEach(t => dd.append(h('button', { type: 'button', onclick: () => { close(); input.value = ''; onPick(t, chanCb && chanCb.checked); } }, h('span', null, t.label, t.year ? h('span', { class: 'muted' }, ` (${t.year})`) : null), h('span', { class: 'k' }, [t.kind_ru, t.genres.slice(0, 2).join(', ')].filter(Boolean).join(' · ')))));
    if (!items.length) dd.append(h('div', { class: 'muted', style: 'padding:6px 9px;font-size:.85rem' }, 'в словаре не нашлось'));
    if (allowCreate) dd.append(h('button', { type: 'button', onclick: async () => { const label = input.value.trim(); close(); try { const t = await post('/api/titles', { label_ru: label, kind: 'film' }); input.value = ''; onPick(Object.assign({ label }, t), chanCb && chanCb.checked); } catch (e) { toast(e.message); } } }, h('span', null, `создать «${q}»`), h('span', { class: 'k' }, 'новое произведение')));
    if (allowNone) dd.append(h('button', { type: 'button', onclick: () => { close(); input.value = ''; onPick(null, false); } }, h('span', null, noneLabel)));
    wrap.append(dd);
  }, 220);
  input.addEventListener('input', search);
  input.addEventListener('keydown', e => { if (e.key === 'Escape') close(); if (e.key === 'ArrowDown' && dd) { e.preventDefault(); dd.querySelector('button')?.focus(); } });
  wrap.addEventListener('keydown', e => { if (!dd) return; const btns = $$('button', dd); const i = btns.indexOf(document.activeElement); if (e.key === 'ArrowDown' && i >= 0) { e.preventDefault(); (btns[i + 1] || btns[0]).focus(); } if (e.key === 'ArrowUp' && i >= 0) { e.preventDefault(); (btns[i - 1] || input).focus(); } });
  document.addEventListener('click', e => { if (dd && !wrap.contains(e.target)) close(); });
  return wrap;
}

async function renderShorts(main) {
  const q = state.query;
  const stateKey = q.state || 'all';
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Ролики и разметка'), h('p', { class: 'lead' }, 'Все собранные шортсы. Неопознанным роликам можно привязать произведение вручную: сервис запомнит написание и следующий раз опознает сам.')),
    h('div', { class: 'actions' }, h('a', { class: 'btn', href: abs(`api/export/shorts.csv?${new URLSearchParams(withFilters({ state: stateKey }))}`), download: true, html: ICON.dl + ' CSV' }))));
  const chips = h('div', { class: 'chips' }, [['all', 'все'], ['recognized', 'опознанные'], ['lowconf', 'низкая уверенность'], ['unrecognized', 'неопознанные'], ['nodetails', 'без деталей']].map(([v, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(stateKey === v), onclick: () => setQuery({ state: v, offset: null }) }, l)));
  const search = h('input', { type: 'search', placeholder: 'поиск по заголовку, описанию, каналу', value: q.q || '', 'aria-label': 'Поиск по роликам' });
  search.addEventListener('input', debounce(() => setQuery({ q: search.value || null, offset: null }), 350));
  const sortSel = h('select', { 'aria-label': 'Сортировка' }, [['views', 'по просмотрам'], ['vpd', 'по просмотрам в день'], ['date', 'по дате публикации'], ['seen', 'по дате появления в выдаче'], ['conf', 'по уверенности']].map(([v, l]) => h('option', { value: v, selected: (q.sort || 'views') === v }, l)));
  sortSel.addEventListener('change', () => setQuery({ sort: sortSel.value, offset: null }));
  main.append(h('div', { class: 'toolbar' }, chips, h('div', { class: 'grow' }, search), sortSel));
  if (q.title_id || q.channel_id) main.append(h('p', { class: 'muted', style: 'margin:-4px 0 10px' }, 'Фильтр: ', q.title_id ? h('a', { href: `#/film/${q.title_id}` }, 'произведение ' + q.title_id) : null, q.channel_id ? h('a', { href: `#/channel/${q.channel_id}` }, 'канал ' + q.channel_id) : null, ' · ', h('a', { href: '#/shorts' }, 'сбросить')));
  const box = h('div'); main.append(box); box.append(skeleton(10));
  const limit = 50, offset = +(q.offset || 0);
  const d = await api('/api/shorts', withFilters({ state: stateKey, q: q.q, sort: q.sort || 'views', title_id: q.title_id, channel_id: q.channel_id, limit, offset }));
  box.innerHTML = '';
  if (!d.items.length) { box.append(panel('Пусто', empty('Ничего не найдено', 'Смени фильтр состояния или период.'))); return; }
  const rows = d.items.map(v => {
    const tr = h('tr');
    const fill = () => {
      tr.innerHTML = '';
      tr.append(
        h('td', null, h('img', { class: 'thumb', src: abs(`thumb/${v.video_id}.jpg`), alt: '', loading: 'lazy', onerror: e => { e.target.style.visibility = 'hidden'; } })),
        h('td', { class: 'row-title' }, h('div', null, extLink(v.url, h('span', { class: 't', title: v.title || v.video_id }, v.title || v.video_id)), h('span', { class: 'sub' }, [v.channel_id ? h('a', { href: `#/channel/${v.channel_id}` }, v.channel_name || 'канал') : (v.channel_name || '—'), ` · ${v.published_at ? fmtDate(v.published_at) : 'дата неизвестна'} · ${fmtDur(v.duration_s)}${v.lang ? ' · ' + v.lang : ''}`]), v.hashtags.length ? h('span', { class: 'sub' }, v.hashtags.slice(0, 6).map(t => '#' + t).join(' ')) : null)),
        h('td', { class: 'num' }, h('span', { title: fmtFull(v.views) }, fmtN(v.views)), h('span', { class: 'sub' }, fmtN(v.vpd) + '/дн'), v.outlier_x != null && v.outlier_x >= 3 ? h('span', { class: 'sub', title: 'во сколько раз выше медианы роликов этого канала за период' }, h('span', { class: 'tag tag-good' }, `×${v.outlier_x} к норме канала`)) : null),
        h('td', null, v.title_id ? h('div', null, h('a', { href: `#/film/${v.title_id}`, class: 'title-link' }, v.title_label, v.title_year ? h('span', { class: 'muted' }, ` (${v.title_year})`) : null), h('div', null, confTag(v))) : h('span', { class: 'muted' }, v.has_details ? (v.recog_manual ? 'отмечено: не фильм' : 'не опознано') : 'детали ещё не получены'), v.candidates && v.candidates.length && !v.title_id ? h('div', { class: 'evidence', title: 'что искали в словаре' }, 'кандидаты: ' + v.candidates.slice(0, 4).join(' · ')) : null),
        h('td', null, assignControl({ placeholder: v.title_id ? 'заменить произведение' : 'какой это фильм?', channelOption: true, onPick: async (t, wholeChannel) => {
          try {
            let ban = null;
            if (!t && v.title_id && v.recog_evidence && !v.recog_manual && ['hashtag', 'text', 'keyword', 'segment'].includes(v.recog_method)) {
              ban = confirm(`Больше не опознавать «${v.title_label}» по улике «${v.recog_evidence}» во всех роликах?`) ? v.recog_evidence : null;
            }
            const r = await post(`/api/videos/${v.video_id}/assign`, { title_id: t ? t.title_id : null, learn: true, channel_default: !!wholeChannel, ban });
            if (t) { Object.assign(v, { title_id: t.title_id, title_label: t.label, title_year: t.year, recog_manual: true, recog_conf: 1, recog_method: 'manual' }); toast(`Привязано: ${t.label}${r.learned_alias ? `. Выучено написание «${r.learned_alias}»` : ''}${wholeChannel ? '. Применено ко всему каналу' : ''}`); }
            else { Object.assign(v, { title_id: null, title_label: null, recog_manual: true, recog_conf: null }); toast(r.banned ? `Отмечено: не фильм. Улика «${r.banned}» больше не используется` : 'Отмечено: не фильм'); }
            fill();
          } catch (e) { toast('Не получилось: ' + e.message); }
        } }), v.recog_manual ? h('button', { class: 'btn btn-sm', type: 'button', style: 'margin-top:6px', title: 'вернуть автоматическое распознавание', onclick: async () => { try { const r = await post(`/api/videos/${v.video_id}/assign`, { reset: true }); toast(r.title_id ? 'Автоматически опознано заново' : 'Сброшено, автоматически не опознано'); render(); } catch (e) { toast(e.message); } } }, 'сбросить ручную') : null),
      );
    };
    fill();
    return tr;
  });
  const tbl = h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th'), h('th', null, 'Ролик'), h('th', { class: 'num' }, 'Просмотры'), h('th', null, 'Произведение'), h('th', null, 'Разметка'))), h('tbody', null, rows)));
  const pager = h('div', { class: 'pager' }, h('span', null, `${offset + 1}–${Math.min(offset + limit, d.total)} из ${fmtFull(d.total)}`), h('div', { class: 'actions' }, h('button', { class: 'btn btn-sm', type: 'button', disabled: offset === 0, onclick: () => setQuery({ offset: Math.max(0, offset - limit) || null }) }, 'назад'), h('button', { class: 'btn btn-sm', type: 'button', disabled: offset + limit >= d.total, onclick: () => setQuery({ offset: offset + limit }) }, 'дальше')));
  const pnl = panel(`${fmtFull(d.total)} ${plural(d.total, 'ролик', 'ролика', 'роликов')}`, tbl, `период: ${periodLabel()}`);
  pnl.append(pager);
  box.append(pnl);
}
function setQuery(patch) { const p = new URLSearchParams(state.query); for (const [k, v] of Object.entries(patch)) { if (v == null) p.delete(k); else p.set(k, v); } go(`#/${state.route}${state.param ? '/' + state.param : ''}` + (p.toString() ? '?' + p : '')); }

// ---------- сбор ----------
async function renderCollect(main) {
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Сбор данных'), h('p', { class: 'lead' }, 'Прогон: страницы хэштегов → вкладки Shorts каналов в слежении и их RSS → страницы роликов (дата, длительность, канал, точные просмотры) → распознавание → автодобавление каналов. Ключ Data API не обязателен, но даёт лайки и комментарии к любому ролику.'))));
  const box = h('div'); main.append(box); box.append(skeleton(6));
  const [runs, sources, settings, dict, neuroSt, notifs] = await Promise.all([api('/api/runs', { limit: 20 }), api('/api/sources'), api('/api/settings'), api('/api/dict/status'), api('api/neuro/status').catch(() => null), api('api/notifications', { limit: 8 }).catch(() => ({ items: [] }))]);
  box.innerHTML = '';
  // состояние
  const cur = runs.current;
  const statusBox = h('div');
  const startBtn = h('button', { class: 'btn btn-primary', type: 'button', html: ICON.play + ' Запустить сбор', disabled: cur.running });
  const stopBtn = h('button', { class: 'btn btn-danger', type: 'button', html: ICON.stop + ' Остановить', disabled: !cur.running });
  startBtn.addEventListener('click', async () => { try { await post('/api/runs'); toast('Сбор запущен'); render(); } catch (e) { toast(e.message); } });
  stopBtn.addEventListener('click', async () => { try { await post('/api/runs/cancel'); toast('Останавливаю после текущего шага'); } catch (e) { toast(e.message); } });
  const logBox = h('div', { class: 'log' });
  const renderStatus = (c) => {
    statusBox.innerHTML = '';
    const st = c.stats || {};
    statusBox.append(h('dl', { class: 'kv' },
      h('dt', null, 'состояние'), h('dd', null, c.running ? `идёт: ${c.phase}${c.progress ? ' · ' + c.progress : ''}` : (c.started_at ? `завершён: ${c.phase}` : 'ожидание, следующий по расписанию или вручную')),
      h('dt', null, 'запущен'), h('dd', null, c.started_at ? `${fmtDateTime(c.started_at)} (${c.trigger === 'schedule' ? 'по расписанию' : c.trigger === 'manual' ? 'вручную' : c.trigger || ''})` : '—'),
      h('dt', null, 'найдено роликов'), h('dd', { class: 'num' }, `${fmtFull(st.discovered || 0)}, новых ${fmtFull(st.new_videos || 0)}`),
      h('dt', null, 'детали роликов'), h('dd', { class: 'num' }, `ок ${fmtFull(st.details_ok || 0)} · удалено ${fmtFull(st.details_removed || 0)} · блок ${fmtFull(st.details_blocked || 0)} · ошибок ${fmtFull(st.details_error || 0)}${st.details_source === 'api' ? ` · Data API, квота ${st.api_quota_used}` : ''}`),
      h('dt', null, 'распознано'), h('dd', { class: 'num' }, `${fmtFull(st.recognized_new || 0)} из ${fmtFull(st.recognized_checked || 0)} проверенных`),
      h('dt', null, 'каналов в слежение'), h('dd', { class: 'num' }, `+${fmtFull(st.auto_channels_added || 0)}`),
      h('dt', null, 'нейросеть'), h('dd', { class: 'num' }, `проверено ${fmtFull(st.neuro_checked || 0)}, опознано ${fmtFull(st.neuro_recognized || 0)}, ${(st.neuro_cost_usd || 0).toFixed(3)} $`),
      h('dt', null, 'популярные видео'), h('dd', { class: 'num' }, st.pop_seen != null ? `в чартах ${fmtFull(st.pop_seen)} · новых ${fmtFull(st.pop_new || 0)} · разбор ${fmtFull(st.pop_text || 0)} · кадров ${fmtFull(st.pop_frames || 0)} · чистых ${fmtFull(st.pop_clean || 0)} · в подборку +${fmtFull(st.pop_picked || 0)}` : '—'),
      h('dt', null, 'авторские права'), h('dd', { class: 'num' }, st.rights_titles != null ? `${fmtFull(st.rights_titles)} произведений · снесено нарезок ${fmtFull(st.rights_removed || 0)} · не проходит ${fmtFull(st.rights_block || 0)} · с оговорками ${fmtFull(st.rights_caution || 0)}` : '—'),
    ));
    logBox.innerHTML = '';
    (c.log || []).forEach(l => logBox.append(h('div', { class: l.level }, `${(l.ts || '').slice(11, 19)}  ${l.msg}`)));
    logBox.scrollTop = logBox.scrollHeight;
    startBtn.disabled = c.running; stopBtn.disabled = !c.running;
  };
  renderStatus(cur);
  if (!cur.running && runs.items[0] && !cur.log?.length) api(`/api/runs/${runs.items[0].id}/log`).then(l => { logBox.innerHTML = ''; l.items.forEach(x => logBox.append(h('div', { class: x.level }, `${x.ts.slice(11, 19)}  ${x.msg}`))); logBox.scrollTop = logBox.scrollHeight; }).catch(() => {});
  const statusPanel = panel('Прогон', h('div', null, statusBox, h('div', { class: 'actions', style: 'margin:12px 0' }, startBtn, stopBtn), logBox), null);
  // словарь
  const pct = dict.items_total ? Math.round(dict.entities_fetched / dict.items_total * 100) : 0;
  const dictPanel = panel('Словарь произведений', h('div', null,
    h('p', null, `Карточек Wikidata получено ${fmtFull(dict.entities_fetched)} из ${fmtFull(dict.items_total)} (${pct} %)`),
    h('div', { class: 'prog' }, h('i', { style: `width:${pct}%` })),
    h('dl', { class: 'kv' }, h('dt', null, 'в базе'), h('dd', { class: 'num' }, `${fmtFull(dict.titles_in_db)} произведений, ${fmtFull(dict.aliases_in_db)} написаний`), h('dt', null, 'добавлено оператором'), h('dd', { class: 'num' }, `${dict.manual_titles} произведений, ${dict.learned_aliases} написаний`), h('dt', null, 'импортировано'), h('dd', null, dict.imported_at ? ago(dict.imported_at) : 'ещё нет'), h('dt', null, 'в памяти распознавателя'), h('dd', { class: 'num' }, dict.recognizer.ready ? `${fmtFull(dict.recognizer.titles)} произведений` : 'загружается')),
    h('div', { class: 'actions', style: 'margin-top:10px' },
      h('button', { class: 'btn btn-sm', type: 'button', html: ICON.refresh + ' импортировать новые карточки', disabled: dict.jobs.dict_import.running, onclick: async () => { await post('/api/dict/import'); toast('Импорт словаря запущен, займёт около минуты'); } }),
      h('button', { class: 'btn btn-sm', type: 'button', title: 'проверить словарём все неопознанные ролики', disabled: dict.jobs.recognize.running, onclick: async () => { await post('/api/recognize'); toast('Распознавание запущено'); } }, 'распознать неопознанные'),
      h('button', { class: 'btn btn-sm', type: 'button', title: 'пересчитать все ролики, кроме размеченных вручную', disabled: dict.jobs.recognize.running, onclick: async () => { if (!confirm('Пересчитать распознавание для всех роликов? Ручная разметка сохранится.')) return; await post('/api/recognize?force=true'); toast('Полное распознавание запущено'); } }, 'пересчитать всё')),
  ), 'автообновляется раз в 10 минут, пока идёт сборка');
  // источники
  const hashtags = sources.items.filter(s => s.kind === 'hashtag');
  const channels = sources.items.filter(s => s.kind === 'channel');
  const srcItem = (s) => {
    const sw = h('input', { type: 'checkbox', class: 'switch', checked: !!s.enabled, 'aria-label': 'включить источник' });
    sw.addEventListener('change', async () => { try { await post(`/api/sources/${s.id}`, { enabled: sw.checked }, 'PATCH'); li.classList.toggle('off', !sw.checked); } catch (e) { toast(e.message); sw.checked = !sw.checked; } });
    const del = h('button', { class: 'btn btn-sm btn-danger', type: 'button', title: 'удалить источник', onclick: async () => { if (!confirm(`Удалить источник ${s.label || s.value}?`)) return; await api(`/api/sources/${s.id}`, {}, { method: 'DELETE' }); li.remove(); } }, '×');
    const li = h('li', { class: s.enabled ? '' : 'off' }, sw, s.kind === 'channel' ? h('a', { href: `#/channel/${s.value}` }, s.label || s.value) : h('span', null, s.label || '#' + s.value), s.auto ? h('span', { class: 'tag', title: 'добавлен автоматически' }, 'авто') : null,
      h('span', { class: 'meta' }, s.last_error ? h('span', { class: 'tag tag-bad', title: s.last_error }, 'ошибка') : null, s.last_run_at ? ` ${fmtFull(s.last_count)} роликов, новых ${fmtFull(s.last_new)} · ${ago(s.last_run_at)}` : ' ещё не опрашивался'), del);
    return li;
  };
  const addForm = (kind, placeholder) => {
    const inp = h('input', { type: 'text', placeholder, 'aria-label': placeholder });
    const btn = h('button', { class: 'btn btn-sm', type: 'button' }, 'добавить');
    const submit = async () => { const v = inp.value.trim(); if (!v) return; btn.disabled = true; try { await post('/api/sources', { kind, value: v }); toast('Источник добавлен'); render(); } catch (e) { toast(e.message); } finally { btn.disabled = false; } };
    btn.addEventListener('click', submit); inp.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
    return h('div', { class: 'inline-form' }, inp, btn);
  };
  const hashPanel = panel(`Хэштеги (${hashtags.filter(s => s.enabled).length} из ${hashtags.length})`, h('div', null, addForm('hashtag', 'новый хэштег, например кинонарезка'), h('ul', { class: 'src-list' }, hashtags.map(srcItem))), 'вкладка Shorts страницы хэштега');
  const chanPanel = panel(`Каналы в слежении (${channels.filter(s => s.enabled).length})`, h('div', null, addForm('channel', 'UC… или @handle канала'), channels.length ? h('ul', { class: 'src-list' }, channels.map(srcItem)) : empty('Пока нет', 'Каналы добавятся автоматически после распознавания, или добавь вручную.')), 'вкладка Shorts и RSS канала');
  // настройки
  const s = settings.settings;
  const fields = [
    ['schedule_hours', 'расписание, часов между прогонами', '0 — только вручную'],
    ['hashtag_depth', 'роликов со страницы хэштега', 'за один прогон, обычно 100–400'],
    ['channel_depth', 'роликов со вкладки Shorts канала', ''],
    ['detail_budget', 'страниц роликов за прогон', 'около 3 с на ролик при 4 потоках'],
    ['detail_concurrency', 'параллельных запросов к YouTube', '1–8; больше — риск блокировки'],
    ['refresh_days', 'переопрашивать ролики моложе, дней', 'для прироста просмотров'],
    ['auto_channels_max', 'максимум каналов в автослежении', ''],
    ['auto_channel_min_shorts', 'опознанных роликов, чтобы взять канал', ''],
    ['dict_min_sitelinks', 'порог известности для словаря в памяти', 'статей в Википедии; ниже — только ручной поиск'],
  ];
  const inputs = {};
  const form = h('div', { class: 'form' }, fields.map(([k, l, hint]) => { inputs[k] = h('input', { type: 'number', min: 0, value: s[k] }); return h('label', null, l, inputs[k], hint ? h('small', null, hint) : null); }));
  inputs.youtube_api_key = h('input', { type: 'password', value: s.youtube_api_key || '', placeholder: 'AIza…', autocomplete: 'off' });
  form.append(h('label', null, 'ключ YouTube Data API v3 (необязательно)', inputs.youtube_api_key, h('small', null, 'Google Cloud → API и сервисы → учётные данные; квота 10 000 единиц в сутки, один прогон тратит около 60')));
  const saveBtn = h('button', { class: 'btn btn-primary', type: 'button' }, 'Сохранить настройки');
  saveBtn.addEventListener('click', async () => { const payload = {}; for (const [k, el] of Object.entries(inputs)) payload[k] = el.value; try { await post('/api/settings', payload, 'PUT'); toast('Настройки сохранены, применятся со следующего прогона'); } catch (e) { toast(e.message); } });
  const settingsPanel = panel('Настройки сбора', h('div', null, form, h('div', { class: 'actions', style: 'margin-top:12px' }, saveBtn)));
  // история
  const histTbl = h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'Начало'), h('th', null, 'Статус'), h('th', { class: 'num' }, 'Мин'), h('th', { class: 'num' }, 'Найдено'), h('th', { class: 'num' }, 'Новых'), h('th', { class: 'num' }, 'Деталей'), h('th', { class: 'num' }, 'Опознано'), h('th', { class: 'num' }, 'Блок'))),
    h('tbody', null, runs.items.map(r => h('tr', null, h('td', { class: 'rank' }, r.id), h('td', null, fmtDateTime(r.started_at), h('span', { class: 'sub' }, r.trigger)), h('td', null, h('span', { class: `tag ${r.status === 'ok' ? 'tag-good' : r.status === 'running' ? 'tag-accent' : r.status === 'cancelled' ? 'tag-warn' : 'tag-bad'}`, title: r.error || '' }, { ok: 'успешно', running: 'идёт', error: 'ошибка', cancelled: 'остановлен' }[r.status] || r.status)), h('td', { class: 'num' }, r.stats.duration_s != null ? Math.round(r.stats.duration_s / 60) : '—'), h('td', { class: 'num' }, fmtFull(r.stats.discovered)), h('td', { class: 'num' }, fmtFull(r.stats.new_videos)), h('td', { class: 'num' }, fmtFull(r.stats.details_ok)), h('td', { class: 'num' }, fmtFull(r.stats.recognized_new)), h('td', { class: 'num' }, r.stats.details_blocked || '—'))))));
  // ---- нейросеть
  const sf = (key, label, type = 'text', hint = '') => { const inp = h('input', { type, value: s[key] || '', autocomplete: 'off', placeholder: type === 'password' ? '••••' : '' }); inputs[key] = inp; return h('label', null, label, inp, hint ? h('small', null, hint) : null); };
  const sel = (key, label, opts, hint = '') => { const el = h('select', null, opts.map(([v, l]) => h('option', { value: v, selected: String(s[key]) === v }, l))); inputs[key] = el; return h('label', null, label, el, hint ? h('small', null, hint) : null); };
  const saveKeys = async (keys, msg) => { const payload = {}; keys.forEach(k => { payload[k] = inputs[k].value; }); try { await post('/api/settings', payload, 'PUT'); toast(msg || 'Сохранено'); } catch (e) { toast(e.message); } };
  const u = neuroSt ? neuroSt.usage_today : null;
  const neuroPanel = panel('Нейросеть: опознание по кадру и разбор сцен', h('div', null,
    neuroSt ? h('div', { class: 'status-line' },
      h('span', null, 'модель: ', h('b', null, neuroSt.model || 'не задана')),
      h('span', null, 'очередь: ', h('b', null, fmtFull(neuroSt.pending))),
      h('span', null, 'проверено: ', h('b', null, fmtFull(neuroSt.done))),
      h('span', null, 'опознано по кадру: ', h('b', null, fmtFull(neuroSt.recognized))),
      h('span', null, 'новых произведений: ', h('b', null, fmtFull(neuroSt.created_titles))),
      h('span', null, 'сегодня: ', h('b', null, `${u.calls} вызовов, ${u.cost_usd.toFixed(3)} $ из ${neuroSt.daily_budget_usd} $`)),
      neuroSt.job.running ? h('span', { class: 'tag tag-accent' }, 'идёт опознание') : null) : h('p', { class: 'muted' }, 'статус недоступен'),
    h('div', { class: 'form' },
      sf('llm_base_url', 'адрес API (OpenAI-совместимый)', 'text', 'OpenRouter: https://openrouter.ai/api/v1'),
      sf('llm_api_key', 'ключ API', 'password', 'хранится только в базе сервиса'),
      sf('llm_model', 'модель со зрением', 'text', 'по бейк-оффу лучшая по цене и точности: google/gemini-2.5-flash-lite'),
      sel('neuro_enabled', 'опознавать по кадру в каждом прогоне', [['1', 'да'], ['0', 'нет']]),
      sf('neuro_per_run', 'роликов за прогон', 'number'),
      sf('neuro_min_views', 'только ролики с просмотрами от', 'number', 'экономим на мелочи'),
      sf('neuro_daily_usd', 'дневной потолок, $', 'number', 'при 0,26 $ за 1000 роликов хватает с запасом')),
    h('div', { class: 'actions', style: 'margin-top:10px' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => saveKeys(['llm_base_url', 'llm_api_key', 'llm_model', 'neuro_enabled', 'neuro_per_run', 'neuro_min_views', 'neuro_daily_usd'], 'Настройки нейросети сохранены') }, 'Сохранить'),
      h('button', { class: 'btn', type: 'button', onclick: async e => { e.target.disabled = true; try { const r = await post('api/neuro/test'); toast(`Модель отвечает: ${JSON.stringify(r.answer).slice(0, 60)}, ${r.tokens} токенов`); } catch (x) { toast(x.message); } e.target.disabled = false; } }, 'проверить модель'),
      h('button', { class: 'btn', type: 'button', disabled: !!(neuroSt && neuroSt.job.running), onclick: async () => { try { await post('api/neuro/run'); toast('Нейро-опознание запущено, смотри счётчики через минуту'); } catch (x) { toast(x.message); } } }, 'опознать очередь сейчас'))),
    'кадр + заголовок + описание + канал → модель → словарь');
  // ---- telegram и календарь
  const notifList = notifs.items.length ? h('ul', { class: 'src-list' }, notifs.items.map(n => h('li', null, h('span', { class: `tag ${n.ok ? 'tag-good' : 'tag-bad'}` }, n.kind === 'digest' ? 'дайджест' : 'тревога'), h('span', { title: n.text }, truncate(n.text.replace(/<[^>]+>/g, ''), 90)), h('span', { class: 'meta' }, ago(n.ts))))) : h('p', { class: 'muted' }, 'сообщений ещё не было');
  const tgPanel = panel('Telegram: дайджест и тревоги', h('div', null,
    h('div', { class: 'form' },
      sf('telegram_bot_token', 'токен бота', 'password', '@BotFather → токен; бот должен быть в чате'),
      sf('telegram_chat_id', 'chat_id', 'text', 'личный чат или группа, можно отрицательный'),
      sel('digest_enabled', 'ежедневный дайджест «что резать»', [['1', 'да'], ['0', 'нет']]),
      sf('digest_hour', 'час дайджеста по Москве', 'number'),
      sel('alerts_enabled', 'тревоги, когда произведение взлетает', [['1', 'да'], ['0', 'нет']], 'после каждого прогона сбора'),
      sf('public_url', 'адрес консоли для ссылок', 'text')),
    h('div', { class: 'actions', style: 'margin-top:10px' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => saveKeys(['telegram_bot_token', 'telegram_chat_id', 'digest_enabled', 'digest_hour', 'alerts_enabled', 'public_url'], 'Настройки Telegram сохранены') }, 'Сохранить'),
      h('button', { class: 'btn', type: 'button', onclick: async () => { try { const r = await post('api/telegram/test'); toast(`Связь есть: бот @${r.bot}`); } catch (x) { toast(x.message); } } }, 'проверить связь'),
      h('button', { class: 'btn', type: 'button', onclick: async () => { try { await post('api/telegram/digest'); toast('Дайджест отправлен'); render(); } catch (x) { toast(x.message); } } }, 'отправить дайджест сейчас')),
    h('h3', { style: 'margin:14px 0 6px' }, 'Последние сообщения'), notifList), 'что уходит оператору');
  const storagePanel = panel('Хранилище видео', h('div', null,
    h('div', { class: 'form' },
      sf('storage_host', 'сервер-хранилище (IP или хост по SSH)', 'text', 'пусто = качать на этот сервер; YouTube тогда недоступен'),
      sf('storage_user', 'пользователь SSH', 'text'),
      sf('storage_key', 'путь к ключу SSH', 'text'),
      sf('storage_dir', 'каталог файлов на хранилище', 'text'),
      sf('storage_url', 'публичный адрес каталога', 'text', 'например http://1.2.3.4/files'),
      sf('storage_max_gb', 'потолок, ГБ', 'number'),
      sf('storage_ttl_hours', 'хранить файлы, часов', 'number')),
    h('div', { class: 'actions', style: 'margin-top:10px' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => saveKeys(['storage_host', 'storage_user', 'storage_key', 'storage_dir', 'storage_url', 'storage_max_gb', 'storage_ttl_hours'], 'Настройки хранилища сохранены') }, 'Сохранить'))), 'отдельный сервер качает исходники (YouTube и Rutube) и раздаёт mp4');
  const calPanel = panel('Календарь нарезки', h('div', null,
    h('div', { class: 'form' },
      sf('schedule_days', 'дней вперёд', 'number'),
      sf('schedule_per_day', 'произведений в день', 'number'),
      sf('schedule_cooldown_days', 'не повторять раньше, дней', 'number'),
      sf('auto_hashtags_max', 'автодобор хэштегов, максимум', 'number', 'хэштеги, под которыми много опознанных киношортсов'),
      sel('rights_policy', 'проверка авторских прав', [['balanced', 'проходит и с оговорками'], ['strict', 'только проходит'], ['off', 'только пометка, не отсеивать']], 'что пускать в календарь; «не проходит» при strict и balanced отсеивается'),
      sf('rights_llm_max', 'правообладателей спрашивать у модели за прогон', 'number'),
      sf('rights_recheck_max', 'чужих нарезок перепроверять на сносы за прогон', 'number', 'страницы роликов из календаря и топа; сносы по жалобам идут в риск')),
    h('div', { class: 'actions', style: 'margin-top:10px' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => saveKeys(['schedule_days', 'schedule_per_day', 'schedule_cooldown_days', 'auto_hashtags_max', 'rights_policy', 'rights_llm_max', 'rights_recheck_max'], 'Сохранено') }, 'Сохранить'),
      h('a', { class: 'btn', href: '#/cut' }, 'открыть календарь'))), 'строится после каждого прогона');
  box.append(h('div', { class: 'grid grid-2' }, statusPanel, dictPanel));
  box.append(h('div', { class: 'grid grid-2', style: 'margin-top:18px' }, neuroPanel, h('div', { class: 'grid', style: 'align-content:start' }, tgPanel, calPanel)));
  const popTa = h('textarea', { rows: 3 }, s.pop_audience || ''); inputs.pop_audience = popTa;
  const popPanel = panel('Видео под перезалив', h('div', null,
    h('div', { class: 'form' },
      sel('pop_enabled', 'собирать популярные видео в каждом прогоне', [['1', 'да'], ['0', 'нет']], 'нужен ключ YouTube Data API выше'),
      sf('pop_regions', 'страны чарта', 'text', 'коды через запятую: RU,KZ,BY,AZ,AM,MD,GE,UA'),
      sf('pop_pages', 'страниц по 50 роликов на страну', 'number', '1 единица квоты за страницу'),
      sf('pop_categories', 'чарты России по категориям', 'text', 'id категорий YouTube через запятую; 20 игры, 28 наука и техника, 24 развлечения'),
      sf('pop_cc_queries', 'поиск Creative Commons: запросы', 'text', '100 единиц квоты за запрос, раз в pop_cc_hours'),
      sf('pop_cc_hours', 'искать Creative Commons раз в, часов', 'number'),
      sel('pop_lang', 'язык роликов', [['ru', 'русский'], ['all', 'все']]),
      sf('pop_min_views', 'анализировать от просмотров', 'number'),
      sf('pop_max_duration', 'не длиннее, секунд', 'number'),
      sf('pop_llm_max', 'разборов текста за прогон', 'number'),
      sf('pop_frames_max', 'проверок кадров за прогон', 'number', '15–30 с на ролик на хранилище'),
      sel('pop_auto_pick', 'чистые сами в подборку', [['1', 'да'], ['0', 'нет']]),
      sf('pop_pick_min_score', 'порог балла для подборки', 'number'),
      h('label', { style: 'grid-column:1/-1' }, 'наша аудитория (для оценки совпадения)', popTa, h('small', null, 'кому продаём: модель сравнивает зрителей ролика с этим описанием'))),
    h('div', { class: 'actions', style: 'margin-top:10px' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => saveKeys(['pop_enabled', 'pop_regions', 'pop_pages', 'pop_categories', 'pop_cc_queries', 'pop_cc_hours', 'pop_lang', 'pop_min_views', 'pop_max_duration', 'pop_llm_max', 'pop_frames_max', 'pop_auto_pick', 'pop_pick_min_score', 'pop_audience'], 'Настройки видео сохранены') }, 'Сохранить'),
      h('a', { class: 'btn', href: '#/videos' }, 'открыть видео'))), 'чарты YouTube по странам, Creative Commons, разбор моделью, кадры с хранилища');
  box.append(h('div', { style: 'margin-top:18px' }, storagePanel));
  box.append(h('div', { style: 'margin-top:18px' }, popPanel));
  box.append(h('div', { class: 'grid grid-halves', style: 'margin-top:18px' }, hashPanel, chanPanel));
  box.append(h('div', { class: 'grid grid-2', style: 'margin-top:18px' }, settingsPanel, panel('История прогонов', runs.items.length ? histTbl : empty('Прогонов ещё не было'))));
  // живое обновление, пока идёт прогон
  const tick = async () => { if (state.route !== 'collect') return; try { const r = await api('/api/runs', { limit: 1 }); renderStatus(r.current); if (r.current.running) setTimeout(tick, 3000); else if (cur.running) render(); } catch (_) {} };
  if (cur.running) setTimeout(tick, 3000);
}


// ---------- что резать: рекомендации и календарь ----------
const poster = (tid, cls = 'poster') => tid && tid.startsWith('Q') ? h('img', { class: cls, src: abs(`api/films/${tid}/poster.jpg`), alt: '', loading: 'lazy', onerror: e => { e.target.remove(); } }) : null;
const absFile = u => /^https?:/.test(u) ? u : location.origin + abs(u);
const fileButton = (src, titleId) => {
  if (!src || !src.video_id) return null;
  const holder = h('span');
  const draw = (st) => {
    holder.innerHTML = '';
    if (st && st.status === 'done' && st.file_url) holder.append(h('a', { class: 'btn btn-sm', href: absFile(st.file_url), target: '_blank', rel: 'noopener', title: 'прямая ссылка на mp4 для нарезчика' }, `mp4 (${(st.size / 1e6).toFixed(0)} МБ)`));
    else if (st && st.status === 'running') { holder.append(h('span', { class: 'tag tag-accent' }, 'качаю в хранилище…')); setTimeout(() => api(`api/downloads/${src.video_id}`).then(draw).catch(() => {}), 5000); }
    else holder.append(h('button', { class: 'btn btn-sm', type: 'button', title: 'хранилище скачает исходник в 720p и даст прямую ссылку на файл', onclick: async () => { try { draw(await post('api/downloads', { video_id: src.video_id, url: src.url, title_id: titleId })); } catch (e) { toast(e.message); } } }, st && st.status === 'error' ? 'файл: повторить' : 'подготовить файл'));
  };
  api(`api/downloads/${src.video_id}`).then(draw).catch(() => draw(null));
  return holder;
};
const cutterLink = (it) => {
  const holder = h('div', { class: 'cutter-link' });
  const draw = (fileUrl) => {
    holder.innerHTML = '';
    const src = it.source, alt = it.source_alt;
    const rt = src || alt || null;
    const url = fileUrl ? absFile(fileUrl) : src ? src.url : null;
    if (!url) return;
    const kind = fileUrl ? 'прямой mp4 из нашего хранилища' : /rutube\.ru/.test(url) ? 'страница Rutube (файл ещё не готов)' : 'ссылка YouTube (файл ещё не готов)';
    holder.append(h('b', null, 'Ссылка для нарезчика'), h('span', { class: 'muted' }, ` · ${kind}`),
      h('div', { class: 'cutter-row' }, h('input', { type: 'text', readonly: true, value: url, onclick: e => e.target.select() }),
        h('button', { class: 'btn btn-sm btn-primary', type: 'button', html: ICON.copy + ' скопировать', onclick: async () => { try { await navigator.clipboard.writeText(url); toast('Ссылка скопирована'); } catch (_) { toast('Выделите и скопируйте вручную'); } } }),
        rt && !fileUrl ? h('button', { class: 'btn btn-sm', type: 'button', title: 'сервер скачает исходник с Rutube в 720p и даст прямую ссылку', onclick: async e => { e.target.disabled = true; e.target.textContent = 'качаю…'; try { await post('api/downloads', { video_id: rt.video_id, url: rt.url, title_id: it.title_id }); const poll = async () => { const st = await api(`api/downloads/${rt.video_id}`); if (st.status === 'done' && st.file_url) draw(st.file_url); else if (st.status === 'running') setTimeout(poll, 5000); else { toast('Файл не получился: ' + (st.error || '')); draw(null); } }; poll(); } catch (x) { toast(x.message); draw(null); } } }, 'подготовить mp4') : null));
  };
  const src = it.source, alt = it.source_alt;
  const rt = src || alt || null;
  if (rt) api(`api/downloads/${rt.video_id}`).then(st => draw(st.status === 'done' ? st.file_url : null)).catch(() => draw(null));
  else draw(null);
  return holder;
};
const copyHint = (titleId) => h('button', { class: 'btn btn-sm', type: 'button', title: 'скопировать текст для поля «Что искать» нарезчика: сцены, реплики, подача', onclick: async e => { try { const b = await api(`api/brief/${titleId}`); const t = b.cutter && b.cutter.hint ? b.cutter.hint : ''; if (!t) { toast('Сначала разберите сцены нейросетью на странице произведения'); return; } await navigator.clipboard.writeText(t); toast('Подсказка для нарезчика скопирована'); } catch (x) { toast('Не скопировалось: ' + x.message); } } }, 'подсказка для нарезчика');
const sourceLine = (src, titleId, onFound) => {
  if (src) {
    const kindRu = src.kind === 'episode' ? `серия${src.episode ? ' ' + src.episode : ''}${src.season ? ', сезон ' + src.season : ''}` : src.kind === 'pack' ? 'все серии' : 'полный фильм';
    const prov = /rutube\.ru/.test(src.url) ? 'Rutube' : 'YouTube';
    return h('div', { class: 'src-line' }, h('b', null, 'Исходник: '), extLink(src.url, `${prov} · ${kindRu} · ${src.channel || ''} · ${src.duration_s ? Math.round(src.duration_s / 60) + ' мин' : ''}`), ' ', src.verified ? h('span', { class: 'tag tag-good', title: src.note || '' }, 'проверено нейросетью' + (src.language ? ', ' + src.language : '')) : h('span', { class: 'tag tag-warn', title: src.note || '' }, 'не проверено'), ' ', fileButton(src, titleId));
  }
  const btn = h('button', { class: 'btn btn-sm', type: 'button' }, 'найти исходник');
  btn.addEventListener('click', async () => { btn.disabled = true; btn.textContent = 'ищу на YouTube…'; try { const r = await post(`api/films/${titleId}/sources`); if (onFound) onFound(r.items); toast(r.items.length ? `Найдено ${r.items.length}, проверено ${r.items.filter(x => x.verified).length}` : 'Полного фильма на YouTube не нашлось'); } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = 'найти исходник'; } });
  return h('div', { class: 'src-line' }, h('b', null, 'Исходник: '), h('span', { class: 'muted' }, 'ещё не искали '), btn);
};
const explainBlock = (text, titleId, onDone) => {
  if (text) return h('div', { class: 'explain', title: 'нажми, чтобы развернуть', onclick: e => e.currentTarget.classList.toggle('open') }, text.split('\n').map(line => h('p', null, line)));
  const btn = h('button', { class: 'btn btn-sm', type: 'button' }, 'объяснить, почему');
  btn.addEventListener('click', async () => { btn.disabled = true; btn.textContent = 'думает…'; try { const r = await post(`api/films/${titleId}/explain`); if (onDone) onDone(r.explain.text); } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = 'объяснить, почему'; } });
  return h('div', { class: 'explain muted' }, btn);
};
const planLine = p => [p.duration ? `длительность ${p.duration}` : null, p.hook ? `хук: ${p.hook}` : null, p.best_time ? `время: ${p.best_time}` : null, p.hashtags && p.hashtags.length ? p.hashtags.slice(0, 4).map(t => '#' + t).join(' ') : null].filter(Boolean).join(' · ');

// ---------- популярные видео СНГ под перезалив ----------
const POP_RU = { clean: 'чистое', check: 'проверить', no: 'не брать', unknown: 'не проверено' };
const POP_CLS = { clean: 'tag-good', check: 'tag-warn', no: 'tag-bad', unknown: '' };
const POP_REGIONS = { RU: 'Россия', KZ: 'Казахстан', BY: 'Беларусь', AZ: 'Азербайджан', AM: 'Армения', MD: 'Молдова', GE: 'Грузия', UA: 'Украина' };
const POP_CATS = { '1': 'кино и анимация', '2': 'авто', '10': 'музыка', '15': 'животные', '17': 'спорт', '19': 'путешествия', '20': 'игры', '22': 'люди и блоги', '23': 'юмор', '24': 'развлечения', '25': 'новости', '26': 'как сделать и стиль', '27': 'образование', '28': 'наука и техника', '29': 'общество' };
const popTag = (v, short = false) => { const lvl = v.level || 'unknown'; return h('span', { class: 'tag ' + POP_CLS[lvl], title: (v.reasons || []).join('\n') || 'оценка чистоты для перезалива' }, POP_RU[lvl] + (v.score != null && !short ? ` · ${v.score}` : '')); };
const licTag = (v) => v.license === 'creativeCommon' ? h('span', { class: 'tag tag-good', title: 'Creative Commons: перезалив разрешён с указанием автора' }, 'CC') : v.licensed_content ? h('span', { class: 'tag tag-bad', title: 'контент заявлен партнёром YouTube: перезалив получит заявку Content ID или блок' }, 'заявлен') : h('span', { class: 'tag', title: 'обычная лицензия YouTube, заявок партнёра нет' }, 'без заявок');
const fitCell = (v) => { const a = v.analysis; if (!a || a.audience_fit == null) return h('span', { class: 'muted' }, '—'); const p = Math.round(a.audience_fit * 100); return h('span', { class: 'fit', title: a.audience_why || '' }, h('span', { class: 'bar' }, h('i', { class: p >= 60 ? 'good' : p >= 35 ? 'mid' : 'low', style: `width:${p}%` })), h('span', { class: 'num' }, p + ' %')); };
const regionChips = (v) => { const r = Object.entries(v.regions || {}); const c = Object.entries(v.cats || {}); if (!r.length && !c.length) return h('span', { class: 'muted' }, v.origin === 'cc' ? 'поиск CC' : '—'); return h('div', { class: 'chips chips-sm' }, r.map(([k, rank]) => h('span', { class: 'tag', title: (POP_REGIONS[k] || k) + (rank ? `, место ${rank}` : '') }, k + (rank ? ' ' + rank : ''))), c.slice(0, 2).map(([k, rank]) => h('span', { class: 'tag', title: 'чарт России по категории' }, (POP_CATS[k] || k) + ' ' + rank))); };
const pickButton = (v, onChange) => { const b = h('button', { class: 'btn btn-sm' + (v.pick ? ' btn-primary' : ''), type: 'button', title: v.pick ? 'убрать из подборки' : 'взять в подборку для перезалива' }, v.pick ? '✓ в подборке' : 'в подборку'); b.addEventListener('click', async () => { try { const d = await post(`api/videos/${v.video_id}/pick`, { pick: !v.pick }); Object.assign(v, { pick: d.pick, pick_auto: d.pick_auto }); toast(v.pick ? 'Взято в подборку' : 'Убрано из подборки'); onChange(); } catch (e) { toast(e.message); } }); return b; };
const popFileButton = (v, onChange) => {
  const f = v.file;
  if (f && f.file_url) return h('button', { class: 'btn btn-sm', type: 'button', title: 'прямая ссылка на mp4 из хранилища: скопировать', onclick: async () => { try { await navigator.clipboard.writeText(f.file_url); toast('Ссылка на mp4 скопирована'); } catch (e) { toast(f.file_url); } } }, 'mp4 · скопировать');
  if (f && f.status === 'running') return h('button', { class: 'btn btn-sm', type: 'button', disabled: true }, 'качается…');
  return h('button', { class: 'btn btn-sm', type: 'button', title: 'скачать 720p на хранилище и получить прямую ссылку', onclick: async e => { e.target.disabled = true; try { await post(`api/videos/${v.video_id}/file`); v.file = { status: 'running' }; toast('Поставлено на скачивание, ссылка появится через пару минут'); onChange(); } catch (x) { toast(x.message); e.target.disabled = false; } } }, f && f.status === 'error' ? 'mp4: повторить' : 'подготовить mp4');
};
const flagChips = (f) => { const items = [['watermark', 'водяной знак' + (f.watermark_where ? ' (' + f.watermark_where + ')' : '')], ['banner', 'чужая реклама или плашка'], ['subscribe_cta', 'призыв подписаться'], ['text_overlay', 'надписи'], ['subtitles', 'субтитры'], ['borders', 'рамки']].filter(([k]) => f[k]).map(([, l]) => h('span', { class: 'tag tag-bad' }, l)); if (!items.length) items.push(h('span', { class: 'tag tag-good' }, f.clean ? 'чисто: чужого брендинга нет' : 'явных помех нет')); if (f.orientation) items.push(h('span', { class: 'tag' }, { vertical: 'вертикальное', horizontal: 'горизонтальное', square: 'квадратное' }[f.orientation] || f.orientation)); if (f.quality === 'низкое') items.push(h('span', { class: 'tag tag-warn' }, 'низкое качество')); return items; };
const cutterBox = (url, label) => h('div', { class: 'src-line' }, h('b', null, 'ссылка для заливщика'), h('span', { class: 'muted' }, label), h('code', { class: 'url' }, url), h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: async () => { try { await navigator.clipboard.writeText(url); toast('Скопировано'); } catch (e) { toast(url); } } }, 'скопировать'));

async function renderVideos(main) {
  const q = state.query; const level = q.level || 'all';
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Видео под перезалив'), h('p', { class: 'lead' }, 'Чарты YouTube по странам СНГ и ролики с лицензией Creative Commons. Каждый проверяется: лицензия и заявки Content ID, канал правообладателя, нейросеть по тексту (тема, хук, совпадение с нашей аудиторией VPN) и по кадрам (водяные знаки, плашки, призывы подписаться, свободные зоны под баннер). Чистые с высоким баллом сами попадают в подборку.')),
    h('div', { class: 'actions' },
      h('a', { class: 'btn', href: abs('api/videos/picks'), target: '_blank', rel: 'noopener', html: ICON.doc + ' подборка JSON' }),
      h('button', { class: 'btn', type: 'button', title: 'скачать mp4 на хранилище для всех роликов подборки без файла', html: ICON.dl + ' mp4 для подборки', onclick: async () => { try { const r = await post('api/videos/picks/prepare-files'); toast(r.queued.length ? `В очереди: ${r.queued.filter(x => x.ok).length}` : 'Файлы подборки уже готовы'); } catch (e) { toast(e.message); } } }),
      h('button', { class: 'btn', type: 'button', html: ICON.refresh + ' обновить чарты и анализ', title: 'собрать чарты, замеры, разбор текста и кадров прямо сейчас (в фоне, 5–15 минут)', onclick: async () => { try { await post('api/videos/refresh', {}); toast('Запущено в фоне: чарты, замеры, разбор текста и кадров'); } catch (e) { toast(e.message); } } }))));
  const chips = h('div', { class: 'chips' }, [['all', 'все'], ['pick', 'подборка'], ['fit', 'под нашу аудиторию'], ['clean', 'чистые'], ['check', 'проверить'], ['no', 'не брать']].map(([v, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(level === v), onclick: () => setQuery({ level: v === 'all' ? null : v, offset: null }) }, l)));
  const sel = (name, opts, cur, label) => { const el = h('select', { 'aria-label': label }, opts.map(([v, l]) => h('option', { value: v, selected: (cur || '') === v }, l))); el.addEventListener('change', () => setQuery({ [name]: el.value || null, offset: null })); return el; };
  const regionSel = sel('region', [['', 'все страны'], ...Object.entries(POP_REGIONS)], q.region, 'Страна');
  const catSel = sel('category', [['', 'все категории'], ...Object.entries(POP_CATS)], q.category, 'Категория');
  const kindSel = sel('short', [['', 'шортсы и длинные'], ['1', 'только шортсы'], ['0', 'только длинные']], q.short, 'Шортсы или длинные');
  const durSel = sel('dur', [['', 'любая длина'], ['60', 'до 60 с'], ['180', 'до 3 мин'], ['600', 'до 10 мин'], ['1800', 'до 30 мин']], q.dur, 'Длительность');
  const langSel = sel('lang', [['ru', 'русский'], ['all', 'все языки']], q.lang || 'ru', 'Язык');
  const sortSel = sel('sort', [['score', 'по баллу'], ['audience', 'по совпадению с аудиторией'], ['growth', 'по приросту в день'], ['views', 'по просмотрам'], ['published', 'по дате'], ['seen', 'по появлению']], q.sort || 'score', 'Сортировка');
  const search = h('input', { type: 'search', placeholder: 'поиск по названию, каналу, описанию', value: q.q || '', 'aria-label': 'Поиск по видео' });
  search.addEventListener('input', debounce(() => setQuery({ q: search.value || null, offset: null }), 350));
  main.append(h('div', { class: 'toolbar' }, chips, h('div', { class: 'grow' }, search), kindSel, regionSel, catSel, durSel, langSel, sortSel));
  const box = h('div'); main.append(box); box.append(skeleton(10));
  const limit = 50, offset = +(q.offset || 0);
  const params = { region: q.region, category: q.category, dur: q.dur, lang: q.lang || 'ru', q: q.q, sort: q.sort || 'score', short: q.short, limit, offset };
  if (level === 'pick') params.pick = 1; else if (level === 'fit') params.fit = 0.6; else if (level !== 'all') params.level = level;
  const d = await api('api/videos', params);
  box.innerHTML = '';
  const lv = d.levels || {};
  box.append(h('div', { class: 'status-line' }, h('span', null, 'всего: ', h('b', null, fmtFull(d.total))), h('span', null, 'чистых: ', h('b', null, fmtFull(lv.clean || 0))), h('span', null, 'проверить: ', h('b', null, fmtFull(lv.check || 0))), h('span', null, 'не брать: ', h('b', null, fmtFull(lv.no || 0))), h('span', null, 'не проверено: ', h('b', null, fmtFull(lv.unknown || 0))), h('span', null, 'в подборке: ', h('b', null, fmtFull(d.picks || 0)), helpIcon('picks')), h('span', null, 'шортсов: ', h('b', null, fmtFull(d.shorts || 0)))));
  if (!d.items.length) { box.append(panel('Пусто', empty('Роликов с такими фильтрами нет', 'Нажми «обновить чарты и анализ» или дождись прогона сбора. Нужен ключ YouTube Data API в разделе «Сбор».'))); return; }
  const rows = d.items.map(v => {
    const tr = h('tr');
    const fill = () => {
      const a = v.analysis || {};
      tr.innerHTML = '';
      tr.append(
        h('td', null, h('a', { href: `#/video/${v.video_id}` }, h('img', { class: 'thumb thumb-wide', src: abs(`thumb/${v.video_id}.jpg`), alt: '', loading: 'lazy', onerror: e => { e.target.style.visibility = 'hidden'; } }))),
        h('td', { class: 'row-title' }, h('div', null, h('a', { href: `#/video/${v.video_id}`, class: 'title-link' }, h('span', { class: 't', title: v.title || v.video_id }, v.title || v.video_id)), h('span', { class: 'sub' }, [v.channel_name, v.channel_subs ? fmtN(v.channel_subs) + ' подп.' : null, fmtDate(v.published_at), fmtDur(v.duration_s) + (v.is_short === 1 ? ' · шортс' : v.is_short === 0 ? ' · длинное' : ''), v.category, v.lang].filter(Boolean).join(' · ')), a.topic ? h('span', { class: 'sub' }, [a.topic, a.format].filter(Boolean).join(' · ')) : null)),
        h('td', null, regionChips(v)),
        h('td', { class: 'num' }, h('span', { title: fmtFull(v.views) }, fmtN(v.views)), h('span', { class: 'sub' }, v.growth != null ? '+' + fmtN(v.growth) + '/дн' : v.vpd != null ? fmtN(v.vpd) + '/дн' : '')),
        h('td', null, fitCell(v)),
        h('td', null, popTag(v), ' ', licTag(v), v.made_for_kids ? h('span', { class: 'tag tag-warn' }, 'детское') : null),
        h('td', null, h('div', { class: 'ibar', style: 'flex-wrap:wrap;gap:6px' }, pickButton(v, fill), popFileButton(v, fill), iconLink('ext', 'открыть на YouTube', v.url))));
    };
    fill();
    return tr;
  });
  const tbl = h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th'), h('th', null, 'Ролик'), h('th', null, 'Чарты'), h('th', { class: 'num' }, 'Просмотры'), h('th', null, 'Наша аудитория', helpIcon('videos')), h('th', null, 'Чистота', helpIcon('clean')), h('th', null, ''))), h('tbody', null, rows)));
  const pager = h('div', { class: 'pager' }, h('span', null, `${offset + 1}–${Math.min(offset + limit, d.total)} из ${fmtFull(d.total)}`), h('div', { class: 'actions' }, h('button', { class: 'btn btn-sm', type: 'button', disabled: offset === 0, onclick: () => setQuery({ offset: Math.max(0, offset - limit) || null }) }, 'назад'), h('button', { class: 'btn btn-sm', type: 'button', disabled: offset + limit >= d.total, onclick: () => setQuery({ offset: offset + limit }) }, 'дальше')));
  const pnl = panel(`${fmtFull(d.total)} ${plural(d.total, 'ролик', 'ролика', 'роликов')}`, tbl, 'чарты YouTube по странам СНГ и поиск Creative Commons');
  pnl.append(pager);
  box.append(pnl);
}

async function renderVideo(main, id) {
  main.append(h('a', { href: '#/videos', class: 'back', html: ICON.back + ' к видео' }));
  const box = h('div'); main.append(box); box.append(skeleton(8));
  let v;
  try { v = await api(`api/videos/${encodeURIComponent(id)}`); } catch (e) { box.innerHTML = ''; box.append(panel('Нет такого ролика', empty(e.message, 'Ролик не из чартов или удалён.'))); return; }
  const draw = () => {
    box.innerHTML = '';
    const a = v.analysis || {}, f = v.frames || {}, cu = v.cutter || {};
    const manual = h('select', { title: 'решение оператора сильнее автоматической оценки' }, [['', 'авто'], ['clean', 'чистое'], ['check', 'проверить'], ['no', 'не брать']].map(([val, l]) => h('option', { value: val, selected: (v.manual_level || '') === val }, l)));
    manual.addEventListener('change', async () => { try { v = await post(`api/videos/${id}/level`, { level: manual.value || null }); v.cutter = cu; toast('Решение сохранено'); draw(); } catch (e) { toast(e.message); } });
    box.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, v.title), h('p', { class: 'lead' }, [v.channel_name + (v.channel_subs ? ` · ${fmtN(v.channel_subs)} подписчиков` : ''), fmtDate(v.published_at), fmtDur(v.duration_s), v.category, v.lang, v.holder ? 'канал правообладателя: ' + v.holder : null].filter(Boolean).join(' · '))),
      h('div', { class: 'actions' }, extLink(v.url, 'YouTube'), pickButton(v, draw),
        h('button', { class: 'btn btn-sm', type: 'button', title: 'разобрать заново: текст, 6 кадров с хранилища, оценка (до минуты)', onclick: async e => { e.target.disabled = true; e.target.textContent = 'разбираю…'; try { v = await post(`api/videos/${id}/analyze`); toast((v.notes || []).join(' · ') || 'Разобрано'); draw(); } catch (x) { toast(x.message); e.target.disabled = false; e.target.textContent = 'разобрать заново'; } } }, 'разобрать заново'),
        popFileButton(v, draw))));
    box.append(facts([
      ['просмотров', fmtN(v.views), v.likes ? `лайков ${fmtN(v.likes)} (${(v.likes / Math.max(1, v.views) * 100).toFixed(1).replace('.', ',')} %)` : null],
      ['прирост в день', v.growth != null ? '+' + fmtN(v.growth) : '—', 'по замерам между прогонами'],
      ['просмотров в день', fmtN(v.vpd), 'с момента публикации'],
      ['комментариев', fmtN(v.comments)],
      ['балл', v.score != null ? String(v.score) : '—', POP_RU[v.level || 'unknown']],
      ['наша аудитория', a.audience_fit != null ? Math.round(a.audience_fit * 100) + ' %' : '—', 'совпадение по модели'],
    ]));
    box.append(cu.direct_file_url ? cutterBox(cu.direct_file_url, 'прямой mp4 из хранилища') : cutterBox(v.url, 'ссылка YouTube, файл ещё не готов'));
    const lvlCls = { clean: 'ok', check: 'caution', no: 'block' }[v.level] || 'unknown';
    const verdict = h('div', null,
      h('div', { class: 'rights-head' }, h('span', { class: 'tag tag-lg ' + POP_CLS[v.level || 'unknown'] }, POP_RU[v.level || 'unknown']), h('span', { class: 'risk', title: 'балл 0–100: от 45 проверить, от 70 чистое' }, h('span', { class: 'bar' }, h('i', { class: lvlCls, style: `width:${v.score || 0}%` })), h('span', { class: 'num' }, `балл ${v.score != null ? v.score : '—'}`)), licTag(v), v.made_for_kids ? h('span', { class: 'tag tag-warn' }, 'детское') : null, v.blocked_ru ? h('span', { class: 'tag tag-warn' }, 'закрыт для РФ') : null, v.pick ? h('span', { class: 'tag tag-accent' }, v.pick_auto ? 'в подборке (авто)' : 'в подборке') : null),
      h('ul', { class: 'why' }, (v.reasons || []).map(x => h('li', null, x))),
      v.notes && v.notes.length ? h('p', { class: 'rule' }, v.notes.join(' · ')) : null,
      h('div', { class: 'rights-actions' }, h('label', { class: 'inline' }, 'решение оператора', manual)));
    const framesBody = v.frames_available
      ? h('div', null, h('img', { class: 'frames', src: abs(`api/videos/${id}/frames.jpg`) + '?t=' + encodeURIComponent(v.frames_at || ''), alt: 'шесть кадров ролика' }), f.error ? h('p', { class: 'muted' }, f.error) : h('div', { class: 'chips', style: 'margin-top:8px' }, flagChips(f)), f.free_zones && f.free_zones.length ? h('p', { class: 'rule' }, 'свободно под наш баннер: ' + f.free_zones.join(', ')) : null, f.note ? h('p', { class: 'muted' }, f.note) : null)
      : h('p', { class: 'muted' }, f.error ? 'кадры не вынулись: ' + f.error : 'Кадры ещё не проверялись. «Разобрать заново» вынет 6 кадров с хранилища и покажет, что в картинке.');
    const kv = (pairs) => h('dl', { class: 'kv' }, pairs.filter(x => x && x[1]).flatMap(([k, val]) => [h('dt', null, k), h('dd', null, val)]));
    const analysisBody = Object.keys(a).length ? kv([['тема', a.topic], ['формат', a.format], ['ниша', a.niche], ['кто смотрит', a.audience], ['наша аудитория', a.audience_fit != null ? `${Math.round(a.audience_fit * 100)} %${a.audience_why ? ' · ' + a.audience_why : ''}` : null], ['почему зашло', a.why_popular], ['хук', a.hook], ['совет по баннеру', a.banner_advice], ['пригодность под баннер', a.reuse_fit != null ? Math.round(a.reuse_fit * 100) + ' %' : null], ['флаги', [a.is_reupload ? 'уже перезалив или компиляция' : null, a.has_sponsor ? 'спонсор в ролике' : null, a.music_heavy ? 'музыка в основе' : null, a.film_title ? 'кадры из «' + a.film_title + '»' : null].filter(Boolean).join(', ') || 'нет']]) : h('p', { class: 'muted' }, 'Нейросеть ещё не разбирала ролик.');
    box.append(h('div', { class: 'grid grid-halves', style: 'margin-top:18px' }, panel('Кадры: что в картинке', framesBody, '6 кадров с хранилища, вердикт модели'), panel('Оценка чистоты', verdict, 'лицензия, канал, текст, кадры, аудитория')));
    box.append(h('div', { class: 'grid grid-halves', style: 'margin-top:18px' },
      panel('Разбор нейросети', analysisBody, 'по названию, описанию, тегам и цифрам'),
      h('div', { class: 'grid', style: 'align-content:start' },
        panel('Где в чартах', regionChips(v), 'место в чарте «самое популярное» по стране и категории'),
        panel('Карточка YouTube', kv([['лицензия', v.license === 'creativeCommon' ? 'Creative Commons' : 'стандартная YouTube'], ['заявлен партнёром', v.licensed_content ? 'да: Content ID' : 'нет'], ['встраивание', v.embeddable ? 'разрешено' : 'запрещено'], ['субтитры', v.caption ? 'есть' : 'нет'], ['качество', v.definition], ['темы', (v.topics || []).join(', ')], ['теги', (v.tags || []).slice(0, 12).join(', ')], ['источник', v.origin === 'cc' ? 'поиск Creative Commons' : 'чарт'], ['id', v.video_id]])),
        v.series && v.series.length > 1 ? panel('Просмотры по замерам', columns(v.series.map(s => ({ day: s.ts.slice(5, 16).replace('T', ' '), value: s.views })), { tipLabel: 'просмотров', height: 120 }), 'между прогонами') : null)));
    if (v.description) box.append(h('details', { class: 'fold', style: 'margin-top:18px' }, h('summary', null, 'Описание ролика'), h('p', { class: 'muted', style: 'white-space:pre-wrap;margin-top:8px' }, v.description.slice(0, 3000))));
  };
  draw();
}

// ---------- авторские права ----------
const RIGHTS_RU = { ok: 'проходит', caution: 'с оговорками', block: 'не проходит', unknown: 'не проверено' };
const RIGHTS_CLS = { ok: 'tag-good', caution: 'tag-warn', block: 'tag-bad', unknown: '' };
const RIGHTS_POLICY_RU = { block: 'блокирует и страйкует', claim: 'заявляет права через Content ID', tolerant: 'терпит нарезки', unknown: 'поведение неизвестно' };
const RIGHTS_EVENT_RU = { claim: 'заявка Content ID', block: 'ролик заблокирован', strike: 'страйк', ok: 'прошёл без претензий', manual: 'решение оператора' };
const rightsTag = (r, short = false) => { if (!r) return null; const lvl = r.level || 'unknown'; return h('span', { class: 'tag rights-tag ' + RIGHTS_CLS[lvl], title: (r.reasons || []).join('\n') || 'проверка авторских прав' }, (short ? '' : 'права: ') + RIGHTS_RU[lvl] + (!short && r.risk != null ? ` · риск ${r.risk}` : '')); };
const rightsLine = (r) => { if (!r || !r.level || r.level === 'ok' || !(r.reasons || []).length) return null; return h('div', { class: 'rights-line ' + r.level }, (r.level === 'block' ? '✕ ' : '⚠ ') + r.reasons.slice(0, 2).join('; ') + (r.clip_max_s ? ` · клип до ${r.clip_max_s} с` : '')); };
const eventTag = (kind) => h('span', { class: `tag ${kind === 'ok' ? 'tag-good' : kind === 'claim' ? 'tag-warn' : kind === 'manual' ? 'tag-accent' : 'tag-bad'}` }, RIGHTS_EVENT_RU[kind] || kind);
function rightsPanel(titleId) {
  const body = h('div');
  const draw = (r) => {
    body.innerHTML = '';
    if (!r) { body.append(h('p', { class: 'muted' }, 'не удалось загрузить оценку')); return; }
    const lvl = r.level || 'unknown', s = r.signals || {};
    body.append(h('div', { class: 'rights-head' },
      h('span', { class: 'tag tag-lg ' + RIGHTS_CLS[lvl] }, RIGHTS_RU[lvl]),
      h('span', { class: 'risk', title: 'риск 0–100: от 40 с оговорками, от 65 не проходит' }, h('span', { class: 'bar' }, h('i', { class: lvl, style: `width:${r.risk || 0}%` })), h('span', { class: 'num' }, r.risk != null ? `риск ${r.risk}` : 'риск —')),
      r.holder ? h('span', { class: 'muted' }, `${r.holder}: ${r.holder_policy_ru || ''}`) : null,
      r.passes === false ? h('span', { class: 'tag tag-bad' }, 'в календарь не ставится') : null));
    body.append(h('ul', { class: 'why' }, (r.reasons || []).map(x => h('li', null, x))));
    if (r.rule) body.append(h('p', { class: 'rule' }, r.rule));
    if (r.notes && r.notes.length) body.append(h('p', { class: 'rule' }, r.notes.join(' · ')));
    const kv = [
      ['чужих нарезок перепроверено', `${fmtFull(s.rated || 0)} · снесено по правам ${fmtFull(s.removed_strict || 0)}${s.removed_total ? ` · удалено всего ${fmtFull(s.removed_total)}` : ''}`],
      s.claimants && s.claimants.length ? ['кто жаловался', s.claimants.map(([n, k]) => `${n} (${k})`).join(', ')] : null,
      ['исходник', s.source ? `${s.source.official ? 'официальный канал' : 'неофициальный канал'}${s.source.alive === false ? ', удалён' : s.source.alive ? ', жив' : ''}${s.source.channel ? ': ' + s.source.channel : ''}` : 'не найден'],
      ['нейросеть о правообладателе', s.llm ? `${s.llm.holder || '—'} · ${RIGHTS_POLICY_RU[s.llm.holder_policy] || s.llm.holder_policy}${s.llm.public_domain ? ' · общественное достояние' : ''}${s.llm.official_free_ru ? ' · официально выложен бесплатно' : ''}${s.llm.music_risk ? ' · музыкальный риск' : ''} · уверенность ${Math.round((s.llm.confidence || 0) * 100)} %${s.llm.note ? ' · ' + s.llm.note : ''}` : 'не спрашивали'],
      ['проверено', r.checked_at ? ago(r.checked_at) : '—'],
    ].filter(Boolean);
    body.append(h('dl', { class: 'kv' }, kv.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])));
    if (s.events && s.events.length) body.append(h('h3', { style: 'margin:10px 0 6px' }, 'Наш опыт за 90 дней'), h('ul', { class: 'src-list' }, s.events.map(e => h('li', null, eventTag(e.kind), h('span', null, [e.claimant, e.note].filter(Boolean).join(' · ')), h('span', { class: 'meta' }, ago(e.ts))))));
    const refresh = h('button', { class: 'btn btn-sm', type: 'button', title: 'спросить модель о правообладателе, перечитать страницы чужих нарезок, проверить исходники' }, 'проверить заново');
    refresh.addEventListener('click', async () => { refresh.disabled = true; refresh.textContent = 'проверяю…'; try { draw(await post(`api/films/${titleId}/rights/refresh`)); toast('Права перепроверены'); } catch (e) { toast(e.message); refresh.disabled = false; refresh.textContent = 'проверить заново'; } });
    const manual = h('select', { title: 'решение оператора сильнее автоматической оценки' }, [['', 'авто'], ['ok', 'проходит'], ['caution', 'с оговорками'], ['block', 'не проходит']].map(([v, l]) => h('option', { value: v, selected: (s.manual_level || '') === v }, l)));
    manual.addEventListener('change', async () => { try { draw(await post(`api/films/${titleId}/rights/manual`, { level: manual.value || null })); toast('Решение сохранено'); } catch (e) { toast(e.message); } });
    const evKind = h('select', null, [['claim', 'заявка Content ID'], ['block', 'ролик заблокирован'], ['strike', 'страйк'], ['ok', 'прошёл без претензий']].map(([v, l]) => h('option', { value: v }, l)));
    const evNote = h('input', { type: 'text', placeholder: 'кто и что, необязательно' });
    const evBtn = h('button', { class: 'btn btn-sm', type: 'button' }, 'записать');
    evBtn.addEventListener('click', async () => { try { draw(await post('api/rights/events', { title_id: titleId, kind: evKind.value, note: evNote.value })); toast('Записано, оценка пересчитана'); } catch (e) { toast(e.message); } });
    body.append(h('div', { class: 'rights-actions' }, refresh, h('label', { class: 'inline' }, 'решение оператора', manual), h('span', { class: 'inline-form' }, 'наш ролик:', evKind, evNote, evBtn)));
  };
  api(`api/films/${encodeURIComponent(titleId)}/rights`).then(draw).catch(() => draw(null));
  return body;
}

async function renderCut(main) {
  main.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Что резать'), h('p', { class: 'lead' }, 'На каждый день четыре произведения: исходник со ссылкой, объяснение «почему», сцены и план. Каждое проверено по авторским правам: не прошедшие в календарь не попадают. Бриф уходит в нарезчик. Чужие шортсы здесь только доказательство, что тема заходит.')),
    h('div', { class: 'actions' },
      h('a', { class: 'btn', href: abs('api/brief/today'), target: '_blank', rel: 'noopener', html: ICON.doc + ' бриф на сегодня' }),
      h('button', { class: 'btn', type: 'button', title: 'скачать на сервер исходники с Rutube для сегодняшних и завтрашних слотов', html: ICON.dl + ' подготовить mp4 на сегодня', onclick: async () => { try { const r = await post('api/schedule/prepare-files'); toast(r.queued.length ? `В очереди: ${r.queued.filter(x => x.ok).length}` : 'Все файлы уже готовы или исходники только на YouTube'); } catch (e) { toast(e.message); } } }),
      h('button', { class: 'btn', type: 'button', title: 'отправить дайджест в Telegram прямо сейчас', onclick: async () => { try { await post('api/telegram/digest'); toast('Дайджест отправлен'); } catch (e) { toast('Не отправлено: ' + e.message); } } }, 'дайджест в Telegram'),
      h('button', { class: 'btn', type: 'button', html: ICON.refresh + ' пересобрать', title: 'пересобрать незафиксированные слоты заново', onclick: async () => { if (!confirm('Пересобрать незафиксированные слоты календаря заново?')) return; try { await post('api/schedule/build', { rebuild: true }); toast('Календарь пересобран'); render(); } catch (e) { toast(e.message); } } }))));
  const box = h('div'); main.append(box); box.append(skeleton(8));
  const [sched, rec] = await Promise.all([api('api/schedule', { days: 7, back: 2 }), api('api/recommend', withFilters({ lang: state.filters.lang === 'all' ? 'ru' : state.filters.lang, limit: 12 }))]);
  box.innerHTML = '';
  const days = sched.days;
  let selected = state.query.day && days.some(d => d.day === state.query.day) ? state.query.day : (days.find(d => d.is_today) || days[0]).day;
  const strip = h('div', { class: 'week', role: 'tablist' });
  const boardWrap = h('div');
  const drawStrip = () => {
    strip.innerHTML = '';
    days.forEach(d => {
      const dt = parseIso(d.day + 'T12:00:00Z');
      const b = h('button', { class: 'week-day' + (d.day === selected ? ' active' : '') + (d.is_past ? ' past' : ''), type: 'button', role: 'tab', 'aria-selected': String(d.day === selected) },
        h('span', { class: 'wd' }, d.is_today ? 'сегодня' : ({ 'понедельник': 'пн', 'вторник': 'вт', 'среда': 'ср', 'четверг': 'чт', 'пятница': 'пт', 'суббота': 'сб', 'воскресенье': 'вс' }[d.weekday] || d.weekday.slice(0, 2))), h('span', { class: 'dn' }, String(dt.getDate())),
        h('span', { class: 'dots' }, d.items.length ? d.items.map(it => h('i', { class: it.status === 'done' ? 'd' : it.status === 'skipped' ? 's' : 'p' })) : h('i')));
      b.addEventListener('click', () => { selected = d.day; state.query.day = d.day; drawStrip(); drawBoard(); });
      strip.append(b);
    });
  };
  const planChips = (p) => h('div', { class: 'plan-chips' }, [p.duration ? h('span', null, 'длительность ' + p.duration) : null, p.hook ? h('span', null, 'хук: ' + p.hook) : null, p.best_time ? h('span', null, p.best_time) : null, ...(p.hashtags || []).slice(0, 3).map(t => h('span', null, '#' + t))].filter(Boolean));
  const cutCard = (it, idx, day) => {
    const card = h('article', { class: 'cut-card' + (it.status === 'done' ? ' done' : it.status === 'skipped' ? ' skipped' : '') });
    const fill = () => {
      card.innerHTML = '';
      const ph = h('div', { class: 'poster-ph' }, (it.label || '?').slice(0, 1).toUpperCase());
      let posterEl = null;
      if (it.title_id && it.title_id.startsWith('Q')) { posterEl = h('img', { class: 'poster', src: abs(`api/films/${it.title_id}/poster.jpg`), alt: '', loading: 'lazy' }); posterEl.addEventListener('error', () => posterEl.replaceWith(ph)); }
      const body = h('div', { class: 'cut-body' },
        h('div', { class: 'cut-title' }, h('span', { class: 'slot' }, String(idx + 1).padStart(2, '0')), it.title_id ? h('a', { href: `#/film/${it.title_id}` }, it.label || it.title_id) : h('span', { class: 'muted' }, 'пусто'),
          it.year ? h('span', { class: 'muted' }, `(${it.year})`) : null, it.kind_ru ? h('span', { class: 'tag' }, it.kind_ru) : null, rightsTag(it.rights),
          it.locked ? h('span', { class: 'tag tag-accent', title: 'слот задан вручную, при пересборке не меняется' }, 'вручную') : null,
          it.status === 'done' ? h('span', { class: 'tag tag-good' }, 'сделано') : it.status === 'skipped' ? h('span', { class: 'tag tag-warn' }, 'пропущено') : null),
        it.title_id && it.source ? cutterLink(it) : null,
        it.title_id ? sourceLine(it.source, it.title_id, items => { it.source = items.find(x => x.verified) || items[0] || null; fill(); }) : null,
        it.source_alt ? h('div', { class: 'cal-plan' }, 'запасной исходник: ', extLink(it.source_alt.url, `${/rutube\.ru/.test(it.source_alt.url) ? 'Rutube' : 'YouTube'} · ${it.source_alt.channel || ''} · ${it.source_alt.duration_s ? Math.round(it.source_alt.duration_s / 60) + ' мин' : ''}`), ' ', fileButton(it.source_alt, it.title_id)) : null,
        it.title_id ? explainBlock(it.explain, it.title_id, text => { it.explain = text; fill(); }) : null,
        it.reason ? h('div', { class: 'cal-reason' }, it.reason) : null,
        it.plan && (it.plan.duration || it.plan.hook || it.plan.best_time) ? planChips(it.plan) : null,
        rightsLine(it.rights),
        it.plan && it.plan.scenes && it.plan.scenes.length ? h('div', { class: 'cal-plan' }, 'сцены: ' + it.plan.scenes.map(sc => sc.name).join('; ')) : null);
      const actions = h('div', { class: 'cut-actions' },
        h('div', { class: 'ibar' },
          it.status !== 'done' ? iconBtn('check', 'сделано', async () => { await post(`api/schedule/${it.id}/status`, { status: 'done' }); it.status = 'done'; card.className = 'cut-card done'; fill(); drawStrip(); toast('Отмечено: сделано'); }, 'good') : iconBtn('undo', 'вернуть в план', async () => { await post(`api/schedule/${it.id}/status`, { status: 'planned' }); it.status = 'planned'; card.className = 'cut-card'; fill(); drawStrip(); }),
          it.status === 'planned' ? iconBtn('x', 'пропустить', async () => { await post(`api/schedule/${it.id}/status`, { status: 'skipped' }); it.status = 'skipped'; card.className = 'cut-card skipped'; fill(); drawStrip(); }, 'warn') : null,
          it.title_id ? iconLink('doc', 'бриф JSON для нарезчика', abs(`api/brief/${it.title_id}`)) : null,
          it.title_id ? iconBtn('copy', 'скопировать подсказку «что искать» для нарезчика', async () => { try { const b = await api(`api/brief/${it.title_id}`); const t = b.cutter && b.cutter.hint; if (!t) { toast('Сначала разберите сцены на странице произведения'); return; } await navigator.clipboard.writeText(t); toast('Подсказка скопирована'); } catch (x) { toast(x.message); } }) : null,
          it.kinopoisk_url ? iconLink('film', 'Кинопоиск', it.kinopoisk_url) : null),
        assignControl({ placeholder: 'заменить на…', allowNone: false, allowCreate: false, onPick: async (t) => { try { await post(`api/schedule/${it.id}/replace`, { title_id: t.title_id }); Object.assign(it, { title_id: t.title_id, label: t.label, year: t.year, kind_ru: t.kind_ru, locked: true, reason: 'выбрано вручную', plan: {}, source: null, explain: null }); fill(); toast(`В слот поставлено: ${t.label}`); } catch (e) { toast(e.message); } } }));
      card.append(posterEl || ph, body, actions);
    };
    fill();
    return card;
  };
  const drawBoard = () => {
    boardWrap.innerHTML = '';
    const d = days.find(x => x.day === selected);
    if (!d) return;
    const done = d.items.filter(i => i.status === 'done').length;
    boardWrap.append(h('div', { class: 'board-head' }, h('h2', null, (d.is_today ? 'Сегодня, ' : '') + d.weekday + ', ' + fmtDate(d.day)), h('span', { class: 'muted' }, d.items.length ? `${done} из ${d.items.length} сделано` : 'пусто')));
    const list = h('div', { class: 'board' });
    d.items.forEach((it, i) => list.append(cutCard(it, i, d)));
    if (!d.items.length) list.append(h('div', { class: 'empty' }, h('b', null, d.is_past ? 'На этот день ничего не планировалось' : 'Пока пусто'), d.is_past ? '' : 'Календарь достраивается после каждого сбора, можно добавить руками.'));
    if (!d.is_past) list.append(h('div', { class: 'cal-add' }, assignControl({ placeholder: '+ добавить произведение в этот день', allowNone: false, allowCreate: false, onPick: async (t) => { try { await post('api/schedule/add', { day: d.day, title_id: t.title_id }); toast(`Добавлено на ${fmtDate(d.day)}: ${t.label}`); render(); } catch (e) { toast(e.message); } } })));
    boardWrap.append(list);
  };
  drawStrip(); drawBoard();
  box.append(strip, boardWrap);
  const recCards = rec.items.map((it, i) => h('div', { class: 'rec-card' },
    poster(it.title_id),
    h('div', { class: 'rec-body' },
      h('div', { class: 'rec-head' }, h('span', { class: 'rank' }, i + 1), h('a', { href: `#/film/${it.title_id}`, class: 'title-link' }, it.label, it.year ? h('span', { class: 'muted' }, ` (${it.year})`) : null), h('span', { class: 'tag' }, it.kind_ru), rightsTag(it.rights), h('span', { class: 'score', title: 'балл возможности' }, h('span', { class: 'bar' }, h('i', { style: `width:${Math.min(100, it.opportunity)}%` })), h('span', { class: 'num' }, it.opportunity))),
      h('ul', { class: 'why' }, it.why.slice(0, 3).map(w => h('li', null, w))),
      rightsLine(it.rights),
      it.wiki_7d != null || it.trends_7d != null ? h('div', { class: 'interest-line' }, 'Интерес: ', it.wiki_7d != null ? `Википедия ${fmtN(it.wiki_7d)}/день${it.wiki_ratio ? ' (×' + String(it.wiki_ratio).replace('.', ',') + ')' : ''}` : '', it.trends_7d != null ? `${it.wiki_7d != null ? ' · ' : ''}Google Trends ${Math.round(it.trends_7d)}/100` : '') : null,
      planChips(it.plan),
      h('div', { class: 'ibar', style: 'margin-top:8px' },
        h('button', { class: 'btn btn-sm', type: 'button', html: ICON.plus + ' сегодня', onclick: async () => { try { await post('api/schedule/add', { day: sched.today, title_id: it.title_id }); toast('Добавлено на сегодня'); render(); } catch (e) { toast(e.message); } } }),
        h('button', { class: 'btn btn-sm', type: 'button', html: ICON.plus + ' завтра', onclick: async () => { const i0 = days.findIndex(x => x.is_today); const tm = days[i0 + 1]; try { await post('api/schedule/add', { day: tm ? tm.day : sched.today, title_id: it.title_id }); toast('Добавлено на завтра'); render(); } catch (e) { toast(e.message); } } }),
        it.plan.refs && it.plan.refs[0] ? extLink(it.plan.refs[0].url, 'лучший ролик') : null))));
  const fold = h('details', { class: 'fold', style: 'margin-top:18px' }, h('summary', null, `Кандидаты, из которых собирается календарь (${rec.items.length})`, helpIcon('opportunity')),
    h('div', { class: 'rec-list', style: 'margin-top:8px' }, recCards.length ? recCards : empty('Пока нет рекомендаций', 'Нужны опознанные ролики за период.')));
  box.append(fold);
}

// ---------- сцены и хуки на странице произведения ----------
function insightsPanel(titleId, cached, nVideos) {
  const body = h('div');
  const draw = (ins) => {
    body.innerHTML = '';
    if (ins) {
      body.append(h('p', { class: 'muted', style: 'margin-bottom:8px' }, `разбор от ${ago(ins.generated_at)} по ${ins.n_videos} роликам, модель ${ins.model || ''}`));
      if (ins.scenes.length) body.append(h('ol', { class: 'scenes' }, ins.scenes.map(s => h('li', null, h('b', null, s.name), h('span', { class: 'muted' }, ` · ${s.n} ${plural(s.n, 'ролик', 'ролика', 'роликов')}, ${fmtN(s.views)} просм.${s.where ? ' · где: ' + s.where : ''}`), h('div', { class: 'cal-reason' }, s.what_to_cut), s.quotes && s.quotes.length ? h('div', { class: 'cal-plan' }, 'реплики для поиска: ' + s.quotes.map(q => '«' + q + '»').join(', ')) : null, s.top && s.top.length ? h('div', { class: 'cal-plan' }, 'референсы: ', s.top.map(t => extLink(t.url, truncate(t.title || t.video_id, 40)))) : null))));
      if (ins.hooks.length) body.append(h('div', { style: 'margin-top:10px' }, h('h3', null, 'Заголовки-хуки в стиле лучших роликов'), h('ul', { class: 'why' }, ins.hooks.map(x => h('li', null, x)))));
      if (ins.advice.length) body.append(h('div', { style: 'margin-top:10px' }, h('h3', null, 'Советы по подаче'), h('ul', { class: 'why' }, ins.advice.map(x => h('li', null, x)))));
    } else {
      body.append(h('p', { class: 'muted' }, nVideos >= 2 ? 'Нейросеть сгруппирует ролики по сценам произведения, подскажет, какие моменты резать, и предложит заголовки.' : 'Нужно хотя бы два ролика, чтобы было что разбирать.'));
    }
    const btn = h('button', { class: 'btn btn-sm' + (ins ? '' : ' btn-primary'), type: 'button', disabled: nVideos < 2, style: 'margin-top:10px' }, ins ? 'разобрать заново' : 'разобрать сцены нейросетью');
    btn.addEventListener('click', async () => { btn.disabled = true; btn.textContent = 'думает…'; try { const r = await post(`api/films/${titleId}/insights?period=all`); draw(r.insights); toast('Сцены разобраны'); } catch (e) { toast('Не вышло: ' + e.message); btn.disabled = false; btn.textContent = 'попробовать ещё раз'; } });
    body.append(h('div', null, btn, ' ', h('a', { class: 'btn btn-sm', href: abs(`api/brief/${titleId}`), target: '_blank', rel: 'noopener', style: 'margin-top:10px' }, 'бриф JSON')));
  };
  draw(cached);
  return body;
}


// ---------- подсказки «что это и зачем» ----------
const HELP = {
  period: ['Период', 'За какие дни берём ролики по дате публикации. 7 дней показывает, что заходит прямо сейчас; 30 дней надёжнее для выбора тем; «всё» нужно для истории и разметки.'],
  lang: ['Язык роликов', 'Определяется по заголовку и описанию. «Русский» отсекает иностранные вирусные ролики, которые попадают по общим хэштегам и не относятся к нашей аудитории.'],
  conf: ['Уверенность распознавания', 'Насколько сервис уверен, что ролик нарезан именно из этого произведения: 0,95 подпись «Название: …», 0,85 кавычки, 0,8 хэштег, 0,5–0,7 совпадение в тексте, 0,9 нейросеть по кадру. Ниже порога ролики в статистику не идут, но их можно разметить вручную.'],
  min_views: ['Минимум просмотров', 'Отсекает мелкие ролики, чтобы статистику не размывал шум.'],
  score: ['Балл', 'Сводный ранг произведения от 0 до 100: 45 % скорость набора просмотров, 25 % число новых роликов за неделю, 20 % медиана просмотров, 10 % число каналов. Чем выше, тем сильнее тема сейчас.'],
  opportunity: ['Балл возможности', 'Балл плюс поправки: свободная ниша (мало каналов) и рост интереса поднимают, один-единственный ролик по теме и толпа конкурентов опускают. По нему строится календарь.'],
  median: ['Медиана просмотров', 'Половина роликов по теме набрала больше, половина меньше. Честнее суммы: сумму делает один вирусный ролик, медиана показывает, заходит ли тема стабильно.'],
  vpd: ['Просмотров в день', 'Просмотры ролика, делённые на его возраст, и сумма по всем роликам темы. Так сравниваются свежие и старые ролики.'],
  growth: ['Прирост за 7 дней', 'Разница между точными замерами просмотров в разных прогонах сбора. Появляется, когда ролик замерен хотя бы дважды.'],
  channels: ['Каналов', 'Сколько разных каналов уже режут это произведение. Мало каналов при высокой медиане означает свободную нишу.'],
  fresh: ['Свежие ролики', 'Сколько роликов по теме опубликовано за последние 7 дней. Показывает, жива ли тема или это старый хит.'],
  outliers: ['Аутлаеры', 'Доля роликов по теме, набравших в 3 и более раз больше обычного для своего канала (медиана роликов канала за период). Так считают VidIQ и Virlo: если тема бьёт норму канала, виновата тема, а не аудитория канала. Чем выше доля, тем вернее тема зайдёт и у вас.'],
  small: ['Маленькие каналы', 'Медиана просмотров у роликов с каналов до 100 тысяч подписчиков. Если она высокая, тема работает без раскрученной аудитории: лучший знак для нового канала.'],
  interest: ['Интерес аудитории', 'Независимо от наших шортсов: просмотры статьи о произведении в русской Википедии в день и поисковый интерес в Google Trends по России. Рост в полтора раза и выше добавляет балл возможности.'],
  videos: ['Видео под перезалив', 'Чарты «самое популярное» YouTube по странам СНГ (Data API, 1 единица квоты за 50 роликов) и поиск роликов с лицензией Creative Commons, в том числе коротких. Шортс распознаётся по странице youtube.com/shorts: если она не уходит на /watch, это шортс, и резать его не нужно: заливщик берёт как есть. У каждого ролика: замеры просмотров между прогонами, язык, лицензия, флаг «заявлен партнёром» (Content ID), подписчики канала, разбор нейросети: тема, формат, хук, спонсор, совпадение с нашей аудиторией VPN (профиль задаётся в «Сборе»).'],
  clean: ['Чистота ролика', 'Балл 0–100 и уровень: чистое (от 70, кадры проверены, нет логотипов и плашек, нет заявок партнёра), проверить (от 45), не брать. Снижают: заявка партнёра, канал правообладателя, музыка, водяной знак, чужая реклама, призыв подписаться, спонсор, не наш язык. Поднимают: Creative Commons, совпадение с аудиторией, пригодность под баннер, прирост просмотров, чистые кадры, свободные зоны. Кадры: 6 штук вынимаются на хранилище без скачивания всего видео.'],
  picks: ['Подборка', 'Чистые ролики с баллом от порога попадают в подборку сами; любой ролик можно взять или убрать руками. «Подборка JSON» (GET /api/videos/picks) отдаёт заливщику ссылку, прямой mp4 из хранилища, кадры, свободные зоны под баннер и совет, где его ставить. «mp4 для подборки» скачивает все файлы разом.'],
  rights: ['Авторские права', 'Пройдёт ли произведение к нарезчику. Четыре улики: сносы чужих нарезок по жалобам (страница YouTube называет заявителя), правообладатель и его привычки (нейросеть плюс список известных студий и каналов), официальный ли и живой ли исходник, наш опыт: заявки, блокировки и страйки на наших роликах. Итог: проходит, с оговорками, не проходит. «Не проходит» в календарь не ставится. Клип не длиннее 59 с: шортс дольше 60 с с заявкой Content ID блокируется.'],
  rights_events: ['Наш опыт', 'Что случилось с нашими роликами по произведению: заявка Content ID, блокировка, страйк или прошёл чисто. Заносит оператор на странице произведения или нарезчик запросом POST /api/rights/events. Страйк сразу выводит произведение из календаря, чистые публикации снижают риск.'],
  source: ['Исходник', 'Полный фильм или серии, найденные на YouTube и Rutube. Нейросеть проверяет каждого кандидата: не трейлер, не пересказ, не другой фильм; определяет серию, сезон, язык. Ссылка уходит в конвейер нарезки.'],
  explain: ['Почему', 'Объяснение нейросети по статистике и лучшим чужим роликам: что цепляет зрителя, что резать первым, чего избегать, риск.'],
  scenes: ['Сцены и хуки', 'Нейросеть группирует чужие ролики по сценам произведения, подсказывает, какой момент резать, даёт реплики для поиска по субтитрам и заголовки-хуки в стиле лучших роликов.'],
  references: ['Референсы', 'Чужие нарезки, которые уже зашли. Не для перезалива, а как доказательство спроса и образец подачи.'],
  brief: ['Бриф', 'JSON для конвейера нарезки. Блок cutter: ссылка на исходник (YouTube в нарезчик как есть, для Rutube можно подготовить прямую ссылку на mp4), подсказка «что искать» из сцен и реплик, длина клипа от и до (не больше 59 с, если права не свободны), rights_ok и уровень прав.'],
  calendar: ['Календарь нарезки', 'Сервис сам раскладывает лучшие произведения по дням: чередует сериалы и фильмы, не повторяет тему 10 дней, достраивается после каждого сбора. Отмечайте сделанное, заменяйте и добавляйте руками: ручные слоты не трогаются. Произведения, не прошедшие проверку авторских прав, в календарь не ставятся.'],
  recognition: ['Опознание', 'Сначала словарь из 133 тысяч названий (подписи, кавычки, хэштеги, текст), потом нейросеть по кадру для оставшихся. Ручная разметка в «Роликах» учит словарь.'],
  neuro: ['Нейросеть', 'Модель со зрением через OpenRouter: опознаёт фильм по кадру, проверяет исходники, пишет объяснения и разбирает сцены. Расход виден здесь же, есть дневной потолок.'],
  dict: ['Словарь произведений', 'Фильмы, сериалы, мультфильмы, аниме и телешоу из Wikidata с ID Кинопоиска, годами, жанрами и вариантами написания. Плюс то, что добавили вы и нейросеть.'],
  sources_cfg: ['Источники сбора', 'Страницы хэштегов и вкладки Shorts каналов. Каналы с несколькими опознанными роликами берутся в слежение сами, хэштеги с высокой долей киношортсов тоже.'],
  run: ['Прогон сбора', 'Хэштеги → каналы → детали роликов → распознавание → нейросеть → интерес аудитории → авторские права → календарь, исходники и объяснения → тревоги. Идёт по расписанию, можно запустить вручную.'],
  interests_page: ['Интересы', 'Срезы по опознанным роликам: что заходит по жанрам, типам, странам, эпохам, длительности, приёмам заголовка, времени публикации. Сравнивайте по медиане.'],
  shorts_page: ['Ролики и разметка', 'Все собранные шортсы. Неопознанным можно привязать произведение: поиск по словарю, новое произведение, «не фильм», и запрет ложной улики для всех роликов сразу.'],
  methods: ['Как опознаны', 'По каким уликам сервис понял, что это за фильм. Чем больше «подпись» и «кавычки», тем чище данные; «нейросеть по кадру» закрывает ролики без названия.'],
  telegram: ['Telegram', 'Утренний дайджест: что режем сегодня и завтра плюс топ-8 с причинами; тревога, когда произведение резко набирает после прогона.'],
};
const PANEL_HELP = {
  'Топ произведений по баллу': 'score', 'Мало конкурентов': 'channels', 'Набирают на этой неделе': 'fresh', 'Как опознаны ролики': 'methods',
  'Полнота данных': 'recognition', 'Календарь нарезки': 'calendar', 'Кандидаты: из чего собран календарь': 'opportunity', 'Исходник для нарезки': 'source',
  'Почему это стоит резать': 'explain', 'Какие сцены режут и какие хуки заходят': 'scenes', 'Референсы: чужие нарезки, которые зашли': 'references',
  'Интерес аудитории': 'interest', 'Видео под перезалив': 'videos', 'Оценка чистоты': 'clean', 'Кадры: что в картинке': 'clean', 'Авторские права': 'rights', 'Наш опыт с правами': 'rights_events', 'Нейросеть: опознание по кадру и разбор сцен': 'neuro', 'Словарь произведений': 'dict', 'Прогон': 'run',
  'Telegram: дайджест и тревоги': 'telegram', 'Жанры': 'interests_page',
};
const FACT_HELP = { 'произведений': 'recognition', 'опознан фильм': 'recognition', 'медиана просмотров': 'median', 'просмотров в день': 'vpd', 'прирост за 7 дней': 'growth', 'словарь': 'dict' };
function showHelp(e, key) {
  const [title, text] = HELP[key] || ['', key];
  tip.className = 'tooltip help-pop'; tip.innerHTML = ''; tip.append(h('b', null, title), h('p', null, text)); tip.hidden = false; moveTip(e);
}
function hideHelp() { tip.hidden = true; tip.className = 'tooltip'; }
function helpIcon(key) {
  if (!HELP[key]) return null;
  const b = h('button', { class: 'help', type: 'button', 'aria-label': 'пояснение: ' + HELP[key][0] }, '?');
  b.addEventListener('pointerenter', e => showHelp(e, key)); b.addEventListener('pointermove', moveTip); b.addEventListener('pointerleave', hideHelp);
  b.addEventListener('focus', e => showHelp({ clientX: b.getBoundingClientRect().left, clientY: b.getBoundingClientRect().bottom }, key)); b.addEventListener('blur', hideHelp);
  b.addEventListener('click', e => { e.stopPropagation(); tip.hidden ? showHelp(e, key) : hideHelp(); });
  return b;
}
$$('[data-help]').forEach(b => { const key = b.dataset.help; b.addEventListener('pointerenter', e => showHelp(e, key)); b.addEventListener('pointermove', moveTip); b.addEventListener('pointerleave', hideHelp); b.addEventListener('click', e => showHelp(e, key)); });

// ---------- онбординг ----------
const TOUR = [
  ['Добро пожаловать в Кино-тренды', 'Сервис следит, какие фильмы и сериалы режут на YouTube Shorts для русскоязычной аудитории, и говорит, что резать вам: с исходником, сценами и объяснением. Пройдёмся по разделам за минуту.', '#/overview'],
  ['Обзор', 'Что набирает прямо сейчас: топ по баллу, свободные ниши, свежие темы недели, динамика по дням и полнота данных. Фильтры сверху действуют на все разделы.', '#/overview'],
  ['Что резать', 'Главный экран. Календарь на неделю: на каждый день произведение, проверенный исходник, объяснение «почему», сцены и план. Каждое произведение проверено по авторским правам. Кнопка «бриф» отдаёт JSON для конвейера нарезки. Отмечайте «сделано», меняйте и добавляйте руками.', '#/cut'],
  ['Видео', 'Популярные ролики СНГ под перезалив с нашим баннером: чарты по странам, лицензия и заявки, кадры без чужих логотипов, совпадение с аудиторией VPN. Чистые попадают в подборку сами, «подборка JSON» уходит заливщику.', '#/videos'],
  ['Фильмы и сериалы', 'Вся таблица с цифрами: ролики, каналы, медиана, просмотров в день, прирост, интерес аудитории, балл. Клик по названию открывает карточку с роликами, сценами, исходниками и интересом.', '#/films'],
  ['Интересы', 'Что заходит по жанрам, типам, странам, эпохам, длительности, приёмам заголовка и времени публикации. Сравнивайте по медиане, а не по сумме.', '#/interests'],
  ['Ролики и разметка', 'Все собранные шортсы. Если фильм не опознан, подскажите его сервису один раз: он запомнит написание и больше не промахнётся. Ложную улику можно запретить для всех роликов сразу.', '#/shorts'],
  ['Сбор', 'Запуск и журнал прогона, нейросеть с расходами, источники, Telegram, словарь, расписание, настройки календаря. Знак «?» рядом с любым элементом объясняет, что это и зачем.', '#/collect'],
];
function startTour() {
  let i = 0;
  const overlay = h('div', { class: 'tour-overlay' });
  const card = h('div', { class: 'tour', role: 'dialog', 'aria-modal': 'true' });
  overlay.append(card);
  const draw = () => {
    const [title, text, route] = TOUR[i];
    if (location.hash !== route) go(route);
    card.innerHTML = '';
    card.append(h('h2', null, title), h('p', null, text), h('div', { class: 'steps' }, TOUR.map((_, k) => h('i', { class: k <= i ? 'on' : '' }))),
      h('div', { class: 'actions' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: finish }, 'Пропустить'),
        h('div', null, i > 0 ? h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { i--; draw(); } }, 'Назад') : null, ' ',
          h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: () => { if (i < TOUR.length - 1) { i++; draw(); } else finish(); } }, i < TOUR.length - 1 ? 'Дальше' : 'Начать работу'))));
    card.querySelector('.btn-primary').focus();
  };
  const finish = () => { overlay.remove(); try { localStorage.setItem('kt.onboarded', '1'); } catch (_) {} go('#/cut'); };
  overlay.addEventListener('keydown', e => { if (e.key === 'Escape') finish(); });
  document.body.append(overlay);
  draw();
}
$('#help-btn').addEventListener('click', () => go('#/help'));

// ---------- страница «Как пользоваться» ----------
function renderHelp(main) {
  const gl = (keys) => h('dl', null, keys.flatMap(k => [h('dt', null, HELP[k][0]), h('dd', null, HELP[k][1])]));
  main.append(h('div', { class: 'helpdoc' },
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Как пользоваться'), h('p', { class: 'lead' }, 'Сервис промежуточный: он не режет и не заливает, а находит, что резать, и отдаёт исходник, сцены и бриф в конвейер.')),
      h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', onclick: startTour }, 'показать тур заново'))),
    h('h2', null, 'Рабочий цикл на день'),
    h('ol', null,
      h('li', null, 'Откройте «Что резать». В слоте сегодняшнего дня: произведение, исходник со ссылкой, «почему», план (длительность, хук, время, хэштеги).'),
      h('li', null, 'Нажмите «бриф»: JSON с исходником, сценами, репликами для поиска по субтитрам, хуками и референсами уходит в админку нарезки. «Бриф на сегодня» отдаёт все слоты дня разом. В нарезчик: ссылку YouTube вставляйте как есть; для Rutube нажмите «подготовить файл» и вставьте прямую ссылку на mp4; «подсказка для нарезчика» копирует текст в поле «Что искать».'),
      h('li', null, 'Не нравится слот: «заменить на…» подставит другое произведение, «пропустить» уберёт, «добавить произведение в этот день» вставит своё. Ручные слоты при пересборке не трогаются.'),
      h('li', null, 'Сделали ролик: «сделано». Это учитывается, чтобы не повторять тему 10 дней.'),
      h('li', null, 'Раз в день смотрите «Ролики» со статусом «неопознанные»: 2–3 клика разметки учат словарь, и следующая статистика становится точнее.')),
    h('h2', null, 'Откуда берутся данные'),
    h('ul', null,
      h('li', null, 'Страницы хэштегов и вкладки Shorts каналов YouTube: какие ролики есть и сколько у них просмотров. Страницы роликов: дата, длительность, канал, точные просмотры. RSS каналов: лайки. С ключом YouTube Data API: лайки, комментарии и закреплённые комментарии с названиями для всех роликов.'),
      h('li', null, 'Словарь из Wikidata: 133 тысячи фильмов, сериалов, мультфильмов, аниме и телешоу с ID Кинопоиска. Постеры с Кинопоиска.'),
      h('li', null, 'Нейросеть через OpenRouter: опознание по кадру, проверка исходников, объяснения, сцены.'),
      h('li', null, 'Исходники: поиск YouTube и Rutube. Интерес аудитории: просмотры статей Википедии и Google Trends по России.')),
    h('h2', null, 'Что значат цифры'),
    gl(['score', 'opportunity', 'median', 'vpd', 'growth', 'channels', 'fresh', 'outliers', 'small', 'interest', 'conf']),
    h('h2', null, 'Нейросеть и исходники'),
    gl(['source', 'rights', 'explain', 'scenes', 'brief', 'neuro', 'recognition']),
    h('h2', null, 'Видео под перезалив'),
    gl(['videos', 'clean', 'picks']),
    h('h2', null, 'Частые вопросы'),
    h('h3', null, 'Почему в карточках чужие ролики?'), h('p', null, 'Это референсы: доказательство, что тема заходит, и образец подачи. Резать нужно из исходника по ссылке, а не перезаливать чужое.'),
    h('h3', null, 'Что значит «проверено нейросетью» у исходника?'), h('p', null, 'Модель посмотрела заголовок, канал и длительность и подтвердила, что это полный фильм или серия нужного произведения, а не трейлер, пересказ или другой фильм. Версии с тифлокомментариями исключаются.'),
    h('h3', null, 'Почему тема в календаре, а не та, что я хотел?'), h('p', null, 'Календарь берёт лучшие по баллу возможности и чередует типы. Любой слот можно заменить вручную, и он зафиксируется.'),
    h('h3', null, 'Сколько это стоит?'), h('p', null, 'Все источники бесплатные. Нейросеть: около 0,3 $ на 1000 опознаний по кадру, доли цента за проверку исходников и объяснения. Дневной потолок задаётся в «Сборе».'),
  ));
}

// ---------- индикатор сбора в шапке ----------
async function pollRun() {
  try {
    const r = await api('/api/runs', { limit: 1 });
    const pill = $('#run-pill'); const c = r.current; const last = r.items[0];
    pill.className = 'pill ' + (c.running ? 'pill-run' : last ? (last.status === 'ok' ? 'pill-ok' : last.status === 'error' ? 'pill-bad' : 'pill-warn') : 'pill-quiet');
    pill.textContent = c.running ? `сбор: ${c.phase}${c.progress ? ' ' + c.progress : ''}` : last ? (last.status === 'running' ? 'сбор идёт из консоли' : `сбор ${ago(last.started_at)}`) : 'сбор не запускался';
    pill.onclick = () => go('#/collect'); pill.style.cursor = 'pointer';
    state.run = c;
  } catch (_) {}
  setTimeout(pollRun, state.run && state.run.running ? 5000 : 20000);
}

// ---------- рендер ----------
let renderSeq = 0;
async function render() {
  parseHash();
  const seq = ++renderSeq;
  $$('.nav a').forEach(a => a.setAttribute('aria-current', (a.dataset.route === state.route || (state.route === 'film' && a.dataset.route === 'films') || (state.route === 'channel' && a.dataset.route === 'channels') || (state.route === 'video' && a.dataset.route === 'videos')) ? 'page' : 'false'));
  const main = $('#main');
  const fresh = h('div');
  main.classList.add('loading');
  try {
    if (state.route === 'help') renderHelp(fresh);
    else if (state.route === 'cut') await renderCut(fresh);
    else if (state.route === 'videos') await renderVideos(fresh);
    else if (state.route === 'video' && state.param) await renderVideo(fresh, state.param);
    else if (state.route === 'films') await renderFilms(fresh);
    else if (state.route === 'film' && state.param) await renderFilm(fresh, state.param);
    else if (state.route === 'interests') await renderInterests(fresh);
    else if (state.route === 'channels') await renderChannels(fresh);
    else if (state.route === 'channel' && state.param) await renderChannel(fresh, state.param);
    else if (state.route === 'shorts') await renderShorts(fresh);
    else if (state.route === 'collect') await renderCollect(fresh);
    else await renderOverview(fresh);
  } catch (e) {
    fresh.append(panel('Не удалось загрузить', empty('Ошибка: ' + e.message, 'Сервис ещё поднимается или сбор занял базу. Попробуй обновить страницу.')));
  }
  if (seq !== renderSeq) return;
  main.innerHTML = '';
  main.append(...fresh.childNodes);
  main.classList.remove('loading');
  document.title = ({ help: 'Как пользоваться', overview: 'Обзор', cut: 'Что резать', videos: 'Видео', video: 'Ролик', films: 'Фильмы и сериалы', film: 'Произведение', interests: 'Интересы', channels: 'Каналы', channel: 'Канал', shorts: 'Ролики', collect: 'Сбор' }[state.route] || 'Кино-тренды') + ' · Кино-тренды Shorts';
}
addEventListener('hashchange', render);
renderFilters();
render();
pollRun();
try { if (!localStorage.getItem('kt.onboarded') && !/notour/.test(location.hash)) setTimeout(startTour, 600); } catch (_) {}
})();
