export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ")
}

export function timeAgo(ts: number): string {
  const seconds = Math.max(1, Math.floor((Date.now() - ts * 1000) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function fmtClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—"
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  const mins = Math.floor(seconds / 60)
  return `${mins}m ${Math.round(seconds % 60)}s`
}
