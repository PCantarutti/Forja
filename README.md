# Forja

Ambiente de desenvolvimento pessoal com agente de IA **local**. Uma interface web de chat onde um modelo rodando no seu PC (Ollama ou LM Studio) lê e escreve arquivos numa pasta de trabalho, sempre com aprovação visível e sem ferramentas escondidas.

- **Chat** e **Agente** em seções separadas (seletor no canto superior esquerdo, barra de conversas recolhível)
- **Modos de permissão** como no Claude: Automático, Manual, Aceitar edições, Plano e Ignorar permissões
- **Esforço** (Baixo a Máximo): controla o raciocínio do modelo e quantos passos o agente pode dar
- **Navegador integrado**: o agente abre rotas, clica, lê o DOM e tira screenshot; você assiste ao vivo na aba *Navegador* e pode interagir também
- Tool calling nativo, com fallback para chamadas escritas em texto (`<tool_call>`, blocos ```json e XML)
- Card de aprovação com diff antes de qualquer escrita
- Painel lateral com as ferramentas **realmente enviadas** ao modelo em cada requisição
- Detecção de "promessa sem ação" (lembra o modelo até 2x) e de loop (3 chamadas idênticas seguidas)
- Execuções continuam no servidor: recarregar a página (F5) reconecta, inclusive com aprovação pendente
- Compactação automática do contexto quando a conversa fica grande
- Tokens, tempo e tokens/s de cada resposta
- Tela de Configurações: provedores e chaves de API (inclui Ollama Cloud), liga/desliga de ferramentas, permissões, MCP e memória da IA
- Memória do projeto em `FORJA.md`, anexos de arquivos e imagens, editar mensagem e regenerar resposta
- **Pasta de trabalho por conversa**, escolhida em qualquer lugar do disco (como no Claude Desktop)
- **Checkpoints**: desfazer as alterações de arquivo de um turno
- **Subagentes**: o agente delega subtarefas para um modelo *Rápido* ou *Capaz*, conforme a dificuldade

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
| `write_file`, `edit_file` | Cria e edita arquivos (card com diff) | conforme o **modo de permissão** |
| `run_command` | `bash` no container Linux, com cwd em `/workspace` (python, git, node, uv) | **sempre**, mesmo com escrita automática |
| `web_search` | Busca via SearXNG local (sem chave, sem conta) | não |
| `fetch_url` | Baixa uma página e devolve o texto. Bloqueia endereços da rede local. | não |
| `browser_navigate`, `browser_read`, `browser_console` | Abre uma URL no navegador integrado, lê a página como árvore de acessibilidade (com refs `eN`) e o console | não |
| `browser_click`, `browser_type`, `browser_upload` | Clica / preenche um campo / anexa arquivo da pasta de trabalho a um input[type=file] (por ref ou seletor) | conforme o **modo de permissão** |
| `browser_tabs` | Lista, abre, troca ou fecha abas da sessão | não |
| `browser_eval` | Executa JavaScript na página | **sempre** |
| `browser_screenshot` | Screenshot da página: aparece no chat para você; vai ao modelo como imagem **só se ele tiver visão** | não |
| `mcp__<servidor>__<tool>` | Ferramentas dos servidores MCP configurados | sim, a menos que o servidor marque a ferramenta como somente leitura (`readOnlyHint`) |

O `run_command` roda **dentro do container**, não no Windows. Ele só enxerga `/workspace`, tem timeout (padrão 60s, teto `SHELL_TIMEOUT_MAX`) e, ao estourar o tempo, mata o processo e todos os filhos.

## Navegador integrado

Um Chromium (Playwright, `chromium-headless-shell`) roda dentro do container do backend. O agente o controla pelas ferramentas `browser_*`; a aba **Navegador** da coluna direita mostra a tela ao vivo (screencast) e abre sozinha na primeira chamada. A coluna é redimensionável pela borda e recolhível; recolhida, a sessão continua viva no backend.

- **Uma sessão por conversa**: cada chat tem o próprio navegador (com suas abas). Trocar de chat troca o que o painel mostra; a tela inicial usa um rascunho à parte. Apagar a conversa fecha a sessão.
- **Abas**: a página que abre popup vira aba nova; você troca/fecha na faixa de abas e o agente usa `browser_tabs`. Limite de 8 por sessão. As outras ferramentas agem na aba ativa.
- **Ociosidade**: sessão sem uso e sem ninguém assistindo fecha após *Navegador: fechar sessão ociosa* (Configurações › Geral, padrão 30 min; 0 = nunca).
- **Upload**: se a página abrir um seletor de arquivo, aparece uma barra no painel para você escolher ou cancelar. O agente usa `browser_upload` com um arquivo da pasta de trabalho.

- **Você também pode usar**: barra de URL, voltar/avançar/recarregar, clique, teclado, roda e colar direto no espelho. O agente vê o estado novo no próximo `browser_read`.
- **Tamanho**: a página do Chromium tem sempre o tamanho da área visível da aba. Redimensione a coluna pela borda e o viewport acompanha, como numa janela de verdade.
- **Qualidade**: o Chromium renderiza em 2x (*escala de renderização*, 1 a 3) e o espelho é PNG sem perda ou JPEG (*formato do espelho*), ambos em Configurações › Geral. Nítido em tela HiDPI, supersampling em tela comum.
- **Screenshots** do agente aparecem grandes no chat, dentro do card da ferramenta; clique para ampliar (Esc fecha).
- **Endereços**: um servidor subido por `run_command` fica em `http://localhost:PORTA` (mesmo container). Um app rodando no Windows fica em `http://host.docker.internal:PORTA`. Só `http(s)`; `file:` e afins são bloqueados.
- **Servidor de desenvolvimento** sem travar o `run_command`: `setsid nohup npm run dev > /tmp/dev.log 2>&1 &` e depois `tail /tmp/dev.log`. O system prompt já ensina isso ao modelo.
- **Visão**: `browser_screenshot` sempre funciona (o print aparece no chat para você), mas a imagem só entra no contexto do modelo se ele tiver visão; sem visão ele recebe um aviso e valida pelo `browser_read`. O Forja detecta no Ollama (`/api/show` → `capabilities`) e no LM Studio (`type: vlm`); para outros providers, ou para forçar, use **Visão do modelo** (auto/sim/não) no painel Info. A imagem entra no contexto como mensagem do usuário; só as 2 últimas ficam como imagem, as anteriores viram texto.
- **Permissões**: `browser_click`/`browser_type` seguem o modo de permissão e aceitam regras em *Permissões* (ex.: `browser_*`). `browser_eval` sempre pergunta. Conteúdo lido da página chega ao modelo marcado como dado não confiável.
- O perfil do navegador é limpo e some ao fechar a sessão. Não peça ao agente para entrar em contas pessoais.

## Seções: Chat e Agente

O seletor no canto superior esquerdo troca entre as duas seções, e cada uma lista só as suas conversas. O botão ao lado esconde e mostra a barra de conversas.

- **Chat**: conversa comum. Nenhuma ferramenta é enviada ao modelo e não há pasta de trabalho.
- **Agente**: ferramentas, pasta de trabalho, permissões e checkpoints.

O tipo é da conversa, não um interruptor: abrir uma conversa antiga leva você para a seção dela. Conversas criadas antes desta versão foram classificadas automaticamente (quem usou ferramenta virou Agente).

## Modos de permissão

No rodapé do campo de mensagem, no Agente. `Shift+Tab` alterna, e os números 1 a 5 escolhem com o menu aberto.

| Modo | O que passa sem perguntar |
|---|---|
| **Automático** | Edições de arquivo e ações na página (clique, digitar). Shell, JavaScript e MCP perguntam |
| **Manual** | Nada. Toda alteração mostra o card |
| **Aceitar edições** | Só `write_file` e `edit_file`. O resto pergunta |
| **Plano** | Nada é alterado: o agente só lê e propõe um plano |
| **Ignorar permissões** | Tudo, inclusive shell e JavaScript. Aparece um aviso fixo no rodapé |

As regras de *Configurações › Permissões* valem em todos os modos (menos Plano) e, como sempre, o bloco da ferramenta mostra o motivo de algo ter passado sem perguntar.

### Modo Plano

O agente recebe **só as ferramentas de leitura** mais uma, `exit_plan_mode`, e o painel lateral mostra exatamente isso. Ele investiga, apresenta o plano num card e espera: você **aprova escolhendo o modo de execução** (por padrão Aceitar edições) ou pede mudanças, e ele replaneja. Depois de aprovado, as ferramentas de escrita voltam e o Forja avisa na conversa qual modo passou a valer.

## Esforço

Baixo, Médio, Alto ou Máximo, ao lado do modo. Mexe em três coisas:

- **Raciocínio do modelo**: `think` no Ollama (só em modelo que declara suporte), `reasoning_effort` nos modelos de raciocínio via API OpenAI (gpt-oss, gpt-5, o-series, deepseek-r) e o interruptor `/no_think` nos Qwen quando o esforço é baixo.
- **Passos**: multiplica o limite de iterações (Baixo 0,4× · Médio 1× · Alto 1,6× · Máximo 3× de `MAX_ITERATIONS`).
- **Instrução**: uma linha no system prompt pedindo mais objetividade ou mais verificação.

## Conversa: anexos, editar e regenerar

- **Anexos**: clipe no campo de mensagem ou arraste arquivos para o chat. Eles são salvos em `.forja/uploads/` **dentro da pasta de trabalho**, então o agente abre com `read_file`/`run_command` como qualquer arquivo. **Imagens** vão para o modelo como visão (formato OpenAI `image_url`; no Ollama, campo `images`) — funciona com modelos de visão, como o Qwen3.6. Imagem maior que 8 MB não é enviada como imagem.
- **Editar**: passe o mouse na sua mensagem → lápis. Ao reenviar, tudo o que veio depois dela é apagado e a resposta é refeita.
- **Regenerar**: botão ⟳ embaixo da última resposta. Apaga a resposta (incluindo as chamadas de ferramenta dela) e gera outra para a mesma mensagem, com o modelo selecionado agora.
- **Editar/regenerar e arquivos**: se o agente alterou arquivos nos turnos que vão ser apagados, o Forja pergunta se também desfaz essas alterações (veja *Checkpoints*).
- **Modelo**: o seletor fica no campo de mensagem (provedor à esquerda, modelos à direita, com busca) e vale para a próxima mensagem. O painel *Modelos nesta conversa* soma tokens e t/s por modelo.

## Pasta de trabalho por conversa

Como no Claude Desktop, cada conversa tem a sua pasta. Ela aparece no chip ao lado do título, no topo. Clique nele para abrir o **seletor de pasta do sistema**: o do Explorer no Windows, o do GNOME/KDE no Linux e o do Finder no macOS. Na tela inicial, a pasta escolhida vale para a próxima conversa criada. Trocar a pasta de uma conversa existente vale a partir da próxima mensagem.

### forja-picker (seletor de pasta do sistema)

O Forja roda no Docker e a interface roda no navegador, e nenhum dos dois consegue abrir o Explorer e receber o caminho da pasta. Por isso existe um ajudante pequeno, `tools/forja_picker.py` (só Python padrão), que roda **no seu sistema**. Ele escuta só em `127.0.0.1:3001` e só atende a interface do Forja: pedidos vindos de outros sites são recusados.

- **Windows**: dê dois cliques em `toolsorja-picker.cmd` (roda sem janela). Para abrir junto com o Windows: `Win+R` → `shell:startup` → cole um atalho para esse `.cmd`.
- **Linux**: `python3 tools/forja_picker.py &`. Usa o `zenity` (GNOME) ou o `kdialog` (KDE). Sem eles, usa o Tk (`sudo apt install python3-tk`). Para iniciar no login, crie um serviço de usuário:

  ```ini
  # ~/.config/systemd/user/forja-picker.service
  [Service]
  ExecStart=/usr/bin/python3 /caminho/do/forja/tools/forja_picker.py
  [Install]
  WantedBy=default.target
  ```
  e rode `systemctl --user enable --now forja-picker`.
- **macOS**: `python3 tools/forja_picker.py &` (usa o `choose folder` do sistema).

Se o ajudante não estiver rodando, o chip abre o **seletor interno** do Forja: discos montados, pastas recentes e caminho digitado. Ele avisa como ligar o ajudante e tem o botão *Abrir seletor do sistema*. Porta e origens: `FORJA_PICKER_PORT` e `FORJA_ORIGINS` no ajudante, e `FORJA_PICKER_URL` no `.env` do Forja.

**Como funciona**: o disco `C:` é montado no container em `/host/c` (`HOST_DRIVE_C`/`HOST_MOUNTS`). As ferramentas de arquivo (`read_file`, `write_file`, `edit_file`, `list_dir`), os anexos, o `FORJA.md` e o cwd do `run_command` usam a pasta da conversa, e caminhos fora dela são bloqueados.

**Segurança (igual ao Claude Desktop)**: o `run_command` roda bash no container e **enxerga o disco montado inteiro**. A proteção é a aprovação: ele sempre pede confirmação, exceto nos comandos que você liberou em *Permissões*. Evite regras largas (`*`) e leia o comando antes de aprovar.

**Outro disco** (ex.: `D:`): em `docker-compose.yml`, acrescente o volume `- D:/:/host/d` no backend e defina `HOST_MOUNTS=C=/host/c,D=/host/d` no `.env`.

**Linux**: monte a sua home (ou outra raiz) e diga o prefixo: volume `- /home/voce:/host/home` e `HOST_MOUNTS=/home/voce=/host/home`. O formato é `prefixo-no-seu-sistema=pasta-no-container`, e vale mais de um separado por vírgula. O mais específico ganha. O `HOST_DRIVE_C` só faz sentido no Windows: no Linux, apague essa linha do compose. Para **não** expor o disco inteiro, troque `HOST_DRIVE_C=C:/` por uma pasta (ex.: `C:/Users/pedro`). Aí o seletor só enxerga o que está dentro dela, mas os caminhos continuam começando em `C:/`.

## Checkpoints (desfazer alterações)

Antes da **primeira** alteração do agente em cada arquivo, dentro de um turno, o Forja guarda como o arquivo estava, ou registra que ele não existia. Isso vale para `write_file` e `edit_file`, inclusive quando quem altera é um subagente. Embaixo da resposta aparece **desfazer N arquivos**: o botão volta os arquivos ao estado de antes daquele turno, desfazendo também os turnos seguintes (dos mais novos para os mais antigos), para não deixar estados misturados.

Mudanças feitas por **`run_command`**, servidores MCP ou pelo navegador **não** são rastreadas. Arquivos maiores que `MAX_FILE_BYTES` também não. Para esses casos, use git na sua pasta.

## Subagentes

Em **Configurações › Subagentes**, escolha provedor e modelo para dois níveis:

- **Rápido**: modelo menor, para tarefas simples e mecânicas (buscar, listar, resumir, edições óbvias).
- **Capaz**: modelo maior e mais lento, para raciocínio difícil (depurar, projetar, código complexo).

Com pelo menos um nível configurado, o agente principal ganha a ferramenta `delegate_task(task, level)` e decide sozinho quando delegar e para qual nível. Se o nível pedido não estiver configurado, usa o outro e avisa. O subagente usa as mesmas ferramentas, aprovações, permissões e pasta de trabalho, mas não pode delegar de novo. Os passos dele aparecem **dentro do bloco da delegação**, inclusive os cards de aprovação, com modelo, tokens e tempo. Só o relatório final volta para a conversa, o que economiza o contexto do agente principal. O limite de passos por subagente fica na mesma tela (padrão 15).

## Memória do projeto (`FORJA.md`)

Um arquivo na raiz da pasta de trabalho que vai junto no system prompt de toda conversa (até 8.000 caracteres). O agente é instruído a atualizá-lo quando aprende algo duradouro do projeto — decisões, convenções, comandos — e você edita em **Configurações › Memória**, onde também dá para trocar o nome do arquivo ou parar de enviar ao modelo. Como é um arquivo comum, entra no git do seu projeto se você quiser.

É diferente da memória MCP (grafo de conhecimento): o `FORJA.md` é por projeto e legível; o grafo é geral e consultado pelo modelo sob demanda.

## Permissões (auto-aprovação)

Em **Configurações › Permissões**, regras com `*` dispensam o card de aprovação:

- **Comandos** (`run_command`): comparados com o comando inteiro. Ex.: `pytest*`, `git status`, `npm run build`.
- **Ferramentas**: comparadas com o nome. Ex.: `write_file`, `browser_*`, `mcp__memoria__*`.

O card de aprovação tem **Sempre permitir** com uma sugestão pronta (ex.: `ls*`, `git status*`, `browser_eval`): cria a regra e aprova na hora. Toda execução liberada por regra mostra, no bloco da ferramenta, qual regra liberou — não existe aprovação invisível. Evite regras largas como `*`.

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
| `WORKSPACE_PATH` | `C:/Users/pedro/Dev/forja-workspace` | Pasta de trabalho **padrão** (conversas sem pasta escolhida) |
| `HOST_DRIVE_C` | `C:/` | O que do Windows aparece como disco `C:` no seletor de pasta (pode ser uma subpasta) |
| `HOST_MOUNTS` | `C=/host/c` | O que está montado no container, como `prefixo=pasta` (ex.: `C=/host/c`, `/home/voce=/host/home`) |
| `FORJA_PICKER_URL` | `http://127.0.0.1:3001` | Endereço do forja-picker, visto pelo navegador |
| `FORJA_PORT` | `3000` | Porta da interface no host |
| `OLLAMA_URL` | `http://host.docker.internal:11434/v1` | Endpoint do Ollama |
| `LMSTUDIO_URL` | `http://host.docker.internal:1234/v1` | Endpoint do LM Studio |
| `NUM_CTX` | `32768` | Janela de contexto enviada ao Ollama |
| `MAX_ITERATIONS` | `25` | Máximo de passos do agente por mensagem |
| `MAX_FILE_BYTES` | `1000000` | Tamanho máximo de arquivo lido/escrito |
| `SHELL_TIMEOUT_MAX` | `300` | Teto em segundos do `run_command` |
| `COMPACT_AT` | `0.8` | Fração da janela que dispara a compactação |
| `BROWSER_IDLE_MINUTES` | `30` | Fecha a sessão do navegador ociosa (0 = nunca) |
| `BROWSER_SCALE` | `2` | Escala de renderização do Chromium (1 a 3) |
| `BROWSER_STREAM` | `png` | Formato do espelho: `png` ou `jpeg` |

## Apontando para Ollama ou LM Studio

**Ollama**: o Forja usa a API nativa `/api/chat` para enviar `options.num_ctx`. A camada `/v1` do Ollama ignora esse parâmetro, e é por isso que outros clientes ficam presos nos 4k de contexto. A lista de modelos vem de `/v1/models`.

**Ollama Cloud**: em Configurações › Provedores, clique em **+ Ollama Cloud**, cole a chave criada em [ollama.com](https://ollama.com) → Settings → Keys e salve. Ele usa a mesma API nativa do Ollama local (`https://ollama.com/api/chat`), com a chave no cabeçalho `Authorization`.

**Quais modelos aparecem no seletor**: em cada provedor, **Modelos no seletor…** lista todos os modelos disponíveis. Marque os que quer ver no chat e clique em Salvar. Sem nenhuma marcação, todos aparecem. Útil para o Ollama Cloud e o OpenRouter, que têm dezenas de modelos. As mensagens saem do seu PC: não use para código que não pode ir para terceiros.

**LM Studio**: aba *Developer* → *Start Server* (porta 1234) e ative **Serve on Local Network**. A janela de contexto é a que você escolhe ao carregar o modelo no LM Studio. O painel lateral mostra o valor carregado.

Qualquer outro servidor compatível com OpenAI funciona como `lmstudio`: basta mudar `LMSTUDIO_URL`.

### Modo de tool calling por modelo

No painel lateral, em **Tool calling deste modelo**:

- `auto` (padrão): envia `tools` nativamente e também aceita chamadas escritas em texto. Se o servidor recusar `tools` (HTTP 400), passa sozinho para o modo texto e avisa na conversa.
- `native`: só tool calling nativo.
- `text`: não envia `tools`. O schema vai no system prompt e o modelo responde com `<tool_call>{...}</tool_call>`. Use com modelos sem suporte nativo.

## Pasta padrão

A pasta de cada conversa é escolhida na tela (veja *Pasta de trabalho por conversa*). `WORKSPACE_PATH` no `.env` define a pasta **padrão**, usada por conversas sem pasta escolhida. Depois de mudar, rode `docker compose up -d`. Se a pasta não existir, o Docker Desktop a cria.

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
  browser.py     navegador integrado (Playwright): sessão, screencast, input do usuário e ferramentas browser_*
  mcp_client.py  conexão com servidores MCP (stdio/HTTP) e registro das ferramentas
  parsing.py     parser de tool calls em texto, detector de promessa e de loop
  compact.py     compactação de contexto
  llm.py         cliente OpenAI-compatível (SSE) e Ollama nativo
  agent.py       loop do agente, execução em background (Run), aprovações
  settings.py    configurações editáveis na UI (banco + aplicação em runtime)
  policy.py      regras de auto-aprovação (Permissões)
  workspace.py   pasta de trabalho por conversa (caminhos Windows <-> container, seletor)
  checkpoints.py desfazer alterações de arquivo por turno
  subagents.py   delegate_task e o loop do subagente
  uploads.py     anexos do chat (arquivos e imagens para visão)
  memory.py      leitura/limpeza da memória (via servidor MCP de grafo)
  main.py        rotas FastAPI
config/          mcp.json (seu, fora do git) e mcp.example.json
searxng/         settings.yml do SearXNG
frontend/src/  React + Tailwind (App, Sidebar, RightPanel, InfoPanel, BrowserPanel, MessageView)
```

Para adicionar uma ferramenta, registre um `Tool(name, description, parameters, handler, mutating, preview)` em `tools.py`. O loop, o painel e o card de aprovação passam a usá-la automaticamente.
