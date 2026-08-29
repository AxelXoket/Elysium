import { type ReactNode } from "react";
import { motion as m, type Variants } from "motion/react";
import { useReducedMotion } from "./ReducedMotion";

const variants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1 },
};

interface FadeInProps {
  children: ReactNode;
  className?: string;
  duration?: number;
  /**
   * Whether this instance actually fades. `false` mounts straight at the
   * visible state instead of animating to it.
   *
   * Added for the message list, where every bubble is wrapped in one of these
   * and a long chat therefore mounted three hundred simultaneous tweens - the
   * caller already knows which handful are near enough to the bottom to be
   * worth animating, and had no way to say so.
   */
  enabled?: boolean;
}

export function FadeIn({
  children,
  className,
  duration = 0.24,
  enabled = true,
}: FadeInProps) {
  const reduced = useReducedMotion();
  return (
    <m.div
      variants={variants}
      // `false`, not "visible": motion treats a false initial as "already
      // there" and skips the mount animation outright, rather than tweening
      // from the target to the target.
      initial={enabled ? "hidden" : false}
      animate="visible"
      transition={{ duration: reduced ? 0 : duration, ease: [0.4, 0, 0.2, 1] }}
      className={className}
    >
      {children}
    </m.div>
  );
}
