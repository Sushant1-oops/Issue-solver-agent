import { useEffect, useState } from "react"
import { lastEvent, type JobStatus, type JobSummary, type StreamEvent } from "../types"
import { cn } from "../lib/utils"
import { StatusBadge } from "./StatusBadge"
import { Timeline } from "./Timeline"
import { LogPanel } from "./LogPanel"
import { PatchPanel } from "./PatchPanel"
import { TestPanel } from "./TestPanel"
import { ResultCard } from "./ResultCard"

interface Props {
  job: JobSummary | null
  events: StreamEvent[]
  budgetS: number
}

// A model-layer fault has nothing to do with GitHub permissions, so the hint
// below follows the message instead of blaming the token for every failure.
const MODEL_FAULT = /\bllm\b|provider|model|404|429|503|rate|quota/i

const MODEL_CAUSES =
  "This fault is in the model layer, not in GitHub: a retired model name (404), an " +
  "overloaded one (503), an empty completion, a rate-limited free tier or a bad key. " +
  "PRIMARY_MODEL and GEMINI_MODEL each take a comma-separated chain that is tried in order."

const GIT_CAUSES =
  "Common causes: a repository that cannot be forked with this token, missing push " +
  "access on the GitHub token, a branch that could not be created, or a run that " +
  "outlived its time budget."

function ElapsedBar({ startedAt, budget }: { startedAt: number; budget: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const elapsed = Math.max(0, now / 1000 - startedAt)
  const pct = Math.min(100, (elapsed / budget) * 100)
  const tone = pct > 80 ? "bg-rose-500" : pct > 55 ? "bg-amber-400" : "bg-emerald-500"

  return (
    <div className="mt-4">
      <div className="flex justify-between text-[11px] text-slate-500">
        <span className="font-mono">{Math.floor(elapsed)}s elapsed</span>
        <span className="font-mono">budget {budget}s</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className={cn("h-full rounded-full transition-all duration-1000", tone)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export function RunView({ job, events, budgetS }: Props) {
  const issue = lastEvent(events, "issue")?.data ?? null
  const test = lastEvent(events, "test")?.data ?? null
  const pr = lastEvent(events, "pr")?.data ?? null
  const done = lastEvent(events, "done")?.data ?? null
  const error = lastEvent(events, "error")?.data ?? null

  const status: JobStatus = error
    ? "error"
    : done
      ? "done"
      : job && !job.done
        ? job.status === "queued"
          ? "queued"
          : "running"
        : "done"
  const running = status === "running" || status === "queued"

  const repository = issue?.repository ?? (job ? `${job.owner}/${job.repo_name}` : "")
  const issueUrl = issue?.url ?? job?.issue_url ?? "#"

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <a
              href={issueUrl}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-xs text-slate-500 hover:text-indigo-300"
            >
              {repository} · issue #{issue?.number ?? job?.issue_number ?? ""} ↗
            </a>
            <h2 className="mt-1 truncate text-lg font-semibold text-white">
              {issue?.title ?? (running ? "Fetching issue…" : (job?.result?.summary || "Run details"))}
            </h2>
          </div>
          <StatusBadge status={status} />
        </div>
        {running && job && <ElapsedBar startedAt={job.created_at} budget={budgetS} />}
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Timeline events={events} errored={status === "error"} />
        <LogPanel events={events} />
      </div>

      <PatchPanel events={events} />

      {test && <TestPanel data={test} />}

      {(pr || done) && <ResultCard pr={pr} done={done} />}

      {error && (
        <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4">
          <h3 className="text-sm font-semibold text-rose-300">Run failed</h3>
          <p className="mt-1 break-words font-mono text-xs leading-relaxed text-rose-200/90">
            {error.message}
          </p>
          <p className="mt-2 text-xs text-rose-200/60">
            {MODEL_FAULT.test(error.message) ? MODEL_CAUSES : GIT_CAUSES}
          </p>
        </div>
      )}
    </div>
  )
}
