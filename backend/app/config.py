"""Configuração.

Os valores vêm do .env e podem ser sobrescritos em tempo de execução pela tela de
Configurações (settings.py aplica os valores salvos no banco nestes atributos). Por isso todo
módulo deve ler `config.X` na hora de usar, nunca copiar o valor no import.
"""
import os
from pathlib import Path

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))  # pasta padrão (conversa sem pasta escolhida)
WORKSPACE_HOST = os.getenv("WORKSPACE_HOST", "")  # o mesmo caminho visto no Windows, só para exibir
PICKER_URL = os.getenv("FORJA_PICKER_URL", "http://127.0.0.1:3001")  # forja-picker (diálogo nativo), visto pelo navegador
# forja-runner: comandos e servidores no sistema do usuário, visto pelo backend (container -> host)
RUNNER_URL = os.getenv("FORJA_RUNNER_URL", "http://host.docker.internal:3002")
RUNNER_TOKEN = os.getenv("FORJA_RUNNER_TOKEN", "")
RUNNER_TOKEN_FILE = os.getenv("FORJA_RUNNER_TOKEN_FILE", "/config/runner-token")  # gerado pelo runner
HOST_MOUNTS = os.getenv("HOST_MOUNTS", "")          # discos do Windows no container: "C=/host/c,D=/host/d"
DB_PATH = os.getenv("DB_PATH", "/data/forja.db")
DATA_DIR = Path(DB_PATH).parent  # volume /data: spill, skills do usuário, testes do Comparar
MCP_CONFIG = Path(os.getenv("MCP_CONFIG", "/config/mcp.json"))

NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "25"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", "1000000"))
# Documento de escritório entra pelo caminho do `documentos.py`, que extrai texto em vez de
# mandar o arquivo inteiro ao modelo: o teto pode ser bem mais folgado que o do texto puro.
MAX_DOC_BYTES = int(os.getenv("MAX_DOC_BYTES", "25000000"))
SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "300"))
TOOL_TIMEOUT = int(os.getenv("TOOL_TIMEOUT", "300"))  # teto de uma chamada de ferramenta (tools.Tool.timeout)
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
COMPACT_AT = float(os.getenv("COMPACT_AT", "0.8"))  # fração da janela que dispara a compactação
# Teto de raciocínio por esforço, em tokens de pensamento. Quem corta é o servidor: ao estourar ele
# fecha o <think> e o modelo responde na MESMA geração — uma requisição só, sem o raciocínio voltar
# como entrada e sem o Forja adivinhar quando interromper.
#
# Isto não é afinação: sem teto, o llama.cpp roda o sampler de reasoning com INT_MAX em modelo com
# tag de thinking, e a fase de pensamento fica ilimitada — trava não determinística e KV cache
# enchendo até cair para a RAM. `localai.argv()` passa o maior valor daqui como --reasoning-budget no
# launch, e cada requisição manda o do seu esforço.
REASONING_BUDGET = {
    "baixo": 0,        # nem pensa: é o esforço de ir direto ao ponto
    "medio": 1024,
    "alto": 2048,
    "maximo": 4096,
    "extremo": 1536,   # o maestro delega em vez de projetar: teto curto de propósito
}
# Afrouxa (>1) ou aperta (<1) todos os tetos acima, sem mexer em código.
REASONING_BUDGET_MULT = float(os.getenv("REASONING_BUDGET_MULT", "1.0"))

# Navegador integrado
BROWSER_IDLE_MINUTES = int(os.getenv("BROWSER_IDLE_MINUTES", "30"))  # 0 = nunca fechar sessão ociosa
BROWSER_SCALE = int(os.getenv("BROWSER_SCALE", "2"))                  # render 1x..3x (vale ao (re)lançar o Chromium)
BROWSER_STREAM = os.getenv("BROWSER_STREAM", "jpeg")                  # jpeg (leve, padrão) | png (sem perda, 3-5x mais pesado)

# type: ollama (API nativa, aceita num_ctx) | lmstudio (OpenAI + janela do modelo carregado) | openai
PROVIDERS = {
    "ollama": {"id": "ollama", "name": "Ollama", "type": "ollama",
               "url": os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/v1"), "api_key": ""},
    "lmstudio": {"id": "lmstudio", "name": "LM Studio", "type": "lmstudio",
                 "url": os.getenv("LMSTUDIO_URL", "http://host.docker.internal:1234/v1"), "api_key": ""},
}

DISABLED_TOOLS: set[str] = set()   # ferramentas desligadas na tela de Configurações
CUSTOM_INSTRUCTIONS = ""           # texto extra no fim do system prompt
AUTO_APPROVE_TOOLS: list[str] = []     # globs de nomes de ferramenta que dispensam aprovação
AUTO_APPROVE_COMMANDS: list[str] = []  # globs de comandos do run_command que dispensam aprovação
AUTO_REVIEW = False  # modo Automático com revisor: o modelo avalia o risco antes de pedir ao usuário
TRUSTED_HOOKS: list[str] = []          # pastas onde .forja/hooks.json tem permissão de rodar
PROJECT_MEMORY = True                  # ler/oferecer o arquivo de memória do projeto
PERSONAL_MEMORY = True             # memória sobre o usuário (índice no prompt, corpo sob demanda)
PERSONAL_MEMORY_DIR = Path(os.getenv("PERSONAL_MEMORY_DIR") or DATA_DIR / "memoria")
# Só o id/tipo: o Docker não sobe IA local embutida (localai é do desktop). Maestro/modelctl/qualidade
# comparam com isto; como o provedor "local" nunca existe aqui, esses caminhos ficam desligados.
LOCAL_PROVIDER = {"id": "local", "name": "IA local (llama.cpp)", "type": "llamacpp", "url": "", "api_key": ""}
PROJECT_MEMORY_FILE = "FORJA.md"
ENABLED_MODELS: dict[str, list[str]] = {}  # provedor -> modelos visíveis nos chats (ausente = todos)
SUBAGENTS: dict[str, dict] = {}            # "rapido"/"capaz" -> {"provider", "model"}
SUBAGENT_MAX_ITERATIONS = 15

# ------------------------------------------------------------------ Maestro
# A Maestro planeja, delega e verifica; os Workers implementam. O estado do projeto fica no SQLite
# (taskdb), nunca no contexto do modelo — é o que permite descarregar um modelo local e carregar
# outro entre tarefas sem perder o trabalho.
MAESTRO_MAX_ITERATIONS = int(os.getenv("MAESTRO_MAX_ITERATIONS", "500"))  # o freio real é max_attempts
MAESTRO_MAX_ATTEMPTS = int(os.getenv("MAESTRO_MAX_ATTEMPTS", "5"))        # tentativas por tarefa
MAX_WORKERS = 1                     # 1 = sequencial (Etapa 6 abre o paralelo)
MAESTRO_MODEL = {"provider": "", "model": ""}  # modelo padrão da Maestro; vazio = o do seletor do chat
# Workers especialistas: a Maestro escolhe pelo nome/quando no plano, e o roteador (subagents.rota)
# escolhe sozinho pelo tipo da tarefa e pelos arquivos. Sem modelo = não existe para a Maestro.
ESPECIALIDADES_PADRAO = [
    {"id": "logica", "nome": "Lógica e back-end",
     "quando": "algoritmos, regras de negócio, APIs, banco de dados, scripts", "provider": "", "model": ""},
    {"id": "frontend", "nome": "Frontend e aparência",
     "quando": "HTML, CSS, componentes de interface, layout, estilo, responsividade", "provider": "", "model": ""},
    {"id": "testes", "nome": "Testes", "quando": "escrever e corrigir testes automatizados",
     "provider": "", "model": ""},
    {"id": "docs", "nome": "Documentação", "quando": "README, guias, comentários e textos", "provider": "", "model": ""},
]
WORKER_ESPECIALIDADES: list[dict] = [dict(e) for e in ESPECIALIDADES_PADRAO]
MAESTRO_VISUAL = {"provider": "", "model": ""}
WORKERS_DO_MAESTRO = False  # Workers rodam no mesmo modelo da Maestro (sem troca, paralelo no mesmo servidor)  # modelo COM VISÃO que julga os prints (visual_review)
MAESTRO_BROWSER = True             # a Maestro valida entregas no navegador (browser_validate e browser_*)
MODEL_LIFECYCLE = "persistent"      # persistent | unload_after_task (Etapa 4)
# Janela mínima (por requisição) de um modelo LOCAL em cada papel. Abaixo disso a Maestro não cabe
# junto com o histórico e o Worker não cabe junto com o contrato e os arquivos — o servidor recusa o
# prompt no meio do trabalho. Modelo de nuvem fica de fora: a janela dele não é o usuário que escolhe.
MAESTRO_MIN_CTX = int(os.getenv("MAESTRO_MIN_CTX", "32768"))
WORKER_MIN_CTX = int(os.getenv("WORKER_MIN_CTX", "16384"))
