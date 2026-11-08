import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getVerb } from "../api/verbs";
import { VerbForm } from "./VerbForm";

describe("VerbForm renders from the verb's JSON schema", () => {
  it("renders typed, labelled inputs and no control flags", () => {
    render(<VerbForm spec={getVerb("tag", "write")} submitLabel="Preview" onSubmit={() => {}} />);
    expect(screen.getByLabelText(/^Block/)).toHaveAttribute("type", "number");
    expect(screen.getByLabelText(/^Block/)).toBeRequired();
    expect(screen.getByLabelText(/^Data/)).toHaveAttribute("type", "text");
    expect(screen.queryByLabelText(/apply/i)).toBeNull();
    expect(screen.queryByLabelText(/include trailers/i)).toBeNull();
    expect(screen.getByText("hex data, a multiple of the block size")).toBeInTheDocument();
  });

  it("renders boolean params as checkboxes", () => {
    render(<VerbForm spec={getVerb("tag", "restore")} submitLabel="Preview" onSubmit={() => {}} />);
    expect(screen.getByLabelText(/^Resume/)).toHaveAttribute("type", "checkbox");
  });

  it("submits typed params and blocks submission with errors in words", async () => {
    const onSubmit = vi.fn();
    render(
      <VerbForm
        spec={getVerb("tag", "write")}
        only={["block", "data"]}
        submitLabel="Preview"
        onSubmit={onSubmit}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("Data is required.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^Block/), "4");
    await userEvent.type(screen.getByLabelText(/^Data/), "00ff");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(onSubmit).toHaveBeenCalledWith({ block: 4, data: "00ff" });
  });
});
