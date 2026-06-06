export function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div
        className="rounded-2xl px-10 py-12 text-center"
        style={{
          backgroundColor: "rgba(47, 49, 45, 0.07)",
          maxWidth: "400px",
          border: "1px solid rgba(47, 49, 45, 0.09)",
          boxShadow: "0 2px 16px rgba(47, 49, 45, 0.06)",
        }}
      >
        {/* Decorative sage orb — CSS only, no image */}
        <div
          className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl"
          style={{
            background: "linear-gradient(135deg, rgba(167,200,161,0.20) 0%, rgba(167,200,161,0.08) 100%)",
            border: "1px solid rgba(167,200,161,0.18)",
          }}
        >
          <span
            className="text-lg font-bold"
            style={{ color: "var(--color-es-primary-sage-deep)" }}
          >
            E
          </span>
        </div>
        <p
          className="text-base font-semibold"
          style={{ color: "var(--color-es-asst-bubble-text)" }}
        >
          Welcome to Elysium
        </p>
        <p
          className="mt-2 text-sm leading-relaxed"
          style={{ color: "var(--color-es-asst-bubble-text)", opacity: 0.6 }}
        >
          Select a character and start a chat to begin.
        </p>
      </div>
    </div>
  );
}
