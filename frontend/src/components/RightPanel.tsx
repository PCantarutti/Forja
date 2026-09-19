import { useEffect, useRef, useState } from "react";
import { Globe, Info, PanelRight } from "./icons";

export type RightTab = "info" | "browser";

const MIN = 288; // = w-72, a largura antiga do painel
const KEY = "forja.right.width";

/** Coluna direita: abas Info / Navegador, redimensionável pela borda esquerda e recolhível numa faixa. */
export default function RightPanel(props: {
  tab: RightTab;
  onTab: (t: RightTab) => void;
  collapsed: boolean;
  onCollapse: (c: boolean) => void;
  browserOpen: boolean;
  children: React.ReactNode;
}) {
  const [width, setWidth] = useState(() => Math.max(MIN, Number(localStorage.getItem(KEY)) || MIN));
  const drag = useRef<{ x: number; w: number } | null>(null);

  useEffect(() => {
    localStorage.setItem(KEY, String(width));
  }, [width]);

  const tabs: { id: RightTab; label: string; icon: React.ReactNode }[] = [
    { id: "info", label: "Info", icon: <Info className="size-3.5" /> },
    { id: "browser", label: "Navegador", icon: <Globe className="size-3.5" /> },
  ];

  if (props.collapsed)
    return (
      <aside className="flex w-10 shrink-0 flex-col items-center gap-1 border-l border-line bg-bg py-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            title={t.label}
            onClick={() => {
              props.onTab(t.id);
              props.onCollapse(false);
            }}
            className="relative grid size-8 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg"
          >
            {t.icon}
            {t.id === "browser" && props.browserOpen && (
              <span className="absolute top-1 right-1 size-1.5 rounded-full bg-emerald-400" />
            )}
          </button>
        ))}
      </aside>
    );

  return (
    <aside className="relative flex shrink-0 flex-col border-l border-line bg-bg" style={{ width }}>
      <div
        title="Arraste para redimensionar"
        className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize hover:bg-fg/15"
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, w: width };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const next = drag.current.w + (drag.current.x - e.clientX);
          setWidth(Math.round(Math.max(MIN, Math.min(window.innerWidth * 0.7, next))));
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      />
      <div className="flex items-center gap-1 border-b border-line px-2 py-1.5 text-xs">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => props.onTab(t.id)}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 ${
              props.tab === t.id ? "bg-raised text-fg" : "text-muted hover:text-fg"
            }`}
          >
            {t.icon} {t.label}
            {t.id === "browser" && props.browserOpen && <span className="size-1.5 rounded-full bg-emerald-400" />}
          </button>
        ))}
        <button
          onClick={() => props.onCollapse(true)}
          title="Recolher painel"
          className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg"
        >
          <PanelRight className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">{props.children}</div>
    </aside>
  );
}
