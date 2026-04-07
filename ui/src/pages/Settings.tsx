import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Config } from "../api";

export default function Settings() {
  const [config, setConfig] = useState<Config | null>(null);
  const [form, setForm] = useState<Partial<Config & { api_key: string }>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setForm({ ...c, api_key: "" });
    }).catch((e) => setError(String(e)));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const patch: Record<string, unknown> = {};
      if (form.api_key) patch.api_key = form.api_key;
      if (form.model !== config?.model) patch.model = form.model;
      if (form.quality_threshold !== config?.quality_threshold) patch.quality_threshold = form.quality_threshold;
      if (form.main_timing !== config?.main_timing) patch.main_timing = form.main_timing;
      if (form.review_timing !== config?.review_timing) patch.review_timing = form.review_timing;
      if (form.hydra_in_super_review !== config?.hydra_in_super_review) patch.hydra_in_super_review = form.hydra_in_super_review;
      if (form.open_doc_on_session !== config?.open_doc_on_session) patch.open_doc_on_session = form.open_doc_on_session;
      const updated = await api.patchConfig(patch);
      setConfig(updated);
      setForm({ ...updated, api_key: "" });
      setSaved(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testConfig();
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, message: String(e) });
    } finally {
      setTesting(false);
    }
  };

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  if (!config) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-600">Loading…</div>
  );

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-4">
        <Link to="/" className="text-gray-500 hover:text-gray-300 text-sm">← Dashboard</Link>
        <h1 className="text-base font-semibold">Settings</h1>
      </header>

      <main className="max-w-xl mx-auto px-6 py-8 space-y-6">
        {error && (
          <div className="text-sm text-red-300 bg-red-900/20 border border-red-700 rounded-lg px-3 py-2">{error}</div>
        )}

        {/* API Key */}
        <Section title="API Key">
          <Field label={`OpenRouter API Key ${config.has_api_key ? "(set)" : "(not set)"}`}>
            <input
              type="password"
              className="input"
              placeholder={config.has_api_key ? "••••••••••••••••" : "sk-or-…"}
              value={form.api_key ?? ""}
              onChange={(e) => set("api_key", e.target.value)}
            />
          </Field>
          <button
            onClick={handleTest}
            disabled={testing}
            className="mt-2 rounded-lg border border-gray-600 px-4 py-2 text-sm hover:border-brand-500 hover:text-brand-300 transition disabled:opacity-40"
          >
            {testing ? "Testing…" : "Test Connection"}
          </button>
          {testResult && (
            <div className={`mt-2 text-sm rounded-lg px-3 py-2 ${testResult.ok ? "bg-green-900/20 text-green-300 border border-green-700" : "bg-red-900/20 text-red-300 border border-red-700"}`}>
              {testResult.message}
            </div>
          )}
        </Section>

        {/* Model */}
        <Section title="Model">
          <Field label="LLM Model (via OpenRouter)">
            <input
              className="input font-mono text-sm"
              value={form.model ?? ""}
              onChange={(e) => set("model", e.target.value)}
              placeholder="e.g. qwen/qwen3.6-plus:free"
            />
          </Field>
        </Section>

        {/* Session defaults */}
        <Section title="Session Defaults">
          <Field label={`Pass Threshold: ${form.quality_threshold ?? 7}/10`}>
            <input
              type="range" min={5} max={10}
              value={form.quality_threshold ?? 7}
              onChange={(e) => set("quality_threshold", Number(e.target.value))}
              className="w-full accent-brand-500"
            />
          </Field>
          <Field label="Main Session Timing">
            <ToggleField
              value={form.main_timing ?? "untimed"}
              onChange={(v) => set("main_timing", v)}
              options={["timed", "untimed"]}
            />
          </Field>
          <Field label="Review Timing">
            <ToggleField
              value={form.review_timing ?? "untimed"}
              onChange={(v) => set("review_timing", v)}
              options={["timed", "untimed"]}
            />
          </Field>
          <label className="flex items-center gap-3 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={form.hydra_in_super_review ?? false}
              onChange={(e) => set("hydra_in_super_review", e.target.checked)}
              className="accent-brand-500 w-4 h-4"
            />
            Hydra Protocol in Super-Review
          </label>
          <label className="flex items-center gap-3 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={form.open_doc_on_session ?? false}
              onChange={(e) => set("open_doc_on_session", e.target.checked)}
              className="accent-brand-500 w-4 h-4"
            />
            Open source document at session start
          </label>
        </Section>

        <div className="flex items-center gap-4">
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-xl bg-brand-600 px-6 py-2 text-sm font-semibold text-white hover:bg-brand-500 disabled:opacity-40 transition"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saved && <span className="text-sm text-green-400">✓ Saved</span>}
        </div>
      </main>

      <style>{`.input { @apply w-full rounded-lg border border-gray-600 bg-gray-800 px-3 py-2 text-sm outline-none focus:border-brand-400 transition; }`}</style>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-gray-500">{title}</h2>
      <div className="rounded-xl border border-gray-700 bg-gray-900 px-5 py-4 space-y-4">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs text-gray-400">{label}</label>
      {children}
    </div>
  );
}

function ToggleField({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <div className="flex gap-2">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={`rounded-lg border px-3 py-1 text-xs font-medium transition
            ${value === o ? "border-brand-500 bg-brand-900/30 text-brand-300" : "border-gray-700 text-gray-500 hover:border-gray-500"}`}
        >
          {o}
        </button>
      ))}
    </div>
  );
}
