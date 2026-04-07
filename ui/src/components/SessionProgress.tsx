/**
 * SessionProgress sidebar — shows which difficulty levels have been cleared.
 * Stars match the terminal CLI output.
 */

interface Props {
  numLevels: number;
  clearedCount: number;
  currentIndex: number;  // 1-based
}

const LEVEL_LABELS = [
  "Word / Phrase",
  "1–2 Sentences",
  "Paragraph",
  "1–2 Paragraphs",
  "Multi-paragraph",
  "Essay",
];

export default function SessionProgress({ numLevels, clearedCount, currentIndex }: Props) {
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-2">
        Progress
      </div>
      {Array.from({ length: numLevels }, (_, i) => {
        const level = i + 1;
        const cleared = level <= clearedCount;
        const active = level === currentIndex;
        return (
          <div
            key={level}
            className={`flex items-center gap-2 rounded px-2 py-1 text-xs font-mono transition
              ${cleared ? "text-green-400" : active ? "text-brand-300 bg-brand-900/30" : "text-gray-600"}`}
          >
            <span className="w-4 text-center">
              {cleared ? "★" : active ? "▶" : "○"}
            </span>
            <span>L{level} — {LEVEL_LABELS[i] ?? `Level ${level}`}</span>
          </div>
        );
      })}
    </div>
  );
}
