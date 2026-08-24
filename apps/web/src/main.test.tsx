// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  App,
  CompactAuditList,
  CompactMetadataList,
  ProjectChooser,
  suggestSlug,
} from "./main";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
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
    const popup = { location: { href: "" }, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
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
        if (url.endsWith("/api/v1/auth/mlflow/session") && method === "POST")
          return json({
            workspace_url:
              "/mlflow/?workspace=pr-01k00000000000000000000000#/experiments",
          });
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
        if (url.includes("/audit-events/page"))
          return json({ items: [], total_count: 0, next_before_sequence: null });
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
    expect(screen.queryByText("protein-folding")).toBeNull();
    expect(screen.getByText("git@git.example:research/protein-folding.git")).not.toBeNull();
    const repositoryList = screen.getByRole("list");
    expect(within(repositoryList).getAllByRole("listitem")).toHaveLength(1);
    const metadata = screen.getByRole("region", { name: "Research metadata" });
    expect(within(metadata).getByRole("heading", { name: "Experiments" })).not.toBeNull();
    expect(
      within(metadata).getByRole("heading", { name: "Pipeline definitions" }),
    ).not.toBeNull();
    expect(
      within(metadata).getByRole("heading", { name: "Environment specifications" }),
    ).not.toBeNull();
    expect(metadata.querySelectorAll(":scope > .overviewInfoGroup")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "Open in MLflow" }));
    await waitFor(() =>
      expect(popup.location.href).toBe(
        "/mlflow/?workspace=pr-01k00000000000000000000000#/experiments",
      ),
    );

    await user.click(screen.getByRole("button", { name: "Homebrew MLflow home" }));
    expect(await screen.findByRole("heading", { name: "Choose a research project" })).not.toBeNull();
    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toBe("");
  });
});

describe("project chooser onboarding", () => {
  it("shows instance-specific machine setup and the API reference without project workflows", () => {
    render(
      <ProjectChooser
        hasProjects
        installCommand={'uv tool install --force --no-build "homebrew-mlflow==0.2.9"'}
        installAvailable
      />,
    );

    const command = screen.getByLabelText("Set up this machine command") as HTMLTextAreaElement;
    expect(command.value).toContain("uv tool install --force --no-build");
    expect(command.value).toContain("homebrew-mlflow version");
    expect(command.value).toContain("homebrew-mlflow login --server 'http://localhost:3000'");
    const machineAccessNote = screen.getByText("Set up Git access on this client").parentElement;
    expect(machineAccessNote).not.toBeNull();
    expect(screen.getByText(/On every client machine, including your first/)).not.toBeNull();
    expect(screen.getByText(/GitLab SSH key authenticates Git/)).not.toBeNull();
    expect(machineAccessNote?.textContent).toContain(
      "homebrew-mlflow login separately authenticates",
    );
    const docs = screen.getByRole("link", { name: "API reference" });
    expect(docs.getAttribute("href")).toBe("/docs");
    expect(docs.getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("link", { name: /workflows/i })).toBeNull();
  });

  it("does not allow copying fallback text when release metadata is unavailable", () => {
    render(
      <ProjectChooser
        hasProjects={false}
        installCommand="CLI release metadata is unavailable."
        installAvailable={false}
      />,
    );

    expect((screen.getByRole("button", { name: /^Copy/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Recommended release metadata is unavailable/)).not.toBeNull();
  });
});

describe("compact Overview metadata", () => {
  const records = [
    { id: "record-2", created_at: "2026-08-22T10:00:00Z" },
    { id: "record-4", created_at: "2026-08-24T10:00:00Z" },
    { id: "record-1", created_at: "2026-08-21T10:00:00Z" },
    { id: "record-3", created_at: "2026-08-23T10:00:00Z" },
  ];

  it("shows the newest two, then expands and collapses the complete sorted list", async () => {
    const user = userEvent.setup();
    render(
      <CompactMetadataList
        items={records}
        label="records"
        getItemId={(item) => item.id}
        renderItem={(item) => <div key={item.id} data-testid="metadata-record">{item.id}</div>}
      />,
    );

    expect(screen.getAllByTestId("metadata-record").map((item) => item.textContent)).toEqual([
      "record-4",
      "record-3",
    ]);
    expect(screen.getByText("…and 2 more")).not.toBeNull();
    expect(screen.queryByText("record-2")).toBeNull();
    expect(screen.queryByText("record-1")).toBeNull();
    const expand = screen.getByRole("button", { name: "Show all 4 records" });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(expand.textContent).toBe("⌄");

    await user.click(expand);
    expect(screen.getAllByTestId("metadata-record").map((item) => item.textContent)).toEqual([
      "record-4",
      "record-3",
      "record-2",
      "record-1",
    ]);
    const collapse = screen.getByRole("button", { name: "Show fewer records" });
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("All 4 shown")).not.toBeNull();
    await user.click(collapse);
    expect(screen.getAllByTestId("metadata-record")).toHaveLength(2);
  });

  it("does not show a disclosure for two records", () => {
    render(
      <CompactMetadataList
        items={records.slice(0, 2)}
        label="records"
        getItemId={(item) => item.id}
        renderItem={(item) => <div key={item.id}>{item.id}</div>}
      />,
    );

    expect(screen.queryByRole("button", { name: /records/ })).toBeNull();
  });

  it("sorts records whose stable identity is supplied by an accessor", () => {
    const principals = [
      { principal_id: "principal-a", created_at: "2026-08-22T10:00:00Z" },
      { principal_id: "principal-c", created_at: "2026-08-24T10:00:00Z" },
      { principal_id: "principal-b", created_at: "2026-08-23T10:00:00Z" },
    ];
    render(
      <CompactMetadataList
        items={principals}
        label="principals"
        getItemId={(item) => item.principal_id}
        renderItem={(item) => <div key={item.principal_id}>{item.principal_id}</div>}
      />,
    );

    expect(screen.getByText("principal-c")).not.toBeNull();
    expect(screen.getByText("principal-b")).not.toBeNull();
    expect(screen.queryByText("principal-a")).toBeNull();
  });
});

describe("compact paged audit metadata", () => {
  const events = [4, 3, 2, 1].map((sequence) => ({
    sequence,
    occurred_at: `2026-08-2${sequence}T10:00:00Z`,
    actor_principal_id: "principal_1",
    action: `action-${sequence}`,
    outcome: sequence === 3 ? "failed" : "succeeded",
    resource_id: `resource-${sequence}`,
    safe_metadata: {},
  }));

  it("shows the latest two, loads older pages, and collapses", async () => {
    const user = userEvent.setup();
    const loadOlder = vi.fn();
    const { rerender } = render(
      <CompactAuditList
        items={events.slice(0, 2)}
        totalCount={4}
        nextBeforeSequence={3}
        loading={false}
        onLoadOlder={loadOlder}
      />,
    );

    expect(screen.getByText("action-4")).not.toBeNull();
    expect(screen.getByText("action-3")).not.toBeNull();
    expect(screen.queryByText("action-2")).toBeNull();
    expect(screen.getByText("…and 2 more")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Show audit history (4 events)" }));
    expect(screen.getByText("2 older not loaded")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Load older audit events" }));
    expect(loadOlder).toHaveBeenCalledOnce();

    rerender(
      <CompactAuditList
        items={events}
        totalCount={4}
        nextBeforeSequence={null}
        loading={false}
        onLoadOlder={loadOlder}
      />,
    );
    expect(screen.getByText("All 4 shown")).not.toBeNull();
    expect(screen.getByText("action-1")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Show fewer audit events" }));
    expect(screen.queryByText("action-2")).toBeNull();
  });
});
