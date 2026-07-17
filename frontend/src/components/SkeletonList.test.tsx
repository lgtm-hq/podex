import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SkeletonList from "./SkeletonList";

describe("SkeletonList", () => {
  it("renders 4 skeleton items by default", () => {
    const { container } = render(<SkeletonList />);
    expect(container.querySelectorAll("li")).toHaveLength(4);
  });

  it("renders the requested number of skeleton items", () => {
    const { container } = render(<SkeletonList count={6} />);
    expect(container.querySelectorAll("li")).toHaveLength(6);
  });

  it("marks the list as aria-busy while loading", () => {
    render(<SkeletonList />);
    const list = screen.getByRole("list");
    expect(list.getAttribute("aria-busy")).toBe("true");
  });

  it("hides decorative skeleton items from screen readers", () => {
    const { container } = render(<SkeletonList count={2} />);
    const items = container.querySelectorAll("li");
    items.forEach((item) => {
      expect(item.getAttribute("aria-hidden")).toBe("true");
    });
  });
});
