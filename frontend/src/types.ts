export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

export type Preview = { kind: "diff" | "new" | "command"; path: string; text: string };

/** Aprovação pendente: preview é null para ferramentas sem preview (ex.: MCP); sent = decisão já enviada. */
/** Uma pergunta do ask_user: o agente manda até 4 de uma vez. */
export type AskQuestion = {
  header?: string;
  question: string;
  options: { label: string; description?: string }[];
  multi_select?: boolean;
};

export type Approval = {
  preview: Preview | null;
  suggest?: string;
  tool?: string;
  plan?: string; // exit_plan_mode
  questions?: AskQuestion[]; // ask_user
  sent?: boolean;
};

/** O que está rodando agora: turno do agente e delegações por conversa, e processos vivos. */
export type Activity = {
  conversations: { id: number; running: boolean; subagents: number; servers: number }[];
  servers: number;
};

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

export type Conversation = {
  id: number;
  title: string;
  updated_at: string;
  workspace?: string | null;
  workspace_label?: string;
  pinned?: boolean;
  archived?: boolean;
  snippet?: string; // trecho que casou na busca por conteúdo
};

/** Arquivo alterado pelo agente nesta conversa (checkpoints), com diff do antes para o agora. */
export type ChangeFile = { path: string; status: "created" | "modified" | "deleted" | "unchanged"; diff: string; additions: number; deletions: number };

export type GitStatus = {
  repo: boolean;
  branch?: string;
  files?: { status: string; path: string; staged: boolean }[];
  ahead?: number | null;
  behind?: number | null;
  remote?: string;
  has_gh?: boolean;
  last_commit?: string;
};

export type Task = { text: string; status: "pending" | "doing" | "done" };

export type Skill = { name: string; kind: "action" | "prompt"; description: string; action?: string; prompt?: string; source?: string };

export type ToolsSent = {
  mode: "chat" | "agent";
  provider: string;
  model: string;
  tool_mode: string;
  via: "native" | "prompt" | "none";
  num_ctx: number | null;
  tools: { name: string; mutating: boolean }[];
  permission?: string;
  permission_label?: string;
  effort?: string;
  max_iterations?: number;
  /** Capacidades efetivas do modelo nesta requisição (ex.: "vision") e de onde veio a informação. */
  capabilities?: string[];
  capabilities_detected?: string[] | null;
  vision_source?: "detectado" | "override" | "desconhecido";
  /** Ferramentas ligadas mas não enviadas porque o modelo não tem a capacidade exigida. */
  blocked?: { name: string; missing: string[] }[];
  /** forja-runner (sistema do usuário) visto nesta requisição e onde run_command/serve_* executam. */
  runner?: string;
  exec_target?: "host" | "container";
};

/** Servidor iniciado pelo agente com serve_start, no seu sistema ("host") ou no container. */
/** Cota consumida num provedor do Ollama Cloud (GET /api/usage). */
export type CloudUsage = {
  provider: string;
  name: string;
  limits: { name: string; usage: number }[];
  models: { name: string; request_count?: number }[];
};

/** Delegação em andamento, em qualquer conversa (aba Instâncias). */
export type SubagentActive = {
  id: string;
  conversation_id: number;
  conversation?: string;
  run_id: string;
  task: string;
  status: string;
  seconds: number;
  level: string;
  model: string;
  provider: string;
  iterations: number;
  tokens: number;
  steps: number;
};

export type ServerInfo = {
  name: string;
  pid?: number;
  alive: boolean;
  exit_code?: number | null;
  command: string;
  cwd?: string;
  log?: string;
  uptime?: number;
  conv?: string; // conversa que subiu o processo, para a aba separar por conversa
  where: "host" | "container";
  error?: string;
};

export type RunnerStatus = { online: boolean; label: string; url?: string; info?: Record<string, any> | null };

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
  permission: "auto" | "manual" | "edits" | "plan" | "bypass";
  effort: "baixo" | "medio" | "alto" | "maximo" | "extremo";
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

// ------------------------------------------------------------------ comparar modelos

/** O que a tela manda para o backend: um modelo de provedor, ou um arquivo .gguf. */
export type CompararEntrada = { provider?: string; model?: string; path?: string; nome: string };

export type CompararItem = {
  id: string;
  rotulo: string; // A, B, C… é o que aparece no modo cego
  provider: string;
  model: string;
  path: string; // vazio = modelo de provedor
  nome: string;
  status: "pendente" | "carregando" | "rodando" | "pronto" | "erro" | "cancelado";
  content: string;
  reasoning: string;
  stats: Stats | null;
  error: string;
};

export type CompararEstado = {
  message_id: number;
  status: "rodando" | "pronto" | "erro" | "cancelado";
  modo: "paralelo" | "sequencial";
  cego: boolean;
  revelado: boolean;
  voto: string; // id do item vencedor
  itens: CompararItem[];
};

export type PlacarLinha = { nome: string; rodadas: number; vitorias: number; erros: number; tps: number | null };

// ------------------------------------------------------------------ pesquisa profunda

export type PesquisaFonte = {
  id: string;
  rodada: number;
  url: string;
  titulo: string;
  dominio: string;
  status: "fila" | "lendo" | "util" | "vazia" | "erro";
  erro: string;
  resumo: string;
  trecho: string;
};

export type PesquisaEstado = {
  message_id: number;
  pergunta: string;
  profundidade: "rapida" | "normal" | "funda";
  status: "rodando" | "pronto" | "erro" | "cancelado";
  fase: "planejando" | "buscando" | "lendo" | "escrevendo" | "pronto";
  contexto: string;
  plano: { perguntas: string[]; buscas: string[] };
  rodada: number;
  rodadas: { n: number; buscas: string[] }[];
  fontes: PesquisaFonte[];
  resumo: string;   // primeiro parágrafo do relatório: é o que a aba mostra
  aviso: string;
  relatorio: string;  // markdown completo; a aba não renderiza, o HTML abre fora
  stats: { fontes: number; uteis: number; segundos: number; rodadas: number;
           extrator: string; escritor: string };
};
