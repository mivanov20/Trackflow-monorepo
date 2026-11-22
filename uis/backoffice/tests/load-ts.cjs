"use strict";
/**
 * Loads the backoffice's TypeScript modules inside `node --test` without a bundler or a new dependency:
 * the TypeScript compiler already in devDependencies transpiles each file, and relative imports are resolved
 * by this loader. `overrides` replaces a module (by file name) with a test double.
 */
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const ROOT = path.resolve(__dirname, "..");

function createLoader(overrides = {}) {
  const registry = new Map();

  function resolveFile(fromDir, request) {
    const base = path.resolve(fromDir, request);
    for (const candidate of [`${base}.ts`, `${base}.tsx`, base]) {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    }
    throw new Error(`Cannot resolve ${request} from ${fromDir}`);
  }

  function loadFile(file) {
    const name = path.basename(file).replace(/\.tsx?$/, "");
    if (Object.prototype.hasOwnProperty.call(overrides, name)) return overrides[name];
    if (registry.has(file)) return registry.get(file).exports;
    const source = fs.readFileSync(file, "utf8");
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      fileName: file,
    });
    const module = { exports: {} };
    registry.set(file, module);
    const localRequire = (request) => {
      if (request.startsWith(".")) return loadFile(resolveFile(path.dirname(file), request));
      return require(request);
    };
    new Function("exports", "require", "module", outputText)(module.exports, localRequire, module);
    return module.exports;
  }

  return (relativePath) => loadFile(resolveFile(ROOT, relativePath));
}

module.exports = { createLoader };
