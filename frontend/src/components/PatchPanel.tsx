import { useMemo, useState } from "react"
import { allEvents, type PatchData, type StreamEvent } from "../types"
import { diffLines, diffStats, type DiffLine } from "../lib/diff"
import { cn } from "../lib/utils"

const COLLAPSE_AFTER = 220

export function PatchPanel({ events }: { events: StreamEvent[] }) {
  const patches = useMemo(() => {
    // Later events for the same path supersede earlier ones (repair rounds).
    const map = new Map<string, PatchData>()
    for (const event of allEvents(events, "patch")) map.set(event.data.path, event.data)
    return [...map.values()]
  }, [events])

  const applied = patches.filter((patch) => !patch.failed)
  const failed = patches.filter((patch) => patch.failed)
  if (patches.length === 0) return null

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Proposed changes
        </span>
        <span className="font-mono text-[11px] text-slate-500">
          {applied.length} applied{failed.length ? ` · ${failed.length} failed` : ""}
        </span>
      </header>

      <div className="divide-y divide-slate-800/80">
        {applied.map((patch) => (
          <PatchCard key={patch.path} patch={patch} />
        ))}
        {failed.map((patch) => (
          <div key={`failed-${patch.path}`} className="px-4 py-2.5">
            <p className="font-mono text-xs text-rose-300">{patch.path}</p>
            <p className="mt-0.5 text-xs text-rose-400/80">
              Patch not applied: {patch.reason ?? "unknown reason"}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

function PatchCard({ patch }: { patch: PatchData }) {
  const lines = useMemo(
    () => diffLines(patch.old_content ?? "", patch.new_content ?? ""),
    [patch.old_content, patch.new_content],
  )
  const stats = useMemo(() => diffStats(lines), [lines])
  const [expanded, setExpanded] = useState(lines.length <= COLLAPSE_AFTER)
  const visible = expanded ? lines : lines.slice(0, COLLAPSE_AFTER)

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
        <span className="font-mono text-xs text-slate-200">{patch.path}</span>
        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
          {patch.action ?? "replace"}
        </span>
        <span className="font-mono text-[11px] text-emerald-400">+{stats.added}</span>
        <span className="font-mono text-[11px] text-rose-400">-{stats.removed}</span>
        {lines.length > COLLAPSE_AFTER && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-auto rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400 hover:bg-slate-800"
          >
            {expanded ? "collapse" : `show all ${lines.length} lines`}
          </button>
        )}
      </div>
      <pre className="overflow-x-auto border-t border-slate-800/70 bg-slate-950/50 px-0 py-1 font-mono text-xs leading-5">
        {visible.map((line, index) => (
          <DiffRow key={`${patch.path}-${index}`} line={line} />
        ))}
        {!expanded && (
          <div className="px-4 py-1 text-slate-600">
            … {lines.length - COLLAPSE_AFTER} more lines
          </div>
        )}
      </pre>
    </div>
  )
}

function DiffRow({ line }: { line: DiffLine }) {
  return (
    <div
      className={cn(
        "flex whitespace-pre",
        line.type === "add" && "bg-emerald-500/10 text-emerald-300",
        line.type === "del" && "bg-rose-500/10 text-rose-300/90",
        line.type === "ctx" && "text-slate-500",
      )}
    >
      <span className="w-8 shrink-0 select-none px-2 text-center text-slate-600">
        {line.type === "add" ? "+" : line.type === "del" ? "−" : ""}
      </span>
      <span className="pr-4">{line.text || " "}</span>
    </div>
  )
}
