import type { ApiError } from "../api/client";

/** A refused or failed call, in words: what happened and what to do about it. */
export function ErrorNote({ error }: { error: ApiError }) {
  return (
    <div role="alert" className="console-error">
      <p>{error.message}</p>
      {error.remediation && <p>{error.remediation}</p>}
    </div>
  );
}
