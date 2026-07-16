export function EidosMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 128 128" focusable="false" aria-hidden="true">
      <defs>
        <linearGradient id="eidos-mark-ink" x1="20" y1="20" x2="98" y2="108" gradientUnits="userSpaceOnUse">
          <stop stopColor="#171a25" />
          <stop offset="0.62" stopColor="#252b42" />
          <stop offset="1" stopColor="#343e89" />
        </linearGradient>
        <linearGradient id="eidos-mark-signal" x1="50" y1="80" x2="108" y2="80" gradientUnits="userSpaceOnUse">
          <stop stopColor="#24aaff" />
          <stop offset="0.52" stopColor="#5140ff" />
          <stop offset="1" stopColor="#c342f0" />
        </linearGradient>
      </defs>
      <path d="M94 36C78 19 51 17 33 34C13 53 17 84 39 99C56 111 79 106 93 92" fill="none" stroke="url(#eidos-mark-ink)" strokeLinecap="round" strokeWidth="17" />
      <path d="M52 80H106" fill="none" stroke="url(#eidos-mark-signal)" strokeLinecap="round" strokeWidth="15" />
    </svg>
  );
}
