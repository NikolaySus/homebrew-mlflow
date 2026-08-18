// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, suggestSlug } from "./main";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("project onboarding", () => {
  it("suggests a GitLab-safe editable slug", () => {
    expect(suggestSlug("Crème Protein Folding!")).toBe("creme-protein-folding");
  });

  it("claims an unclaimed installation without retaining the token", async () => {
    const requests: { url: string; body?: string }[] = [];
    let claimed = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input);
        requests.push({ url, body: init.body?.toString() });
        if (url.endsWith("/api/v1/auth/web/session"))
          return json({ access_token: "access" });
        if (url.endsWith("/api/v1/setup/status")) return json({ claimed });
        if (url.endsWith("/api/v1/me"))
          return json({
            principal_id: "principal_1",
            display_name: "Ada",
            organizations: claimed
              ? [{ resource_id: "org_1", role: "admin" }]
              : [],
          });
        if (url.endsWith("/api/v1/projects")) return json([]);
        if (url.endsWith("/api/v1/organization"))
          return claimed ? json({ id: "org_1", name: "Research" }) : json({}, 404);
        if (url.endsWith("/api/v1/setup/claim")) {
          claimed = true;
          return json({
            organization_id: "org_1",
            principal_id: "principal_1",
            role: "admin",
          });
        }
        throw new Error(`unexpected request ${url}`);
      }),
    );

    render(<App />);
    const user = userEvent.setup();
    await user.type(await screen.findByPlaceholderText("Organization name"), "Research");
    await user.type(screen.getByPlaceholderText("Bootstrap token"), "one-time-secret");
    await user.click(screen.getByRole("button", { name: "Claim installation" }));

    await screen.findByRole("heading", { name: "Create a research project" });
    const claim = requests.find((request) => request.url.endsWith("/api/v1/setup/claim"));
    expect(JSON.parse(claim?.body ?? "{}")).toEqual({
      organization_name: "Research",
      bootstrap_token: "one-time-secret",
    });
    await waitFor(() =>
      expect(screen.queryByDisplayValue("one-time-secret")).toBeNull(),
    );
  });

  it("creates a project and reveals the seeded GitLab repository", async () => {
    let created = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input);
        const method = init.method ?? "GET";
        if (url.endsWith("/api/v1/auth/web/session"))
          return json({ access_token: "access" });
        if (url.endsWith("/api/v1/setup/status")) return json({ claimed: true });
        if (url.endsWith("/api/v1/me"))
          return json({
            principal_id: "principal_1",
            display_name: "Ada",
            organizations: [{ resource_id: "org_1", role: "admin" }],
          });
        if (url.endsWith("/api/v1/organization"))
          return json({ id: "org_1", name: "Research" });
        if (url.endsWith("/api/v1/projects") && method === "POST") {
          created = true;
          return json(
            {
              id: "project_1",
              organization_id: "org_1",
              name: "Protein Folding",
              slug: "protein-folding",
              default_repository: {
                id: "repository_1",
                name: "Protein Folding",
                state: "provisioning",
                web_url: null,
                http_clone_url: null,
                ssh_clone_url: null,
                failure_code: null,
              },
            },
            202,
          );
        }
        if (url.endsWith("/api/v1/projects"))
          return json(
            created
              ? [
                  {
                    id: "project_1",
                    organization_id: "org_1",
                    name: "Protein Folding",
                    slug: "protein-folding",
                    state: "active",
                    archived_at: null,
                  },
                ]
              : [],
          );
        if (url.endsWith("/repositories"))
          return json([
            {
              id: "repository_1",
              name: "Protein Folding",
              state: "active",
              web_url: "https://git.example/research/protein-folding",
              http_clone_url: "https://git.example/research/protein-folding.git",
              ssh_clone_url: "git@git.example:research/protein-folding.git",
              failure_code: null,
            },
          ]);
        if (url.includes("/secret-context")) return json({}, 404);
        if (
          url.includes("/experiments") ||
          url.includes("/runs") ||
          url.includes("/artifacts") ||
          url.includes("/memberships") ||
          url.includes("/audit-events") ||
          url.includes("/principals") ||
          url.includes("/pipeline-definitions") ||
          url.includes("/environment-specifications") ||
          url.includes("/machine-credentials") ||
          url.includes("/shared-artifact-references")
        )
          return json([]);
        throw new Error(`unexpected request ${method} ${url}`);
      }),
    );

    render(<App />);
    const user = userEvent.setup();
    const name = await screen.findByPlaceholderText("Project name");
    await user.type(name, "Protein Folding");
    expect(screen.getByDisplayValue("protein-folding")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Create and provision" }));

    await screen.findByRole("link", { name: "Open in GitLab" });
    expect(screen.getByText("git@git.example:research/protein-folding.git")).not.toBeNull();
    const metadata = screen.getByRole("region", { name: "Research metadata" });
    expect(within(metadata).getByRole("heading", { name: "Experiments" })).not.toBeNull();
    expect(
      within(metadata).getByRole("heading", { name: "Pipeline definitions" }),
    ).not.toBeNull();
    expect(
      within(metadata).getByRole("heading", { name: "Environment specifications" }),
    ).not.toBeNull();
    expect(metadata.querySelectorAll(":scope > .overviewInfoGroup")).toHaveLength(3);
  });
});
