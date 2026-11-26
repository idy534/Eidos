import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import anthropicLogo from "../assets/providers/anthropic.svg";
import geminiLogo from "../assets/providers/gemini.svg";
import openaiLogo from "../assets/providers/openai.svg";
import qoderLogo from "../assets/providers/qoder.svg";
import { ProviderLogo } from "./ProviderLogo.js";

describe("ProviderLogo", () => {
  it("renders openai and qoder as monochrome masked icons", () => {
    const { container: openaiContainer } = render(<ProviderLogo provider="openai" className="custom-class" />);
    const openaiIcon = openaiContainer.querySelector("span.provider-logo.provider-logo--monochrome.custom-class");
    expect(openaiIcon).not.toBeNull();
    expect(openaiIcon).toHaveStyle({ maskImage: `url("${openaiLogo}")` });
    expect(openaiContainer.querySelector("svg")).toBeNull();

    const { container: qoderContainer } = render(<ProviderLogo provider="qoder" />);
    const qoderIcon = qoderContainer.querySelector("span.provider-logo.provider-logo--monochrome");
    expect(qoderIcon).not.toBeNull();
    expect(qoderIcon).toHaveStyle({ maskImage: `url("${qoderLogo}")` });
  });

  it("renders anthropic and gemini as color img logos", () => {
    const { container: anthropicContainer } = render(<ProviderLogo provider="anthropic" className="model-vendor-icon" />);
    const anthropicImg = anthropicContainer.querySelector("img.provider-logo.model-vendor-icon");
    expect(anthropicImg).toHaveAttribute("src", anthropicLogo);
    expect(anthropicContainer.querySelector("svg")).toBeNull();

    const { container: geminiContainer } = render(<ProviderLogo provider="gemini" className="model-vendor-icon" />);
    const geminiImg = geminiContainer.querySelector("img.provider-logo.model-vendor-icon");
    expect(geminiImg).toHaveAttribute("src", geminiLogo);
    expect(geminiContainer.querySelector("svg")).toBeNull();
  });

  it("keeps the inline svg for custom providers", () => {
    const { container } = render(<ProviderLogo provider="custom" />);
    expect(container.querySelector("svg.provider-logo")).not.toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
});
