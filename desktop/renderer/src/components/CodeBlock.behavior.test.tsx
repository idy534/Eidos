import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CodeBlock } from "./CodeBlock.js";

describe("CodeBlock highlighting behavior", () => {
  it("highlights typescript code asynchronously with colored token spans", async () => {
    const { container } = render(
      <CodeBlock className="language-ts">
        {"const message: string = 'hello';"}
      </CodeBlock>,
    );

    expect(screen.getByText("TypeScript")).toBeInTheDocument();

    await waitFor(() => {
      const coloredTokens = container.querySelectorAll(".shiki-code-body span[style*='color']");
      expect(coloredTokens.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it("highlights python code when language has uppercase letters", async () => {
    const { container } = render(
      <CodeBlock className="language-Python">
        {"def greet(name):\n    return f'Hello, {name}'"}
      </CodeBlock>,
    );

    expect(screen.getByText("Python")).toBeInTheDocument();

    await waitFor(() => {
      const coloredTokens = container.querySelectorAll(".shiki-code-body span[style*='color']");
      expect(coloredTokens.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it("highlights go and rust code with colored token spans", async () => {
    const { container } = render(
      <CodeBlock className="language-go">
        {"package main\n\nfunc main() {}"}
      </CodeBlock>,
    );

    expect(screen.getByText("Go")).toBeInTheDocument();

    await waitFor(() => {
      const coloredTokens = container.querySelectorAll(".shiki-code-body span[style*='color']");
      expect(coloredTokens.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it("normalizes language aliases like golang, c++, c# and rs", async () => {
    const { container } = render(
      <CodeBlock className="language-c++">
        {"#include <iostream>\nint main() { return 0; }"}
      </CodeBlock>,
    );

    expect(screen.getByText("C++")).toBeInTheDocument();

    await waitFor(() => {
      const coloredTokens = container.querySelectorAll(".shiki-code-body span[style*='color']");
      expect(coloredTokens.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it("copies code content when copy button is clicked", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    render(
      <CodeBlock className="language-ts">
        {"const x = 1;"}
      </CodeBlock>,
    );

    const copyBtn = screen.getByRole("button", { name: "复制代码" });
    await waitFor(() => {
      fireEvent.click(copyBtn);
      expect(writeTextMock).toHaveBeenCalledWith("const x = 1;");
    });
  });

  it("does not trigger highlight while isStreaming is true", () => {
    const { container } = render(
      <CodeBlock className="language-ts" isStreaming={true}>
        {"const streaming = true;"}
      </CodeBlock>,
    );

    // During streaming, it stays on fallback pre code
    expect(container.querySelector(".shiki-code-body")).toBeNull();
    expect(container.querySelector("code.language-ts")).toBeInTheDocument();
  });
});
