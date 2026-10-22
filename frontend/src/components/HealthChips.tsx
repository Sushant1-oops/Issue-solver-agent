import type { HealthInfo } from "../types"
import { cn } from "../lib/utils"

function Chip({ label, ok, hint }: { label: string; ok: boolean; hint: string }) {
  return (
    <div
      title={hint}
      className={cn(
        "flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs",
        ok
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
          : "border-amber-500/30 bg-amber-500/10 text-amber-300",
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", ok ? "bg-emerald-400" : "bg-amber-400")} />
      {label}
    </div>
  )
}

export function HealthChips({ health }: { health: HealthInfo | null }) {
  if (!health) {
    return (
      <span className="rounded-full border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">
        backend offline — start uvicorn
      </span>
    )
  }
  const { primary, fallback } = health.providers
  const gh = health.github
  const tokenLabel = !gh.token_configured
    ? "no GitHub token"
    : gh.can_fork
      ? "GitHub · fork + PR"
      : "GitHub · own repos only"
  const tokenHint = !gh.token_configured
    ? "Set GITHUB_TOKEN (fine-grained) or GITHUB_CLASSIC_TOKEN in backend/.env to push branches and open pull requests."
    : gh.can_fork
      ? "A classic token with the repo scope is set, so issues in someone else's repository can be forked, fixed and submitted as a cross-repo pull request."
      : "Only a fine-grained token is set: repositories you can push to get a pull request, but someone else's repository cannot be forked. Add GITHUB_CLASSIC_TOKEN with the repo scope to enable that."
  // Each provider is an ordered chain of models, because free tiers retire model
  // names (404) and shed traffic (503). The chip shows the first candidate and how
  // many stand behind it; the hint lists the whole chain.
  const chainOf = (models: string[] | undefined, model: string) =>
    models && models.length ? models : [model]
  const primaryChain = chainOf(primary.models, primary.model)
  const fallbackChain = chainOf(fallback.models, fallback.model)
  const extra = (list: string[]) => (list.length > 1 ? ` +${list.length - 1}` : "")
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Chip
        label={
          primary.configured
            ? `${primary.model.split("/").pop() ?? "primary"}${extra(primaryChain)}`
            : "no OpenRouter key"
        }
        ok={primary.configured}
        hint={`${primary.label}, tried in order: ${primaryChain.join(" → ")}`}
      />
      <Chip
        label={
          fallback.configured
            ? `${fallback.model}${extra(fallbackChain)} fallback`
            : "no Gemini key"
        }
        ok={fallback.configured}
        hint={`${fallback.label}, tried in order: ${fallbackChain.join(" → ")}`}
      />
      <Chip
        label={tokenLabel}
        ok={gh.token_configured}
        hint={tokenHint}
      />
      <span className="hidden rounded-full border border-slate-700/70 bg-slate-800/40 px-3 py-1.5 text-xs text-slate-400 md:inline">
        {health.limits.llm_calls_per_job} LLM calls · {health.limits.time_budget_s}s budget
      </span>
    </div>
  )
}
