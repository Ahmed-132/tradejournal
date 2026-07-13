/* ============================================================
   TradeJournal — a live trading journal (vanilla JS, offline)
   Data lives in localStorage. No server, no account needed.
   ============================================================ */

(() => {
  'use strict';

  const STORE_KEY = 'tradejournal.v1';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // ---------- State ----------
  let state = load();

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* ignore */ }
    return { trades: [], settings: { currency: '$', account: 'Hoofdaccount' } };
  }
  function save() {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  }

  // ---------- Helpers ----------
  const uid = () => 't_' + Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-4);
  const cur = () => state.settings.currency || '$';

  function money(n, withSign = false) {
    const v = Number(n) || 0;
    const sign = v > 0 && withSign ? '+' : v < 0 ? '-' : '';
    const abs = Math.abs(v).toLocaleString('nl-NL', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${sign}${cur()}${abs}`;
  }
  function num(n, d = 2) {
    return (Number(n) || 0).toLocaleString('nl-NL', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString('nl-NL', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  function dayKey(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toISOString().slice(0, 10);
  }
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.add('hidden'), 2400);
  }
  function pnlClass(n) { return n > 0 ? 'pos' : n < 0 ? 'neg' : ''; }

  // Compute P&L from fields when not given directly
  function computePnl(t) {
    if (t.pnl !== '' && t.pnl != null && !isNaN(Number(t.pnl))) return Number(t.pnl);
    const entry = Number(t.entry), exit = Number(t.exit), qty = Number(t.qty), fees = Number(t.fees) || 0;
    if (!entry || !exit || !qty) return 0;
    const gross = t.side === 'short' ? (entry - exit) * qty : (exit - entry) * qty;
    return gross - fees;
  }

  // ---------- Filtering ----------
  function activeTrades() {
    let list = [...state.trades];
    const acc = $('#accountFilter').value;
    if (acc && acc !== '__all__') list = list.filter(t => (t.account || state.settings.account) === acc);

    const range = $('#rangeFilter').value;
    if (range !== 'all') {
      const now = new Date();
      let from;
      if (range === 'ytd') from = new Date(now.getFullYear(), 0, 1);
      else from = new Date(now.getTime() - Number(range) * 86400000);
      list = list.filter(t => new Date(t.date) >= from);
    }
    return list.sort((a, b) => new Date(a.date) - new Date(b.date));
  }

  // ---------- Stats ----------
  function computeStats(trades) {
    const n = trades.length;
    let net = 0, gp = 0, gl = 0, wins = 0, losses = 0, be = 0;
    let best = -Infinity, worst = Infinity;
    let winSum = 0, lossSum = 0;
    for (const t of trades) {
      const p = computePnl(t);
      net += p;
      if (p > 0) { wins++; gp += p; winSum += p; best = Math.max(best, p); }
      else if (p < 0) { losses++; gl += Math.abs(p); lossSum += p; worst = Math.min(worst, p); }
      else be++;
    }
    const winRate = n ? (wins / n) * 100 : 0;
    const profitFactor = gl > 0 ? gp / gl : (gp > 0 ? Infinity : 0);
    const avgWin = wins ? winSum / wins : 0;
    const avgLoss = losses ? lossSum / losses : 0;
    const expectancy = n ? net / n : 0;
    return {
      n, net, gp, gl, wins, losses, be, winRate, profitFactor,
      avgWin, avgLoss, expectancy,
      best: best === -Infinity ? 0 : best,
      worst: worst === Infinity ? 0 : worst,
    };
  }

  // ---------- Views ----------
  const VIEW_META = {
    dashboard: ['Dashboard', 'Overzicht van je handelsprestaties'],
    trades: ['Trades', 'Alle geregistreerde trades'],
    calendar: ['Kalender', 'Dagelijkse winst en verlies'],
    import: ['Importeren', 'Laad trades automatisch uit een CSV-bestand'],
  };

  function switchView(name) {
    $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    $$('.tab[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    $$('.view').forEach(v => v.classList.toggle('hidden', v.dataset.view !== name));
    $('#viewTitle').textContent = VIEW_META[name][0];
    $('#viewSubtitle').textContent = VIEW_META[name][1];
    render(name);
  }

  function render(name) {
    if (name === 'dashboard') renderDashboard();
    else if (name === 'trades') renderTrades();
    else if (name === 'calendar') renderCalendar();
    else if (name === 'import') renderImport();
  }
  function renderAll() {
    refreshAccountFilter();
    const active = $('.nav-item.active')?.dataset.view || 'dashboard';
    render(active);
  }

  // ---------- Dashboard ----------
  function renderDashboard() {
    const el = $('.view[data-view="dashboard"]');
    const trades = activeTrades();
    if (!trades.length) { el.innerHTML = emptyState('Nog geen trades', 'Voeg je eerste trade toe of importeer een CSV om je statistieken te zien.'); return; }
    const s = computeStats(trades);

    const pf = s.profitFactor === Infinity ? '∞' : num(s.profitFactor, 2);
    el.innerHTML = `
      <div class="grid stats-grid">
        ${statCard('Netto P&L', money(s.net, true), pnlClass(s.net), `${s.n} trades`)}
        ${statCard('Winratio', num(s.winRate, 1) + '%', '', `${s.wins}W / ${s.losses}L`)}
        ${statCard('Profit factor', pf, s.profitFactor >= 1 ? 'pos' : 'neg', 'bruto winst / verlies')}
        ${statCard('Verwachting/trade', money(s.expectancy, true), pnlClass(s.expectancy), 'gem. per trade')}
      </div>

      <div class="grid two-col">
        <div class="card">
          <p class="panel-title">Equity-curve</p>
          <p class="panel-sub">Cumulatieve netto P&L over tijd</p>
          <div class="chart-wrap"><canvas id="equityChart" height="260"></canvas></div>
        </div>
        <div class="card">
          <p class="panel-title">Win / Verlies</p>
          <p class="panel-sub">Verdeling van uitkomsten</p>
          <div class="winrate-ring">
            <div class="ring">
              <canvas id="donut" width="120" height="120"></canvas>
              <div class="ring-center"><b>${num(s.winRate, 0)}%</b><span class="muted" style="font-size:11px">winst</span></div>
            </div>
            <div class="ring-legend">
              <div><span class="dot" style="background:var(--green)"></span>Winst — ${s.wins}</div>
              <div><span class="dot" style="background:var(--red)"></span>Verlies — ${s.losses}</div>
              ${s.be ? `<div><span class="dot" style="background:var(--muted-2)"></span>Break-even — ${s.be}</div>` : ''}
            </div>
          </div>
          <div class="section-gap" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            ${miniStat('Gem. winst', money(s.avgWin, true), 'pos')}
            ${miniStat('Gem. verlies', money(s.avgLoss, true), 'neg')}
            ${miniStat('Beste trade', money(s.best, true), 'pos')}
            ${miniStat('Slechtste trade', money(s.worst, true), 'neg')}
          </div>
        </div>
      </div>

      <div class="card section-gap">
        <div class="flex between items-center" style="margin-bottom:12px">
          <div><p class="panel-title" style="margin:0">Recente trades</p></div>
          <button class="btn btn-sm btn-ghost" data-goto="trades">Alle bekijken →</button>
        </div>
        ${tradesTable(trades.slice(-8).reverse(), false)}
      </div>
    `;

    requestAnimationFrame(() => {
      drawEquity($('#equityChart'), trades);
      drawDonut($('#donut'), s);
    });
    $('[data-goto="trades"]', el)?.addEventListener('click', () => switchView('trades'));
  }

  function statCard(label, value, cls, sub) {
    return `<div class="card stat">
      <div class="stat-label">${label}</div>
      <div class="stat-value ${cls}">${value}</div>
      <div class="stat-sub">${sub}</div>
    </div>`;
  }
  function miniStat(label, value, cls) {
    return `<div><div class="stat-label" style="font-size:11px">${label}</div>
      <div class="${cls}" style="font-family:var(--mono);font-weight:700;font-size:16px;margin-top:3px">${value}</div></div>`;
  }
  function emptyState(title, sub) {
    return `<div class="empty"><div class="big">◔</div><h3>${title}</h3><p>${sub}</p>
      <div class="chips" style="justify-content:center;margin-top:16px">
        <button class="btn btn-primary" onclick="document.getElementById('addTradeBtn').click()">+ Trade toevoegen</button>
        <button class="btn" data-goto-import>CSV importeren</button>
      </div></div>`;
  }

  // ---------- Charts (canvas, no libs) ----------
  function drawEquity(canvas, trades) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = 260;
    canvas.width = W * dpr; canvas.height = H * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    // build cumulative series
    let cum = 0;
    const pts = [{ x: 0, v: 0 }];
    trades.forEach((t, i) => { cum += computePnl(t); pts.push({ x: i + 1, v: cum }); });
    const vals = pts.map(p => p.v);
    let min = Math.min(...vals, 0), max = Math.max(...vals, 0);
    if (min === max) { min -= 1; max += 1; }
    const pad = { l: 56, r: 12, t: 14, b: 24 };
    const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
    const X = i => pad.l + (i / (pts.length - 1)) * plotW;
    const Y = v => pad.t + (1 - (v - min) / (max - min)) * plotH;

    // grid + y labels
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.fillStyle = '#8b93a7';
    ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const v = min + (i / steps) * (max - min);
      const y = Y(v);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillText(cur() + Math.round(v).toLocaleString('nl-NL'), pad.l - 8, y);
    }

    // zero line
    if (min < 0 && max > 0) {
      ctx.strokeStyle = 'rgba(255,255,255,0.18)';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, Y(0)); ctx.lineTo(W - pad.r, Y(0)); ctx.stroke();
      ctx.setLineDash([]);
    }

    // area fill
    const last = pts[pts.length - 1].v;
    const color = last >= 0 ? '34,197,94' : '239,68,68';
    const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + plotH);
    grad.addColorStop(0, `rgba(${color},0.28)`);
    grad.addColorStop(1, `rgba(${color},0.01)`);
    ctx.beginPath();
    pts.forEach((p, i) => { const x = X(i), y = Y(p.v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.lineTo(X(pts.length - 1), Y(min)); ctx.lineTo(X(0), Y(min)); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();

    // line
    ctx.beginPath();
    pts.forEach((p, i) => { const x = X(i), y = Y(p.v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = `rgb(${color})`; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    ctx.stroke();

    // last dot
    ctx.beginPath();
    ctx.arc(X(pts.length - 1), Y(last), 3.5, 0, Math.PI * 2);
    ctx.fillStyle = `rgb(${color})`; ctx.fill();
  }

  function drawDonut(canvas, s) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = 120 * dpr; canvas.height = 120 * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    const cx = 60, cy = 60, r = 48, lw = 14;
    const total = s.wins + s.losses + s.be || 1;
    const segs = [
      { v: s.wins, c: '#22c55e' },
      { v: s.losses, c: '#ef4444' },
      { v: s.be, c: '#5c6579' },
    ];
    let a = -Math.PI / 2;
    ctx.lineWidth = lw;
    segs.forEach(seg => {
      if (!seg.v) return;
      const ang = (seg.v / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a, a + ang);
      ctx.strokeStyle = seg.c; ctx.stroke();
      a += ang;
    });
  }

  // ---------- Trades table ----------
  function tradesTable(trades, withActions = true) {
    if (!trades.length) return `<p class="muted" style="padding:20px;text-align:center">Geen trades in deze selectie.</p>`;
    const rows = trades.map(t => {
      const p = computePnl(t);
      const tags = (t.tags || []).map(x => `<span class="tag">${escapeHtml(x)}</span>`).join('');
      return `<tr>
        <td>${fmtDate(t.date)}</td>
        <td><b>${escapeHtml(t.symbol || '—')}</b></td>
        <td><span class="pill pill-${t.side === 'short' ? 'short' : 'long'}">${t.side === 'short' ? 'Short' : 'Long'}</span></td>
        <td class="mono">${t.entry ? num(t.entry) : '—'}</td>
        <td class="mono">${t.exit ? num(t.exit) : '—'}</td>
        <td class="mono">${t.qty ? num(t.qty, 0) : '—'}</td>
        <td class="mono ${pnlClass(p)}"><b>${money(p, true)}</b></td>
        <td>${tags || '<span class="muted">—</span>'}</td>
        ${withActions ? `<td><div class="row-actions">
            <button class="icon-btn" data-edit="${t.id}" title="Bewerken">✎</button>
            <button class="icon-btn" data-del="${t.id}" title="Verwijderen">🗑</button>
          </div></td>` : ''}
      </tr>`;
    }).join('');
    return `<div class="table-wrap"><table>
      <thead><tr>
        <th>Datum</th><th>Symbool</th><th>Richting</th><th>Entry</th><th>Exit</th>
        <th>Aantal</th><th>P&L</th><th>Tags</th>${withActions ? '<th></th>' : ''}
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function renderTrades() {
    const el = $('.view[data-view="trades"]');
    const trades = activeTrades().reverse();
    if (!trades.length) { el.innerHTML = emptyState('Nog geen trades', 'Voeg handmatig een trade toe of importeer een CSV.'); bindEmpty(el); return; }
    el.innerHTML = `
      <div class="flex between items-center" style="margin-bottom:16px">
        <p class="muted" style="margin:0">${trades.length} trade(s) in selectie</p>
        <div class="chips">
          <button class="btn btn-sm" id="exportCsv">Exporteer CSV</button>
          <button class="btn btn-sm btn-primary" onclick="document.getElementById('addTradeBtn').click()">+ Trade</button>
        </div>
      </div>
      ${tradesTable(trades, true)}
    `;
    $$('[data-edit]', el).forEach(b => b.addEventListener('click', () => openTradeModal(b.dataset.edit)));
    $$('[data-del]', el).forEach(b => b.addEventListener('click', () => deleteTrade(b.dataset.del)));
    $('#exportCsv', el)?.addEventListener('click', exportCsv);
  }

  function bindEmpty(el) {
    $('[data-goto-import]', el)?.addEventListener('click', () => switchView('import'));
  }

  function deleteTrade(id) {
    const t = state.trades.find(x => x.id === id);
    if (!t) return;
    if (!confirm(`Trade ${t.symbol || ''} van ${fmtDate(t.date)} verwijderen?`)) return;
    state.trades = state.trades.filter(x => x.id !== id);
    save(); renderAll(); toast('Trade verwijderd');
  }

  // ---------- Calendar ----------
  let calMonth = new Date();
  function renderCalendar() {
    const el = $('.view[data-view="calendar"]');
    const trades = activeTrades();
    const byDay = {};
    trades.forEach(t => {
      const k = dayKey(t.date);
      if (!byDay[k]) byDay[k] = { pnl: 0, n: 0 };
      byDay[k].pnl += computePnl(t); byDay[k].n++;
    });

    const y = calMonth.getFullYear(), m = calMonth.getMonth();
    const monthName = calMonth.toLocaleDateString('nl-NL', { month: 'long', year: 'numeric' });
    const first = new Date(y, m, 1);
    const startDow = (first.getDay() + 6) % 7; // Monday-first
    const daysInMonth = new Date(y, m + 1, 0).getDate();

    let monthPnl = 0, monthTrades = 0;
    for (let d = 1; d <= daysInMonth; d++) {
      const k = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      if (byDay[k]) { monthPnl += byDay[k].pnl; monthTrades += byDay[k].n; }
    }

    const dow = ['Ma', 'Di', 'Wo', 'Do', 'Vr', 'Za', 'Zo'];
    let cells = dow.map(d => `<div class="cal-dow">${d}</div>`).join('');
    for (let i = 0; i < startDow; i++) cells += `<div class="cal-cell empty"></div>`;
    for (let d = 1; d <= daysInMonth; d++) {
      const k = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const data = byDay[k];
      let cls = '', inner = `<div class="day-num">${d}</div>`;
      if (data) {
        cls = data.pnl > 0 ? 'win' : data.pnl < 0 ? 'loss' : '';
        inner = `<div class="day-num">${d}</div>
          <div class="day-pnl ${pnlClass(data.pnl)}">${money(data.pnl, true)}</div>
          <div class="day-trades">${data.n} trade${data.n > 1 ? 's' : ''}</div>`;
      }
      cells += `<div class="cal-cell ${cls}">${inner}</div>`;
    }

    el.innerHTML = `
      <div class="card">
        <div class="cal-head">
          <div class="cal-nav">
            <button class="btn btn-sm" id="prevMonth">←</button>
            <b style="font-size:16px;text-transform:capitalize;min-width:150px;text-align:center;display:inline-block">${monthName}</b>
            <button class="btn btn-sm" id="nextMonth">→</button>
            <button class="btn btn-sm btn-ghost" id="todayBtn">Vandaag</button>
          </div>
          <div class="flex gap items-center">
            <span class="muted" style="font-size:13px">Maand:</span>
            <b class="${pnlClass(monthPnl)}" style="font-family:var(--mono);font-size:16px">${money(monthPnl, true)}</b>
            <span class="muted" style="font-size:13px">· ${monthTrades} trades</span>
          </div>
        </div>
        <div class="cal-grid">${cells}</div>
      </div>`;

    $('#prevMonth', el).addEventListener('click', () => { calMonth = new Date(y, m - 1, 1); renderCalendar(); });
    $('#nextMonth', el).addEventListener('click', () => { calMonth = new Date(y, m + 1, 1); renderCalendar(); });
    $('#todayBtn', el).addEventListener('click', () => { calMonth = new Date(); renderCalendar(); });
  }

  // ---------- Import (CSV) ----------
  const FIELD_ALIASES = {
    date: ['date', 'datum', 'time', 'opened', 'open time', 'closetime', 'close time', 'exit time', 'closed', 'trade date', 'datetime'],
    symbol: ['symbol', 'ticker', 'instrument', 'market', 'pair', 'symbool', 'asset', 'contract'],
    side: ['side', 'direction', 'type', 'action', 'richting', 'buy/sell', 'position', 'l/s'],
    entry: ['entry', 'entry price', 'open', 'open price', 'buy price', 'avg entry', 'price in', 'entryprice'],
    exit: ['exit', 'exit price', 'close', 'close price', 'sell price', 'avg exit', 'price out', 'exitprice'],
    qty: ['qty', 'quantity', 'size', 'volume', 'shares', 'units', 'contracts', 'amount', 'aantal', 'lots'],
    fees: ['fee', 'fees', 'commission', 'commissions', 'kosten', 'cost'],
    pnl: ['pnl', 'p&l', 'p/l', 'profit', 'net', 'net pnl', 'realized', 'realized pnl', 'gain', 'result', 'winst', 'return', 'net profit'],
  };
  let importPreview = null;

  function renderImport() {
    const el = $('.view[data-view="import"]');
    el.innerHTML = `
      <div class="card">
        <p class="panel-title">Automatisch importeren uit CSV</p>
        <p class="panel-sub">Exporteer je trades bij je broker (bv. MT4/MT5, Interactive Brokers, Binance, Bybit, Webull, Trading212) en sleep het CSV-bestand hierheen. De kolommen worden automatisch herkend.</p>
        <div class="dropzone" id="dropzone">
          <div class="big">⇪</div>
          <div><b>Sleep een CSV-bestand hierheen</b> of klik om te bladeren</div>
          <div class="muted" style="margin-top:6px;font-size:12.5px">Kolommen zoals datum, symbool, richting, prijs en P&L worden automatisch gemapt</div>
        </div>
        <input type="file" id="fileInput" accept=".csv,text/csv" style="display:none" />
        <div id="importArea"></div>
      </div>
      <div class="card section-gap">
        <p class="panel-title">Geen CSV bij de hand?</p>
        <p class="panel-sub">Laad voorbeelddata om de app uit te proberen, of voeg handmatig een trade toe.</p>
        <div class="chips">
          <button class="btn" id="loadSample">Voorbeelddata laden</button>
          <button class="btn btn-danger" id="clearAll">Alle data wissen</button>
        </div>
      </div>`;

    const dz = $('#dropzone', el), fi = $('#fileInput', el);
    dz.addEventListener('click', () => fi.click());
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
    dz.addEventListener('drop', e => {
      e.preventDefault(); dz.classList.remove('drag');
      if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
    });
    fi.addEventListener('change', () => fi.files[0] && handleFile(fi.files[0]));
    $('#loadSample', el).addEventListener('click', loadSample);
    $('#clearAll', el).addEventListener('click', () => {
      if (confirm('Alle trades en instellingen wissen? Dit kan niet ongedaan worden gemaakt.')) {
        state = { trades: [], settings: { currency: '$', account: 'Hoofdaccount' } };
        save(); renderAll(); toast('Alle data gewist');
      }
    });
  }

  function handleFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = parseCsv(reader.result);
        if (!parsed.rows.length) { toast('Geen rijen gevonden in CSV'); return; }
        importPreview = buildMapping(parsed);
        renderImportPreview();
      } catch (e) {
        toast('Kon CSV niet lezen: ' + e.message);
      }
    };
    reader.readAsText(file);
  }

  // Minimal CSV parser handling quotes, commas, and ; delimiter
  function parseCsv(text) {
    text = text.replace(/^﻿/, '');
    const firstLine = text.slice(0, text.indexOf('\n') > -1 ? text.indexOf('\n') : text.length);
    const delim = (firstLine.split(';').length > firstLine.split(',').length) ? ';' : ',';
    const rows = [];
    let field = '', row = [], inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i], next = text[i + 1];
      if (inQuotes) {
        if (c === '"' && next === '"') { field += '"'; i++; }
        else if (c === '"') inQuotes = false;
        else field += c;
      } else {
        if (c === '"') inQuotes = true;
        else if (c === delim) { row.push(field); field = ''; }
        else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
        else if (c === '\r') { /* skip */ }
        else field += c;
      }
    }
    if (field.length || row.length) { row.push(field); rows.push(row); }
    const header = rows.shift().map(h => h.trim());
    const dataRows = rows.filter(r => r.some(c => c.trim() !== ''));
    return { header, rows: dataRows };
  }

  function buildMapping(parsed) {
    const { header, rows } = parsed;
    const lower = header.map(h => h.toLowerCase().trim());
    const map = {};
    for (const [field, aliases] of Object.entries(FIELD_ALIASES)) {
      let idx = -1;
      for (const a of aliases) {
        idx = lower.findIndex(h => h === a);
        if (idx > -1) break;
      }
      if (idx === -1) {
        for (const a of aliases) {
          idx = lower.findIndex(h => h.includes(a));
          if (idx > -1) break;
        }
      }
      map[field] = idx;
    }
    return { header, rows, map };
  }

  function renderImportPreview() {
    const { header, rows, map } = importPreview;
    const area = $('#importArea');
    const fields = Object.keys(FIELD_ALIASES);
    const labels = { date: 'Datum', symbol: 'Symbool', side: 'Richting', entry: 'Entry-prijs', exit: 'Exit-prijs', qty: 'Aantal', fees: 'Kosten', pnl: 'P&L (netto)' };

    const options = ['<option value="-1">— niet gebruiken —</option>', ...header.map((h, i) => `<option value="${i}">${escapeHtml(h)}</option>`)].join('');
    const mapUi = fields.map(f => `
      <label class="field">${labels[f]}${f === 'pnl' || f === 'date' || f === 'symbol' ? ' *' : ''}
        <select class="input map-select" data-field="${f}">
          ${options.replace(`value="${map[f]}"`, `value="${map[f]}" selected`)}
        </select>
      </label>`).join('');

    area.innerHTML = `
      <div class="section-gap">
        <div class="flex between items-center" style="margin-bottom:8px">
          <b>Kolommen koppelen</b>
          <span class="muted" style="font-size:12.5px">${rows.length} rij(en) gevonden</span>
        </div>
        <p class="muted" style="font-size:12.5px;margin:0 0 4px">De kolommen zijn automatisch herkend. Controleer of het klopt en pas indien nodig aan. Velden met * zijn aanbevolen.</p>
        <div class="map-grid">${mapUi}</div>
        <div id="previewTable"></div>
        <div class="flex gap" style="margin-top:16px">
          <button class="btn btn-primary" id="doImport">Importeer ${rows.length} trades</button>
          <button class="btn btn-ghost" id="cancelImport">Annuleren</button>
        </div>
      </div>`;

    $$('.map-select', area).forEach(s => s.addEventListener('change', () => {
      importPreview.map[s.dataset.field] = Number(s.value);
      drawPreviewTable();
    }));
    $('#doImport', area).addEventListener('click', doImport);
    $('#cancelImport', area).addEventListener('click', () => { importPreview = null; area.innerHTML = ''; });
    drawPreviewTable();
  }

  function drawPreviewTable() {
    const { rows, map } = importPreview;
    const sample = rows.slice(0, 5).map(r => {
      const t = rowToTrade(r, map);
      const p = computePnl(t);
      return `<tr>
        <td>${fmtDate(t.date) || '<span class="neg">?</span>'}</td>
        <td><b>${escapeHtml(t.symbol || '—')}</b></td>
        <td>${t.side === 'short' ? 'Short' : 'Long'}</td>
        <td class="mono">${t.entry ? num(t.entry) : '—'}</td>
        <td class="mono">${t.exit ? num(t.exit) : '—'}</td>
        <td class="mono ${pnlClass(p)}">${money(p, true)}</td>
      </tr>`;
    }).join('');
    $('#previewTable').innerHTML = `
      <div class="muted" style="font-size:12px;margin:14px 0 6px">Voorbeeld (eerste 5 rijen):</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Datum</th><th>Symbool</th><th>Richting</th><th>Entry</th><th>Exit</th><th>P&L</th></tr></thead>
        <tbody>${sample}</tbody></table></div>`;
  }

  function parseNumber(v) {
    if (v == null) return '';
    let s = String(v).trim().replace(/[^\d.,\-]/g, '');
    if (s === '' || s === '-') return '';
    // handle both 1.234,56 and 1,234.56
    if (s.includes(',') && s.includes('.')) {
      if (s.lastIndexOf(',') > s.lastIndexOf('.')) s = s.replace(/\./g, '').replace(',', '.');
      else s = s.replace(/,/g, '');
    } else if (s.includes(',')) {
      s = s.replace(',', '.');
    }
    const n = parseFloat(s);
    return isNaN(n) ? '' : n;
  }

  function parseSide(v) {
    const s = String(v || '').toLowerCase();
    if (/short|sell|verkoop|s\b|-1|bear/.test(s)) return 'short';
    return 'long';
  }

  function parseDate(v) {
    if (!v) return '';
    const s = String(v).trim();
    let d = new Date(s);
    if (!isNaN(d)) return d.toISOString();
    // try dd/mm/yyyy or dd-mm-yyyy
    const m = s.match(/(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/);
    if (m) {
      let [_, a, b, y] = m;
      if (y.length === 2) y = '20' + y;
      d = new Date(Number(y), Number(b) - 1, Number(a));
      if (!isNaN(d)) return d.toISOString();
    }
    return s;
  }

  function rowToTrade(r, map) {
    const g = i => (i > -1 && i < r.length ? r[i] : '');
    return {
      date: parseDate(g(map.date)),
      symbol: String(g(map.symbol) || '').trim().toUpperCase(),
      side: parseSide(g(map.side)),
      entry: parseNumber(g(map.entry)),
      exit: parseNumber(g(map.exit)),
      qty: parseNumber(g(map.qty)),
      fees: parseNumber(g(map.fees)),
      pnl: map.pnl > -1 ? parseNumber(g(map.pnl)) : '',
      tags: ['imported'],
    };
  }

  function doImport() {
    const { rows, map } = importPreview;
    let added = 0;
    for (const r of rows) {
      const t = rowToTrade(r, map);
      if (!t.symbol && !t.pnl && !t.entry) continue;
      if (!t.date) t.date = new Date().toISOString();
      t.id = uid();
      t.account = state.settings.account;
      state.trades.push(t);
      added++;
    }
    save();
    importPreview = null;
    toast(`${added} trades geïmporteerd`);
    switchView('dashboard');
  }

  function exportCsv() {
    const trades = activeTrades();
    const head = ['date', 'symbol', 'side', 'entry', 'exit', 'qty', 'fees', 'pnl', 'tags'];
    const lines = [head.join(',')];
    trades.forEach(t => {
      const row = [t.date, t.symbol, t.side, t.entry, t.exit, t.qty, t.fees, computePnl(t), (t.tags || []).join('|')];
      lines.push(row.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `trades-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    toast('CSV geëxporteerd');
  }

  // ---------- Trade modal ----------
  function openTradeModal(editId) {
    const t = editId ? state.trades.find(x => x.id === editId) : null;
    const v = f => (t && t[f] != null ? t[f] : '');
    const dt = t ? new Date(t.date) : new Date();
    const dateVal = isNaN(dt) ? '' : dt.toISOString().slice(0, 16);

    $('#modalRoot').innerHTML = `
      <div class="modal-overlay" id="ovl">
        <div class="modal">
          <div class="modal-head">
            <h2>${t ? 'Trade bewerken' : 'Nieuwe trade'}</h2>
            <button class="icon-btn" id="closeModal" style="font-size:20px">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-grid">
              <label class="field full">Symbool
                <input class="input" id="f_symbol" placeholder="bv. AAPL, EURUSD, BTC" value="${escapeAttr(v('symbol'))}" />
              </label>
              <label class="field">Datum & tijd
                <input class="input" id="f_date" type="datetime-local" value="${dateVal}" />
              </label>
              <label class="field">Richting
                <select class="input" id="f_side">
                  <option value="long" ${v('side') !== 'short' ? 'selected' : ''}>Long (kopen)</option>
                  <option value="short" ${v('side') === 'short' ? 'selected' : ''}>Short (verkopen)</option>
                </select>
              </label>
              <label class="field">Entry-prijs
                <input class="input" id="f_entry" type="number" step="any" placeholder="0.00" value="${v('entry')}" />
              </label>
              <label class="field">Exit-prijs
                <input class="input" id="f_exit" type="number" step="any" placeholder="0.00" value="${v('exit')}" />
              </label>
              <label class="field">Aantal
                <input class="input" id="f_qty" type="number" step="any" placeholder="0" value="${v('qty')}" />
              </label>
              <label class="field">Kosten / commissie
                <input class="input" id="f_fees" type="number" step="any" placeholder="0.00" value="${v('fees')}" />
              </label>
              <label class="field full">P&L (netto) — leeg = automatisch berekenen
                <input class="input" id="f_pnl" type="number" step="any" placeholder="Automatisch uit entry/exit/aantal" value="${v('pnl')}" />
              </label>
              <label class="field full">Tags (komma-gescheiden)
                <input class="input" id="f_tags" placeholder="bv. breakout, scalp, FOMO" value="${escapeAttr((t?.tags || []).join(', '))}" />
              </label>
              <label class="field full">Notities
                <textarea class="input" id="f_notes" placeholder="Wat ging goed of fout?">${escapeHtml(v('notes'))}</textarea>
              </label>
            </div>
            <div id="pnlPreview" class="muted" style="margin-top:14px;font-size:13px"></div>
          </div>
          <div class="modal-foot">
            ${t ? '<button class="btn btn-danger" id="delFromModal">Verwijderen</button>' : ''}
            <button class="btn btn-ghost" id="cancelModal">Annuleren</button>
            <button class="btn btn-primary" id="saveTrade">${t ? 'Opslaan' : 'Toevoegen'}</button>
          </div>
        </div>
      </div>`;

    const close = () => { $('#modalRoot').innerHTML = ''; };
    $('#closeModal').addEventListener('click', close);
    $('#cancelModal').addEventListener('click', close);
    $('#ovl').addEventListener('click', e => { if (e.target.id === 'ovl') close(); });
    if (t) $('#delFromModal').addEventListener('click', () => { close(); deleteTrade(editId); });

    const updatePreview = () => {
      const draft = readModal();
      const p = computePnl(draft);
      const explicit = $('#f_pnl').value.trim() !== '';
      $('#pnlPreview').innerHTML = `Berekende P&L: <b class="${pnlClass(p)}" style="font-family:var(--mono)">${money(p, true)}</b>${explicit ? ' (handmatig ingevoerd)' : ''}`;
    };
    ['f_entry', 'f_exit', 'f_qty', 'f_fees', 'f_pnl', 'f_side'].forEach(id => $('#' + id).addEventListener('input', updatePreview));
    updatePreview();

    $('#saveTrade').addEventListener('click', () => {
      const draft = readModal();
      if (!draft.symbol) { toast('Vul een symbool in'); return; }
      if (t) {
        Object.assign(t, draft);
      } else {
        draft.id = uid();
        draft.account = state.settings.account;
        state.trades.push(draft);
      }
      save(); close(); renderAll();
      toast(t ? 'Trade bijgewerkt' : 'Trade toegevoegd');
    });

    setTimeout(() => $('#f_symbol').focus(), 50);
  }

  function readModal() {
    const val = id => $('#' + id).value.trim();
    const dateRaw = val('f_date');
    return {
      date: dateRaw ? new Date(dateRaw).toISOString() : new Date().toISOString(),
      symbol: val('f_symbol').toUpperCase(),
      side: val('f_side'),
      entry: val('f_entry') === '' ? '' : Number(val('f_entry')),
      exit: val('f_exit') === '' ? '' : Number(val('f_exit')),
      qty: val('f_qty') === '' ? '' : Number(val('f_qty')),
      fees: val('f_fees') === '' ? '' : Number(val('f_fees')),
      pnl: val('f_pnl') === '' ? '' : Number(val('f_pnl')),
      notes: val('f_notes'),
      tags: val('f_tags') ? val('f_tags').split(',').map(s => s.trim()).filter(Boolean) : [],
    };
  }

  // ---------- Settings modal ----------
  function openSettings() {
    const s = state.settings;
    $('#modalRoot').innerHTML = `
      <div class="modal-overlay" id="ovl">
        <div class="modal" style="max-width:440px">
          <div class="modal-head"><h2>Instellingen</h2><button class="icon-btn" id="closeS" style="font-size:20px">✕</button></div>
          <div class="modal-body">
            <div class="form-grid">
              <label class="field">Valutasymbool
                <input class="input" id="s_cur" value="${escapeAttr(s.currency)}" />
              </label>
              <label class="field">Account-naam
                <input class="input" id="s_acc" value="${escapeAttr(s.account)}" />
              </label>
            </div>
            <p class="muted" style="font-size:12.5px;margin-top:16px">Data wordt lokaal in je browser opgeslagen (${state.trades.length} trades). Gebruik "Exporteer CSV" op de Trades-pagina voor een back-up.</p>
          </div>
          <div class="modal-foot">
            <button class="btn btn-ghost" id="cancelS">Annuleren</button>
            <button class="btn btn-primary" id="saveS">Opslaan</button>
          </div>
        </div>
      </div>`;
    const close = () => { $('#modalRoot').innerHTML = ''; };
    $('#closeS').addEventListener('click', close);
    $('#cancelS').addEventListener('click', close);
    $('#ovl').addEventListener('click', e => { if (e.target.id === 'ovl') close(); });
    $('#saveS').addEventListener('click', () => {
      state.settings.currency = $('#s_cur').value.trim() || '$';
      state.settings.account = $('#s_acc').value.trim() || 'Hoofdaccount';
      save(); close(); renderAll(); toast('Instellingen opgeslagen');
    });
  }

  function refreshAccountFilter() {
    const accs = [...new Set(state.trades.map(t => t.account || state.settings.account))];
    const sel = $('#accountFilter');
    const prev = sel.value;
    if (accs.length <= 1) { sel.classList.add('hidden'); }
    else {
      sel.classList.remove('hidden');
      sel.innerHTML = `<option value="__all__">Alle accounts</option>` + accs.map(a => `<option value="${escapeAttr(a)}">${escapeHtml(a)}</option>`).join('');
      if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
    }
  }

  // ---------- Sample data ----------
  function loadSample() {
    const syms = ['AAPL', 'TSLA', 'NVDA', 'EURUSD', 'BTCUSD', 'MSFT', 'AMD', 'SPY', 'ETHUSD', 'META'];
    const tags = [['breakout'], ['scalp'], ['swing'], ['news'], ['reversal', 'A+'], ['FOMO'], ['trend']];
    const out = [];
    const today = new Date();
    // deterministic-ish pseudo random
    let seed = 42;
    const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
    for (let i = 0; i < 60; i++) {
      const daysAgo = Math.floor(rnd() * 75);
      const d = new Date(today.getTime() - daysAgo * 86400000);
      d.setHours(9 + Math.floor(rnd() * 7), Math.floor(rnd() * 60));
      const sym = syms[Math.floor(rnd() * syms.length)];
      const side = rnd() > 0.42 ? 'long' : 'short';
      const entry = +(20 + rnd() * 480).toFixed(2);
      const move = (rnd() - 0.44) * entry * 0.05;
      const exit = +(entry + (side === 'long' ? move : -move)).toFixed(2);
      const qty = Math.ceil(rnd() * 200);
      const fees = +(rnd() * 6).toFixed(2);
      out.push({
        id: uid(), date: d.toISOString(), symbol: sym, side,
        entry, exit, qty, fees, pnl: '',
        tags: tags[Math.floor(rnd() * tags.length)],
        account: state.settings.account, notes: '',
      });
    }
    state.trades = state.trades.concat(out);
    save(); renderAll(); switchView('dashboard');
    toast('60 voorbeeldtrades geladen');
  }

  // ---------- Escaping ----------
  function escapeHtml(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function escapeAttr(s) { return escapeHtml(s).replace(/`/g, '&#96;'); }

  // ---------- Wire up ----------
  $$('.nav-item').forEach(b => b.addEventListener('click', () => switchView(b.dataset.view)));
  $$('.tab[data-view]').forEach(b => b.addEventListener('click', () => switchView(b.dataset.view)));
  $('#addTradeTab')?.addEventListener('click', () => openTradeModal(null));
  $('#addTradeBtn').addEventListener('click', () => openTradeModal(null));
  $('#settingsBtn').addEventListener('click', openSettings);
  $('#rangeFilter').addEventListener('change', renderAll);
  $('#accountFilter').addEventListener('change', renderAll);
  window.addEventListener('resize', () => { const v = $('.nav-item.active')?.dataset.view; if (v === 'dashboard') renderDashboard(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modalRoot').innerHTML = ''; });

  // First run
  refreshAccountFilter();
  switchView('dashboard');

  // Register service worker for installable app (offline)
  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
})();
