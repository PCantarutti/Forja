import type { Settings, ToolsSent } from "../types";

const VIA: Record<string, string> = {
  native: "nativo (campo tools da API)",
  prompt: "texto (schema no system prompt)",
  none: "nenhuma",
};

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 py-0.5">
      <span className="text-zinc-500">{k}</span>
      <span className="truncate text-right text-zinc-200">{children}</span>
    </div>
  );
}

function Tools({ list }: { list: { name: string; mutating: boolean }[] }) {
  if (!list.length) return <div className="text-zinc-400">Nenhuma ferramenta.</div>;
  return (
    <ul className="space-y-1">
      {list.map((t) => (
        <li key={t.name} className="flex items-center justify-between font-mono">
          <span className="text-amber-300">{t.name}</span>
          {t.mutating && <span className="rounded bg-zinc-800 px-1.5 text-[10px] text-zinc-200">escrita</span>}
        </li>
      ))}
    </ul>
  );
}

export default function InfoPanel(props: {
  settings: Settings;
  toolMode: string;
  onToolMode: (m: string) => void;
  allTools: { name: string; mutating: boolean }[];
  sent: ToolsSent | null;
  ctx: { used: number; max: number | null; estimated: boolean } | null;
  numCtx: number;
}) {
  const { settings, sent, ctx } = props;
  const agent = settings.mode === "agent";
  const nextVia = !agent ? "none" : props.toolMode === "text" ? "prompt" : "native";
  const pct = ctx?.max ? Math.min(100, (ctx.used / ctx.max) * 100) : 0;

  return (
    <aside className="flex w-72 shrink-0 flex-col gap-5 overflow-y-auto border-l border-zinc-800 bg-zinc-950 p-4 text-xs">
      <section>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">Estado</h3>
        <Row k="Modo">{agent ? "Agente" : "Chat"}</Row>
        <Row k="Provider">{settings.provider}</Row>
        <Row k="Modelo">
          <span title={settings.model}>{settings.model || "—"}</span>
        </Row>
        <Row k="Escrita">{settings.writePolicy === "ask" ? "perguntar" : "automática"}</Row>
        <label className="mt-2 flex items-center justify-between gap-2">
          <span className="text-zinc-500">Tool calling deste modelo</span>
          <select
            value={props.toolMode}
            onChange={(e) => props.onToolMode(e.target.value)}
            disabled={!settings.model}
            className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-zinc-200"
          >
            <option value="auto">auto</option>
            <option value="native">native</option>
            <option value="text">text</option>
          </select>
        </label>
      </section>

      <section>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">
          Enviadas na última requisição
        </h3>
        {sent ? (
          <>
            <div className="mb-2 text-zinc-400">
              {sent.model} · via {VIA[sent.via]}
            </div>
            <Tools list={sent.tools} />
          </>
        ) : (
          <div className="text-zinc-400">Nenhuma requisição nesta sessão ainda.</div>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">
          Próxima requisição enviará
        </h3>
        <div className="mb-2 text-zinc-400">via {VIA[nextVia]}</div>
        <Tools list={agent ? props.allTools : []} />
        {!agent && <div className="mt-1 text-zinc-400">Modo Chat não envia ferramentas. Troque para Agente.</div>}
      </section>

      <section>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">Contexto</h3>
        {ctx ? (
          <>
            <div className="mb-1 h-2 overflow-hidden rounded bg-zinc-800">
              <div
                className={`h-full ${pct > 85 ? "bg-red-500" : pct > 60 ? "bg-amber-500" : "bg-emerald-500"}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="text-zinc-300">
              {ctx.estimated ? "~" : ""}
              {ctx.used.toLocaleString()} / {ctx.max ? ctx.max.toLocaleString() : "?"} tokens
            </div>
          </>
        ) : (
          <div className="text-zinc-400">Sem dados ainda.</div>
        )}
        <div className="mt-1 text-zinc-500">
          {settings.provider === "ollama"
            ? `num_ctx enviado ao Ollama: ${props.numCtx.toLocaleString()}`
            : "Janela definida ao carregar o modelo no LM Studio."}
        </div>
      </section>
    </aside>
  );
}
