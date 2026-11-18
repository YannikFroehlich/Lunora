import { describe, expect, it } from "vitest";
import { Schema } from "@tiptap/pm/model";
import { EditorState } from "@tiptap/pm/state";
import { Step } from "@tiptap/pm/transform";

import { collabPlugin, getVersion, insertedRanges, receiveTransaction, sendableSteps } from "./collab.js";

const schema = new Schema({
  nodes: {
    doc: { content: "paragraph+" },
    paragraph: { content: "text*", toDOM: () => ["p", 0] },
    text: {},
  },
  marks: { strong: { toDOM: () => ["strong", 0] } },
});

function editorState(text) {
  const doc = schema.node("doc", null, [schema.node("paragraph", null, text ? [schema.text(text)] : [])]);
  return EditorState.create({ doc, plugins: [collabPlugin(0)] });
}

function type(state, pos, text) {
  return state.apply(state.tr.insertText(text, pos));
}

// The server's part: numbered steps, accepted only on top of the newest version.
function authority() {
  const log = [];
  return {
    push(version, steps, clientId) {
      if (version !== log.length) return { accepted: false, missing: log.slice(version) };
      steps.forEach((step) => log.push({ step: step.toJSON(), clientId }));
      return { accepted: true };
    },
    since(version) {
      return log.slice(version);
    },
  };
}

function receive(state, entries, ownId) {
  const steps = entries.map((entry) => Step.fromJSON(schema, entry.step));
  return state.apply(
    receiveTransaction(
      state,
      steps,
      entries.map((entry) => entry.clientId),
      ownId,
    ),
  );
}

function sync(state, server, clientId) {
  const sendable = sendableSteps(state);
  if (sendable) {
    const answer = server.push(sendable.version, sendable.steps, clientId);
    if (answer.accepted) {
      return state.apply(
        receiveTransaction(
          state,
          sendable.steps,
          sendable.steps.map(() => clientId),
          clientId,
        ),
      );
    }
    return sync(receive(state, answer.missing, clientId), server, clientId);
  }
  return receive(state, server.since(getVersion(state)), clientId);
}

describe("live editing", () => {
  it("converges when two editors type at the same time", () => {
    const server = authority();
    let alice = type(editorState("Hallo Welt"), 1, "A");
    let bob = type(editorState("Hallo Welt"), 11, "B");

    alice = sync(alice, server, "alice");
    bob = sync(bob, server, "bob");
    alice = sync(alice, server, "alice");

    expect(alice.doc.textContent).toBe("AHallo WeltB");
    expect(bob.doc.eq(alice.doc)).toBe(true);
    expect(getVersion(alice)).toBe(2);
    expect(getVersion(bob)).toBe(2);
    expect(sendableSteps(alice)).toBeNull();
    expect(sendableSteps(bob)).toBeNull();
  });

  it("keeps typing that happened while own steps were in flight", () => {
    const server = authority();
    let alice = type(editorState(""), 1, "eins");
    const sendable = sendableSteps(alice);
    server.push(sendable.version, sendable.steps, "alice");
    alice = type(alice, 5, " zwei");
    alice = receive(alice, server.since(0), "alice");

    expect(getVersion(alice)).toBe(1);
    expect(alice.doc.textContent).toBe("eins zwei");
    expect(sendableSteps(alice).steps).toHaveLength(1);
  });

  it("drops own steps whose text another editor deleted", () => {
    const server = authority();
    let alice = editorState("abc");
    let bob = editorState("abc");
    alice = alice.apply(alice.tr.delete(1, 4));
    bob = type(bob, 2, "X");

    alice = sync(alice, server, "alice");
    bob = sync(bob, server, "bob");
    alice = sync(alice, server, "alice");

    expect(bob.doc.eq(alice.doc)).toBe(true);
  });
});

describe("authorship ranges", () => {
  it("covers inserted text only, mapped through later changes", () => {
    const state = editorState("Hallo");
    const typed = state.tr.insertText("neu ", 1);
    const shifted = state.apply(typed).tr.insertText("X", 1);
    expect(insertedRanges([typed], () => true)).toEqual([[1, 5]]);
    expect(insertedRanges([typed, shifted], (tr) => tr === typed)).toEqual([[2, 6]]);
  });

  it("ignores formatting and deletions", () => {
    const state = editorState("Hallo");
    const bold = state.tr.addMark(1, 3, schema.marks.strong.create());
    const removed = state.tr.delete(1, 3);
    expect(insertedRanges([bold, removed], () => true)).toEqual([]);
  });
});
