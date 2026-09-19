# Forja

Ambiente de desenvolvimento pessoal com agente de IA **local**. Uma interface web de chat onde um modelo rodando no seu PC (Ollama ou LM Studio) lê e escreve arquivos numa pasta de trabalho, sempre com aprovação visível e sem ferramentas escondidas.

- **Chat** (sem ferramentas) ou **Agente** (com `list_dir`, `read_file`, `write_file` e `edit_file`)
- Tool calling nativo, com fallback para chamadas escritas em texto (`<tool_call>`, blocos ```json e XML)
- Card de aprovação com diff antes de qualquer escrita
- Painel lateral com as ferramentas **realmente enviadas** ao modelo em cada requisição
- Detecção de "promessa sem ação" (lembra o modelo até 2x) e de loop (3 chamadas idênticas seguidas)

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
Em geral o contexto está pequeno. No Ollama, aumente `NUM_CTX` no `.env` (32768 ou mais, se couber na VRAM). No LM Studio, recarregue o modelo com um *Context Length* maior. A barra **Contexto** no painel mostra quanto está em uso.

**"Conexão interrompida... modelo descarregado"**
O LM Studio pode descarregar o modelo por TTL/JIT no meio de uma resposta. Mande a mensagem de novo.

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
  tools.py    registry de ferramentas + confinamento em /workspace
  parsing.py  parser de tool calls em texto, detector de promessa e de loop
  llm.py      cliente OpenAI-compatível (SSE) e Ollama nativo
  agent.py    loop do agente, eventos SSE, aprovações
  main.py     rotas FastAPI
frontend/src/  React + Tailwind (App, Sidebar, InfoPanel, MessageView)
```

Para adicionar uma ferramenta, registre um `Tool(name, description, parameters, handler, mutating, preview)` em `tools.py`. O loop, o painel e o card de aprovação passam a usá-la automaticamente.
