import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Approval, Attachment, Message, Preview, ToolCall } from "../types";
import { Brain, Check, Chevron, Split, Clock, Copy, Cube, Gauge, Shield, Tokens, X } from "./icons";

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

// Anexos e screenshots ficam na pasta de trabalho DA CONVERSA; o App avisa qual está aberta.
let fileConv = "0";
export const setFileConv = (conv: number | null) => {
  fileConv = conv === null ? "0" : String(conv);
};
export const fileUrl = (a: Attachment) => `/api/files?path=${encodeURIComponent(a.path)}&conv=${fileConv}`;

/** Imagem em tela cheia; clique (ou Esc) fecha. */
export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div onClick={onClose} className="fixed inset-0 z-50 grid cursor-zoom-out place-items-center bg-black/85 p-4">
      <img src={src} alt="" className="max-h-full max-w-full rounded-lg shadow-2xl" />
    </div>
  );
}

/** Screenshot devolvido por uma ferramenta: grande no chat, clique abre em tela cheia. */
export function ToolImages({ list }: { list: Attachment[] }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const images = list.filter((a) => a.kind === "image");
  if (!images.length) return null;
  return (
    <div className="space-y-2 border-t border-line p-3">
      {images.map((a) => (
        <img
          key={a.path}
          src={fileUrl(a)}
          alt={a.name}
          title="Clique para ampliar"
          onClick={() => setZoom(fileUrl(a))}
          className="block w-full cursor-zoom-in rounded-xl border border-line bg-black"
        />
      ))}
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

/** Anexos de uma mensagem: miniatura para imagem (clique amplia), chip para o resto. */
export function Attachments({ list, onRemove }: { list: Attachment[]; onRemove?: (a: Attachment) => void }) {
  const [zoom, setZoom] = useState<string | null>(null);
  if (!list.length) return null;
  return (
    <div className="mt-2 flex flex-wrap justify-end gap-2">
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
      {list.map((a) => (
        <div key={a.path} className="relative">
          {a.kind === "image" ? (
            <img
              src={fileUrl(a)}
              alt={a.name}
              onClick={() => setZoom(fileUrl(a))}
              className="max-h-40 cursor-zoom-in rounded-xl border border-line object-cover"
            />
          ) : (
            <span className="inline-flex max-w-60 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-muted">
              <span className="truncate">{a.name}</span>
              <span className="text-faint">{Math.round(a.size / 1024)} KB</span>
            </span>
          )}
          {onRemove && (
            <button
              onClick={() => onRemove(a)}
              title="Remover"
              className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full bg-raised text-faint hover:text-fg"
            >
              <X className="size-3" />
            </button>
          )}
        </div>
      ))}
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
  onDecide: (approved: boolean, alwaysAllow?: boolean) => void;
  children?: React.ReactNode; // passos de um subagente (delegate_task)
}) {
  const { call, result, approval, running, queued } = props;
  const waiting = approval !== undefined && !result;
  const status = result?.status ?? (waiting ? "aguardando" : running ? (queued ? "fila" : "executando") : "pendente");
  const [open, setOpen] = useState(false);
  const [label, cls] = STATUS[status];
  const preview: Preview | undefined = approval?.preview ?? result?.meta?.preview ?? undefined;
  const a = call.arguments;
  const hint = (call.name === "delegate_task"
    ? `${a.level ?? ""} · ${a.task ?? ""}`
    : [a.path, a.command, a.query, a.url, a.selector, a.script].find((v) => typeof v === "string")) as string | undefined;
  const verb =
    {
      edit_file: "editar",
      write_file: "escrever",
      run_command: "executar um comando em",
      browser_click: "clicar em",
      browser_type: "digitar em",
      browser_eval: "executar JavaScript em",
    }[call.name] ?? `usar ${call.name}`;
  const target = preview?.path ?? (call.name.startsWith("browser_") ? hint : "");

  return (
    <div className={`my-2 overflow-hidden rounded-2xl border ${waiting ? "border-amber-500/50" : "border-line"} bg-surface`}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-raised/50">
        <span className="font-mono text-fg">{call.name}</span>
        <span className="truncate font-mono text-faint">{hint}</span>
        <span className={`ml-auto shrink-0 text-xs ${cls}`}>● {label}</span>
        <Chevron className="size-4 shrink-0 text-faint" />
      </button>

      {result?.meta?.attachments && <ToolImages list={result.meta.attachments} />}
      {props.children && <div className="border-t border-line px-3 py-2">{props.children}</div>}

      {waiting && !approval?.sent && (
        <div className="space-y-3 border-t border-line p-4">
          <div className="text-sm text-fg">
            O agente quer {verb} <span className="font-mono">{target}</span>
          </div>
          {preview ? (
            <DiffView preview={preview} />
          ) : (
            <pre className="max-h-64 overflow-auto rounded-xl border border-line bg-[#0d0d0d] p-3 font-mono text-xs whitespace-pre-wrap text-muted">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => props.onDecide(true)}
              className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white"
            >
              Aprovar
            </button>
            {approval?.suggest && (
              <button
                onClick={() => props.onDecide(true, true)}
                title={`Cria a regra "${approval.suggest}" em Configurações › Permissões`}
                className="inline-flex items-center gap-1.5 rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised"
              >
                <Shield className="size-3.5" /> Sempre permitir{" "}
                <span className="font-mono text-xs text-muted">{approval.suggest}</span>
              </button>
            )}
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
          {result?.meta?.auto_rule && (
            <div className="text-amber-200">Aprovada automaticamente pela regra: {result.meta.auto_rule}</div>
          )}
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


type SubStep = { call: ToolCall; result?: Message };

/** Passos de um subagente, desenhados dentro do bloco do delegate_task (aprovações inclusas). */
export function SubagentSteps(props: {
  info?: { level: string; model: string; tokens?: number; seconds?: number; fallback?: string };
  status?: string;
  steps: SubStep[];
  approvals: Record<string, Approval>;
  running: boolean;
  onDecide: (callId: string, approved: boolean, alwaysAllow?: boolean) => void;
}) {
  const { info } = props;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5">
          <Split className="size-3.5" /> subagente{info ? ` ${info.level === "capaz" ? "Capaz" : "Rápido"} · ${info.model}` : ""}
        </span>
        {info?.tokens != null && info.seconds != null && (
          <span className="text-faint">
            {info.tokens.toLocaleString("pt-BR")} tokens · {info.seconds}s · {props.steps.length} passos
          </span>
        )}
        {props.status && <span className="animate-pulse text-sky-300">{props.status}</span>}
      </div>
      {info?.fallback && <div className="text-xs text-amber-200">{info.fallback}</div>}
      {props.steps.map((st, k) => (
        <ToolBlock
          key={st.call.id}
          call={st.call}
          result={st.result}
          approval={props.approvals[st.call.id]}
          running={props.running}
          queued={props.steps.slice(0, k).some((p) => !p.result)}
          onDecide={(ok, always) => props.onDecide(st.call.id, ok, always)}
        />
      ))}
    </div>
  );
}
