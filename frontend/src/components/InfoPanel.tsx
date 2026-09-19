import type { Settings, ToolsSent } from "../types";
import { Wrench } from "./icons";

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

function Tools({ list }: { list: { name: string; mutating: boolean }[] }) {
  if (!list.length) return <div className="text-muted">Nenhuma ferramenta.</div>;
  return (
    <ul className="space-y-1">
      {list.map((t) => (
        <li key={t.name} className="flex items-center justify-between font-mono">
          <span className="flex items-center gap-1.5 text-fg">
            <Wrench className="size-3 text-faint" /> {t.name}
          </span>
          {t.mutating && <span className="rounded bg-raised px-1.5 text-[10px] text-muted">escrita</span>}
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-3.5">
      <h3 className="mb-2 text-[11px] font-medium tracking-wider text-faint uppercase">{title}</h3>
      {children}
    </section>
  );
}

export default function InfoPanel(props: {
  settings: Settings;
  toolMode: string;
  onToolMode: (m: string) => void;
  allTools: { name: string; mutating: boolean }[];
  sent: ToolsSent | null;
}) {
  const { settings, sent } = props;
  const agent = settings.mode === "agent";
  const nextVia = !agent ? "none" : props.toolMode === "text" ? "prompt" : "native";

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
              {sent.model} · via {VIA[sent.via]}
            </div>
            <Tools list={sent.tools} />
          </>
        ) : (
          <div className="text-muted">Nenhuma requisição nesta sessão ainda.</div>
        )}
      </Section>

      <Section title="Próxima requisição enviará">
        <div className="mb-2 text-muted">via {VIA[nextVia]}</div>
        <Tools list={agent ? props.allTools : []} />
        {!agent && <div className="mt-1 text-muted">Modo Chat não envia ferramentas. Troque para Agente.</div>}
      </Section>
    </aside>
  );
}
