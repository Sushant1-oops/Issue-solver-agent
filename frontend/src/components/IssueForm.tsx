import { useEffect, useState, type FormEvent } from "react"

interface Props {
  onStart: (repoUrl: string, issueNumber?: number) => void
  busy: boolean
  error: string | null
  offline: boolean
}

const ISSUE_URL_RE = /github\.com\/[^/\s]+\/[^/\s]+(?:\.git)?\/issues\/(\d+)/i

export function IssueForm({ onStart, busy, error, offline }: Props) {
  const [url, setUrl] = useState("")
  const [number, setNumber] = useState("")

  // Auto-fill the issue number when a full issue URL is pasted.
  useEffect(() => {
    const match = url.match(ISSUE_URL_RE)
    if (match) setNumber(match[1])
  }, [url])

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!url.trim() || busy) return
    const parsed = number.trim() === "" ? undefined : Number(number)
    const issueNumber = parsed && Number.isInteger(parsed) && parsed > 0 ? parsed : undefined
    onStart(url.trim(), issueNumber)
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 shadow-lg shadow-black/20"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
        Solve an issue
      </h2>
      <label className="mt-4 block text-xs font-medium text-slate-400" htmlFor="issue-url">
        GitHub issue URL
      </label>
      <input
        id="issue-url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/owner/repo/issues/123"
        spellCheck={false}
        className="mt-1.5 w-full rounded-lg border border-slate-700/80 bg-slate-950/70 px-3 py-2.5 font-mono text-xs text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-indigo-500/70 focus:ring-2 focus:ring-indigo-500/20"
      />

      <div className="mt-3 flex items-end gap-3">
        <div className="w-28">
          <label className="block text-xs font-medium text-slate-400" htmlFor="issue-number">
            Issue #
          </label>
          <input
            id="issue-number"
            value={number}
            onChange={(e) => setNumber(e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
            placeholder="auto"
            className="mt-1.5 w-full rounded-lg border border-slate-700/80 bg-slate-950/70 px-3 py-2 text-xs text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-indigo-500/70"
          />
        </div>
        <button
          type="submit"
          disabled={busy || !url.trim()}
          className="flex-1 rounded-lg bg-gradient-to-r from-indigo-600 to-fuchsia-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-950/40 transition hover:from-indigo-500 hover:to-fuchsia-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Starting…" : "Solve issue"}
        </button>
      </div>

      <p className="mt-3 text-xs leading-relaxed text-slate-500">
        Works on any issue you can read. The agent clones the repo, indexes it locally, locates the
        relevant files with two to three bounded LLM calls, patches the code and runs the repository
        tests. If you can push to the repository it opens the pull request there; otherwise it forks
        the repository into your account and opens the pull request from that fork.
      </p>

      {error && (
        <p className="mt-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}
      {offline && (
        <p className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          Backend is unreachable. Run{" "}
          <code className="font-mono">python run.py</code> in the backend folder.
        </p>
      )}
    </form>
  )
}
