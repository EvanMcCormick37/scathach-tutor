import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, SessionSummary, Topic } from "../api";

export default function Dashboard() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteName, setPasteName] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const navigate = useNavigate();

  const load = async () => {
    try {
      const [t, s] = await Promise.all([api.listTopics(), api.listSessions()]);
      setTopics(t.topics);
      setSessions(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIngesting(true);
    try {
      await api.ingestFile(file);
      await load();
    } catch (err) {
      setError(String(err));
    } finally {
      setIngesting(false);
      e.target.value = "";
    }
  };

  const handlePasteSubmit = async () => {
    if (!pasteText.trim() || !pasteName.trim()) return;
    setIngesting(true);
    try {
      await api.ingestPaste(pasteText, pasteName);
      setPasteOpen(false);
      setPasteText("");
      setPasteName("");
      await load();
    } catch (err) {
      setError(String(err));
    } finally {
      setIngesting(false);
    }
  };

  const activeSessions = sessions.filter((s) => s.status === "active");

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Top bar */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight text-brand-300">⚔ scathach</h1>
        <nav className="flex gap-4 text-sm">
          <Link to="/review" className="hover:text-brand-300 transition">Review</Link>
          <Link to="/super-review" className="hover:text-brand-300 transition">Super-Review</Link>
          <Link to="/stats" className="hover:text-brand-300 transition">Stats</Link>
          <Link to="/settings" className="hover:text-brand-300 transition">Settings</Link>
        </nav>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8 space-y-8">
        {error && (
          <div className="rounded-lg bg-red-900/30 border border-red-700 px-4 py-3 text-red-300 text-sm">
            {error}
            <button onClick={() => setError(null)} className="ml-4 text-red-500 hover:text-red-300">✕</button>
          </div>
        )}

        {/* Ingest controls */}
        <section className="flex flex-wrap gap-3">
          <label className={`cursor-pointer rounded-lg border border-brand-600 px-4 py-2 text-sm font-medium text-brand-300 hover:bg-brand-900/30 transition ${ingesting ? "opacity-50 pointer-events-none" : ""}`}>
            {ingesting ? "Ingesting…" : "＋ Upload File"}
            <input type="file" className="hidden" onChange={handleFileUpload} accept=".pdf,.docx,.pptx,.html,.txt,.md,.rst" />
          </label>
          <button
            onClick={() => setPasteOpen(true)}
            className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 hover:border-brand-500 hover:text-brand-300 transition"
          >
            ＋ Paste Text
          </button>
        </section>

        {/* Active sessions */}
        {activeSessions.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-widest text-gray-500 mb-3">Resume</h2>
            <div className="space-y-2">
              {activeSessions.map((s) => (
                <button
                  key={s.session_id}
                  onClick={() => navigate(`/sessions/${s.session_id}`)}
                  className="w-full text-left flex items-center justify-between rounded-lg border border-yellow-700/50 bg-yellow-900/10 px-4 py-3 hover:bg-yellow-900/20 transition"
                >
                  <div>
                    <div className="font-medium text-yellow-300">{s.topic_name}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {s.cleared_count}/{s.num_levels} cleared · {s.timing}
                    </div>
                  </div>
                  <span className="text-yellow-500 text-xs">Resume →</span>
                </button>
              ))}
            </div>
          </section>
        )}

        {/* Topics grid */}
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-widest text-gray-500 mb-3">Topics</h2>
          {loading ? (
            <div className="text-gray-600 text-sm">Loading…</div>
          ) : topics.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-700 px-6 py-10 text-center text-gray-600 text-sm">
              No topics yet. Upload a document or paste text to get started.
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {topics.map((t) => (
                <Link
                  key={t.id}
                  to={`/topics/${t.id}`}
                  className="group rounded-lg border border-gray-700 bg-gray-900 px-5 py-4 hover:border-brand-600 transition"
                >
                  <div className="font-medium text-gray-100 group-hover:text-brand-300 transition truncate">{t.name}</div>
                  {t.source_path && (
                    <div className="text-xs text-gray-600 mt-1 truncate">{t.source_path}</div>
                  )}
                  <div className="text-xs text-gray-600 mt-2">{new Date(t.created_at).toLocaleDateString()}</div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>

      {/* Paste modal */}
      {pasteOpen && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="w-full max-w-lg rounded-xl border border-gray-700 bg-gray-900 p-6 space-y-4">
            <h2 className="text-lg font-semibold">Paste Text</h2>
            <input
              className="w-full rounded-lg border border-gray-600 bg-gray-800 px-3 py-2 text-sm outline-none focus:border-brand-400"
              placeholder="Topic name"
              value={pasteName}
              onChange={(e) => setPasteName(e.target.value)}
            />
            <textarea
              className="w-full h-48 resize-none rounded-lg border border-gray-600 bg-gray-800 px-3 py-2 text-sm font-mono outline-none focus:border-brand-400"
              placeholder="Paste your text here…"
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
            <div className="flex justify-end gap-3">
              <button onClick={() => setPasteOpen(false)} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200">
                Cancel
              </button>
              <button
                onClick={handlePasteSubmit}
                disabled={ingesting || !pasteText.trim() || !pasteName.trim()}
                className="px-4 py-2 text-sm rounded-lg bg-brand-600 text-white hover:bg-brand-500 disabled:opacity-50 transition"
              >
                {ingesting ? "Ingesting…" : "Ingest"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
