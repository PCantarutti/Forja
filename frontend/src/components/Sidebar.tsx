import { useState } from "react";
import type { Conversation } from "../types";
import { Edit, Search, Trash } from "./icons";

export default function Sidebar(props: {
  conversations: Conversation[];
  current: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
}) {
  const [q, setQ] = useState("");
  const list = props.conversations.filter((c) => c.title.toLowerCase().includes(q.toLowerCase()));

  return (
    <aside className="flex w-64 shrink-0 flex-col bg-side">
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-2">
        <div className="grid size-7 place-items-center rounded-full bg-fg text-sm font-bold text-black">F</div>
        <span className="font-medium">Forja</span>
        <button onClick={props.onNew} title="Nova conversa" className="ml-auto rounded-lg p-1.5 text-muted hover:bg-raised hover:text-fg">
          <Edit />
        </button>
      </div>
      <label className="mx-3 mt-2 mb-3 flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted focus-within:bg-surface">
        <Search />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar"
          className="w-full bg-transparent text-fg placeholder:text-muted focus:outline-none"
        />
      </label>
      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        {list.map((c) => (
          <div
            key={c.id}
            onClick={() => props.onSelect(c.id)}
            className={`group flex cursor-pointer items-center rounded-lg px-3 py-2 text-sm ${
              c.id === props.current ? "bg-raised text-fg" : "text-muted hover:bg-surface hover:text-fg"
            }`}
          >
            <span className="flex-1 truncate">{c.title}</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (confirm(`Apagar "${c.title}"?`)) props.onDelete(c.id);
              }}
              className="hidden text-faint group-hover:block hover:text-red-400"
              title="Apagar"
            >
              <Trash className="size-3.5" />
            </button>
          </div>
        ))}
      </nav>
    </aside>
  );
}
