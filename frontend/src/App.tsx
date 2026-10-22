import { useCallback, useEffect, useState } from "react"
import { createJob, getHealth, listJobs } from "./api"
import { HealthChips } from "./components/HealthChips"
import { IssueForm } from "./components/IssueForm"
import { JobHistory } from "./components/JobHistory"
import { RunView } from "./components/RunView"
import { useJobStream } from "./hooks/useJobStream"
import type { HealthInfo, JobSummary } from "./types"

const PIPELINE = [
  { title: "Read", body: "Fetches the issue, comments and base branch, then shallow-clones the repository." },
  { title: "Locate", body: "Indexes files locally and asks the model which paths and regexes matter." },
  { title: "Fix & test", body: "Applies search/replace patches, then runs pytest or npm test." },
  { title: "Ship", body: "Commits the branch, pushes it, and opens a pull request linked to the issue." },
]

export default function App() {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [starting, setStarting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const refreshJobs = useCallback(async () => {
    try {
      setJobs(await listJobs())
    } catch {
      /* backend offline: keep the last known list */
    }
  }, [])

  const probeHealth = useCallback(async () => {
    try {
      setHealth(await getHealth())
    } catch {
      setHealth(null)
    }
  }, [])

  useEffect(() => {
    void refreshJobs()
    void probeHealth()
    const timer = window.setInterval(() => void probeHealth(), 30_000)
    return () => window.clearInterval(timer)
  }, [refreshJobs, probeHealth])

  const events = useJobStream(activeId, refreshJobs)
  const activeJob = jobs.find((job) => job.id === activeId) ?? null

  // Follow the newest run automatically (also restores state after a reload).
  useEffect(() => {
    if (!activeId && jobs.length > 0) setActiveId(jobs[0].id)
  }, [jobs, activeId])

  // Poll job status while a run is in flight; SSE carries the detail.
  useEffect(() => {
    if (!activeId || activeJob?.done) return
    const timer = window.setInterval(() => void refreshJobs(), 2500)
    return () => window.clearInterval(timer)
  }, [activeId, activeJob?.done, refreshJobs])

  async function handleStart(repoUrl: string, issueNumber?: number) {
    setFormError(null)
    setStarting(true)
    try {
      const { job_id } = await createJob(repoUrl, issueNumber)
      setActiveId(job_id)
      await refreshJobs()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }

  const budget = health?.limits.time_budget_s ?? 240

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm font-black text-white">
              IS
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-white">Issue Solver</h1>
              <p className="text-xs text-slate-500">
                GitHub issue → codebase read → fix → tests → pull request
              </p>
            </div>
          </div>
          <HealthChips health={health} />
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-6 px-6 py-8 lg:grid-cols-[350px_minmax(0,1fr)]">
        <section className="space-y-6">
          <IssueForm
            onStart={handleStart}
            busy={starting}
            error={formError}
            offline={health === null}
          />
          <JobHistory jobs={jobs} activeId={activeId} onSelect={setActiveId} />
        </section>

        <section className="min-w-0">
          {activeId ? (
            <RunView job={activeJob} events={events} budgetS={budget} />
          ) : (
            <div className="rounded-2xl border border-slate-800 bg-gradient-to-br from-indigo-950/40 via-slate-900/60 to-slate-900/60 p-6">
              <h2 className="text-xl font-semibold text-white">Issue in, pull request out.</h2>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-400">
                Paste a GitHub issue URL to start. The agent reads the repository, writes the fix,
                runs the tests, and opens a PR — typically inside three minutes, using only two or
                three LLM calls so free-tier rate limits stay comfortable.
              </p>
              <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {PIPELINE.map((phase, index) => (
                  <div key={phase.title} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                    <span className="font-mono text-[11px] text-indigo-400">
                      0{index + 1}
                    </span>
                    <h3 className="mt-1 text-sm font-semibold text-slate-200">{phase.title}</h3>
                    <p className="mt-1 text-xs leading-relaxed text-slate-500">{phase.body}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      </main>

      <footer className="mx-auto max-w-7xl px-6 pb-8 text-xs text-slate-600">
        Primary model: {health?.providers.primary.model ?? "—"} · Fallback:{" "}
        {health?.providers.fallback.model ?? "—"} · Time budget: {budget}s · Clones live in{" "}
        <code className="font-mono">backend/workspace</code>
      </footer>
    </div>
  )
}
