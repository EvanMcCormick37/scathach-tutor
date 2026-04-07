/**
 * SuperReviewScreen — processes the super-review queue (levels 3–6).
 * Mirrors ReviewScreen but fetches from mode=super-review.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Question, ReviewAnswerResult } from "../api";
import AnswerEditor from "../components/AnswerEditor";

type Phase = "loading" | "idle" | "question" | "result" | "done";

export default function SuperReviewScreen() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [queue, setQueue] = useState<Question[]>([]);
  const [qIndex, setQIndex] = useState(0);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<ReviewAnswerResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getDueQuestions("untimed", "super-review").then((r) => {
      setQueue(r.questions);
      setPhase(r.questions.length === 0 ? "idle" : "question");
    }).catch((e) => setError(String(e)));
  }, []);

  const current = queue[qIndex] ?? null;

  const handleSubmit = async () => {
    if (!current || submitting || !answer.trim()) return;
    setSubmitting(true);
    try {
      const r = await api.submitReviewAnswer(current.id, answer, "untimed", false);
      setResult(r);
      setPhase("result");
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleNext = () => {
    setAnswer("");
    setResult(null);
    const next = qIndex + 1;
    if (next >= queue.length) {
      setPhase("done");
    } else {
      setQIndex(next);
      setPhase("question");
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-4">
        <Link to="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        <h1 className="text-base font-semibold">Super-Review</h1>
        <span className="text-xs bg-purple-900/40 text-purple-300 px-2 py-0.5 rounded ml-1">Levels 3–6</span>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-8">
        {error && (
          <div className="text-sm text-red-300 bg-red-900/20 border border-red-700 rounded-lg px-3 py-2 mb-4">
            {error}
          </div>
        )}

        {phase === "loading" && <div className="text-gray-600">Loading queue…</div>}

        {phase === "idle" && (
          <div className="text-center py-16 text-gray-600">
            <div className="text-4xl mb-4">✓</div>
            <p>No questions due for super-review.</p>
            <Link to="/" className="mt-4 inline-block text-sm text-brand-400 hover:text-brand-300">Back</Link>
          </div>
        )}

        {phase === "done" && (
          <div className="text-center py-16 space-y-4">
            <div className="text-4xl">🏅</div>
            <p className="text-gray-300">Super-review complete — {queue.length} question{queue.length !== 1 ? "s" : ""} done!</p>
            <Link to="/" className="inline-block rounded-xl bg-brand-600 px-6 py-2 text-sm font-semibold text-white hover:bg-brand-500 transition">
              Back to Dashboard
            </Link>
          </div>
        )}

        {phase === "question" && current && (
          <div className="space-y-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-gray-500 font-mono">{qIndex + 1}/{queue.length}</span>
              <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded font-mono">Level {current.difficulty}</span>
            </div>
            <div className="rounded-xl border border-gray-700 bg-gray-900 px-5 py-4">
              <p className="text-gray-100 leading-relaxed whitespace-pre-wrap">{current.body}</p>
            </div>
            <AnswerEditor value={answer} onChange={setAnswer} />
            <button
              onClick={handleSubmit}
              disabled={submitting || !answer.trim()}
              className="w-full rounded-xl bg-brand-600 py-3 font-semibold text-white hover:bg-brand-500 disabled:opacity-40 transition"
            >
              {submitting ? "Scoring…" : "Submit"}
            </button>
          </div>
        )}

        {phase === "result" && result && (
          <div className="space-y-4">
            <div className={`rounded-xl border px-5 py-4 space-y-2 ${result.passed ? "border-green-700 bg-green-900/10" : "border-red-700 bg-red-900/10"}`}>
              <div className="flex justify-between">
                <span className={`font-bold ${result.passed ? "text-green-400" : "text-red-400"}`}>
                  {result.passed ? "✓ Passed" : "✗ Failed"}
                </span>
                <span className="font-mono font-bold">{result.final_score}/10</span>
              </div>
              <p className="text-sm text-gray-300">{result.diagnosis}</p>
              {!result.passed && (
                <details className="text-sm">
                  <summary className="cursor-pointer text-gray-500">Show ideal answer</summary>
                  <p className="mt-2 text-gray-400 font-mono whitespace-pre-wrap">{result.ideal_answer}</p>
                </details>
              )}
            </div>
            <button onClick={handleNext} className="w-full rounded-xl bg-brand-600 py-3 font-semibold text-white hover:bg-brand-500 transition">
              Next →
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
