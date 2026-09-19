import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Approval, Message, Preview, ToolCall } from "../types";
import { Brain, Check, Chevron, Clock, Copy, Cube, Gauge, Tokens } from "./icons";

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md text-[15px]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function Thinking({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  if (!text) return null;
  const isOpen = open ?? !!live;
  return (
    <div className="mb-3 overflow-hidden rounded-2xl border border-line bg-surface">
      <button
        onClick={() => setOpen(!isOpen)}
        className="flex w-full items-center gap-2 px-4 py-2.5 font-mono text-sm text-muted hover:text-fg"
      >
        <Brain className={`size-4 ${live ? "animate-pulse" : ""}`} />
        {live ? "Raciocinando..." : "Raciocínio"}
        <Chevron className="ml-auto size-4" />
      </button>
      {isOpen && (
        <div className="max-h-80 overflow-y-auto border-t border-line px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap text-fg/85">
          {text}
        </div>
      )}
    </div>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      title="Copiar"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
      className="rounded-md p-1.5 text-faint hover:bg-raised hover:text-fg"
    >
      {done ? <Check /> : <Copy />}
    </button>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-raised px-2 py-0.5 text-xs text-muted">{children}</span>
  );
}

export type TurnStats = { model: string; tokens: number; seconds: number; tps: number | null; estimated: boolean };

export function StatsRow({ s }: { s: TurnStats }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
      <Chip>
        <Cube className="size-3.5" /> {s.model}
      </Chip>
      <span className="inline-flex items-center gap-1.5" title={s.estimated ? "estimado (chars/4)" : "informado pelo provider"}>
        <Tokens className="size-3.5" /> {s.estimated ? "~" : ""}
        {s.tokens.toLocaleString("pt-BR")} tokens
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Clock className="size-3.5" /> {s.seconds < 60 ? `${s.seconds.toFixed(1)}s` : `${Math.floor(s.seconds / 60)}m${Math.round(s.seconds % 60)}s`}
      </span>
      {s.tps != null && (
        <span className="inline-flex items-center gap-1.5">
          <Gauge className="size-3.5" /> {s.tps.toFixed(2)} t/s
        </span>
      )}
    </div>
  );
}

export function DiffView({ preview }: { preview: Preview }) {
  if (preview.kind === "command")
    return (
      <div className="overflow-hidden rounded-xl border border-line">
        <div className="bg-raised px-3 py-1.5 text-xs text-muted">
          Comando · <span className="font-mono">{preview.path}</span>
        </div>
        <pre className="max-h-64 overflow-auto bg-[#0d0d0d] px-3 py-2.5 font-mono text-xs whitespace-pre-wrap text-fg">
          <span className="text-faint select-none">$ </span>
          {preview.text}
        </pre>
      </div>
    );
  const lines = preview.kind === "new" ? preview.text.split("\n").map((l) => "+" + l) : preview.text.split("\n");
  return (
    <div className="overflow-hidden rounded-xl border border-line">
      <div className="bg-raised px-3 py-1.5 text-xs text-muted">
        {preview.kind === "new" ? "Arquivo novo" : "Diff"} · <span className="font-mono">{preview.path}</span>
      </div>
      <pre className="max-h-96 overflow-auto bg-[#0d0d0d] py-2 font-mono text-xs leading-5">
        {lines.map((l, i) => {
          const cls = l.startsWith("@@")
            ? "text-sky-400"
            : l.startsWith("+++") || l.startsWith("---")
              ? "text-faint"
              : l.startsWith("+")
                ? "bg-emerald-950/70 text-emerald-300"
                : l.startsWith("-")
                  ? "bg-red-950/70 text-red-300"
                  : "text-muted";
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
  ok: ["ok", "text-emerald-400"],
  erro: ["erro", "text-red-400"],
  rejeitada: ["rejeitada", "text-orange-400"],
  cancelada: ["cancelada", "text-faint"],
  aguardando: ["aguardando aprovação", "text-amber-300"],
  executando: ["executando…", "text-sky-400"],
  fila: ["na fila", "text-faint"],
  pendente: ["não executada", "text-faint"],
};

export function ToolBlock(props: {
  call: ToolCall;
  result?: Message;
  approval?: Approval;
  running: boolean;
  queued?: boolean; // uma chamada anterior da mesma resposta ainda não terminou
  onDecide: (approved: boolean) => void;
}) {
  const { call, result, approval, running, queued } = props;
  const waiting = approval !== undefined && !result;
  const status = result?.status ?? (waiting ? "aguardando" : running ? (queued ? "fila" : "executando") : "pendente");
  const [open, setOpen] = useState(false);
  const [label, cls] = STATUS[status];
  const preview: Preview | undefined = approval?.preview ?? result?.meta?.preview ?? undefined;
  const a = call.arguments;
  const hint = [a.path, a.command, a.query, a.url].find((v) => typeof v === "string") as string | undefined;
  const verb =
    { edit_file: "editar", write_file: "escrever", run_command: "executar um comando em" }[call.name] ?? `usar ${call.name}`;

  return (
    <div className={`my-2 overflow-hidden rounded-2xl border ${waiting ? "border-amber-500/50" : "border-line"} bg-surface`}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-raised/50">
        <span className="font-mono text-fg">{call.name}</span>
        <span className="truncate font-mono text-faint">{hint}</span>
        <span className={`ml-auto shrink-0 text-xs ${cls}`}>● {label}</span>
        <Chevron className="size-4 shrink-0 text-faint" />
      </button>

      {waiting && !approval?.sent && (
        <div className="space-y-3 border-t border-line p-4">
          <div className="text-sm text-fg">
            O agente quer {verb} <span className="font-mono">{preview?.path ?? ""}</span>
          </div>
          {preview ? (
            <DiffView preview={preview} />
          ) : (
            <pre className="max-h-64 overflow-auto rounded-xl border border-line bg-[#0d0d0d] p-3 font-mono text-xs whitespace-pre-wrap text-muted">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => props.onDecide(true)}
              className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white"
            >
              Aprovar
            </button>
            <button
              onClick={() => props.onDecide(false)}
              className="rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised"
            >
              Rejeitar
            </button>
          </div>
        </div>
      )}

      {open && (
        <div className="space-y-2 border-t border-line p-4 text-xs">
          <div className="text-faint">Argumentos</div>
          <pre className="max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono whitespace-pre-wrap text-muted">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          {preview && !waiting && <DiffView preview={preview} />}
          {result && (
            <>
              <div className="text-faint">Resultado</div>
              <pre className="max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono whitespace-pre-wrap text-muted">
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
  warning: "border-amber-500/30 text-amber-200",
  error: "border-red-500/30 text-red-200",
  nudge: "border-sky-500/30 text-sky-200",
  info: "border-line text-muted",
};

export function EventNotice({ m }: { m: Message }) {
  const kind = m.meta?.kind ?? "info";
  if (kind === "summary")
    return (
      <details className="my-3 rounded-2xl border border-line bg-surface px-4 py-2.5 text-sm text-muted">
        <summary className="cursor-pointer select-none">
          Contexto compactado: o histórico anterior foi resumido para caber na janela do modelo
        </summary>
        <div className="mt-2 border-t border-line pt-2">
          <Markdown text={m.content} />
        </div>
      </details>
    );
  const title = { warning: "Aviso", error: "Erro", nudge: "Lembrete automático ao modelo", info: "Info" }[kind as string];
  return (
    <div className={`my-3 rounded-2xl border bg-surface px-4 py-2.5 text-sm ${EVENT_STYLE[kind] ?? EVENT_STYLE.info}`}>
      <span className="font-medium">{title}:</span> {m.content}
    </div>
  );
}
