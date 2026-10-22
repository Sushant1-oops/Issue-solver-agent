import type { JobStatus, JobSummary } from "../types"
import { cn, fmtDuration, timeAgo } from "../lib/utils"

interface Props {
  jobs: JobSummary[]
  activeId: string | null
  onSelect: (id: string) => void
}

const DOT: Record<JobStatus, string> = {
  queued: "bg-slate-400",
  running: "bg-indigo-400 animate-pulse",
  done: "bg-emerald-400",
  error: "bg-rose-400",
}

export function JobHistory({ jobs, activeId, onSelect }: Props) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Recent runs
        </span>
        <span className="font-mono text-[11px] text-slate-600">{jobs.length}</span>
      </header>
      <ul className="max-h-[420px] space-y-1 overflow-y-auto p-2">
        {jobs.length === 0 && (
          <li className="px-2 py-6 text-center text-xs text-slate-600">
            No runs yet — paste an issue URL to start.
          </li>
        )}
        {jobs.map((job) => (
          <li key={job.id}>
            <button
              type="button"
              onClick={() => onSelect(job.id)}
              className={cn(
                "w-full rounded-lg px-3 py-2 text-left transition hover:bg-slate-800/60",
                job.id === activeId && "bg-slate-800/80 ring-1 ring-indigo-500/40",
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOT[job.status])} />
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-300">
                  {job.owner}/{job.repo_name}
                </span>
                <span className="shrink-0 font-mono text-[11px] text-slate-500">
                  #{job.issue_number}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between pl-3.5 text-[11px] text-slate-500">
                <span>{timeAgo(job.created_at)}</span>
                <span className="flex items-center gap-2">
                  {job.duration_s != null && <span>{fmtDuration(job.duration_s)}</span>}
                  {job.result?.pr_url && (
                    <a
                      href={job.result.pr_url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-emerald-400 hover:text-emerald-300"
                    >
                      PR ↗
                    </a>
                  )}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
