import { act, fireEvent } from "@testing-library/react";

/**
 * Opens collapsed `<details>` the way a user would. Feed bodies mount on first
 * open, and jsdom fires `toggle` asynchronously, so nested groups appear only
 * after the previous pass settles. `selector` limits which groups are opened.
 */
export async function expandDetails(
  root: ParentNode = document.body,
  selector = "details",
): Promise<void> {
  for (let pass = 0; pass < 6; pass += 1) {
    const closed = Array.from(root.querySelectorAll<HTMLDetailsElement>(`${selector}:not([open])`));
    if (closed.length === 0) return;
    for (const details of closed) {
      const summary = details.querySelector(":scope > summary");
      if (summary) fireEvent.click(summary);
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}
