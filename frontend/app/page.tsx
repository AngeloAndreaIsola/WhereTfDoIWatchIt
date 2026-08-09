export const dynamic = "force-dynamic";

type Health = { status: string; db: string };

async function checkBackend(): Promise<{ ok: boolean; body: string }> {
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${backendUrl}/api/health/`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as Health;
    return { ok: true, body: `${data.status} — db: ${data.db}` };
  } catch (err) {
    return { ok: false, body: err instanceof Error ? err.message : "unknown error" };
  }
}

export default async function Home() {
  const { ok, body } = await checkBackend();

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-2xl font-semibold">Streaming Tracker</h1>
      <p className="text-sm text-gray-500">Phase 0 scaffold — backend connectivity check</p>
      <div
        className={`rounded-md border px-4 py-2 text-sm font-mono ${
          ok ? "border-green-500 text-green-700" : "border-red-500 text-red-700"
        }`}
      >
        backend: {body}
      </div>
    </main>
  );
}
