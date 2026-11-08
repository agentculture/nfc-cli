// Verb specs generated from nfc_cli.registry.REGISTRY by scripts/export-verbs.py.
// Forms render from these JSON schemas, so the console never hand-declares a parameter.
import data from "./verbs.json";

export type JsonType = "string" | "integer" | "boolean" | "object" | "array";

export interface SchemaProp {
  type?: JsonType;
  description?: string;
  default?: unknown;
}

export interface VerbSpec {
  noun: string;
  verb: string;
  summary: string;
  route: string;
  mutating: boolean;
  trailer_flag: string | null;
  writes_trailers: boolean;
  schema: { properties: Record<string, SchemaProp>; required: string[] };
}

export const VERBS: VerbSpec[] = (data as unknown as { verbs: VerbSpec[] }).verbs;

/** Flags the write flow owns (dry-run/apply, key reveal, trailer gate); never plain form fields. */
export const CONTROL_PARAMS = ["apply", "include_keys", "include_trailers", "expect_uid"] as const;

export function getVerb(noun: string, verb: string): VerbSpec {
  const spec = VERBS.find((v) => v.noun === noun && v.verb === verb);
  if (!spec) throw new Error(`unknown verb ${noun} ${verb}`);
  return spec;
}

export function hasParam(spec: VerbSpec, name: string): boolean {
  return name in spec.schema.properties;
}

export interface Field {
  name: string;
  type: JsonType;
  label: string;
  help: string;
  required: boolean;
  default?: unknown;
}

function humanize(name: string): string {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function formFields(
  spec: VerbSpec,
  opts: { only?: readonly string[]; omit?: readonly string[] } = {},
): Field[] {
  const control = new Set<string>(CONTROL_PARAMS);
  const names = Object.keys(spec.schema.properties).filter(
    (n) => !control.has(n) && (!opts.only || opts.only.includes(n)) && !opts.omit?.includes(n),
  );
  const ordered = opts.only ? opts.only.filter((n) => names.includes(n)) : names;
  return ordered.map((name) => {
    const prop = spec.schema.properties[name];
    return {
      name,
      type: prop.type ?? "string",
      label: humanize(name),
      help: prop.description ?? "",
      required: spec.schema.required.includes(name),
      ...(prop.default !== undefined ? { default: prop.default } : {}),
    };
  });
}

export type RawValues = Record<string, string | boolean | undefined>;

/** Turn raw input values into typed params; blank fields are left out (the server default wins). */
export function coerceParams(
  fields: Field[],
  raw: RawValues,
): { params: Record<string, unknown>; errors: Record<string, string> } {
  const params: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const f of fields) {
    const v = raw[f.name];
    if (f.type === "boolean") {
      if (v === true) params[f.name] = true;
      continue;
    }
    const text = typeof v === "string" ? v.trim() : "";
    if (!text) {
      if (f.required) errors[f.name] = `${f.label} is required.`;
      continue;
    }
    if (f.type === "integer") {
      if (!/^-?\d+$/.test(text)) {
        errors[f.name] = `${f.label} must be a whole number.`;
        continue;
      }
      params[f.name] = Number.parseInt(text, 10);
    } else if (f.type === "object" || f.type === "array") {
      try {
        params[f.name] = JSON.parse(text);
      } catch {
        errors[f.name] = `${f.label} must be valid JSON.`;
      }
    } else {
      params[f.name] = text;
    }
  }
  return { params, errors };
}
