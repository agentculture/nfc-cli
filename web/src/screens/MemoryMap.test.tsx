import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryMap } from "./MemoryMap";
import { layoutFor } from "./tagModel";

const blocks: Record<string, string> = {};
for (let i = 0; i < 64; i++) blocks[i] = i % 4 === 3 ? "************ff078069************" : "00".repeat(16);
blocks[0] = "cb30c5605e0804006263646566676869";

describe("MemoryMap", () => {
  it("is hidden decoration while no tag is present", () => {
    const { container } = render(<MemoryMap layout={layoutFor(null, 0)} present={false} />);
    expect(container.querySelector("figure")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("offers one button per sector, describing trailers in words", async () => {
    const onSelect = vi.fn();
    render(
      <MemoryMap
        layout={layoutFor("classic_1k", 64)}
        present
        uid="cb30c560"
        blocks={blocks}
        selected={0}
        onSelect={onSelect}
      />,
    );
    const sectors = screen.getAllByRole("button");
    expect(sectors).toHaveLength(16);
    expect(sectors[0]).toHaveAttribute("aria-pressed", "true");
    expect(sectors[1]).toHaveAccessibleName(/sector 1: 3 data blocks, 3 empty; trailer locked/i);
    await userEvent.click(sectors[5]);
    expect(onSelect).toHaveBeenCalledWith(5);
  });

  it("marks blocks a pending write would change, in words too", () => {
    render(
      <MemoryMap
        layout={layoutFor("classic_1k", 64)}
        present
        uid="cb30c560"
        blocks={blocks}
        pending={[4, 7]}
        selected={null}
        onSelect={() => {}}
      />,
    );
    expect(screen.getAllByRole("button")[1]).toHaveAccessibleName(/2 blocks would change/i);
  });
});
