# Forja

Ambiente de desenvolvimento pessoal com agente de IA **local**. Uma interface web de chat onde um modelo rodando no seu PC (Ollama ou LM Studio) lê e escreve arquivos numa pasta de trabalho, sempre com aprovação visível e sem ferramentas escondidas.

- **Chat** e **Agente** em seções separadas (seletor no canto superior esquerdo, barra de conversas recolhível)
- **Modos de permissão** como no Claude: Automático, Manual, Aceitar edições, Plano e Ignorar permissões
- **Esforço** (Baixo a Extremo): controla o raciocínio do modelo e quantos passos o agente pode dar. No **Extremo** ele vira multi-modelo: delega a lógica difícil a um modelo mais forte, roda o comando de verificação e revisa o diff
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

Abra http://localhost:7001. No topo, escolha o provider e o modelo, selecione **Agente** e peça, por exemplo: *"crie calc.py com funções soma e multiplicacao"*.

Para atualizar depois de mudar o código, rode `docker compose up -d --build` de novo. As conversas ficam no volume `forja-data`.

## Ferramentas

| Ferramenta | O que faz | Aprovação |
|---|---|---|
| `list_dir`, `read_file` | Lê a pasta de trabalho | não |
| `write_file`, `edit_file` | Cria e edita arquivos (card com diff) | conforme o **modo de permissão** |
| `run_command` | Comando de shell na pasta da conversa: **no seu sistema** (PowerShell/bash via forja-runner) ou, sem o runner, `bash` no container | **sempre**, mesmo com escrita automática |
| `serve_start`, `serve_status`, `serve_stop` | Servidor de desenvolvimento em segundo plano (log em arquivo), no seu sistema ou no container | `serve_start` **sempre**; `serve_stop` conforme **Escrita** |
| `web_search` | Busca via SearXNG local (sem chave, sem conta) | não |
| `fetch_url` | Baixa uma página e devolve o texto. Bloqueia endereços da rede local. | não |
| `browser_navigate`, `browser_read`, `browser_console` | Abre uma URL no navegador integrado, lê a página como árvore de acessibilidade (com refs `eN`) e o console | não |
| `browser_click`, `browser_type`, `browser_upload` | Clica / preenche um campo / anexa arquivo da pasta de trabalho a um input[type=file] (por ref ou seletor) | conforme o **modo de permissão** |
| `browser_tabs` | Lista, abre, troca ou fecha abas da sessão | não |
| `browser_eval` | Executa JavaScript na página | **sempre** |
| `browser_screenshot` | Screenshot da página: aparece no chat para você; vai ao modelo como imagem **só se ele tiver visão** | não |
| `mcp__<servidor>__<tool>` | Ferramentas dos servidores MCP configurados | sim, a menos que o servidor marque a ferramenta como somente leitura (`readOnlyHint`) |

Com o **forja-runner** ligado (veja abaixo), o `run_command` executa **no seu sistema**, na pasta da conversa, com o shell de lá (PowerShell no Windows, bash no Linux/macOS): `npm install`, venvs e servidores ficam nativos. Sem o runner, ele roda `bash` dentro do container do Forja (enxerga os discos montados em `/host`). Nos dois casos há timeout (padrão 60s, teto `SHELL_TIMEOUT_MAX`) e o processo inteiro é morto ao estourar. O modelo pode forçar com `target='container'` ou `target='host'`.

### forja-runner (comandos e servidores no seu sistema)

`tools/forja_runner.py` (só Python padrão) roda **no seu sistema** e recebe do backend os comandos do agente. No Windows, `tools/forja-picker.cmd` já inicia o picker e o runner juntos; no Linux/macOS, `python3 tools/forja_runner.py &`.

- **Token**: gerado na primeira execução em `config/runner-token`. A pasta `config/` é montada no container, então o backend lê o mesmo arquivo; nada para copiar. Toda chamada exige o token.
- **Rede**: o container só alcança o host por `host.docker.internal`, por isso o runner escuta em `0.0.0.0:3002` (mude com `FORJA_RUNNER_BIND`/`FORJA_RUNNER_PORT`; no `.env` do Forja, `FORJA_RUNNER_URL`). Mantenha a porta fechada no firewall para redes públicas.
- **Servidores**: `serve_start(name, command)` sobe o processo em segundo plano com log em arquivo (`%TEMP%orja-serve` ou `/tmp/forja-serve`); `serve_status(name)` mostra o log e se está vivo; `serve_stop(name)` encerra a árvore inteira. Servidores no seu sistema aparecem em `http://localhost:PORTA` para você e em `http://host.docker.internal:PORTA` para o navegador integrado.
- **Aba Instâncias** (coluna direita): lista os servidores que o agente subiu, no seu sistema ou no container, com log ao vivo e botão **Parar**. O número na aba é quantos estão rodando.
- **Ambiente no prompt**: o system prompt de cada execução descreve onde os comandos rodam (sistema e shell do usuário, versões de node/python/git, pasta da conversa nos dois mundos, URLs dos servidores). O painel *Info* mostra **Comandos em**.
- Sem o runner, o prompt avisa o modelo que pacotes instalados no container ficam com binários Linux na sua pasta e sugere ligar o runner.

## Trabalhando como no Claude Desktop

- **Alterações**: aba com os arquivos que o agente mudou nesta conversa (diff do antes para o agora, abrir no editor, revelar na pasta) e o **git** da pasta: branch, arquivos alterados com diff, **Commit** (o modelo escreve a mensagem, você edita e confirma), **Criar PR** (push + `gh pr create`, precisa do GitHub CLI onde os comandos rodam) e **Worktree** (branch nova num worktree irmão; a conversa passa a trabalhar lá).
- **Terminal**: seu shell na pasta da conversa (PowerShell no Windows via runner, senão bash no container). Sem PTY: comandos comuns funcionam, programas de tela cheia não. Um shell por conversa, vivo enquanto o app estiver aberto.
- **Saída ao vivo**: `run_command` mostra o que o comando imprime enquanto roda, dentro do card.
- **Tarefas**: em trabalhos com vários passos o agente mantém uma lista (`update_tasks`) que aparece no chat com o que já foi feito.
- **Fila de mensagens**: enviar durante a execução não bloqueia: a mensagem entra na fila e o agente a recebe no próximo passo.
- **Notificações e não lidas**: com a aba fora de foco, o sistema avisa quando termina ou quando há aprovação pendente; conversas que terminaram em segundo plano ganham um ponto azul na lista.
- **Comandos `/`**: digite `/` no campo. Ações do Forja (`/compactar`, `/commit`, `/pr`, `/alteracoes`) e prompts prontos (`/revisar`, `/testar`, `/explicar`). Crie os seus em `.forja/skills/<nome>.md` na pasta da conversa (cabeçalho opcional `description:`; `$ARGUMENTS` recebe o que vier depois do comando).
- **Hooks**: `.forja/hooks.json` na pasta da conversa roda comandos depois de uma ferramenta, ex.: `{"post_tool": [{"tools": ["write_file", "edit_file"], "command": "npx prettier --write \"{path}\""}]}`. A saída é anexada ao resultado para o modelo ver.
- **Compactar agora**: botão `compactar` na linha de contexto (ou `/compactar`).
- **Colar imagem**: Ctrl+V com uma imagem no clipboard vira anexo.
- **Abrir no editor / revelar**: nos cards de arquivo e no chip da pasta (precisa do runner).
- **Conversas**: menu `⋯` em cada uma: renomear, fixar no topo, arquivar, exportar em Markdown, apagar (confirmação inline). A busca da barra lateral procura também no conteúdo das mensagens.
- **Agrupadas por pasta**: na seção Agente, a barra lateral agrupa as conversas pela pasta de trabalho (grupos recolhíveis; o lápis no cabeçalho do grupo abre uma conversa nova naquela pasta). A busca desfaz o agrupamento.
- **Seleção múltipla**: botão *Selecionar* na barra lateral; marque conversas (ou uma pasta inteira pelo cabeçalho) e aplique arquivar, desarquivar, fixar, desafixar ou apagar em lote. Conversas em execução não são apagadas.

## Navegador integrado

Os botões **Info**, **Navegador**, **Terminal**, **Alterações**, **Instâncias** e **Planos** ficam sempre visíveis no topo direito do chat. Clicar abre o painel lateral naquela aba; clicar de novo recolhe. Planos lista o que o agente propôs nesta conversa no modo Plano, com status e atalho para o card no chat. Só a aba Navegador é redimensionável pela borda; as outras têm largura fixa (Planos é mais larga). O painel lembra, por conversa, se estava aberto e em qual aba; conversa nova começa recolhida.

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
| **Ignorar permissões** | Qualquer comando **não destrutivo**, além de edições e JavaScript. Aparece um aviso fixo no rodapé |

No **Ignorar permissões**, o Forja ainda pede confirmação para comando destrutivo: apagar (`rm`, `del`,
`Remove-Item`, `shred`), formatar/particionar, desligar ou reiniciar, `sudo`/`runas`, matar processo,
`chmod`/`chown`/`icacls`, mexer em registro/serviços, `git reset --hard`, `git clean`, `git push --force`,
`docker rm`/`prune`, `kubectl delete`, `terraform destroy`, `npm publish` e `drop table|database`. Para
liberar até isso, crie a regra em *Configurações › Permissões* (as regras valem acima do modo).

**Trocar o modo no meio da resposta funciona**: vale já na próxima ferramenta e, se houver um card de
aprovação aberto que o novo modo aceita, ele é executado na hora em vez de ficar esperando. A conversa
registra a troca e o painel lateral volta a mostrar as ferramentas do novo modo. (Entrar no modo Plano
no meio de uma resposta não é oferecido: ele só vale no começo do turno.)

As regras de *Configurações › Permissões* valem em todos os modos (menos Plano) e, como sempre, o bloco da ferramenta mostra o motivo de algo ter passado sem perguntar.

### Modo Plano

O agente recebe **só as ferramentas de leitura** mais uma, `exit_plan_mode`, e o painel lateral mostra exatamente isso. Ele investiga, apresenta o plano num card e espera: você **aprova escolhendo o modo de execução** (por padrão Aceitar edições) ou pede mudanças, e ele replaneja. Depois de aprovado, as ferramentas de escrita voltam e o Forja avisa na conversa qual modo passou a valer.

## Esforço

Baixo, Médio, Alto, Máximo ou Extremo, ao lado do modo. Mexe em três coisas:

- **Raciocínio do modelo**: `think` no Ollama (só em modelo que declara suporte), `reasoning_effort` nos modelos de raciocínio via API OpenAI (gpt-oss, gpt-5, o-series, deepseek-r) e o interruptor `/no_think` nos Qwen quando o esforço é baixo.
- **Passos**: multiplica o limite de iterações (Baixo 0,4× · Médio 1× · Alto 1,6× · Máximo 3× · Extremo 4× de `MAX_ITERATIONS`).
- **Instrução**: uma linha no system prompt pedindo mais objetividade ou mais verificação.

**Extremo (multi-modelo)** vira o papel do principal: com subagentes configurados, ele **não escreve a lógica difícil nem projeta a solução** — é maestro, não autor. Ele ainda raciocina — é o que faz o pedido sair bom — mas com **teto**: passou de ~6 mil caracteres de raciocínio sem começar a responder, o Forja corta e refaz a chamada com o pensamento desligado. Sem o teto um modelo local gasta minutos projetando exatamente o que ia delegar; sem raciocínio nenhum ele delega rápido, mas inventa o enunciado. O subagente não tem teto: ele recebe esforço máximo e pensa à vontade. Ele localiza os arquivos, delega com `files` e `done_when`, integra o que voltou e responde. Uma delegação sem contexto suficiente é recusada com um exemplo de chamada correta — é erro de ferramenta, o modelo refaz. Regra de prompt sozinha não segura modelo pequeno, então escrever um arquivo grande na mão também volta uma vez, com a instrução de delegar; se for mesmo trivial, repetir a chamada passa. Faz sentido quando o modelo principal é pequeno (e barato) e o *Capaz*/*Nuvem* é bem maior; com dois modelos do mesmo tamanho, você só espera duas vezes.

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

- **Windows**: dê dois cliques em `tools/forja-picker.cmd` (roda sem janela; inicia o picker e o runner). Se só o runner estiver ligado, o botão *Abrir seletor do sistema* sobe o picker sozinho. Para abrir junto com o Windows: `Win+R` → `shell:startup` → cole um atalho para esse `.cmd`.
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

## Cota do Ollama Cloud

Provedor apontando para `https://ollama.com` com chave de API mostra quanto da cota já foi consumida, lida do
`GET /api/usage` (endpoint **não documentado** da Ollama: se sair do ar, a barra simplesmente não aparece; nada quebra).
São frações de 0 a 1 por janela — o plano grátis devolve `monthly`, o pago `session` (~5 h) e `weekly` — mais a
contagem de requisições por modelo. Token não é exposto pela Ollama.

Aparece em três lugares, e só onde a nuvem está em jogo:

- **Configurações › Provedores**, embaixo da chave, com as requisições por modelo.
- **No cartão da delegação** (aba Instâncias), quando o subagente roda num provedor de nuvem — é ali que a cota queima
  sem você ver.
- **No anel de contexto**, ao abrir o popover, quando o modelo da nuvem está selecionado no seletor ou já respondeu
  nesta conversa.

A consulta é uma por minuto, compartilhada pelas três telas, e o anel só pergunta com o popover aberto.

## Subagentes

Em **Configurações › Subagentes**, escolha provedor e modelo para três níveis:

- **Rápido**: modelo menor, para tarefas simples e mecânicas (buscar, listar, resumir, edições óbvias).
- **Capaz**: modelo maior e mais lento, para raciocínio difícil (depurar, projetar, código complexo).
- **Nuvem**: rede de segurança. O modelo **nunca** escolhe este nível: ele entra quando o escolhido não roda agora nesta máquina ou falha (ex.: Ollama Cloud, em Configurações › Provedores).

Com pelo menos um nível configurado, o agente principal ganha a ferramenta `delegate_task(task, level, files, done_when)` e decide sozinho quando delegar e para qual nível. Em `files` vão os arquivos relevantes — o conteúdo segue junto com a tarefa, então o subagente começa sabendo em vez de gastar iterações procurando.

**`done_when`** é o comando que prova que ficou pronto (`pytest -q ...`, `npm test`, um lint). Ele roda **depois** que o subagente para, pelo caminho normal do `run_command`: card de aprovação, políticas e globs de auto-aprovação valem igual (`pytest*` em Configurações › Permissões evita o card a cada delegação). Quem verifica é o turno principal, não o subagente — o relatório dele é palavra dele, o exit code é medição. No esforço **Extremo**, quando **não há essa prova** — sem `done_when`, ou com ele reprovando — o diff dos arquivos que ele tocou vai para uma revisão barata no nível *Rápido*, e o parecer entra no relatório como conselho, nunca como veredito. Com a verificação passando a revisão fica calada: um revisor menor que o autor gera falso-positivo, e o exit code já respondeu.

**Quando um nível não roda**: um slot que aponta para o provedor local só vale se o modelo dele for justamente o que está carregado — o Forja sobe um `llama-server` por vez e o llama.cpp ignora o campo `model` do pedido, então pedir outro alias rodaria o modelo errado calado. Nesse caso a delegação cai para a *Nuvem*, e sem ela devolve um erro dizendo o porquê, para o principal fazer sozinho. Falha de conexão no meio também cai para o próximo nível — mas só se o subagente ainda não tiver mexido em arquivo nenhum. O subagente usa as mesmas ferramentas, aprovações, permissões e pasta de trabalho, mas não pode delegar de novo. Os passos dele aparecem **dentro do bloco da delegação**, inclusive os cards de aprovação, com modelo, tokens e tempo. Enquanto ele trabalha, a delegação também aparece na aba **Instâncias** — de qualquer conversa, com nível, modelo, tempo, passos e o que ele está fazendo agora, mais os botões de abrir a conversa e parar o turno. Só o relatório final volta para a conversa, o que economiza o contexto do agente principal. O limite de passos por subagente fica na mesma tela (padrão 15).

### Subagentes do projeto (`.forja/agents/*.md`)

Um arquivo por persona, no mesmo formato das skills: cabeçalho e instruções. O `level` escolhe o slot
(`rapido`/`capaz`, padrão `rapido`) e `tools` limita as ferramentas — o que não estiver na lista nem
aparece para ele:

```markdown
---
description: Revisa diff atrás de bug, não mexe em nada
level: capaz
tools: read_file, search, list_dir, run_command
---
Você revisa código. Responda com os problemas reais em ordem de gravidade, com arquivo e linha.
Não edite nada, não elogie e não comente estilo.
```

Com o arquivo em `.forja/agents/revisor.md`, o agente principal vê `revisor` na lista de subagentes do
projeto e chama `delegate_task(agent="revisor", task=...)` — sem persona, continua escolhendo só o
`level`. São arquivos comuns: entram no git do projeto junto com o código.

## Memória do projeto (`FORJA.md`)

Um arquivo na raiz da pasta de trabalho que vai junto no system prompt de toda conversa (até 8.000 caracteres). O agente é instruído a atualizá-lo quando aprende algo duradouro do projeto — decisões, convenções, comandos — e você edita em **Configurações › Memória**, onde também dá para trocar o nome do arquivo ou parar de enviar ao modelo. Como é um arquivo comum, entra no git do seu projeto se você quiser.

É diferente da memória MCP (grafo de conhecimento): o `FORJA.md` é por projeto e legível; o grafo é geral e consultado pelo modelo sob demanda.

## Permissões (auto-aprovação)

Em **Configurações › Permissões**, regras com `*` dispensam o card de aprovação:

- **Comandos** (`run_command`): comparados com o comando inteiro. Ex.: `pytest*`, `git status`, `npm run build`.
- **Ferramentas**: comparadas com o nome. Ex.: `write_file`, `browser_*`, `mcp__memoria__*`.

Duas coisas uma regra **não** faz, porque o glob casa prefixo e o resto da linha viajaria de carona:

- **Comando encadeado não passa.** `pytest*` libera `pytest -q`, mas não `pytest -q; Remove-Item -Recurse C:\`. Vale para `;`, `&&`, `||`, `|`, quebra de linha, crase e `$(...)`.
- **Regra com curinga não cobre comando destrutivo.** `git push*` não dispensa o card de `git push --force`, e regra de *ferramenta* (`run_command`) nunca dispensa. Só uma regra de comando **exata** — `rm -rf build`, escrita por você — passa, em qualquer modo.

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
| `FORJA_PORT` | `7001` | Porta da interface no host |
| `FORJA_RUNNER_URL` | `http://host.docker.internal:3002` | forja-runner (comandos e servidores no seu sistema). Vazio = sempre no container |
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
$env:API_URL="http://127.0.0.1:7001"; npx vite
```

### Estrutura

```
backend/app/
  tools.py       registry de ferramentas + confinamento em /workspace + ferramentas de arquivo
  shell.py       run_command e serve_* (no seu sistema via runner, ou no container)
  runner.py      cliente do forja-runner
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
