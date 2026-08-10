/**
 * renderWithQueryClient.tsx - one QueryClient wrapper for component tests.
 *
 * Around 50 test files hand-rolled the same `wrapper` function: a fresh
 * QueryClient with retries off, wrapped in a QueryClientProvider, handed to
 * render(ui, { wrapper }). This is that function, once.
 *
 * Retries are off in tests DELIBERATELY, and that is the one piece of config
 * worth keeping over the production defaults in lib/query/queryClient.ts: a
 * retrying query turns a single failed request into three, so an intended
 * error surfaces late (or as a fake-timer hang) instead of as the error the
 * test is asserting on. Mutations get the same treatment for the same reason;
 * a query-only test is unaffected by a mutation setting it never triggers.
 *
 * The client is created FRESH per call so no cache leaks between tests, and it
 * is returned alongside the render result for the tests that read the cache
 * directly (getQueryData / setQueryData / invalidateQueries).
 *
 * A test that needs extra context (TooltipProvider, a settings provider) passes
 * it as `wrapper`; the helper nests it INSIDE the QueryClientProvider, so those
 * providers can use the same client. `renderHookWithQueryClient` is the same
 * idea for hook tests that reach for react-query.
 */
import {
  render,
  renderHook,
  type RenderOptions,
  type RenderHookOptions,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentType, ReactElement, ReactNode } from "react";

/** A QueryClient tuned for tests: no retries on queries or mutations. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

type ExtraWrapper = ComponentType<{ children: ReactNode }>;

function makeWrapper(queryClient: QueryClient, Extra?: ExtraWrapper) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const inner = Extra ? <Extra>{children}</Extra> : children;
    return (
      <QueryClientProvider client={queryClient}>{inner}</QueryClientProvider>
    );
  };
}

type Options = {
  client?: QueryClient;
  wrapper?: ExtraWrapper;
} & Omit<RenderOptions, "wrapper">;

/**
 * render(ui) under a QueryClientProvider. Returns the usual render result plus
 * `queryClient`. Pass `client` to reuse a specific QueryClient, or `wrapper` to
 * nest extra providers inside the QueryClientProvider.
 */
export function renderWithQueryClient(ui: ReactElement, options?: Options) {
  const { client, wrapper: Extra, ...renderOptions } = options ?? {};
  const queryClient = client ?? createTestQueryClient();
  return {
    queryClient,
    ...render(ui, { wrapper: makeWrapper(queryClient, Extra), ...renderOptions }),
  };
}

type HookOptions<Props> = {
  client?: QueryClient;
  wrapper?: ExtraWrapper;
} & Omit<RenderHookOptions<Props>, "wrapper">;

/** renderHook under a QueryClientProvider; returns the result plus `queryClient`. */
export function renderHookWithQueryClient<Result, Props>(
  callback: (props: Props) => Result,
  options?: HookOptions<Props>,
) {
  const { client, wrapper: Extra, ...hookOptions } = options ?? {};
  const queryClient = client ?? createTestQueryClient();
  return {
    queryClient,
    ...renderHook(callback, {
      wrapper: makeWrapper(queryClient, Extra),
      ...hookOptions,
    }),
  };
}
