/**
 * DualZoneTimer
 *
 * A horizontal progress bar that drains over time.
 * Zones (matching scathach.core.question.TimerZone):
 *   NORMAL  → green   (0 … timeLimit)
 *   PENALTY → yellow  (timeLimit … 2×timeLimit)
 *   EXPIRED → red     (past 2×timeLimit — answer auto-locked)
 *
 * The timer is driven entirely by JavaScript; no server polling.
 * `startedAt` is the ISO UTC string returned by the API when the question
 * was first presented.  `onElapsed` fires on every tick with the current
 * elapsed seconds so the parent can track it for answer submission.
 */

import { useEffect, useRef, useState } from "react";

interface Props {
  timeLimitS: number;   // NORMAL zone duration (seconds)
  startedAt: string;    // ISO UTC timestamp from API
  onElapsed?: (elapsedS: number) => void;
  onExpired?: () => void;
}

function zoneMeta(elapsed: number, limit: number) {
  if (elapsed <= limit)
    return { label: "On time", color: "bg-green-500", zone: "normal" } as const;
  if (elapsed <= limit * 2)
    return { label: "Penalty zone", color: "bg-yellow-400", zone: "penalty" } as const;
  return { label: "Time expired", color: "bg-red-600", zone: "expired" } as const;
}

export default function DualZoneTimer({ timeLimitS, startedAt, onElapsed, onExpired }: Props) {
  const [elapsed, setElapsed] = useState(0);
  const expiredFired = useRef(false);

  useEffect(() => {
    expiredFired.current = false;
    const origin = new Date(startedAt).getTime();
    const tick = () => {
      const e = (Date.now() - origin) / 1000;
      setElapsed(e);
      onElapsed?.(e);
      if (e > timeLimitS * 2 && !expiredFired.current) {
        expiredFired.current = true;
        onExpired?.();
      }
    };
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [startedAt, timeLimitS]);

  const penaltyLimit = timeLimitS * 2;
  const { label, color, zone } = zoneMeta(elapsed, timeLimitS);

  // Progress shrinks from 100% → 0% over the full penalty limit
  const progress = Math.max(0, 1 - elapsed / penaltyLimit);

  const formatTime = (s: number) => {
    const remaining = Math.max(0, penaltyLimit - s);
    const m = Math.floor(remaining / 60);
    const sec = Math.floor(remaining % 60);
    return m > 0 ? `${m}:${sec.toString().padStart(2, "0")}` : `${sec}s`;
  };

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-gray-400 font-mono">
        <span>{label}</span>
        <span>{formatTime(elapsed)}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${color} ${zone === "expired" ? "animate-pulse" : ""}`}
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    </div>
  );
}
