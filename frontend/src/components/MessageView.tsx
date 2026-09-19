import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Message, Preview, ToolCall } from "../types";

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function Thinking({ text, live }: { text: string; live?: boolean }) {
  if (!text) return null;
  return (
    <details className="mb-2 text-sm text-zinc-500" open={live}>
      <summary className="cursor-pointer select-none">{live ? "Pensando…" : "Raciocínio"}</summary>
      <div className="mt-1 whitespace-pre-wrap border-l-2 border-zinc-800 pl-3">{text}</div>
    </details>
  );
}

export function DiffView({ preview }: { preview: Preview }) {
  const lines = preview.kind === "new" ? preview.text.split("\n").map((l) => "+" + l) : preview.text.split("\n");
  return (
    <div className="overflow-hidden rounded-md border border-zinc-800">
      <div className="bg-zinc-900 px-3 py-1 text-xs text-zinc-400">
        {preview.kind === "new" ? "Arquivo novo" : "Diff"} · <span className="font-mono">{preview.path}</span>
      </div>
      <pre className="max-h-96 overflow-auto bg-[#0d1117] py-2 font-mono text-xs leading-5">
        {lines.map((l, i) => {
          const cls = l.startsWith("@@")
            ? "text-sky-400"
            : l.startsWith("+++") || l.startsWith("---")
              ? "text-zinc-500"
              : l.startsWith("+")
                ? "bg-emerald-950/60 text-emerald-300"
                : l.startsWith("-")
                  ? "bg-red-950/60 text-red-300"
                  : "text-zinc-400";
          return (
            <div key={i} className={`px-3 whitespace-pre ${cls}`}>
              {l || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}

const STATUS: Record<string, [string, string]> = {
  ok: ["ok", "bg-emerald-900/60 text-emerald-300"],
  erro: ["erro", "bg-red-900/60 text-red-300"],
  rejeitada: ["rejeitada", "bg-orange-900/60 text-orange-300"],
  cancelada: ["cancelada", "bg-zinc-800 text-zinc-400"],
  aguardando: ["aguardando aprovação", "bg-amber-900/60 text-amber-300"],
  executando: ["executando…", "bg-sky-900/60 text-sky-300"],
  pendente: ["não executada", "bg-zinc-800 text-zinc-400"],
};

export function ToolBlock(props: {
  call: ToolCall;
  result?: Message;
  approval?: Preview | null;
  running: boolean;
  onDecide: (approved: boolean) => void;
}) {
  const { call, result, approval, running } = props;
  const waiting = approval !== undefined && !result;
  const status = result?.status ?? (waiting ? "aguardando" : running ? "executando" : "pendente");
  const [open, setOpen] = useState(false);
  const [label, cls] = STATUS[status];
  const preview: Preview | undefined = approval ?? result?.meta?.preview;
  const path = typeof call.arguments.path === "string" ? call.arguments.path : "";

  return (
    <div className={`my-2 rounded-lg border ${waiting ? "border-amber-600/70" : "border-zinc-800"} bg-zinc-900/40`}>
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-zinc-900"
      >
        <span className="text-zinc-500">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-amber-300">{call.name}</span>
        <span className="truncate font-mono text-zinc-400">{path}</span>
        <span className={`ml-auto shrink-0 rounded px-2 py-0.5 text-xs ${cls}`}>{label}</span>
      </button>

      {waiting && preview && (
        <div className="space-y-3 border-t border-zinc-800 p-3">
          <div className="text-sm font-medium text-amber-200">
            O agente quer {call.name === "edit_file" ? "editar" : "escrever"} <span className="font-mono">{preview.path}</span>
          </div>
          <DiffView preview={preview} />
          <div className="flex gap-2">
            <button
              onClick={() => props.onDecide(true)}
              className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
            >
              Aprovar
            </button>
            <button
              onClick={() => props.onDecide(false)}
              className="rounded-md bg-zinc-800 px-4 py-1.5 text-sm font-medium text-zinc-200 hover:bg-red-900"
            >
              Rejeitar
            </button>
          </div>
        </div>
      )}

      {open && (
        <div className="space-y-2 border-t border-zinc-800 p-3 text-xs">
          <div className="text-zinc-500">Argumentos</div>
          <pre className="max-h-64 overflow-auto rounded bg-zinc-950 p-2 font-mono whitespace-pre-wrap text-zinc-300">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          {preview && !waiting && <DiffView preview={preview} />}
          {result && (
            <>
              <div className="text-zinc-500">Resultado</div>
              <pre className="max-h-64 overflow-auto rounded bg-zinc-950 p-2 font-mono whitespace-pre-wrap text-zinc-300">
                {result.content}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const EVENT_STYLE: Record<string, string> = {
  warning: "border-amber-700/60 bg-amber-950/40 text-amber-200",
  error: "border-red-800/60 bg-red-950/40 text-red-200",
  nudge: "border-sky-800/60 bg-sky-950/30 text-sky-200",
  info: "border-zinc-700 bg-zinc-900 text-zinc-300",
};

export function EventNotice({ m }: { m: Message }) {
  const kind = m.meta?.kind ?? "info";
  const title = { warning: "Aviso", error: "Erro", nudge: "Lembrete automático ao modelo", info: "Info" }[kind as string];
  return (
    <div className={`my-2 rounded-md border px-3 py-2 text-sm ${EVENT_STYLE[kind] ?? EVENT_STYLE.info}`}>
      <span className="font-medium">{title}:</span> {m.content}
    </div>
  );
}
