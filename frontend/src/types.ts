export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

export type Preview = { kind: "diff" | "new" | "command"; path: string; text: string };

/** Aprovação pendente: preview é null para ferramentas sem preview (ex.: MCP); sent = decisão já enviada. */
export type Approval = { preview: Preview | null; suggest?: string; tool?: string; sent?: boolean };

export type Attachment = { path: string; name: string; size: number; mime: string; kind: "image" | "text" | "file" };

export type Message = {
  id: number;
  role: "user" | "assistant" | "tool" | "event";
  content: string;
  thinking: string;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
  status: "ok" | "erro" | "rejeitada" | "cancelada" | null;
  meta: Record<string, any> | null;
};

export type Conversation = { id: number; title: string; updated_at: string };

export type ToolsSent = {
  mode: "chat" | "agent";
  provider: string;
  model: string;
  tool_mode: string;
  via: "native" | "prompt" | "none";
  num_ctx: number | null;
  tools: { name: string; mutating: boolean }[];
  /** Capacidades efetivas do modelo nesta requisição (ex.: "vision") e de onde veio a informação. */
  capabilities?: string[];
  capabilities_detected?: string[] | null;
  vision_source?: "detectado" | "override" | "desconhecido";
  /** Ferramentas ligadas mas não enviadas porque o modelo não tem a capacidade exigida. */
  blocked?: { name: string; missing: string[] }[];
};

export type BrowserTab = { index: number; url: string; title: string; active: boolean };

/** Estado da sessão do navegador de uma conversa (`key` = id da conversa, "0" = rascunho). */
export type BrowserState = {
  key?: string;
  open: boolean;
  url: string;
  title: string;
  width: number;
  height: number;
  scale?: number;
  tabs?: BrowserTab[];
  file_chooser?: boolean;
};

export type Settings = {
  provider: string;
  model: string;
  mode: "chat" | "agent";
  writePolicy: "ask" | "auto";
};

export type Stats = {
  model: string;
  prompt_tokens: number;
  tokens: number;
  estimated: boolean;
  seconds: number;
  tps: number | null;
  ctx_max: number | null;
};
