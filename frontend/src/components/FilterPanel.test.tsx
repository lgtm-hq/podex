import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FilterPanel from "./FilterPanel";

describe("FilterPanel", () => {
  beforeEach(() => {
    Object.defineProperty(window, "location", {
      value: { href: "" },
      writable: true,
    });
  });

  it("should render all media type buttons", () => {
    render(<FilterPanel />);

    expect(screen.getByText("Books")).toBeInTheDocument();
    expect(screen.getByText("Movies")).toBeInTheDocument();
    expect(screen.getByText("TV Shows")).toBeInTheDocument();
    expect(screen.getByText("Docs")).toBeInTheDocument();
    expect(screen.getByText("Podcasts")).toBeInTheDocument();
    expect(screen.getByText("Standup")).toBeInTheDocument();
    expect(screen.getByText("Articles")).toBeInTheDocument();
    expect(screen.getByText("Studies")).toBeInTheDocument();
  });

  it("should render sort dropdown with default value", () => {
    render(<FilterPanel />);

    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("mention_count");
  });

  it("should highlight selected types", () => {
    render(<FilterPanel initialTypes={["book", "movie"]} />);

    const booksButton = screen.getByText("Books").closest("button");
    const moviesButton = screen.getByText("Movies").closest("button");
    const tvButton = screen.getByText("TV Shows").closest("button");

    // Selected buttons should have different styling (bg-text)
    expect(booksButton).toHaveClass("bg-text");
    expect(moviesButton).toHaveClass("bg-text");
    expect(tvButton).not.toHaveClass("bg-text");
  });

  it("should call onFilterChange when type is toggled", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(<FilterPanel onFilterChange={onFilterChange} />);

    const booksButton = screen.getByText("Books").closest("button")!;
    await user.click(booksButton);

    expect(onFilterChange).toHaveBeenCalledWith({
      types: ["book"],
      sort: "mention_count",
      order: "desc",
    });
  });

  it("should remove type when already selected type is clicked", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(<FilterPanel initialTypes={["book"]} onFilterChange={onFilterChange} />);

    const booksButton = screen.getByText("Books").closest("button")!;
    await user.click(booksButton);

    expect(onFilterChange).toHaveBeenCalledWith({
      types: [],
      sort: "mention_count",
      order: "desc",
    });
  });

  it("should call onFilterChange when sort is changed", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(<FilterPanel onFilterChange={onFilterChange} />);

    const select = screen.getByRole("combobox");
    await user.selectOptions(select, "title");

    expect(onFilterChange).toHaveBeenCalledWith({
      types: [],
      sort: "title",
      order: "desc",
    });
  });

  it("should toggle sort order when order button is clicked", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(<FilterPanel onFilterChange={onFilterChange} />);

    const orderButton = screen.getByTitle("Descending (highest first)");
    await user.click(orderButton);

    expect(onFilterChange).toHaveBeenCalledWith({
      types: [],
      sort: "mention_count",
      order: "asc",
    });
  });

  it("should show clear filters button when filters are active", () => {
    render(<FilterPanel initialTypes={["book"]} />);
    expect(screen.getByText("Clear filters")).toBeInTheDocument();
  });

  it("should not show clear filters button when no filters are active", () => {
    render(<FilterPanel />);
    expect(screen.queryByText("Clear filters")).not.toBeInTheDocument();
  });

  it("should navigate to /media when clear filters is clicked", async () => {
    const user = userEvent.setup();
    render(<FilterPanel initialTypes={["book"]} />);

    await user.click(screen.getByText("Clear filters"));

    expect(window.location.href).toBe("/media");
  });

  it("should navigate with query params when no callback provided", async () => {
    const user = userEvent.setup();
    render(<FilterPanel />);

    const booksButton = screen.getByText("Books").closest("button")!;
    await user.click(booksButton);

    expect(window.location.href).toBe("/media?type=book");
  });

  it("should include multiple types in URL", async () => {
    const user = userEvent.setup();
    render(<FilterPanel initialTypes={["book"]} />);

    const moviesButton = screen.getByText("Movies").closest("button")!;
    await user.click(moviesButton);

    expect(window.location.href).toContain("type=book");
    expect(window.location.href).toContain("type=movie");
  });
});
