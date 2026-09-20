import {pretty, title, clock, eventTime, captureTime, indexAt, observationIndexAt, placement, deriveLog, visibleLog, unitLabelVisible} from './replay-state.js';
const $ = id => document.getElementById(id);
const canvas = $('arena'), ctx = canvas.getContext('2d'), video = $('game-video');
let config, events = [], controls = [], log = [], current = null, bitmap = null, mode, runMode;
let playing = false, start = null, paintVersion = 0, renderVersion = 0;
let media = {}, summary = {}, activeIndex = -2, playhead = 0, duration = 0, firstTime = 0;
let liveRunning = false, liveConnected = false;
let isLive = false, liveOrigin = 0, liveRun = null;
let liveRevision = '', liveRequest = null, lastDrawKey = '', lastUIUpdate = 0;
let displayDelay = 5, displayClock = 0, videoSequence = -1, videoURL = null;
let observation = null, activeObservationIndex = -2;
let stageCssWidth = 0, labelBoxes = [];
const positionLabelLifetime = 1.25;
let hasVideo = false, initialized = false, lastTick = 0, noticeKey = '', lastAgeLabel = '';
async function get(path) { const r = await fetch(path); if (!r.ok) throw Error(`HTTP ${r.status}`); return r.json(); }
async function post(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await r.json(); if (!r.ok) throw Error(data.error || `HTTP ${r.status}`); return data;
}
function text(id, value) { const el = $(id), next = String(value); if (el.textContent !== next) el.textContent = next; }
function element(tag, value, cls) { const el = document.createElement(tag); el.textContent = value; if (cls) el.className = cls; return el; }
function toast(message) { text('toast', message); $('toast').hidden = false; clearTimeout(toast.timeout); toast.timeout = setTimeout(() => $('toast').hidden = true, 2500); }
function overlayScale() { return canvas.width / (stageCssWidth || canvas.width); }
function label(value, x, y, color, align = 'left', anchor = null) {
  const scale = overlayScale(), font = Math.min(12, Math.max(10, stageCssWidth / 34)) * scale;
  ctx.font = `500 ${font}px -apple-system, sans-serif`;
  const pad = 3 * scale, gap = 3 * scale, margin = 3 * scale;
  while (value.length > 2 && ctx.measureText(value).width > canvas.width - 4 * pad) value = value.slice(0, -2) + '…';
  const width = ctx.measureText(value).width + pad * 2, height = font + pad * 2;
  const left = align === 'center' ? x - width / 2 : x;
  const top = y - height;
  const offsets = [[0, 0], [0, -height - gap], [0, height + gap], [-width / 2 - gap, 0], [width / 2 + gap, 0], [0, -2 * (height + gap)], [0, 2 * (height + gap)]];
  const candidates = offsets.map(([dx, dy]) => ({
    x: Math.max(margin, Math.min(left + dx, canvas.width - width - margin)),
    y: Math.max(margin, Math.min(top + dy, canvas.height - height - margin)), w: width, h: height,
  }));
  const overlaps = (a, b) => a.x < b.x + b.w + gap && a.x + a.w + gap > b.x && a.y < b.y + b.h + gap && a.y + a.h + gap > b.y;
  const box = mode === 'calibrate' ? candidates[0] : candidates.find(b => !labelBoxes.some(other => overlaps(b, other)));
  if (!box) return false;
  labelBoxes.push(box);
  if (anchor) {
    const ax = Math.max(box.x, Math.min(anchor[0], box.x + box.w));
    const ay = Math.max(box.y, Math.min(anchor[1], box.y + box.h));
    ctx.strokeStyle = color; ctx.lineWidth = scale; ctx.globalAlpha = .55;
    ctx.beginPath(); ctx.moveTo(...anchor); ctx.lineTo(ax, ay); ctx.stroke(); ctx.globalAlpha = 1;
  }
  ctx.fillStyle = '#111519ed'; ctx.beginPath(); ctx.roundRect(box.x, box.y, box.w, box.h, 2 * scale); ctx.fill();
  ctx.fillStyle = color; ctx.textBaseline = 'top'; ctx.fillText(value, box.x + pad, box.y + pad); ctx.textBaseline = 'alphabetic';
  return true;
}
function rect(r, color, caption) {
  ctx.strokeStyle = color; ctx.lineWidth = canvas.width / 300;
  ctx.strokeRect(r.x * canvas.width, r.y * canvas.height, r.w * canvas.width, r.h * canvas.height);
  if (caption) label(caption, r.x * canvas.width + 8, r.y * canvas.height + canvas.width / 29, color);
}
function arenaPoint(x, y) { const a = config.layout.arena; return [(a.x + x * a.w) * canvas.width, (a.y + y * a.h) * canvas.height]; }
function fitStage() {
  // Keep the canvas and the video's pixels aligned, including the phone border.
  const wrap = document.querySelector('.canvas-wrap'), stage = $('game-stage');
  const outer = getComputedStyle(wrap), inner = getComputedStyle(stage);
  const borderX = parseFloat(inner.borderLeftWidth) + parseFloat(inner.borderRightWidth);
  const borderY = parseFloat(inner.borderTopWidth) + parseFloat(inner.borderBottomWidth);
  const availableW = wrap.clientWidth - parseFloat(outer.paddingLeft) - parseFloat(outer.paddingRight) - borderX;
  const availableH = wrap.clientHeight - parseFloat(outer.paddingTop) - parseFloat(outer.paddingBottom) - borderY;
  const sourceSize = config?.layout.reference_size;
  const ratio = isLive && sourceSize ? sourceSize[0] / sourceSize[1] : canvas.width / canvas.height;
  const width = Math.max(0, Math.min(availableW, availableH * ratio));
  stageCssWidth = width;
  stage.style.width = `${width + borderX}px`; stage.style.height = `${width / ratio + borderY}px`;
  if (isLive && width > 0) {
    const pixels = Math.max(1, Math.round(width * devicePixelRatio));
    if (canvas.width !== pixels) {
      canvas.width = pixels; canvas.height = Math.round(pixels / ratio); draw();
    }
  }
  if (!isLive) lastDrawKey = '';
  draw();
}
function observationAge() {
  const frame = observation ?? current;
  if (frame?.state && !hasVideo && !isLive) return 0;
  return frame?.state ? Math.max(0, isLive ? displayClock - frame.state.captured_at : playhead - captureTime(frame, media)) : Infinity;
}
function positionLabelsExpired() {
  if (!(observation ?? current)?.state) return true;
  const age = observationAge();
  // Buffered video can align to capture time. In immediate mode, briefly show each
  // newly completed observation, retaining its real age in the observation badge.
  const shownFor = isLive && !displayDelay ? playhead - eventTime(observation ?? current, media) : age;
  return shownFor > positionLabelLifetime || age > (config?.runtime.max_state_age_ms ?? 5500) / 1000;
}
function presentationActive() { return liveRunning || (displayDelay > 0 && events.length && playhead <= eventTime(events.at(-1), media) + 3); }
function draw() {
  if (!config || (!bitmap && !hasVideo && !isLive)) return;
  const stale = (hasVideo || isLive) && positionLabelsExpired();
  const showAction = Boolean(current?.action?.position && playhead - eventTime(current, media) < 2.8);
  if (mode !== 'calibrate') {
    const key = [renderVersion, paintVersion, canvas.width, canvas.height, stageCssWidth, presentationActive(), liveConnected, stale, showAction,
      ...['overlay', 'units-toggle', 'actions-toggle', 'hud-toggle'].map(id => $(id).checked)].join('|');
    if (key === lastDrawKey) return;
    lastDrawKey = key;
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  labelBoxes = [];
  text('overlay-detail', 'Model estimates');
  if (!hasVideo && !isLive && bitmap) ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  if (!$('overlay').checked) return;
  if (mode === 'calibrate') {
    rect(config.layout.arena, '#9ce4b1', 'Arena');
    config.layout.hand.forEach((r, i) => rect(r, '#9ccaf6', `Card ${i + 1}`));
    rect(config.layout.elixir, '#d29eeb', 'Elixir');
    if (config.layout.timer) rect(config.layout.timer, '#f6d899', 'Timer');
    const a = config.layout.arena;
    config.layout.forbidden.forEach(r => rect({x: a.x + r.x * a.w, y: a.y + r.y * a.h, w: r.w * a.w, h: r.h * a.h}, '#ff8c9e', 'No deploy'));
    config.layout.placements.forEach(p => { const [x, y] = arenaPoint(p.x, p.y); ctx.fillStyle = '#f6d899'; ctx.beginPath(); ctx.arc(x, y, canvas.width / 130, 0, Math.PI * 2); ctx.fill(); label(p.name, x + 7, y, '#f6d899'); });
    return;
  }
  const state = (observation ?? current)?.state;
  if (!state?.battle_active || (isLive && (!presentationActive() || !liveConnected))) return;
  const scale = overlayScale();
  // Reserve the observation badge and event notice rather than painting through them.
  labelBoxes.push({x: 0, y: 0, w: canvas.width, h: 32 * scale});
  if ($('game-notices').childElementCount) labelBoxes.push({x: 0, y: canvas.height * .79 - 44 * scale, w: canvas.width, h: 44 * scale});
  if ($('actions-toggle').checked && showAction) {
    const [x, y] = arenaPoint(current.action.position.x, current.action.position.y);
    ctx.fillStyle = '#f0ece2'; ctx.beginPath(); ctx.arc(x, y, 2 * scale, 0, Math.PI * 2); ctx.fill();
    label(`${title(current.action.card)} · ${current.status === 'sent' ? 'placed' : 'selected'}`, x + 8 * scale, y - 7 * scale, '#e6e4df', 'left', [x, y]);
  }
  if (!stale && $('units-toggle').checked) {
    const units = (state.units ?? []).filter(unit => unitLabelVisible(unit.type, config.runtime.vision_unit_types)).sort((a, b) => b.confidence - a.confidence);
    let shown = 0;
    for (const unit of units.slice(0, 6)) {
      const [x, y] = arenaPoint(unit.x, unit.y), color = unit.team === 'enemy' ? '#ff8c9e' : unit.team === 'ally' ? '#9ce4b1' : '#b6bcc6';
      if (label(title(unit.type), x, y - 7 * scale, color, 'center', [x, y])) {
        const size = 2.5 * scale;
        ctx.fillStyle = color; ctx.fillRect(x - size / 2, y - size / 2, size, size); shown++;
      }
    }
    if (units.length > shown) text('overlay-detail', `+${units.length - shown} more`);
    for (const tower of state.towers ?? []) {
      if (stageCssWidth < 300) continue; // Native HP remains visible in the game itself.
      if (tower.hp === null && !tower.destroyed) continue;
      const [x, y] = arenaPoint(tower.x, tower.y);
      label(tower.destroyed ? 'DESTROYED' : `${tower.hp} HP`, x, y + canvas.width / 18, tower.id.startsWith('enemy') ? '#ffacb9' : '#bdeaca', 'center');
    }
  }
  if (!stale && $('hud-toggle').checked) {
    const selected = current?.action?.slot;
    for (const card of state.hud.hand) {
      const r = config.layout.hand[card.slot];
      if (selected === card.slot) { ctx.fillStyle = '#d5dfd8'; ctx.fillRect(r.x * canvas.width, (r.y + r.h) * canvas.height, r.w * canvas.width, 1.5 * scale); }
      const name = stageCssWidth < 300 ? `${card.slot + 1}` : card.card ? title(card.card) : '?';
      label(name, (r.x + r.w / 2) * canvas.width, (r.y + r.h + .017) * canvas.height, selected === card.slot ? '#f6d899' : '#c1dcf8', 'center');
    }
    const r = config.layout.elixir;
    label(`${state.hud.elixir ?? '?'} / 10`, (r.x + r.w / 2) * canvas.width, (r.y - .005) * canvas.height, '#e3b8f6', 'center');
  }
}
async function loadImage(url) {
  const revision = ++paintVersion;
  const img = new Image(); img.src = url; await img.decode(); if (revision !== paintVersion) return;
  bitmap = img; canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  $('game-stage').style.aspectRatio = `${canvas.width} / ${canvas.height}`;
  $('empty').hidden = true; fitStage(); draw();
}
const percent = value => Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—';
function renderScores(id, values, selected, frame, cardMode = false) {
  const ranked = Object.entries(values ?? {}).sort((a, b) => b[1] - a[1]);
  const rows = ranked.slice(0, 6), chosen = ranked.find(([key]) => key === selected);
  if (chosen && !rows.some(([key]) => key === selected)) rows[rows.length - 1] = chosen;
  $(id).replaceChildren(...rows.map(([key, probability]) => {
    const action = frame.candidates?.find(option => option.id === key);
    const name = key === 'WAIT' ? 'Wait' : cardMode ? title(key.replace(/^CARD_\d+_/, '')) : action ? placement(action) : pretty(key);
    const row = element('div', '', `decision-score${key === selected ? ' selected' : ''}`);
    const caption = element('div', '', 'score-caption');
    caption.append(element('span', name), element('span', percent(probability)));
    const bar = element('div', '', 'score-bar'), fill = element('i', '');
    fill.style.width = `${Math.max(0, Math.min(100, probability * 100))}%`;
    bar.append(fill); row.append(caption, bar); return row;
  }));
  if (!rows.length) $(id).append(element('p', 'No placement chosen.', 'decision-note'));
  else if (ranked.length > rows.length) $(id).append(element('p', `${rows.length} of ${ranked.length} options · selected included`, 'decision-note'));
}
function renderDecision(frame) {
  const decision = frame?.decision, action = frame?.action;
  const available = Boolean(decision && decision.source !== 'controller');
  $('decision-empty').hidden = available; $('decision-content').hidden = !available;
  if (!available) {
    text('decision-empty', frame?.status?.includes('error') ? `Decision unavailable · ${pretty(frame.status)}` : 'Waiting for Jev’s next decision.');
    return;
  }
  text('decision-choice', action?.card ? `${title(action.card)} → ${placement(action)}` : 'Wait & observe');
  const status = {sent: 'Taps sent', wait: 'Holding elixir', dry_run: 'Input disabled'}[frame.status] ?? pretty(frame.status);
  text('decision-status', `${clock(eventTime(frame, media) - firstTime)} · ${status}`);
  const hud = frame.state?.hud;
  text('hand-elixir', `${hud?.elixir ?? '—'} / 10 elixir`);
  $('decision-hand').replaceChildren(...(hud?.hand ?? []).map(card => {
    const selected = action?.card && action.slot === card.slot;
    const tile = element('div', '', `hand-card${selected ? ' selected' : ''}`);
    tile.append(element('span', card.card ? title(card.card) : 'Unknown'));
    const cost = config.deck.find(entry => entry.id === card.card)?.cost;
    tile.append(element('small', `Slot ${card.slot + 1}${cost ? ` · ${cost} elixir` : ''}`));
    return tile;
  }));
  const staged = Object.keys(decision.card_probabilities ?? {}).length > 0;
  text('card-scores-title', staged ? 'Card choice' : 'Action choices');
  renderScores('card-scores', staged ? decision.card_probabilities : decision.probabilities,
    staged ? action?.card ? `CARD_${action.slot}_${action.card}` : 'WAIT' : decision.choice, frame, staged);
  renderScores('placement-scores', staged && action?.card ? decision.probabilities : {}, decision.choice, frame);
  text('action-json', JSON.stringify({card: action?.card ?? null, slot: action?.slot ?? null,
    position: action?.position ?? null, status: frame.status}, null, 2));
}
async function render(index, observedIndex = index) {
  const revision = ++renderVersion;
  activeIndex = index; activeObservationIndex = observedIndex;
  observation = events[observedIndex] ?? null;
  current = events[index] ?? (observation ? {frame_id: observation.frame_id, status: 'processing'} : null);
  renderDecision(events[index]);
  text('frame-label', current ? `FRAME ${String((observation ?? current).frame_id).padStart(4, '0')}` : 'FRAME —');
  if (!current) {
    if (hasVideo || isLive) $('empty').hidden = true;
    draw(); return;
  }
  if (!hasVideo && !isLive) {
    try { await loadImage(`/frames/${encodeURIComponent(current.image)}`); }
    catch (error) { if (revision === renderVersion) toast(`Frame unavailable: ${error.message}`); }
  } else { $('empty').hidden = true; draw(); }
}
function updateLog() {
  const visible = visibleLog(log, playhead);
  const recent = visible.filter(item => playhead - item.time < 3.2 && item.overlayHeading !== null && ['detected', 'sent', 'confirmed', 'tower', 'controller'].includes(item.kind)).map(item => ({...item, heading: item.overlayHeading ?? item.heading}));
  const selected = recent.filter(item => item.kind !== 'detected' || $('units-toggle').checked).filter(item => !['sent', 'confirmed'].includes(item.kind) || $('actions-toggle').checked).slice(-1);
  const key = $('overlay').checked ? selected.map(item => `${item.time}-${item.heading}`).join('|') : '';
  if (key !== noticeKey) {
    noticeKey = key;
    $('game-notices').replaceChildren(...($('overlay').checked ? selected : []).map(item => {
      const el = element('div', '', `game-notice ${item.tone}`); el.append(element('small', item.kind === 'detected' ? (runMode === 'offline-fixture' ? 'FIXTURE · ANNOTATION' : 'QWEN · DETECTED') : item.kind.toUpperCase()), element('span', item.heading)); return el;
    }));
    lastDrawKey = ''; draw();
  }
}
function updateClock() {
  const relative = Math.max(0, playhead - firstTime);
  if (!isLive) { $('scrub').value = playhead; text('play-time', clock(relative)); text('duration', clock(duration - firstTime)); }
  const age = observationAge(), stale = positionLabelsExpired();
  const state = (observation ?? current)?.state;
  $('observation-tag').hidden = !$('overlay').checked || !state?.battle_active || (isLive && (!presentationActive() || !liveConnected));
  const ageLabel = `${stale ? 'STALE' : 'OBSERVED'} · ${Number.isFinite(age) ? age.toFixed(1) : '—'}s AGO`;
  if (ageLabel !== lastAgeLabel) { text('observation-age', ageLabel); lastAgeLabel = ageLabel; }
  updateLog();
}
function setPlaying(value) {
  playing = value; text('play', value ? 'Ⅱ' : '▶'); $('play').setAttribute('aria-label', value ? 'Pause replay' : 'Play replay');
}
async function seek(time) {
  playhead = Math.max(firstTime, Math.min(duration, time));
  if (hasVideo && Math.abs(video.currentTime - playhead) > .04) video.currentTime = playhead;
  const next = indexAt(events, playhead, media); if (next !== activeIndex) await render(next);
  updateClock(); draw();
}
function tick(now) {
  const delta = lastTick ? Math.min(.25, (now - lastTick) / 1000) : 0; lastTick = now;
  if (mode === 'viewer' && initialized) {
    if (isLive) playhead = Math.max(0, displayClock - liveOrigin);
    else if (hasVideo) playhead = video.currentTime;
    else if (playing) playhead = Math.min(duration, playhead + delta * Number($('speed').value));
    if ((playing || (isLive && presentationActive())) && now - lastUIUpdate >= 100) {
      lastUIUpdate = now;
      if (isLive) updateLiveTimeline();
      else { const next = indexAt(events, playhead, media); if (next !== activeIndex) render(next); }
      updateClock(); draw();
      if (!hasVideo && !isLive && playhead >= duration) setPlaying(false);
    }
  }
  if (isLive) setTimeout(() => tick(performance.now()), 100);
  else requestAnimationFrame(tick);
}
async function refresh() {
  const [allEvents, nextControls, nextSummary] = await Promise.all([get('/api/events'), get('/api/control'), get('/api/summary')]);
  const battle = allEvents.findIndex(e => e.state?.battle_active);
  events = battle >= 0 ? allEvents.slice(battle) : allEvents; controls = nextControls; summary = nextSummary;
  log = deriveLog(events, controls, media, config.runtime.vision_unit_types);
  const oldFirst = firstTime;
  firstTime = events.length ? Math.max(0, eventTime(events[0], media) - (hasVideo ? (events[0].latency_ms?.total ?? 0) / 1000 : 0)) : 0;
  duration = hasVideo && Number.isFinite(video.duration) ? video.duration : Math.max(firstTime, events.length ? eventTime(events.at(-1), media) : 0);
  $('scrub').min = firstTime; $('scrub').max = duration; $('play').disabled = !events.length;
  const completed = Boolean(summary.stop_reason);
  text('trace-label', runMode === 'offline-fixture' ? 'SYNTHETIC REPLAY' : completed ? 'REPLAY' : 'OBSERVING');
  if (!initialized && events.length) {
    initialized = true; $('follow').checked = !completed && runMode === 'live';
    // Open on the first recorded observation.
    await seek($('follow').checked ? duration : eventTime(events[0], media));
  } else if ($('follow').checked && events.length) await seek(eventTime(events.at(-1), media));
  else if (oldFirst !== firstTime && playhead < firstTime) await seek(firstTime);
  updateClock();
}
function refreshLive() {
  if (!liveRequest) liveRequest = fetchLive().finally(() => { liveRequest = null; });
  return liveRequest;
}
async function fetchLive() {
  const data = await get(`/api/live?revision=${encodeURIComponent(liveRevision)}`);
  liveRevision = data.revision;
  if (data.run !== liveRun) {
    liveRun = data.run; current = observation = null; activeIndex = activeObservationIndex = -2;
    events = []; controls = []; log = []; noticeKey = '';
    $('game-notices').replaceChildren();
  }
  if (data.manifest) {
    config = data.manifest.config; summary = data.summary;
    liveOrigin = data.manifest.started_monotonic ?? data.now;
    media = {capture_clock_offset_s: -liveOrigin, elapsed_offset_s: 0, align_observations: displayDelay > 0};
    events = data.events; controls = data.control; log = deriveLog(events, controls, media, config.runtime.vision_unit_types);
  }
  playhead = Math.max(0, displayClock - liveOrigin); firstTime = 0; duration = playhead;
  const connected = data.stream.connected; liveConnected = connected; liveRunning = data.bot.running;
  $('empty').hidden = connected && displayClock > 0;
  if (!connected) text('empty-message', data.stream.error ?? 'Connecting to the live MuMu display…');
  text('source-label', connected ? displayDelay ? 'MUMU · 5s BUFFER' : 'LIVE · MUMU' : 'CONNECTING');
  text('trace-label', data.bot.running ? 'JEV RUNNING' : 'JEV STOPPED');
  const state = data.bot.stopping ? 'Stopping Jev…' : data.bot.running ? 'Jev running' : data.bot.error ? data.bot.error : summary.stop_reason ? `Stopped · ${pretty(summary.stop_reason)}` : 'Live game · Jev is idle';
  text('live-status', connected ? state : 'Waiting for device video');
  $('start-bot').disabled = data.bot.running || !connected;
  $('stop-bot').disabled = !data.bot.running || data.bot.stopping;
  text('start-bot', data.bot.execute ? 'Start Jev' : 'Start observer');
  updateLiveTimeline();
  draw();
  updateClock();
  initialized = true;
}
function updateLiveTimeline() {
  if (!displayClock) return;
  playhead = Math.max(0, displayClock - liveOrigin);
  const next = displayDelay ? indexAt(events, playhead, media) : events.length - 1;
  const observed = displayDelay ? observationIndexAt(events, playhead, media) : next;
  if (next !== activeIndex || observed !== activeObservationIndex) render(next, observed);
}
async function streamLiveVideo() {
  // One request and decode at a time. Slow viewers skip frames instead of building a queue.
  while (isLive) {
    const delay = displayDelay;
    let url = null;
    try {
      const response = await fetch(`/live-frame.jpg?after=${videoSequence}&delay=${delay}`);
      if (response.status === 204) continue;
      if (!response.ok) throw Error(`Video HTTP ${response.status}`);
      url = URL.createObjectURL(await response.blob());
      const decoded = new Image(); decoded.src = url; await decoded.decode();
      if (delay !== displayDelay) { URL.revokeObjectURL(url); continue; }
      $('live-screen').src = url;
      if (videoURL) URL.revokeObjectURL(videoURL);
      videoURL = url;
      videoSequence = Number(response.headers.get('X-Frame-Sequence'));
      displayClock = Number(response.headers.get('X-Frame-Time'));
      $('empty').hidden = true;
    } catch (error) {
      if (url && url !== videoURL) URL.revokeObjectURL(url);
      text('empty-message', `Video reconnecting · ${error.message}`);
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
}
$('display-delay').onchange = () => {
  displayDelay = Number($('display-delay').value); videoSequence = -1; displayClock = 0;
  activeIndex = activeObservationIndex = -2;
  media = {...media, align_observations: displayDelay > 0};
  log = deriveLog(events, controls, media, config.runtime.vision_unit_types);
  text('empty-message', displayDelay ? 'Buffering 5 seconds of gameplay…' : 'Connecting to live gameplay…');
  $('empty').hidden = false;
};
async function pollLive() {
  try { await refreshLive(); }
  catch (error) { text('live-status', `Connection lost · ${error.message}`); $('start-bot').disabled = true; }
  setTimeout(pollLive, 250);
}
for (const [id, route] of [['start-bot', 'start'], ['stop-bot', 'stop']]) {
  $(id).onclick = async () => {
    $(id).disabled = true;
    try { await post(`/api/live/${route}`, {}); await refreshLive(); }
    catch (error) { toast(error.message); $(id).disabled = false; }
  };
}
function regionOptions() {
  const chosen=$('region').value;$('region').replaceChildren();
  [['arena','Battlefield'],...config.layout.hand.map((_,i)=>[`hand:${i}`,`Card ${i+1} artwork`]),['elixir','Elixir bar'],['timer','Timer (optional)'],...config.layout.placements.map((p,i)=>[`point:${i}`,`Placement: ${p.name}`])].forEach(([value,title])=>{const option=element('option',title);option.value=value;$('region').append(option);});
  if([...$('region').options].some(o=>o.value===chosen))$('region').value=chosen;
  $('card').replaceChildren(...config.deck.map(c=>{const option=element('option',pretty(c.id));option.value=c.id;return option;}));
  syncConfig();
}
function syncConfig(){ $('config-json').value=JSON.stringify(config,null,2);text('templates',config.deck.map(c=>`${pretty(c.id)}: ${c.templates.length}`).join(' · '));draw(); }
function pointerPosition(e){const r=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};}
canvas.addEventListener('pointerdown',e=>{if(mode!=='calibrate')return;start=pointerPosition(e);canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(!start)return;draw();const end=pointerPosition(e);rect({x:Math.min(start.x,end.x),y:Math.min(start.y,end.y),w:Math.abs(start.x-end.x),h:Math.abs(start.y-end.y)},'#fff');});
canvas.addEventListener('pointerup',e=>{
  if(!start)return;const end=pointerPosition(e),key=$('region').value;
  if(key.startsWith('point:')){const a=config.layout.arena,p=config.layout.placements[Number(key.split(':')[1])];p.x=Math.max(0,Math.min(1,(end.x-a.x)/a.w));p.y=Math.max(0,Math.min(1,(end.y-a.y)/a.h));}
  else {const r={x:Math.min(start.x,end.x),y:Math.min(start.y,end.y),w:Math.abs(start.x-end.x),h:Math.abs(start.y-end.y)};if(r.w>.002&&r.h>.002){if(key.startsWith('hand:'))config.layout.hand[Number(key.split(':')[1])]=r;else config.layout[key]=r;}}
  config.layout.calibrated=false;$('confirmed').checked=false;start=null;syncConfig();
});
for (const id of ['overlay', 'units-toggle', 'actions-toggle', 'hud-toggle']) $(id).onchange = () => { draw(); if (mode !== 'calibrate') updateClock(); };
$('scrub').oninput = () => { $('follow').checked = false; seek(Number($('scrub').value)); };
$('play').onclick = async () => {
  $('follow').checked = false;
  if (playing) { if (hasVideo) video.pause(); setPlaying(false); }
  else {
    if (playhead >= duration - .1) await seek(firstTime);
    if (hasVideo) { try { await video.play(); } catch (error) { toast(error.message); return; } }
    setPlaying(true);
  }
};
$('speed').onchange = () => { video.playbackRate = Number($('speed').value); };
$('follow').onchange = () => { if ($('follow').checked) { video.pause(); setPlaying(false); seek(events.length ? eventTime(events.at(-1), media) : duration); } };
video.onended = () => setPlaying(false);
video.onpause = () => setPlaying(false);
video.onplay = () => setPlaying(true);
video.onseeking = () => { canvas.style.visibility = 'hidden'; $('game-notices').style.visibility = 'hidden'; $('observation-tag').style.visibility = 'hidden'; };
video.onseeked = () => { canvas.style.visibility = ''; $('game-notices').style.visibility = ''; $('observation-tag').style.visibility = ''; playhead = video.currentTime; const index = indexAt(events, playhead, media); if (index !== activeIndex) render(index); updateClock(); draw(); };
async function guarded(fn) { try { await fn(); } catch (e) { text('save-result', e.message); } }
$('apply-json').onclick = () => guarded(async () => { config = JSON.parse($('config-json').value); config.layout.calibrated = false; $('confirmed').checked = false; regionOptions(); });
$('inspect').onclick = () => guarded(async () => text('inspection', JSON.stringify(await post('/api/inspect', {config}), null, 2)));
$('template').onclick = () => guarded(async () => { config = await post('/api/template', {config, slot: Number($('slot').value), card: $('card').value}); syncConfig(); text('save-result', 'Card artwork saved.'); });
$('save').onclick = () => guarded(async () => { const result = await post('/api/save', {config, confirmed: $('confirmed').checked}); config = result.config; syncConfig(); text('save-result', `Saved ${result.saved}`); });
new ResizeObserver(fitStage).observe(document.querySelector('.canvas-wrap'));
async function main() {
  if (location.protocol === 'file:') {
    text('empty-message', 'Start the viewer with: uv run clash-jev view runs/qwen-demo-verified --port 8767');
    text('trace-label', 'Open http://127.0.0.1:8767 — this page needs the local viewer server.'); return;
  }
  ({mode} = await get('/api/info'));
  if (mode === 'live') {
    isLive = true; mode = 'viewer'; runMode = 'live';
    $('playback').hidden = true; $('live-controls').hidden = false;
    $('live-screen').hidden = false;
    text('empty-message', 'Buffering 5 seconds of gameplay…');
    const data = await get('/api/live'); config = data.manifest.config;
    const size = config.layout.reference_size ?? [1440, 2560];
    canvas.width = size[0]; canvas.height = size[1]; fitStage();
    await pollLive(); streamLiveVideo(); requestAnimationFrame(tick);
    return;
  }
  if (mode === 'calibrate') {
    document.body.classList.add('calibrating');
    $('viewer').hidden = true; $('calibrator').hidden = false; $('playback').hidden = true; $('arena-legend').hidden = true; $('overlay-controls').hidden = true;
    text('canvas-title', 'Drag to select a region'); text('source-label', 'LOCAL CALIBRATION');
    config = await get('/api/config'); regionOptions(); await loadImage('/capture.png'); config.layout.reference_size = [canvas.width, canvas.height]; syncConfig();
  } else {
    const manifest = await get('/api/manifest'); runMode = manifest.mode; config = manifest.config;
    if (!config) throw Error('No manifest found. Start a run, then open its viewer.');
    media = await get('/api/media'); hasVideo = Boolean(media.url && Number.isFinite(media.elapsed_offset_s) && Number.isFinite(media.capture_clock_offset_s));
    const size = config.layout.reference_size ?? [1440, 2560]; canvas.width = size[0]; canvas.height = size[1]; $('game-stage').style.aspectRatio = `${size[0]} / ${size[1]}`;
    fitStage();
    if (hasVideo) {
      video.hidden = false; video.src = media.url;
      try { await new Promise((resolve, reject) => { video.onloadedmetadata = resolve; video.onerror = () => reject(Error('Video could not be loaded')); }); }
      catch (error) { hasVideo = false; video.hidden = true; toast(`${error.message}; using recorded frames`); }
    }
    text('source-label', hasVideo ? 'VIDEO + TELEMETRY' : 'CAPTURED FRAMES');
    await refresh(); setInterval(() => refresh().catch(e => text('trace-label', `Viewer connection: ${e.message}`)), 1500);
    requestAnimationFrame(tick);
  }
}
main().catch(e => { text('empty-message', e.message); text('trace-label', 'Viewer unavailable'); });
