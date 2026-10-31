/** Short names for the generated API types. Never declare API shapes by hand: regenerate schema.d.ts. */
import type { Readable } from "openapi-typescript-helpers"; // openapi-fetch's own helper types
import type { components, operations } from "./schema";

/** As openapi-fetch returns them (`Readable` turns the schema's tuples into arrays). */
type Schemas = { [K in keyof components["schemas"]]: Readable<components["schemas"][K]> };

export type ApiKeyInfo = Schemas["ApiKeyInfo"];
export type ApiKeyCreated = Schemas["ApiKeyCreated"];
export type ChatMessage = Schemas["ChatMessage"];
export type ChatResponse = Schemas["ChatResponse"];
export type Check = Schemas["Check"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type DocumentType = Schemas["DocumentType"];
export type Flag = Schemas["Flag"];
export type FlagCode = Flag["code"];
export type FieldKind = Check["kind"];
export type FormDetail = Schemas["FormDetail"];
export type FormSummary = Schemas["FormSummary"];
export type LibraryDocument = Schemas["LibraryDocument"];
export type LibraryStats = Schemas["LibraryStats"];
export type ReviewRequest = Schemas["ReviewRequest"];
export type Decision = ReviewRequest["decision"];
export type ReviewStatus = NonNullable<LibraryDocument["review_status"]>;
export type SearchFilters = Schemas["SearchFilters"];
export type Source = Schemas["Source"];
export type ValueSource = Schemas["ValueSource"];
export type Scalar = Check["value"];

/** Query parameters shared by the document list and stats routes. */
export type DocumentFilters = NonNullable<operations["stats_v1_library_stats_get"]["parameters"]["query"]>;
