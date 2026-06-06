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
}

export function FadeIn({ children, className, duration = 0.24 }: FadeInProps) {
  const reduced = useReducedMotion();
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
