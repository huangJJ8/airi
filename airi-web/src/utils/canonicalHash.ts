/**
 * Byte-identical reimplementation of the backend's `canonical_hash`
 * (airi/approvals/artifacts.py):
 *
 *   sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
 *                     ensure_ascii=False, allow_nan=False))
 *
 * The Web UI attests "I reviewed exactly this IR" by hashing the metric_ir it
 * received from `/development/generate`. Sorting is recursive; number/bool/
 * null/string leaf values only, which the Metric IR guarantees.
 */

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value !== 'object') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map(canonicalize)
  }
  const sorted: Record<string, unknown> = {}
  for (const key of Object.keys(value as Record<string, unknown>).sort()) {
    sorted[key] = canonicalize((value as Record<string, unknown>)[key])
  }
  return sorted
}

export async function canonicalHash(value: unknown): Promise<string> {
  const encoded = JSON.stringify(canonicalize(value))
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(encoded))
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}
