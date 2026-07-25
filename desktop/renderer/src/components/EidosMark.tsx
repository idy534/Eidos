interface EidosMarkProps {
  className?: string;
  variant?: "mark" | "icon" | "hero";
}

export function EidosMark({ className, variant = "mark" }: EidosMarkProps) {
  if (variant === "hero") {
    return (
      <svg className={className} viewBox="0 0 512 512" focusable="false" aria-hidden="true">
        <defs>
          <linearGradient id="eidos-hero-bg" x1="256" y1="32" x2="256" y2="480" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#0F172A" />
            <stop offset="100%" stopColor="#1E293B" />
          </linearGradient>
          <linearGradient id="eidos-hero-bezel" x1="32" y1="32" x2="480" y2="480" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#475569" stopOpacity="0.6" />
            <stop offset="100%" stopColor="#0F172A" stopOpacity="0.8" />
          </linearGradient>
          <linearGradient id="eidos-hero-mark" x1="80" y1="80" x2="432" y2="432" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#FFFFFF" />
            <stop offset="100%" stopColor="#F1F5F9" />
          </linearGradient>
          <filter id="eidos-hero-shadow" x="-10%" y="-10%" width="120%" height="120%">
            <feDropShadow dx="0" dy="16" stdDeviation="20" floodColor="#000000" floodOpacity="0.45" />
            <feDropShadow dx="0" dy="4" stdDeviation="6" floodColor="#0F172A" floodOpacity="0.3" />
          </filter>
        </defs>

        {/* Ambient Squircle Surface */}
        <g filter="url(#eidos-hero-shadow)">
          <rect x="32" y="32" width="448" height="448" rx="100" fill="url(#eidos-hero-bg)" />
          <rect
            x="32.5"
            y="32.5"
            width="447"
            height="447"
            rx="99.5"
            fill="none"
            stroke="url(#eidos-hero-bezel)"
            strokeWidth="1.5"
          />
        </g>

        {/* Eidos Geometric Mark */}
        <g id="eidos-hero-mark-group" transform="translate(64, 64) scale(0.75)">
          <path
            fill="url(#eidos-hero-mark)"
            fillRule="evenodd"
            clipRule="evenodd"
            d="
              M 116 80
              L 396 80
              A 36 36 0 0 1 432 116
              L 432 348
              L 348 432
              L 116 432
              A 36 36 0 0 1 80 396
              L 80 116
              A 36 36 0 0 1 116 80
              Z
              M 432 172
              L 224 172
              A 24 24 0 0 0 224 220
              L 432 220
              Z
              M 432 280
              L 224 280
              A 24 24 0 0 0 224 328
              L 432 328
              Z
            "
          />
        </g>
      </svg>
    );
  }

  if (variant === "icon") {
    return (
      <svg className={className} viewBox="0 0 512 512" focusable="false" aria-hidden="true">
        <defs>
          <linearGradient id="eidos-icon-bg" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#0F172A" />
            <stop offset="100%" stopColor="#1E293B" />
          </linearGradient>
        </defs>

        <rect x="32" y="32" width="448" height="448" rx="100" fill="url(#eidos-icon-bg)" />
        <g transform="translate(64, 64) scale(0.75)">
          <path
            fill="#FFFFFF"
            fillRule="evenodd"
            clipRule="evenodd"
            d="
              M 116 80
              L 396 80
              A 36 36 0 0 1 432 116
              L 432 348
              L 348 432
              L 116 432
              A 36 36 0 0 1 80 396
              L 80 116
              A 36 36 0 0 1 116 80
              Z
              M 432 172
              L 224 172
              A 24 24 0 0 0 224 220
              L 432 220
              Z
              M 432 280
              L 224 280
              A 24 24 0 0 0 224 328
              L 432 328
              Z
            "
          />
        </g>
      </svg>
    );
  }

  return (
    <svg className={className} viewBox="0 0 512 512" focusable="false" aria-hidden="true">
      <path
        fill="currentColor"
        fillRule="evenodd"
        clipRule="evenodd"
        d="
          M 116 80
          L 396 80
          A 36 36 0 0 1 432 116
          L 432 348
          L 348 432
          L 116 432
          A 36 36 0 0 1 80 396
          L 80 116
          A 36 36 0 0 1 116 80
          Z
          M 432 172
          L 224 172
          A 24 24 0 0 0 224 220
          L 432 220
          Z
          M 432 280
          L 224 280
          A 24 24 0 0 0 224 328
          L 432 328
          Z
        "
      />
    </svg>
  );
}
