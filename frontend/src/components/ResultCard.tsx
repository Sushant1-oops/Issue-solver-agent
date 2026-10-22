import type { DoneData, PrData } from "../types"

interface Props {
  pr: PrData | null
  done: DoneData | null
}

export function ResultCard({ pr, done }: Props) {
  const url = pr?.url ?? done?.pr_url ?? ""
  if (!url && !done) return null
  const forked = Boolean(pr?.forked ?? done?.forked)
  const headRepo = pr?.head_repo ?? done?.head_repo ?? ""

  const testLine = done
    ? done.tests_skipped
      ? done.test_reason || "Tests were skipped for this repository."
      : done.test_passed
        ? `Tests passing via \`${done.test_command || "tests"}\`.`
        : "Tests still failing — review before merging."
    : null

  return (
    <div className="rounded-2xl border border-emerald-500/30 bg-gradient-to-br from-emerald-500/10 via-slate-900/60 to-slate-900/60 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-emerald-300">
            {url
              ? forked
                ? "Pull request opened from your fork"
                : "Pull request opened"
              : "Run finished"}
          </h3>
          {pr?.title && <p className="mt-1 text-sm text-slate-300">{pr.title}</p>}
          {!url && (
            <p className="mt-1 text-sm text-slate-400">
              The fix was committed locally on the working branch, but nothing was pushed to
              GitHub.
            </p>
          )}
        </div>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition hover:bg-emerald-500"
          >
            View pull request ↗
          </a>
        )}
      </div>

      {done?.summary && (
        <p className="mt-3 text-sm leading-relaxed text-slate-400">{done.summary}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-500">
        {(pr?.branch ?? done?.branch) && (
          <span>
            branch <code className="font-mono text-slate-400">{pr?.branch ?? done?.branch}</code>
          </span>
        )}
        {forked && headRepo && (
          <span>
            head <code className="font-mono text-slate-400">{headRepo}</code>
          </span>
        )}
        {testLine && <span>{testLine}</span>}
      </div>

      {done?.changed_files && done.changed_files.length > 0 && (
        <ul className="mt-3 grid gap-1 text-xs text-slate-400 sm:grid-cols-2">
          {done.changed_files.map((file) => (
            <li key={file} className="font-mono truncate">
              {file}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
