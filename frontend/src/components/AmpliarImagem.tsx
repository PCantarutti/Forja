import { useEffect, useRef, useState } from "react";
import { api, uploadReferencia } from "../api";
import { Image, X } from "./icons";
import { btnPrimary } from "./ImagensUi";

/** O que o backend acha: os ESRGAN (.pth) nas pastas de modelos. Mesma rota do desktop. */
type Ampliadores = { no_disco: { path: string; name: string }[]; erro: string };

/** Método (ESRGAN achado ou Lanczos) e fator. `enviar` cria o lote ampliado. */
export function PainelAmpliar(props: {
  w: number;
  h: number;
  enviar: (corpo: { fator: number; modelo: string }) => Promise<void>;
  onError: (e: string) => void;
}) {
  const [cat, setCat] = useState<Ampliadores | null>(null);
  const [modelo, setModelo] = useState<string | null>(null); // null = ainda não escolheu
  const [fator, setFator] = useState<2 | 4>(2);
  const [enviando, setEnviando] = useState(false);
  useEffect(() => {
    api.get<Ampliadores>("/local/video/ampliadores").then(setCat).catch((e) => props.onError(e.message));
  }, []);
  const escolhido = modelo ?? cat?.no_disco[0]?.path ?? "";

  async function ampliar() {
    setEnviando(true);
    try {
      await props.enviar({ fator, modelo: escolhido });
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setEnviando(false);
    }
  }

  if (!cat) return <p className="text-xs text-muted">Carregando…</p>;
  const opcao = (ligada: boolean) =>
    `rounded-md border px-2 py-1 text-xs ${ligada ? "border-sky-500/60 bg-sky-500/10 text-sky-200" : "border-line text-muted hover:bg-raised hover:text-fg"}`;
  return (
    <div className="flex flex-col gap-2.5 text-xs">
      <label className="flex flex-col gap-1">
        <span className="text-faint">Método</span>
        <select className="rounded-md border border-line bg-raised px-2 py-1 text-fg" value={escolhido} onChange={(e) => setModelo(e.target.value)}>
          {cat.no_disco.map((m) => <option key={m.path} value={m.path}>{m.name} (IA)</option>)}
          <option value="">Rápido, sem IA (Lanczos)</option>
        </select>
      </label>
      {!cat.no_disco.length && (
        <p className="text-faint">
          Para ampliar com IA, ponha um RealESRGAN_x4plus.pth (ou x4plus_anime_6B) numa das pastas de modelos (github.com/xinntao/Real-ESRGAN).
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-faint">Fator</span>
        {([2, 4] as const).map((f) => (
          <button key={f} className={opcao(fator === f)} aria-pressed={fator === f} onClick={() => setFator(f)}>
            {f}× {!!props.w && <span className="text-faint">{props.w * f}×{props.h * f}</span>}
          </button>
        ))}
      </div>
      <button className={btnPrimary} disabled={enviando} onClick={ampliar}>
        {enviando ? "Começando…" : `Ampliar ${fator}×`}
      </button>
    </div>
  );
}

/** Uma imagem do dispositivo: vai como cópia para <pasta de imagens>/referencias (o navegador não dá o
 *  caminho) e o resultado entra no feed como lote. */
export function AmpliarArquivo(props: {
  ensureConversation: () => Promise<number>;
  onPronto: (conv: number) => void;
  onError: (e: string) => void;
}) {
  const [arq, setArq] = useState<{ file: File; url: string; w: number; h: number } | null>(null);
  const entrada = useRef<HTMLInputElement>(null);
  useEffect(() => () => {
    if (arq) URL.revokeObjectURL(arq.url);
  }, [arq]);

  function escolher(f: File | undefined) {
    if (!f) return;
    if (!/^image\/(png|jpeg|webp)$/.test(f.type)) return props.onError("Use uma imagem PNG, JPG ou WebP.");
    setArq({ file: f, url: URL.createObjectURL(f), w: 0, h: 0 });
  }

  return (
    <div
      className="flex flex-wrap items-start gap-3"
      onDragOver={(e) => e.dataTransfer.types.includes("Files") && e.preventDefault()}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        escolher(e.dataTransfer.files[0]);
      }}
    >
      <input ref={entrada} type="file" accept="image/png,image/jpeg,image/webp" hidden
        onChange={(e) => {
          escolher(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <div className="relative w-56 shrink-0">
        <button
          onClick={() => entrada.current?.click()}
          className="grid w-full place-items-center overflow-hidden rounded-xl border border-dashed border-line text-xs text-muted hover:text-fg"
          style={{ aspectRatio: arq?.w ? arq.w / arq.h : 4 / 3 }}
          title={arq ? "Escolher outra imagem" : "Escolher uma imagem"}
        >
          {arq ? (
            <img src={arq.url} alt={arq.file.name} className="size-full bg-black object-contain"
              onLoad={(e) => {
                const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
                setArq((a) => a && { ...a, w, h });
              }}
            />
          ) : (
            <span className="flex flex-col items-center gap-1.5 px-3 text-center">
              <Image className="size-5" />
              Escolha ou solte uma imagem
              <span className="text-faint">png, jpg, webp</span>
            </span>
          )}
        </button>
        {arq && (
          <button onClick={() => setArq(null)} aria-label="Remover a imagem anexada" title="Remover esta imagem"
            className="absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-full border border-white/15 bg-[#161616]/85 text-fg"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
      <div className="min-w-56 flex-1">
        {!arq ? (
          <p className="text-xs leading-relaxed text-muted">
            Amplia a resolução de qualquer imagem (ESRGAN, ou Lanczos sem IA). O original fica como está e o resultado entra aqui no feed.
          </p>
        ) : (
          <>
            <p className="mb-2 truncate text-xs text-fg">
              {arq.file.name}
              {!!arq.w && <span className="ml-2 text-faint">{arq.w}×{arq.h}</span>}
            </p>
            <PainelAmpliar w={arq.w} h={arq.h} onError={props.onError}
              enviar={async (c) => {
                const path = await uploadReferencia(arq.file);
                const conv = await props.ensureConversation();
                await api.post(`/imagens/${conv}/ampliar-arquivo`, { path, ...c });
                setArq(null);
                props.onPronto(conv);
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}
