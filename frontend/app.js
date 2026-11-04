/* Rubik's Cube Race: browser side.
 * Talks to the FastAPI backend (no API key here, the key lives on the server only).
 */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var state = {
    cfg: null, backend: 'simulated', usage: {}, lanes: {}, runId: null, es: null, running: false, view: 'iso',
    stats: null, meta: null, puzzle: null, timer: null,
  };

  // ------------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtTime(s) {
    if (s == null) return '0:00.00';
    var m = Math.floor(s / 60), r = s - m * 60;
    return m + ':' + (r < 10 ? '0' : '') + r.toFixed(2);
  }
  function fmtMs(ms) {
    if (ms == null) return '–';
    return ms >= 1000 ? (ms / 1000).toFixed(2) + ' s' : Math.round(ms) + ' ms';
  }
  function fmtSecs(s) {
    if (s == null) return '–';
    if (s >= 60) return Math.floor(s / 60) + ' min ' + Math.round(s % 60) + ' s';
    return s.toFixed(1) + ' s';
  }
  function num(x, d) { return x == null ? '–' : Number(x).toFixed(d == null ? 1 : d); }
  function roster() { return state.cfg.rosters[state.backend] || []; }
  function limitFor(m) {
    var s = state.cfg.settings;
    if (state.backend === 'ollama') return s.move_limit;
    return Math.min(s.move_limit, m.category === 'llm' ? s.max_claude_requests : s.max_requests_per_decision_model);
  }
  function ordinal(n) { return ['1st', '2nd', '3rd', '4th', '5th', '6th'][n - 1] || (n + 'th'); }

  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function post(path, body) {
    return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }

  var toastTimer = null;
  function toast(msg, isError) {
    var el = $('#toast');
    el.textContent = msg;
    el.className = 'toast' + (isError ? ' error' : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, isError ? 7000 : 3500);
  }

  // ------------------------------------------------------------------ student identity bar
  function getStudent() {
    try {
      var saved = JSON.parse(localStorage.getItem('rcr.student') || 'null');
      if (saved && saved.name && saved.roll) return saved;
    } catch (e) { /* ignore */ }
    var s = state.cfg ? state.cfg.student : {};
    return { name: s.name || '', roll: s.roll_no || '' };
  }
  function renderStudent() {
    var s = getStudent();
    $('#studentName').textContent = s.name || '';
    $('#studentRoll').textContent = s.roll || '';
    $('#idbar').classList.toggle('missing', !(s.name && s.roll));
    syncBarHeight();
  }
  function syncBarHeight() {
    document.documentElement.style.setProperty('--idbar-h', $('#idbar').offsetHeight + 'px');
  }
  function initStudentDialog() {
    var dlg = $('#studentDlg');
    $('#editStudent').addEventListener('click', function () {
      var s = getStudent();
      $('#inName').value = s.name; $('#inRoll').value = s.roll;
      dlg.showModal();
    });
    $('#dlgCancel').addEventListener('click', function () { dlg.close(); });
    $('#studentForm').addEventListener('submit', function () {
      try {
        localStorage.setItem('rcr.student', JSON.stringify({ name: $('#inName').value.trim(), roll: $('#inRoll').value.trim() }));
      } catch (e) { /* ignore */ }
      renderStudent();
    });
    window.addEventListener('resize', syncBarHeight);
  }

  // ------------------------------------------------------------------ lanes
  function buildLanes() {
    var wrap = $('#lanes');
    wrap.innerHTML = '';
    state.lanes = {};
    roster().forEach(function (m) {
      var el = document.createElement('article');
      el.className = 'lane';
      el.style.setProperty('--lane', m.color);
      el.dataset.key = m.key;
      var isLlm = m.category === 'llm';
      el.innerHTML =
        '<div class="lane-head"><h3>' + esc(m.name) + '</h3>' +
        '<span class="tag ' + (isLlm ? 'llm' : 'decision') + '">' + (isLlm ? 'LLM' : 'Decision model') + '</span>' +
        (state.backend === 'simulated' ? '<span class="tag sim" title="Practice mode: this lane is a simulation, not the real model">Simulated</span>' : '') +
        (state.backend === 'ollama' ? '<span class="tag local" title="Runs on your computer through Ollama">Local</span>' : '') +
        '<span class="rank" aria-label="Rank"></span></div>' +
        '<div class="lane-body"><div class="cube"></div>' +
        '<div class="readout"><div class="timer">0:00.00</div>' +
        '<div class="count"><b class="moves">0</b> / <span class="limit">' + limitFor(m) + '</span> moves</div>' +
        '<div class="status">ready</div></div></div>' +
        '<div class="lane-api"><span class="api-calls">API calls 0</span><span class="api-cache">cache hits 0</span></div>' +
        '<div class="now"><span class="now-label">Current move</span><span class="now-move">–</span><span class="now-meta"></span></div>' +
        '<ol class="history" aria-label="Move history"></ol>' +
        '<div class="result"></div>';
      wrap.appendChild(el);
      state.lanes[m.key] = {
        key: m.key, model: m, el: el, facelets: null, running: false, base: 0, syncAt: 0, moves: 0,
        limitEl: el.querySelector('.limit'), apiEl: el.querySelector('.api-calls'), cacheEl: el.querySelector('.api-cache'),
        calls: 0, hits: 0,
        timerEl: el.querySelector('.timer'), movesEl: el.querySelector('.moves'), statusEl: el.querySelector('.status'),
        nowEl: el.querySelector('.now-move'), metaEl: el.querySelector('.now-meta'), histEl: el.querySelector('.history'),
        resultEl: el.querySelector('.result'), rankEl: el.querySelector('.rank'), cubeEl: el.querySelector('.cube'),
      };
    });
  }
  function drawCube(lane) {
    if (!lane.facelets) return;
    lane.cubeEl.classList.toggle('netview', state.view === 'net');
    lane.cubeEl.innerHTML = state.view === 'iso' ? CubeView.iso(lane.facelets) : CubeView.net(lane.facelets);
  }
  function setStatus(lane, text, busy) {
    lane.statusEl.textContent = text;
    lane.statusEl.classList.toggle('busy', !!busy);
  }
  function resetLane(lane, facelets) {
    lane.facelets = facelets;
    lane.running = false; lane.base = 0; lane.moves = 0;
    lane.timerEl.textContent = fmtTime(0);
    lane.movesEl.textContent = '0';
    lane.calls = 0; lane.hits = 0; lane.apiEl.textContent = 'API calls 0'; lane.cacheEl.textContent = 'cache hits 0';
    lane.nowEl.textContent = '–';
    lane.metaEl.textContent = '';
    lane.histEl.innerHTML = '';
    lane.resultEl.className = 'result';
    lane.resultEl.innerHTML = '';
    lane.rankEl.className = 'rank';
    lane.rankEl.textContent = '';
    lane.el.classList.remove('winner');
    setStatus(lane, 'ready', false);
    drawCube(lane);
  }
  function syncClock(lane, elapsedS) {
    lane.base = elapsedS || 0;
    lane.syncAt = performance.now();
  }
  function tick() {
    var now = performance.now();
    Object.keys(state.lanes).forEach(function (k) {
      var l = state.lanes[k];
      if (l.running) l.timerEl.textContent = fmtTime(l.base + (now - l.syncAt) / 1000);
    });
  }
  function selectedModels() {
    return Array.prototype.map.call(document.querySelectorAll('#modelPick input:checked'), function (i) { return i.value; });
  }
  function applySelection() {
    var sel = selectedModels();
    Object.keys(state.lanes).forEach(function (k) { state.lanes[k].el.hidden = sel.indexOf(k) < 0; });
  }

  // ------------------------------------------------------------------ puzzle info
  function showPuzzle(p, label) {
    state.puzzle = p;
    $('#piProgress').textContent = label;
    $('#piScramble').textContent = 'Scramble: ' + p.scramble.join(' ');
    var facts = p.scramble_len + ' turns. Shortest solution: ';
    if (p.reference_kind === 'exact') {
      facts += '<b>' + p.reference_moves + ' moves</b> (exact, breadth-first search), for example <span style="font-family:var(--mono)">' + esc(p.reference_solution.join(' ')) + '</span>';
    } else {
      facts += '<b>at most ' + p.reference_moves + ' moves</b> (upper bound, exact search is too large)';
    }
    $('#piFacts').innerHTML = facts;
  }
  function previewScramble() {
    var body = { test_set: $('#testSet').value, seed: Number($('#seed').value) || 0, index: 0 };
    return post('/api/scramble', body).then(function (p) {
      Object.keys(state.lanes).forEach(function (k) { resetLane(state.lanes[k], p.facelets); });
      showPuzzle(p, 'Preview, same start for every model');
    }).catch(function (e) { toast(e.message, true); });
  }

  // ------------------------------------------------------------------ events from the server
  var STATUS_TEXT = {
    SOLVED: 'Solved ✓', RAN_OUT_OF_MOVES: 'Ran out of moves', OUT_OF_TIME: 'Out of time', ERROR: 'Error',
    BUDGET_EXHAUSTED: 'Request budget used up',
  };
  function onEvent(ev) {
    var lane = ev.model ? state.lanes[ev.model] : null;
    switch (ev.type) {
      case 'run_start':
        state.meta = { config: ev.config, mode: ev.mode, puzzles: ev.puzzles.length, run_id: ev.run_id };
        state.stats = null;
        setModeBanner(ev.backend || ev.mode);
        $('#dashboard').innerHTML = '<p class="empty">First results appear when the first race is finished.</p>';
        break;
      case 'puzzle_start':
        Object.keys(state.lanes).forEach(function (k) { resetLane(state.lanes[k], ev.puzzle.facelets); });
        showPuzzle(ev.puzzle, 'Race ' + ev.race_no + ' of ' + ev.total_races + ' (' + ev.puzzle.id + (ev.repeat ? ', repeat ' + (ev.repeat + 1) : '') + ')');
        break;
      case 'usage':
        if (ev.backend) state.usage[ev.backend] = ev.usage;
        renderUsage();
        break;
      case 'trial_start':
        if (!lane) break;
        if (ev.move_limit != null) lane.limitEl.textContent = ev.move_limit;
        lane.running = true; syncClock(lane, 0);
        setStatus(lane, 'starting..', true);
        break;
      case 'thinking':
        if (!lane) break;
        syncClock(lane, ev.elapsed_s);
        setStatus(lane, (lane.model.category === 'llm' ? 'thinking.. ' : 'deciding.. ') + 'move ' + ev.move_no, true);
        break;
      case 'attempt_failed':
        if (!lane) break;
        syncClock(lane, ev.elapsed_s);
        setStatus(lane, 'retry after ' + ev.kind.replace('_', ' ') + ' (attempt ' + ev.attempt + ')', true);
        break;
      case 'move':
        if (!lane) break;
        lane.facelets = ev.facelets; lane.moves = ev.move_no;
        lane.movesEl.textContent = String(ev.move_no);
        syncClock(lane, ev.elapsed_s);
        lane.timerEl.textContent = fmtTime(ev.elapsed_s);
        lane.nowEl.textContent = ev.move;
        var cached = ev.source === 'cache';
        if (cached) { lane.hits += 1; } else { lane.calls += 1; }
        lane.apiEl.textContent = 'API calls ' + lane.calls;
        lane.cacheEl.textContent = 'cache hits ' + lane.hits;
        lane.metaEl.textContent = (cached ? 'from cache in ' + (ev.cache_ms == null ? '0' : Number(ev.cache_ms).toFixed(2)) + ' ms (live was ' + fmtMs(ev.latency_ms) + ')' : 'live ' + fmtMs(ev.latency_ms)) +
          (ev.confidence != null ? '  ·  confidence ' + Number(ev.confidence).toFixed(2) : '');
        Array.prototype.forEach.call(lane.histEl.querySelectorAll('.latest'), function (x) { x.classList.remove('latest'); });
        var li = document.createElement('li');
        li.className = 'latest' + (cached ? ' cached' : '');
        li.textContent = ev.move;
        li.title = 'Move ' + ev.move_no + ', ' + (cached ? 'from cache' : 'live ' + fmtMs(ev.latency_ms));
        lane.histEl.appendChild(li);
        lane.histEl.scrollTop = lane.histEl.scrollHeight;
        setStatus(lane, 'moved', false);
        drawCube(lane);
        break;
      case 'trial_end':
        if (!lane) break;
        lane.running = false;
        lane.timerEl.textContent = fmtTime(ev.total_time_s);
        lane.facelets = ev.facelets; drawCube(lane);
        var ok = ev.status === 'SOLVED';
        setStatus(lane, STATUS_TEXT[ev.status] || ev.status, false);
        lane.resultEl.className = 'result show ' + (ok ? 'solved' : 'failed');
        var detail = ok
          ? ev.moves_used + ' moves, shortest is ' + (ev.gap_kind === 'upper_bound' ? '≤ ' : '') + ev.reference_moves
          : ev.moves_used + ' moves, ' + ev.stickers_in_place + ' of 54 stickers in place';
        if (ev.error_message) detail += ' (' + ev.error_message + ')';
        if (ev.api_calls != null) { lane.apiEl.textContent = 'API calls ' + ev.api_calls; lane.cacheEl.textContent = 'cache hits ' + (ev.cache_hits || 0); }
        lane.resultEl.innerHTML = '<span>' + esc(STATUS_TEXT[ev.status] || ev.status) + '</span><small>' + esc(detail) + '</small>';
        break;
      case 'puzzle_end':
        ev.ranking.forEach(function (key, i) {
          var l = state.lanes[key];
          if (!l) return;
          l.rankEl.textContent = ordinal(i + 1);
          l.rankEl.className = 'rank show r' + (i + 1);
        });
        var top = state.lanes[ev.ranking[0]];
        if (top && top.resultEl.classList.contains('solved')) top.el.classList.add('winner');
        state.stats = ev.stats;
        renderDashboard();
        break;
      case 'run_end':
        state.stats = ev.stats;
        setRunning(false);
        closeStream();
        renderDashboard();
        loadSavedRuns();
        if (ev.status === 'finished') {
          toast('Benchmark finished. Results saved' + (ev.saved_to ? ' to ' + ev.saved_to : '') + '.');
          if ($('#autoSlide').checked && state.stats && state.stats.n_trials) setTimeout(openSlide, 2500);
        } else if (ev.status === 'cancelled') {
          toast('Benchmark stopped. Partial results were saved.');
        }
        break;
      case 'error':
        toast(ev.message, true);
        break;
      default:
        break;
    }
  }

  function openStream(runId) {
    closeStream();
    var es = new EventSource('/api/runs/' + encodeURIComponent(runId) + '/events');
    es.onmessage = function (e) {
      try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); }
    };
    es.onerror = function () { if (!state.running) closeStream(); };
    state.es = es;
  }
  function closeStream() {
    if (state.es) { state.es.close(); state.es = null; }
  }

  function setRunning(on) {
    state.running = on;
    $('#btnRun').disabled = on;
    $('#btnStop').disabled = !on;
    $('#btnScramble').disabled = on;
    ['backend', 'testSet', 'numPuzzles', 'repeats', 'seed', 'pause', 'parallel'].forEach(function (id) { $('#' + id).disabled = on; });
    if (!on) lockForBackend();
    Array.prototype.forEach.call(document.querySelectorAll('#modelPick input'), function (i) { i.disabled = on; });
  }

  function setModeBanner(mode) {
    var b = $('#modeBanner');
    if (mode === 'openrouter') {
      b.className = 'mode-banner live';
      b.textContent = 'REAL mode: the models are called through OpenRouter. Requests are limited (Claude 5, decision models 20 each, 65 in total) and cached.';
    } else if (mode === 'ollama') {
      b.className = 'mode-banner live local';
      b.textContent = 'Local mode: the models run on this computer through Ollama. Free, no request limit.';
    } else {
      b.className = 'mode-banner';
      b.textContent = 'PRACTICE mode: simulated models, no real request. These are NOT real results. Use it to test the app.';
    }
    b.hidden = false;
    syncBarHeight();
  }

  // ------------------------------------------------------------------ usage panel
  function renderUsage() {
    var u = state.usage[state.backend], el = $('#usagePanel');
    if (!u) { el.innerHTML = ''; return; }
    if (u.error) { el.innerHTML = '<span class="u-err">Usage file problem: ' + esc(u.error) + '</span>'; return; }
    var nameOf = {};
    roster().forEach(function (m) { nameOf[m.key] = m.name; });
    var chips = Object.keys(u.models || {}).map(function (k) {
      var m = u.models[k];
      var full = m.limit != null && m.requests >= m.limit;
      return '<span class="u-chip' + (full ? ' full' : '') + '">' + esc(nameOf[k] || k) + ' <b>' + m.requests + (m.limit != null ? ' / ' + m.limit : '') + '</b></span>';
    }).join('');
    var total = u.total_limit ? '<span class="u-chip total">Total ' + (state.backend === 'simulated' ? 'practice' : 'real') + ' requests <b>' + u.total_requests + ' / ' + u.total_limit + '</b></span>'
      : '<span class="u-chip total">Total requests <b>' + u.total_requests + '</b> (no limit, local)</span>';
    el.innerHTML = '<span class="u-title">Request counter (saved on disk)</span>' + total + chips +
      '<span class="u-chip">Cache entries <b>' + u.cache_entries + '</b></span><span class="u-chip">Cache hits <b>' + u.cache_hits + '</b></span>' +
      (u.backend === 'openrouter' ? '<span class="u-chip' + (u.cost_limit_usd ? ' total' : '') + '">Cost <b>$' + Number((u.total_cost_usd || 0) + (u.assumed_cost_usd || 0)).toFixed(4) + (u.cost_limit_usd ? ' / $' + Number(u.cost_limit_usd).toFixed(2) : '') + '</b></span>' : '');
  }

  // ------------------------------------------------------------------ run control
  function startRun() {
    var models = selectedModels();
    if (!models.length) { toast('Choose at least one model.', true); return; }
    var s = getStudent();
    if (!s.name || !s.roll) { toast('Set your name and roll number first (Edit button, top right).', true); }
    var body = {
      backend: state.backend,
      test_set: $('#testSet').value,
      num_puzzles: Number($('#numPuzzles').value) || 1,
      seed: Number($('#seed').value) || 0,
      repeats: Number($('#repeats').value) || 1,
      pause_s: Number($('#pause').value) || 0,
      parallel: $('#parallel').checked,
      models: models,
      student_name: s.name, student_roll_no: s.roll,
    };
    if (state.backend === 'openrouter') {
      var u = state.usage.openrouter || {};
      var ok = window.confirm('This run sends REAL requests with your personal OpenRouter key.\n\n' +
        'Limits: Claude ' + state.cfg.settings.max_claude_requests + ', each decision model ' + state.cfg.settings.max_requests_per_decision_model +
        ', total ' + state.cfg.settings.max_total_requests + '.\nAlready used: ' + (u.total_requests || 0) + ' requests.\n\nStart the real run?');
      if (!ok) return;
      body.confirm_live = true;
    }
    post('/api/runs', body).then(function (r) {
      state.runId = r.run_id;
      setRunning(true);
      openStream(r.run_id);
    }).catch(function (e) { toast(e.message, true); });
  }
  function stopRun() {
    if (state.runId) post('/api/runs/' + encodeURIComponent(state.runId) + '/cancel').catch(function (e) { toast(e.message, true); });
  }

  // ------------------------------------------------------------------ dashboard and slide
  function bestSet(stats) { return stats.best || {}; }
  function isBest(list, key) { return !!list && list.indexOf(key) >= 0; }

  function statsTable(stats) {
    var best = bestSet(stats);
    var rows = stats.models.map(function (m) {
      var gapTxt = m.mean_gap == null ? '<span class="nodata">no solved puzzle</span>'
        : (m.gap_kind === 'upper_bound' ? '≤ ' : '') + (m.mean_gap > 0 ? '+' : '') + num(m.mean_gap, 2);
      var gapSub = m.mean_gap == null ? '' : '<small>' + m.optimal_solves + ' of ' + m.trials + ' optimal</small>';
      return '<tr>' +
        '<td class="model"><i style="background:' + esc(m.key && colorOf(m.key)) + '"></i>' + esc(m.name) +
          '<small>' + (m.category === 'llm' ? 'LLM' : 'Decision model') + (m.mode === 'simulated' ? ', simulated' : (m.mode === 'ollama' ? ', local' : '')) + '</small></td>' +
        '<td class="' + (isBest(best.solve_rate, m.key) ? 'best' : '') + '">' + num(m.success_rate, 1) + '%<small>' + m.solved + ' of ' + m.trials + ' solved</small></td>' +
        '<td class="' + (isBest(best.fastest, m.key) ? 'best' : '') + '">' + fmtMs(m.avg_latency_ms) + '<small>median ' + fmtMs(m.median_latency_ms) + ', p95 ' + fmtMs(m.p95_latency_ms) + '</small></td>' +
        '<td class="' + (isBest(best.quickest_total, m.key) ? 'best' : '') + '">' + fmtSecs(m.avg_total_time_s) + '<small>per puzzle, total ' + fmtSecs(m.sum_total_time_s) + '</small></td>' +
        '<td class="' + (isBest(best.fewest_moves_solved, m.key) ? 'best' : '') + '">' + num(m.avg_moves, 1) + '<small>per puzzle, total ' + m.total_moves + '</small></td>' +
        '<td class="' + (isBest(best.shortest, m.key) ? 'best' : '') + '">' + gapTxt + gapSub + '</td>' +
        '<td>' + (m.api_calls || 0) + '<small>cache hits ' + (m.cache_hits || 0) + (m.failed_requests ? ', failed ' + m.failed_requests : '') +
          (m.cost_usd ? ', $' + Number(m.cost_usd).toFixed(4) : '') + '</small></td>' +
        '</tr>';
    }).join('');
    return '<table class="stats"><thead><tr>' +
      '<th>Model</th><th>Success rate</th><th>Avg latency per move</th><th>Solve time (avg per puzzle)</th><th>Moves (avg per puzzle)</th><th>Optimality gap (avg)</th><th>API calls</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }
  function colorOf(key) {
    var l = state.lanes[key];
    if (l) return l.model.color;
    var found = '#888';
    Object.keys(state.cfg.rosters).forEach(function (b) {
      state.cfg.rosters[b].forEach(function (m) { if (m.key === key) found = m.color; });
    });
    return found;
  }

  function barChart(title, stats, field, unitFmt, opts) {
    opts = opts || {};
    var vals = stats.models.map(function (m) { return m[field]; });
    var max = opts.max || Math.max.apply(null, vals.filter(function (v) { return v != null; }).concat([0.0001]));
    var rows = stats.models.map(function (m) {
      var v = m[field];
      var w = v == null ? 0 : Math.max(0, Math.min(100, (Math.abs(v) / max) * 100));
      return '<div class="bar-row"><span class="bar-label" title="' + esc(m.name) + '">' + esc(m.name) + '</span>' +
        '<div class="bar-track"><div class="bar-fill" style="width:' + w.toFixed(1) + '%;background:' + esc(colorOf(m.key)) + '"></div></div>' +
        '<span class="bar-val">' + (v == null ? '–' : unitFmt(v)) + '</span></div>';
    }).join('');
    return '<div class="chart"><h3>' + esc(title) + '</h3>' + rows + '</div>';
  }

  function metaLine(stats, meta) {
    var cfg = (meta && meta.config) || {};
    var sets = state.cfg.test_sets.filter(function (t) { return t.key === (cfg.test_set || stats.test_set); })[0];
    var parts = [
      '<b>' + esc(sets ? sets.label : (stats.test_set || '')) + '</b>',
      stats.n_puzzles + (stats.n_puzzles === 1 ? ' puzzle x ' : ' puzzles x ') + stats.models.length + ' models = ' + stats.n_trials + ' trials',
      'move limit ' + (cfg.move_limit || state.cfg.settings.move_limit),
      'time limit ' + (cfg.trial_time_limit_s || state.cfg.settings.trial_time_limit_s) + ' s',
    ];
    var run = stats.run_id ? esc(stats.run_id) : '';
    return parts.join('  ·  ') + (run ? '  ·  run ' + run : '');
  }

  var DEFS = '<p class="defs"><b>Success rate</b>: share of puzzles solved within the move limit and the time limit. ' +
    '<b>Latency</b>: live API time until a model returns one move (retries not included); answers from the cache are timed separately. ' +
    '<b>Solve time</b>: time from the first request until the cube is solved or the limit is reached. ' +
    '<b>Optimality gap</b> = model moves minus the exact shortest solution, for solved puzzles only (0 means optimal; "≤" means the reference is only an upper bound). ' +
    'Green cells mark the best value in a column.</p>';

  function statsBody(stats, meta, forSlide) {
    var mock = stats.models.some(function (m) { return m.mode === 'simulated'; });
    var h = '';
    if (mock) h += '<p class="mock-note">Simulated data: these are not results of the real models.</p>';
    h += '<p class="dash-meta">' + metaLine(stats, meta) + '</p>';
    h += statsTable(stats);
    var uu = state.usage[stats.mode] || state.usage[state.backend];
    if (uu && !uu.error) h += '<p class="dash-meta">Requests counted so far (' + esc(uu.backend) + '): <b>' + uu.total_requests + (uu.total_limit ? ' of ' + uu.total_limit : '') +
      '</b> · cache entries ' + uu.cache_entries + ' · cache hits ' + uu.cache_hits + '</p>';
    h += '<div class="charts">' +
      barChart('Success rate (%)', stats, 'success_rate', function (v) { return v.toFixed(0) + '%'; }, { max: 100 }) +
      barChart('Average latency per move', stats, 'avg_latency_ms', fmtMs) +
      barChart('Average solve time per puzzle', stats, 'avg_total_time_s', fmtSecs) +
      barChart('Optimality gap (avg moves above the shortest)', stats, 'mean_gap', function (v) { return v.toFixed(2); }) +
      '</div>';
    h += DEFS;
    if (!forSlide) h += puzzleTable(stats);
    return h;
  }

  function puzzleTable(stats) {
    if (!stats.per_puzzle.length) return '';
    var head = '<tr><th>Puzzle</th><th>Scramble</th><th>Shortest</th>' + stats.models.map(function (m) { return '<th>' + esc(m.name) + '</th>'; }).join('') + '</tr>';
    var rows = stats.per_puzzle.map(function (p) {
      var cells = stats.models.map(function (m) {
        var r = p.results[m.key];
        if (!r) return '<td class="nodata">–</td>';
        if (r.status === 'SOLVED') {
          var g = r.gap == null ? '' : ' (' + (r.gap > 0 ? '+' : '') + r.gap + ')';
          return '<td class="cell-ok">✓ ' + r.moves + g + ' · ' + fmtSecs(r.time_s) + '</td>';
        }
        return '<td class="cell-bad">✗ ' + esc((STATUS_TEXT[r.status] || r.status).toLowerCase()) + ' · ' + r.moves + ' moves</td>';
      }).join('');
      return '<tr><td>' + esc(p.puzzle_id) + (p.repeat ? ' r' + (p.repeat + 1) : '') + '</td><td style="text-align:left;font-family:var(--mono)">' + esc((p.scramble || []).join(' ')) + '</td>' +
        '<td>' + (p.reference_kind === 'upper_bound' ? '≤ ' : '') + p.reference_moves + '</td>' + cells + '</tr>';
    }).join('');
    return '<details class="more"><summary>Every puzzle, one row each (' + stats.per_puzzle.length + ')</summary>' +
      '<table class="stats puzzles"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></details>';
  }

  function renderDashboard() {
    var el = $('#dashboard');
    if (!state.stats || !state.stats.n_trials) {
      el.innerHTML = '<p class="empty">No results yet. Press <b>Run benchmark</b> to start.</p>';
    } else {
      el.innerHTML = statsBody(state.stats, state.meta, false);
    }
    if (!$('#slide').hidden) renderSlide();
  }
  function renderSlide() {
    var el = $('#slideBody');
    if (!state.stats || !state.stats.n_trials) {
      el.innerHTML = '<p class="empty">No results yet. Run a benchmark first, or open a saved run.</p>';
    } else {
      el.innerHTML = statsBody(state.stats, state.meta, true);
    }
  }
  function openSlide() {
    $('#toast').hidden = true; // a toast must not cover the statistics in the recording
    renderSlide();
    $('#slide').hidden = false;
    document.body.classList.add('slide-open');
    syncBarHeight();
  }
  function closeSlide() {
    $('#slide').hidden = true;
    document.body.classList.remove('slide-open');
  }

  function loadSavedRuns() {
    api('/api/runs').then(function (d) {
      var sel = $('#savedRuns');
      var keep = sel.value;
      sel.innerHTML = '<option value="">Open a saved run</option>' + d.runs.map(function (r) {
        return '<option value="' + esc(r.run_id) + '">' + esc(r.run_id) + ' (' + r.n_trials + ' trials, ' + esc(r.status || '') + ')</option>';
      }).join('');
      sel.value = keep;
    }).catch(function () { /* the list is optional */ });
  }
  function openSaved() {
    var id = $('#savedRuns').value;
    if (!id) { toast('Choose a saved run first.', true); return; }
    api('/api/runs/' + encodeURIComponent(id)).then(function (d) {
      state.stats = d.stats;
      state.meta = { config: d.config, mode: d.mode, puzzles: d.puzzles.length, run_id: d.run_id };
      setModeBanner(d.mode);
      renderDashboard();
      toast('Loaded ' + id);
    }).catch(function (e) { toast(e.message, true); });
  }

  // ------------------------------------------------------------------ start up
  function setTestSetOptions() {
    var ts = $('#testSet');
    var keep = ts.value;
    ts.innerHTML = state.cfg.test_sets.map(function (t) {
      var off = state.backend === 'openrouter' && t.key !== 'teacher';
      return '<option value="' + t.key + '"' + (off ? ' disabled' : '') + '>' + esc(t.label) + ' (' + t.min_turns + '-' + t.max_turns + ' turns)</option>';
    }).join('');
    ts.value = keep;
  }
  function applyDefaults() {
    var t = state.cfg.test_sets.filter(function (x) { return x.key === $('#testSet').value; })[0];
    if (t) $('#numPuzzles').value = t.default_count;
  }
  // OpenRouter: one puzzle, one repeat, teacher set only (the request budget is tiny).
  function lockForBackend() {
    var real = state.backend === 'openrouter';
    if (real) {
      $('#testSet').value = 'teacher'; $('#numPuzzles').value = 1; $('#repeats').value = 1;
    }
    ['testSet', 'numPuzzles', 'repeats'].forEach(function (id) { $('#' + id).disabled = real; });
    $('#btnReset').hidden = state.backend !== 'simulated';
  }
  function rebuildModels() {
    var box = $('#modelPick');
    var old = box.querySelector('.model-options');
    if (old) old.remove();
    box.insertAdjacentHTML('beforeend', '<div class="model-options">' + roster().map(function (m) {
      return '<label class="check"><input type="checkbox" value="' + m.key + '" checked> ' + esc(m.name) + '</label>';
    }).join('') + '</div>');
  }
  function backendNote() {
    var info = (state.cfg.backends || {})[state.backend] || {};
    if (state.backend === 'ollama' && !info.available) toast('Ollama is not running. Start the Ollama app first.', true);
    if (state.backend === 'ollama' && info.available) {
      var miss = (info.models || []).filter(function (m) { return !m.installed; });
      if (miss.length) toast('Not installed yet: ' + miss.map(function (m) { return m.pull; }).join('  |  '), true);
    }
    if (state.backend === 'openrouter' && !info.available) toast('No OPENROUTER_API_KEY found in .env', true);
  }
  function switchBackend(b) {
    state.backend = b;
    setTestSetOptions();
    $('#testSet').value = b === 'ollama' ? 'primary' : 'teacher';
    applyDefaults();
    lockForBackend();
    rebuildModels();
    buildLanes();
    setModeBanner(b);
    renderUsage();
    backendNote();
    return previewScramble();
  }

  function initControls() {
    var ts = $('#testSet');
    setTestSetOptions();
    ts.value = 'teacher';
    applyDefaults();
    ts.addEventListener('change', function () { applyDefaults(); previewScramble(); });
    $('#backend').addEventListener('change', function () { switchBackend($('#backend').value); });
    $('#btnReset').addEventListener('click', function () {
      if (!window.confirm('Clear the PRACTICE counters and practice cache? (The real ones are never touched.)')) return;
      post('/api/simulated/reset').then(function (r) { state.usage.simulated = r.usage; renderUsage(); toast('Practice counters cleared.'); })
        .catch(function (e) { toast(e.message, true); });
    });
    $('#modelPick').addEventListener('change', applySelection);
    rebuildModels();

    $('#btnRun').addEventListener('click', startRun);
    $('#btnStop').addEventListener('click', stopRun);
    $('#btnScramble').addEventListener('click', function () {
      $('#seed').value = String(Math.floor(Math.random() * 90000) + 1000);
      previewScramble();
    });
    $('#seed').addEventListener('change', previewScramble);
    $('#btnView').addEventListener('click', function () {
      state.view = state.view === 'iso' ? 'net' : 'iso';
      $('#btnView').textContent = state.view === 'iso' ? 'Show unfolded net' : 'Show 3D cube';
      Object.keys(state.lanes).forEach(function (k) { drawCube(state.lanes[k]); });
    });
    $('#btnSlide').addEventListener('click', openSlide);
    $('#closeSlide').addEventListener('click', closeSlide);
    $('#btnOpenSaved').addEventListener('click', openSaved);
    document.addEventListener('keydown', function (e) {
      var tag = (document.activeElement && document.activeElement.tagName) || '';
      if (e.key === 'Escape' && !$('#slide').hidden) closeSlide();
      if ((e.key === 's' || e.key === 'S') && !/INPUT|SELECT|TEXTAREA/.test(tag) && !e.ctrlKey && !e.metaKey) {
        if ($('#slide').hidden) openSlide(); else closeSlide();
      }
    });
  }

  function init() {
    api('/api/config').then(function (cfg) {
      state.cfg = cfg;
      initStudentDialog();
      renderStudent();
      initControls();
      state.usage = cfg.usage || {};
      buildLanes();
      setModeBanner(state.backend);
      lockForBackend();
      renderUsage();
      state.timer = setInterval(tick, 50);
      loadSavedRuns();
      return previewScramble();
    }).catch(function (e) {
      document.body.insertAdjacentHTML('beforeend', '<p style="padding:24px;color:#b3261e">Cannot reach the backend: ' + esc(e.message) + '. Start it with <code>python -m app.main</code>.</p>');
    });
  }
  init();
})();
