/**
 * ReadingRulesSection.tsx - how the user says a name, so the engine says it too.
 *
 * The machinery for this shipped a long time ago: `pronunciations` is threaded
 * through speech_prep, SpeechQueue, SpeakHook and open_speaker, unit-tested at
 * every layer - and no production caller ever passed one. So somebody whose
 * character is called "Aoife" heard it read wrong in every single reply, and
 * the Settings section that was supposed to fix it did not exist. This is that
 * section; the pipe underneath it is unchanged.
 *
 * The whole table is sent on every save. Editing reading rules is a list
 * operation - entries are removed at least as often as they are added - and a
 * merge-only endpoint cannot express a deletion.
 */

import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { getPronunciations, savePronunciations } from "@/lib/api/tts";
import { isApiError } from "@/lib/api/client";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { useErrorStore } from "@/lib/errors/errorStore";

interface Rule {
  /** Stable across re-orders and edits; the written form is not. */
  key: number;
  said: string;
  as: string;
}

let nextKey = 1;

function toRules(table: Record<string, string>): Rule[] {
  return Object.entries(table).map(([said, as]) => ({
    key: nextKey++,
    said,
    as,
  }));
}

/** Last writer wins on a duplicate written form - the same thing the backend
 *  does, so what the user sees after a save is what they typed. */
function toTable(rules: Rule[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rule of rules) {
    const said = rule.said.trim();
    if (said) out[said] = rule.as.trim();
  }
  return out;
}

export function ReadingRulesSection() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [limits, setLimits] = useState({ maxEntries: 200, maxChars: 80 });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    getPronunciations()
      .then((body) => {
        if (!alive) return;
        setRules(toRules(body.pronunciations));
        setLimits({ maxEntries: body.max_entries, maxChars: body.max_chars });
      })
      .catch((err: unknown) => {
        if (!alive) return;
        // Same split DeliverySection makes: "voice is not set up" is not a
        // failure and shows nothing; anything else is a failure and says so,
        // because a section that vanishes on a 500 presents a broken backend
        // as a feature that does not exist.
        if (
          isApiError(err) &&
          (err.status === 404 || err.detail === "tts_not_configured")
        ) {
          return;
        }
        setLoadError(getErrorMessage(isApiError(err) ? err.detail : undefined));
      });
    return () => {
      alive = false;
    };
  }, []);

  const dirtyTable = useMemo(() => (rules ? toTable(rules) : {}), [rules]);
  const full = rules != null && rules.length >= limits.maxEntries;

  if (loadError) {
    return (
      <p role="alert" className="settings-hint">
        {loadError}
      </p>
    );
  }
  if (!rules) return null;

  const persist = (next: Rule[]) => {
    setRules(next);
    setSaving(true);
    savePronunciations(toTable(next))
      .then((body) => setLimits({
        maxEntries: body.max_entries,
        maxChars: body.max_chars,
      }))
      .catch((err) => useErrorStore.getState().pushError(err))
      .finally(() => setSaving(false));
  };

  const update = (key: number, patch: Partial<Rule>) =>
    setRules(rules.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <section
      aria-label="Reading rules"
      className="settings-voice-section space-y-2"
    >
      <h3 className="settings-section-title">Reading rules</h3>
      <p className="settings-category-desc">
        How to say a word the voice gets wrong - a name, an initialism, a word
        from another language. Write it the way it should sound. Applies to
        spoken replies only; the chat text never changes.
      </p>

      {rules.map((rule) => (
        <div key={rule.key} className="settings-voice-row">
          <input
            className="settings-voice-input min-w-0 flex-1"
            aria-label="Written as"
            placeholder="Aoife"
            maxLength={limits.maxChars}
            value={rule.said}
            onChange={(e) => update(rule.key, { said: e.currentTarget.value })}
            onBlur={() => persist(rules)}
          />
          <span aria-hidden className="settings-category-desc shrink-0 px-1">
            →
          </span>
          <input
            className="settings-voice-input min-w-0 flex-1"
            aria-label="Said as"
            placeholder="EE-fa"
            maxLength={limits.maxChars}
            value={rule.as}
            onChange={(e) => update(rule.key, { as: e.currentTarget.value })}
            onBlur={() => persist(rules)}
          />
          <button
            type="button"
            className="settings-voice-button is-quiet"
            aria-label={`Remove rule for ${rule.said || "this word"}`}
            disabled={saving}
            onClick={() => persist(rules.filter((r) => r.key !== rule.key))}
          >
            <Trash2 size={12} />
          </button>
        </div>
      ))}

      <button
        type="button"
        className="settings-voice-button inline-flex items-center gap-1"
        disabled={full}
        title={
          full
            ? `That is the most rules Elysium will apply (${limits.maxEntries}).`
            : undefined
        }
        onClick={() =>
          setRules([...rules, { key: nextKey++, said: "", as: "" }])
        }
      >
        <Plus size={12} />
        Add a rule
      </button>

      {Object.keys(dirtyTable).length === 0 ? (
        <p className="settings-category-desc">
          No rules yet. Names are the usual reason to add one.
        </p>
      ) : null}
    </section>
  );
}
