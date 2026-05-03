import { primaryChannel, ALL_CHANNELS, CHANNEL_LABEL } from '../../static/js/sections/sensor_map.mjs';

let failures = 0;
function expect(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) console.log(`  ok  ${label}`);
  else { console.log(`  FAIL ${label}\n    exp: ${e}\n    got: ${a}`); failures++; }
}

expect('high_tvoc → tvoc_ppb',         primaryChannel('high_tvoc'),         'tvoc_ppb');
expect('eco2_elevated → eco2_ppm',     primaryChannel('eco2_elevated'),     'eco2_ppm');
expect('humidity_low → humidity_pct',  primaryChannel('humidity_low'),      'humidity_pct');
expect('high_pm25 → pm25_ug_m3',       primaryChannel('high_pm25'),         'pm25_ug_m3');
expect('anomaly_tvoc_ppb → tvoc_ppb',  primaryChannel('anomaly_tvoc_ppb'),  'tvoc_ppb');
expect('unknown → null',               primaryChannel('mystery_event'),     null);
expect('null/undefined → null',        primaryChannel(undefined),           null);
expect('ALL_CHANNELS has six',         ALL_CHANNELS.length >= 6,            true);
expect('CHANNEL_LABEL covers TVOC',    CHANNEL_LABEL.tvoc_ppb,              'TVOC');

if (failures) { console.error(`${failures} failed`); process.exit(1); }
console.log('All sensor_map tests passed');
