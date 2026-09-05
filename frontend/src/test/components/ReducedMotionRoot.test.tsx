/**
 * ReducedMotionRoot.test.tsx - the app reads reduced motion at the root.
 *
 * This replaces an assertion in `test/lib/motionTokens.test.ts` that read
 * `app/providers.tsx` as text and looked for the string `reducedMotion="user"`
 * in it. The string is in that file twice: once as the prop and once inside
 * the JSX comment above it that explains what the prop does and does not
 * reach. Measured: deleting the real `<MotionConfig reducedMotion="user">`
 * and leaving the comment kept the old assertion green, and only stripping
 * the literal out of the COMMENT turned it red. It was reading the comment.
 *
 * So the prop is read off the component that receives it. `MotionConfig` is
 * swapped for a recorder that renders its children, which also means the
 * assertion only holds while the config is really in the mounted tree.
 *
 * The comment in providers.tsx stays exactly as it is. It is the accurate
 * half of the story - this prop covers positional and layout animation and
 * nothing else, which is why the shared useReducedMotion hook exists as well.
 * Nothing here depends on it either way, and one of the cases below proves
 * that by asserting on the prop while the comment is irrelevant.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";

/** Hoisted: `vi.mock`'s factory is lifted above the imports. */
const recorder = vi.hoisted(() => ({
  configs: [] as Record<string, unknown>[],
}));

vi.mock("motion/react", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("motion/react")>();
  return {
    ...actual,
    MotionConfig: (props: { children?: ReactNode } & Record<string, unknown>) => {
      recorder.configs.push(props);
      return React.createElement(React.Fragment, null, props.children);
    },
  };
});

describe("the app reads reduced motion at the root, not per component", () => {
  beforeEach(() => {
    recorder.configs.length = 0;
  });

  it("POSITIVE CONTROL: a MotionConfig is mounted and its children render", () => {
    // Without this the case below would also pass on a tree that mounted no
    // MotionConfig at all, since `undefined` never equals "always" either.
    render(
      <Providers>
        <span>the app</span>
      </Providers>,
    );

    expect(recorder.configs.length, "no MotionConfig was mounted").toBe(1);
    expect(
      screen.getByText("the app"),
      "the config was constructed but nothing rendered inside it",
    ).toBeInTheDocument();
  });

  it("hands MotionConfig the user's own preference", () => {
    // "user" is the split the guidance asks for: transform and layout
    // animation are stripped, opacity and colour are kept, and the decision
    // is the reader's rather than this app's. "always" and "never" both
    // override the person using it, in opposite directions.
    render(
      <Providers>
        <span>the app</span>
      </Providers>,
    );

    expect(recorder.configs[0]?.reducedMotion).toBe("user");
  });
});
