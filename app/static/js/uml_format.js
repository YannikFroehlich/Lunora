// Pure helpers for the UML class diagram editor: member-line parsing, document normalization and
// text exports (PlantUML, Java). Kept DOM-free so frontend/uml_format.test.js can run them in Node.
(function () {
  const CLASS_KINDS = ["class", "abstract", "interface", "enum"];
  const RELATION_KINDS = [
    "association",
    "directed_association",
    "aggregation",
    "composition",
    "inheritance",
    "realization",
    "dependency",
    "note_link",
  ];
  const COLORS = ["default", "blue", "green", "yellow", "red", "violet", "gray"];
  const VISIBILITIES = ["", "+", "-", "#", "~"];
  // Mirrors the server-side limits in app/services/uml_content.py so a save never fails on length.
  const LIMITS = {
    name: 120,
    type: 120,
    default: 200,
    params: 400,
    stereotype: 60,
    label: 120,
    multiplicity: 20,
    note: 2000,
    members: 80,
  };
  const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

  function newId(prefix) {
    const random = Math.random().toString(36).slice(2, 10);
    return `${prefix}_${Date.now().toString(36)}${random}`.slice(0, 40);
  }

  function text(value, limit) {
    const result = typeof value === "string" ? value : value == null ? "" : String(value);
    return result.slice(0, limit);
  }

  function coordinate(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.round(Math.max(-100000, Math.min(100000, number)) * 10) / 10;
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function pick(value, allowed, fallback) {
    return allowed.includes(value) ? value : fallback;
  }

  function normalizeAttribute(item) {
    return {
      visibility: pick(item?.visibility, VISIBILITIES, ""),
      name: text(item?.name, LIMITS.name),
      type: text(item?.type, LIMITS.type),
      default: text(item?.default, LIMITS.default),
      is_static: item?.is_static === true,
    };
  }

  function normalizeMethod(item) {
    return {
      visibility: pick(item?.visibility, VISIBILITIES, ""),
      name: text(item?.name, LIMITS.name),
      params: text(item?.params, LIMITS.params),
      return_type: text(item?.return_type, LIMITS.type),
      is_static: item?.is_static === true,
      is_abstract: item?.is_abstract === true,
    };
  }

  // Coerces imported or stored data into the exact shape the server validator accepts.
  function normalizeDocument(input) {
    const source = input && typeof input === "object" ? input : {};
    const usedIds = new Set();
    const uniqueId = (value, prefix) => {
      let id = typeof value === "string" && ID_PATTERN.test(value) ? value : newId(prefix);
      while (usedIds.has(id)) id = newId(prefix);
      usedIds.add(id);
      return id;
    };

    const classes = list(source.classes).map((item) => ({
      id: uniqueId(item?.id, "c"),
      kind: pick(item?.kind, CLASS_KINDS, "class"),
      name: text(item?.name, LIMITS.name) || "Unbenannt",
      stereotype: text(item?.stereotype, LIMITS.stereotype),
      x: coordinate(item?.x),
      y: coordinate(item?.y),
      color: pick(item?.color, COLORS, "default"),
      attributes: list(item?.attributes)
        .map(normalizeAttribute)
        .filter((entry) => entry.name.trim())
        .slice(0, LIMITS.members),
      methods: list(item?.methods)
        .map(normalizeMethod)
        .filter((entry) => entry.name.trim())
        .slice(0, LIMITS.members),
      literals: list(item?.literals)
        .map((entry) => text(entry, LIMITS.name))
        .filter((entry) => entry.trim())
        .slice(0, LIMITS.members),
    }));
    const notes = list(source.notes).map((item) => ({
      id: uniqueId(item?.id, "n"),
      text: text(item?.text, LIMITS.note),
      x: coordinate(item?.x),
      y: coordinate(item?.y),
    }));

    const kindById = new Map();
    classes.forEach((item) => kindById.set(item.id, "class"));
    notes.forEach((item) => kindById.set(item.id, "note"));
    const relationIds = new Set();
    const relations = [];
    list(source.relations).forEach((item) => {
      const kind = pick(item?.kind, RELATION_KINDS, "association");
      const sourceId = item?.source;
      const targetId = item?.target;
      if (!relationAllowed(kind, kindById.get(sourceId), kindById.get(targetId), sourceId === targetId)) return;
      let id = typeof item?.id === "string" && ID_PATTERN.test(item.id) ? item.id : newId("r");
      while (relationIds.has(id)) id = newId("r");
      relationIds.add(id);
      relations.push({
        id,
        kind,
        source: sourceId,
        target: targetId,
        label: text(item?.label, LIMITS.label),
        source_multiplicity: text(item?.source_multiplicity, LIMITS.multiplicity),
        target_multiplicity: text(item?.target_multiplicity, LIMITS.multiplicity),
      });
    });

    return { version: 1, classes, relations, notes };
  }

  // Same rules as _clean_relation in app/services/uml_content.py.
  function relationAllowed(kind, sourceType, targetType, isSelf) {
    if (!sourceType || !targetType) return false;
    if (kind === "note_link") return !isSelf && (sourceType === "note" || targetType === "note");
    if (sourceType !== "class" || targetType !== "class") return false;
    return !(isSelf && (kind === "inheritance" || kind === "realization"));
  }

  function parseModifiers(line) {
    let rest = line.trim();
    let visibility = "";
    if (/^[+\-#~]/.test(rest)) {
      visibility = rest[0];
      rest = rest.slice(1).trim();
    }
    let isStatic = false;
    let isAbstract = false;
    for (;;) {
      const match = rest.match(/^(\{static\}|static\b|\{abstract\}|abstract\b)\s*/i);
      if (!match) break;
      if (match[1].toLowerCase().includes("static")) isStatic = true;
      else isAbstract = true;
      rest = rest.slice(match[0].length);
    }
    return { visibility, isStatic, isAbstract, rest };
  }

  // "- {static} name: Type = value" → attribute object, or null for a blank line.
  function parseAttribute(line) {
    const { visibility, isStatic, rest } = parseModifiers(line);
    let head = rest;
    let defaultValue = "";
    const equalsIndex = rest.indexOf("=");
    if (equalsIndex >= 0) {
      head = rest.slice(0, equalsIndex);
      defaultValue = rest.slice(equalsIndex + 1).trim();
    }
    let name = head;
    let type = "";
    const colonIndex = head.indexOf(":");
    if (colonIndex >= 0) {
      name = head.slice(0, colonIndex);
      type = head.slice(colonIndex + 1).trim();
    }
    name = name.trim();
    if (!name) return null;
    return normalizeAttribute({ visibility, name, type, default: defaultValue, is_static: isStatic });
  }

  // "+ {abstract} name(a: int): Type" → method object, or null for a blank line.
  function parseMethod(line) {
    const { visibility, isStatic, isAbstract, rest } = parseModifiers(line);
    let name = rest;
    let params = "";
    let returnType = "";
    const openIndex = rest.indexOf("(");
    const closeIndex = rest.lastIndexOf(")");
    if (openIndex >= 0 && closeIndex > openIndex) {
      name = rest.slice(0, openIndex);
      params = rest.slice(openIndex + 1, closeIndex).trim();
      returnType = rest
        .slice(closeIndex + 1)
        .trim()
        .replace(/^:\s*/, "");
    } else if (openIndex >= 0) {
      name = rest.slice(0, openIndex);
      params = rest.slice(openIndex + 1).trim();
    }
    name = name.trim();
    if (!name) return null;
    return normalizeMethod({
      visibility,
      name,
      params,
      return_type: returnType,
      is_static: isStatic,
      is_abstract: isAbstract,
    });
  }

  function parseLines(value, parser) {
    return value
      .split("\n")
      .map((line) => (line.trim() ? parser(line) : null))
      .filter(Boolean)
      .slice(0, LIMITS.members);
  }

  function formatAttribute(item) {
    const visibility = item.visibility ? `${item.visibility} ` : "";
    const modifier = item.is_static ? "{static} " : "";
    const type = item.type ? `: ${item.type}` : "";
    const defaultValue = item.default ? ` = ${item.default}` : "";
    return `${visibility}${modifier}${item.name}${type}${defaultValue}`;
  }

  function formatMethod(item) {
    const visibility = item.visibility ? `${item.visibility} ` : "";
    const modifiers = `${item.is_static ? "{static} " : ""}${item.is_abstract ? "{abstract} " : ""}`;
    const returnType = item.return_type ? `: ${item.return_type}` : "";
    return `${visibility}${modifiers}${item.name}(${item.params})${returnType}`;
  }

  // ---- PlantUML -------------------------------------------------------------------------------

  const PLANTUML_ARROWS = {
    association: "--",
    directed_association: "-->",
    aggregation: "--o",
    composition: "--*",
    inheritance: "--|>",
    realization: "..|>",
    dependency: "..>",
    note_link: "..",
  };

  function plantAlias(id) {
    return id.replace(/[^A-Za-z0-9_]/g, "_");
  }

  function plantQuote(value) {
    return `"${value.replace(/"/g, "'").replace(/\n/g, " ")}"`;
  }

  function toPlantUml(doc, title) {
    const lines = ["@startuml", "skinparam classAttributeIconSize 0"];
    if (title) lines.push(`title ${title.replace(/\n/g, " ")}`);
    lines.push("");
    const keyword = { class: "class", abstract: "abstract class", interface: "interface", enum: "enum" };
    doc.classes.forEach((item) => {
      const stereotype = item.stereotype ? ` <<${item.stereotype}>>` : "";
      lines.push(`${keyword[item.kind]} ${plantQuote(item.name)} as ${plantAlias(item.id)}${stereotype} {`);
      item.literals.forEach((literal) => lines.push(`  ${literal}`));
      if (item.literals.length && (item.attributes.length || item.methods.length)) lines.push("  --");
      item.attributes.forEach((attribute) => {
        const modifier = attribute.is_static ? "{static} " : "";
        const type = attribute.type ? ` : ${attribute.type}` : "";
        const defaultValue = attribute.default ? ` = ${attribute.default}` : "";
        lines.push(`  ${modifier}${attribute.visibility}${attribute.name}${type}${defaultValue}`);
      });
      item.methods.forEach((method) => {
        const modifiers = `${method.is_static ? "{static} " : ""}${method.is_abstract ? "{abstract} " : ""}`;
        const returnType = method.return_type ? ` : ${method.return_type}` : "";
        lines.push(`  ${modifiers}${method.visibility}${method.name}(${method.params})${returnType}`);
      });
      lines.push("}");
    });
    doc.notes.forEach((note) => {
      lines.push(`note as ${plantAlias(note.id)}`);
      (note.text || " ").split("\n").forEach((line) => lines.push(`  ${line}`));
      lines.push("end note");
    });
    if (doc.relations.length) lines.push("");
    doc.relations.forEach((relation) => {
      const sourceMultiplicity = relation.source_multiplicity ? ` ${plantQuote(relation.source_multiplicity)}` : "";
      const targetMultiplicity = relation.target_multiplicity ? `${plantQuote(relation.target_multiplicity)} ` : "";
      const label = relation.label ? ` : ${relation.label.replace(/\n/g, " ")}` : "";
      lines.push(
        `${plantAlias(relation.source)}${sourceMultiplicity} ${PLANTUML_ARROWS[relation.kind]} ` +
          `${targetMultiplicity}${plantAlias(relation.target)}${label}`,
      );
    });
    lines.push("@enduml");
    return `${lines.join("\n")}\n`;
  }

  // ---- Java -----------------------------------------------------------------------------------

  const JAVA_VISIBILITY = { "+": "public ", "-": "private ", "#": "protected ", "~": "", "": "" };
  const PRIMITIVE_DEFAULTS = {
    int: "0",
    long: "0L",
    short: "0",
    byte: "0",
    double: "0.0",
    float: "0.0f",
    char: "' '",
    boolean: "false",
  };

  function javaIdentifier(value) {
    return value.trim().replace(/\s+/g, "");
  }

  function lowerFirst(value) {
    return value ? value[0].toLowerCase() + value.slice(1) : value;
  }

  // Splits on commas outside generic brackets, so "m: Map<String, Integer>, n: int" stays two params.
  function splitParams(params) {
    const parts = [];
    let depth = 0;
    let current = "";
    for (const character of params) {
      if (character === "<") depth += 1;
      if (character === ">") depth = Math.max(0, depth - 1);
      if (character === "," && depth === 0) {
        parts.push(current);
        current = "";
      } else {
        current += character;
      }
    }
    parts.push(current);
    return parts.map((part) => part.trim()).filter(Boolean);
  }

  function javaParams(params) {
    return splitParams(params)
      .map((param) => {
        const colonIndex = param.indexOf(":");
        if (colonIndex < 0) return param;
        return `${param.slice(colonIndex + 1).trim() || "Object"} ${param.slice(0, colonIndex).trim()}`;
      })
      .join(", ");
  }

  function isMany(multiplicity) {
    const value = multiplicity.trim();
    if (!value) return false;
    if (value.includes("*")) return true;
    const upper = value.split("..").pop();
    return Number(upper) > 1;
  }

  function toJava(doc) {
    const byId = new Map(doc.classes.map((item) => [item.id, item]));
    const extra = new Map(doc.classes.map((item) => [item.id, { extends: [], implements: [], fields: [] }]));

    const addField = (owner, other, multiplicity, label) => {
      const many = isMany(multiplicity);
      const otherName = javaIdentifier(other.name);
      const name = javaIdentifier(label) || lowerFirst(otherName) + (many ? "Liste" : "");
      const known = owner.attributes.some((attribute) => attribute.name.trim() === name);
      const info = extra.get(owner.id);
      if (known || info.fields.some((field) => field.name === name)) return;
      info.fields.push({ name, type: many ? `List<${otherName}>` : otherName });
    };

    doc.relations.forEach((relation) => {
      const source = byId.get(relation.source);
      const target = byId.get(relation.target);
      if (!source || !target) return;
      const info = extra.get(source.id);
      const targetName = javaIdentifier(target.name);
      if (relation.kind === "inheritance") {
        if (target.kind === "interface" && source.kind !== "interface") info.implements.push(targetName);
        else info.extends.push(targetName);
      } else if (relation.kind === "realization") {
        info.implements.push(targetName);
      } else if (relation.kind === "directed_association") {
        addField(source, target, relation.target_multiplicity, relation.label);
      } else if (relation.kind === "aggregation" || relation.kind === "composition") {
        // The diamond sits at the target, so the target is the whole that holds its parts.
        addField(target, source, relation.source_multiplicity, relation.label);
      }
    });

    return doc.classes.map((item) => javaClass(item, extra.get(item.id))).join("\n");
  }

  function javaClass(item, info) {
    const name = javaIdentifier(item.name) || "Unbenannt";
    const isInterface = item.kind === "interface";
    const keyword = { class: "class", abstract: "abstract class", interface: "interface", enum: "enum" }[item.kind];
    let signature = `public ${keyword} ${name}`;
    if (item.kind === "enum") {
      if (info.implements.length || info.extends.length) {
        signature += ` implements ${[...info.implements, ...info.extends].join(", ")}`;
      }
    } else if (isInterface) {
      const parents = [...info.extends, ...info.implements];
      if (parents.length) signature += ` extends ${parents.join(", ")}`;
    } else {
      if (info.extends.length) signature += ` extends ${info.extends[0]}`;
      if (info.implements.length) signature += ` implements ${info.implements.join(", ")}`;
    }

    const body = [];
    if (item.kind === "enum") {
      body.push(`    ${item.literals.map((literal) => javaIdentifier(literal)).join(", ")};`);
      if (item.attributes.length || info.fields.length || item.methods.length) body.push("");
    }
    item.attributes.forEach((attribute) => {
      const modifiers = `${JAVA_VISIBILITY[attribute.visibility]}${attribute.is_static ? "static " : ""}`;
      const defaultValue = attribute.default ? ` = ${attribute.default}` : "";
      body.push(`    ${modifiers}${attribute.type || "Object"} ${javaIdentifier(attribute.name)}${defaultValue};`);
    });
    info.fields.forEach((field) => body.push(`    private ${field.type} ${field.name};`));
    if ((item.attributes.length || info.fields.length) && item.methods.length) body.push("");

    item.methods.forEach((method, index) => {
      if (index > 0) body.push("");
      const methodName = javaIdentifier(method.name);
      const isConstructor = methodName === name && !method.return_type;
      const returnType = isConstructor ? "" : `${method.return_type || "void"} `;
      const params = javaParams(method.params);
      const isAbstract = method.is_abstract && !isInterface;
      const modifiers =
        `${JAVA_VISIBILITY[method.visibility]}${method.is_static ? "static " : ""}` +
        `${isAbstract ? "abstract " : ""}`;
      const head = `    ${modifiers}${returnType}${methodName}(${params})`;
      if (isAbstract || (isInterface && !method.is_static)) {
        body.push(`${head};`);
        return;
      }
      body.push(`${head} {`);
      body.push("        // TODO");
      const plainType = (method.return_type || "void").trim();
      if (!isConstructor && plainType !== "void") {
        body.push(`        return ${PRIMITIVE_DEFAULTS[plainType] || "null"};`);
      }
      body.push("    }");
    });

    const usesList = info.fields.some((field) => field.type.startsWith("List<"));
    const imports = usesList ? "import java.util.List;\n\n" : "";
    return `// Datei: ${name}.java\n${imports}${signature} {\n${body.join("\n")}${body.length ? "\n" : ""}}\n`;
  }

  globalThis.LunoraUmlFormat = {
    CLASS_KINDS,
    RELATION_KINDS,
    COLORS,
    LIMITS,
    newId,
    normalizeDocument,
    relationAllowed,
    parseAttribute,
    parseMethod,
    parseLines,
    formatAttribute,
    formatMethod,
    toPlantUml,
    toJava,
  };
})();
