// Lightweight inline SVG icon set, AstroUX-flavored.
// Stroke-based, 1.5px, currentColor — match Astro's UI tone.

const __strokeIc = (path) => ({ size = 16, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="1.6"
       strokeLinecap="round" strokeLinejoin="round" {...rest}>
    {path}
  </svg>
);

const Icons = {
  hub: __strokeIc(
    <>
      <circle cx="12" cy="12" r="3" />
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
      <path d="M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    </>,
  ),
  grow: __strokeIc(
    <>
      <path d="M12 21V10" />
      <path d="M12 10c-3 0-5-2-5-5 3 0 5 2 5 5z" />
      <path d="M12 13c3 0 5-2 5-5-3 0-5 2-5 5z" />
      <path d="M8 21h8" />
    </>,
  ),
  fan: __strokeIc(
    <>
      <circle cx="12" cy="12" r="1.5" />
      <path d="M12 10.5c0-3 2-5 5-5 0 3-2 5-5 5z" />
      <path d="M12 13.5c0 3-2 5-5 5 0-3 2-5 5-5z" />
      <path d="M10.5 12c-3 0-5-2-5-5 3 0 5 2 5 5z" />
      <path d="M13.5 12c3 0 5 2 5 5-3 0-5-2-5-5z" />
    </>,
  ),
  ac: __strokeIc(
    <>
      <rect x="3" y="5" width="18" height="9" rx="1.5" />
      <path d="M7 10h10" />
      <path d="M8 14v2M12 14v3M16 14v2" />
    </>,
  ),
  heat: __strokeIc(
    <>
      <path d="M8 21c0-3 2-5 2-7s-2-3-2-5 2-3 2-3" />
      <path d="M14 21c0-3 2-5 2-7s-2-3-2-5 2-3 2-3" />
    </>,
  ),
  humidifier: __strokeIc(
    <>
      <path d="M12 3c-3 4-5 6.5-5 9a5 5 0 0010 0c0-2.5-2-5-5-9z" />
      <path d="M9 21h6" />
    </>,
  ),
  light: __strokeIc(
    <>
      <path d="M9 17h6" />
      <path d="M10 21h4" />
      <path d="M12 3a6 6 0 014 10.5c-.7.6-1 1.2-1 2V17H9v-1.5c0-.8-.3-1.4-1-2A6 6 0 0112 3z" />
    </>,
  ),
  pump: __strokeIc(
    <>
      <rect x="4" y="10" width="12" height="10" rx="1" />
      <circle cx="10" cy="15" r="2.5" />
      <path d="M16 12h4v3h-4" />
      <path d="M10 10V6h4" />
    </>,
  ),
  co2: __strokeIc(
    <>
      <path d="M9 9a4 4 0 100 6" />
      <circle cx="15" cy="12" r="3.5" />
      <text x="12" y="22" fontSize="6" textAnchor="middle"
            stroke="none" fill="currentColor" fontFamily="monospace">CO₂</text>
    </>,
  ),
  cog: __strokeIc(
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" />
    </>,
  ),
  close: __strokeIc(<><path d="M6 6l12 12M6 18l12-12" /></>),
  reset: __strokeIc(
    <>
      <path d="M4 4v6h6" />
      <path d="M20 12a8 8 0 11-3-6.2L20 8" />
    </>,
  ),
};

function effectorIcon(role) {
  switch (role) {
    case 'circulation': return Icons.fan;
    case 'cooling':     return Icons.ac;
    case 'heating':     return Icons.heat;
    case 'humidity':    return Icons.humidifier;
    case 'lighting':    return Icons.light;
    case 'irrigation':  return Icons.pump;
    case 'co2':         return Icons.co2;
    default:            return Icons.cog;
  }
}

Object.assign(window, { Icons, effectorIcon });
