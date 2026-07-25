interface EidosMarkProps {
  className?: string;
  variant?: "mark" | "icon" | "hero";
}

export function EidosMark({ className, variant = "mark" }: EidosMarkProps) {
  if (variant === "hero") {
    return (
      <svg className={className} viewBox="0 0 200 200" focusable="false" aria-hidden="true">
        <defs>
          <linearGradient id="eidos-hero-bg" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#3b6e57" />
            <stop offset="50%" stopColor="#295441" />
            <stop offset="100%" stopColor="#19382b" />
          </linearGradient>
          <linearGradient id="eidos-hero-arc" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="100%" stopColor="#b4eed2" />
          </linearGradient>
          <linearGradient id="eidos-hero-accent" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#6ee7b7" />
            <stop offset="100%" stopColor="#34d399" />
          </linearGradient>
          <filter id="eidos-hero-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="8" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        {/* Ambient Squircle Surface */}
        <rect x="16" y="16" width="168" height="168" rx="42" fill="url(#eidos-hero-bg)" />
        <rect
          x="16.5"
          y="16.5"
          width="167"
          height="167"
          rx="41.5"
          fill="none"
          stroke="rgba(255, 255, 255, 0.25)"
          strokeWidth="1"
        />

        {/* Dynamic Vector Mark */}
        <g transform="translate(50, 50)" filter="url(#eidos-hero-glow)">
          <path
            d="M 75 22 C 55 8 28 10 14 26 C -2 44 0 76 22 92 C 40 105 66 100 80 84"
            fill="none"
            stroke="url(#eidos-hero-arc)"
            strokeWidth="9"
            strokeLinecap="round"
          />
          <path
            d="M 32 55 H 86"
            fill="none"
            stroke="url(#eidos-hero-accent)"
            strokeWidth="8"
            strokeLinecap="round"
          />
          <circle cx="86" cy="55" r="4.5" fill="#ffffff" />
        </g>
      </svg>
    );
  }

  if (variant === "icon") {
    return (
      <svg className={className} viewBox="0 0 512 512" focusable="false" aria-hidden="true">
        <defs>
          <linearGradient id="eidos-icon-bg" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#34634e" />
            <stop offset="100%" stopColor="#1e3e30" />
          </linearGradient>
          <linearGradient id="eidos-icon-stroke" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="100%" stopColor="#a7f3d0" />
          </linearGradient>
        </defs>

        <rect x="32" y="32" width="448" height="448" rx="108" fill="url(#eidos-icon-bg)" />
        <rect
          x="33"
          y="33"
          width="446"
          height="446"
          rx="107"
          fill="none"
          stroke="rgba(255, 255, 255, 0.22)"
          strokeWidth="2"
        />

        <g transform="translate(128, 128) scale(2)">
          <path
            d="M 84 32 C 68 18 44 18 28 34 C 12 50 14 78 34 94 C 50 106 72 102 84 88"
            fill="none"
            stroke="url(#eidos-icon-stroke)"
            strokeLinecap="round"
            strokeWidth="12"
          />
          <path
            d="M 42 64 H 92"
            fill="none"
            stroke="#6ee7b7"
            strokeLinecap="round"
            strokeWidth="10"
          />
          <circle cx="92" cy="64" r="5" fill="#ffffff" />
        </g>
      </svg>
    );
  }

  return (
    <svg className={className} viewBox="0 0 128 128" focusable="false" aria-hidden="true">
      <defs>
        <linearGradient id="eidos-mark-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#3b6e57" />
          <stop offset="100%" stopColor="#244b39" />
        </linearGradient>
      </defs>
      <path
        d="M 92 34 C 75 18 48 18 31 34 C 14 50 16 80 37 96 C 53 108 76 103 89 90"
        fill="none"
        stroke="url(#eidos-mark-grad)"
        strokeLinecap="round"
        strokeWidth="13"
      />
      <path
        d="M 46 64 H 100"
        fill="none"
        stroke="#34d399"
        strokeLinecap="round"
        strokeWidth="11"
      />
      <circle cx="100" cy="64" r="4.5" fill="#244b39" />
    </svg>
  );
}


