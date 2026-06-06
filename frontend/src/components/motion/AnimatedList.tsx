import { type ReactNode } from "react";
import { motion as m, type Variants } from "motion/react";
import { useReducedMotion } from "./ReducedMotion";

interface AnimatedListProps {
  children: ReactNode;
  className?: string;
  stagger?: number;
}

const containerVariants: Variants = {
  hidden: {},
  visible: {
    transition: {
      staggerChildren: 0.04,
    },
  },
};

const itemVariants: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0 },
};

const reducedItemVariants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1 },
};

export function AnimatedList({
  children,
  className,
  stagger = 0.04,
}: AnimatedListProps) {
  const reduced = useReducedMotion();

  const container: Variants = {
    ...containerVariants,
    visible: {
      transition: {
        staggerChildren: reduced ? 0 : stagger,
      },
    },
  };

  return (
    <m.div
      variants={container}
      initial="hidden"
      animate="visible"
      className={className}
    >
      {children}
    </m.div>
  );
}

export function AnimatedListItem({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduced = useReducedMotion();
  return (
    <m.div
      variants={reduced ? reducedItemVariants : itemVariants}
      transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
      className={className}
    >
      {children}
    </m.div>
  );
}
