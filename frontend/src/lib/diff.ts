export type DiffLineType = "add" | "del" | "ctx"

export interface DiffLine {
  type: DiffLineType
  text: string
}

/**
 * Minimal line diff (LCS backtrace). Files arrive already size-capped by the
 * backend, so the quadratic table stays well inside a browser frame budget.
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.length ? before.split("\n") : []
  const b = after.length ? after.split("\n") : []
  const n = a.length
  const m = b.length

  // dp[i][j] = length of the LCS of a[i:] and b[j:]
  const dp: Uint32Array[] = []
  for (let i = 0; i <= n; i += 1) dp.push(new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }

  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: "ctx", text: a[i] })
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: "del", text: a[i] })
      i += 1
    } else {
      out.push({ type: "add", text: b[j] })
      j += 1
    }
  }
  while (i < n) {
    out.push({ type: "del", text: a[i] })
    i += 1
  }
  while (j < m) {
    out.push({ type: "add", text: b[j] })
    j += 1
  }
  return out
}

export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const line of lines) {
    if (line.type === "add") added += 1
    else if (line.type === "del") removed += 1
  }
  return { added, removed }
}
