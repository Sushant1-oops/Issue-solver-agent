export type JobStatus = "queued" | "running" | "done" | "error"

export interface StepData {
  name: string
  status: "running" | "done"
}

export interface LogData {
  message: string
}

export interface IssueData {
  title: string
  number: number
  url: string
  repository: string
  default_branch: string
}

export interface PatchData {
  path: string
  action?: "replace" | "create" | "delete"
  old_content?: string
  new_content?: string
  failed?: boolean
  reason?: string
}

export interface TestData {
  command: string | null
  passed: boolean | null
  skipped: boolean
  output: string
  // Set when the suite could not even be imported: unverified, not failing.
  environment_fault?: string
}

export interface PrData {
  url: string
  title: string
  branch: string
  number?: number
  head_repo?: string
  forked?: boolean
}

export interface DoneData {
  pr_url: string
  branch: string
  summary: string
  test_passed: boolean | null
  tests_skipped: boolean
  test_command: string
  test_reason?: string
  changed_files: string[]
  head_repo?: string
  forked?: boolean
}

export interface ErrorData {
  message: string
}

interface EventOf<T extends string, D> {
  type: T
  data: D
  ts: number
}

export type StreamEvent =
  | EventOf<"step", StepData>
  | EventOf<"log", LogData>
  | EventOf<"issue", IssueData>
  | EventOf<"patch", PatchData>
  | EventOf<"test", TestData>
  | EventOf<"pr", PrData>
  | EventOf<"done", DoneData>
  | EventOf<"error", ErrorData>

export type EventType = StreamEvent["type"]

type EventData<K extends EventType> = Extract<StreamEvent, { type: K }>

export function allEvents<K extends EventType>(events: StreamEvent[], type: K): EventData<K>[] {
  return events.filter((e) => e.type === type) as unknown as EventData<K>[]
}

export function lastEvent<K extends EventType>(
  events: StreamEvent[],
  type: K,
): EventData<K> | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].type === type) return events[i] as unknown as EventData<K>
  }
  return null
}

export interface JobSummary {
  id: string
  owner: string
  repo_name: string
  issue_number: number
  repo_url: string
  issue_url: string
  status: JobStatus
  created_at: number
  done: boolean
  duration_s: number | null
  result: Partial<DoneData> & { error?: string }
  event_count: number
}

export interface HealthInfo {
  status: string
  providers: {
    // `model` is the first candidate; `models` is the whole fallback chain.
    primary: { label: string; model: string; models: string[]; configured: boolean }
    fallback: { label: string; model: string; models: string[]; configured: boolean }
  }
  github: {
    token_configured: boolean
    classic_token: boolean
    fine_grained_token: boolean
    can_fork: boolean
    allow_fork: boolean
  }
  limits: {
    time_budget_s: number
    llm_calls_per_job: string
    max_files_read: number
    max_file_chars: number
  }
}
