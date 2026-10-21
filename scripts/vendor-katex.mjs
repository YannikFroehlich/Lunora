// Copies KaTeX's stylesheet, woff2 fonts and license from node_modules into
// app/static/vendor/katex/. Run `npm run vendor:katex` after bumping the katex package.
// Each @font-face keeps only its woff2 source, versioned with ?v=katex-<version>, because
// template tags cannot rewrite CSS url(...) values (see CLAUDE.md "Static asset versioning").
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const source = fileURLToPath(new URL("node_modules/katex/", root));
const target = fileURLToPath(new URL("app/static/vendor/katex/", root));
const { version } = JSON.parse(readFileSync(`${source}package.json`, "utf8"));

const fonts = [];
const css = readFileSync(`${source}dist/katex.min.css`, "utf8").replace(
  /src:url\(fonts\/([\w-]+)\.woff2\) format\("woff2"\),url\(fonts\/\1\.woff\) format\("woff"\),url\(fonts\/\1\.ttf\) format\("truetype"\)/g,
  (_match, font) => {
    fonts.push(`${font}.woff2`);
    return `src:url(fonts/${font}.woff2?v=katex-${version}) format("woff2")`;
  },
);

const fontFaces = css.match(/@font-face\{/g)?.length ?? 0;
if (!fonts.length || fonts.length !== fontFaces || /\.(woff|ttf)\)/.test(css)) {
  throw new Error(
    `katex.min.css ${version}: rewrote ${fonts.length} of ${fontFaces} @font-face rules; update this script for the new src format.`,
  );
}

rmSync(target, { recursive: true, force: true });
mkdirSync(`${target}fonts`, { recursive: true });
writeFileSync(`${target}katex.min.css`, css);
copyFileSync(`${source}LICENSE`, `${target}LICENSE.txt`);
for (const font of fonts) {
  copyFileSync(`${source}dist/fonts/${font}`, `${target}fonts/${font}`);
}
console.log(`Vendored KaTeX ${version}: stylesheet and ${fonts.length} woff2 fonts in app/static/vendor/katex/`);
