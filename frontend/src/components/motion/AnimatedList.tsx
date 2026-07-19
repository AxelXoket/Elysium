import { useMemo, type ReactNode } from "react";
import { motion as m, type Variants } from "motion/react";
import { useReducedMotion } from "./ReducedMotion";

interface AnimatedListProps {
  children: ReactNode;
  className?: string;
  stagger?: number;
}

const itemVariants: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0 },
};

const reducedItemVariants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1 },
};

/* Hoisted/memoized objects: these components sit on streaming-hot paths
   (MessageList re-renders per flush) - fresh variants/transition literals
   per render would make motion re-diff every item every frame. */
const ITEM_TRANSITION = { duration: 0.18, ease: [0.4, 0, 0.2, 1] } as const;

export function AnimatedList({
  children,
  className,
  stagger = 0.04,
}: AnimatedListProps) {
  const reduced = useReducedMotion();

  const container: Variants = useMemo(
    () => ({
      hidden: {},
      visible: {
        transition: {
          staggerChildren: reduced ? 0 : stagger,
        },
      },
    }),
    [reduced, stagger],
  );

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
      transition={ITEM_TRANSITION}
      className={className}
    >
      {children}
    </m.div>
  );
}
