import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

// uml_format.js is a plain <script> (not bundled), so load it into a sandbox and read its global.
const source = readFileSync(new URL("../app/static/js/uml_format.js", import.meta.url), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const format = sandbox.LunoraUmlFormat;

describe("UML member parsing", () => {
  it("parses attributes and formats them back", () => {
    const attribute = format.parseAttribute("- {static} anzahl: int = 0");
    expect(attribute).toEqual({ visibility: "-", name: "anzahl", type: "int", default: "0", is_static: true });
    expect(format.formatAttribute(attribute)).toBe("- {static} anzahl: int = 0");
  });

  it("parses methods with generic parameters", () => {
    const method = format.parseMethod("# abstract berechne(werte: Map<String, Integer>, n: int): double");
    expect(method.visibility).toBe("#");
    expect(method.is_abstract).toBe(true);
    expect(method.params).toBe("werte: Map<String, Integer>, n: int");
    expect(method.return_type).toBe("double");
  });

  it("drops blank lines", () => {
    expect(format.parseLines("+ a: int\n\n   \n- b", format.parseAttribute).map((item) => item.name)).toEqual([
      "a",
      "b",
    ]);
  });
});

describe("UML document normalization", () => {
  it("fills missing keys and drops relations that the server would reject", () => {
    const doc = format.normalizeDocument({
      classes: [
        { id: "a", name: "A" },
        { id: "b", name: "B", kind: "interface" },
      ],
      notes: [{ id: "n", text: "Hinweis" }],
      relations: [
        { id: "r1", kind: "inheritance", source: "a", target: "b" },
        { id: "r2", kind: "inheritance", source: "a", target: "a" },
        { id: "r3", kind: "association", source: "n", target: "a" },
        { id: "r4", kind: "association", source: "a", target: "missing" },
      ],
    });
    expect(doc.classes[0]).toMatchObject({ kind: "class", color: "default", attributes: [], literals: [] });
    expect(doc.relations.map((item) => item.id)).toEqual(["r1"]);
  });
});

describe("UML exports", () => {
  const doc = format.normalizeDocument({
    classes: [
      {
        id: "tier",
        kind: "abstract",
        name: "Tier",
        attributes: [{ visibility: "-", name: "name", type: "String" }],
        methods: [{ visibility: "+", name: "laut", return_type: "String", is_abstract: true }],
      },
      {
        id: "hund",
        name: "Hund",
        methods: [
          { visibility: "+", name: "Hund", params: "name: String" },
          { visibility: "+", name: "laut", return_type: "String" },
        ],
      },
      { id: "zoo", name: "Zoo" },
    ],
    relations: [
      { id: "r1", kind: "inheritance", source: "hund", target: "tier" },
      { id: "r2", kind: "aggregation", source: "tier", target: "zoo", source_multiplicity: "0..*" },
    ],
  });

  it("generates Java skeletons with inheritance, constructors and aggregation fields", () => {
    const java = format.toJava(doc);
    expect(java).toContain("import java.util.List;");
    expect(java).toContain("public abstract class Tier {");
    expect(java).toContain("    public abstract String laut();");
    expect(java).toContain("public class Hund extends Tier {");
    expect(java).toContain("    public Hund(String name) {");
    expect(java).toContain("        return null;");
    expect(java).toContain("    private List<Tier> tierListe;");
  });

  it("generates PlantUML with aliases and multiplicities", () => {
    const plantUml = format.toPlantUml(doc, "Zoo");
    expect(plantUml).toContain('abstract class "Tier" as tier {');
    expect(plantUml).toContain("hund --|> tier");
    expect(plantUml).toContain('tier "0..*" --o zoo');
    expect(plantUml.trim().endsWith("@enduml")).toBe(true);
  });
});
