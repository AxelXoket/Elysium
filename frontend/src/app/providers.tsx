import { type ReactNode } from "react";
import { MotionConfig } from "motion/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/lib/query/queryClient";
import { TooltipProvider } from "@/components/ui/tooltip";

interface ProvidersProps {
  children: ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  return (
    <QueryClientProvider client={queryClient}>
      {/*
        reducedMotion="user" does the RIGHT split automatically: it strips
        transform and layout animation while KEEPING opacity and colour.
        That distinction is the whole of the accessibility guidance - it is
        large, fast, physical movement that triggers vestibular symptoms, not
        the fact that something changed. Blanket `animation: none` is the
        common mistake, and it removes the state-change feedback that people
        with vestibular sensitivity still need.

        What it does NOT reach, and an earlier version of this comment claimed
        otherwise: a staggerChildren delay, an opacity duration, whether a
        canvas effect runs at all, a native scrollIntoView behaviour. The only
        two things `shouldReduceMotion` is consulted for are positional keys
        (width/height/top/left/right/bottom plus the transform props) and
        layout animations. Everything else animates at full length with this
        prop set.

        So the shared useReducedMotion hook in components/motion is not the
        per-component anti-pattern this comment used to call it. It is one
        module every consumer imports, covering the part this prop structurally
        cannot. Both are needed; removing either is a regression.
      */}
      <MotionConfig reducedMotion="user">
        <TooltipProvider>{children}</TooltipProvider>
      </MotionConfig>
    </QueryClientProvider>
  );
}
