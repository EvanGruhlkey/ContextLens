import { build, type Plugin } from "esbuild";

const unrelatedImports: Plugin = {
  name: "contextlens-unrelated-imports",
  setup(builder) {
    builder.onResolve({ filter: /^@app\/hooks\/api\/secretSyncs$/ }, () => ({
      path: "secret-sync-enum",
      namespace: "contextlens"
    }));
    builder.onResolve({ filter: /^@app\/lib\/schemas$/ }, () => ({
      path: "slug-schema",
      namespace: "contextlens"
    }));
    builder.onLoad({ filter: /.*/, namespace: "contextlens" }, (args) => ({
      contents:
        args.path === "secret-sync-enum"
          ? "export const SecretSyncInitialSyncBehavior = { Import: 'import' };"
          : "import { z } from 'zod'; export const slugSchema = () => z.string();",
      loader: "js",
      resolveDir: process.cwd()
    }));
  }
};

const bundled = await build({
  bundle: true,
  format: "cjs",
  platform: "node",
  plugins: [unrelatedImports],
  stdin: {
    contents:
      'export { BaseSecretSyncSchema } from "./src/components/secret-syncs/forms/schemas/base-secret-sync-schema.ts";',
    loader: "ts",
    resolveDir: process.cwd()
  },
  treeShaking: true,
  write: false
});
const moduleValue: { exports: Record<string, unknown> } = { exports: {} };
new Function("module", "exports", bundled.outputFiles[0].text)(
  moduleValue,
  moduleValue.exports
);
const BaseSecretSyncSchema = moduleValue.exports.BaseSecretSyncSchema as () => {
  safeParse(value: unknown):
    | { success: true }
    | { success: false; error: { issues: Array<{ path: unknown[]; message: string }> } };
};

const schema = BaseSecretSyncSchema();
const nullResult = schema.safeParse({ connection: null });
if (nullResult.success) {
  throw new Error("null connection unexpectedly passed validation");
}
const connectionIssue = nullResult.error.issues.find(
  (issue) => issue.path.length === 1 && issue.path[0] === "connection"
);
if (connectionIssue?.message !== "Connection Required") {
  throw new Error(
    `expected Connection Required, received ${connectionIssue?.message ?? "no connection issue"}`
  );
}

const validResult = schema.safeParse({
  connection: {
    name: "fixture",
    id: "123e4567-e89b-12d3-a456-426614174000"
  }
});
if (
  !validResult.success &&
  validResult.error.issues.some(
    (issue) => issue.path.length === 1 && issue.path[0] === "connection"
  )
) {
  throw new Error("a valid connection produced a connection-field error");
}

console.log("ContextLens hidden connection-schema grader: PASS");
