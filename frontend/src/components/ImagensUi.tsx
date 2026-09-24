// Classes e campos da aba Imagens. No desktop moram no LocalPanel.tsx (painel IA local), que não
// existe no Docker; os valores são os mesmos para as duas telas ficarem iguais.

export const card = "rounded-2xl border border-line bg-surface p-3.5";
export const btn = "rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
export const btnPrimary = "rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white disabled:opacity-40";
// `campo` sem largura: quem precisa de outra (w-24, w-auto) usa a base, senão o w-full do `input` vence.
export const campo = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";
export const input = `w-full ${campo}`;
export const SAMPLERS = ["euler_a", "euler", "heun", "dpm2", "dpm++2s_a", "dpm++2m", "dpm++2mv2", "ipndm", "lcm",
  "ddim_trailing", "tcd", "res_multistep", "er_sde", "dpm++2m_sde", "lms"];

export function Field(props: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-muted">{props.label}</span>
      {props.children}
      {props.hint && <span className="text-faint">{props.hint}</span>}
    </label>
  );
}

export function Num(props: { label: string; value: number; onChange: (v: number) => void; hint?: string; step?: number }) {
  return (
    <Field label={props.label} hint={props.hint}>
      <input
        type="number"
        step={props.step || 1}
        className={`${input} text-right`}
        value={props.value}
        onChange={(e) => props.onChange(Number(e.target.value))}
      />
    </Field>
  );
}
