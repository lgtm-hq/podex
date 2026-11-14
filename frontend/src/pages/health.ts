/** HTTP liveness only: no database, backend, or credential dependency. */
export const GET = (): Response =>
  new Response(JSON.stringify({ status: "ok" }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
