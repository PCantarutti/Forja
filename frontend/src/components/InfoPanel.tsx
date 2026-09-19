import { useState } from "react";
import type { Settings, ToolsSent } from "../types";
import { Chevron, Wrench } from "./icons";

export type ToolInfo = { name: string; mutating: boolean; always_ask?: boolean; source?: string };
export type McpStatus = {
  config: string;
  config_error: string;
  servers: { name: string; status: string; error: string; tools: string[]; transport: string }[];
};

const VIA: Record<string, string> = {
  native: "nativo (campo tools da API)",
  prompt: "texto (schema no system prompt)",
  none: "nenhuma",
};

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 py-0.5">
      <span className="text-faint">{k}</span>
      <span className="truncate text-right text-fg">{children}</span>
    </div>
  );
}

function Badge({ t }: { t: ToolInfo }) {
  if (t.always_ask) return <span className="rounded bg-raised px-1.5 text-[10px] text-amber-200">sempre pergunta</span>;
  if (t.mutating) return <span className="rounded bg-raised px-1.5 text-[10px] text-muted">escrita</span>;
  return null;
}

function ToolRow({ t, label }: { t: ToolInfo; label?: string }) {
  return (
    <li className="flex items-center justify-between gap-2 font-mono">
      <span className="flex min-w-0 items-center gap-1.5 text-fg" title={t.name}>
        <Wrench className="size-3 shrink-0 text-faint" /> <span className="truncate">{label ?? t.name}</span>
      </span>
      <Badge t={t} />
    </li>
  );
}

/** Ferramentas nativas listadas uma a uma; as de MCP agrupadas por servidor (recolhíveis). */
function Tools({ list, info }: { list: { name: string; mutating: boolean }[]; info: Map<string, ToolInfo> }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!list.length) return <div className="text-muted">Nenhuma ferramenta.</div>;
  const full = list.map((t) => ({ ...t, ...info.get(t.name) }));
  const builtin = full.filter((t) => !t.name.startsWith("mcp__"));
  const groups = new Map<string, ToolInfo[]>();
  for (const t of full.filter((t) => t.name.startsWith("mcp__"))) {
    const server = t.name.split("__")[1];
    groups.set(server, [...(groups.get(server) ?? []), t]);
  }
  return (
    <ul className="space-y-1">
      {builtin.map((t) => (
        <ToolRow key={t.name} t={t} />
      ))}
      {[...groups].map(([server, tools]) => (
        <li key={server}>
          <button
            onClick={() => setOpen((o) => ({ ...o, [server]: !o[server] }))}
            className="flex w-full items-center justify-between font-mono text-fg hover:text-white"
          >
            <span>
              <span className="text-faint">mcp · </span>
              {server} <span className="text-faint">({tools.length})</span>
            </span>
            <Chevron className="size-3 text-faint" />
          </button>
          {open[server] && (
            <ul className="mt-1 space-y-1 border-l border-line pl-2.5">
              {tools.map((t) => (
                <ToolRow key={t.name} t={t} label={t.name.split("__").slice(2).join("__")} />
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-3.5">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[11px] font-medium tracking-wider text-faint uppercase">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

const DOT: Record<string, string> = {
  connected: "text-emerald-400",
  connecting: "text-sky-400 animate-pulse",
  error: "text-red-400",
  stopped: "text-faint",
};

export default function InfoPanel(props: {
  settings: Settings;
  toolMode: string;
  onToolMode: (m: string) => void;
  allTools: ToolInfo[];
  sent: ToolsSent | null;
  mcp: McpStatus | null;
  onReloadMcp: () => void;
}) {
  const { settings, sent, mcp } = props;
  const agent = settings.mode === "agent";
  const nextVia = !agent ? "none" : props.toolMode === "text" ? "prompt" : "native";
  const info = new Map(props.allTools.map((t) => [t.name, t]));

  return (
    <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l border-line bg-bg p-3 text-xs">
      <Section title="Estado">
        <Row k="Modo">{agent ? "Agente" : "Chat"}</Row>
        <Row k="Provider">{settings.provider}</Row>
        <Row k="Modelo">
          <span title={settings.model}>{settings.model || "—"}</span>
        </Row>
        <Row k="Escrita">{settings.writePolicy === "ask" ? "perguntar" : "automática"}</Row>
        <label className="mt-2 flex items-center justify-between gap-2">
          <span className="text-faint">Tool calling do modelo</span>
          <select
            value={props.toolMode}
            onChange={(e) => props.onToolMode(e.target.value)}
            disabled={!settings.model}
            className="rounded-md border border-line bg-raised px-1.5 py-0.5 text-fg"
          >
            <option value="auto">auto</option>
            <option value="native">native</option>
            <option value="text">text</option>
          </select>
        </label>
      </Section>

      <Section title="Enviadas na última requisição">
        {sent ? (
          <>
            <div className="mb-2 text-muted">
              {sent.model} · via {VIA[sent.via]} · {sent.tools.length} ferramentas
            </div>
            <Tools list={sent.tools} info={info} />
          </>
        ) : (
          <div className="text-muted">Nenhuma requisição nesta sessão ainda.</div>
        )}
      </Section>

      <Section title="Próxima requisição enviará">
        <div className="mb-2 text-muted">
          via {VIA[nextVia]}
          {agent && ` · ${props.allTools.length} ferramentas`}
        </div>
        <Tools list={agent ? props.allTools : []} info={info} />
        {!agent && <div className="mt-1 text-muted">Modo Chat não envia ferramentas. Troque para Agente.</div>}
      </Section>

      <Section
        title="Servidores MCP"
        action={
          <button onClick={props.onReloadMcp} className="rounded-md px-1.5 py-0.5 text-muted hover:bg-raised hover:text-fg">
            recarregar
          </button>
        }
      >
        {mcp?.config_error && <div className="mb-2 text-red-300">{mcp.config_error}</div>}
        {mcp && mcp.servers.length ? (
          <ul className="space-y-1.5">
            {mcp.servers.map((s) => (
              <li key={s.name}>
                <div className="flex items-center justify-between">
                  <span className="text-fg">
                    <span className={DOT[s.status] ?? "text-faint"}>●</span> {s.name}
                  </span>
                  <span className="text-faint">
                    {s.transport} · {s.status === "connected" ? `${s.tools.length} tools` : s.status}
                  </span>
                </div>
                {s.error && <div className="mt-0.5 break-words text-red-300">{s.error}</div>}
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-muted">
            Nenhum servidor. Crie <span className="font-mono">config/mcp.json</span> (veja o exemplo) e clique em
            recarregar.
          </div>
        )}
      </Section>
    </aside>
  );
}
