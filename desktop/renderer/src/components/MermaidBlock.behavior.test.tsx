import { describe, expect, it } from "vitest";
import mermaid from "mermaid";

if (typeof window !== "undefined" && window.SVGElement) {
  window.SVGElement.prototype.getBBox = function () {
    return { x: 0, y: 0, width: 100, height: 20 };
  };
  window.SVGElement.prototype.getBoundingClientRect = function () {
    return { x: 0, y: 0, width: 100, height: 20, top: 0, left: 0, right: 100, bottom: 20 };
  };
}

describe("Mermaid SVG output inspection", () => {
  it("renders gitGraph and flowchart with fixed dimensions (useMaxWidth: false)", async () => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: "neutral",
      fontFamily: "var(--font-ui)",
      flowchart: { useMaxWidth: false, rankSpacing: 28, nodeSpacing: 24, padding: 8, curve: "basis" },
      sequence: { useMaxWidth: false },
      gitGraph: { useMaxWidth: false },
      state: { useMaxWidth: false },
      class: { useMaxWidth: false },
      er: { useMaxWidth: false },
      themeVariables: { fontSize: "13px", edgeLabelBackground: "transparent" },
    });

    console.log("BODY CHILDREN BEFORE:", document.body.children.length);
    const gitGraphCode = `gitGraph
       commit id: "119ddad" type: HIGHLIGHT tag: "Initial commit"
       branch "text/aaa-0818"
       checkout "text/aaa-0818"
       commit id: "a8225ef" tag: "测试代码"
       commit id: "d4114e4" tag: "docs: README 更新"`;

    const { svg: gitGraphSvg } = await mermaid.render("test_git_graph_user", gitGraphCode);
    const gitSvgTag = gitGraphSvg.match(/<svg[^>]*>/)?.[0] ?? "";
    console.log("GITGRAPH FULL SVG:\n", gitGraphSvg);

    // width should be a pixel number, NOT width="100%"
    expect(gitSvgTag).not.toMatch(/width="100%"/);
    expect(gitSvgTag).toMatch(/width="\d+(\.\d+)?"/);

    // Image 2 from user
    const flowCode = `graph TD
        A["main · codex/123<br/>origin/main · origin/HEAD"]
        B["119ddad<br/>Initial commit<br/>2026-08-18"]
        C["a8225ef<br/>测试代码<br/>2026-08-24"]
        D["text/aaa-0818 ★HEAD<br/>origin/text/aaa-0818"]
        E["工作区（未提交）<br/>M README.md<br/>M tests/sample_test.py<br/>?? 20 个未跟踪样例文件"]
        F["d4114e4<br/>docs: 更新 README 目录结构与<br/>运行说明<br/>2026-09-20"]
        A --> B
        B --> C
        D --> F
        C --> F
        E --> F`;

    const { svg: flowSvg } = await mermaid.render("test_flow_user", flowCode);
    const flowSvgTag = flowSvg.match(/<svg[^>]*>/)?.[0] ?? "";
    console.log("USER FLOW SVG TAG:", flowSvgTag);

    expect(flowSvgTag).not.toMatch(/width="100%"/);
    expect(flowSvgTag).toMatch(/width="\d+(\.\d+)?"/);
    expect(flowSvg).toContain("工作区（未提交）");
    expect(flowSvg).toContain("?? 20 个未跟踪样例文件");
  });
});
