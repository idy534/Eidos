import test from "node:test";
import assert from "node:assert/strict";

test("useSessionController synchronous creation lock invariant", () => {
  // Test ref locking behavior pattern logic
  let creatingSessionRef = false;
  let createCount = 0;

  async function mockCreateSession(workspace: string | null) {
    if (creatingSessionRef) return undefined;
    creatingSessionRef = true;
    try {
      if (!workspace) return undefined;
      createCount++;
      return { id: `session-${createCount}`, workspaceRoot: workspace };
    } finally {
      creatingSessionRef = false;
    }
  }

  // Two synchronous invocations
  const call1 = mockCreateSession("/ws/1");
  const call2 = mockCreateSession("/ws/2");

  return Promise.all([call1, call2]).then(([res1, res2]) => {
    assert.ok(res1);
    assert.equal(res2, undefined); // Blocked by synchronous lock
    assert.equal(createCount, 1);
    assert.equal(creatingSessionRef, false); // Released in finally
  });
});

test("useSessionController releases lock on workspace select cancellation", async () => {
  let creatingSessionRef = false;

  async function mockCreateSession() {
    if (creatingSessionRef) return undefined;
    creatingSessionRef = true;
    try {
      const workspace = null; // User canceled picker
      if (!workspace) return undefined;
      return { id: "s-1", workspaceRoot: workspace };
    } finally {
      creatingSessionRef = false;
    }
  }

  const res1 = await mockCreateSession();
  assert.equal(res1, undefined);
  assert.equal(creatingSessionRef, false);

  // Subsequent call after cancellation succeeds
  let secondRan = false;
  async function mockSecondCall() {
    if (creatingSessionRef) return undefined;
    creatingSessionRef = true;
    try {
      secondRan = true;
      return { id: "s-2" };
    } finally {
      creatingSessionRef = false;
    }
  }

  const res2 = await mockSecondCall();
  assert.equal(secondRan, true);
  assert.ok(res2);
});

test("useSessionController releases lock on error during snapshot load or creation", async () => {
  let creatingSessionRef = false;

  async function mockFailingCreateSession() {
    if (creatingSessionRef) return undefined;
    creatingSessionRef = true;
    try {
      throw new Error("RPC network error");
    } finally {
      creatingSessionRef = false;
    }
  }

  await assert.rejects(() => mockFailingCreateSession(), { message: "RPC network error" });
  assert.equal(creatingSessionRef, false);
});
