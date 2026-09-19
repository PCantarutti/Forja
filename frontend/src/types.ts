export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

export type Preview = { kind: "diff" | "new"; path: string; text: string };

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
