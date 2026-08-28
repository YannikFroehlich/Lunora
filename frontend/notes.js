import { Editor, Mark, Node, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight";
import { Highlight } from "@tiptap/extension-highlight";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { TableKit } from "@tiptap/extension-table";
import { TextAlign } from "@tiptap/extension-text-align";
import { TextStyleKit } from "@tiptap/extension-text-style";
import { Underline } from "@tiptap/extension-underline";
import { Subscript } from "@tiptap/extension-subscript";
import { Superscript } from "@tiptap/extension-superscript";
import { CharacterCount } from "@tiptap/extensions";
import Suggestion from "@tiptap/suggestion";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { common, createLowlight } from "lowlight";
import katex from "katex";

import { normalizeLinkHref } from "./link-utils.js";
import { eventToShortcut, findShortcutConflict, mergeShortcuts, shortcutMatches } from "./shortcut-utils.js";


const app = document.querySelector("[data-notes-app]");

function readJsonScript(id, fallback) {
  const element = document.getElementById(id);
  if (!element) return fallback;
  try {
    return JSON.parse(element.textContent);
  } catch (_error) {
    return fallback;
  }
}

function csrfToken() {
  return document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
}

async function requestJson(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) headers["X-CSRFToken"] = csrfToken();
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  let data = {};
  try {
    data = await response.json();
  } catch (_error) {
    data = { ok: false, error: "Der Server hat keine gültige Antwort gesendet." };
  }
  return { response, data };
}

function attachmentUrl(id, disposition) {
  return `/notes/attachments/${encodeURIComponent(id)}/${disposition}/`;
}

const lowlight = createLowlight(common);

const NoteImage = Node.create({
  name: "noteImage",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      attachmentId: { default: null, parseHTML: (element) => element.dataset.attachmentId },
      alt: { default: "", parseHTML: (element) => element.getAttribute("alt") || "" },
      title: { default: null, parseHTML: (element) => element.getAttribute("title") },
      width: { default: 720, parseHTML: (element) => Number(element.getAttribute("width")) || 720 },
    };
  },
  parseHTML() {
    return [{ tag: "img[data-note-image]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "img",
      mergeAttributes(HTMLAttributes, {
        "data-note-image": "true",
        "data-attachment-id": HTMLAttributes.attachmentId,
        src: attachmentUrl(HTMLAttributes.attachmentId, "inline"),
        width: HTMLAttributes.width,
        class: "note-image",
      }),
    ];
  },
});

const NoteAttachmentNode = Node.create({
  name: "noteAttachment",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      attachmentId: { default: null, parseHTML: (element) => element.dataset.attachmentId },
      name: { default: "Datei", parseHTML: (element) => element.dataset.name || element.textContent || "Datei" },
      size: { default: 0, parseHTML: (element) => Number(element.dataset.size) || 0 },
    };
  },
  parseHTML() {
    return [{ tag: "a[data-note-attachment]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "a",
      {
        "data-note-attachment": "true",
        "data-attachment-id": HTMLAttributes.attachmentId,
        "data-name": HTMLAttributes.name,
        "data-size": HTMLAttributes.size,
        href: attachmentUrl(HTMLAttributes.attachmentId, "download"),
        class: "note-attachment",
        contenteditable: "false",
      },
      `📎 ${HTMLAttributes.name} · ${formatBytes(HTMLAttributes.size)}`,
    ];
  },
});

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderMathInto(target, latex, displayMode) {
  target.innerHTML = "";
  const value = (latex || "").trim();
  if (!value) {
    target.classList.add("is-empty");
    target.classList.remove("is-error");
    target.textContent = displayMode ? "Formel einfügen…" : "∑";
    return;
  }
  target.classList.remove("is-empty");
  try {
    katex.render(value, target, { throwOnError: false, displayMode, trust: false });
    target.classList.remove("is-error");
  } catch (_error) {
    target.classList.add("is-error");
    target.textContent = value;
  }
}

function createMathNodeView(displayMode) {
  return ({ node, getPos, editor, extension }) => {
    const dom = document.createElement(displayMode ? "div" : "span");
    dom.className = `note-math ${displayMode ? "note-math-block" : "note-math-inline"}`;
    dom.tabIndex = 0;
    dom.setAttribute("role", "button");
    dom.setAttribute("aria-label", "Formel bearbeiten");
    renderMathInto(dom, node.attrs.latex, displayMode);
    dom.addEventListener("click", (event) => {
      event.preventDefault();
      if (!editor.isEditable) return;
      extension.options.onEdit?.({
        latex: node.attrs.latex,
        isBlock: displayMode,
        lockType: true,
        onConfirm: (nextLatex) => {
          const pos = typeof getPos === "function" ? getPos() : null;
          if (typeof pos !== "number") return;
          editor.view.dispatch(editor.view.state.tr.setNodeAttribute(pos, "latex", nextLatex));
        },
        onDelete: () => {
          const pos = typeof getPos === "function" ? getPos() : null;
          if (typeof pos !== "number") return;
          editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run();
        },
      });
    });
    return {
      dom,
      update(updatedNode) {
        if (updatedNode.type.name !== node.type.name) return false;
        node = updatedNode;
        renderMathInto(dom, node.attrs.latex, displayMode);
        return true;
      },
      selectNode() { dom.classList.add("is-selected"); },
      deselectNode() { dom.classList.remove("is-selected"); },
      ignoreMutation: () => true,
    };
  };
}

const MathInline = Node.create({
  name: "mathInline",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addOptions() {
    return { onEdit: null };
  },
  addAttributes() {
    return { latex: { default: "", parseHTML: (element) => element.dataset.latex || "" } };
  },
  parseHTML() {
    return [{ tag: "span[data-math-inline]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { "data-math-inline": "true", "data-latex": HTMLAttributes.latex, class: "note-math note-math-inline" })];
  },
  renderText({ node }) {
    return `$${node.attrs.latex}$`;
  },
  addNodeView() {
    return createMathNodeView(false);
  },
});

const MathBlock = Node.create({
  name: "mathBlock",
  group: "block",
  atom: true,
  selectable: true,
  addOptions() {
    return { onEdit: null };
  },
  addAttributes() {
    return { latex: { default: "", parseHTML: (element) => element.dataset.latex || "" } };
  },
  parseHTML() {
    return [{ tag: "div[data-math-block]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-math-block": "true", "data-latex": HTMLAttributes.latex, class: "note-math note-math-block" })];
  },
  renderText({ node }) {
    return `$$${node.attrs.latex}$$`;
  },
  addNodeView() {
    return createMathNodeView(true);
  },
});

const Mention = Node.create({
  name: "mention",
  group: "inline",
  inline: true,
  atom: true,
  selectable: false,
  addOptions() {
    return { suggestion: { char: "@" } };
  },
  addAttributes() {
    return {
      userId: { default: null, parseHTML: (element) => Number(element.dataset.userId) || null },
      label: { default: "", parseHTML: (element) => element.dataset.label || element.textContent.replace(/^@/, "") },
    };
  },
  parseHTML() {
    return [{ tag: "span[data-mention]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-mention": "true",
        "data-user-id": HTMLAttributes.userId,
        "data-label": HTMLAttributes.label,
        class: "note-mention",
      }),
      `@${HTMLAttributes.label}`,
    ];
  },
  renderText({ node }) {
    return `@${node.attrs.label}`;
  },
  addProseMirrorPlugins() {
    return [Suggestion({ editor: this.editor, pluginKey: new PluginKey("mentionSuggestion"), ...this.options.suggestion })];
  },
});

const NoteLink = Node.create({
  name: "noteLink",
  group: "inline",
  inline: true,
  atom: true,
  selectable: false,
  addOptions() {
    return { suggestion: { char: "[[" }, canEdit: true };
  },
  addAttributes() {
    return {
      noteId: { default: null, parseHTML: (element) => Number(element.dataset.noteId) || null },
      label: {
        default: "",
        parseHTML: (element) => element.dataset.label || element.textContent.replace(/^\[\[|\]\]$/g, ""),
      },
    };
  },
  parseHTML() {
    return [{ tag: "a[data-note-link]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "a",
      mergeAttributes(HTMLAttributes, {
        "data-note-link": "true",
        "data-note-id": HTMLAttributes.noteId,
        "data-label": HTMLAttributes.label,
        href: `/notes/${HTMLAttributes.noteId}/`,
        class: "note-link-chip",
      }),
      `📄 ${HTMLAttributes.label}`,
    ];
  },
  renderText({ node }) {
    return `[[${node.attrs.label}]]`;
  },
  addProseMirrorPlugins() {
    const canEdit = this.options.canEdit;
    return [
      Suggestion({ editor: this.editor, pluginKey: new PluginKey("noteLinkSuggestion"), ...this.options.suggestion }),
      new Plugin({
        props: {
          handleClickOn(_view, _pos, node, _nodePos, event) {
            if (node.type.name !== "noteLink" || !canEdit) return false;
            event.preventDefault();
            return true;
          },
        },
      }),
    ];
  },
});

const CommentThread = Mark.create({
  name: "commentThread",
  addAttributes() {
    return {
      threadId: { default: null, parseHTML: (element) => element.dataset.threadId },
    };
  },
  parseHTML() {
    return [{ tag: "span[data-comment-thread]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-comment-thread": "true",
        "data-thread-id": HTMLAttributes.threadId,
        class: "note-comment-mark",
      }),
      0,
    ];
  },
});

function createSuggestionRenderer({ className, getLabel }) {
  let popup = null;
  let items = [];
  let selectedIndex = 0;
  let command = null;

  function renderOptions() {
    if (!popup) return;
    popup.innerHTML = "";
    if (!items.length) {
      popup.hidden = true;
      return;
    }
    popup.hidden = false;
    items.forEach((item, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = `${className}-option${index === selectedIndex ? " is-active" : ""}`;
      option.textContent = getLabel(item);
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
        command(item);
      });
      popup.append(option);
    });
  }

  function positionPopup(clientRect) {
    const rect = clientRect?.();
    if (!popup || !rect) return;
    popup.style.left = `${rect.left}px`;
    popup.style.top = `${rect.bottom + 6}px`;
  }

  return {
    onStart(props) {
      popup = document.createElement("div");
      popup.className = `${className}-popup`;
      document.body.append(popup);
      items = props.items;
      selectedIndex = 0;
      command = props.command;
      positionPopup(props.clientRect);
      renderOptions();
    },
    onUpdate(props) {
      items = props.items;
      selectedIndex = Math.min(selectedIndex, Math.max(0, items.length - 1));
      command = props.command;
      positionPopup(props.clientRect);
      renderOptions();
    },
    onKeyDown(props) {
      if (!items.length) return false;
      if (props.event.key === "ArrowDown") {
        selectedIndex = (selectedIndex + 1) % items.length;
        renderOptions();
        return true;
      }
      if (props.event.key === "ArrowUp") {
        selectedIndex = (selectedIndex - 1 + items.length) % items.length;
        renderOptions();
        return true;
      }
      if (props.event.key === "Enter") {
        command(items[selectedIndex]);
        return true;
      }
      if (props.event.key === "Escape") {
        popup?.remove();
        popup = null;
        return true;
      }
      return false;
    },
    onExit() {
      popup?.remove();
      popup = null;
    },
  };
}

function createMentionSuggestionRenderer() {
  return createSuggestionRenderer({ className: "mention-suggestion", getLabel: (item) => item.name });
}

function createLinkSuggestionRenderer() {
  return createSuggestionRenderer({ className: "note-link-suggestion", getLabel: (item) => item.title });
}

const COMMAND_DEFAULTS = {
  save: { label: "Speichern", shortcut: "Mod+S" },
  undo: { label: "Rückgängig", shortcut: "Mod+Z" },
  redo: { label: "Wiederholen", shortcut: "Mod+Shift+Z" },
  bold: { label: "Fett", shortcut: "Mod+B" },
  italic: { label: "Kursiv", shortcut: "Mod+I" },
  underline: { label: "Unterstrichen", shortcut: "Mod+U" },
  strike: { label: "Durchgestrichen", shortcut: "Mod+Shift+X" },
  superscript: { label: "Hochgestellt", shortcut: "Mod+." },
  subscript: { label: "Tiefgestellt", shortcut: "Mod+," },
  link: { label: "Link", shortcut: "Mod+K" },
  paragraph: { label: "Absatz", shortcut: "Mod+Alt+0" },
  heading1: { label: "Überschrift 1", shortcut: "Mod+Alt+1" },
  heading2: { label: "Überschrift 2", shortcut: "Mod+Alt+2" },
  heading3: { label: "Überschrift 3", shortcut: "Mod+Alt+3" },
  alignLeft: { label: "Linksbündig", shortcut: "Mod+Shift+L" },
  alignCenter: { label: "Zentriert", shortcut: "Mod+Shift+E" },
  alignRight: { label: "Rechtsbündig", shortcut: "Mod+Shift+R" },
  alignJustify: { label: "Blocksatz", shortcut: "Mod+Shift+J" },
  bulletList: { label: "Aufzählung", shortcut: "Mod+Shift+8" },
  orderedList: { label: "Nummerierung", shortcut: "Mod+Shift+7" },
  taskList: { label: "Checkliste", shortcut: "Mod+Shift+9" },
  indent: { label: "Einzug vergrößern", shortcut: "Mod+]" },
  outdent: { label: "Einzug verkleinern", shortcut: "Mod+[" },
  fontFamily: { label: "Schriftart wählen", shortcut: "Alt+Shift+F" },
  fontSize: { label: "Schriftgröße wählen", shortcut: "Alt+Shift+S" },
  textColor: { label: "Textfarbe wählen", shortcut: "Alt+Shift+C" },
  highlight: { label: "Markierfarbe wählen", shortcut: "Alt+Shift+H" },
  lineHeight: { label: "Zeilenabstand wählen", shortcut: "Alt+Shift+L" },
  image: { label: "Bild einfügen", shortcut: "Alt+Shift+I" },
  attachment: { label: "Datei einfügen", shortcut: "Alt+Shift+A" },
  horizontalRule: { label: "Trennlinie", shortcut: "Alt+Shift+-" },
  codeBlock: { label: "Codeblock", shortcut: "Mod+Alt+C" },
  insertMath: { label: "Formel einfügen", shortcut: "Mod+Alt+Q" },
  addComment: { label: "Kommentar hinzufügen", shortcut: "Mod+Alt+M" },
  comments: { label: "Kommentare anzeigen", shortcut: "Mod+Alt+/" },
  insertTable: { label: "Tabelle einfügen", shortcut: "Ctrl+Alt+T" },
  addRowBefore: { label: "Zeile darüber", shortcut: "Ctrl+Alt+ArrowUp" },
  addRowAfter: { label: "Zeile darunter", shortcut: "Ctrl+Alt+ArrowDown" },
  addColumnBefore: { label: "Spalte links", shortcut: "Ctrl+Alt+ArrowLeft" },
  addColumnAfter: { label: "Spalte rechts", shortcut: "Ctrl+Alt+ArrowRight" },
  deleteRow: { label: "Zeile löschen", shortcut: "Ctrl+Alt+Shift+R" },
  deleteColumn: { label: "Spalte löschen", shortcut: "Ctrl+Alt+Shift+C" },
  deleteTable: { label: "Tabelle löschen", shortcut: "Ctrl+Alt+Shift+T" },
  mergeCells: { label: "Zellen verbinden", shortcut: "Ctrl+Alt+M" },
  splitCell: { label: "Zelle trennen", shortcut: "Ctrl+Alt+Shift+M" },
  clearFormat: { label: "Formatierung entfernen", shortcut: "Mod+\\" },
  newNote: { label: "Neue Notiz", shortcut: "Mod+Alt+N" },
  focusSearch: { label: "Suche fokussieren", shortcut: "Mod+Alt+F" },
  pin: { label: "Anpinnen/Loslösen", shortcut: "Mod+Alt+P" },
  archive: { label: "Archivieren/Wiederherstellen", shortcut: "Mod+Alt+A" },
  style: { label: "Farbe & Icon", shortcut: "Mod+Alt+I" },
  duplicate: { label: "Duplizieren", shortcut: "Mod+Alt+D" },
  trash: { label: "In Papierkorb", shortcut: "Mod+Alt+Backspace" },
  share: { label: "Teilen", shortcut: "Mod+Alt+H" },
  outline: { label: "Inhaltsverzeichnis anzeigen/verbergen", shortcut: "Mod+Alt+O" },
  versions: { label: "Versionsverlauf", shortcut: "Mod+Alt+V" },
  exportPdf: { label: "Als PDF exportieren", shortcut: "Mod+Alt+E" },
  exportMarkdown: { label: "Als Markdown exportieren", shortcut: "Mod+Alt+Shift+E" },
  print: { label: "Drucken", shortcut: "Mod+P" },
  saveAsTemplate: { label: "Als Vorlage speichern", shortcut: "Mod+Alt+T" },
  shortcutSettings: { label: "Hotkey-Einstellungen", shortcut: "Mod+Alt+K" },
};

function initNotesApp() {
  let note = readJsonScript("note-initial-data", null);
  const savedOverrides = readJsonScript("note-shortcut-overrides", {});
  let commands = mergeShortcuts(COMMAND_DEFAULTS, savedOverrides);
  let editor = null;
  let dirty = false;
  let saving = false;
  let editGeneration = 0;
  let saveQueue = Promise.resolve(true);
  let applying = false;
  let debounceTimer = null;
  let maxWaitTimer = null;
  let retryTimer = null;
  let conflictServerNote = null;
  let conflictDraft = null;
  let pendingFolderId = null;
  let contextNoteCard = null;
  let contextFolder = null;
  let movingNoteCard = null;
  let stylingNoteCard = null;
  let draggedTreeItem = null;
  let treeDropDescriptor = null;
  let hoveredDropFolder = null;
  let folderExpandTimer = null;
  let pendingTreePointer = null;
  let suppressTreeClick = false;
  const selectedNoteIds = new Set();
  let selectionAnchorId = null;
  const EDITOR_ZOOM_MIN = 0.5;
  const EDITOR_ZOOM_MAX = 2;
  const EDITOR_ZOOM_STEP = 0.1;
  const EDITOR_ZOOM_STORAGE_KEY = "lunora-note-editor-zoom";
  let editorZoom = loadEditorZoom();
  let editorZoomIndicatorTimer = null;

  const titleInput = document.querySelector("input[data-note-title]");
  const notesBackLink = document.querySelector(".notes-mobile-back");
  const saveStatus = document.querySelector("[data-save-status]");
  const wordCount = document.querySelector("[data-word-count]");
  const shareDialog = document.querySelector("[data-share-dialog]");
  const versionsDialog = document.querySelector("[data-versions-dialog]");
  const outlinePanel = document.querySelector("[data-note-outline-panel]");
  const outlineList = document.querySelector("[data-note-outline-list]");
  const shortcutDialog = document.querySelector("[data-shortcut-dialog]");
  const conflictDialog = document.querySelector("[data-conflict-dialog]");
  const tableDialog = document.querySelector("[data-table-dialog]");
  const mathDialog = document.querySelector("[data-math-dialog]");
  const mathInput = document.querySelector("[data-math-input]");
  const mathPreview = document.querySelector("[data-math-preview]");
  const mathBlockToggle = document.querySelector("[data-math-block-toggle]");
  const mathConfirmButton = document.querySelector("[data-math-confirm]");
  const mathDeleteButton = document.querySelector("[data-math-delete]");
  const commentsDialog = document.querySelector("[data-comments-dialog]");
  const templateDialog = document.querySelector("[data-template-dialog]");
  const noteMoveDialog = document.querySelector("[data-note-move-dialog]");
  const noteMoveFolder = document.querySelector("[data-note-move-folder]");
  const noteStyleDialog = document.querySelector("[data-style-dialog]");
  const noteColorGrid = document.querySelector("[data-note-color-grid]");
  const noteIconGrid = document.querySelector("[data-note-icon-grid]");
  const noteContextMenu = document.querySelector("[data-note-context-menu]");
  const folderContextMenu = document.querySelector("[data-folder-context-menu]");
  const notesList = document.querySelector("[data-notes-list]");
  const bulkBar = document.querySelector("[data-notes-bulk-bar]");
  const bulkCount = document.querySelector("[data-notes-bulk-count]");
  const bulkFolderSelect = document.querySelector("[data-notes-bulk-folder]");

  if (note && document.querySelector("[data-note-editor]")) {
    editor = new Editor({
      element: document.querySelector("[data-note-editor]"),
      editable: Boolean(note.can_edit && !note.is_deleted),
      content: note.document,
      extensions: [
        StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: { openOnClick: !note.can_edit }, codeBlock: false }),
        CodeBlockLowlight.configure({ lowlight }),
        Underline,
        Superscript,
        Subscript,
        Highlight.configure({ multicolor: true }),
        TextStyleKit,
        TextAlign.configure({ types: ["heading", "paragraph"] }),
        TableKit.configure({ table: { resizable: true } }),
        TaskList,
        TaskItem.configure({ nested: true }),
        CharacterCount,
        NoteImage,
        NoteAttachmentNode,
        MathInline.configure({ onEdit: openMathDialog }),
        MathBlock.configure({ onEdit: openMathDialog }),
        CommentThread,
        Mention.configure({
          suggestion: {
            items: async ({ query }) => {
              const { data } = await requestJson(`/notes/api/${note.id}/mention-candidates/?q=${encodeURIComponent(query)}`);
              return data.users || [];
            },
            command: ({ editor: mentionEditor, range, props }) => {
              mentionEditor
                .chain()
                .focus()
                .insertContentAt(range, [
                  { type: "mention", attrs: { userId: props.id, label: props.name } },
                  { type: "text", text: " " },
                ])
                .run();
            },
            render: createMentionSuggestionRenderer,
          },
        }),
        NoteLink.configure({
          canEdit: note.can_edit,
          suggestion: {
            char: "[[",
            items: async ({ query }) => {
              const { data } = await requestJson(`/notes/api/${note.id}/link-candidates/?q=${encodeURIComponent(query)}`);
              return data.notes || [];
            },
            command: ({ editor: linkEditor, range, props }) => {
              linkEditor
                .chain()
                .focus()
                .insertContentAt(range, [
                  { type: "noteLink", attrs: { noteId: props.id, label: props.title } },
                  { type: "text", text: " " },
                ])
                .run();
            },
            render: createLinkSuggestionRenderer,
          },
        }),
      ],
      onUpdate: () => {
        updateCounts();
        updateToolbarState();
        markDirty();
        if (outlinePanel && !outlinePanel.hidden) updateOutline();
      },
      onSelectionUpdate: updateToolbarState,
    });
    document.querySelector("[data-note-editor]")?.addEventListener("dblclick", (event) => {
      const image = event.target.closest("img[data-note-image]");
      if (!image || !note.can_edit || note.is_deleted) return;

      const currentWidth = Number(image.getAttribute("width")) || 720;
      const requestedWidth = window.prompt("Bildbreite in Pixeln (120–1600)", String(currentWidth));
      if (requestedWidth === null) return;

      const width = Number.parseInt(requestedWidth, 10);
      if (!Number.isInteger(width) || width < 120 || width > 1600) {
        setStatus("Bitte eine Bildbreite zwischen 120 und 1600 Pixeln eingeben.", "error");
        return;
      }

      try {
        const position = editor.view.posAtDOM(image, 0);
        editor.chain().focus().setNodeSelection(position).updateAttributes("noteImage", { width }).run();
      } catch (_error) {
        setStatus("Die Bildgröße konnte nicht geändert werden.", "error");
      }
    });
    restoreLocalDraft();
    updateCounts();
    updateToolbarState();
  }

  updateShortcutTooltips();
  initializeFolderTree();
  initializeNoteTreeDragAndDrop();
  initializeBulkSelection();
  initializeEditorZoom();
  initToolbarTabs();

  titleInput?.addEventListener("input", markDirty);
  titleInput?.addEventListener("change", markDirty);
  notesBackLink?.addEventListener("click", async (event) => {
    if (!note || (!dirty && !saving)) return;
    event.preventDefault();
    if (await saveNow()) window.location.assign(notesBackLink.href);
  });

  document.querySelector("[data-note-move-confirm]")?.addEventListener("click", moveNoteFromDialog);
  noteMoveDialog?.addEventListener("close", () => { movingNoteCard = null; });
  document.querySelector("[data-note-style-confirm]")?.addEventListener("click", applyStyleFromDialog);
  noteStyleDialog?.addEventListener("close", () => { stylingNoteCard = null; });
  noteIconGrid?.addEventListener("click", (event) => {
    const option = event.target.closest("[data-note-icon-value]");
    if (!option) return;
    noteIconGrid.querySelectorAll("[data-note-icon-value]").forEach((button) => button.classList.toggle("is-active", button === option));
  });
  noteColorGrid?.addEventListener("change", (event) => {
    const input = event.target.closest("input[name='note-style-color']");
    if (!input) return;
    noteColorGrid.querySelectorAll(".note-color-dot").forEach((dot) => {
      dot.classList.toggle("is-active", dot.querySelector("input") === input);
    });
  });
  document.querySelector("[data-table-dialog-open]")?.addEventListener("click", () => tableDialog?.showModal());
  mathInput?.addEventListener("input", renderMathPreview);
  mathBlockToggle?.addEventListener("change", renderMathPreview);

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-command]");
    if (!button) return;
    event.preventDefault();
    if (button.closest("[data-table-dialog]")) tableDialog.close();
    runCommand(button.dataset.command);
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-folder-action]");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    handleFolderAction(button);
  });

  document.addEventListener("click", (event) => {
    const noteAction = event.target.closest("[data-note-context-action]");
    if (noteAction && contextNoteCard) {
      const card = contextNoteCard;
      closeContextMenus();
      handleNoteContextAction(noteAction.dataset.noteContextAction, card);
      return;
    }
    const folderAction = event.target.closest("[data-folder-context-action]");
    if (folderAction && contextFolder) {
      const folder = contextFolder;
      closeContextMenus();
      handleFolderContextAction(folderAction.dataset.folderContextAction, folder);
    }
  });

  document.addEventListener("contextmenu", (event) => {
    const noteCard = event.target.closest("[data-note-card]");
    if (noteCard) {
      event.preventDefault();
      openNoteContextMenu(noteCard, event.clientX, event.clientY);
      return;
    }
    const folderSummary = event.target.closest("summary");
    const folder = folderSummary?.closest("[data-note-folder]");
    if (folder && folderSummary === folder.querySelector(":scope > summary")) {
      event.preventDefault();
      openFolderContextMenu(folder, event.clientX, event.clientY);
    }
  });

  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("[data-note-context-menu], [data-folder-context-menu]")) closeContextMenus();
  });
  window.addEventListener("resize", closeContextMenus);
  window.addEventListener("scroll", closeContextMenus, true);

  document.querySelectorAll("[data-format]").forEach((control) => {
    control.addEventListener("change", () => applyFormatControl(control));
  });
  document.querySelector("[data-image-input]")?.addEventListener("change", (event) => uploadSelectedFile(event.target, "image"));
  document.querySelector("[data-file-input]")?.addEventListener("change", (event) => uploadSelectedFile(event.target, "file"));
  document.querySelector("[data-trash-restore]")?.addEventListener("click", () => performAction("restore"));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeContextMenus();
      if (selectedNoteIds.size) clearNoteSelection();
    }
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      const noteCard = event.target.closest?.("[data-note-card]");
      const folderSummary = event.target.closest?.("[data-note-folder] > summary");
      const target = noteCard || folderSummary;
      if (target) {
        event.preventDefault();
        event.stopPropagation();
        const rect = target.getBoundingClientRect();
        if (noteCard) openNoteContextMenu(noteCard, rect.left + 18, rect.bottom - 4);
        else openFolderContextMenu(folderSummary.closest("[data-note-folder]"), rect.left + 18, rect.bottom - 4);
        return;
      }
    }
    if (event.target.matches("[data-shortcut-capture]")) return;
    const match = Object.entries(commands).find(([_action, config]) => shortcutMatches(event, config.shortcut));
    if (!match) return;
    event.preventDefault();
    event.stopPropagation();
    runCommand(match[0]);
  }, true);

  window.addEventListener("beforeunload", () => {
    if (dirty) persistLocalDraft();
  });

  function runCommand(action) {
    const editorActions = {
      save: () => saveNow({ force: true }),
      undo: () => editor?.chain().focus().undo().run(),
      redo: () => editor?.chain().focus().redo().run(),
      bold: () => editor?.chain().focus().toggleBold().run(),
      italic: () => editor?.chain().focus().toggleItalic().run(),
      underline: () => editor?.chain().focus().toggleUnderline().run(),
      strike: () => editor?.chain().focus().toggleStrike().run(),
      superscript: () => editor?.chain().focus().unsetSubscript().toggleSuperscript().run(),
      subscript: () => editor?.chain().focus().unsetSuperscript().toggleSubscript().run(),
      paragraph: () => editor?.chain().focus().setParagraph().run(),
      heading1: () => editor?.chain().focus().toggleHeading({ level: 1 }).run(),
      heading2: () => editor?.chain().focus().toggleHeading({ level: 2 }).run(),
      heading3: () => editor?.chain().focus().toggleHeading({ level: 3 }).run(),
      alignLeft: () => editor?.chain().focus().setTextAlign("left").run(),
      alignCenter: () => editor?.chain().focus().setTextAlign("center").run(),
      alignRight: () => editor?.chain().focus().setTextAlign("right").run(),
      alignJustify: () => editor?.chain().focus().setTextAlign("justify").run(),
      bulletList: () => editor?.chain().focus().toggleBulletList().run(),
      orderedList: () => editor?.chain().focus().toggleOrderedList().run(),
      taskList: () => editor?.chain().focus().toggleTaskList().run(),
      indent: () => sinkCurrentListItem(),
      outdent: () => liftCurrentListItem(),
      horizontalRule: () => editor?.chain().focus().setHorizontalRule().run(),
      codeBlock: () => editor?.chain().focus().toggleCodeBlock().run(),
      insertMath: insertMath,
      addComment: addCommentToSelection,
      insertTable: () => insertTable(),
      addRowBefore: () => editor?.chain().focus().addRowBefore().run(),
      addRowAfter: () => editor?.chain().focus().addRowAfter().run(),
      addColumnBefore: () => editor?.chain().focus().addColumnBefore().run(),
      addColumnAfter: () => editor?.chain().focus().addColumnAfter().run(),
      deleteRow: () => editor?.chain().focus().deleteRow().run(),
      deleteColumn: () => editor?.chain().focus().deleteColumn().run(),
      deleteTable: () => editor?.chain().focus().deleteTable().run(),
      mergeCells: () => editor?.chain().focus().mergeCells().run(),
      splitCell: () => editor?.chain().focus().splitCell().run(),
      clearFormat: () => editor?.chain().focus().unsetAllMarks().clearNodes().run(),
      link: editLink,
      fontFamily: () => openFormatControl("fontFamily"),
      fontSize: () => openFormatControl("fontSize"),
      textColor: () => openFormatControl("textColor"),
      highlight: () => openFormatControl("highlight"),
      lineHeight: () => openFormatControl("lineHeight"),
      image: () => document.querySelector("[data-image-input]")?.click(),
      attachment: () => document.querySelector("[data-file-input]")?.click(),
    };
    if (editorActions[action]) {
      if (action !== "save" && (!editor || !note?.can_edit || note?.is_deleted)) return;
      editorActions[action]();
      updateToolbarState();
      return;
    }
    const pageActions = {
      newNote: openTemplateDialog,
      focusSearch: () => document.querySelector("[data-notes-search]")?.focus(),
      pin: () => performAction(note?.is_pinned ? "unpin" : "pin"),
      archive: () => performAction(note?.is_archived ? "unarchive" : "archive"),
      style: () => openStyleDialog(null),
      duplicate: () => performAction("duplicate"),
      trash: trashCurrentNote,
      share: openShareDialog,
      comments: openCommentsDialog,
      outline: toggleOutlinePanel,
      versions: openVersionsDialog,
      exportPdf: exportPdf,
      exportMarkdown: exportMarkdown,
      print: () => window.print(),
      saveAsTemplate: saveNoteAsTemplate,
      shortcutSettings: openShortcutDialog,
    };
    pageActions[action]?.();
  }

  function renderMathPreview() {
    if (!mathPreview || !mathInput) return;
    renderMathInto(mathPreview, mathInput.value, Boolean(mathBlockToggle?.checked));
  }

  function insertMath() {
    if (!editor || !note?.can_edit || note?.is_deleted) return;
    openMathDialog({
      latex: "",
      isBlock: false,
      lockType: false,
      onConfirm: (latex) => {
        const type = mathBlockToggle?.checked ? "mathBlock" : "mathInline";
        editor.chain().focus().insertContent({ type, attrs: { latex } }).run();
      },
    });
  }

  function openMathDialog({ latex = "", isBlock = false, lockType = false, onConfirm, onDelete } = {}) {
    if (!mathDialog || !mathInput) return;
    mathInput.value = latex;
    if (mathBlockToggle) {
      mathBlockToggle.checked = isBlock;
      mathBlockToggle.disabled = lockType;
    }
    if (mathDeleteButton) mathDeleteButton.hidden = !onDelete;
    renderMathPreview();
    if (mathConfirmButton) {
      mathConfirmButton.onclick = () => {
        const value = mathInput.value.trim();
        if (!value) return;
        onConfirm?.(value);
        mathDialog.close();
      };
    }
    if (mathDeleteButton) {
      mathDeleteButton.onclick = () => {
        onDelete?.();
        mathDialog.close();
      };
    }
    mathDialog.showModal();
    mathInput.focus();
  }

  function insertTable() {
    if (!editor || !note?.can_edit || note?.is_deleted) return;
    const requestedRows = window.prompt("Anzahl der Zeilen (1–20)", "3");
    if (requestedRows === null) return;
    const requestedColumns = window.prompt("Anzahl der Spalten (1–20)", "3");
    if (requestedColumns === null) return;

    const rows = Number.parseInt(requestedRows, 10);
    const cols = Number.parseInt(requestedColumns, 10);
    if (
      !Number.isInteger(rows) || rows < 1 || rows > 20
      || !Number.isInteger(cols) || cols < 1 || cols > 20
    ) {
      setStatus("Tabellen dürfen zwischen 1×1 und 20×20 Zellen groß sein.", "error");
      return;
    }
    editor.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
  }

  function applyFormatControl(control) {
    if (!editor || !note?.can_edit || note?.is_deleted) return;
    const format = control.dataset.format;
    const value = control.value;
    if (format === "block") runCommand(value);
    if (format === "fontFamily") editor.chain().focus().setFontFamily(value).run();
    if (format === "fontSize") editor.chain().focus().setFontSize(value).run();
    if (format === "lineHeight") editor.chain().focus().setLineHeight(value).run();
    if (format === "textColor") editor.chain().focus().setColor(value).run();
    if (format === "highlight") editor.chain().focus().toggleHighlight({ color: value }).run();
    if (format === "codeLanguage") editor.chain().focus().updateAttributes("codeBlock", { language: value || null }).run();
  }

  function openFormatControl(format) {
    const control = document.querySelector(`[data-format="${format}"]`);
    if (!control) return;
    const panel = control.closest("[data-toolbar-panel]");
    if (panel?.hidden) activateToolbarTab(panel.dataset.toolbarPanel);
    control.focus();
    control.click();
  }

  function activateToolbarTab(name, { focusTab = false } = {}) {
    const tabs = Array.from(document.querySelectorAll("[data-toolbar-tab]"));
    const panels = Array.from(document.querySelectorAll("[data-toolbar-panel]"));
    const activeTab = tabs.find((tab) => tab.dataset.toolbarTab === name);
    if (!activeTab) return;

    tabs.forEach((tab) => {
      const isActive = tab === activeTab;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
      tab.tabIndex = isActive ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.toolbarPanel !== name;
    });
    if (focusTab) activeTab.focus();
  }

  function initToolbarTabs() {
    const tabsContainer = document.querySelector("[data-toolbar-tabs]");
    const tabs = Array.from(document.querySelectorAll("[data-toolbar-tab]"));
    if (!tabsContainer || !tabs.length) return;

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateToolbarTab(tab.dataset.toolbarTab));
    });

    tabsContainer.addEventListener("keydown", (event) => {
      const currentIndex = tabs.indexOf(document.activeElement);
      if (currentIndex === -1) return;

      let nextIndex = currentIndex;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabs.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabs.length - 1;
      else return;

      event.preventDefault();
      activateToolbarTab(tabs[nextIndex].dataset.toolbarTab, { focusTab: true });
    });
  }

  function sinkCurrentListItem() {
    if (editor.isActive("taskItem")) editor.chain().focus().sinkListItem("taskItem").run();
    else editor.chain().focus().sinkListItem("listItem").run();
  }

  function liftCurrentListItem() {
    if (editor.isActive("taskItem")) editor.chain().focus().liftListItem("taskItem").run();
    else editor.chain().focus().liftListItem("listItem").run();
  }

  function editLink() {
    const previous = editor.getAttributes("link").href || "https://";
    const href = window.prompt("Link-Adresse (https://, http:// oder mailto:)", previous);
    if (href === null) return;
    if (!href.trim()) editor.chain().focus().extendMarkRange("link").unsetLink().run();
    else {
      const wasApplied = editor.chain().focus().extendMarkRange("link").setLink({
        href: normalizeLinkHref(href),
        target: "_blank",
        rel: "noopener noreferrer",
      }).run();
      if (!wasApplied) setSaveStatus("Ungültige Link-Adresse");
    }
  }

  function currentSelectionElement() {
    if (!editor) return null;
    try {
      const dom = editor.view.domAtPos(editor.state.selection.$from.pos).node;
      return dom.nodeType === window.Node.TEXT_NODE ? dom.parentElement : dom;
    } catch (_error) {
      return null;
    }
  }

  function updateToolbarState() {
    if (!editor) return;
    const inCodeBlock = editor.isActive("codeBlock");
    const selectionElement = currentSelectionElement();
    const computedStyle = selectionElement ? window.getComputedStyle(selectionElement) : null;
    const effectivelyBold = computedStyle
      ? computedStyle.fontWeight === "bold" || Number.parseInt(computedStyle.fontWeight, 10) >= 600
      : false;
    const active = {
      bold: editor.isActive("bold") || effectivelyBold, italic: editor.isActive("italic"), underline: editor.isActive("underline"),
      strike: editor.isActive("strike"), superscript: editor.isActive("superscript"), subscript: editor.isActive("subscript"),
      bulletList: editor.isActive("bulletList"), orderedList: editor.isActive("orderedList"),
      taskList: editor.isActive("taskList"), alignLeft: editor.isActive({ textAlign: "left" }),
      alignCenter: editor.isActive({ textAlign: "center" }), alignRight: editor.isActive({ textAlign: "right" }),
      alignJustify: editor.isActive({ textAlign: "justify" }), link: editor.isActive("link"), codeBlock: inCodeBlock,
    };
    Object.entries(active).forEach(([action, value]) => {
      document.querySelectorAll(`[data-command="${action}"]`).forEach((button) => button.classList.toggle("is-active", value));
    });

    const languageGroup = document.querySelector("[data-code-language-group]");
    if (languageGroup) languageGroup.hidden = !inCodeBlock;
    const languageSelect = document.querySelector('[data-format="codeLanguage"]');
    if (languageSelect && inCodeBlock) {
      languageSelect.value = editor.getAttributes("codeBlock").language || "";
    }

    const blockSelect = document.querySelector('[data-format="block"]');
    if (blockSelect) {
      const headingLevel = [1, 2, 3].find((level) => editor.isActive("heading", { level }));
      blockSelect.value = headingLevel ? `heading${headingLevel}` : "paragraph";
    }

    const textStyle = editor.getAttributes("textStyle");
    const fontFamilySelect = document.querySelector('[data-format="fontFamily"]');
    if (fontFamilySelect) fontFamilySelect.value = textStyle.fontFamily || "Inter";
    const fontSizeSelect = document.querySelector('[data-format="fontSize"]');
    if (fontSizeSelect) {
      if (textStyle.fontSize) {
        fontSizeSelect.value = textStyle.fontSize;
      } else {
        const computedPx = computedStyle ? Number.parseFloat(computedStyle.fontSize) : 16;
        const available = Array.from(fontSizeSelect.options, (option) => Number.parseInt(option.value, 10));
        const nearest = available.reduce(
          (best, size) => (Math.abs(size - computedPx) < Math.abs(best - computedPx) ? size : best),
          available[0],
        );
        fontSizeSelect.value = `${nearest}px`;
      }
    }
    const lineHeightSelect = document.querySelector('[data-format="lineHeight"]');
    if (lineHeightSelect) lineHeightSelect.value = textStyle.lineHeight || "1.5";
    const textColorInput = document.querySelector('[data-format="textColor"]');
    if (textColorInput) textColorInput.value = textStyle.color || "#40372f";
    const highlightInput = document.querySelector('[data-format="highlight"]');
    if (highlightInput) highlightInput.value = editor.getAttributes("highlight").color || "#f1d99e";
  }

  function updateCounts() {
    if (!editor || !wordCount) return;
    const words = editor.storage.characterCount.words();
    const characters = editor.storage.characterCount.characters();
    wordCount.textContent = `${words} ${words === 1 ? "Wort" : "Wörter"} · ${characters} Zeichen`;
  }

  function toggleOutlinePanel() {
    if (!outlinePanel) return;
    const willShow = outlinePanel.hidden;
    outlinePanel.hidden = !willShow;
    document.querySelectorAll('[data-command="outline"]').forEach((button) => button.setAttribute("aria-pressed", String(willShow)));
    if (willShow) updateOutline();
  }

  function buildOutlineTree(headings) {
    const root = [];
    const stack = [{ level: 0, children: root }];
    headings.forEach((heading) => {
      const level = Number(heading.tagName.slice(1));
      while (stack.length > 1 && stack[stack.length - 1].level >= level) stack.pop();
      const node = { heading, level, children: [] };
      stack[stack.length - 1].children.push(node);
      stack.push(node);
    });
    return root;
  }

  function renderOutlineNodes(nodes, container) {
    nodes.forEach((node) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "note-outline-item";
      item.textContent = node.heading.textContent.trim() || "Ohne Titel";
      item.addEventListener("click", () => node.heading.scrollIntoView({ behavior: "smooth", block: "start" }));
      container.appendChild(item);
      if (node.children.length) {
        const childWrap = document.createElement("div");
        childWrap.className = "note-outline-children";
        renderOutlineNodes(node.children, childWrap);
        container.appendChild(childWrap);
      }
    });
  }

  function updateOutline() {
    if (!outlineList) return;
    const editorElement = document.querySelector("[data-note-editor]");
    const headings = editorElement ? Array.from(editorElement.querySelectorAll("h1, h2, h3")) : [];
    outlineList.innerHTML = "";
    if (!headings.length) {
      const empty = document.createElement("p");
      empty.className = "note-outline-empty";
      empty.textContent = "Noch keine Überschriften in dieser Notiz.";
      outlineList.appendChild(empty);
      return;
    }
    renderOutlineNodes(buildOutlineTree(headings), outlineList);
  }

  function currentPayload() {
    return {
      title: titleInput?.value || "Unbenannte Notiz",
      document: editor?.getJSON(),
      base_revision: note?.revision || 0,
    };
  }

  function markDirty() {
    if (applying || !note?.can_edit || note?.is_deleted) return;
    editGeneration += 1;
    dirty = true;
    setSaveStatus("Ungespeichert");
    persistLocalDraft();
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => saveNow(), 1000);
    if (!maxWaitTimer) maxWaitTimer = window.setTimeout(() => saveNow(), 5000);
  }

  function saveNow(options = {}) {
    const saveTask = () => savePendingChanges(options);
    saveQueue = saveQueue.then(saveTask, saveTask);
    return saveQueue;
  }

  async function savePendingChanges({ conflictResolution = false, force = false } = {}) {
    if (!note || !editor || (conflictServerNote && !conflictResolution)) return false;
    if (!dirty && !conflictResolution && !force) return true;
    let resolveConflict = conflictResolution;
    let forceSave = force;

    while (dirty || resolveConflict || forceSave) {
      const savedGeneration = editGeneration;
      saving = true;
      window.clearTimeout(debounceTimer);
      window.clearTimeout(maxWaitTimer);
      debounceTimer = null;
      maxWaitTimer = null;
      setSaveStatus("Speichern …");
      const payload = { ...currentPayload(), conflict_resolution: resolveConflict };
      if (resolveConflict && conflictServerNote) payload.base_revision = conflictServerNote.revision;

      try {
        const { response, data } = await requestJson(`/notes/api/${note.id}/`, { method: "PATCH", body: JSON.stringify(payload) });
        if (response.status === 409) {
          conflictServerNote = data.note;
          conflictDraft = payload;
          setSaveStatus("Konflikt – nicht gespeichert");
          conflictDialog?.showModal();
          return false;
        }
        if (!response.ok || !data.ok) {
          dirty = true;
          persistLocalDraft();
          const message = data.error || "Speichern fehlgeschlagen.";
          if (response.status >= 500) {
            setSaveStatus(`Serverfehler – ${message}`);
            window.clearTimeout(retryTimer);
            retryTimer = window.setTimeout(() => saveNow(), 5000);
          } else {
            setSaveStatus(`Nicht gespeichert – ${message}`);
          }
          return false;
        }

        note = data.note;
        conflictServerNote = null;
        conflictDraft = null;
        dirty = editGeneration !== savedGeneration;
        updateNoteCard(note);

        if (dirty) {
          persistLocalDraft();
          setSaveStatus("Weitere Änderungen werden gespeichert …");
        } else {
          localStorage.removeItem(draftKey());
          setSaveStatus(`Gespeichert ${new Date(note.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
        }
      } catch (error) {
        dirty = true;
        persistLocalDraft();
        setSaveStatus(`Offline – ${error.message}`);
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(() => saveNow(), 5000);
        return false;
      } finally {
        saving = false;
      }

      resolveConflict = false;
      forceSave = false;
    }

    return true;
  }

  async function exportPdf() {
    while (saving) await new Promise((resolve) => window.setTimeout(resolve, 50));
    if (dirty && !await saveNow()) {
      setSaveStatus("PDF-Export erst nach erfolgreichem Speichern möglich");
      return;
    }
    window.location.assign(`/notes/${note.id}/export/pdf/`);
  }

  async function exportMarkdown() {
    while (saving) await new Promise((resolve) => window.setTimeout(resolve, 50));
    if (dirty && !await saveNow()) {
      setSaveStatus("Markdown-Export erst nach erfolgreichem Speichern möglich");
      return;
    }
    window.location.assign(`/notes/${note.id}/export/markdown/`);
  }

  async function saveNoteAsTemplate() {
    if (!note) return;
    const name = window.prompt("Name der Vorlage", note.title);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    if (dirty && !await saveNow()) return;
    const { response, data } = await requestJson("/notes/api/templates/", {
      method: "POST",
      body: JSON.stringify({ note_id: note.id, name: trimmed }),
    });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Die Vorlage konnte nicht gespeichert werden.");
      return;
    }
    setSaveStatus("Als Vorlage gespeichert");
  }

  function setSaveStatus(text) {
    if (saveStatus) saveStatus.textContent = text;
  }

  function updateNoteCard(savedNote) {
    const card = document.querySelector(`[data-note-card="${savedNote.id}"]`);
    if (!card) return;

    const title = card.querySelector("[data-note-card-title]");
    const preview = card.querySelector("[data-note-card-preview]");
    const updated = card.querySelector("[data-note-card-updated]");

    if (title) title.textContent = savedNote.title || "Unbenannte Notiz";
    card.dataset.noteCardName = savedNote.title || "Unbenannte Notiz";
    card.dataset.noteFolderId = savedNote.folder_id || "";
    card.dataset.notePinned = String(Boolean(savedNote.is_pinned));
    card.dataset.noteArchived = String(Boolean(savedNote.is_archived));
    if (preview) preview.textContent = savedNote.preview?.trim() || "Noch kein Inhalt";
    if (updated) {
      updated.dateTime = savedNote.updated_at;
      updated.textContent = savedNote.updated_at?.slice(0, 10) || "";
    }
  }

  function draftKey() {
    return `lunora-note-draft-${note?.id || "new"}`;
  }

  function persistLocalDraft() {
    if (!note || !editor) return;
    try {
      localStorage.setItem(draftKey(), JSON.stringify({ ...currentPayload(), saved_at: Date.now() }));
    } catch (_error) {
      // The visible save state still warns the user if browser storage is unavailable.
    }
  }

  function restoreLocalDraft() {
    try {
      const raw = localStorage.getItem(draftKey());
      if (!raw) return;
      const draft = JSON.parse(raw);
      if (draft.base_revision !== note.revision) return;
      if (!window.confirm("Es gibt einen lokal gesicherten Entwurf. Wiederherstellen?")) {
        localStorage.removeItem(draftKey());
        return;
      }
      applying = true;
      titleInput.value = draft.title;
      editor.commands.setContent(draft.document);
      applying = false;
      dirty = true;
      setSaveStatus("Lokaler Entwurf – ungespeichert");
      markDirty();
    } catch (_error) {
      localStorage.removeItem(draftKey());
    }
  }

  function openTemplateDialog(folderId = null) {
    const parsedFolderId = Number.parseInt(folderId, 10);
    pendingFolderId = Number.isInteger(parsedFolderId) && parsedFolderId > 0 ? parsedFolderId : null;
    templateDialog?.showModal();
  }

  templateDialog?.addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-custom-template-delete]");
    if (deleteButton) {
      deleteCustomTemplate(deleteButton.dataset.customTemplateDelete);
      return;
    }
    const customChoice = event.target.closest("[data-custom-template-choice]");
    if (customChoice) {
      templateDialog.close();
      createNewNote(null, pendingFolderId, customChoice.dataset.customTemplateChoice);
      return;
    }
    const choice = event.target.closest("[data-template-choice]");
    if (!choice) return;
    templateDialog.close();
    createNewNote(choice.dataset.templateChoice, pendingFolderId);
  });

  async function createNewNote(template = "blank", folderId = null, customTemplateId = null) {
    const body = { folder_id: folderId };
    if (customTemplateId) body.custom_template_id = customTemplateId;
    else body.template = template;
    const { response, data } = await requestJson("/notes/api/create/", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (response.ok && data.note) {
      if (folderId) rememberFolderOpen(folderId, true);
      window.location.assign(`/notes/${data.note.id}/`);
    }
    else window.alert(data.error || "Die Notiz konnte nicht erstellt werden.");
  }

  async function deleteCustomTemplate(templateId) {
    if (!window.confirm("Diese Vorlage löschen?")) return;
    const { response, data } = await requestJson(`/notes/api/templates/${templateId}/`, { method: "DELETE" });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Die Vorlage konnte nicht gelöscht werden.");
      return;
    }
    window.location.reload();
  }

  function folderStorageKey() {
    return "lunora-note-folders-open";
  }

  function readOpenFolders() {
    try {
      const values = JSON.parse(localStorage.getItem(folderStorageKey()) || "[]");
      return new Set(Array.isArray(values) ? values.map(String) : []);
    } catch (_error) {
      return new Set();
    }
  }

  function rememberFolderOpen(folderId, isOpen) {
    if (!folderId) return;
    const openFolders = readOpenFolders();
    const key = String(folderId);
    if (isOpen) openFolders.add(key);
    else openFolders.delete(key);
    try {
      localStorage.setItem(folderStorageKey(), JSON.stringify([...openFolders]));
    } catch (_error) {
      // Folder toggling still works when browser storage is unavailable.
    }
  }

  function initializeFolderTree() {
    const openFolders = readOpenFolders();
    document.querySelectorAll("details[data-note-folder]").forEach((folder) => {
      if (folder.classList.contains("contains-selected")) folder.open = true;
      else if (openFolders.has(folder.dataset.noteFolder)) folder.open = true;
      folder.addEventListener("toggle", () => rememberFolderOpen(folder.dataset.noteFolder, folder.open));
    });
  }

  function clearTreeDropVisuals() {
    document.querySelectorAll(".is-drop-before, .is-drop-after, .is-drop-inside, .is-drop-root").forEach((element) => {
      element.classList.remove("is-drop-before", "is-drop-after", "is-drop-inside", "is-drop-root");
    });
    treeDropDescriptor = null;
  }

  function setHoveredDropFolder(folder) {
    if (folder === hoveredDropFolder) return;
    window.clearTimeout(folderExpandTimer);
    hoveredDropFolder = folder;
    if (folder && !folder.open) {
      folderExpandTimer = window.setTimeout(() => {
        folder.open = true;
        rememberFolderOpen(folder.dataset.noteFolder, true);
      }, 650);
    }
  }

  function resetTreeDragState() {
    clearTreeDropVisuals();
    window.clearTimeout(folderExpandTimer);
    hoveredDropFolder = null;
    draggedTreeItem?.classList.remove("is-being-dragged");
    draggedTreeItem = null;
    notesList?.classList.remove("is-dragging");
  }

  function invalidFolderDrop(destinationFolder) {
    if (!draggedTreeItem || draggedTreeItem.dataset.itemType !== "folder" || !destinationFolder) return false;
    return draggedTreeItem === destinationFolder || draggedTreeItem.contains(destinationFolder);
  }

  function startTreeDrag(item) {
    closeContextMenus();
    draggedTreeItem = item;
    notesList.classList.add("is-dragging");
    item.classList.add("is-being-dragged");
  }

  function updateTreeDropTarget(targetElement, clientY) {
    if (!draggedTreeItem || !targetElement) return;
    const rootTarget = targetElement.closest("[data-root-drop-target]");
    const targetItem = targetElement.closest("[data-tree-item]");
    clearTreeDropVisuals();

    if (rootTarget) {
      setHoveredDropFolder(null);
      rootTarget.classList.add("is-drop-root");
      treeDropDescriptor = { placement: "root", targetType: null, targetId: null, destinationFolderId: null };
      return;
    }

    if (targetItem === draggedTreeItem) {
      setHoveredDropFolder(null);
      return;
    }

    if (targetItem?.dataset.itemType === "note" && targetItem !== draggedTreeItem) {
      const destinationFolder = targetItem.parentElement?.closest("[data-note-folder]") || null;
      if (invalidFolderDrop(destinationFolder)) return;
      setHoveredDropFolder(null);
      const rect = targetItem.getBoundingClientRect();
      const placement = clientY < rect.top + rect.height / 2 ? "before" : "after";
      targetItem.classList.add(placement === "before" ? "is-drop-before" : "is-drop-after");
      treeDropDescriptor = {
        placement,
        targetType: "note",
        targetId: Number.parseInt(targetItem.dataset.itemId, 10),
        destinationFolderId: destinationFolder ? Number.parseInt(destinationFolder.dataset.noteFolder, 10) : null,
      };
      return;
    }

    const folder = targetElement.closest("[data-note-folder]");
    if (folder && !invalidFolderDrop(folder)) {
      setHoveredDropFolder(folder);
      folder.classList.add("is-drop-inside");
      treeDropDescriptor = {
        placement: "inside",
        targetType: "folder",
        targetId: Number.parseInt(folder.dataset.noteFolder, 10),
        destinationFolderId: Number.parseInt(folder.dataset.noteFolder, 10),
      };
    }
  }

  async function commitTreeDrop() {
    if (!draggedTreeItem || !treeDropDescriptor) {
      resetTreeDragState();
      return;
    }
    const dragged = {
      type: draggedTreeItem.dataset.itemType,
      id: Number.parseInt(draggedTreeItem.dataset.itemId, 10),
    };
    const target = { ...treeDropDescriptor };
    resetTreeDragState();
    const { response, data } = await requestJson("/notes/api/tree/move/", {
      method: "PATCH",
      body: JSON.stringify({
        item_type: dragged.type,
        item_id: dragged.id,
        placement: target.placement,
        target_type: target.targetType,
        target_id: target.targetId,
      }),
    });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Das Element konnte nicht verschoben werden.");
      return;
    }
    if (target.destinationFolderId) rememberFolderOpen(target.destinationFolderId, true);
    const url = new URL(window.location.href);
    url.searchParams.set("sort", "custom");
    window.location.assign(url.toString());
  }

  function initializeNoteTreeDragAndDrop() {
    if (!notesList) return;

    notesList.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.isPrimary === false || event.pointerType === "touch") return;
      if (event.target.closest("button, input, select, textarea, [contenteditable='true']")) return;
      const item = event.target.closest("[data-tree-item]");
      if (!item || item.dataset.treeDraggable !== "true" || item.dataset.noteDeleted === "true") return;
      pendingTreePointer = {
        pointerId: event.pointerId,
        item,
        startX: event.clientX,
        startY: event.clientY,
        started: false,
      };
    });

    notesList.addEventListener("click", (event) => {
      if (!suppressTreeClick) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressTreeClick = false;
    }, true);

    window.addEventListener("pointermove", (event) => {
      if (!pendingTreePointer || event.pointerId !== pendingTreePointer.pointerId) return;
      if (!pendingTreePointer.started) {
        const distance = Math.hypot(
          event.clientX - pendingTreePointer.startX,
          event.clientY - pendingTreePointer.startY,
        );
        if (distance < 6) return;
        pendingTreePointer.started = true;
        startTreeDrag(pendingTreePointer.item);
      }
      event.preventDefault();
      updateTreeDropTarget(document.elementFromPoint(event.clientX, event.clientY), event.clientY);
    }, { passive: false });

    window.addEventListener("pointerup", (event) => {
      if (!pendingTreePointer || event.pointerId !== pendingTreePointer.pointerId) return;
      const wasDragging = pendingTreePointer.started;
      pendingTreePointer = null;
      if (!wasDragging) return;
      event.preventDefault();
      updateTreeDropTarget(document.elementFromPoint(event.clientX, event.clientY), event.clientY);
      suppressTreeClick = true;
      window.setTimeout(() => { suppressTreeClick = false; }, 0);
      commitTreeDrop();
    });

    window.addEventListener("pointercancel", () => {
      pendingTreePointer = null;
      resetTreeDragState();
    });

    window.addEventListener("blur", () => {
      pendingTreePointer = null;
      resetTreeDragState();
    });
  }

  function initializeBulkSelection() {
    if (!notesList) return;

    notesList.addEventListener("click", (event) => {
      const link = event.target.closest(".note-list-card-link");
      if (!link) return;
      const card = link.closest("[data-note-card]");
      if (!card) return;
      const noteId = Number.parseInt(card.dataset.noteCard, 10);
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        toggleNoteSelection(noteId);
        return;
      }
      if (event.shiftKey) {
        event.preventDefault();
        selectNoteRange(noteId);
      }
    });

    document.querySelector("[data-notes-bulk-select-all]")?.addEventListener("click", () => {
      notesList.querySelectorAll("[data-note-card]").forEach((card) => {
        selectedNoteIds.add(Number.parseInt(card.dataset.noteCard, 10));
      });
      syncSelectionVisuals();
      updateBulkBar();
    });

    document.querySelector("[data-notes-bulk-clear]")?.addEventListener("click", clearNoteSelection);

    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-notes-bulk-action]");
      if (!button) return;
      runBulkAction(button.dataset.notesBulkAction);
    });

    bulkFolderSelect?.addEventListener("change", async () => {
      if (!bulkFolderSelect.value || selectedNoteIds.size === 0) return;
      if (!(await ensureSelectionSaved())) return;
      const folderId = bulkFolderSelect.value === "none" ? null : Number.parseInt(bulkFolderSelect.value, 10);
      const { response, data } = await requestJson("/notes/api/bulk-action/", {
        method: "POST",
        body: JSON.stringify({ note_ids: [...selectedNoteIds], action: "move_folder", folder_id: folderId }),
      });
      if (!response.ok || !data.ok) {
        bulkFolderSelect.value = "";
        window.alert(data.error || "Die Notizen konnten nicht verschoben werden.");
        return;
      }
      reportBulkSkips(data.skipped_ids);
      window.location.reload();
    });
  }

  function loadEditorZoom() {
    const stored = Number.parseFloat(window.localStorage.getItem(EDITOR_ZOOM_STORAGE_KEY));
    if (Number.isFinite(stored) && stored >= EDITOR_ZOOM_MIN && stored <= EDITOR_ZOOM_MAX) return stored;
    return 1;
  }

  function applyEditorZoom() {
    const page = document.querySelector("[data-note-editor]");
    const frame = document.querySelector("[data-note-zoom-frame]");
    if (page && frame) {
      // Reset to the natural, unzoomed layout before measuring it, since a
      // pinned width/frame size from a previous zoom would otherwise be
      // measured instead of the page's real CSS-driven size.
      page.style.width = "";
      page.style.margin = "";
      page.style.transform = "";
      page.style.transformOrigin = "";
      frame.style.width = "";
      frame.style.height = "";
      if (editorZoom !== 1) {
        const naturalWidth = page.offsetWidth;
        const naturalHeight = page.offsetHeight;
        // Pin the page to its natural pixel size and scale it visually, then
        // size the frame to match so the scroll container's overflow (and
        // therefore horizontal/vertical scrolling) accounts for the zoomed
        // size instead of collapsing back to the unzoomed percentage width.
        page.style.width = `${naturalWidth}px`;
        page.style.margin = "0";
        page.style.transformOrigin = "top left";
        page.style.transform = `scale(${editorZoom})`;
        frame.style.width = `${naturalWidth * editorZoom}px`;
        frame.style.height = `${naturalHeight * editorZoom}px`;
      }
    }
    const indicator = document.querySelector("[data-editor-zoom-level]");
    if (indicator) indicator.textContent = `${Math.round(editorZoom * 100)}%`;
  }

  function setEditorZoom(nextZoom) {
    const clamped = Math.min(EDITOR_ZOOM_MAX, Math.max(EDITOR_ZOOM_MIN, Math.round(nextZoom * 100) / 100));
    if (clamped === editorZoom) return;
    editorZoom = clamped;
    window.localStorage.setItem(EDITOR_ZOOM_STORAGE_KEY, String(editorZoom));
    applyEditorZoom();
    const indicator = document.querySelector("[data-editor-zoom-level]");
    if (indicator) {
      indicator.classList.add("is-visible");
      window.clearTimeout(editorZoomIndicatorTimer);
      editorZoomIndicatorTimer = window.setTimeout(() => indicator.classList.remove("is-visible"), 1200);
    }
  }

  function initializeEditorZoom() {
    const editorScroll = document.querySelector(".note-editor-scroll");
    if (!editorScroll) return;
    applyEditorZoom();
    editorScroll.addEventListener(
      "wheel",
      (event) => {
        if (!event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        setEditorZoom(editorZoom + (event.deltaY < 0 ? EDITOR_ZOOM_STEP : -EDITOR_ZOOM_STEP));
      },
      { passive: false }
    );
    document.querySelector("[data-editor-zoom-level]")?.addEventListener("click", () => setEditorZoom(1));
    window.addEventListener("resize", () => {
      if (editorZoom !== 1) applyEditorZoom();
    });
  }

  function toggleNoteSelection(noteId) {
    if (selectedNoteIds.has(noteId)) selectedNoteIds.delete(noteId);
    else selectedNoteIds.add(noteId);
    selectionAnchorId = noteId;
    syncSelectionVisuals();
    updateBulkBar();
  }

  function selectNoteRange(noteId) {
    const ids = [...notesList.querySelectorAll("[data-note-card]")].map((card) => Number.parseInt(card.dataset.noteCard, 10));
    const anchorId = selectionAnchorId !== null && ids.includes(selectionAnchorId) ? selectionAnchorId : noteId;
    const anchorIndex = ids.indexOf(anchorId);
    const targetIndex = ids.indexOf(noteId);
    if (anchorIndex === -1 || targetIndex === -1) return;
    const [start, end] = anchorIndex < targetIndex ? [anchorIndex, targetIndex] : [targetIndex, anchorIndex];
    selectedNoteIds.clear();
    ids.slice(start, end + 1).forEach((id) => selectedNoteIds.add(id));
    selectionAnchorId = anchorId;
    syncSelectionVisuals();
    updateBulkBar();
  }

  function syncSelectionVisuals() {
    notesList?.querySelectorAll("[data-note-card]").forEach((card) => {
      const id = Number.parseInt(card.dataset.noteCard, 10);
      card.classList.toggle("is-selected", selectedNoteIds.has(id));
    });
  }

  function clearNoteSelection() {
    selectedNoteIds.clear();
    selectionAnchorId = null;
    syncSelectionVisuals();
    updateBulkBar();
  }

  function updateBulkBar() {
    if (!bulkBar) return;
    const count = selectedNoteIds.size;
    bulkBar.hidden = count === 0;
    if (bulkCount) bulkCount.textContent = count === 1 ? "1 ausgewählt" : `${count} ausgewählt`;
  }

  async function ensureSelectionSaved() {
    if (note && selectedNoteIds.has(note.id)) return ensureCurrentNoteSaved(note.id);
    return true;
  }

  function reportBulkSkips(skippedIds) {
    if (skippedIds && skippedIds.length) {
      window.alert(`${skippedIds.length} Notiz(en) konnten nicht bearbeitet werden (keine Berechtigung).`);
    }
  }

  async function runBulkAction(action) {
    if (selectedNoteIds.size === 0) return;
    if (action === "purge" && !window.confirm(`${selectedNoteIds.size} Notiz(en) endgültig löschen? Das kann nicht rückgängig gemacht werden.`)) return;
    if (action === "trash" && !window.confirm(`${selectedNoteIds.size} Notiz(en) für 30 Tage in den Papierkorb verschieben?`)) return;
    if (!(await ensureSelectionSaved())) return;
    const { response, data } = await requestJson("/notes/api/bulk-action/", {
      method: "POST",
      body: JSON.stringify({ note_ids: [...selectedNoteIds], action }),
    });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Die Aktion konnte nicht ausgeführt werden.");
      return;
    }
    reportBulkSkips(data.skipped_ids);
    window.location.reload();
  }

  function datasetFlag(value) {
    return value === "true";
  }

  function setContextItem(menu, action, { hidden = false, label = null } = {}) {
    const button = menu?.querySelector(`[data-note-context-action="${action}"], [data-folder-context-action="${action}"]`);
    if (!button) return;
    button.hidden = hidden;
    const labelTarget = button.querySelector("[data-note-context-label], [data-folder-context-label]");
    if (label !== null && labelTarget) labelTarget.textContent = label;
  }

  function positionContextMenu(menu, x, y) {
    if (!menu) return;
    menu.hidden = false;
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    const rect = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(x, window.innerWidth - rect.width - 8));
    const top = Math.max(8, Math.min(y, window.innerHeight - rect.height - 8));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.querySelector("button:not([hidden])")?.focus({ preventScroll: true });
  }

  function closeContextMenus() {
    noteContextMenu?.setAttribute("hidden", "");
    folderContextMenu?.setAttribute("hidden", "");
    contextNoteCard?.classList.remove("has-context-menu");
    contextFolder?.classList.remove("has-context-menu");
    contextNoteCard = null;
    contextFolder = null;
  }

  function openNoteContextMenu(card, x, y) {
    closeContextMenus();
    contextNoteCard = card;
    card.classList.add("has-context-menu");
    const isDeleted = datasetFlag(card.dataset.noteDeleted);
    const canEdit = datasetFlag(card.dataset.noteCanEdit);
    const canManage = datasetFlag(card.dataset.noteCanManage);
    const isPinned = datasetFlag(card.dataset.notePinned);
    const isArchived = datasetFlag(card.dataset.noteArchived);
    setContextItem(noteContextMenu, "pin", { hidden: isDeleted, label: isPinned ? "Pin lösen" : "Anpinnen" });
    setContextItem(noteContextMenu, "rename", { hidden: isDeleted || !canEdit });
    setContextItem(noteContextMenu, "duplicate", { hidden: isDeleted });
    setContextItem(noteContextMenu, "move", { hidden: isDeleted });
    setContextItem(noteContextMenu, "style", { hidden: isDeleted });
    setContextItem(noteContextMenu, "archive", {
      hidden: isDeleted,
      label: isArchived ? "Aus Archiv holen" : "Archivieren",
    });
    setContextItem(noteContextMenu, "restore", { hidden: !isDeleted || !canManage });
    setContextItem(noteContextMenu, "delete", {
      hidden: !canManage,
      label: isDeleted ? "Endgültig löschen" : "In Papierkorb",
    });
    positionContextMenu(noteContextMenu, x, y);
  }

  function openFolderContextMenu(folder, x, y) {
    closeContextMenus();
    contextFolder = folder;
    folder.classList.add("has-context-menu");
    setContextItem(folderContextMenu, "toggle", { label: folder.open ? "Zuklappen" : "Aufklappen" });
    positionContextMenu(folderContextMenu, x, y);
  }

  async function ensureCurrentNoteSaved(noteId) {
    if (!note || note.id !== noteId || !dirty) return true;
    return saveNow();
  }

  async function renameNoteFromCard(card) {
    const noteId = Number.parseInt(card.dataset.noteCard, 10);
    const currentTitle = card.dataset.noteCardName || "Unbenannte Notiz";
    const requestedTitle = window.prompt("Neuer Notiztitel", currentTitle);
    if (requestedTitle === null || requestedTitle.trim() === currentTitle) return;

    if (note?.id === noteId && titleInput) {
      titleInput.value = requestedTitle.trim();
      markDirty();
      await saveNow();
      return;
    }

    const { response: loadResponse, data: loadData } = await requestJson(`/notes/api/${noteId}/`);
    if (!loadResponse.ok || !loadData.note) {
      window.alert(loadData.error || "Die Notiz konnte nicht geladen werden.");
      return;
    }
    const loadedNote = loadData.note;
    const { response, data } = await requestJson(`/notes/api/${noteId}/`, {
      method: "PATCH",
      body: JSON.stringify({
        title: requestedTitle.trim(),
        document: loadedNote.document,
        base_revision: loadedNote.revision,
      }),
    });
    if (!response.ok || !data.note) {
      window.alert(response.status === 409 ? "Die Notiz wurde inzwischen geändert." : data.error || "Umbenennen fehlgeschlagen.");
      return;
    }
    window.location.reload();
  }

  async function performNoteCardAction(card, action) {
    const noteId = Number.parseInt(card.dataset.noteCard, 10);
    if (!await ensureCurrentNoteSaved(noteId)) return;
    const { response, data } = await requestJson(`/notes/api/${noteId}/actions/`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Die Aktion konnte nicht ausgeführt werden.");
      return;
    }
    if (action === "duplicate" && data.note) {
      window.location.assign(`/notes/${data.note.id}/`);
      return;
    }
    if (["trash", "purge"].includes(action) && note?.id === noteId) window.location.assign("/notes/");
    else window.location.reload();
  }

  function openMoveNoteDialog(card) {
    movingNoteCard = card;
    if (noteMoveFolder) noteMoveFolder.value = card.dataset.noteFolderId || "";
    noteMoveDialog?.showModal();
  }

  function openStyleDialog(card) {
    const noteId = card ? Number.parseInt(card.dataset.noteCard, 10) : note?.id;
    if (!noteStyleDialog || !noteId) return;
    stylingNoteCard = card;
    const currentColor = card ? (card.dataset.noteColor || "") : (note?.color || "");
    const currentIcon = card ? (card.dataset.noteIcon || "") : (note?.icon || "");
    noteColorGrid?.querySelectorAll(".note-color-dot").forEach((dot) => {
      const input = dot.querySelector("input");
      const matches = input?.value === currentColor;
      if (input) input.checked = matches;
      dot.classList.toggle("is-active", matches);
    });
    noteIconGrid?.querySelectorAll("[data-note-icon-value]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.noteIconValue === currentIcon);
    });
    noteStyleDialog.showModal();
  }

  async function performNoteStyleAction(noteId, { color, icon }) {
    if (!await ensureCurrentNoteSaved(noteId)) return false;
    const { response, data } = await requestJson(`/notes/api/${noteId}/actions/`, {
      method: "POST",
      body: JSON.stringify({ action: "style", color, icon }),
    });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Die Farbe/das Icon konnte nicht gespeichert werden.");
      return false;
    }
    return true;
  }

  async function applyStyleFromDialog() {
    const noteId = stylingNoteCard ? Number.parseInt(stylingNoteCard.dataset.noteCard, 10) : note?.id;
    if (!noteId) return;
    const color = noteColorGrid?.querySelector("input[name='note-style-color']:checked")?.value ?? "";
    const icon = noteIconGrid?.querySelector(".is-active")?.dataset.noteIconValue ?? "";
    if (!await performNoteStyleAction(noteId, { color, icon })) return;
    stylingNoteCard = null;
    noteStyleDialog?.close();
    window.location.reload();
  }

  async function moveNoteFromDialog() {
    if (!movingNoteCard) return;
    const card = movingNoteCard;
    const noteId = Number.parseInt(card.dataset.noteCard, 10);
    if (!await ensureCurrentNoteSaved(noteId)) return;
    const parsedFolderId = Number.parseInt(noteMoveFolder?.value, 10);
    const folderId = Number.isInteger(parsedFolderId) && parsedFolderId > 0 ? parsedFolderId : null;
    const { response, data } = await requestJson(`/notes/api/${noteId}/folder/`, {
      method: "PATCH",
      body: JSON.stringify({ folder_id: folderId }),
    });
    if (!response.ok || !data.note) {
      window.alert(data.error || "Die Notiz konnte nicht verschoben werden.");
      return;
    }
    if (folderId) rememberFolderOpen(folderId, true);
    movingNoteCard = null;
    noteMoveDialog?.close();
    window.location.reload();
  }

  function handleNoteContextAction(action, card) {
    const isDeleted = datasetFlag(card.dataset.noteDeleted);
    if (action === "open") {
      const href = card.querySelector(".note-list-card-link")?.href;
      if (href) window.location.assign(href);
      return;
    }
    if (action === "pin") {
      performNoteCardAction(card, datasetFlag(card.dataset.notePinned) ? "unpin" : "pin");
      return;
    }
    if (action === "rename") {
      renameNoteFromCard(card);
      return;
    }
    if (action === "duplicate") {
      performNoteCardAction(card, "duplicate");
      return;
    }
    if (action === "move") {
      openMoveNoteDialog(card);
      return;
    }
    if (action === "style") {
      openStyleDialog(card);
      return;
    }
    if (action === "archive") {
      performNoteCardAction(card, datasetFlag(card.dataset.noteArchived) ? "unarchive" : "archive");
      return;
    }
    if (action === "restore") {
      performNoteCardAction(card, "restore");
      return;
    }
    if (action === "delete") {
      const message = isDeleted
        ? "Diese Notiz und alle Dateien endgültig löschen?"
        : "Diese Notiz für 30 Tage in den Papierkorb verschieben?";
      if (window.confirm(message)) performNoteCardAction(card, isDeleted ? "purge" : "trash");
    }
  }

  function handleFolderAction(button) {
    return runFolderAction(button.dataset.folderAction, {
      folder: button.closest("[data-note-folder]"),
      folderId: Number.parseInt(button.dataset.folderId, 10) || null,
      parentId: Number.parseInt(button.dataset.parentId, 10) || null,
    });
  }

  function handleFolderContextAction(action, folder) {
    if (action === "toggle") {
      folder.open = !folder.open;
      return;
    }
    const folderId = Number.parseInt(folder.dataset.noteFolder, 10) || null;
    return runFolderAction(action, {
      folder,
      folderId,
      parentId: action === "create" ? folderId : null,
    });
  }

  async function runFolderAction(action, { folder = null, folderId = null, parentId = null } = {}) {
    if (action === "new-note") {
      openTemplateDialog(folderId);
      return;
    }

    if (action === "create") {
      const name = window.prompt(parentId ? "Name des neuen Unterordners" : "Name des neuen Ordners", "Neuer Ordner");
      if (name === null) return;
      const { response, data } = await requestJson("/notes/api/folders/", {
        method: "POST",
        body: JSON.stringify({ name, parent_id: parentId }),
      });
      if (!response.ok || !data.folder) {
        window.alert(data.error || "Der Ordner konnte nicht erstellt werden.");
        return;
      }
      if (parentId) rememberFolderOpen(parentId, true);
      window.location.reload();
      return;
    }

    if (!folderId || !folder) return;
    if (action === "rename") {
      const name = window.prompt("Neuer Ordnername", folder.dataset.folderName || "");
      if (name === null) return;
      const { response, data } = await requestJson(`/notes/api/folders/${folderId}/`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      if (!response.ok || !data.folder) {
        window.alert(data.error || "Der Ordner konnte nicht umbenannt werden.");
        return;
      }
      window.location.reload();
      return;
    }
    if (action === "delete") {
      const confirmed = window.confirm(
        "Ordner löschen? Enthaltene Notizen und Unterordner werden eine Ebene nach oben verschoben."
      );
      if (!confirmed) return;
      const { response, data } = await requestJson(`/notes/api/folders/${folderId}/`, { method: "DELETE" });
      if (!response.ok || !data.ok) {
        window.alert(data.error || "Der Ordner konnte nicht gelöscht werden.");
        return;
      }
      rememberFolderOpen(folderId, false);
      window.location.reload();
    }
  }

  async function performAction(action) {
    if (!note) return;
    const { response, data } = await requestJson(`/notes/api/${note.id}/actions/`, { method: "POST", body: JSON.stringify({ action }) });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Die Aktion konnte nicht ausgeführt werden.");
      return;
    }
    if (action === "duplicate") window.location.assign(`/notes/${data.note.id}/`);
    else if (["trash", "purge"].includes(action)) window.location.assign("/notes/");
    else window.location.reload();
  }

  function trashCurrentNote() {
    if (!note?.can_manage) return;
    if (note.is_deleted) {
      if (window.confirm("Diese Notiz und alle Dateien endgültig löschen?")) performAction("purge");
    } else if (window.confirm("Diese Notiz für 30 Tage in den Papierkorb verschieben?")) {
      performAction("trash");
    }
  }

  async function uploadSelectedFile(input, kind) {
    const file = input.files?.[0];
    input.value = "";
    if (!file || !note?.can_edit) return;
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    setSaveStatus("Datei wird hochgeladen …");
    const { response, data } = await requestJson(`/notes/api/${note.id}/attachments/`, { method: "POST", body: form });
    if (!response.ok || !data.ok) {
      setSaveStatus("Upload fehlgeschlagen");
      window.alert(data.error || "Die Datei konnte nicht hochgeladen werden.");
      return;
    }
    const item = data.attachment;
    if (kind === "image") {
      const alt = window.prompt("Alternativtext für das Bild", file.name) ?? file.name;
      editor.chain().focus().insertContent({ type: "noteImage", attrs: { attachmentId: item.id, alt, title: file.name, width: 720 } }).run();
    } else {
      editor.chain().focus().insertContent({ type: "noteAttachment", attrs: { attachmentId: item.id, name: item.name, size: item.size } }).run();
    }
  }

  async function openShareDialog() {
    if (!shareDialog || !note?.can_manage) return;
    renderShares(note.shares || []);
    shareDialog.showModal();
    document.querySelector("[data-share-search]")?.focus();
  }

  function renderShares(shares) {
    const target = document.querySelector("[data-share-list]");
    if (!target) return;
    target.innerHTML = shares.length ? "" : '<p class="dialog-hint">Noch mit niemandem geteilt.</p>';
    shares.forEach((share) => {
      const row = document.createElement("div");
      row.className = "share-row";
      row.innerHTML = `<span><strong></strong><small></small></span><select aria-label="Rolle"><option value="reader">Leser</option><option value="editor">Bearbeiter</option></select><button type="button" aria-label="Freigabe entfernen"><i class="fa-solid fa-xmark"></i></button>`;
      row.querySelector("strong").textContent = share.name;
      row.querySelector("small").textContent = share.email;
      const select = row.querySelector("select");
      select.value = share.role;
      select.addEventListener("change", () => updateShare(share.user_id, select.value));
      row.querySelector("button").addEventListener("click", () => deleteShare(share.user_id));
      target.append(row);
    });
  }

  let shareSearchTimer = null;
  document.querySelector("[data-share-search]")?.addEventListener("input", (event) => {
    window.clearTimeout(shareSearchTimer);
    shareSearchTimer = window.setTimeout(() => searchShareCandidates(event.target.value), 250);
  });

  async function searchShareCandidates(query) {
    const target = document.querySelector("[data-share-candidates]");
    if (!target) return;
    if (query.trim().length < 2) {
      target.innerHTML = "";
      return;
    }
    const { data } = await requestJson(`/notes/api/share-candidates/?q=${encodeURIComponent(query)}`);
    target.innerHTML = "";
    (data.users || []).forEach((user) => {
      const row = document.createElement("div");
      row.className = "share-row";
      row.innerHTML = `<span><strong></strong><small></small></span><select><option value="reader">Leser</option><option value="editor">Bearbeiter</option></select><button class="primary-button" type="button">Teilen</button>`;
      row.querySelector("strong").textContent = user.name;
      row.querySelector("small").textContent = user.email;
      row.querySelector("button").addEventListener("click", () => updateShare(user.id, row.querySelector("select").value));
      target.append(row);
    });
  }

  async function updateShare(userId, role) {
    const { response, data } = await requestJson(`/notes/api/${note.id}/shares/`, { method: "POST", body: JSON.stringify({ user_id: userId, role }) });
    if (!response.ok) return window.alert(data.error || "Freigabe fehlgeschlagen.");
    note.shares = data.shares;
    renderShares(note.shares);
    document.querySelector("[data-share-candidates]").innerHTML = "";
  }

  async function deleteShare(userId) {
    const { response, data } = await requestJson(`/notes/api/${note.id}/shares/${userId}/`, { method: "DELETE" });
    if (!response.ok) return window.alert(data.error || "Freigabe konnte nicht entfernt werden.");
    note.shares = note.shares.filter((share) => share.user_id !== userId);
    renderShares(note.shares);
  }

  async function addCommentToSelection() {
    if (!editor || !note?.can_edit || note?.is_deleted) return;
    const { from, to, empty } = editor.state.selection;
    if (empty) {
      window.alert("Markiere zuerst einen Textabschnitt, um ihn zu kommentieren.");
      return;
    }
    const body = window.prompt("Kommentar zu dieser Textstelle:");
    if (!body || !body.trim()) return;
    const anchorText = editor.state.doc.textBetween(from, to, " ");
    const threadId = window.crypto.randomUUID();
    const { response, data } = await requestJson(`/notes/api/${note.id}/comments/`, {
      method: "POST",
      body: JSON.stringify({ thread_id: threadId, anchor_text: anchorText, body: body.trim() }),
    });
    if (!response.ok || !data.ok) {
      window.alert(data.error || "Kommentar konnte nicht gespeichert werden.");
      return;
    }
    editor.chain().setTextSelection({ from, to }).setMark("commentThread", { threadId }).run();
  }

  async function openCommentsDialog() {
    if (!commentsDialog || !note) return;
    const target = document.querySelector("[data-comment-thread-list]");
    target.innerHTML = '<p class="dialog-hint">Kommentare werden geladen …</p>';
    commentsDialog.showModal();
    const { response, data } = await requestJson(`/notes/api/${note.id}/comments/`);
    if (!response.ok) {
      target.textContent = data.error || "Kommentare konnten nicht geladen werden.";
      return;
    }
    renderCommentThreads(data.threads || []);
  }

  function renderCommentThreads(threads) {
    const target = document.querySelector("[data-comment-thread-list]");
    if (!target) return;
    target.innerHTML = threads.length ? "" : '<p class="dialog-hint">Noch keine Kommentare vorhanden.</p>';
    threads.forEach((thread) => {
      const row = document.createElement("div");
      row.className = `comment-thread-row${thread.is_resolved ? " is-resolved" : ""}`;
      row.innerHTML = [
        '<button type="button" class="comment-thread-anchor"></button>',
        '<div class="comment-list"></div>',
        note?.can_edit && !note?.is_deleted
          ? '<form class="comment-reply-form"><input type="text" placeholder="Antworten ..." aria-label="Antworten"></form>'
          : "",
        '<div class="comment-thread-actions">',
        '<button type="button" class="ghost-button" data-thread-toggle-resolve></button>',
        '<button type="button" class="is-danger" data-thread-delete aria-label="Kommentar löschen"><i class="fa-solid fa-trash"></i></button>',
        "</div>",
      ].join("");

      const anchorButton = row.querySelector(".comment-thread-anchor");
      anchorButton.textContent = thread.anchor_text ? `„${thread.anchor_text}“` : "Textstelle ansehen";
      anchorButton.addEventListener("click", () => jumpToCommentThread(thread.thread_id));

      const list = row.querySelector(".comment-list");
      thread.comments.forEach((comment) => {
        const item = document.createElement("div");
        item.className = "comment-item";
        item.innerHTML = "<strong></strong><p></p><small></small>";
        item.querySelector("strong").textContent = comment.author;
        item.querySelector("p").textContent = comment.body;
        item.querySelector("small").textContent = new Date(comment.created_at).toLocaleString();
        list.append(item);
      });

      const resolveButton = row.querySelector("[data-thread-toggle-resolve]");
      resolveButton.textContent = thread.is_resolved ? "Wieder öffnen" : "Auflösen";
      resolveButton.addEventListener("click", () => toggleThreadResolved(thread.thread_id, !thread.is_resolved));

      row.querySelector("[data-thread-delete]").addEventListener("click", () => deleteCommentThread(thread.thread_id));

      const replyForm = row.querySelector(".comment-reply-form");
      replyForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        const input = replyForm.querySelector("input");
        const body = input.value.trim();
        if (!body) return;
        input.value = "";
        replyToCommentThread(thread.thread_id, body);
      });

      target.append(row);
    });
  }

  async function replyToCommentThread(threadId, body) {
    const { response, data } = await requestJson(`/notes/api/${note.id}/comments/${threadId}/`, {
      method: "POST",
      body: JSON.stringify({ action: "reply", body }),
    });
    if (!response.ok) return window.alert(data.error || "Antwort konnte nicht gespeichert werden.");
    renderCommentThreads(data.threads || []);
  }

  async function toggleThreadResolved(threadId, resolved) {
    const { response, data } = await requestJson(`/notes/api/${note.id}/comments/${threadId}/`, {
      method: "POST",
      body: JSON.stringify({ action: resolved ? "resolve" : "reopen" }),
    });
    if (!response.ok) return window.alert(data.error || "Aktion fehlgeschlagen.");
    renderCommentThreads(data.threads || []);
  }

  async function deleteCommentThread(threadId) {
    if (!window.confirm("Diesen Kommentar-Thread wirklich löschen?")) return;
    const { response, data } = await requestJson(`/notes/api/${note.id}/comments/${threadId}/`, { method: "DELETE" });
    if (!response.ok) return window.alert(data.error || "Löschen fehlgeschlagen.");
    const { data: listData } = await requestJson(`/notes/api/${note.id}/comments/`);
    renderCommentThreads(listData.threads || []);
  }

  function jumpToCommentThread(threadId) {
    const element = document.querySelector(`[data-note-editor] [data-thread-id="${threadId}"]`);
    if (!element) {
      window.alert("Diese Textstelle ist in der aktuellen Ansicht nicht sichtbar.");
      return;
    }
    commentsDialog?.close();
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    element.classList.add("is-highlighted");
    window.setTimeout(() => element.classList.remove("is-highlighted"), 1400);
  }

  async function openVersionsDialog() {
    if (!versionsDialog || !note) return;
    const target = document.querySelector("[data-version-list]");
    target.innerHTML = '<p class="dialog-hint">Versionen werden geladen …</p>';
    versionsDialog.showModal();
    const { response, data } = await requestJson(`/notes/api/${note.id}/versions/`);
    if (!response.ok) {
      target.textContent = data.error || "Versionen konnten nicht geladen werden.";
      return;
    }
    target.innerHTML = data.versions.length ? "" : '<p class="dialog-hint">Noch keine älteren Versionen vorhanden.</p>';
    data.versions.forEach((version) => {
      const row = document.createElement("div");
      row.className = "version-row";
      row.innerHTML = `<span><strong></strong><small></small></span>${note.can_edit ? '<button class="ghost-button" type="button">Wiederherstellen</button>' : ''}`;
      row.querySelector("strong").textContent = version.title;
      row.querySelector("small").textContent = `${new Date(version.created_at).toLocaleString()} · ${version.created_by} · Revision ${version.revision}`;
      row.querySelector("button")?.addEventListener("click", () => restoreVersion(version.id));
      target.append(row);
    });
  }

  async function restoreVersion(versionId) {
    if (!window.confirm("Diese Version als neuen Stand wiederherstellen?")) return;
    const { response, data } = await requestJson(`/notes/api/${note.id}/versions/${versionId}/restore/`, {
      method: "POST", body: JSON.stringify({ base_revision: note.revision }),
    });
    if (response.status === 409) return window.alert("Die Notiz wurde inzwischen geändert. Lade sie neu und versuche es erneut.");
    if (!response.ok) return window.alert(data.error || "Version konnte nicht wiederhergestellt werden.");
    window.location.reload();
  }

  function openShortcutDialog() {
    if (!shortcutDialog) return;
    renderShortcutList(commands);
    shortcutDialog.showModal();
  }

  function renderShortcutList(values) {
    const target = document.querySelector("[data-shortcut-list]");
    target.innerHTML = "";
    Object.entries(values).forEach(([action, config]) => {
      const row = document.createElement("div");
      row.className = "shortcut-row";
      row.innerHTML = '<label></label><input type="text" readonly data-shortcut-capture><button type="button" aria-label="Belegung entfernen"><i class="fa-solid fa-eraser"></i></button>';
      row.querySelector("label").textContent = config.label;
      const input = row.querySelector("input");
      input.value = config.shortcut;
      input.dataset.action = action;
      input.addEventListener("keydown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const shortcut = eventToShortcut(event);
        if (!shortcut) return;
        const draftCommands = shortcutDraftFromDialog();
        const conflict = findShortcutConflict(draftCommands, action, shortcut);
        if (conflict) {
          window.alert(`Diese Kombination ist bereits für „${commands[conflict].label}“ vergeben.`);
          return;
        }
        input.value = shortcut;
      });
      row.querySelector("button").addEventListener("click", () => { input.value = ""; });
      target.append(row);
    });
  }

  function shortcutDraftFromDialog() {
    const values = structuredClone(commands);
    document.querySelectorAll("[data-shortcut-capture]").forEach((input) => {
      values[input.dataset.action].shortcut = input.value;
    });
    return values;
  }

  document.querySelector("[data-shortcuts-reset]")?.addEventListener("click", () => renderShortcutList(mergeShortcuts(COMMAND_DEFAULTS, {})));
  document.querySelector("[data-shortcuts-save]")?.addEventListener("click", async () => {
    const values = shortcutDraftFromDialog();
    const overrides = Object.fromEntries(Object.entries(values).map(([action, config]) => [action, config.shortcut]));
    const { response, data } = await requestJson("/notes/api/shortcuts/", { method: "PATCH", body: JSON.stringify({ shortcuts: overrides }) });
    if (!response.ok) return window.alert(data.error || "Hotkeys konnten nicht gespeichert werden.");
    commands = mergeShortcuts(COMMAND_DEFAULTS, data.shortcuts);
    updateShortcutTooltips();
    shortcutDialog.close();
  });

  function updateShortcutTooltips() {
    Object.entries(commands).forEach(([action, config]) => {
      document.querySelectorAll(`[data-command="${action}"]`).forEach((element) => {
        element.title = config.shortcut ? `${config.label} (${config.shortcut})` : config.label;
      });
      const formatControl = document.querySelector(`[data-format="${action}"]`);
      if (formatControl) formatControl.title = config.shortcut ? `${config.label} (${config.shortcut})` : config.label;
    });
  }

  document.querySelector("[data-conflict-load]")?.addEventListener("click", () => {
    applying = true;
    note = conflictServerNote;
    titleInput.value = note.title;
    editor.commands.setContent(note.document);
    applying = false;
    dirty = false;
    localStorage.removeItem(draftKey());
    conflictServerNote = null;
    conflictDraft = null;
    setSaveStatus("Serverstand geladen");
    conflictDialog.close();
  });

  document.querySelector("[data-conflict-overwrite]")?.addEventListener("click", async () => {
    conflictDialog.close();
    dirty = true;
    await saveNow({ conflictResolution: true });
  });

  document.querySelector("[data-conflict-copy]")?.addEventListener("click", async () => {
    const draft = conflictDraft || currentPayload();
    const { response, data } = await requestJson(`/notes/api/${note.id}/actions/`, {
      method: "POST",
      body: JSON.stringify({ action: "duplicate", title: `${draft.title} (Konfliktkopie)`, document: draft.document }),
    });
    if (!response.ok) return window.alert(data.error || "Kopie konnte nicht erstellt werden.");
    localStorage.removeItem(draftKey());
    window.location.assign(`/notes/${data.note.id}/`);
  });
}

if (app) {
  initNotesApp();
}
