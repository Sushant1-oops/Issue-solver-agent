import { useEffect, useMemo, useRef } from "react"
import { allEvents, type StreamEvent } from "../types"
import { cn, fmtClock } from "../lib/utils"

export function LogPanel({ events }: { events: StreamEvent[] }) {
  const logs = useMemo(() => allEvents(events, "log"), [events])
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs.length])

  return (
    <div className="flex min-h-0 flex-col rounded-2xl border border-slate-800 bg-slate-900/60">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Agent log
        </span>
        <span className="font-mono text-[11px] text-slate-600">{logs.length} lines</span>
      </header>
      <div
        ref={bodyRef}
        className="h-[340px] space-y-1 overflow-y-auto px-4 py-3 font-mono text-xs leading-relaxed"
      >
        {logs.length === 0 && <p className="text-slate-600">Waiting for agent output…</p>}
        {logs.map((entry, index) => {
          const message = entry.data.message ?? ""
          const tone = message.startsWith("Warning") || message.includes("failed")
            ? "text-amber-300"
            : message.startsWith("Aborting")
              ? "text-rose-300"
              : "text-slate-400"
          return (
            <div key={`${entry.ts}-${index}`} className={cn("flex gap-2", tone)}>
              <span className="shrink-0 select-none text-slate-600">{fmtClock(entry.ts)}</span>
              <span className="min-w-0 break-words">{message}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
