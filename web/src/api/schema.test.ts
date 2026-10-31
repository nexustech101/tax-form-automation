import { readFileSync } from "node:fs";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";
import { expect, it } from "vitest";

it("schema.d.ts is generated from the committed openapi.json (else: npm run gen:api)", async () => {
  const ast = await openapiTS(new URL("./openapi.json", import.meta.url));
  const committed = readFileSync(new URL("./schema.d.ts", import.meta.url), "utf8").replace(/\r\n/g, "\n"); // git autocrlf
  expect(committed).toBe(COMMENT_HEADER + astToString(ast));
});
