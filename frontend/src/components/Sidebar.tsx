import type { Conversation } from "../types";

export default function Sidebar(props: {
  conversations: Conversation[];
  current: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
}) {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-lg font-semibold tracking-tight text-amber-400">Forja</span>
        <button
          onClick={props.onNew}
          className="rounded-md border border-zinc-700 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
        >
          + Nova
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {props.conversations.map((c) => (
          <div
            key={c.id}
            onClick={() => props.onSelect(c.id)}
            className={`group flex cursor-pointer items-center rounded-md px-2 py-1.5 text-sm ${
              c.id === props.current ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:bg-zinc-900"
            }`}
          >
            <span className="flex-1 truncate">{c.title}</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (confirm(`Apagar "${c.title}"?`)) props.onDelete(c.id);
              }}
              className="hidden px-1 text-zinc-500 group-hover:block hover:text-red-400"
              title="Apagar"
            >
              ×
            </button>
          </div>
        ))}
      </nav>
    </aside>
  );
}
