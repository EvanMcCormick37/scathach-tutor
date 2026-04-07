import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, SessionSummary, Topic } from "../api";

export default function TopicDetail() {
  const { topicId } = useParams<{ topicId: string }>();
  const navigate = useNavigate();
  const [topic, setTopic] = useState<Topic | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = Number(topicId);
    Promise.all([api.listTopics(), api.listSessions()]).then(([t, s]) => {
      const found = t.topics.find((x) => x.id === id) ?? null;
      setTopic(found);
      if (found) setNewName(found.name);
      setSessions(s.filter((x) => x.topic_id === id));
    });
  }, [topicId]);

  const handleRename = async () => {
    if (!topic || !newName.trim()) return;
    try {
      const updated = await api.renameTopic(topic.id, newName.trim());
      setTopic(updated);
      setRenaming(false);
    } catch (e) {
      setError(String(e));
    }
  };

  const activeSessions = sessions.filter((s) => s.status === "active");

  if (!topic) return <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-600">Loading…</div>;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-4">
        <Link to="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        <div className="flex-1 flex items-center gap-3">
          {renaming ? (
            <>
              <input
                autoFocus
                className="rounded-lg border border-brand-500 bg-gray-800 px-3 py-1 text-sm outline-none"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleRename(); if (e.key === "Escape") setRenaming(false); }}
              />
              <button onClick={handleRename} className="text-xs text-brand-400 hover:text-brand-200">Save</button>
              <button onClick={() => setRenaming(false)} className="text-xs text-gray-500 hover:text-gray-300">Cancel</button>
            </>
          ) : (
            <>
              <h1 className="text-lg font-semibold">{topic.name}</h1>
              <button onClick={() => setRenaming(true)} className="text-xs text-gray-600 hover:text-gray-400">✎ rename</button>
            </>
          )}
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-8 space-y-6">
        {error && (
          <div className="text-sm text-red-300 bg-red-900/20 border border-red-700 rounded-lg px-4 py-3">
            {error}
          </div>
        )}

        {topic.source_path && (
          <div className="text-xs text-gray-600 font-mono">{topic.source_path}</div>
        )}

        {/* Start new session */}
        <button
          onClick={() => navigate(`/topics/${topic.id}/new-session`)}
          className="w-full rounded-xl border border-brand-600 bg-brand-900/20 py-4 text-center font-semibold text-brand-300 hover:bg-brand-900/40 transition"
        >
          ＋ New Session
        </button>

        {/* Active sessions */}
        {activeSessions.length > 0 && (
          <div className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-widest text-gray-500">Active Sessions</h2>
            {activeSessions.map((s) => (
              <button
                key={s.session_id}
                onClick={() => navigate(`/sessions/${s.session_id}`)}
                className="w-full text-left flex items-center justify-between rounded-lg border border-yellow-700/40 bg-yellow-900/10 px-4 py-3 hover:bg-yellow-900/20 transition"
              >
                <div>
                  <div className="text-sm font-medium text-yellow-300">
                    {s.cleared_count}/{s.num_levels} cleared · {s.timing}
                  </div>
                  <div className="text-xs text-gray-600 mt-0.5">Started {new Date(s.created_at).toLocaleString()}</div>
                </div>
                <span className="text-yellow-500 text-xs">Resume →</span>
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
