import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchBar from "./SearchBar";

describe("SearchBar", () => {
  beforeEach(() => {
    // Mock window.location
    Object.defineProperty(window, "location", {
      value: { href: "" },
      writable: true,
    });
  });

  it("should render with default placeholder", () => {
    render(<SearchBar />);
    expect(screen.getByPlaceholderText("Search media...")).toBeInTheDocument();
  });

  it("should render with custom placeholder", () => {
    render(<SearchBar placeholder="Find something..." />);
    expect(screen.getByPlaceholderText("Find something...")).toBeInTheDocument();
  });

  it("should update input value on typing", async () => {
    const user = userEvent.setup();
    render(<SearchBar />);

    const input = screen.getByPlaceholderText("Search media...");
    await user.type(input, "test query");

    expect(input).toHaveValue("test query");
  });

  it("should show clear button when query is not empty", async () => {
    const user = userEvent.setup();
    render(<SearchBar />);

    const input = screen.getByPlaceholderText("Search media...");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    await user.type(input, "test");

    // Clear button should now be visible
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("should clear input when clear button is clicked", async () => {
    const user = userEvent.setup();
    render(<SearchBar />);

    const input = screen.getByPlaceholderText("Search media...");
    await user.type(input, "test query");
    expect(input).toHaveValue("test query");

    const clearButton = screen.getByRole("button");
    await user.click(clearButton);

    expect(input).toHaveValue("");
  });

  it("should call onSearch callback on form submit", async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    const input = screen.getByPlaceholderText("Search media...");
    await user.type(input, "test query{enter}");

    expect(onSearch).toHaveBeenCalledWith("test query");
  });

  it("should navigate to search page on submit without callback", async () => {
    const user = userEvent.setup();
    render(<SearchBar />);

    const input = screen.getByPlaceholderText("Search media...");
    await user.type(input, "my search{enter}");

    expect(window.location.href).toBe("/search?q=my%20search");
  });

  it("should not submit empty queries", async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    const input = screen.getByPlaceholderText("Search media...");
    await user.type(input, "   {enter}");

    expect(onSearch).not.toHaveBeenCalled();
  });

  it("should trim whitespace from queries", async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    const input = screen.getByPlaceholderText("Search media...");
    await user.type(input, "  test query  {enter}");

    expect(onSearch).toHaveBeenCalledWith("test query");
  });
});
