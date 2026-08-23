// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CommandCard,
  CopyField,
  PublicationCommandCard,
  RunCommandCard,
  quoteBash,
  quotePowerShell,
  repositorySetupCommands,
} from "./workflow";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("workflow commands", () => {
  it("quotes dynamic values for both supported shells", () => {
    expect(quotePowerShell("team's model")).toBe("'team''s model'");
    expect(quoteBash("team's model")).toBe("'team'\"'\"'s model'");
  });

  it("builds a repository-specific clone and configuration sequence", () => {
    const commands = repositorySetupCommands({
      name: "Research",
      ssh_clone_url: "git@git.example:team/research.git",
      http_clone_url: null,
    });
    expect(commands.powershell).toContain("git clone 'git@git.example:team/research.git'");
    expect(commands.powershell).toContain("cd 'research'");
    expect(commands.powershell).toContain("homebrew-mlflow repository configure");
  });

  it("copies an exact value with visible feedback", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    render(<CopyField label="Run ID" value="run_01M0F1WMYDJVMTJB5V94KY7XR6" />);
    await user.click(screen.getByRole("button", { name: /^Copy/ }));
    expect(writeText).toHaveBeenCalledWith("run_01M0F1WMYDJVMTJB5V94KY7XR6");
    expect(screen.getByRole("button", { name: /Copied/ })).not.toBeNull();
  });

  it("switches shells and remembers the preference", async () => {
    render(<CommandCard title="Example" commands={{ powershell: "Write-Output ok", bash: "echo ok" }} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Bash" }));
    expect((screen.getByLabelText("Example command") as HTMLTextAreaElement).value).toBe("echo ok");
    expect(window.localStorage.getItem("homebrew-mlflow-shell")).toBe("bash");
  });

  it("requires complete Run inputs and preserves exact input order", async () => {
    const user = userEvent.setup();
    render(<RunCommandCard />);
    const copy = screen.getByRole("button", { name: /^Copy/ }) as HTMLButtonElement;
    expect(copy.disabled).toBe(true);
    await user.type(screen.getByLabelText("Platform experiment"), "evaluation");
    await user.type(screen.getByLabelText("DVC experiment"), "evaluation-v2");
    await user.type(screen.getByLabelText("DVC stage"), "train");
    await user.type(
      screen.getByLabelText("Input av_ IDs"),
      "av_01M0F1WMYDJVMTJB5V94KY7XR6 av_01M0F50QKC6KB2PYNAK8VRDNHQ",
    );
    const value = (screen.getByLabelText("Record a managed DVC Run command") as HTMLTextAreaElement).value;
    expect(value.indexOf("av_01M0F1WMYDJVMTJB5V94KY7XR6")).toBeLessThan(
      value.indexOf("av_01M0F50QKC6KB2PYNAK8VRDNHQ"),
    );
    expect(copy.disabled).toBe(false);
  });

  it("requires a committed model signature before enabling publication copy", async () => {
    const user = userEvent.setup();
    render(
      <PublicationCommandCard
        artifact={{ id: "ar_01M0F1WMYDJVMTJB5V94KY7XR6", name: "trained-model", kind: "model" }}
        runs={[{ id: "run_01M0F1WMYDJVMTJB5V94KY7XR6", state: "succeeded" }]}
      />,
    );
    await user.type(screen.getByLabelText("DVC stage"), "train");
    await user.type(screen.getByLabelText("Output path"), "models/model");
    await user.type(screen.getByLabelText("Producing Run (optional)"), "run_01M0F1WMYDJVMTJB5V94KY7XR6");
    await user.type(screen.getByLabelText("Model manifest"), "models/model/manifest.json");
    const command = (screen.getByLabelText("Publish trained-model command") as HTMLTextAreaElement).value;
    expect(command).toContain("--artifact 'trained-model'");
    expect(command).toContain("--signature 'model-signature.json'");
    expect((screen.getAllByRole("button", { name: /^Copy/ }).at(-1) as HTMLButtonElement).disabled).toBe(false);
  });
});
