// 扁平配置 (ESLint 9)。规则只保留两类: 会导致真实故障的, 以及不熟 React 的人容易踩的。
// 格式一律不管 —— 那是 Prettier 的事, eslint-config-prettier 放在最后关掉所有格式规则。

import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import prettier from "eslint-config-prettier";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "tests/snapshots"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      // hooks 的两条: 顺序错了会在运行时炸, 依赖漏了会读到过期的闭包 ——
      // 这个仓库里已经因为后者出过跨会话串数据的问题 (ADR-0048 决策 2)。
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // 一个文件放不下两百多行时, 通常是它装了两件事。只是提醒, 不拦。
      "max-lines": ["warn", { max: 260, skipBlankLines: true, skipComments: true }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  prettier,
);
