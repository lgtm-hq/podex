import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AccountHome } from "./AccountHome";
import { AccountSettings } from "./AccountSettings";
import { FollowPodcastButton } from "./FollowPodcastButton";
import { MagicLinkVerification } from "./MagicLinkVerification";
import { SaveMediaButton } from "./SaveMediaButton";
import { SignIn } from "./SignIn";

describe("Account authentication", () => {
  it("requests a passwordless sign-in link", async () => {
    const user = userEvent.setup();
    render(<SignIn redirectPath="/account" />);

    await user.type(screen.getByLabelText("Email address"), "reader@example.com");
    await user.click(screen.getByRole("button", { name: "Email me a sign-in link" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Check your email for a sign-in link.",
    );
  });

  it("verifies a one-time link and presents the account continuation", async () => {
    render(<MagicLinkVerification token="one-time-token" redirectPath="/account" />);

    expect(await screen.findByText(/You are signed in/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue to Podex" })).toHaveAttribute(
      "href",
      "/account",
    );
  });

  it("loads the authenticated account and signs out", async () => {
    const user = userEvent.setup();
    render(<AccountHome />);

    expect(await screen.findByText("reader@example.com")).toBeInTheDocument();
    expect(screen.getByText("Test Book")).toBeInTheDocument();
    expect(screen.getByText("Test Podcast")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.queryByText("Test Book")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unfollow" }));
    expect(screen.queryByText("Test Podcast")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("saves a public media record from its detail action", async () => {
    const user = userEvent.setup();
    render(<SaveMediaButton mediaId="med_2" />);

    const saveButton = await screen.findByRole("button", { name: "Save reference" });
    await user.click(saveButton);
    expect(await screen.findByRole("button", { name: "Saved" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Saved" }));
    expect(await screen.findByRole("button", { name: "Save reference" })).toBeInTheDocument();
  });

  it("follows a public podcast source from its detail action", async () => {
    const user = userEvent.setup();
    render(<FollowPodcastButton podcastId="pod_2" slug="second-podcast" />);

    const followButton = await screen.findByRole("button", { name: "Follow source" });
    await user.click(followButton);
    expect(await screen.findByRole("button", { name: "Following" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Following" }));
    expect(await screen.findByRole("button", { name: "Follow source" })).toBeInTheDocument();
  });

  it("manages alert rules for account-linked public resources", async () => {
    const user = userEvent.setup();
    render(<AccountHome />);

    expect(await screen.findByText("New mentions for med_1")).toBeInTheDocument();
    expect(screen.getByText("Podex digest: 1 new update")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Alert on episodes" }));
    expect(await screen.findByText("New episodes for pod_1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("No new alert activity.");
    await user.click(screen.getByRole("button", { name: "Send digest" }));
    expect(await screen.findByText("Podex digest: 2 new updates")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("Digest delivered");
  });

  it("stores notification settings for the signed-in account", async () => {
    const user = userEvent.setup();
    render(<AccountSettings />);

    const enabled = await screen.findByRole("checkbox", { name: "Digest emails enabled" });
    expect(enabled).toBeChecked();
    expect(screen.getByText("Free discovery")).toBeInTheDocument();
    expect(screen.getByText(/Paid upgrades are not available/)).toBeInTheDocument();
    await user.click(enabled);
    await user.selectOptions(screen.getByLabelText("Digest frequency"), "weekly");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Notification settings saved.");
    expect(enabled).not.toBeChecked();
    expect(screen.getByLabelText("Digest frequency")).toHaveValue("weekly");
  });
});
