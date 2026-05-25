import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import SearchOperations from "./SearchOperations";

describe("SearchOperations", () => {
  it("renders projection diagnostics and queues scoped repairs", async () => {
    const user = userEvent.setup();
    render(<SearchOperations />);

    expect(await screen.findByText("Projection repairs")).toBeInTheDocument();
    expect(screen.getByText("Relevance signals")).toBeInTheDocument();
    expect(screen.getByText("sci fi")).toBeInTheDocument();
    expect(screen.getByText("med_1")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Reindex media type"), "book");
    await user.click(screen.getByRole("button", { name: "Queue reindex" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("6 projection repairs queued.");
    });
  });

  it("previews and applies synonym and ranking tuning", async () => {
    const user = userEvent.setup();
    render(<SearchOperations />);

    await screen.findByText("Projection repairs");
    await user.type(screen.getByLabelText("Tuning sample query"), "sci fi");
    await user.type(screen.getByLabelText("Tuning synonyms"), "sci fi: science fiction");
    await user.click(screen.getByRole("button", { name: "Preview tuning" }));
    expect(await screen.findByText("Science Fiction Essentials")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Apply tuning" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Tuning update for media enqueued.",
    );
  });
});
