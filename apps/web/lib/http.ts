/** The `detail` message FastAPI puts in an error response, if there is one. */
export async function errorDetail(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  return undefined;
}
