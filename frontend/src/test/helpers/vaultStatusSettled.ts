/**
 * vaultStatusSettled.ts - an absence that means something.
 *
 * Every notice test used to check "nothing rendered" like this:
 *
 *     await waitFor(() => expect(screen.queryByTestId(x)).toBeNull());
 *
 * which passes on the very first render, before /vault/status has answered,
 * because a query that has not answered renders nothing too. So it proved
 * nothing about the settled state, and it would have passed on a component
 * that never renders anything at all. That is the vacuous shape a ground
 * control must not have.
 *
 * This helper waits until the real query cache says the vault status query
 * has settled to success AND the container is empty at the same moment.
 * Both in one waitFor on purpose: TanStack flips the cache first and
 * notifies observers a tick later, so "settled, then check the DOM" could
 * look at a render that has not caught up. A container that is empty while
 * the query is in success is a component that answered "nothing to report"
 * with the answer in hand; the pending and error states both paint a line,
 * so neither can satisfy it.
 */
import { waitFor } from "@testing-library/react";
import { expect } from "vitest";
import type { QueryClient } from "@tanstack/react-query";

import { keys } from "@/lib/query/keys";

export async function expectNothingOnceAnswered(rendered: {
  container: HTMLElement;
  queryClient: QueryClient;
}): Promise<void> {
  await waitFor(() => {
    expect(
      rendered.queryClient.getQueryState(keys.vault())?.status,
      "the vault status query has not settled to success",
    ).toBe("success");
    expect(rendered.container).toBeEmptyDOMElement();
  });
}
