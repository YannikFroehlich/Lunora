// Live editing of notes against the server's step log (app/services/note_collab.py).
// The step bookkeeping follows prosemirror-collab (MIT, Marijn Haverbeke): local steps stay
// "unconfirmed" until the server has numbered them, steps from others are applied to the
// confirmed document and the unconfirmed ones rebased on top, so every editor converges.
import { Extension, Mark } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Step } from "@tiptap/pm/transform";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

export const collabKey = new PluginKey("lunoraCollab");
const cursorKey = new PluginKey("lunoraCollabCursors");

const FAST_POLL_MS = 1000;
const SLOW_POLL_MS = 5000;
const PUSH_DELAY_MS = 150;
const MAX_ROUNDS = 5;
const CURSOR_TONES = ["blue", "green", "red", "violet", "sand"];

class Rebaseable {
  constructor(step, inverted, origin) {
    this.step = step;
    this.inverted = inverted;
    this.origin = origin;
  }
}

// Undo the unconfirmed steps, apply the confirmed ones, then map the unconfirmed steps over
// them and apply what still fits. Steps that no longer apply (their text is gone) are dropped.
export function rebaseSteps(steps, over, transform) {
  for (let i = steps.length - 1; i >= 0; i--) transform.step(steps[i].inverted);
  for (const step of over) transform.step(step);
  const result = [];
  for (let i = 0, mapFrom = steps.length; i < steps.length; i++) {
    const mapped = steps[i].step.map(transform.mapping.slice(mapFrom));
    mapFrom--;
    if (mapped && !transform.maybeStep(mapped).failed) {
      transform.mapping.setMirror(mapFrom, transform.steps.length - 1);
      result.push(new Rebaseable(mapped, mapped.invert(transform.docs[transform.docs.length - 1]), steps[i].origin));
    }
  }
  return result;
}

function unconfirmedFrom(transform) {
  return transform.steps.map((step, i) => new Rebaseable(step, step.invert(transform.docs[i]), transform));
}

export function collabPlugin(version) {
  return new Plugin({
    key: collabKey,
    state: {
      init: () => ({ version, unconfirmed: [] }),
      apply(tr, state) {
        const meta = tr.getMeta(collabKey);
        if (meta) return meta;
        if (tr.docChanged)
          return { version: state.version, unconfirmed: state.unconfirmed.concat(unconfirmedFrom(tr)) };
        return state;
      },
    },
    // Lets the undo history rebase its entries instead of dropping them on remote steps.
    historyPreserveItems: true,
  });
}

export function getVersion(state) {
  return collabKey.getState(state).version;
}

export function sendableSteps(state) {
  const { version, unconfirmed } = collabKey.getState(state);
  return unconfirmed.length ? { version, steps: unconfirmed.map((item) => item.step) } : null;
}

export function isRemoteTransaction(tr) {
  return Boolean(tr.getMeta(collabKey));
}

// Steps the server put into its log, as a transaction: the leading ones of this client
// confirm its unconfirmed steps, the rest are applied with the unconfirmed ones rebased.
export function receiveTransaction(state, steps, clientIds, ownClientId) {
  const collabState = collabKey.getState(state);
  const version = collabState.version + steps.length;
  let ours = 0;
  while (ours < clientIds.length && clientIds[ours] === ownClientId) ours++;
  let unconfirmed = collabState.unconfirmed.slice(ours);
  const remote = steps.slice(ours);
  const tr = state.tr;
  if (!remote.length) return tr.setMeta(collabKey, { version, unconfirmed });
  const rebased = unconfirmed.length;
  if (rebased) unconfirmed = rebaseSteps(unconfirmed, remote, tr);
  else remote.forEach((step) => tr.step(step));
  return tr.setMeta("rebased", rebased).setMeta("addToHistory", false).setMeta(collabKey, { version, unconfirmed });
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export function cursorTone(userId) {
  return CURSOR_TONES[Math.abs(userId) % CURSOR_TONES.length];
}

function caretElement(cursor) {
  const caret = document.createElement("span");
  caret.className = `note-remote-caret tone-${cursorTone(cursor.user_id)}`;
  caret.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "note-remote-caret-label";
  label.textContent = cursor.name;
  caret.append(label);
  return caret;
}

// ponytail: positions are taken as the other editor sent them, clamped to this document;
// they can be a few characters off while steps are in flight and settle with the next poll.
function cursorDecorations(doc, cursors) {
  const max = doc.content.size;
  const decorations = [];
  cursors.forEach((cursor) => {
    const head = clamp(cursor.head, 0, max);
    const anchor = clamp(cursor.anchor, 0, max);
    const tone = `tone-${cursorTone(cursor.user_id)}`;
    if (anchor !== head) {
      decorations.push(
        Decoration.inline(Math.min(anchor, head), Math.max(anchor, head), { class: `note-remote-selection ${tone}` }),
      );
    }
    decorations.push(
      Decoration.widget(head, () => caretElement(cursor), { key: `${cursor.client_id}-${cursor.name}` }),
    );
  });
  return DecorationSet.create(doc, decorations);
}

function cursorPlugin() {
  return new Plugin({
    key: cursorKey,
    state: {
      init: () => DecorationSet.empty,
      apply(tr, set) {
        const cursors = tr.getMeta(cursorKey);
        if (cursors) return cursorDecorations(tr.doc, cursors);
        return set.map(tr.mapping, tr.doc);
      },
    },
    props: {
      decorations: (state) => cursorKey.getState(state),
    },
  });
}

// Text a local change inserted, as [from, to] ranges in the document after all `transactions`.
export function insertedRanges(transactions, isTracked) {
  let ranges = [];
  transactions.forEach((tr) => {
    const tracked = isTracked(tr);
    tr.mapping.maps.forEach((map) => {
      ranges = ranges.map(([from, to]) => [map.map(from, 1), map.map(to, -1)]).filter(([from, to]) => to > from);
      if (tracked) {
        map.forEach((_oldStart, _oldEnd, newStart, newEnd) => {
          if (newEnd > newStart) ranges.push([newStart, newEnd]);
        });
      }
    });
  });
  return ranges;
}

export const KEEP_AUTHORS = "lunoraKeepAuthors";

export function keepAuthors(tr) {
  return tr.setMeta(KEEP_AUTHORS, true);
}

// "Wer hat was geschrieben": every text this editor types (or pastes) on a shared note gets an
// `author` mark with the own user id, which travels with the steps like any other formatting.
// Steps of others, undo/redo and reloads keep the marks they bring.
export const Authorship = Mark.create({
  name: "author",
  inclusive: false,
  addOptions() {
    return { userId: null };
  },
  addAttributes() {
    return {
      userId: {
        default: null,
        parseHTML: (element) => Number.parseInt(element.dataset.author, 10) || null,
        renderHTML: (attributes) =>
          attributes.userId
            ? { "data-author": attributes.userId, class: `note-author tone-${cursorTone(attributes.userId)}` }
            : {},
      },
    };
  },
  parseHTML() {
    return [{ tag: "span[data-author]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["span", HTMLAttributes, 0];
  },
  addProseMirrorPlugins() {
    const userId = this.options.userId;
    if (!userId) return [];
    return [
      new Plugin({
        appendTransaction(transactions, _oldState, newState) {
          const ranges = insertedRanges(
            transactions,
            (tr) => tr.docChanged && !isRemoteTransaction(tr) && !tr.getMeta("history$") && !tr.getMeta(KEEP_AUTHORS),
          );
          if (!ranges.length) return null;
          const type = newState.schema.marks.author;
          const mark = type.create({ userId });
          const tr = newState.tr;
          ranges.forEach(([from, to]) => {
            newState.doc.nodesBetween(from, to, (node, pos) => {
              if (!node.isText || mark.isInSet(node.marks)) return;
              const start = Math.max(from, pos);
              const end = Math.min(to, pos + node.nodeSize);
              tr.removeMark(start, end, type).addMark(start, end, mark);
            });
          });
          return tr.docChanged ? keepAuthors(tr) : null;
        },
      }),
    ];
  },
});

export const Collaboration = Extension.create({
  name: "lunoraCollaboration",
  addOptions() {
    return { version: 0 };
  },
  addProseMirrorPlugins() {
    return [collabPlugin(this.options.version), cursorPlugin()];
  },
});

function randomTab() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// Polls the step log: once a second while someone else is in the note, every five seconds
// otherwise, and right after local changes. Returns the controls notes.js needs.
export function startCollabSync({ editor, url, canEdit, request, reload, onSynced, onBroken }) {
  const tab = randomTab();
  let clientId = null;
  let inflight = null;
  let timer = null;
  let broken = false;
  let othersPresent = false;

  function schedule(delay) {
    window.clearTimeout(timer);
    timer = window.setTimeout(tick, delay);
  }

  function dispatch(tr) {
    editor.view.dispatch(tr);
  }

  function selection() {
    if (!canEdit || !editor.isFocused) return null;
    const { anchor, head } = editor.state.selection;
    return { anchor, head };
  }

  async function resync() {
    const fresh = await reload();
    if (!fresh) return false;
    const pending = canEdit ? sendableSteps(editor.state)?.steps || [] : [];
    editor
      .chain()
      .setMeta(collabKey, { version: fresh.collab_version, unconfirmed: [] })
      .setMeta("addToHistory", false)
      .setContent(fresh.document)
      .run();
    // ponytail: own changes the server never saw are replayed onto the reloaded document
    // as well as they still fit; a reset only happens after a version restore or an old tab.
    const tr = editor.state.tr;
    pending.forEach((step) => tr.maybeStep(step));
    if (tr.docChanged) dispatch(tr);
    return true;
  }

  async function exchange() {
    for (let round = 0; round < MAX_ROUNDS && !broken; round++) {
      const sendable = canEdit ? sendableSteps(editor.state) : null;
      const body = { version: getVersion(editor.state), tab, selection: selection() };
      if (sendable) body.steps = sendable.steps.map((step) => step.toJSON());
      const { response, data } = await request(url, { method: "POST", body: JSON.stringify(body) });
      if (!response.ok || !data.ok) return false;
      clientId = data.client_id;
      othersPresent = data.cursors.length > 0;
      if (data.resync) {
        if (!(await resync())) return false;
        continue;
      }
      try {
        if (data.accepted) {
          dispatch(
            receiveTransaction(
              editor.state,
              sendable.steps,
              sendable.steps.map(() => clientId),
              clientId,
            ),
          );
        } else if (data.steps.length) {
          const steps = data.steps.map((item) => Step.fromJSON(editor.schema, item.step));
          const clientIds = data.steps.map((item) => item.client_id);
          dispatch(receiveTransaction(editor.state, steps, clientIds, clientId));
        }
      } catch (_error) {
        broken = true;
        onBroken?.();
        return false;
      }
      dispatch(editor.state.tr.setMeta(cursorKey, data.cursors));
      onSynced?.(data);
      if (getVersion(editor.state) !== data.version) continue;
      // Sent steps that weren't taken are rebased now and go out in the next round.
      if (!(sendable && !data.accepted)) return true;
    }
    return !broken;
  }

  async function tick() {
    if (inflight) return inflight;
    inflight = exchange()
      .catch(() => false)
      .finally(() => {
        inflight = null;
        if (broken) return;
        const pending = canEdit && sendableSteps(editor.state);
        schedule(pending ? PUSH_DELAY_MS : othersPresent ? FAST_POLL_MS : SLOW_POLL_MS);
      });
    return inflight;
  }

  // Sends everything typed so far; resolves with the confirmed snapshot to store, or null.
  async function snapshot() {
    for (let attempt = 0; attempt < MAX_ROUNDS; attempt++) {
      if (broken) break;
      if (!sendableSteps(editor.state)) return { version: getVersion(editor.state), document: editor.getJSON() };
      if (inflight) await inflight;
      else await tick();
    }
    return !broken && !sendableSteps(editor.state)
      ? { version: getVersion(editor.state), document: editor.getJSON() }
      : null;
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) tick();
  });
  tick();

  return {
    push: () => schedule(PUSH_DELAY_MS),
    snapshot,
    get broken() {
      return broken;
    },
  };
}
