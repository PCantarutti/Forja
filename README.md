# Forja

Ambiente de desenvolvimento pessoal com agente de IA **local**. Uma interface web de chat onde um modelo rodando no seu PC (Ollama ou LM Studio) lê e escreve arquivos numa pasta de trabalho, sempre com aprovação visível e sem ferramentas escondidas.

- **Chat** (sem ferramentas) ou **Agente** (arquivos, shell, busca web e servidores MCP)
- Tool calling nativo, com fallback para chamadas escritas em texto (`<tool_call>`, blocos ```json e XML)
- Card de aprovação com diff antes de qualquer escrita
- Painel lateral com as ferramentas **realmente enviadas** ao modelo em cada requisição
- Detecção de "promessa sem ação" (lembra o modelo até 2x) e de loop (3 chamadas idênticas seguidas)
- Execuções continuam no servidor: recarregar a página (F5) reconecta, inclusive com aprovação pendente
- Compactação automática do contexto quando a conversa fica grande
- Tokens, tempo e tokens/s de cada resposta
- Tela de Configurações: provedores e chaves de API, liga/desliga de ferramentas, MCP e memória da IA

## Requisitos

- Windows 11 com **Docker Desktop** (backend WSL2)
- **Ollama** ou **LM Studio** rodando no host, com um modelo que saiba usar ferramentas (testado com Qwen3.6-35B-A3B)

## Setup

```powershell
copy .env.example .env
# edite WORKSPACE_PATH no .env se quiser outra pasta
docker compose up -d --build
```

Abra http://localhost:3000. No topo, escolha o provider e o modelo, selecione **Agente** e peça, por exemplo: *"crie calc.py com funções soma e multiplicacao"*.

Para atualizar depois de mudar o código, rode `docker compose up -d --build` de novo. As conversas ficam no volume `forja-data`.

## Ferramentas

| Ferramenta | O que faz | Aprovação |
|---|---|---|
| `list_dir`, `read_file` | Lê a pasta de trabalho | não |
| `write_file`, `edit_file` | Cria e edita arquivos (card com diff) | conforme **Escrita** (perguntar/automática) |
| `run_command` | `bash` no container Linux, com cwd em `/workspace` (python, git, node, uv) | **sempre**, mesmo com escrita automática |
| `web_search` | Busca via SearXNG local (sem chave, sem conta) | não |
| `fetch_url` | Baixa uma página e devolve o texto. Bloqueia endereços da rede local. | não |
| `mcp__<servidor>__<tool>` | Ferramentas dos servidores MCP configurados | sim, a menos que o servidor marque a ferramenta como somente leitura (`readOnlyHint`) |

O `run_command` roda **dentro do container**, não no Windows. Ele só enxerga `/workspace`, tem timeout (padrão 60s, teto `SHELL_TIMEOUT_MAX`) e, ao estourar o tempo, mata o processo e todos os filhos.

## MCP

Copie o exemplo e edite:

```powershell
copy config\mcp.example.json config\mcp.json
```

O formato é o mesmo do Claude Desktop:

```json
{
  "mcpServers": {
    "memoria": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"] },
    "tempo":   { "command": "uvx", "args": ["mcp-server-time"] },
    "remoto":  { "url": "http://host.docker.internal:8931/mcp", "headers": { "Authorization": "Bearer ..." } },
    "desligado": { "command": "...", "disabled": true }
  }
}
```

- **stdio** (`command`): o processo roda no container do backend, que já tem `npx` e `uvx`. O cwd é `/workspace`.
- **HTTP** (`url`): streamable HTTP. Para um servidor rodando no Windows, use `host.docker.internal`.
- Depois de editar, clique em **recarregar** no painel *Servidores MCP*. Não precisa reiniciar o container.
- Servidor com erro não derruba o app. O erro aparece no painel.

## Configurações

Botão **Configurações** no rodapé da barra lateral. O que você muda ali fica no banco e vale na próxima requisição, sem reiniciar o container; o que não for alterado continua vindo do `.env`. "Restaurar padrões" apaga tudo o que foi salvo e volta ao `.env`.

| Aba | O que dá para fazer |
|---|---|
| **Geral** | Instruções personalizadas (vão no fim do system prompt, sempre), `num_ctx`, máximo de iterações, limite da compactação, tamanho máximo de arquivo, timeout do shell e URL do SearXNG |
| **Provedores** | Editar Ollama/LM Studio e **adicionar qualquer API compatível com OpenAI** (OpenRouter, OpenAI, Groq...) com chave. Botão *Testar conexão* lista os modelos |
| **Ferramentas** | Ligar/desligar cada ferramenta, nativa ou de MCP. O que está desligado não vai no `tools` nem é citado no prompt, e recusa ser chamado |
| **MCP** | Editar o `mcp.json` com validação, salvar e reconectar, e ver o status de cada servidor |
| **Memória** | Ver o grafo de conhecimento do servidor MCP de memória (entidades, observações, relações), buscar e apagar entidades |

**Chaves de API** ficam no SQLite e **nunca voltam para o navegador**: a tela só mostra se existe chave e os 4 últimos caracteres. Como é uso pessoal em localhost, elas são gravadas sem criptografia; quem tiver acesso ao volume `forja-data` lê o arquivo.

Para acrescentar uma configuração nova no futuro: adicione a chave em `ENV_DEFAULTS` (e a regra em `NUMBERS`, se for número) em `backend/app/settings.py`, aplique em `apply()` e mostre o campo na aba certa de `frontend/src/components/Settings.tsx`.

### Memória da IA

A memória vem de um servidor MCP de grafo de conhecimento; qualquer servidor que exponha `read_graph` aparece na aba. O exemplo usa o `@modelcontextprotocol/server-memory` com `MEMORY_FILE_PATH=/data/memoria.json`, ou seja, dentro do volume `forja-data` — sem isso o arquivo fica dentro do container e some no próximo `--build`.

## Contexto longo: compactação

Antes de cada chamada, o Forja estima o tamanho do prompt. Se passar de `COMPACT_AT` (padrão 80%) da janela do modelo, as mensagens anteriores aos 2 últimos turnos viram um resumo escrito pelo próprio modelo. O resumo aparece na conversa como *Contexto compactado* e pode ser expandido. Nada é apagado do banco: o histórico completo continua visível. Só o que vai para o modelo encolhe.

## Variáveis (.env)

| Variável | Padrão | Para que serve |
|---|---|---|
| `WORKSPACE_PATH` | `C:/Users/pedro/Dev/forja-workspace` | Pasta do Windows montada em `/workspace`. O agente só enxerga essa pasta. |
| `FORJA_PORT` | `3000` | Porta da interface no host |
| `OLLAMA_URL` | `http://host.docker.internal:11434/v1` | Endpoint do Ollama |
| `LMSTUDIO_URL` | `http://host.docker.internal:1234/v1` | Endpoint do LM Studio |
| `NUM_CTX` | `32768` | Janela de contexto enviada ao Ollama |
| `MAX_ITERATIONS` | `25` | Máximo de passos do agente por mensagem |
| `MAX_FILE_BYTES` | `1000000` | Tamanho máximo de arquivo lido/escrito |
| `SHELL_TIMEOUT_MAX` | `300` | Teto em segundos do `run_command` |
| `COMPACT_AT` | `0.8` | Fração da janela que dispara a compactação |

## Apontando para Ollama ou LM Studio

**Ollama**: o Forja usa a API nativa `/api/chat` para enviar `options.num_ctx`. A camada `/v1` do Ollama ignora esse parâmetro, e é por isso que outros clientes ficam presos nos 4k de contexto. A lista de modelos vem de `/v1/models`.

**LM Studio**: aba *Developer* → *Start Server* (porta 1234) e ative **Serve on Local Network**. A janela de contexto é a que você escolhe ao carregar o modelo no LM Studio. O painel lateral mostra o valor carregado.

Qualquer outro servidor compatível com OpenAI funciona como `lmstudio`: basta mudar `LMSTUDIO_URL`.

### Modo de tool calling por modelo

No painel lateral, em **Tool calling deste modelo**:

- `auto` (padrão): envia `tools` nativamente e também aceita chamadas escritas em texto. Se o servidor recusar `tools` (HTTP 400), passa sozinho para o modo texto e avisa na conversa.
- `native`: só tool calling nativo.
- `text`: não envia `tools`. O schema vai no system prompt e o modelo responde com `<tool_call>{...}</tool_call>`. Use com modelos sem suporte nativo.

## Trocando a pasta de trabalho

Mude `WORKSPACE_PATH` no `.env` (use `/` ou `\\`) e rode `docker compose up -d`. Se a pasta não existir, o Docker Desktop a cria. Qualquer caminho fora dela (`..`, absolutos ou symlinks que apontem para fora) é bloqueado e o modelo recebe um erro.

## Troubleshooting

**"Não foi possível conectar em http://host.docker.internal:11434"**
O Ollama escuta só em `127.0.0.1` por padrão, e o container não alcança esse endereço. Defina a variável de ambiente do Windows e reinicie o Ollama (pela bandeja do sistema):

```powershell
setx OLLAMA_HOST 0.0.0.0
```

**Firewall do Windows**
Se o Docker ainda não conseguir conectar, libere a porta de entrada (11434 para Ollama, 1234 para LM Studio) para redes privadas:

```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow -Profile Private
```

**Modelo "esquece" as ferramentas ou responde fora de contexto**
Em geral o contexto está pequeno. No Ollama, aumente `NUM_CTX` no `.env` (32768 ou mais, se couber na VRAM). No LM Studio, recarregue o modelo com um *Context Length* maior. A linha **Contexto** acima do campo de mensagem mostra quanto está em uso.

**"Conexão interrompida... modelo descarregado"**
O LM Studio pode descarregar o modelo por TTL/JIT. Se a conexão cair antes do primeiro token, o Forja tenta de novo uma vez sozinho. Se cair no meio da resposta, mande a mensagem de novo.

**Busca web: "Busca indisponível"**
Confira se o container `searxng` está de pé (`docker compose ps`). O SearXNG não é exposto no host; só o backend fala com ele.

**Servidor MCP em "error"**
Leia a mensagem no painel. Em servidores stdio, o comando precisa existir **no container** (`npx`, `uvx`, `python`). Um executável do Windows não funciona aqui. Para servidores no Windows, use `url` com `host.docker.internal`.

**O modelo diz "vou criar o arquivo" e não cria**
O Forja manda até 2 lembretes automáticos (aparecem em azul na conversa). Se não resolver, troque o modo de tool calling do modelo para `text` no painel lateral.

## Desenvolvimento

```powershell
cd backend
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest
```

Ou dentro do container: `docker compose exec backend pytest`.

Para o frontend com hot reload (backend rodando em Docker):

```powershell
cd frontend; npm install
$env:API_URL="http://127.0.0.1:3000"; npx vite
```

### Estrutura

```
backend/app/
  tools.py       registry de ferramentas + confinamento em /workspace + ferramentas de arquivo
  shell.py       run_command
  web.py         web_search (SearXNG) e fetch_url
  mcp_client.py  conexão com servidores MCP (stdio/HTTP) e registro das ferramentas
  parsing.py     parser de tool calls em texto, detector de promessa e de loop
  compact.py     compactação de contexto
  llm.py         cliente OpenAI-compatível (SSE) e Ollama nativo
  agent.py       loop do agente, execução em background (Run), aprovações
  settings.py    configurações editáveis na UI (banco + aplicação em runtime)
  memory.py      leitura/limpeza da memória (via servidor MCP de grafo)
  main.py        rotas FastAPI
config/          mcp.json (seu, fora do git) e mcp.example.json
searxng/         settings.yml do SearXNG
frontend/src/  React + Tailwind (App, Sidebar, InfoPanel, MessageView)
```

Para adicionar uma ferramenta, registre um `Tool(name, description, parameters, handler, mutating, preview)` em `tools.py`. O loop, o painel e o card de aprovação passam a usá-la automaticamente.
