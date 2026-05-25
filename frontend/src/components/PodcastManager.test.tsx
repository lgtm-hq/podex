import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import PodcastManager from "./PodcastManager";

describe("PodcastManager", () => {
  it("loads podcast inventory and opens the create form", async () => {
    const user = userEvent.setup();
    render(<PodcastManager />);

    expect(await screen.findByText("Test Podcast")).toBeInTheDocument();
    expect(screen.getAllByText("RSS")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Add podcast" }));
    expect(screen.getByRole("dialog", { name: "Add podcast" })).toBeInTheDocument();
  });

  it("pauses a podcast through the ops mutation", async () => {
    const user = userEvent.setup();
    render(<PodcastManager />);

    await screen.findByText("Test Podcast");
    await user.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Test Podcast is now paused.");
    });
  });
});
