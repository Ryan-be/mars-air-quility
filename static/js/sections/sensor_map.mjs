// Mirror of mlss_monitor/routes/api_inferences.py::_RULE_CHANNEL_MAP — used
// by Storyline (which sensor lane?) and Co-occurrence (which node?).
//
// Keep this file in sync with the backend map — when a new event_type is
// added to the backend, both files must list it.

const _RULE_CHANNEL_MAP = {
  high_tvoc:         'tvoc_ppb',
  tvoc_spike:        'tvoc_ppb',
  high_eco2:         'eco2_ppm',
  eco2_elevated:     'eco2_ppm',
  eco2_danger:       'eco2_ppm',
  high_temperature:  'temperature_c',
  low_temperature:   'temperature_c',
  high_humidity:     'humidity_pct',
  low_humidity:      'humidity_pct',
  humidity_low:      'humidity_pct',
  rapid_humidity_change: 'humidity_pct',
  vpd_high:          'humidity_pct',  // VPD is humidity-derived
  high_pm25:         'pm25_ug_m3',
  high_pm10:         'pm10_ug_m3',
  high_co:           'co_ppb',
  high_no2:          'no2_ppb',
  high_nh3:          'nh3_ppb',
};

const _ANOMALY_PREFIX = 'anomaly_';

// Display order top-to-bottom in Storyline lanes.
export const ALL_CHANNELS = [
  'tvoc_ppb',
  'eco2_ppm',
  'co_ppb',
  'pm25_ug_m3',
  'humidity_pct',
  'temperature_c',
];

export function primaryChannel(eventType) {
  if (!eventType) return null;
  if (_RULE_CHANNEL_MAP[eventType]) return _RULE_CHANNEL_MAP[eventType];
  if (eventType.startsWith(_ANOMALY_PREFIX)) {
    const stem = eventType.slice(_ANOMALY_PREFIX.length);
    if (ALL_CHANNELS.includes(stem)) return stem;
  }
  return null;
}

export const CHANNEL_LABEL = {
  tvoc_ppb:       'TVOC',
  eco2_ppm:       'eCO₂',
  co_ppb:         'CO',
  pm25_ug_m3:     'PM₂.₅',
  pm10_ug_m3:     'PM₁₀',
  humidity_pct:   'Humid',
  temperature_c:  'Temp',
  no2_ppb:        'NO₂',
  nh3_ppb:        'NH₃',
};
