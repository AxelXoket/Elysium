/**
 * ElysiumMark - the app icon. This is the real brand illustration (the misty
 * wave-swirl with the sprig and sparkle), processed once into a transparent
 * PNG with the warm sprig shifted into the Azure palette (scripts in
 * scratchpad/process_icon.py). Transparent background so it sits cleanly on
 * both the dark auth/sidebar surfaces and the light chat canvas.
 *
 * Served from /public, so no bundler import and no remote asset.
 */
interface ElysiumMarkProps {
  size?: number;
  className?: string;
  /** Accessible name; when omitted the mark is decorative (aria-hidden). */
  title?: string;
}

export function ElysiumMark({ size = 40, className, title }: ElysiumMarkProps) {
  return (
    <img
      src="/elysium-icon.png?v=3"
      width={size}
      height={size}
      alt={title ?? ""}
      aria-hidden={title ? undefined : true}
      className={className}
      draggable={false}
      style={{ objectFit: "contain", display: "block", userSelect: "none" }}
    />
  );
}
