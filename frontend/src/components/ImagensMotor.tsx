import { useEffect, useState } from "react";
import { api } from "../api";
import type { ImageParams, LocalState } from "../types";
import { X } from "./icons";
import { btn, btnPrimary, card, Field, input, Num, SAMPLERS } from "./ImagensUi";

/** Estado da aba Imagens no Docker (GET /api/imagens/estado). */
export type Motor = {
  sd_cli: string;
  sd_ok: boolean;
  dirs: string[];
  runner: boolean;
  runner_label: string;
  invisiveis: string[];  // pastas que o container não enxerga (disco fora de HOST_MOUNTS)
  api_models: { provider: string; model: string }[];
  providers: { id: string; name: string }[];
};
export type EstadoImagens = Pick<LocalState, "image" | "image_models" | "image_dir" | "image_busy"> & {
  runtimes: { sd: { installed: boolean } };
  server: { running: boolean; alias?: string };
  motor: Motor;
};

const ROTULO: Record<string, string> = {
  vae: "VAE", llm: "Codificador LLM", llm_vision: "Visão do LLM (mmproj)", clip_l: "clip_l", t5xxl: "t5xxl", taesd: "TAESD",
};
const PREVIA: Record<string, string> = { proj: "projeção", tae: "TAESD", vae: "VAE completo" };

/** Onde e com o que gerar: o sd-cli do seu Windows (pelo forja-runner, na sua GPU) e/ou modelos de nuvem
 *  com o endpoint OpenAI de imagens. Mais os ajustes de cada modelo local (VAE, codificador...). */
export default function ImagensMotor(props: { st: EstadoImagens; onMudou: () => void; onError: (e: string) => void; onFechar: () => void }) {
  const m = props.st.motor;
  const [sd, setSd] = useState(m.sd_cli);
  const [dirs, setDirs] = useState(m.dirs.join("\n"));
  const [prov, setProv] = useState(m.providers[0]?.id ?? "");
  const [nome, setNome] = useState("");
  const [salvo, setSalvo] = useState("");

  async function salvar(patch: Partial<{ sd_cli: string; dirs: string[]; api_models: Motor["api_models"] }>) {
    try {
      await api.put("/imagens/motor", patch);
      setSalvo("Salvo.");
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  return (
    <div className={`${card} mb-2 max-h-[60vh] overflow-y-auto text-xs`}>
      <div className="mb-2.5 flex items-center gap-2">
        <span className="font-medium text-fg">Motor e modelos</span>
        {salvo && <span className="text-emerald-400">{salvo}</span>}
        <button onClick={props.onFechar} className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-2.5">
          <p className="text-fg">No seu computador (stable-diffusion.cpp)</p>
          <p className={m.runner ? "text-emerald-400" : "text-amber-400"}>
            forja-runner: {m.runner_label}
            {!m.runner && " — é ele que roda o sd-cli na sua GPU. Inicie tools/forja-picker.cmd."}
          </p>
          <Field label="sd-cli.exe" hint="Caminho no Windows. O Forja Desktop instala em %APPDATA%\Forja\runtimes\sd\<vulkan|cuda|cpu>.">
            <div className="flex gap-2">
              <input className={input} value={sd} onChange={(e) => setSd(e.target.value)} spellCheck={false}
                     placeholder="C:/Users/voce/AppData/Roaming/Forja/runtimes/sd/vulkan/sd-cli.exe" />
              <button className={btn} onClick={() => salvar({ sd_cli: sd })}>Salvar</button>
            </div>
            {m.sd_cli && <span className={m.sd_ok ? "text-emerald-400" : "text-amber-400"}>{m.sd_ok ? "encontrado" : "não encontrado"}</span>}
          </Field>
          <Field label="Pastas de modelos" hint="Uma por linha. .safetensors, .ckpt e .gguf de difusão entram na lista (LLMs ficam de fora).">
            <textarea className={`${input} h-16 font-mono`} value={dirs} onChange={(e) => setDirs(e.target.value)} spellCheck={false} />
            {m.invisiveis.map((d) => (
              <span key={d} className="text-amber-400">
                O Forja (no Docker) não enxerga {d}: monte o disco {d.slice(0, 2)} no docker-compose (HOST_MOUNTS e volumes).
              </span>
            ))}
            <button className={`${btn} self-start`} onClick={() => salvar({ dirs: dirs.split("\n").map((d) => d.trim()).filter(Boolean) })}>
              Salvar pastas
            </button>
          </Field>
        </div>

        <div className="flex flex-col gap-2.5">
          <p className="text-fg">Na nuvem (API de imagens OpenAI-compatível)</p>
          <p className="text-faint">
            OpenAI (gpt-image-1, dall-e-3), Together (FLUX), xAI, Gemini… O provedor vem de Configurações › Provedores (tipo
            OpenAI); aqui só o nome do modelo de imagem. Negativo, semente e passos não existem nessa API.
          </p>
          {!m.providers.length ? (
            <p className="text-amber-400">Nenhum provedor OpenAI-compatível em Configurações › Provedores.</p>
          ) : (
            <div className="flex gap-2">
              <select className={`${input} w-auto`} value={prov} onChange={(e) => setProv(e.target.value)}>
                {m.providers.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <input className={input} value={nome} onChange={(e) => setNome(e.target.value)} placeholder="gpt-image-1" spellCheck={false} />
              <button
                className={btn}
                disabled={!nome.trim() || !prov}
                onClick={() => {
                  salvar({ api_models: [...m.api_models, { provider: prov, model: nome.trim() }] });
                  setNome("");
                }}
              >
                Adicionar
              </button>
            </div>
          )}
          {m.api_models.map((a) => (
            <div key={`${a.provider}:${a.model}`} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-fg">{a.model}</span>
              <span className="text-faint">{m.providers.find((p) => p.id === a.provider)?.name ?? a.provider}</span>
              <button className="text-faint hover:text-red-400"
                      onClick={() => salvar({ api_models: m.api_models.filter((x) => x !== a) })}>
                remover
              </button>
            </div>
          ))}
        </div>
      </div>

      <AjustesDosModelos st={props.st} onDone={props.onMudou} onError={props.onError} />
    </div>
  );
}

/** Ajustes de cada modelo local (o Flux não quer o mesmo CFG que o SD 1.5; o Qwen-Image precisa de VAE e
 *  codificador à parte). Mesmo formulário do painel IA local do desktop. */
function AjustesDosModelos(props: { st: EstadoImagens; onDone: () => void; onError: (e: string) => void }) {
  const lista = props.st.image_models.filter((m) => !m.path.startsWith("api:"));
  const [sel, setSel] = useState("");
  const [form, setForm] = useState<ImageParams | null>(null);
  const [salvo, setSalvo] = useState("");
  const [busca, setBusca] = useState<{ path: string; r: Record<string, string[]> }>({ path: "", r: {} });
  const atual = lista.find((m) => m.path === sel);
  const achados = busca.path === sel ? busca.r : {};
  const temReq = !!atual?.req;
  useEffect(() => {
    if (!sel || !temReq) return;
    let vivo = true;
    api
      .get<Record<string, string[]>>(`/imagens/achados?path=${encodeURIComponent(sel)}`)
      .then((r) => vivo && setBusca({ path: sel, r }))
      .catch(() => {});
    return () => {
      vivo = false;
    };
  }, [sel, temReq]);
  type Arquivo = "vae" | "llm" | "llm_vision" | "clip_l" | "t5xxl" | "taesd";
  const set = <K extends keyof ImageParams>(k: K, v: ImageParams[K]) => {
    setForm((f) => f && { ...f, [k]: v });
    setSalvo("");
  };
  const achadoDe = (k: string) => {
    const f = achados[k]?.[0];
    return f && form && form[k as Arquivo] !== f ? f : "";
  };
  const achado = (k: string) => {
    const f = achadoDe(k);
    if (!f) return null;
    return (
      <span className="mt-0.5 flex items-center gap-1.5 pl-4 text-faint" title={f}>
        encontrado: <span className="truncate text-muted">{f.split(/[\\/]/).pop()}</span>
        <button className="shrink-0 underline text-fg" onClick={() => set(k as Arquivo, f)}>Usar</button>
      </span>
    );
  };

  async function salvar() {
    try {
      await api.put("/imagens/modelo", { path: sel, params: form });
      setSalvo("Salvo.");
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  if (!lista.length) return <p className="mt-3 text-faint">Nenhum modelo local nas pastas.</p>;
  return (
    <div className="mt-3 border-t border-line pt-3">
      <p className="mb-1.5 text-fg">Ajustes por modelo local ({lista.length})</p>
      <div className="flex flex-col">
        {lista.map((m) => (
          <button
            key={m.path}
            title={m.path}
            onClick={() => {
              const novo = sel === m.path ? "" : m.path;
              setSel(novo);
              setForm(novo ? m.params ?? null : null);
              setSalvo("");
            }}
            className={`flex items-center gap-2 rounded-lg px-2 py-1 text-left ${sel === m.path ? "bg-raised" : "hover:bg-raised"}`}
          >
            <span className="min-w-0 flex-1 truncate text-fg">{m.name}</span>
            {!!m.falta?.length && <span className="shrink-0 text-amber-400">falta arquivo</span>}
          </button>
        ))}
      </div>
      {sel && form && (
        <div className="mt-2 flex flex-col gap-2.5">
          {atual?.req && (
            <div className={`rounded-lg border p-2.5 ${atual.falta?.length ? "border-amber-500/40 bg-amber-500/5" : "border-line"}`}>
              <p className="text-fg">{atual.req.nome}: este arquivo é só o modelo de difusão. Ele precisa também de:</p>
              <ul className="mt-1.5 flex flex-col gap-1">
                {Object.entries(atual.req.precisa).map(([k, [oque, link]]) => (
                  <li key={k}>
                    <span className={atual.falta?.includes(k) ? "text-amber-400" : "text-emerald-400"}>
                      {atual.falta?.includes(k) ? "✗" : "✓"} {ROTULO[k] ?? k}
                    </span>{" "}
                    <span className="text-muted">— {oque}</span>{" "}
                    <a href={link} target="_blank" rel="noreferrer" className="underline text-faint">baixar</a>
                    {achado(k)}
                  </li>
                ))}
                {Object.entries(atual.req.edita ?? {}).map(([k, [oque, link]]) => (
                  <li key={k}>
                    <span className={atual.falta_edicao?.includes(k) ? "text-faint" : "text-emerald-400"}>
                      {atual.falta_edicao?.includes(k) ? "○" : "✓"} {ROTULO[k] ?? k}
                    </span>{" "}
                    <span className="text-muted">— para editar imagens: {oque}</span>{" "}
                    <a href={link} target="_blank" rel="noreferrer" className="underline text-faint">baixar</a>
                    {achado(k)}
                  </li>
                ))}
              </ul>
              <button className="mt-1.5 underline text-fg" onClick={() => setForm((f) => f && { ...f, ...atual.req!.sugere })}>
                Aplicar ajustes sugeridos
              </button>
            </div>
          )}
          <div className="grid grid-cols-4 gap-2">
            <Num label="Passos" value={form.steps} onChange={(v) => set("steps", v)} />
            <Num label="CFG" value={form.cfg} onChange={(v) => set("cfg", v)} step={0.5} />
            <Num label="Largura" value={form.width} onChange={(v) => set("width", v)} step={64} />
            <Num label="Altura" value={form.height} onChange={(v) => set("height", v)} step={64} />
          </div>
          <Field label="Amostrador">
            <select className={input} value={form.sampler} onChange={(e) => set("sampler", e.target.value)}>
              {SAMPLERS.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          {(["vae", "llm", "llm_vision", "clip_l", "t5xxl"] as const).map((k) => (
            <Field key={k} label={ROTULO[k]}>
              <input className={input} value={form[k] ?? ""} onChange={(e) => set(k, e.target.value)} placeholder="opcional" spellCheck={false} />
            </Field>
          ))}
          <label className="flex items-center gap-2 text-muted">
            <input type="checkbox" checked={!!form.offload} onChange={(e) => set("offload", e.target.checked)} />
            Pesos na RAM (modelo maior que a VRAM)
          </label>
          <label className="flex items-center gap-2 text-muted">
            <input type="checkbox" checked={!!form.flash_attn} onChange={(e) => set("flash_attn", e.target.checked)} />
            Flash attention na difusão
          </label>
          <label className="flex items-center gap-2 text-muted">
            <input type="checkbox" checked={!!form.vae_tiling} onChange={(e) => set("vae_tiling", e.target.checked)} />
            VAE em blocos (evita estourar a VRAM no fim)
          </label>
          <Field label="Codificador de texto na CPU" hint="Libera VRAM para a difusão; na edição fica mais lento.">
            <select className={input} value={form.te_cpu} onChange={(e) => set("te_cpu", e.target.value as ImageParams["te_cpu"])}>
              <option value="">nunca</option>
              <option value="gerar">na geração</option>
              <option value="editar">na edição</option>
              <option value="sempre">nas duas</option>
            </select>
          </Field>
          <Field label="Prévia enquanto gera" hint={atual?.previa_auto ? `Automática neste modelo: ${PREVIA[atual.previa_auto]}.` : undefined}>
            <select className={input} value={form.preview ?? ""} onChange={(e) => set("preview", e.target.value as ImageParams["preview"])}>
              <option value="">Automática</option>
              <option value="none">Nenhuma</option>
              <option value="proj">Projeção do latente</option>
              <option value="tae">TAESD (precisa do arquivo)</option>
              <option value="vae">VAE completo (mais lenta)</option>
            </select>
          </Field>
          {form.preview === "tae" && (
            <Field label="TAESD">
              <input className={input} value={form.taesd ?? ""} onChange={(e) => set("taesd", e.target.value)} spellCheck={false} />
              {achado("taesd")}
            </Field>
          )}
          <div className="flex items-center gap-2">
            <button className={btnPrimary} onClick={salvar}>Salvar</button>
            {salvo && <span className="text-emerald-400">{salvo}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
