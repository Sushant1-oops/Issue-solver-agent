import { useMemo } from "react"
import { allEvents, type StreamEvent } from "../types"
import { cn } from "../lib/utils"

interface StepDef {
  key: string
  label: string
  description: string
}

const STEPS: StepDef[] = [
  { key: "fetch_issue", label: "Fetch issue", description: "Issue, comments, and base branch via GitHub API" },
  { key: "clone_repo", label: "Clone repository", description: "Shallow clone (depth 1) + a fresh ai-fix branch" },
  { key: "map_repo", label: "Index codebase", description: "Local file map with size caps — no embeddings" },
  { key: "locate", label: "Locate relevant files", description: "LLM call 1: candidate paths + regex searches" },
  { key: "gather", label: "Read & analyze", description: "Grep the repo, load capped file contents" },
  { key: "apply_fix", label: "Apply fix", description: "LLM call 2: search/replace patches, one repair retry" },
  { key: "run_tests", label: "Run tests", description: "Auto-detected pytest / npm test with hard timeouts" },
  { key: "create_pr", label: "Create pull request", description: "Commit, push branch, open the PR" },
]

type StepState = "pending" | "running" | "done" | "error"

export function Timeline({ events, errored }: { events: StreamEvent[]; errored: boolean }) {
  const stepEvents = useMemo(() => allEvents(events, "step"), [events])

  const latest = useMemo(() => {
    const map = new Map<string, "running" | "done">()
    for (const event of stepEvents) map.set(event.data.name, event.data.status)
    return map
  }, [stepEvents])

  // Sum closed running→done segments so a repair loop shows total time per step.
  const durations = useMemo(() => {
    const open = new Map<string, number>()
    const total = new Map<string, number>()
    for (const event of stepEvents) {
      const { name, status } = event.data
      if (status === "running") {
        if (!open.has(name)) open.set(name, event.ts)
      } else if (status === "done") {
        const started = open.get(name)
        if (started != null) total.set(name, (total.get(name) ?? 0) + (event.ts - started))
        open.delete(name)
      }
    }
    return total
  }, [stepEvents])

  const stateFor = (key: string): StepState => {
    const raw = latest.get(key)
    if (!raw) return "pending"
    if (raw === "done") return "done"
    return errored ? "error" : "running"
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60">
      <header className="border-b border-slate-800 px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
        Agent pipeline
      </header>
      <ol className="space-y-0.5 p-4">
        {STEPS.map((step) => {
          const state = stateFor(step.key)
          const seconds = durations.get(step.key)
          return (
            <li key={step.key} className="flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-slate-800/40">
              <span
                className={cn(
                  "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold",
                  state === "done" && "border-emerald-500/50 bg-emerald-500/15 text-emerald-300",
                  state === "running" &&
                    "animate-pulse border-indigo-400/60 bg-indigo-500/20 text-indigo-200",
                  state === "error" && "border-rose-500/60 bg-rose-500/20 text-rose-300",
                  state === "pending" && "border-slate-700 bg-slate-800/40 text-slate-600",
                )}
              >
                {state === "done" ? "✓" : state === "error" ? "✕" : state === "running" ? "●" : "○"}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span
                    className={cn(
                      "text-sm font-medium",
                      state === "pending" ? "text-slate-500" : "text-slate-200",
                    )}
                  >
                    {step.label}
                  </span>
                  {seconds != null && (
                    <span className="shrink-0 font-mono text-[11px] text-slate-500">
                      {seconds.toFixed(1)}s
                    </span>
                  )}
                  {state === "running" && (
                    <span className="shrink-0 text-[11px] font-medium text-indigo-300">in progress</span>
                  )}
                  {state === "error" && (
                    <span className="shrink-0 text-[11px] font-medium text-rose-300">failed</span>
                  )}
                </div>
                <p className="mt-0.5 text-xs leading-relaxed text-slate-500">{step.description}</p>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
