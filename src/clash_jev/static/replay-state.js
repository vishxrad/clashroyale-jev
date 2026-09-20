/**
 * Share pure formatting and timeline helpers between live and recorded views.
 * Map capture and action timestamps to the displayed frame, filter unit labels,
 * and derive visible events without changing the recorded state or bot decisions.
 */
export const pretty = value => String(value ?? 'unknown').replaceAll('_', ' ');
export const title = value => pretty(value).replace(/\b\w/g, c => c.toUpperCase());
// Presentation preference: retain raw detections in recorded events and bot state.
export function unitLabelVisible(type, allowedTypes = null) {
  const name = String(type ?? '').toLowerCase().replace(/[^a-z]/g, '');
  if (Array.isArray(allowedTypes)) {
    const normalize = value => String(value).toLowerCase().replace(/[^a-z]/g, '').replace(/s$/, '');
    return allowedTypes.some(allowed => normalize(allowed) === normalize(type));
  }
  return !name.includes('goblin') && !name.includes('hogrider');
}
export const clock = seconds => `${Math.floor(Math.max(0, seconds) / 60)}:${String(Math.floor(Math.max(0, seconds) % 60)).padStart(2, '0')}`;
export const eventTime = (event, media = {}) => Math.max(0, (event.elapsed_s ?? 0) + (media.elapsed_offset_s ?? 0));
export function indexAt(events, time, media = {}) {
  let index = -1;
  for (let i = 0; i < events.length; i++) {
    if (eventTime(events[i], media) > time + 0.001) break;
    index = i;
  }
  return index;
}
export function captureTime(event, media = {}) {
  if (event?.state && Number.isFinite(media.capture_clock_offset_s)) return event.state.captured_at + media.capture_clock_offset_s;
  return eventTime(event ?? {}, media) - (event?.latency_ms?.total ?? 0) / 1000;
}
export function observationIndexAt(events, time, media = {}) {
  let index = -1;
  for (let i = 0; i < events.length; i++) {
    if (captureTime(events[i], media) <= time + .001) index = i;
  }
  return index;
}
export function placement(action) {
  if (!action?.position) return 'Observe the next frame';
  const prefix = `PLAY_${action.slot}_${action.card}_`;
  return action.id.startsWith(prefix) ? title(action.id.slice(prefix.length)) : `(${action.position.x.toFixed(2)}, ${action.position.y.toFixed(2)})`;
}
export function deriveLog(events, controls, media = {}, allowedTypes = null) {
  const log = [];
  let previous = null;
  const add = (time, kind, heading, detail, tone = '', action = null, overlayHeading = heading) => log.push({time, kind, heading, detail, tone, action, overlayHeading});
  for (const e of events) {
    const time = eventTime(e, media), state = e.state;
    const observed = media.align_observations ? captureTime(e, media) : time;
    if (state?.battle_active) {
      if (!previous) add(observed, 'arena', 'Battlefield acquired', 'Arena state and local HUD are available.');
      const oldTracks = new Set((previous?.units ?? []).map(u => u.track_id));
      const fresh = (state.units ?? []).filter(u => !oldTracks.has(u.track_id));
      // Group per observation so a crowded arena cannot flood the presentation.
      for (const team of ['enemy', 'ally', 'unknown']) {
        const found = fresh.filter(u => u.team === team);
        if (!found.length) continue;
        const types = [...new Set(found.map(u => title(u.type)))];
        const labelTypes = types.filter(type => unitLabelVisible(type, allowedTypes));
        const overlayHeading = labelTypes.length ? `${title(team)} detected · ${labelTypes.slice(0, 2).join(', ')}${labelTypes.length > 2 ? ` +${labelTypes.length - 2}` : ''}` : null;
        add(observed, 'detected', `${title(team)} detected · ${types.slice(0, 2).join(', ')}${types.length > 2 ? ` +${types.length - 2}` : ''}`, `${found.length} new track${found.length === 1 ? '' : 's'} · ${e.mode === 'offline-fixture' ? 'fixture annotation' : 'Qwen estimate'}`, team === 'enemy' ? 'enemy' : '', null, overlayHeading);
      }
      for (const tower of state.towers ?? []) {
        const before = previous?.towers?.find(t => t.id === tower.id);
        if (before && !before.destroyed && tower.destroyed) add(observed, 'tower', `${title(tower.id)} reported destroyed`, 'Reported by battlefield perception.', 'enemy');
        else if (before && Number.isFinite(before.hp) && Number.isFinite(tower.hp) && tower.hp < before.hp) add(observed, 'tower', `${title(tower.id)} · ${tower.hp} HP`, `Observed HP ${before.hp} → ${tower.hp}`, tower.id.startsWith('enemy') ? 'enemy' : '');
      }
      previous = state;
    }
    if (e.action?.card) {
      const sent = e.status === 'sent';
      add(time, sent ? 'sent' : 'choice', `${title(e.action.card)} → ${placement(e.action)}`, sent ? `Slot ${e.action.slot + 1} · ${e.action.cost} elixir · taps sent` : `${pretty(e.status)} · selected by ${e.decision?.source ?? 'policy'}`, 'action', e.action);
    } else if (e.status === 'wait' && e.decision?.source === 'jev') {
      add(time, 'choice', 'Wait & observe', `${state?.hud?.elixir ?? '?'} elixir · Jev selected WAIT`);
    }
    if (e.status?.includes('error') || e.status?.includes('unconfirmed') || e.status?.startsWith('rejected')) add(time, 'controller', pretty(e.status), e.error ?? 'See the recorded event for details.', 'error');
  }
  for (const control of controls) {
    if (control.status !== 'confirmed') continue;
    const observation = controls.find(c => c.frame_id === control.frame_id && c.status === 'confirmation_observation');
    // Never reveal a confirmation before its observation or the next recorded frame.
    const next = events.find(e => e.frame_id >= control.frame_id);
    const time = Number.isFinite(control.elapsed_s) ? eventTime(control, media) : observation && Number.isFinite(media.capture_clock_offset_s) ? observation.captured_at + media.capture_clock_offset_s : next ? eventTime(next, media) : Infinity;
    add(time, 'confirmed', `${title(control.card)} deployment confirmed`, 'Card replacement + elixir drop observed.', '', control);
  }
  return log.sort((a, b) => a.time - b.time);
}
export function visibleLog(log, time) { return log.filter(item => item.time <= time + .001); }
