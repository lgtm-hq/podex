import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DegradedBanner from "./DegradedBanner";

describe("DegradedBanner", () => {
  it("renders the default error message", () => {
    render(<DegradedBanner />);
    expect(
      screen.getByText("Unable to load podcasts right now."),
    ).toBeDefined();
  });

  it("renders a custom message when provided", () => {
    render(<DegradedBanner message="Something went wrong." />);
    expect(screen.getByText("Something went wrong.")).toBeDefined();
  });

  it("exposes role=alert for screen readers", () => {
    render(<DegradedBanner />);
    expect(screen.getByRole("alert")).toBeDefined();
  });
});
