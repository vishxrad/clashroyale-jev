import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
// Import as ESM without imposing a package.json on this Python project.
const source = await readFile(new URL('../src/clash_jev/static/replay-state.js', import.meta.url), 'utf8');
const {indexAt, observationIndexAt, eventTime, captureTime, deriveLog, visibleLog} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const media = {elapsed_offset_s: -1, capture_clock_offset_s: -100};
const events = [
  {frame_id: 1, elapsed_s: 5, latency_ms: {total: 3000}, state: {captured_at: 101, battle_active: true, units: [], towers: [], hud: {elixir: 7}}, status: 'sent', action: {id: 'PLAY_0_giant_left_bridge', card: 'giant', slot: 0, cost: 5, position: {x: .2, y: .58}}},
  {frame_id: 6, elapsed_s: 10, state: {captured_at: 107, battle_active: true, units: [{track_id: 'u1', type: 'knight', team: 'enemy'}], towers: [], hud: {elixir: 4}}, status: 'wait', decision: {source: 'jev'}},
];
test('playback reveals outputs only after processing, supports backward seeks', () => {
  assert.equal(captureTime(events[0], media), 1);
  assert.equal(eventTime(events[0], media), 4);
  assert.equal(indexAt(events, 3, media), -1);
  assert.equal(indexAt(events, 4, media), 0);
  assert.equal(indexAt(events, 9, media), 1);
  assert.equal(indexAt(events, 6, media), 0);
});
test('confirmations and detections cannot leak into earlier replay frames', () => {
  const controls = [{frame_id: 4, status: 'confirmation_observation', captured_at: 106}, {frame_id: 4, status: 'confirmed', card: 'giant'}];
  const log = deriveLog(events, controls, media);
  assert.equal(visibleLog(log, 3).length, 0);
  assert.equal(visibleLog(log, 5).some(x => x.kind === 'confirmed'), false);
  assert.equal(visibleLog(log, 6).some(x => x.kind === 'confirmed'), true);
  assert.equal(visibleLog(log, 8).some(x => x.kind === 'detected'), false);
  assert.equal(visibleLog(log, 9).filter(x => x.kind === 'detected').length, 1);
  assert.equal(visibleLog(log, 4).some(x => x.kind === 'confirmed'), false);
});
test('a vanished unit is not reported as killed and unchanged tracks do not retrigger', () => {
  const repeated = {...events[1], elapsed_s: 11};
  const disappeared = {...events[1], elapsed_s: 12, state: {...events[1].state, units: []}};
  const log = deriveLog([...events, repeated, disappeared], [], media);
  assert.equal(log.filter(x => x.kind === 'detected').length, 1);
  assert.equal(log.some(x => /killed|died/i.test(x.heading)), false);
});
test('buffered detections align with source frames while actions retain execution time', () => {
  const buffered = {...media, align_observations: true};
  assert.equal(observationIndexAt(events, .9, buffered), -1);
  assert.equal(observationIndexAt(events, 1, buffered), 0);
  assert.equal(indexAt(events, 1, buffered), -1);
  assert.equal(indexAt(events, 4, buffered), 0);
  assert.equal(observationIndexAt(events, 7, buffered), 1);
  assert.equal(indexAt(events, 7, buffered), 0);
  const log = deriveLog(events, [], buffered);
  assert.equal(visibleLog(log, 1).some(x => x.kind === 'sent'), false);
  assert.equal(visibleLog(log, 4).some(x => x.kind === 'sent'), true);
  assert.equal(visibleLog(log, 6.9).some(x => x.kind === 'detected'), false);
  assert.equal(visibleLog(log, 7).some(x => x.kind === 'detected'), true);
});
