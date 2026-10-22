import type { TestData } from "../types"
import { cn } from "../lib/utils"

function verdict(data: TestData): { label: string; tone: string } {
  if (data.environment_fault) {
    // The suite never started, which is further from "passing" than a skip is.
    return { label: "UNVERIFIED", tone: "bg-orange-500/15 text-orange-300 ring-orange-500/40" }
  }
  if (data.skipped || data.passed == null) {
    return { label: "SKIPPED", tone: "bg-amber-500/15 text-amber-300 ring-amber-500/40" }
  }
  if (data.passed) {
    return { label: "PASSING", tone: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40" }
  }
  return { label: "FAILING", tone: "bg-rose-500/15 text-rose-300 ring-rose-500/40" }
}

export function TestPanel({ data }: { data: TestData }) {
  const state = verdict(data)
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-800 px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Test run
        </span>
        <span
          className={cn(
            "rounded-full px-2.5 py-0.5 text-[11px] font-bold tracking-wide ring-1",
            state.tone,
          )}
        >
          {state.label}
        </span>
        {data.command && (
          <code className="ml-auto rounded bg-slate-950/70 px-2 py-1 font-mono text-[11px] text-slate-400">
            {data.command}
          </code>
        )}
      </header>
      {data.environment_fault && (
        <p className="border-b border-slate-800 px-4 py-2 text-xs leading-relaxed text-orange-200/80">
          The test suite could not be imported — <code className="font-mono">{data.environment_fault}</code>{" "}
          is missing from the environment, so this run verified nothing about the fix.
        </p>
      )}
      <pre className="h-64 overflow-auto px-4 py-3 font-mono text-xs leading-relaxed text-slate-400">
        {data.output?.trim() || "No output captured."}
      </pre>
    </div>
  )
}
