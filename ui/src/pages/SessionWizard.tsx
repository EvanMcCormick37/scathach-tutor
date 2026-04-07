/**
 * SessionWizard — pre-session configuration modal.
 * Navigates to /sessions/:id on submit.
 */

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";

export default function SessionWizard() {
  const { topicId } = useParams<{ topicId: string }>();
  const navigate = useNavigate();

  const [timing, setTiming] = useState<"timed" | "untimed">("untimed");
  const [threshold, setThreshold] = useState(7);
  const [numLevels, setNumLevels] = useState(6);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    try {
      const res = await api.createSession(Number(topicId), timing, threshold, numLevels);
      navigate(`/sessions/${res.session_id}`, { state: res });
    } catch (e) {
      setError(String(e));
      setStarting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-gray-700 bg-gray-900 p-8 space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Configure Session</h1>
          <Link to={`/topics/${topicId}`} className="text-sm text-gray-500 hover:text-gray-300">✕</Link>
        </div>

        {error && (
          <div className="text-sm text-red-300 bg-red-900/20 border border-red-700 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {/* Timing */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-400">Timing Mode</label>
          <div className="grid grid-cols-2 gap-2">
            {(["untimed", "timed"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTiming(t)}
                className={`rounded-lg border py-2 text-sm font-medium transition
                  ${timing === t
                    ? "border-brand-500 bg-brand-900/30 text-brand-300"
                    : "border-gray-700 text-gray-500 hover:border-gray-500"}`}
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Pass threshold */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-400">
            Pass Threshold — <span className="text-gray-200 font-mono">{threshold}/10</span>
          </label>
          <input
            type="range"
            min={5}
            max={10}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="w-full accent-brand-500"
          />
          <div className="flex justify-between text-xs text-gray-600 font-mono">
            <span>5</span><span>10</span>
          </div>
        </div>

        {/* Difficulty levels */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-400">
            Difficulty Levels — <span className="text-gray-200 font-mono">1–{numLevels}</span>
          </label>
          <input
            type="range"
            min={1}
            max={6}
            value={numLevels}
            onChange={(e) => setNumLevels(Number(e.target.value))}
            className="w-full accent-brand-500"
          />
          <div className="flex justify-between text-xs text-gray-600 font-mono">
            <span>1</span><span>6</span>
          </div>
        </div>

        <button
          onClick={handleStart}
          disabled={starting}
          className="w-full rounded-xl bg-brand-600 py-3 font-semibold text-white hover:bg-brand-500 disabled:opacity-50 transition"
        >
          {starting ? "Generating questions…" : "Start Session →"}
        </button>
      </div>
    </div>
  );
}
