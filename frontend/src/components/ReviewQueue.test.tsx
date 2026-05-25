import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ReviewQueue from "./ReviewQueue";

describe("ReviewQueue", () => {
  it("shows candidate evidence, extraction history, and decision controls", async () => {
    render(<ReviewQueue />);

    expect((await screen.findAllByText("Test Book")).length).toBeGreaterThan(0);
    expect(screen.getByText(/I recommend Test Book/)).toBeInTheDocument();
    expect(document.body).toHaveTextContent("gpt-test");
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("opens split replacements and submits a reclassification", async () => {
    const user = userEvent.setup();
    render(<ReviewQueue />);

    await screen.findAllByText("Test Book");
    await user.click(screen.getByRole("button", { name: "Split" }));
    expect(screen.getByRole("dialog", { name: "split review item" })).toBeInTheDocument();
    expect(screen.getAllByLabelText(/Split title/)).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Close" }));

    await user.click(screen.getByRole("button", { name: "Reclassify" }));
    await user.selectOptions(screen.getByLabelText("Reclassified media type"), "article");
    await user.click(screen.getByRole("button", { name: "Confirm reclassify" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Test Book reclassified.");
    });
  });
});
