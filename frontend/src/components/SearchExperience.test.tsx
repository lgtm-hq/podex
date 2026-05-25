import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { SearchExperience } from "./SearchExperience";

describe("SearchExperience", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/search");
  });

  it("loads grouped v2 results and preserves URL filter state", async () => {
    const user = userEvent.setup();
    render(<SearchExperience initialQuery="test" />);

    const source = await screen.findByRole("link", { name: /Test Podcast/ });
    expect(source).toHaveAttribute("href", "/sources/test-podcast");

    await user.click(screen.getByRole("button", { name: "Sources" }));
    expect(window.location.search).toBe("?q=test&type=podcast");
    expect(screen.getByRole("link", { name: /Test Podcast/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "References" }));
    expect(await screen.findByText('No results for "test"')).toBeInTheDocument();
    expect(window.location.search).toBe("?q=test&type=media");
  });

  it("submits a trimmed catalog query into URL state", async () => {
    const user = userEvent.setup();
    render(<SearchExperience />);

    await user.type(screen.getByLabelText("Search Podex"), "  test  {enter}");

    expect(window.location.search).toBe("?q=test");
    expect(await screen.findByText("Test Podcast")).toBeInTheDocument();
  });
});
