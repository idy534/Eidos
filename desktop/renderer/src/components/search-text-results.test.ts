import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSearchTextResults,
  initialVisibleFileCount,
  searchHighlightTerms,
} from "./search-text-results.js";

const ROOT = "/Users/me/Documents/AF/appforge-service";

test("groups matches by file and shows the shared absolute root once", () => {
  const view = buildSearchTextResults([
    { path: `${ROOT}/internal/app/handler/workflow_ref.go`, line: 42, column: 9, preview: '\tg.POST("/GetWorkflowReferenceAlerts", h)' },
    { path: `${ROOT}/internal/app/service/workflow.go`, line: 118, column: 6, preview: "func GetWorkflowReferenceAlerts(ctx) {" },
    { path: `${ROOT}/internal/app/handler/workflow_ref.go`, line: 80, column: 30, preview: "// @Param request body GetWorkflowReferenceAlertsRequest" },
  ], "GetWorkflowReference", false);

  assert.equal(view.root, `${ROOT}/internal/app`);
  assert.equal(view.hitCount, 3);
  assert.deepEqual(view.files.map((file) => [file.name, file.directory, file.hits.map((hit) => hit.line)]), [
    ["workflow_ref.go", "handler", [42, 80]],
    ["workflow.go", "service", [118]],
  ]);
  assert.equal(view.files[0]!.path, `${ROOT}/internal/app/handler/workflow_ref.go`);
});

test("keeps workspace-relative paths as they are", () => {
  const view = buildSearchTextResults([
    { path: "src/app.ts", line: 1, column: 1, preview: "needle" },
    { path: "src/lib/util.ts", line: 2, column: 1, preview: "needle" },
    { path: "README.md", line: 3, column: 1, preview: "needle" },
  ], "needle", false);

  assert.equal(view.root, "");
  assert.deepEqual(view.files.map((file) => [file.name, file.directory]), [
    ["app.ts", "src"],
    ["util.ts", "src/lib"],
    ["README.md", ""],
  ]);
});

test("does not collapse paths that only share a top-level folder or mix relative paths", () => {
  assert.equal(buildSearchTextResults([
    { path: "/Users/a/x.ts", preview: "x" },
    { path: "/Users/b/y.ts", preview: "y" },
  ], "x", false).root, "");
  assert.equal(buildSearchTextResults([
    { path: "/opt/tools/x.ts", preview: "x" },
    { path: "src/y.ts", preview: "y" },
  ], "x", false).root, "");
  assert.equal(buildSearchTextResults([
    { path: "C:/Users/me/repo/a.ts", preview: "x" },
    { path: "C:/Users/me/repo/b/c.ts", preview: "x" },
  ], "x", false).root, "C:/Users/me/repo");
});

test("highlights every case-insensitive literal occurrence after removing indentation", () => {
  const [hit] = buildSearchTextResults([
    { path: "a.go", line: 1, column: 5, preview: "    getWorkflow(GetWorkflow)\n" },
  ], "getworkflow", false).files[0]!.hits;

  assert.equal(hit!.text, "getWorkflow(GetWorkflow)");
  assert.equal(hit!.leadingEllipsis, false);
  assert.deepEqual(hit!.highlights, [{ start: 0, end: 11 }, { start: 12, end: 23 }]);
});

test("cuts long lines so the reported match stays visible", () => {
  const preview = `${"x".repeat(60)} = sdk.GetWorkflowReferenceAlertsRequest{}`;
  const [hit] = buildSearchTextResults([
    { path: "a.go", line: 7, column: 68, preview },
  ], "GetWorkflowReference", false).files[0]!.hits;

  assert.equal(hit!.leadingEllipsis, true);
  assert.equal(hit!.text.slice(hit!.highlights[0]!.start, hit!.highlights[0]!.end), "GetWorkflowReference");
  assert.equal(hit!.text, "= sdk.GetWorkflowReferenceAlertsRequest{}");
  assert.equal(hit!.highlights[0]!.start, 6);
  assert.equal(hit!.preview, preview);
});

test("starts a cut line at a word boundary", () => {
  const preview = "\t\treturn workflowVersionAction[sdk.GetWorkflowReferenceAlertsRequest, sdk.GetWorkflowReferenceAlertsResponse](ctx)";
  const [hit] = buildSearchTextResults([
    { path: "a.go", line: 92, column: 75, preview },
  ], "GetWorkflowReference", false).files[0]!.hits;

  assert.equal(hit!.leadingEllipsis, true);
  assert.ok(hit!.text.startsWith("sdk.GetWorkflowReferenceAlertsResponse"), hit!.text);
  assert.equal(hit!.text.slice(hit!.highlights[0]!.start, hit!.highlights[0]!.end), "GetWorkflowReference");
});

test("uses the reported column for windowing when the pattern cannot be highlighted", () => {
  const preview = `${"y".repeat(50)}fooBar(1)`;
  const [hit] = buildSearchTextResults([
    { path: "a.go", line: 7, column: 51, preview },
  ], "foo\\w+\\(", true).files[0]!.hits;

  assert.deepEqual(hit!.highlights, []);
  assert.equal(hit!.leadingEllipsis, true);
  assert.equal(hit!.text, "fooBar(1)");
});

test("counts the reported column in code points, not UTF-16 units", () => {
  const preview = `${"😀".repeat(45)}needle`;
  const [hit] = buildSearchTextResults([
    { path: "a.txt", line: 1, column: 46, preview },
  ], "needle", false).files[0]!.hits;

  assert.equal(hit!.leadingEllipsis, true);
  assert.equal(hit!.text.slice(hit!.highlights[0]!.start), "needle");
  assert.equal(hit!.text.codePointAt(0), "😀".codePointAt(0));
});

test("only highlights regular expressions that are literal alternatives", () => {
  assert.deepEqual(searchHighlightTerms("needle", false), ["needle"]);
  assert.deepEqual(searchHighlightTerms("a.b", false), ["a.b"]);
  assert.deepEqual(searchHighlightTerms("\\bfoo\\b|bar\\.baz", true), ["foo", "bar.baz"]);
  assert.deepEqual(searchHighlightTerms("^import$", true), ["import"]);
  assert.deepEqual(searchHighlightTerms("(a+)+$", true), []);
  assert.deepEqual(searchHighlightTerms("foo.*bar", true), []);
  assert.deepEqual(searchHighlightTerms("\\d+", true), []);
  assert.deepEqual(searchHighlightTerms("a||b", true), []);
  assert.deepEqual(searchHighlightTerms("", false), []);
});

test("merges overlapping highlights from several alternatives", () => {
  const [hit] = buildSearchTextResults([
    { path: "a.ts", line: 1, column: 1, preview: "workflowReference" },
  ], "workflow|flowRef", true).files[0]!.hits;

  assert.deepEqual(hit!.highlights, [{ start: 0, end: 11 }]);
});

test("shows files until the capped hits fill the budget", () => {
  const files = buildSearchTextResults([
    ...Array.from({ length: 9 }, (_, index) => ({ path: "a.ts", line: index + 1, preview: "x" })),
    ...Array.from({ length: 2 }, (_, index) => ({ path: "b.ts", line: index + 1, preview: "x" })),
    ...Array.from({ length: 4 }, (_, index) => ({ path: "c.ts", line: index + 1, preview: "x" })),
  ], "x", false).files;

  assert.equal(initialVisibleFileCount(files, 5, 7), 2);
  assert.equal(initialVisibleFileCount(files, 5, 30), 3);
  assert.equal(initialVisibleFileCount(files, 5, 1), 1);
  assert.equal(initialVisibleFileCount([], 5, 30), 0);
});
