import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// §7.2 / §7.7: шрифты только из @fontsource-variable (лежат в бандле). next/font/google тянет
// файлы с fonts.gstatic.com на этапе сборки и в рантайме недоступен у оператора из РФ.
const FONT_GOOGLE_MESSAGE =
  "next/font/google запрещён: шрифты подключаются из @fontsource-variable/* (docs/ARCHITECTURE.md §7.7).";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [{ name: "next/font/google", message: FONT_GOOGLE_MESSAGE }],
          patterns: [{ group: ["next/font/google/*"], message: FONT_GOOGLE_MESSAGE }],
        },
      ],
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "out-mock/**",
    "build/**",
    "next-env.d.ts",
    "lib/api/schema.d.ts",
    "public/mockServiceWorker.js",
    "test-results/**",
    "playwright-report/**",
  ]),
]);

export default eslintConfig;
