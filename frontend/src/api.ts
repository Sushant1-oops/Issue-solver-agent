import type { HealthInfo, JobSummary } from "./types"

const BASE = "/api"

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

export async function getHealth(): Promise<HealthInfo> {
  return json(await fetch(`${BASE}/health`))
}

export async function createJob(
  repoUrl: string,
  issueNumber?: number,
): Promise<{ job_id: string }> {
  return json(
    await fetch(`${BASE}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: repoUrl, issue_number: issueNumber ?? null }),
    }),
  )
}

export async function listJobs(): Promise<JobSummary[]> {
  return json(await fetch(`${BASE}/jobs`))
}

export async function getJob(id: string): Promise<JobSummary> {
  return json(await fetch(`${BASE}/jobs/${id}`))
}
