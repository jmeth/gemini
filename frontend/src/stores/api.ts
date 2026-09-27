/**
 * JSON GET that refuses to guess.
 *
 * A path missing from the Vite dev proxy does not 404 — the dev server answers
 * with index.html, so `res.ok` is true and only `res.json()` fails. Swallowing
 * that made a proxy gap look like disconnected hardware, so it is reported.
 */
export async function getJson<T>(url: string): Promise<T | null> {
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    return null   // backend down; the header already shows that
  }

  if (!res.ok) {
    console.warn(`[api] ${url} -> HTTP ${res.status}`)
    return null
  }

  const type = res.headers.get('content-type') ?? ''
  if (!type.includes('application/json')) {
    console.error(
      `[api] ${url} returned ${type || 'no content-type'}, not JSON. ` +
      `In dev this usually means the path is missing from the proxy table in vite.config.ts.`
    )
    return null
  }

  try {
    return await res.json() as T
  } catch (err) {
    console.error(`[api] ${url} returned malformed JSON`, err)
    return null
  }
}
