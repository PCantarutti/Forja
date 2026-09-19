// Ícones SVG inline (traço 1.8, herdam currentColor).
type P = { className?: string };
const base = (d: React.ReactNode) =>
  function Icon({ className = "size-4" }: P) {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
        {d}
      </svg>
    );
  };

export const Copy = base(<><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>);
export const Check = base(<path d="m5 12 5 5 9-10" />);
export const Brain = base(<><path d="M9 4a3 3 0 0 0-3 3v.5A3 3 0 0 0 4 10.5a3 3 0 0 0 1 2.2A3 3 0 0 0 6 18a3 3 0 0 0 3 2 3 3 0 0 0 3-3V7a3 3 0 0 0-3-3z" /><path d="M15 4a3 3 0 0 1 3 3v.5a3 3 0 0 1 2 3 3 3 0 0 1-1 2.2 3 3 0 0 1-1 5.3 3 3 0 0 1-3 2 3 3 0 0 1-3-3" /></>);
export const Chevron = base(<path d="m8 10 4-4 4 4M8 14l4 4 4-4" />);
export const ChevronDown = base(<path d="m6 9 6 6 6-6" />);
export const Cube = base(<><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></>);
export const Tokens = base(<><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /></>);
export const Clock = base(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>);
export const Gauge = base(<><path d="M4 16a8 8 0 1 1 16 0" /><path d="m12 16 4-5" /></>);
export const ArrowUp = base(<path d="M12 19V5m-6 6 6-6 6 6" />);
export const Square = ({ className = "size-3.5" }: P) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
    <rect x="5" y="5" width="14" height="14" rx="2.5" />
  </svg>
);
export const Edit = base(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></>);
export const Search = base(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>);
export const Trash = base(<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />);
export const Wrench = base(<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z" />);
