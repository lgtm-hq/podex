import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import EmptyState from "./EmptyState";

describe("EmptyState", () => {
  it("renders the title", () => {
    render(<EmptyState title="No podcasts yet." />);
    expect(screen.getByText("No podcasts yet.")).toBeDefined();
  });

  it("exposes role=status for screen readers", () => {
    render(<EmptyState title="Nothing here." />);
    expect(screen.getByRole("status")).toBeDefined();
  });

  it("renders description when provided", () => {
    render(
      <EmptyState title="Empty" description="Check back later." />,
    );
    expect(screen.getByText("Check back later.")).toBeDefined();
  });

  it("does not render a description paragraph when omitted", () => {
    const { container } = render(<EmptyState title="Empty" />);
    const paras = container.querySelectorAll("p");
    expect(paras).toHaveLength(1);
  });
});
