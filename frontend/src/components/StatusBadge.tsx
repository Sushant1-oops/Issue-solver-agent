import type { JobStatus } from "../types"
import { cn } from "../lib/utils"

const STYLES: Record<JobStatus, string> = {
  queued: "bg-slate-800 text-slate-300 ring-slate-700",
  running: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/40",
  done: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  error: "bg-rose-500/15 text-rose-300 ring-rose-500/40",
}

const DOTS: Record<JobStatus, string> = {
  queued: "bg-slate-400",
  running: "bg-indigo-400 animate-pulse",
  done: "bg-emerald-400",
  error: "bg-rose-400",
}

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs font-medium uppercase tracking-wide ring-1",
        STYLES[status],
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", DOTS[status])} />
      {status}
    </span>
  )
}
