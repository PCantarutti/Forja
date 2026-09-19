import { useEffect, useMemo, useRef, useState } from "react";
import { api, streamRun } from "./api";
import Sidebar from "./components/Sidebar";
import InfoPanel from "./components/InfoPanel";
import { EventNotice, Markdown, Thinking, ToolBlock } from "./components/MessageView";
import type { Conversation, Message, Preview, Settings, ToolsSent } from "./types";

type Config = { providers: string[]; num_ctx: number };

function loadSettings(): Settings {
  const def: Settings = { provider: "ollama", model: "", mode: "agent", writePolicy: "ask" };
  try {
    return { ...def, ...JSON.parse(localStorage.getItem("forja.settings") ?? "{}") };
  } catch {
    return def;
  }
}

const select = "rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200";

export default function App() {
  const [config, setConfig] = useState<Config>({ providers: ["ollama", "lmstudio"], num_ctx: 32768 });
  const [allTools, setAllTools] = useState<{ name: string; mutating: boolean }[]>([]);
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState("");
  const [toolMode, setToolMode] = useState("auto");

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState<{ content: string; thinking: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [approvals, setApprovals] = useState<Record<string, Preview | null>>({});
  const [sent, setSent] = useState<ToolsSent | null>(null);
  const [ctx, setCtx] = useState<{ used: number; max: number | null; estimated: boolean } | null>(null);
  const [error, setError] = useState("");
  const [input, setInput] = useState("");
  const runId = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const update = (p: Partial<Settings>) => setSettings((s) => ({ ...s, ...p }));

  useEffect(() => {
    localStorage.setItem("forja.settings", JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    api.get<Config>("/config").then(setConfig).catch(() => {});
    api.get<{ name: string; mutating: boolean }[]>("/tools").then(setAllTools).catch(() => {});
    refreshConversations();
  }, []);

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

  async function openConversation(id: number) {
    if (running) return;
    setCurrentId(id);
    setApprovals({});
    const c = await api.get<{ messages: Message[] }>(`/conversations/${id}`);
    setMessages(c.messages);
  }

  function newConversation() {
    if (running) return;
    setCurrentId(null);
    setMessages([]);
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
      case "message":
      case "event":
      case "tool_result":
        setMessages((ms) => [...ms, ev.message]);
        if (ev.type === "tool_result")
          setApprovals(({ [ev.message.tool_call_id]: _, ...rest }) => rest);
        break;
      case "assistant_start":
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
        setApprovals((a) => ({ ...a, [ev.call.id]: ev.preview }));
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
    setRunning(true);
    try {
      let id = currentId;
      if (id === null) {
        id = (await api.post<Conversation>("/conversations")).id;
        setCurrentId(id);
        setMessages([]);
      }
      await streamRun(
        `/conversations/${id}/run`,
        { content, provider: settings.provider, model: settings.model, mode: settings.mode, write_policy: settings.writePolicy },
        onEvent,
      );
      // Recarrega do banco: garante que a tela é igual ao que foi persistido.
      const c = await api.get<{ messages: Message[] }>(`/conversations/${id}`);
      setMessages(c.messages);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
      setDraft(null);
      setApprovals({});
      runId.current = null;
      refreshConversations();
    }
  }

  async function stop() {
    if (runId.current) await api.post(`/runs/${runId.current}/stop`).catch(() => {});
  }

  async function decide(callId: string, approved: boolean) {
    if (!runId.current) return;
    setApprovals((a) => ({ ...a, [callId]: null })); // evita clique duplo
    await api.post(`/runs/${runId.current}/approve`, { call_id: callId, approved }).catch((e) => setError(e.message));
  }

  const results = useMemo(() => {
    const m = new Map<string, Message>();
    for (const msg of messages) if (msg.role === "tool" && msg.tool_call_id) m.set(msg.tool_call_id, msg);
    return m;
  }, [messages]);

  return (
    <div className="flex h-full">
      <Sidebar
        conversations={conversations}
        current={currentId}
        onSelect={openConversation}
        onNew={newConversation}
        onDelete={deleteConversation}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-4 py-2">
          <select className={select} value={settings.provider} onChange={(e) => update({ provider: e.target.value })}>
            {config.providers.map((p) => (
              <option key={p} value={p}>
                {p === "lmstudio" ? "LM Studio" : p === "ollama" ? "Ollama" : p}
              </option>
            ))}
          </select>
          <select
            className={`${select} max-w-72`}
            value={settings.model}
            onChange={(e) => update({ model: e.target.value })}
          >
            {!models.length && <option value="">(sem modelos)</option>}
            {models.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>

          <div className="ml-2 flex overflow-hidden rounded-md border border-zinc-700 text-sm" role="radiogroup" aria-label="Modo">
            {(["chat", "agent"] as const).map((m) => (
              <button
                key={m}
                role="radio"
                aria-checked={settings.mode === m}
                onClick={() => update({ mode: m })}
                className={`px-3 py-1 ${settings.mode === m ? "bg-amber-500 font-medium text-zinc-950" : "text-zinc-300 hover:bg-zinc-800"}`}
              >
                {m === "chat" ? "Chat" : "Agente"}
              </button>
            ))}
          </div>

          <label className="ml-2 flex items-center gap-1 text-sm text-zinc-400">
            Escrita
            <select
              className={select}
              value={settings.writePolicy}
              onChange={(e) => update({ writePolicy: e.target.value as Settings["writePolicy"] })}
              disabled={settings.mode === "chat"}
            >
              <option value="ask">perguntar</option>
              <option value="auto">automática</option>
            </select>
          </label>
        </header>

        {modelsError && (
          <div className="border-b border-red-900 bg-red-950/50 px-4 py-2 text-sm text-red-200">
            Não consegui listar modelos: {modelsError}
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {!messages.length && !draft && (
              <div className="mt-24 text-center text-zinc-400">
                <div className="mb-2 text-2xl font-semibold text-zinc-200">O que vamos construir?</div>
                <div className="text-sm">
                  Modo {settings.mode === "agent" ? "Agente: lê e escreve em /workspace" : "Chat: sem ferramentas"}.
                </div>
              </div>
            )}

            {messages.map((m) => {
              if (m.role === "user")
                return (
                  <div key={m.id} className="my-4 flex justify-end">
                    <div className="max-w-[85%] rounded-2xl bg-zinc-800 px-4 py-2 whitespace-pre-wrap">{m.content}</div>
                  </div>
                );
              if (m.role === "event") return <EventNotice key={m.id} m={m} />;
              if (m.role === "assistant")
                return (
                  <div key={m.id} className="my-4">
                    <Thinking text={m.thinking} />
                    {m.content && <Markdown text={m.content} />}
                    {m.tool_calls?.map((c) => (
                      <ToolBlock
                        key={c.id}
                        call={c}
                        result={results.get(c.id)}
                        approval={approvals[c.id]}
                        running={running}
                        onDecide={(ok) => decide(c.id, ok)}
                      />
                    ))}
                  </div>
                );
              return null;
            })}

            {draft && (
              <div className="my-4">
                <Thinking text={draft.thinking} live={!draft.content} />
                {draft.content ? (
                  <Markdown text={draft.content} />
                ) : (
                  !draft.thinking && <div className="animate-pulse text-zinc-500">…</div>
                )}
              </div>
            )}
            <div ref={bottom} />
          </div>
        </div>

        <div className="border-t border-zinc-800 p-3">
          {error && <div className="mx-auto mb-2 max-w-3xl text-sm text-red-300">{error}</div>}
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={Math.min(8, input.split("\n").length)}
              placeholder={settings.mode === "agent" ? "Peça algo ao agente…" : "Mensagem…"}
              className="flex-1 resize-none rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-zinc-100 placeholder:text-zinc-500 focus:border-amber-500 focus:outline-none"
            />
            {running ? (
              <button onClick={stop} className="rounded-xl bg-red-600 px-4 py-2.5 font-medium text-white hover:bg-red-500">
                Parar
              </button>
            ) : (
              <button
                onClick={send}
                disabled={!input.trim()}
                className="rounded-xl bg-amber-500 px-4 py-2.5 font-medium text-zinc-950 hover:bg-amber-400 disabled:opacity-40"
              >
                Enviar
              </button>
            )}
          </div>
        </div>
      </main>

      <InfoPanel
        settings={settings}
        toolMode={toolMode}
        onToolMode={changeToolMode}
        allTools={allTools}
        sent={sent}
        ctx={ctx}
        numCtx={config.num_ctx}
      />
    </div>
  );
}
