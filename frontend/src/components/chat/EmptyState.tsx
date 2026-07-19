import { ElysiumMark } from "@/components/brand/ElysiumMark";
import { Wordmark } from "@/components/brand/Wordmark";

export function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div
        className="flex flex-col items-center rounded-xl px-10 py-12 text-center"
        style={{
          backgroundColor: "rgba(28, 38, 50, 0.06)",
          maxWidth: "400px",
          border: "1px solid rgba(28, 38, 50, 0.09)",
        }}
      >
        {/* The mark is tuned bright for the dark auth/sidebar surfaces; on
            this light canvas it is darkened for contrast so it reads here too. */}
        <span
          className="mb-4 inline-flex"
          style={{ filter: "brightness(0.42) saturate(1.15)" }}
        >
          <ElysiumMark size={90} />
        </span>
        <span className="mb-1 flex items-baseline gap-1.5">
          <span
            className="text-base font-semibold"
            style={{ color: "var(--color-es-asst-bubble-text)" }}
          >
            Welcome to
          </span>
          <Wordmark size={19} tone="onLight" />
        </span>
        <p
          className="mt-1 text-sm leading-relaxed"
          style={{ color: "var(--color-es-asst-bubble-text)", opacity: 0.6 }}
        >
          Select a character and start a chat to begin.
        </p>
      </div>
    </div>
  );
}
