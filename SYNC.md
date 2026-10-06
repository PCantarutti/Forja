# Sincronizar Forja (Docker) e Forja Desktop

São dois repositórios com a mesma história até `5fc50e8`:

| Repo | Pasta | O que é |
|---|---|---|
| Forja (web/Docker) | `C:\Projetos\Forja\forja-web` | versão Docker (backend + searxng + nginx), remoto `origin` no GitHub |
| Forja Desktop | `C:\Projetos\Forja\forja-desktop` | versão nativa do Windows (Electron + NSIS) |

Cada um enxerga o outro como remoto (`docker` lá, `desktop` cá), então **toda mudança compartilhada viaja por `git cherry-pick`** — não por copiar arquivo.

```powershell
# no repo que vai receber
git fetch desktop          # ou: git fetch docker
git log --oneline desktop/main -5
git cherry-pick <sha>
```

Para isso funcionar, os caminhos são iguais nos dois (`backend/app/...`, `frontend/src/...`). Não renomeie pastas de um lado só.

## Regra prática

1. Implemente **primeiro no repo onde o pedido nasceu** e commite só aquela mudança (commit pequeno = cherry-pick limpo).
2. `git fetch` + `git cherry-pick` no outro.
3. Rode os testes do lado que recebeu: `cd backend; .venv\Scripts\python -m pytest`.

Mudança que só toca os arquivos "comuns" (a maioria) aplica sem conflito. Mudança que encosta nos arquivos da tabela abaixo vai conflitar — é esperado: resolva à mão, os dois lados fazem a mesma coisa de jeitos diferentes.

## Arquivos que divergem de propósito

| Arquivo | Docker | Desktop |
|---|---|---|
| `backend/app/native.py` | não existe | shell do sistema em processo |
| `backend/app/runner.py` | cliente HTTP do forja-runner | não existe |
| `backend/app/shell.py` | escolhe host (runner) ou container (bash) | só local, sem `target` |
| `backend/app/terminal.py` | idem | só local |
| `backend/app/workspace.py` | traduz `C:/...` ⇄ `/host/c` (`HOST_MOUNTS`) | identidade |
| `backend/app/config.py` | `/data`, `/config`, `host.docker.internal` | `%APPDATA%\Forja`, `127.0.0.1` |
| `backend/app/main.py` | `/api/picker/start`, `/api/runner`; nginx serve a UI | sem esses; FastAPI serve a UI (`FORJA_WEB`) |
| `backend/app/agent.py` | bloco *Ambiente* fala de runner/container | fala só da máquina do usuário |
| `backend/requirements.txt` e `backend/Dockerfile` | OCR pelo tesseract do apt (`pytesseract`) | OCR pelo Windows.Media.Ocr (pacotes `winrt-*`) |
| `backend/app/web.py` | `web_search` via SearXNG do compose | DuckDuckGo quando `SEARXNG_URL` está vazio |
| `backend/app/gitops.py` | `worktree` traduz caminho | caminho direto |
| `backend/app/mirror.py` | espelho em `/data/conversas` (volume) | espelho em `%APPDATA%\Forja\conversas` |
| `backend/app/browser.py` | só o modo espelho (Chromium headless + screencast) | mais o modo nativo (`FORJA_CDP`, views do Electron) |
| `backend/app/llm.py` | sem `_inference()` nem sonda do `localai` | amostragem por modelo, `ctx`/visão do `localai`, dica do Ollama por hostname |
| `backend/app/lsp.py` | mata o servidor de linguagem com `os.killpg` | `native.popen_kwargs`/`kill_tree` |
| `backend/app/baterias.py` | `http.server` do teste sobe com `shell._local_start` (container) | `shell._start` com `&` do PowerShell |
| `backend/app/sessoes.py` e `projstate.py` | comparam pasta pelo caminho no container (`workspace.resolve`) | `workspace.normalize` (identidade) |
| `backend/app/terminal.py` (terminal do agente) | `terminal_*` sempre bash no container | shell do sistema |
| `backend/tests/conftest.py` | fixture que monta o disco do pytest como `HOST_MOUNTS` (os testes do desktop usam `tmp_path` como pasta) | `FORJA_DATA` temporário |
| `backend/app/imagegen.py` e `lotes.py` | motores `runner` (sd-cli do Windows pelo `/serve` do forja-runner) e `api` (endpoint OpenAI de imagens); caminhos do usuário traduzidos por `workspace.to_container`; config em `/data/imagens.json`, importada do `local.json` do desktop | sd-cli local via `localai` |
| `backend/app/downloads.py` | registro de jobs + `_fetch` (modelos da tela Voz) | registro + download de runtimes e modelos |
| `backend/app/tts.py` | motor de voz no Windows pelo forja-runner (`/serve`, motor em `--fila`), no Python que o Forja Desktop instalou; caminhos `C:/...` ⇄ `/host/c`; sem instalar nem pergunta de VRAM; busca no HF com helpers próprios (não há `localai`) | subprocess residente com stdin, instala o runtime por `uv`, Configurações › Runtime |
| `frontend/src/components/ImagensView.tsx` | `/api/imagens/estado` e `/api/imagens/ajustes`; anexo sempre por upload | `/api/local`; anexo pelo caminho do arquivo (Electron) |
| `frontend/src/components/ImagensMotor.tsx` e `ImagensUi.tsx` | sd-cli, pastas, modelos de nuvem e ajustes por modelo | não existem (é o painel IA local, `LocalPanel.tsx`) |
| `frontend/src/components/BrowserPanel.tsx` | espelho: clique/teclado/roda vão ao backend | mais as views nativas do Electron |
| `frontend/src/components/Settings.tsx` | 7 abas | mais a aba *Aplicativo* (zoom, bandeja, iniciar com o Windows) |
| `frontend/src/forja.d.ts` | não existe | tipos da ponte `window.forja` |
| `frontend/src/App.tsx` | `chooseFolder` usa o forja-picker | usa `window.forja.pickFolder` |
| `frontend/src/components/InfoPanel.tsx` | linha de status do runner | "Comandos em: <sistema>" |
| `frontend/src/components/ServersPanel.tsx` | `runner` na resposta de `/api/servers` | `environment` |
| `frontend/src/components/FolderPicker.tsx` | texto sobre `forja-picker.cmd` | sem esse texto |
| `frontend/src/types.ts` | `RunnerStatus`, `exec_target` | `environment` |
| `backend/app/main.py` (tela Estudos) | `/api/mcp/servidor` devolve `disponivel: false` (o nginx não repassa o `/mcp`): a tela esconde o motor "Claude via MCP"; `estudos.apagar` chamado nos dois caminhos de apagar conversa | sem o campo (Claude via MCP funciona); `estudos.apagar` dentro do `_limpar_disco` |
| `frontend/src/components/EstudosProva.tsx` (questão com figura) | `Lightbox` importado do `MessageView` (o web não tem o `Lightbox.tsx` separado) | `import { Lightbox } from "./Lightbox"` |
| `backend/app/main.py` (rota `/api/estudos-figura`) | só a rota (sem token) | a rota e o prefixo em `TOKEN_FORA_DO_HEADER` (é um `<img>`: vale o cookie e o `?t=` do celular) |
| `frontend/src/api.ts` | `auth()` só com o `X-Forja-Materia` da tela Estudos (a fronteira é o nginx) | `auth()` com o token do Electron e o `X-Forja-Materia` |
| `docker-compose.yml`, `backend/Dockerfile` e `frontend/nginx.conf` (tela Estudos) | `TZ` + `tzdata` (o "hoje" da revisão e do cronograma é o do usuário); `client_max_body_size 30m` (material até 25 MB) | — |
| raiz | `docker-compose.yml`, `*/Dockerfile`, `nginx.conf`, `searxng/`, `tools/` | `electron/`, `scripts/`, `package.json` |

Tudo o que não está nessa lista — agente, ferramentas, aprovações, checkpoints, subagentes, MCP, navegador integrado, Settings, Sidebar, MessageView — é igual e deve continuar igual.

A tela Estudos (`backend/app/estudos*.py`, `frontend/src/components/Estudos*.tsx`, `estudosUi.ts`, `estudosTexto.ts`
e os testes, `estudos_figuras.py` incluído) é **idêntica** nos dois: o que muda fica nas linhas acima. No web `mobile.avisa` é stub, então o
lembrete do cronograma não sai (e a tela não fala dele).

## Quando um arquivo divergente precisar da mesma feature

Não force o cherry-pick: implemente nos dois e cite o outro commit na mensagem (`ver <sha> no repo Docker`). É mais barato que resolver conflito em código que nasceu diferente.
