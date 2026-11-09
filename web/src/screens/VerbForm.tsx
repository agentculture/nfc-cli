import { type FormEvent, useId, useMemo, useState } from "react";
import { type RawValues, type VerbSpec, coerceParams, formFields } from "../api/verbs";

export interface VerbFormProps {
  spec: VerbSpec;
  only?: readonly string[];
  omit?: readonly string[];
  initial?: RawValues;
  submitLabel: string;
  busy?: boolean;
  onSubmit: (params: Record<string, unknown>) => void;
  onChange?: () => void;
}

/** A form generated from a verb's JSON schema (web/src/api/verbs.json). */
export function VerbForm({ spec, only, omit, initial, submitLabel, busy, onSubmit, onChange }: VerbFormProps) {
  const fields = useMemo(() => formFields(spec, { only, omit }), [spec, only, omit]);
  const [values, setValues] = useState<RawValues>(() => ({ ...initial }));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const base = useId();

  function set(name: string, value: string | boolean) {
    setValues((v) => ({ ...v, [name]: value }));
    onChange?.();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const { params, errors: errs } = coerceParams(fields, values);
    setErrors(errs);
    if (Object.keys(errs).length === 0) onSubmit(params);
  }

  return (
    <form className="verb-form" onSubmit={submit} noValidate>
      {fields.length > 0 && (
        <div className="verb-form__fields">
          {fields.map((f) => {
            const id = `${base}-${f.name}`;
            const hintId = `${id}-hint`;
            const errId = `${id}-err`;
            const describedBy = [f.help || f.default !== undefined ? hintId : "", errors[f.name] ? errId : ""]
              .filter(Boolean)
              .join(" ") || undefined;
            const hint = (
              <span id={hintId} className="field__hint">
                {f.help}
                {f.default !== undefined && f.type !== "boolean" ? ` (default ${String(f.default)})` : ""}
              </span>
            );
            if (f.type === "boolean") {
              return (
                <div className="field field--check" key={f.name}>
                  <input
                    id={id}
                    type="checkbox"
                    checked={values[f.name] === true}
                    onChange={(e) => set(f.name, e.target.checked)}
                    aria-describedby={describedBy}
                  />
                  <label htmlFor={id}>{f.label}</label>
                  {hint}
                </div>
              );
            }
            const mono = ["data", "key", "apdu", "block", "count"].includes(f.name);
            return (
              <div className="field" key={f.name}>
                <label htmlFor={id}>
                  {f.label}
                  {f.required && <span className="field__req"> (required)</span>}
                </label>
                <input
                  id={id}
                  className={mono ? "field__input field__input--mono" : "field__input"}
                  type={f.type === "integer" ? "number" : "text"}
                  inputMode={f.type === "integer" ? "numeric" : undefined}
                  required={f.required}
                  spellCheck={false}
                  autoComplete="off"
                  value={typeof values[f.name] === "string" ? (values[f.name] as string) : ""}
                  onChange={(e) => set(f.name, e.target.value)}
                  aria-invalid={errors[f.name] ? true : undefined}
                  aria-describedby={describedBy}
                />
                {hint}
                {errors[f.name] && (
                  <span id={errId} className="field__error">
                    {errors[f.name]}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
      <button type="submit" className="console-button console-button--quiet" disabled={busy}>
        {submitLabel}
      </button>
    </form>
  );
}
