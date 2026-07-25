interface EidosMarkProps {
  className?: string;
  variant?: "mark" | "icon";
}

export function EidosMark({ className, variant = "mark" }: EidosMarkProps) {
  if (variant === "icon") {
    return (
      <svg className={className} viewBox="0 0 512 512" focusable="false" aria-hidden="true">
        {/* Flat macOS Squircle Background */}
        <rect
          x="32"
          y="32"
          width="448"
          height="448"
          rx="105"
          fill="#244b39"
        />
        {/* Minimalist Flat Geometric Eidos Mark */}
        <g transform="translate(128, 128) scale(2)">
          <path
            d="M84 32 C68 18 44 18 28 34 C12 50 14 78 34 94 C50 106 72 102 84 88"
            fill="none"
            stroke="#ffffff"
            strokeLinecap="round"
            strokeWidth="13"
          />
          <path
            d="M44 64 H92"
            fill="none"
            stroke="#7ee4b7"
            strokeLinecap="round"
            strokeWidth="11"
          />
        </g>
      </svg>
    );
  }

  return (
    <svg className={className} viewBox="0 0 128 128" focusable="false" aria-hidden="true">
      {/* Minimalist Flat Vector Design Mark */}
      <path
        d="M92 34 C75 18 48 18 31 34 C14 50 16 80 37 96 C53 108 76 103 89 90"
        fill="none"
        stroke="#315f4a"
        strokeLinecap="round"
        strokeWidth="14"
      />
      <path
        d="M48 64 H102"
        fill="none"
        stroke="#4da17e"
        strokeLinecap="round"
        strokeWidth="12"
      />
    </svg>
  );
}

