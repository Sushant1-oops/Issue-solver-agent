import { useEffect, useRef, useState } from "react"
import type { StreamEvent } from "../types"

/**
 * Subscribes to a job's Server-Sent Events stream.
 * The backend replays every stored event from index 0, so attaching to a
 * finished job (or reloading the page) rebuilds the full run history.
 */
export function useJobStream(jobId: string | null, onFinish: () => void): StreamEvent[] {
  const [events, setEvents] = useState<StreamEvent[]>([])
  const finishRef = useRef(onFinish)
  finishRef.current = onFinish

  useEffect(() => {
    if (!jobId) {
      setEvents([])
      return
    }
    setEvents([])
    const source = new EventSource(`/api/jobs/${jobId}/events`)
    let closed = false
    const close = () => {
      if (!closed) {
        closed = true
        source.close()
      }
    }

    source.onmessage = (raw) => {
      let event: StreamEvent
      try {
        event = JSON.parse(raw.data) as StreamEvent
      } catch {
        return
      }
      setEvents((prev) => [...prev, event])
      if (event.type === "done" || event.type === "error") {
        close()
        finishRef.current()
      }
    }
    // The server closes the stream once the job finishes; stop reconnecting.
    source.onerror = () => {
      close()
      finishRef.current()
    }

    return close
  }, [jobId])

  return events
}
