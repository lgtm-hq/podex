import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import MediaManager from "./MediaManager";

describe("MediaManager", () => {
  it("previews a canonical merge with moved mentions and aliases", async () => {
    const user = userEvent.setup();
    render(<MediaManager />);

    await user.click(await screen.findByRole("button", { name: /Test Book/ }));
    await user.click(screen.getByRole("button", { name: "Choose as survivor" }));
    await user.click(screen.getByRole("button", { name: "Generate preview" }));

    expect(await screen.findByText("New aliases on survivor")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("(merge)");
    expect(screen.getByRole("button", { name: "Merge into survivor" })).toBeInTheDocument();
  });

  it("edits metadata and adds managed aliases and references", async () => {
    const user = userEvent.setup();
    render(<MediaManager />);

    await user.click(await screen.findByRole("button", { name: /Test Book/ }));
    await user.clear(screen.getByLabelText("Canonical title"));
    await user.type(screen.getByLabelText("Canonical title"), "Corrected Book");
    await user.click(screen.getByRole("button", { name: "Save metadata" }));
    expect(await screen.findByText("Corrected Book metadata updated.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("New media alias"), "Alternate Book");
    await user.click(screen.getByRole("button", { name: "Add alias" }));
    expect(await screen.findByText("Alternate Book", { exact: false })).toBeInTheDocument();

    await user.type(screen.getByLabelText("External reference identifier"), "ref-1");
    await user.click(screen.getByRole("button", { name: "Save reference" }));
    expect(await screen.findByText("ref-1")).toBeInTheDocument();
  });

  it("recovers selected mentions into a new canonical record", async () => {
    const user = userEvent.setup();
    render(<MediaManager />);

    await user.click(await screen.findByRole("button", { name: /Test Book/ }));
    await user.click(screen.getByLabelText("Recover mention from Test Episode"));
    await user.type(screen.getByLabelText("Recovered media title"), "Recovered Book");
    await user.click(screen.getByRole("button", { name: "Recover selected mentions" }));

    expect(await screen.findByText("1 mention recovered into Recovered Book.")).toBeInTheDocument();
  });
});
