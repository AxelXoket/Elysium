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

        At the root rather than per component: a hand-rolled useReducedMotion
        branch in each place is a correctness bug waiting for the one component
        somebody forgets.
      */}
      <MotionConfig reducedMotion="user">
        <TooltipProvider>{children}</TooltipProvider>
      </MotionConfig>
    </QueryClientProvider>
  );
}
