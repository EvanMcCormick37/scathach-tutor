/**
 * SessionScreen — the active Q&A interface.
 *
 * Receives initial state via React Router location.state (from SessionWizard)
 * or re-fetches it by session_id from the URL for direct navigation / resume.
 */

import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { AnswerResult, api, Question, QuestionContext, SessionCreateResponse } from "../api";
import AnswerEditor from "../components/AnswerEditor";
import DualZoneTimer from "../components/DualZoneTimer";
import HydraNotice from "../components/HydraNotice";
import SessionProgress from "../components/SessionProgress";

const DIFFICULTY_TIME_LIMITS: Record<number, number> = {
  1: 30, 2: 60, 3: 300, 4: 600, 5: 900, 6: 1800,
};

type Phase = "question" | "result" | "complete";

interface State {
  question: Question;
  context: QuestionContext;
  result?: AnswerResult;
  phase: Phase;
  clearedCount: number;
  numLevels: number;
  sessionId: string;
}

export default function SessionScreen() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const initData = location.state as SessionCreateResponse | undefined;

  const [state, setState] = useState<State | null>(null);
  const [answer, setAnswer] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [timerLocked, setTimerLocked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hydraCount, setHydraCount] = useState(0);
  const answerRef = useRef<string>("");
  answerRef.current = answer;

  // Initialize state from location or session reload
  useEffect(() => {
    if (initData && initData.session_id === sessionId) {
      setState({
        question: initData.question,
        context: initData.context,
        phase: "question",
        clearedCount: 0,
        numLevels: initData.context.total,
        sessionId: sessionId!,
      });
    } else {
      // Resume: get summary then show current state
      api.getSession(sessionId!).catch(() => navigate("/"));
      // For a full resume we'd need a /sessions/{id}/current-question endpoint.
      // For now redirect to home if state is missing (user can click Resume there).
      if (!initData) navigate("/");
    }
  }, []);

  const handleTimerExpired = () => {
    setTimerLocked(true);
  };

  const handleSubmit = async () => {
    if (!state || submitting) return;
    const text = answerRef.current.trim();
    if (!text) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.submitAnswer(
        state.sessionId,
        text,
        state.context.is_timed ? elapsed : undefined
      );
      setHydraCount(result.hydra_spawned ? result.subquestion_count : 0);
      if (result.is_complete) {
        setState((s) => s ? { ...s, result, phase: "complete", clearedCount: result.cleared_count ?? s.clearedCount } : s);
      } else {
        setState((s) => s ? {
          ...s,
          result,
          phase: "result",
          clearedCount: result.passed
            ? (result.next_context?.depth === 0 ? (s.clearedCount + (s.context.depth === 0 ? 1 : 0)) : s.clearedCount)
            : s.clearedCount,
        } : s);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleNext = () => {
    if (!state?.result?.next_question || !state.result.next_context) return;
    setAnswer("");
    setElapsed(0);
    setTimerLocked(false);
    setState((s) => s ? {
      ...s,
      question: state.result!.next_question!,
      context: state.result!.next_context!,
      phase: "question",
      result: undefined,
    } : s);
  };

  if (!state) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-600">Loading…</div>
  );

  const timeLimit = DIFFICULTY_TIME_LIMITS[state.question.difficulty] ?? 60;
  const isTimed = state.context.is_timed;

  // --- Complete screen ---
  if (state.phase === "complete") {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center p-4">
        <div className="max-w-md w-full text-center space-y-6">
          <div className="text-5xl">🏆</div>
          <h1 className="text-2xl font-bold text-brand-300">Session Complete!</h1>
          <p className="text-gray-400">
            {state.result?.cleared_count ?? state.clearedCount} levels cleared in{" "}
            {state.result?.total_attempts ?? "?"} attempts.
          </p>
          <Link
            to="/"
            className="inline-block rounded-xl bg-brand-600 px-8 py-3 font-semibold text-white hover:bg-brand-500 transition"
          >
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  // --- Result screen ---
  if (state.phase === "result" && state.result) {
    const r = state.result;
    const passed = r.passed;
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-4">
          <Link to="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        </header>
        <main className="max-w-2xl mx-auto px-6 py-8 space-y-6">
          {hydraCount > 0 && <HydraNotice count={hydraCount} onDismiss={() => setHydraCount(0)} />}

          <div className={`rounded-xl border px-6 py-5 space-y-3 ${passed ? "border-green-700 bg-green-900/10" : "border-red-700 bg-red-900/10"}`}>
            <div className="flex items-center justify-between">
              <span className={`text-lg font-bold ${passed ? "text-green-400" : "text-red-400"}`}>
                {passed ? "✓ Passed" : "✗ Failed"}
              </span>
              <span className="font-mono text-xl font-bold">
                {r.final_score}/10
                {r.time_penalty && <span className="text-xs text-yellow-400 ml-2">(penalty)</span>}
              </span>
            </div>
            <p className="text-sm text-gray-300 leading-relaxed">{r.diagnosis}</p>
            {!passed && (
              <details className="text-sm">
                <summary className="cursor-pointer text-gray-500 hover:text-gray-300">Show ideal answer</summary>
                <p className="mt-2 text-gray-400 font-mono leading-relaxed whitespace-pre-wrap">{r.ideal_answer}</p>
              </details>
            )}
          </div>

          <button
            onClick={handleNext}
            className="w-full rounded-xl bg-brand-600 py-3 font-semibold text-white hover:bg-brand-500 transition"
          >
            {r.hydra_spawned ? "Start Sub-Questions →" : "Next Question →"}
          </button>
        </main>
      </div>
    );
  }

  // --- Question screen ---
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-4">
        <Link to="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        <span className="text-sm text-gray-600 font-mono ml-auto">
          Q{state.context.index}/{state.context.total}
          {state.context.depth > 0 && <span className="text-yellow-500 ml-2">⚡ depth {state.context.depth}</span>}
        </span>
      </header>

      <div className="max-w-3xl mx-auto px-6 py-8 grid grid-cols-1 md:grid-cols-[1fr_180px] gap-8">
        <main className="space-y-6">
          {error && (
            <div className="text-sm text-red-300 bg-red-900/20 border border-red-700 rounded-lg px-4 py-3">
              {error}
              <button onClick={() => setError(null)} className="ml-3 text-red-500">✕</button>
            </div>
          )}

          {/* Question */}
          <div className="rounded-xl border border-gray-700 bg-gray-900 px-6 py-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-xs font-mono bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                Level {state.question.difficulty}
              </span>
              {state.context.depth > 0 && (
                <span className="text-xs font-mono bg-yellow-900/30 text-yellow-400 px-2 py-0.5 rounded">
                  Hydra
                </span>
              )}
            </div>
            <p className="text-gray-100 leading-relaxed whitespace-pre-wrap">{state.question.body}</p>
          </div>

          {/* Timer */}
          {isTimed && (
            <DualZoneTimer
              timeLimitS={timeLimit}
              startedAt={state.context.started_at}
              onElapsed={setElapsed}
              onExpired={handleTimerExpired}
            />
          )}

          {!isTimed && (
            <p className="text-xs text-gray-600">
              Untimed mode — press Submit when ready. (Escape+Enter also submits)
            </p>
          )}

          {/* Answer */}
          <AnswerEditor
            value={answer}
            onChange={setAnswer}
            locked={timerLocked}
          />

          <button
            onClick={handleSubmit}
            disabled={submitting || !answer.trim() || timerLocked}
            className="w-full rounded-xl bg-brand-600 py-3 font-semibold text-white hover:bg-brand-500 disabled:opacity-40 transition"
          >
            {submitting ? "Scoring…" : "Submit Answer"}
          </button>
        </main>

        {/* Sidebar */}
        <aside className="hidden md:block">
          <SessionProgress
            numLevels={state.numLevels}
            clearedCount={state.clearedCount}
            currentIndex={state.context.index}
          />
        </aside>
      </div>
    </div>
  );
}
