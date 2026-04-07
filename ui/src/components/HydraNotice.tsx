/**
 * HydraNotice — dismissible banner shown when sub-questions are spawned.
 */

import { useState } from "react";

interface Props {
  count: number;
  onDismiss?: () => void;
}

export default function HydraNotice({ count, onDismiss }: Props) {
  const [visible, setVisible] = useState(true);
  if (!visible) return null;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-yellow-500/40 bg-yellow-900/20 px-4 py-3">
      <span className="text-yellow-400 text-lg">⚡</span>
      <div className="flex-1 text-sm text-yellow-200">
        <span className="font-semibold">Hydra Protocol activated — </span>
        {count} sub-question{count !== 1 ? "s" : ""} spawned to address knowledge gaps.
        Clear them to return to the parent question.
      </div>
      <button
        onClick={() => { setVisible(false); onDismiss?.(); }}
        className="text-yellow-500 hover:text-yellow-300 text-xs mt-0.5"
      >
        ✕
      </button>
    </div>
  );
}
