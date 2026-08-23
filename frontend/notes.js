import { Editor, Mark, Node, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight";
import { Highlight } from "@tiptap/extension-highlight";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { TableKit } from "@tiptap/extension-table";
import { TextAlign } from "@tiptap/extension-text-align";
import { TextStyleKit } from "@tiptap/extension-text-style";
import { Underline } from "@tiptap/extension-underline";
import { CharacterCount } from "@tiptap/extensions";
import Suggestion from "@tiptap/suggestion";
import { common, createLowlight } from "lowlight";

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
    return [Suggestion({ editor: this.editor, ...this.options.suggestion })];
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

function createMentionSuggestionRenderer() {
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
      option.className = `mention-suggestion-option${index === selectedIndex ? " is-active" : ""}`;
      option.textContent = item.name;
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
      popup.className = "mention-suggestion-popup";
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

const COMMAND_DEFAULTS = {
  save: { label: "Speichern", shortcut: "Mod+S" },
  undo: { label: "Rückgängig", shortcut: "Mod+Z" },
  redo: { label: "Wiederholen", shortcut: "Mod+Shift+Z" },
  bold: { label: "Fett", shortcut: "Mod+B" },
  italic: { label: "Kursiv", shortcut: "Mod+I" },
  underline: { label: "Unterstrichen", shortcut: "Mod+U" },
  strike: { label: "Durchgestrichen", shortcut: "Mod+Shift+X" },
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
  duplicate: { label: "Duplizieren", shortcut: "Mod+Alt+D" },
  trash: { label: "In Papierkorb", shortcut: "Mod+Alt+Backspace" },
  share: { label: "Teilen", shortcut: "Mod+Alt+H" },
  versions: { label: "Versionsverlauf", shortcut: "Mod+Alt+V" },
  exportPdf: { label: "Als PDF exportieren", shortcut: "Mod+Alt+E" },
  shortcutSettings: { label: "Hotkey-Einstellungen", shortcut: "Mod+Alt+K" },
};

function initNotesApp() {
  let note = readJsonScript("note-initial-data", null);
  const savedOverrides = readJsonScript("note-shortcut-overrides", {});
  let commands = mergeShortcuts(COMMAND_DEFAULTS, savedOverrides);
  let editor = null;
  let dirty = false;
  let saving = false;
  let applying = false;
  let debounceTimer = null;
  let maxWaitTimer = null;
  let retryTimer = null;
  let conflictServerNote = null;
  let conflictDraft = null;

  const titleInput = document.querySelector("[data-note-title]");
  const tagsInput = document.querySelector("[data-note-tags]");
  const saveStatus = document.querySelector("[data-save-status]");
  const wordCount = document.querySelector("[data-word-count]");
  const shareDialog = document.querySelector("[data-share-dialog]");
  const versionsDialog = document.querySelector("[data-versions-dialog]");
  const shortcutDialog = document.querySelector("[data-shortcut-dialog]");
  const conflictDialog = document.querySelector("[data-conflict-dialog]");
  const tableDialog = document.querySelector("[data-table-dialog]");
  const commentsDialog = document.querySelector("[data-comments-dialog]");

  if (note && document.querySelector("[data-note-editor]")) {
    editor = new Editor({
      element: document.querySelector("[data-note-editor]"),
      editable: Boolean(note.can_edit && !note.is_deleted),
      content: note.document,
      extensions: [
        StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: { openOnClick: !note.can_edit }, codeBlock: false }),
        CodeBlockLowlight.configure({ lowlight }),
        Underline,
        Highlight.configure({ multicolor: true }),
        TextStyleKit,
        TextAlign.configure({ types: ["heading", "paragraph"] }),
        TableKit.configure({ table: { resizable: true } }),
        TaskList,
        TaskItem.configure({ nested: true }),
        CharacterCount,
        NoteImage,
        NoteAttachmentNode,
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
      ],
      onUpdate: () => {
        updateCounts();
        updateToolbarState();
        markDirty();
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

  titleInput?.addEventListener("input", markDirty);
  tagsInput?.addEventListener("input", markDirty);
  document.querySelector("[data-table-dialog-open]")?.addEventListener("click", () => tableDialog?.showModal());

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-command]");
    if (!button) return;
    event.preventDefault();
    if (button.closest("[data-table-dialog]")) tableDialog.close();
    runCommand(button.dataset.command);
  });

  document.querySelectorAll("[data-format]").forEach((control) => {
    control.addEventListener("change", () => applyFormatControl(control));
  });
  document.querySelector("[data-image-input]")?.addEventListener("change", (event) => uploadSelectedFile(event.target, "image"));
  document.querySelector("[data-file-input]")?.addEventListener("change", (event) => uploadSelectedFile(event.target, "file"));
  document.querySelector("[data-trash-restore]")?.addEventListener("click", () => performAction("restore"));

  document.addEventListener("keydown", (event) => {
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
      save: () => saveNow(),
      undo: () => editor?.chain().focus().undo().run(),
      redo: () => editor?.chain().focus().redo().run(),
      bold: () => editor?.chain().focus().toggleBold().run(),
      italic: () => editor?.chain().focus().toggleItalic().run(),
      underline: () => editor?.chain().focus().toggleUnderline().run(),
      strike: () => editor?.chain().focus().toggleStrike().run(),
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
      newNote: createNewNote,
      focusSearch: () => document.querySelector("[data-notes-search]")?.focus(),
      pin: () => performAction(note?.is_pinned ? "unpin" : "pin"),
      archive: () => performAction(note?.is_archived ? "unarchive" : "archive"),
      duplicate: () => performAction("duplicate"),
      trash: trashCurrentNote,
      share: openShareDialog,
      comments: openCommentsDialog,
      versions: openVersionsDialog,
      exportPdf: exportPdf,
      shortcutSettings: openShortcutDialog,
    };
    pageActions[action]?.();
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
    control?.focus();
    control?.click();
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

  function updateToolbarState() {
    if (!editor) return;
    const inCodeBlock = editor.isActive("codeBlock");
    const active = {
      bold: editor.isActive("bold"), italic: editor.isActive("italic"), underline: editor.isActive("underline"),
      strike: editor.isActive("strike"), bulletList: editor.isActive("bulletList"), orderedList: editor.isActive("orderedList"),
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
  }

  function updateCounts() {
    if (!editor || !wordCount) return;
    const words = editor.storage.characterCount.words();
    const characters = editor.storage.characterCount.characters();
    wordCount.textContent = `${words} ${words === 1 ? "Wort" : "Wörter"} · ${characters} Zeichen`;
  }

  function currentPayload() {
    return {
      title: titleInput?.value || "Unbenannte Notiz",
      document: editor?.getJSON(),
      tags: (tagsInput?.value || "").split(/[\s,]+/).map((tag) => tag.replace(/^#/, "").trim()).filter(Boolean),
      base_revision: note?.revision || 0,
    };
  }

  function markDirty() {
    if (applying || !note?.can_edit || note?.is_deleted) return;
    dirty = true;
    setSaveStatus("Ungespeichert");
    persistLocalDraft();
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => saveNow(), 1000);
    if (!maxWaitTimer) maxWaitTimer = window.setTimeout(() => saveNow(), 5000);
  }

  async function saveNow({ conflictResolution = false } = {}) {
    if (!note || !editor || saving || (conflictServerNote && !conflictResolution)) return false;
    if (!dirty && !conflictResolution) return true;
    saving = true;
    window.clearTimeout(debounceTimer);
    window.clearTimeout(maxWaitTimer);
    debounceTimer = null;
    maxWaitTimer = null;
    setSaveStatus("Speichern …");
    const payload = { ...currentPayload(), conflict_resolution: conflictResolution };
    if (conflictResolution && conflictServerNote) payload.base_revision = conflictServerNote.revision;
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
      dirty = false;
      conflictServerNote = null;
      conflictDraft = null;
      localStorage.removeItem(draftKey());
      updateNoteCard(note);
      setSaveStatus(`Gespeichert ${new Date(note.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      return true;
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
  }

  async function exportPdf() {
    while (saving) await new Promise((resolve) => window.setTimeout(resolve, 50));
    if (dirty && !await saveNow()) {
      setSaveStatus("PDF-Export erst nach erfolgreichem Speichern möglich");
      return;
    }
    window.location.assign(`/notes/${note.id}/export/pdf/`);
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
    const tags = card.querySelector("[data-note-card-tags]");

    if (title) title.textContent = savedNote.title || "Unbenannte Notiz";
    if (preview) preview.textContent = savedNote.preview?.trim() || "Noch kein Inhalt";
    if (updated) {
      updated.dateTime = savedNote.updated_at;
      updated.textContent = savedNote.updated_at?.slice(0, 10) || "";
    }
    if (tags) {
      tags.replaceChildren();
      savedNote.tags.slice(0, 3).forEach((tag) => {
        const badge = document.createElement("small");
        badge.textContent = `#${tag}`;
        tags.append(badge);
      });
      tags.hidden = savedNote.tags.length === 0;
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
      tagsInput.value = draft.tags.join(", ");
      editor.commands.setContent(draft.document);
      applying = false;
      dirty = true;
      setSaveStatus("Lokaler Entwurf – ungespeichert");
      markDirty();
    } catch (_error) {
      localStorage.removeItem(draftKey());
    }
  }

  async function createNewNote() {
    const { response, data } = await requestJson("/notes/api/create/", { method: "POST", body: JSON.stringify({}) });
    if (response.ok && data.note) window.location.assign(`/notes/${data.note.id}/`);
    else window.alert(data.error || "Die Notiz konnte nicht erstellt werden.");
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
    tagsInput.value = note.tags.join(", ");
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
      body: JSON.stringify({ action: "duplicate", title: `${draft.title} (Konfliktkopie)`, document: draft.document, tags: draft.tags }),
    });
    if (!response.ok) return window.alert(data.error || "Kopie konnte nicht erstellt werden.");
    localStorage.removeItem(draftKey());
    window.location.assign(`/notes/${data.note.id}/`);
  });
}

if (app) {
  initNotesApp();
}
