import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import RetentionSampling from "./RetentionSampling";

describe("RetentionSampling", () => {
  it("shows stratified coverage and recalculates a versioned policy", async () => {
    const user = userEvent.setup();
    render(<RetentionSampling />);

    expect(await screen.findByText("Coverage by stratum")).toBeInTheDocument();
    expect(screen.getByText("test-podcast")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Sampling policy version"));
    await user.type(screen.getByLabelText("Sampling policy version"), "retention-sample-v2");
    await user.selectOptions(screen.getByLabelText("Sampling target rate"), "0.1");
    await user.click(screen.getByRole("button", { name: "Recalculate sample" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "12 transcripts retained under retention-sample-v2.",
    );
  });

  it("previews lifecycle gates, purges with a digest, and re-acquires hot", async () => {
    const user = userEvent.setup();
    render(<RetentionSampling />);

    await user.click(await screen.findByRole("button", { name: /Retention Ready Episode/ }));
    expect(screen.getByLabelText("Suppress raw retention for source")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Preview lifecycle" }));
    expect(await screen.findByText("Purge eligible")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save evaluation" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Retention evaluation saved for Retention Ready Episode.",
    );
    await user.click(screen.getByRole("button", { name: "Purge raw transcript" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Raw transcript purged; digest digest_1 retained.",
    );
    await user.click(screen.getByRole("button", { name: "Re-acquire raw transcript" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Raw transcript re-acquired as trn_2; retention restarted at hot.",
    );
  });

  it("reviews and approves a submitted takedown request", async () => {
    const user = userEvent.setup();
    render(<RetentionSampling />);

    expect(await screen.findByText("Takedown intake")).toBeInTheDocument();
    expect(screen.getByText("Rights Holder")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Takedown operator name"), "operator");
    await user.type(screen.getByLabelText("Takedown decision note"), "Evidence verified.");
    await user.click(screen.getByRole("button", { name: "Approve request" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Takedown request td_1 approved.");
  });
});
