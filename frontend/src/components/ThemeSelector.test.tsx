import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ThemeSelector from "./ThemeSelector";

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] || null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
    get length() {
      return Object.keys(store).length;
    },
    key: vi.fn((index: number) => Object.keys(store)[index] || null),
  };
})();

Object.defineProperty(window, "localStorage", { value: localStorageMock });

describe("ThemeSelector", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();

    // Reset document classes and styles
    document.documentElement.className = "";
    document.documentElement.removeAttribute("style");
  });

  afterEach(() => {
    cleanup();
  });

  it("should render with default theme", () => {
    render(<ThemeSelector />);
    expect(screen.getByText("Bulma Light")).toBeInTheDocument();
  });

  it("should open dropdown when clicked", async () => {
    const user = userEvent.setup();
    render(<ThemeSelector />);

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    // Check that multiple themes are visible in the dropdown
    expect(screen.getByText("Bulma Dark")).toBeInTheDocument();
    expect(screen.getByText("Catppuccin Mocha")).toBeInTheDocument();
    expect(screen.getByText("Dracula")).toBeInTheDocument();
    expect(screen.getByText("GitHub Light")).toBeInTheDocument();
    expect(screen.getByText("GitHub Dark")).toBeInTheDocument();
  });

  it("should close dropdown when clicking outside", async () => {
    const user = userEvent.setup();
    render(<ThemeSelector />);

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    // Verify dropdown is open
    expect(screen.getByText("Dracula")).toBeInTheDocument();

    // Click the overlay (fixed inset-0 element)
    const overlay = document.querySelector(".fixed.inset-0");
    expect(overlay).toBeTruthy();
    if (overlay) {
      await user.click(overlay);
    }

    // Dropdown should close - look for a theme that's only in the dropdown
    // Wait for state update
    await vi.waitFor(() => {
      // When dropdown is closed, there should only be one "Bulma Light" visible (in the button)
      const allBulmaLight = screen.getAllByText(/Bulma Light/);
      expect(allBulmaLight.length).toBe(1);
    });
  });

  it("should select a theme and save to localStorage", async () => {
    const user = userEvent.setup();
    render(<ThemeSelector />);

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    // Select Dracula theme
    const draculaOption = screen.getByText("Dracula");
    await user.click(draculaOption);

    // Verify localStorage was updated
    expect(localStorageMock.setItem).toHaveBeenCalledWith("podex-theme", "dracula");
  });

  it("should apply dark class for dark themes", async () => {
    const user = userEvent.setup();
    render(<ThemeSelector />);

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    const draculaOption = screen.getByText("Dracula");
    await user.click(draculaOption);

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("dracula")).toBe(true);
  });

  it("should remove dark class for light themes", async () => {
    const user = userEvent.setup();

    // Start with a dark theme pre-selected
    localStorageMock.getItem.mockReturnValueOnce("dracula");
    render(<ThemeSelector />);

    // The theme should be applied
    await vi.waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(true);
    });

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    const lightOption = screen.getByText("Bulma Light");
    await user.click(lightOption);

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.classList.contains("bulma-light")).toBe(true);
  });

  it("should load saved theme from localStorage on mount", () => {
    localStorageMock.getItem.mockReturnValueOnce("catppuccin-mocha");
    render(<ThemeSelector />);

    // The button should show the saved theme
    expect(screen.getByText("Catppuccin Mocha")).toBeInTheDocument();
  });

  it("should apply CSS variables when theme is selected", async () => {
    const user = userEvent.setup();
    render(<ThemeSelector />);

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    const githubDark = screen.getByText("GitHub Dark");
    await user.click(githubDark);

    // Check that CSS variables were applied
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--color-primary")).toBe("#58a6ff");
    expect(style.getPropertyValue("--color-background")).toBe("#0d1117");
  });

  it("should show checkmark for current theme", async () => {
    const user = userEvent.setup();
    render(<ThemeSelector />);

    const button = screen.getByRole("button", { name: /select theme/i });
    await user.click(button);

    // Find the current theme button (has an SVG checkmark)
    const themeButtons = screen
      .getAllByRole("button")
      .filter((btn) => btn.textContent?.includes("Bulma Light"));
    // There should be 2 - main button and dropdown option
    expect(themeButtons.length).toBe(2);

    // The dropdown option should have a checkmark SVG
    const dropdownOption = themeButtons.find((btn) => btn.querySelector("svg"));
    expect(dropdownOption).toBeDefined();
  });
});
