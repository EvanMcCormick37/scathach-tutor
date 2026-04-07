/**
 * StatsScreen — shows a summary of review queue stats and topics.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ReviewQueueResponse, Topic } from "../api";

export default function StatsScreen() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [reviewDue, setReviewDue] = useState<ReviewQueueResponse | null>(null);
  const [superDue, setSuperDue] = useState<ReviewQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.listTopics(),
      api.getDueQuestions("untimed", "review", 100),
      api.getDueQuestions("untimed", "super-review", 100),
    ])
      .then(([t, r, sr]) => {
        setTopics(t.topics);
        setReviewDue(r);
        setSuperDue(sr);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-4">
        <Link to="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        <h1 className="text-base font-semibold">Stats</h1>
      </header>
      <main className="max-w-2xl mx-auto px-6 py-8 space-y-8">
        {error && (
          <div className="text-sm text-red-300 bg-red-900/20 border border-red-700 rounded-lg px-3 py-2">{error}</div>
        )}
        {loading ? (
          <div className="text-gray-600">Loading…</div>
        ) : (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-3 gap-4">
              <StatCard label="Topics" value={topics.length} />
              <StatCard label="Review Due" value={reviewDue?.total_due ?? 0} accent="text-blue-400" />
              <StatCard label="Super-Review Due" value={superDue?.total_due ?? 0} accent="text-purple-400" />
            </div>

            {/* Due questions breakdown */}
            {(reviewDue?.total_due ?? 0) > 0 && (
              <section>
                <h2 className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">Review Due (Levels 1–2)</h2>
                <div className="space-y-1">
                  {reviewDue?.questions.map((q) => (
                    <div key={q.id} className="flex items-start gap-3 rounded-lg bg-gray-900 px-4 py-2 text-sm">
                      <span className="text-xs font-mono bg-gray-800 text-gray-500 px-1.5 py-0.5 rounded mt-0.5">L{q.difficulty}</span>
                      <span className="text-gray-400 line-clamp-2">{q.body}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Topics list */}
            <section>
              <h2 className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">Topics ({topics.length})</h2>
              <div className="space-y-1">
                {topics.map((t) => (
                  <Link
                    key={t.id}
                    to={`/topics/${t.id}`}
                    className="flex items-center justify-between rounded-lg bg-gray-900 px-4 py-2 hover:bg-gray-800 transition text-sm"
                  >
                    <span className="text-gray-300">{t.name}</span>
                    <span className="text-xs text-gray-600">{new Date(t.created_at).toLocaleDateString()}</span>
                  </Link>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function StatCard({ label, value, accent = "text-gray-100" }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-xl border border-gray-700 bg-gray-900 px-4 py-5 text-center space-y-1">
      <div className={`text-3xl font-bold font-mono ${accent}`}>{value}</div>
      <div className="text-xs text-gray-500 uppercase tracking-wide">{label}</div>
    </div>
  );
}
