import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import PipelineInspector from "./PipelineInspector";

describe("PipelineInspector", () => {
  it("renders pipeline runs, jobs, and recurring work", async () => {
    render(<PipelineInspector />);

    expect(await screen.findByText("run_1")).toBeInTheDocument();
    expect(screen.getByText("daily-ingestion")).toBeInTheDocument();
    expect(screen.getByText("Test Episode")).toBeInTheDocument();
  });

  it("plans due recurring work", async () => {
    const user = userEvent.setup();
    render(<PipelineInspector />);

    await screen.findByText("run_1");
    await user.click(screen.getByRole("button", { name: "Plan due work" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("1 scheduled work item planned.");
    });
  });
});
