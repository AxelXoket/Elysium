import { Children, useMemo, type ReactNode } from "react";
import { motion as m, type Variants } from "motion/react";
import { staggerStep } from "@/lib/motion/stagger";
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
  const count = Children.count(children);

  const container: Variants = useMemo(
    () => ({
      hidden: {},
      visible: {
        transition: {
          // Budgeted by TOTAL time, not per item (V10).
          //
          // A fixed per-item delay makes a long list feel slower purely for
          // being long: sixteen rows at 40ms each is 0.64s before the last one
          // moves, against the ~0.35s a sequence can occupy before it reads as
          // waiting rather than as considered. The per-item value stays the
          // ceiling, so short lists are untouched and only long ones tighten.
          staggerChildren: reduced ? 0 : Math.min(stagger, staggerStep(count)),
        },
      },
    }),
    [reduced, stagger, count],
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
  animated = true,
}: {
  children: ReactNode;
  className?: string;
  /** false renders a plain div - no entrance, no stagger slot. Long lists
   * cap the animated window this way (v1.1 FF5): 150 messages x 0.04s
   * stagger held the bottom bubble hostage for ~6 seconds. */
  animated?: boolean;
}) {
  const reduced = useReducedMotion();
  // ALWAYS a motion element, animated or not.
  //
  // Returning a plain <div> for animated=false changed the ELEMENT TYPE under
  // an unchanged key, so React unmounted and remounted the whole subtree. The
  // `animated` flag is per-index (`index >= length - ANIMATED_TAIL_GROUPS`), so
  // it flips on an existing item every time the list grows: in a chat with more
  // than 16 variant groups, completing one exchange remounted two older
  // bubbles, and MessageBubble lost viewIndex, hasNavigated, confirmDelete,
  // editing, editDraft and lightboxAttachment - a bubble the reader had paged
  // to another variant snapped back to the active one, an open delete-confirm
  // closed, and its FadeIn replayed as a blink.
  //
  // animated=false still means NO entrance and no stagger slot (a variant-less
  // motion child is not counted by staggerChildren), which is what the
  // long-list cap is actually for - 150 messages x 0.04s held the bottom bubble
  // hostage for ~6 seconds. It simply no longer changes what React reconciles.
  return (
    <m.div
      variants={
        animated ? (reduced ? reducedItemVariants : itemVariants) : undefined
      }
      transition={animated ? ITEM_TRANSITION : undefined}
      className={className}
    >
      {children}
    </m.div>
  );
}
