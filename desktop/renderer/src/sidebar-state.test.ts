import assert from "node:assert/strict";
import test from "node:test";
import { groupSessionsByProject, projectSidebarGroups } from "./session-state.js";
import type { Project, Session } from "./contracts.js";

void test("mixed placement preserves object identity, excludes duplicates and restores normal order", () => {
  const project: Project = { id: "project-a", name: "A", workspaceRoot: "/a", gitAvailable: false, createdAt: 1, updatedAt: 1 };
  const older: Session = { id: "older", project, workspaceRoot: "/a", title: "older", taskStatus: "completed", createdAt: 2, updatedAt: 2 };
  const newer: Session = { ...older, id: "newer", title: "newer", createdAt: 3 };
  const groups = groupSessionsByProject([older, newer], [project]);
  const result = projectSidebarGroups(groups, [{ kind: "session", id: older.id }, { kind: "project", id: project.id }]);
  assert.equal(result.projects.length, 0);
  assert.equal(result.pinned[0]?.kind, "session");
  assert.equal(result.pinned[1]?.kind, "project");
  if (result.pinned[0]?.kind === "session") assert.equal(result.pinned[0].session, older);
  if (result.pinned[1]?.kind === "project") assert.deepEqual(result.pinned[1].project.sessions, [newer]);
  assert.deepEqual(projectSidebarGroups(groups, []).projects[0]?.sessions, [newer, older]);
  assert.deepEqual(groups[0]?.sessions, [newer, older]);
});

void test("stale pin references are hidden and pinning a recent Session does not leave an empty recent group", () => {
  const session: Session = { id: "recent", projectless: true, workspaceRoot: "/recent", title: "recent", taskStatus: "completed", createdAt: 2, updatedAt: 2 };
  const groups = groupSessionsByProject([session]);
  const result = projectSidebarGroups(groups, [
    { kind: "session", id: "missing" }, { kind: "project", id: "missing" },
    { kind: "session", id: session.id }, { kind: "session", id: session.id },
  ]);
  assert.equal(result.pinned.length, 1);
  assert.deepEqual(result.projects, []);
});
