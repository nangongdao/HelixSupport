// Helix Support — ESLint flat config (Phase 26.4)
//
// Zero-build constraint: ESLint is optional (not installed by default). The
// CI gate `scripts/frontend_gate.py` runs `node --check` + the Node test
// runner + the 400-line module limit without ESLint; when ESLint is present
// (`npm i -D eslint` in the repo root) it is applied here.
//
// Run: npx eslint app/static/js/ tests/frontend/

export default [
  {
    files: ["app/static/js/**/*.js", "tests/frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-undef": "off", // browser globals (fetch, EventSource, window) are ambient
      "no-constant-condition": "error",
      "no-duplicate-imports": "error",
    },
  },
];
