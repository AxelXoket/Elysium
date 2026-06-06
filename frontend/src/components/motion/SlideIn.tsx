import { type ReactNode } from "react";
import { motion as m, type Variants } from "motion/react";
import { useReducedMotion } from "./ReducedMotion";

interface SlideInProps {
  children: ReactNode;
  className?: string;
  duration?: number;
  offsetY?: number;
}

export function SlideIn({
  children,
  className,
  duration = 0.22,
  offsetY = 8,
}: SlideInProps) {
  const reduced = useReducedMotion();

  const variants: Variants = {
    hidden: { opacity: 0, y: reduced ? 0 : offsetY },
    visible: { opacity: 1, y: 0 },
  };

  return (
    <m.div
      variants={variants}
      initial="hidden"
      animate="visible"
      transition={{ duration: reduced ? 0 : duration, ease: [0.4, 0, 0.2, 1] }}
      className={className}
    >
      {children}
    </m.div>
  );
}
