/**
 * AnswerEditor — resizable textarea for submitting answers.
 * Disables input when `locked` is true (e.g. timer expired).
 */

import { useEffect, useRef } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  locked?: boolean;
  placeholder?: string;
  minRows?: number;
}

export default function AnswerEditor({
  value,
  onChange,
  locked = false,
  placeholder = "Type your answer here…",
  minRows = 6,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-resize
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  const wordCount = value.trim() ? value.trim().split(/\s+/).length : 0;

  return (
    <div className="space-y-1">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => !locked && onChange(e.target.value)}
        disabled={locked}
        placeholder={placeholder}
        rows={minRows}
        className={`w-full resize-none rounded-lg border px-4 py-3 font-mono text-sm leading-relaxed outline-none transition
          ${locked
            ? "cursor-not-allowed border-gray-700 bg-gray-900 text-gray-500"
            : "border-gray-600 bg-gray-800 text-gray-100 focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
          }`}
      />
      <div className="text-right text-xs text-gray-500 font-mono">
        {wordCount} {wordCount === 1 ? "word" : "words"}
        {locked && <span className="ml-3 text-red-400 font-semibold">Input locked — time expired</span>}
      </div>
    </div>
  );
}
