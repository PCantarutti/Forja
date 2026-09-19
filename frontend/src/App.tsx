import { useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE } from "./api";
import Sidebar from "./components/Sidebar";
import InfoPanel, { type McpStatus, type ToolInfo } from "./components/InfoPanel";
import { CopyButton, EventNotice, Markdown, StatsRow, Thinking, ToolBlock, type TurnStats } from "./components/MessageView";
import { ArrowUp, ChevronDown, Cube, Square } from "./components/icons";
import type { Approval, Conversation, Message, Settings, Stats, ToolsSent } from "./types";

type Config = { providers: string[]; num_ctx: number };
type Live = {
  messages: Message[];
  run: { run_id: string; cursor: number; draft: { content: string; thinking: string } | null; sent: ToolsSent | null; approvals: { call: { id: string }; preview: any }[] } | null;
};

function loadSettings(): Settings {
  const def: Settings = { provider: "ollama", model: "", mode: "agent", writePolicy: "ask" };
  try {
    return { ...def, ...JSON.parse(localStorage.getItem("forja.settings") ?? "{}") };
  } catch {
    return def;
  }
}

const pill = "rounded-full border border-line bg-transparent px-3 py-1 text-xs text-muted hover:bg-raised";

function aggregate(list: Stats[]): TurnStats {
  const withTps = list.filter((s) => s.tps);
  const gen = withTps.reduce((a, s) => a + s.tokens / s.tps!, 0);
  return {
    model: list[list.length - 1].model,
    tokens: list.reduce((a, s) => a + s.tokens, 0),
    seconds: list.reduce((a, s) => a + s.seconds, 0),
    tps: gen > 0 ? withTps.reduce((a, s) => a + s.tokens, 0) / gen : null,
    estimated: list.some((s) => s.estimated),
  };
}

const fmt = (n: number) => n.toLocaleString("pt-BR");

export default function App() {
  const [config, setConfig] = useState<Config>({ providers: ["ollama", "lmstudio"], num_ctx: 32768 });
  const [allTools, setAllTools] = useState<ToolInfo[]>([]);
  const [mcp, setMcp] = useState<McpStatus | null>(null);
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState("");
  const [toolMode, setToolMode] = useState("auto");

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState<{ content: string; thinking: string } | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [approvals, setApprovals] = useState<Record<string, Approval>>({});
  const [sent, setSent] = useState<ToolsSent | null>(null);
  const [ctx, setCtx] = useState<{ used: number; max: number | null; estimated: boolean } | null>(null);
  const [error, setError] = useState("");
  const [input, setInput] = useState("");
  const runId = useRef<string | null>(null);
  const streamCtl = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const update = (p: Partial<Settings>) => setSettings((s) => ({ ...s, ...p }));

  useEffect(() => {
    localStorage.setItem("forja.settings", JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    api.get<Config>("/config").then(setConfig).catch(() => {});
    refreshTools();
    refreshConversations();
    // F5: volta para a conversa aberta e reconecta à execução, se houver.
    const saved = Number(localStorage.getItem("forja.current"));
    if (saved) openConversation(saved).catch(() => localStorage.removeItem("forja.current"));
  }, []);

  // Enquanto algum servidor MCP estiver conectando, atualiza status e ferramentas a cada 2s.
  useEffect(() => {
    if (!mcp?.servers.some((s) => s.status === "connecting")) return;
    const t = setTimeout(refreshTools, 2000);
    return () => clearTimeout(t);
  }, [mcp]);

  useEffect(() => {
    if (currentId === null) localStorage.removeItem("forja.current");
    else localStorage.setItem("forja.current", String(currentId));
  }, [currentId]);

  useEffect(() => {
    setModelsError("");
    api
      .get<{ models: string[] }>(`/models?provider=${settings.provider}`)
      .then(({ models }) => {
        setModels(models);
        if (!models.includes(settings.model)) update({ model: models[0] ?? "" });
      })
      .catch((e) => {
        setModels([]);
        setModelsError(e.message);
      });
  }, [settings.provider]);

  useEffect(() => {
    if (!settings.model) return;
    api
      .get<{ tool_mode: string }>(`/model-settings?model=${encodeURIComponent(settings.model)}`)
      .then((r) => setToolMode(r.tool_mode))
      .catch(() => {});
  }, [settings.model]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, draft, approvals]);

  function refreshConversations() {
    api.get<Conversation[]>("/conversations").then(setConversations).catch((e) => setError(e.message));
  }

  function refreshTools() {
    api.get<ToolInfo[]>("/tools").then(setAllTools).catch(() => {});
    api.get<McpStatus>("/mcp").then(setMcp).catch(() => {});
  }

  async function reloadMcp() {
    setMcp((m) => m && { ...m, servers: m.servers.map((s) => ({ ...s, status: "connecting" })) });
    try {
      setMcp(await api.post<McpStatus>("/mcp/reload"));
    } catch (e: any) {
      setError(e.message);
    }
    refreshTools();
  }

  function resetLive() {
    setDraft(null);
    setStatus(null);
    setApprovals({});
    runId.current = null;
  }

  /** Assina o SSE de uma execução. Trocar de conversa só aborta a assinatura; a execução segue no servidor. */
  async function follow(convId: number, path: string, init?: RequestInit) {
    streamCtl.current?.abort();
    const ctl = new AbortController();
    streamCtl.current = ctl;
    setRunning(true);
    try {
      await streamSSE(path, { ...init, signal: ctl.signal }, onEvent);
    } catch (e: any) {
      if (!ctl.signal.aborted) setError(e.message);
    } finally {
      if (!ctl.signal.aborted) {
        // Recarrega do banco: a tela fica igual ao que foi persistido.
        const live = await api.get<Live>(`/conversations/${convId}/live`).catch(() => null);
        if (live) setMessages(live.messages);
        setRunning(false);
        resetLive();
        refreshConversations();
      }
    }
  }

  async function openConversation(id: number) {
    streamCtl.current?.abort();
    setRunning(false);
    resetLive();
    setCtx(null);
    setError("");
    setCurrentId(id);
    const live = await api.get<Live>(`/conversations/${id}/live`);
    setMessages(live.messages);
    const run = live.run;
    if (run) {
      runId.current = run.run_id;
      setDraft(run.draft);
      setSent(run.sent);
      setApprovals(Object.fromEntries(run.approvals.map((a) => [a.call.id, { preview: a.preview }])));
      follow(id, `/runs/${run.run_id}/stream?cursor=${run.cursor}`);
    }
  }

  function newConversation() {
    streamCtl.current?.abort();
    setRunning(false);
    resetLive();
    setCurrentId(null);
    setMessages([]);
    setCtx(null);
  }

  async function deleteConversation(id: number) {
    await api.del(`/conversations/${id}`);
    if (id === currentId) newConversation();
    refreshConversations();
  }

  async function changeToolMode(m: string) {
    setToolMode(m);
    await api.put("/model-settings", { model: settings.model, tool_mode: m });
  }

  function onEvent(ev: any) {
    switch (ev.type) {
      case "run_started":
        runId.current = ev.run_id;
        break;
      case "tools_sent":
        setSent(ev);
        break;
      case "status":
        setStatus(ev.text);
        break;
      case "message":
      case "event":
      case "tool_result":
        setStatus(null);
        setMessages((ms) => [...ms, ev.message]);
        if (ev.type === "tool_result") setApprovals(({ [ev.message.tool_call_id]: _, ...rest }) => rest);
        break;
      case "assistant_start":
        setStatus(null);
        setDraft({ content: "", thinking: "" });
        break;
      case "token":
        setDraft((d) => d && { ...d, content: d.content + ev.text });
        break;
      case "thinking":
        setDraft((d) => d && { ...d, thinking: d.thinking + ev.text });
        break;
      case "assistant_end":
        setDraft(null);
        setMessages((ms) => [...ms, ev.message]);
        break;
      case "approval_request":
        setApprovals((a) => ({ ...a, [ev.call.id]: { preview: ev.preview } }));
        break;
      case "context":
        setCtx(ev);
        break;
    }
  }

  async function send() {
    const content = input.trim();
    if (!content || running) return;
    if (!settings.model) {
      setError("Escolha um modelo primeiro.");
      return;
    }
    setError("");
    setInput("");
    let id = currentId;
    if (id === null) {
      id = (await api.post<Conversation>("/conversations")).id;
      setCurrentId(id);
      setMessages([]);
    }
    await follow(id, `/conversations/${id}/run`, {
      method: "POST",
      body: JSON.stringify({
        content,
        provider: settings.provider,
        model: settings.model,
        mode: settings.mode,
        write_policy: settings.writePolicy,
      }),
    });
  }

  async function stop() {
    if (runId.current) await api.post(`/runs/${runId.current}/stop`).catch(() => {});
  }

  async function decide(callId: string, approved: boolean) {
    if (!runId.current) return;
    setApprovals((a) => ({ ...a, [callId]: { ...a[callId], sent: true } })); // evita clique duplo
    await api.post(`/runs/${runId.current}/approve`, { call_id: callId, approved }).catch((e) => setError(e.message));
  }

  const results = useMemo(() => {
    const m = new Map<string, Message>();
    for (const msg of messages) if (msg.role === "tool" && msg.tool_call_id) m.set(msg.tool_call_id, msg);
    return m;
  }, [messages]);

  // Estatísticas por turno (todas as iterações do agente até a próxima mensagem do usuário),
  // exibidas embaixo da última resposta do turno.
  const turns = useMemo(() => {
    const out = new Map<number, { stats: TurnStats | null; text: string }>();
    let acc: Stats[] = [];
    let text: string[] = [];
    let last = -1;
    const flush = () => {
      if (last >= 0) out.set(last, { stats: acc.length ? aggregate(acc) : null, text: text.join("\n\n") });
      acc = [];
      text = [];
      last = -1;
    };
    messages.forEach((m, i) => {
      if (m.role === "user") flush();
      else if (m.role === "assistant") {
        last = i;
        if (m.meta?.stats) acc.push(m.meta.stats);
        if (m.content) text.push(m.content);
      }
    });
    flush();
    return out;
  }, [messages]);

  // Linha acima do input: contexto atual, saída do último turno e média de t/s da conversa.
  const summary = useMemo(() => {
    const all: Stats[] = messages.flatMap((m) => (m.role === "assistant" && m.meta?.stats ? [m.meta.stats] : []));
    const lastStats = all[all.length - 1];
    const lastTurn = [...turns.values()].pop()?.stats;
    const avg = all.length ? aggregate(all).tps : null;
    // Contexto ocupado após a última resposta = prompt + saída (é o que entra na próxima requisição).
    const used = lastStats ? lastStats.prompt_tokens + lastStats.tokens : (ctx?.used ?? null);
    const max = ctx?.max ?? lastStats?.ctx_max ?? null;
    return { used, max, out: lastTurn?.tokens ?? null, avg };
  }, [messages, turns, ctx]);

  // O turno atual ainda está rodando: não mostra estatísticas dele até terminar.
  const lastUserIndex = messages.map((m) => m.role).lastIndexOf("user");

  return (
    <div className="flex h-full">
      <Sidebar
        conversations={conversations}
        current={currentId}
        onSelect={openConversation}
        onNew={newConversation}
        onDelete={deleteConversation}
      />

      <main className="flex min-w-0 flex-1 flex-col bg-bg">
        <header className="flex items-center gap-1 px-4 py-2.5">
          <label className="relative flex items-center text-muted">
            <select
              value={settings.provider}
              onChange={(e) => update({ provider: e.target.value })}
              className="appearance-none bg-transparent py-1 pr-6 pl-2 text-sm hover:text-fg focus:outline-none"
            >
              {config.providers.map((p) => (
                <option key={p} value={p} className="bg-surface">
                  {p === "lmstudio" ? "LM Studio" : p === "ollama" ? "Ollama" : p}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-1 size-3.5" />
          </label>
          <span className="text-faint">/</span>
          <label className="relative flex items-center">
            <select
              value={settings.model}
              onChange={(e) => update({ model: e.target.value })}
              className="max-w-80 appearance-none truncate bg-transparent py-1 pr-7 pl-2 text-[17px] font-medium text-fg focus:outline-none"
            >
              {!models.length && <option value="">(sem modelos)</option>}
              {models.map((m) => (
                <option key={m} className="bg-surface text-sm">
                  {m}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-1.5 size-4 text-muted" />
          </label>
        </header>

        {modelsError && (
          <div className="mx-4 rounded-xl border border-red-500/30 bg-surface px-4 py-2 text-sm text-red-200">
            Não consegui listar modelos: {modelsError}
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-5 py-6">
            {!messages.length && !draft && (
              <div className="mt-[22vh]">
                <div className="mb-4 grid size-11 place-items-center rounded-full bg-fg text-xl font-bold text-black">F</div>
                <div className="text-3xl font-semibold">Olá!</div>
                <div className="text-3xl text-faint">Como posso ajudar hoje?</div>
                <div className="mt-4 text-sm text-muted">
                  Modo {settings.mode === "agent" ? "Agente: lê e escreve em /workspace" : "Chat: sem ferramentas"}.
                </div>
              </div>
            )}

            {messages.map((m, i) => {
              if (m.role === "user")
                return (
                  <div key={m.id} className="group my-6 flex flex-col items-end">
                    <div className="max-w-[85%] rounded-3xl bg-raised px-5 py-2.5 whitespace-pre-wrap">{m.content}</div>
                    <div className="mt-1 opacity-0 transition group-hover:opacity-100">
                      <CopyButton text={m.content} />
                    </div>
                  </div>
                );
              if (m.role === "event") return <EventNotice key={m.id} m={m} />;
              if (m.role !== "assistant") return null;
              const turn = turns.get(i);
              const showTurn = turn && !(running && i > lastUserIndex);
              return (
                <div key={m.id} className="my-4">
                  <Thinking text={m.thinking} />
                  {m.content && <Markdown text={m.content} />}
                  {m.tool_calls?.map((c, k) => (
                    <ToolBlock
                      key={c.id}
                      call={c}
                      result={results.get(c.id)}
                      approval={approvals[c.id]}
                      running={running}
                      queued={m.tool_calls!.slice(0, k).some((p) => !results.has(p.id))}
                      onDecide={(ok) => decide(c.id, ok)}
                    />
                  ))}
                  {showTurn && (
                    <div className="mt-4 space-y-1.5">
                      {turn.stats && <StatsRow s={turn.stats} />}
                      <CopyButton text={turn.text} />
                    </div>
                  )}
                </div>
              );
            })}

            {draft && (
              <div className="my-4">
                <Thinking text={draft.thinking} live={!draft.content} />
                {draft.content ? (
                  <Markdown text={draft.content} />
                ) : (
                  !draft.thinking && <div className="animate-pulse text-faint">●</div>
                )}
              </div>
            )}
            {status && !draft && <div className="my-4 animate-pulse text-sm text-muted">{status}</div>}
            <div ref={bottom} />
          </div>
        </div>

        <div className="px-5 pb-4">
          <div className="mx-auto max-w-3xl">
            {summary.used != null && (
              <div className="mb-2 flex flex-wrap justify-center gap-x-8 font-mono text-[13px] text-muted">
                <span>
                  Contexto: {fmt(summary.used)}/{summary.max ? fmt(summary.max) : "?"}
                  {summary.max ? ` (${Math.round((summary.used / summary.max) * 100)}%)` : ""}
                </span>
                {summary.out != null && <span>Saída: {fmt(summary.out)}</span>}
                {summary.avg != null && <span>Média: {summary.avg.toFixed(1)} t/s</span>}
              </div>
            )}
            {error && <div className="mb-2 text-sm text-red-300">{error}</div>}

            <div className="rounded-3xl border border-line bg-surface px-4 pt-3 pb-2.5 focus-within:border-[#454545]">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                rows={Math.min(8, Math.max(2, input.split("\n").length))}
                placeholder={settings.mode === "agent" ? "Peça algo ao agente..." : "Digite uma mensagem..."}
                className="w-full resize-none bg-transparent text-[15px] text-fg placeholder:text-faint focus:outline-none"
              />
              <div className="mt-1 flex items-center gap-2">
                <div className="flex rounded-full border border-line p-0.5 text-xs" role="radiogroup" aria-label="Modo">
                  {(["chat", "agent"] as const).map((m) => (
                    <button
                      key={m}
                      role="radio"
                      aria-checked={settings.mode === m}
                      onClick={() => update({ mode: m })}
                      className={`rounded-full px-3 py-1 ${settings.mode === m ? "bg-fg font-medium text-black" : "text-muted hover:text-fg"}`}
                    >
                      {m === "chat" ? "Chat" : "Agente"}
                    </button>
                  ))}
                </div>
                {settings.mode === "agent" && (
                  <select
                    value={settings.writePolicy}
                    onChange={(e) => update({ writePolicy: e.target.value as Settings["writePolicy"] })}
                    className={pill}
                    title="Permissão de escrita"
                  >
                    <option value="ask" className="bg-surface">Escrita: perguntar</option>
                    <option value="auto" className="bg-surface">Escrita: automática</option>
                  </select>
                )}

                <span className="ml-auto hidden max-w-60 items-center gap-1.5 truncate rounded-lg bg-raised px-2.5 py-1 text-xs text-muted sm:inline-flex">
                  <Cube className="size-3.5 shrink-0" /> <span className="truncate">{settings.model || "sem modelo"}</span>
                </span>
                {running ? (
                  <button onClick={stop} title="Parar" className="grid size-9 place-items-center rounded-full bg-raised text-fg hover:bg-[#3a3a3a]">
                    <Square />
                  </button>
                ) : (
                  <button
                    onClick={send}
                    disabled={!input.trim()}
                    title="Enviar"
                    className="grid size-9 place-items-center rounded-full bg-fg text-black hover:bg-white disabled:bg-raised disabled:text-faint"
                  >
                    <ArrowUp />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </main>

      <InfoPanel
        settings={settings}
        toolMode={toolMode}
        onToolMode={changeToolMode}
        allTools={allTools}
        sent={sent}
        mcp={mcp}
        onReloadMcp={reloadMcp}
      />
    </div>
  );
}
