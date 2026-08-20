import React, { FormEvent, useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";

type Project = {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  state: string;
  archived_at: string | null;
};
type SetupStatus = { claimed: boolean };
type Me = {
  principal_id: string;
  display_name: string;
  organizations: { resource_id: string; role: string }[];
};
type Organization = { id: string; name: string };
type Repository = {
  id: string;
  name: string;
  state: string;
  web_url: string | null;
  http_clone_url: string | null;
  ssh_clone_url: string | null;
  failure_code: string | null;
};
type Experiment = {
  id: string;
  name: string;
  created_at: string;
  archived_at: string | null;
};
type Run = {
  id: string;
  state: string;
  experiment_id: string;
  repository_id: string;
  pipeline_version_id: string | null;
  environment_specification_id: string | null;
  command: string[];
  ended_at: string | null;
  provenance_status: "pending" | "complete" | "incomplete" | "invalid";
  dvc_experiment_revision: string | null;
};
type Metric = {
  key: string;
  value: number;
  timestamp_ms: number;
  step: number;
};
type RunDetail = {
  run: Run;
  created_at: string;
  started_at: string | null;
  git_commit_sha: string | null;
  provenance_status: "pending" | "complete" | "incomplete" | "invalid";
  dvc_experiment_revision: string | null;
  finalization_evidence: Record<string, unknown> | null;
  input_artifact_version_ids: string[];
  output_artifact_version_ids: string[];
  parameters: { key: string; value: string }[];
  metrics: Metric[];
  tags: { key: string; value: string }[];
};
type ArtifactKind = "dataset" | "model" | "checkpoint" | "report" | "generic";
type Artifact = {
  id: string;
  name: string;
  kind: ArtifactKind;
  description: string | null;
  created_at: string;
};
type ArtifactAlias = { alias: string; artifact_version_id: string };
type Version = {
  id: string;
  artifact_id: string;
  owning_project_id: string;
  digest: string;
  algorithm: string;
  output_kind: string;
  size: number;
  file_count: number;
  integrity: string;
  availability: string;
  published_at: string;
  sequence: number;
  mlflow_model_id: string;
  producing_run_id: string | null;
  model_signature: Record<string, unknown> | null;
  model_signature_sha256: string | null;
};
type ArtifactFile = { path: string; size: number; digest: string | null };
type Lineage = {
  id: string;
  source_artifact_version_id: string;
  derived_artifact_version_id: string;
  created_at: string;
};
type Grant = {
  id: string;
  artifact_version_id: string;
  consuming_project_id: string;
  effective_at: string;
  revoked_at: string | null;
};
type Membership = {
  principal_id: string;
  display_name: string;
  principal_kind: string;
  gitlab_username: string | null;
  role: string;
};
type OrganizationPrincipal = {
  principal_id: string;
  display_name: string;
  principal_kind: string;
  gitlab_username: string | null;
  organization_role: string | null;
};
type SecretContext = {
  infisical_project_id: string;
  environment_slug: string;
  secret_path: string;
  reconciliation_state: string;
  last_error_code: string | null;
};
type AuditEvent = {
  sequence: number;
  occurred_at: string;
  actor_principal_id: string | null;
  action: string;
  outcome: string;
  resource_id: string | null;
  safe_metadata: Record<string, unknown>;
};
type Consumption = { bash_commands: string[]; powershell_commands: string[] };
type PipelineDefinition = {
  id: string;
  project_id: string;
  name: string;
  created_at: string;
  archived_at: string | null;
};
type PipelineVersion = {
  id: string;
  definition_id: string;
  repository_id: string;
  git_commit_sha: string;
  pipeline_path: string;
  content_sha256: string;
  created_at: string;
  archived_at: string | null;
};
type EnvironmentSpecification = {
  id: string;
  project_id: string;
  name: string;
  kind: "uv" | "pip" | "conda" | "container" | "system";
  document: Record<string, unknown>;
  sha256: string;
  created_at: string;
  archived_at: string | null;
};
type MachineCredential = {
  credential_id: string;
  principal_id: string;
  project_id: string;
  scopes: string[];
  revoked: boolean;
  expires_at: string;
};
type RetentionDependencies = {
  blockers: string[];
  retained_runs: number;
  shared_references: number;
  derivatives: number;
  active_grants: number;
  replicas: number;
  aliases: number;
  legal_hold: boolean;
};
type SharedReference = {
  id: string;
  artifact_version_id: string;
  grant_id: string;
  consuming_project_id: string;
  run_id: string | null;
  created_at: string;
};
type Tab = "overview" | "runs" | "artifacts" | "access";

function csrfToken() {
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith("hm_csrf="));
  return value ? decodeURIComponent(value.slice("hm_csrf=".length)) : "";
}

export function suggestSlug(name: string) {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function App() {
  const initialQuery = useMemo(() => new URLSearchParams(window.location.search), []);
  const [token, setToken] = useState("");
  const [sessionChecked, setSessionChecked] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [setupClaimed, setSetupClaimed] = useState<boolean | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [projectBusy, setProjectBusy] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectSlug, setNewProjectSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [projectId, setProjectId] = useState(initialQuery.get("project") ?? "");
  const [tab, setTab] = useState<Tab>(initialQuery.has("artifact") ? "artifacts" : "overview");
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [artifactId, setArtifactId] = useState(initialQuery.get("artifact") ?? "");
  const [deepLinkedVersionId, setDeepLinkedVersionId] = useState(
    initialQuery.get("version") ?? "",
  );
  const [artifactKind, setArtifactKind] = useState<ArtifactKind | "all">("all");
  const [artifactAliases, setArtifactAliases] = useState<ArtifactAlias[]>([]);
  const [version, setVersion] = useState<Version | null>(null);
  const [files, setFiles] = useState<ArtifactFile[]>([]);
  const [lineage, setLineage] = useState<Lineage[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [consumption, setConsumption] = useState<Consumption | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [organizationPrincipals, setOrganizationPrincipals] = useState<
    OrganizationPrincipal[]
  >([]);
  const [secretContext, setSecretContext] = useState<SecretContext | null>(
    null,
  );
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [pipelineVersions, setPipelineVersions] = useState<PipelineVersion[]>(
    [],
  );
  const [environments, setEnvironments] = useState<EnvironmentSpecification[]>(
    [],
  );
  const [machines, setMachines] = useState<MachineCredential[]>([]);
  const [machineSecret, setMachineSecret] = useState("");
  const [retention, setRetention] = useState<RetentionDependencies | null>(
    null,
  );
  const [sharedReferences, setSharedReferences] = useState<SharedReference[]>(
    [],
  );
  const [publicationLog, setPublicationLog] = useState<string[]>([]);
  const [error, setError] = useState("");

  async function refreshBrowserSession() {
    const response = await fetch("/api/v1/auth/web/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken() },
    });
    if (!response.ok) return "";
    const value = (await response.json()).access_token as string;
    setToken(value);
    return value;
  }

  async function request<T>(
    path: string,
    init: RequestInit = {},
    bearer = token,
  ): Promise<T> {
    const invoke = (value: string) =>
      fetch(path, {
        ...init,
        headers: {
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...(init.headers ?? {}),
          Authorization: `Bearer ${value}`,
        },
      });
    let response = await invoke(bearer);
    if (response.status === 401 && bearer === token) {
      const refreshed = await refreshBrowserSession();
      if (refreshed) response = await invoke(refreshed);
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(
        `${response.status} ${body?.error?.code ?? response.statusText}`,
      );
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  async function scopedToken(audience: string, scope: string) {
    const result = await request<{ access_token: string }>(
      "/api/v1/auth/exchange",
      {
        method: "POST",
        body: JSON.stringify({
          audience,
          project_id: projectId,
          scopes: [scope],
        }),
      },
    );
    return result.access_token;
  }

  async function openMlflow() {
    if (!selected || selected.state !== "active") return;
    const target = window.open("about:blank", "_blank");
    try {
      const result = await request<{ workspace_url: string }>(
        "/api/v1/auth/mlflow/session",
        {
          method: "POST",
          body: JSON.stringify({ project_id: selected.id }),
        },
      );
      if (target) target.location.href = result.workspace_url;
      else window.location.href = result.workspace_url;
    } catch (caught) {
      target?.close();
      showError(caught);
    }
  }

  useEffect(() => {
    refreshBrowserSession().finally(() => setSessionChecked(true));
  }, []);
  useEffect(() => {
    if (!token) return;
    Promise.all([
      request<SetupStatus>("/api/v1/setup/status"),
      request<Me>("/api/v1/me"),
      request<Project[]>("/api/v1/projects"),
      request<Organization>("/api/v1/organization").catch(() => null),
    ])
      .then(([setup, identity, projectValues, organizationValue]) => {
        setSetupClaimed(setup.claimed);
        setMe(identity);
        setProjects(projectValues);
        setOrganization(organizationValue);
      })
      .catch(showError);
  }, [token]);
  useEffect(() => {
    if (!projectId) return;
    setRunDetail(null);
    setVersion(null);
    setPublicationLog([]);
    Promise.all([
      request<Repository[]>(`/api/v1/projects/${projectId}/repositories`),
      request<Experiment[]>(`/api/v1/projects/${projectId}/experiments`),
      request<Run[]>(`/api/v1/projects/${projectId}/runs`),
      request<Artifact[]>(`/api/v1/projects/${projectId}/artifacts`),
      request<Membership[]>(`/api/v1/projects/${projectId}/memberships`),
      request<AuditEvent[]>(`/api/v1/projects/${projectId}/audit-events`),
      request<SecretContext>(
        `/api/v1/projects/${projectId}/secret-context`,
      ).catch(() => null),
      request<OrganizationPrincipal[]>(
        `/api/v1/organizations/${projects.find((item) => item.id === projectId)?.organization_id}/principals`,
      ).catch(() => []),
      request<PipelineDefinition[]>(
        `/api/v1/projects/${projectId}/pipeline-definitions`,
      ),
      request<EnvironmentSpecification[]>(
        `/api/v1/projects/${projectId}/environment-specifications`,
      ),
      request<MachineCredential[]>(
        `/api/v1/projects/${projectId}/machine-credentials`,
      ).catch(() => []),
      request<SharedReference[]>(
        `/api/v1/projects/${projectId}/shared-artifact-references`,
      ).catch(() => []),
    ])
      .then(
        ([
          repos,
          exps,
          runValues,
          artifactValues,
          members,
          events,
          context,
          principals,
          pipelineValues,
          environmentValues,
          machineValues,
          referenceValues,
        ]) => {
          setRepositories(repos);
          setExperiments(exps);
          setRuns(runValues);
          setArtifacts(artifactValues);
          setMemberships(members);
          setAudit(events);
          setSecretContext(context);
          setOrganizationPrincipals(principals);
          setPipelines(pipelineValues);
          setEnvironments(environmentValues);
          setMachines(machineValues);
          setSharedReferences(referenceValues);
          setError("");
        },
      )
      .catch(showError);
  }, [projectId, token, projects]);
  useEffect(() => {
    if (!artifactId) {
      setVersions([]);
      return;
    }
    Promise.all([
      request<Version[]>(`/api/v1/artifacts/${artifactId}/versions`),
      request<ArtifactAlias[]>(`/api/v1/artifacts/${artifactId}/aliases`),
    ])
      .then(([values, aliases]) => {
        setVersions(values);
        setArtifactAliases(aliases);
      })
      .catch(showError);
  }, [artifactId]);

  useEffect(() => {
    if (!deepLinkedVersionId || version || versions.length === 0) return;
    const target = versions.find((value) => value.id === deepLinkedVersionId);
    setDeepLinkedVersionId("");
    if (target) void chooseVersion(target);
  }, [deepLinkedVersionId, version, versions]);

  function showError(value: unknown) {
    setError(String(value));
  }
  const selected = projects.find((project) => project.id === projectId);
  const organizationRole = me?.organizations.find(
    (binding) => binding.resource_id === organization?.id,
  )?.role;
  const canCreateProject = organizationRole === "admin";
  const currentMembership = memberships.find(
    (membership) => membership.principal_id === me?.principal_id,
  );
  const canManageArtifacts = currentMembership?.role === "maintainer";
  const experimentNames = useMemo(
    () => new Map(experiments.map((item) => [item.id, item.name])),
    [experiments],
  );

  useEffect(() => {
    if (!selected || selected.state !== "provisioning") return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      Promise.all([
        request<Project[]>("/api/v1/projects"),
        request<Repository[]>(`/api/v1/projects/${selected.id}/repositories`),
      ])
        .then(([projectValues, repositoryValues]) => {
          setProjects(projectValues);
          setRepositories(repositoryValues);
          if (Date.now() - started > 300_000) {
            setError("Provisioning is taking longer than expected; status polling continues.");
          }
        })
        .catch(showError);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [selected?.id, selected?.state, token]);

  async function claimInstallation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      setProjectBusy(true);
      await request("/api/v1/setup/claim", {
        method: "POST",
        body: JSON.stringify({
          organization_name: String(data.get("organization")).trim(),
          bootstrap_token: String(data.get("bootstrap_token")),
        }),
      });
      form.reset();
      const [identity, organizationValue] = await Promise.all([
        request<Me>("/api/v1/me"),
        request<Organization>("/api/v1/organization"),
      ]);
      setMe(identity);
      setOrganization(organizationValue);
      setSetupClaimed(true);
      setError("");
    } catch (value) {
      showError(value);
    } finally {
      setProjectBusy(false);
    }
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!organization) return;
    const data = new FormData(event.currentTarget);
    try {
      setProjectBusy(true);
      const created = await request<{
        id: string;
        organization_id: string;
        name: string;
        slug: string;
        default_repository: Repository;
      }>("/api/v1/projects", {
        method: "POST",
        body: JSON.stringify({
          organization_id: organization.id,
          name: String(data.get("name")).trim(),
          slug: String(data.get("slug")).trim(),
        }),
      });
      setProjects(await request<Project[]>("/api/v1/projects"));
      setProjectId(created.id);
      setRepositories([created.default_repository]);
      setShowProjectForm(false);
      setError("");
    } catch (value) {
      showError(value);
    } finally {
      setProjectBusy(false);
    }
  }

  async function retryProvisioning(repositoryId: string) {
    if (!selected) return;
    try {
      await request(
        `/api/v1/projects/${selected.id}/repositories/${repositoryId}/retry-provisioning`,
        { method: "POST" },
      );
      const [projectValues, repositoryValues] = await Promise.all([
        request<Project[]>("/api/v1/projects"),
        request<Repository[]>(`/api/v1/projects/${selected.id}/repositories`),
      ]);
      setProjects(projectValues);
      setRepositories(repositoryValues);
      setError("");
    } catch (value) {
      showError(value);
    }
  }

  async function chooseRun(run: Run) {
    try {
      setRunDetail(await request<RunDetail>(`/api/v1/runs/${run.id}`));
      setError("");
    } catch (value) {
      showError(value);
    }
  }
  async function chooseVersion(value: Version) {
    try {
      const [nextFiles, edges, nextGrants, commands, dependencies] =
        await Promise.all([
          request<ArtifactFile[]>(
            `/api/v1/artifact-versions/${value.id}/files`,
          ),
          request<Lineage[]>(`/api/v1/artifact-versions/${value.id}/lineage`),
          request<Grant[]>(
            `/api/v1/artifact-versions/${value.id}/sharing-grants`,
          ),
          request<Consumption>(
            `/api/v1/artifact-versions/${value.id}/consumption`,
          ),
          request<RetentionDependencies>(
            `/api/v1/artifact-versions/${value.id}/retention-dependencies`,
          ),
        ]);
      setVersion(value);
      setFiles(nextFiles);
      setLineage(edges);
      setGrants(nextGrants);
      setConsumption(commands);
      setRetention(dependencies);
      const query = new URLSearchParams({
        project: value.owning_project_id,
        artifact: value.artifact_id,
        version: value.id,
      });
      window.history.replaceState(null, "", `/?${query.toString()}`);
      setError("");
    } catch (problem) {
      showError(problem);
    }
  }

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/v1/projects/${projectId}/runs`, {
        method: "POST",
        body: JSON.stringify({
          repository_id: data.get("repository"),
          experiment_name: data.get("experiment"),
          command: String(data.get("command")).trim().split(/\s+/),
          pipeline_version_id: data.get("pipeline_version") || null,
          environment_specification_id: data.get("environment") || null,
        }),
      });
      setRuns(await request(`/api/v1/projects/${projectId}/runs`));
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function createArtifact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/v1/projects/${projectId}/artifacts`, {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          kind: data.get("kind"),
          description: data.get("description") || null,
        }),
      });
      setArtifacts(await request(`/api/v1/projects/${projectId}/artifacts`));
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function updateArtifactMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const selectedArtifact = artifacts.find((item) => item.id === artifactId);
    if (!selectedArtifact) return;
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/v1/artifacts/${artifactId}`, {
        method: "PATCH",
        body: JSON.stringify({
          kind: data.get("kind"),
          description: data.get("description") || null,
        }),
      });
      setArtifacts(await request(`/api/v1/projects/${projectId}/artifacts`));
    } catch (value) {
      showError(value);
    }
  }

  async function setArtifactAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const alias = String(data.get("alias"));
    try {
      await request(`/api/v1/artifacts/${artifactId}/aliases/${alias}`, {
        method: "PUT",
        body: JSON.stringify({ artifact_version_id: data.get("version") }),
      });
      setArtifactAliases(
        await request(`/api/v1/artifacts/${artifactId}/aliases`),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function deleteArtifactAlias(alias: string) {
    try {
      await request(`/api/v1/artifacts/${artifactId}/aliases/${alias}`, {
        method: "DELETE",
      });
      setArtifactAliases(
        await request(`/api/v1/artifacts/${artifactId}/aliases`),
      );
    } catch (value) {
      showError(value);
    }
  }

  async function createGrant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!version) return;
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/v1/artifact-versions/${version.id}/sharing-grants`, {
        method: "POST",
        body: JSON.stringify({ consuming_project_id: data.get("project") }),
      });
      setGrants(
        await request(`/api/v1/artifact-versions/${version.id}/sharing-grants`),
      );
    } catch (value) {
      showError(value);
    }
  }

  async function revokeGrant(id: string) {
    try {
      await request(`/api/v1/sharing-grants/${id}`, { method: "DELETE" });
      if (version)
        setGrants(
          await request(
            `/api/v1/artifact-versions/${version.id}/sharing-grants`,
          ),
        );
    } catch (value) {
      showError(value);
    }
  }

  async function createSharedReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/v1/projects/${projectId}/shared-artifact-references`, {
        method: "POST",
        body: JSON.stringify({
          artifact_version_id: data.get("artifact_version_id"),
          run_id: data.get("run_id") || null,
        }),
      });
      setSharedReferences(
        await request(`/api/v1/projects/${projectId}/shared-artifact-references`),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function createDerivation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const derived = String(data.get("derived_version_id"));
    try {
      await request(`/api/v1/artifact-versions/${derived}/derivations`, {
        method: "POST",
        body: JSON.stringify({
          source_artifact_version_id: data.get("source_version_id"),
        }),
      });
      if (version) {
        setLineage(
          await request(`/api/v1/artifact-versions/${version.id}/lineage`),
        );
      }
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function setMembership(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await request(
        `/api/v1/projects/${projectId}/memberships/${data.get("principal")}`,
        { method: "PUT", body: JSON.stringify({ role: data.get("role") }) },
      );
      setMemberships(
        await request(`/api/v1/projects/${projectId}/memberships`),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function removeMembership(principalId: string) {
    try {
      await request(`/api/v1/projects/${projectId}/memberships/${principalId}`, {
        method: "DELETE",
      });
      setMemberships(await request(`/api/v1/projects/${projectId}/memberships`));
    } catch (value) {
      showError(value);
    }
  }

  async function recoverMaintainer(principalId: string) {
    try {
      await request(
        `/api/v1/projects/${projectId}/memberships/${principalId}/recover-maintainer`,
        { method: "POST" },
      );
      setMemberships(await request(`/api/v1/projects/${projectId}/memberships`));
    } catch (value) {
      showError(value);
    }
  }

  async function archiveRepository(repositoryId: string) {
    try {
      await request(`/api/v1/projects/${projectId}/repositories/${repositoryId}`, {
        method: "DELETE",
      });
      setRepositories(await request(`/api/v1/projects/${projectId}/repositories`));
    } catch (value) {
      showError(value);
    }
  }

  async function archiveExperiment(experimentId: string) {
    try {
      await request(`/api/v1/projects/${projectId}/experiments/${experimentId}`, {
        method: "DELETE",
      });
      setExperiments(await request(`/api/v1/projects/${projectId}/experiments`));
    } catch (value) {
      showError(value);
    }
  }

  async function setOrganizationMembership(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const data = new FormData(event.currentTarget);
    try {
      await request(
        `/api/v1/organizations/${selected.organization_id}/memberships/${data.get("principal")}`,
        { method: "PUT", body: JSON.stringify({ role: data.get("role") }) },
      );
      setOrganizationPrincipals(
        await request(
          `/api/v1/organizations/${selected.organization_id}/principals`,
        ),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function createPipeline(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/v1/projects/${projectId}/pipeline-definitions`, {
        method: "POST",
        body: JSON.stringify({ name: data.get("name") }),
      });
      setPipelines(
        await request(`/api/v1/projects/${projectId}/pipeline-definitions`),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function registerPipelineVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const definition = String(data.get("definition"));
    try {
      await request(`/api/v1/pipeline-definitions/${definition}/versions`, {
        method: "POST",
        body: JSON.stringify({
          repository_id: data.get("repository"),
          git_commit_sha: data.get("commit"),
          pipeline_path: data.get("path"),
        }),
      });
      setPipelineVersions(
        await request(`/api/v1/pipeline-definitions/${definition}/versions`),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function createEnvironment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const document = JSON.parse(String(data.get("document"))) as Record<
        string,
        unknown
      >;
      await request(
        `/api/v1/projects/${projectId}/environment-specifications`,
        {
          method: "POST",
          body: JSON.stringify({
            name: data.get("name"),
            kind: data.get("kind"),
            document,
          }),
        },
      );
      setEnvironments(
        await request(
          `/api/v1/projects/${projectId}/environment-specifications`,
        ),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function createMachine(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const created = await request<MachineCredential & { secret: string }>(
        `/api/v1/projects/${projectId}/machine-credentials`,
        {
          method: "POST",
          body: JSON.stringify({
            display_name: data.get("name"),
            role: data.get("role"),
            scopes:
              data.get("role") === "viewer"
                ? ["read"]
                : ["read", "track", "dvc_transfer", "publish"],
          }),
        },
      );
      setMachineSecret(created.secret);
      setMachines(
        await request(`/api/v1/projects/${projectId}/machine-credentials`),
      );
      event.currentTarget.reset();
    } catch (value) {
      showError(value);
    }
  }

  async function revokeMachine(credentialId: string) {
    try {
      await request(`/api/v1/machine-credentials/${credentialId}`, {
        method: "DELETE",
      });
      setMachines(
        await request(`/api/v1/projects/${projectId}/machine-credentials`),
      );
    } catch (value) {
      showError(value);
    }
  }

  async function toggleProjectLifecycle() {
    if (!selected) return;
    try {
      await request(
        `/api/v1/projects/${selected.id}${selected.state === "archived" ? "/restore" : ""}`,
        { method: selected.state === "archived" ? "POST" : "DELETE" },
      );
      setProjects(await request("/api/v1/projects"));
    } catch (value) {
      showError(value);
    }
  }

  async function configureSecrets(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      setSecretContext(
        await request(`/api/v1/projects/${projectId}/secret-context`, {
          method: "PUT",
          body: JSON.stringify({
            infisical_project_id: data.get("infisical_project_id"),
            environment_slug: data.get("environment_slug"),
            secret_path: data.get("secret_path"),
          }),
        }),
      );
    } catch (value) {
      showError(value);
    }
  }

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const publicationToken = await scopedToken("publication", "publish");
      const pipeline = String(data.get("pipeline") ?? "");
      const body = {
        artifact_id: data.get("artifact"),
        repository_id: data.get("repository"),
        commit_sha: data.get("commit"),
        selector: pipeline
          ? {
              kind: "pipeline-output",
              pipeline_file: pipeline,
              stage: data.get("stage"),
              output: data.get("output"),
            }
          : {
              kind: "standalone-output",
              dvc_file: data.get("dvc_file"),
              output: data.get("output"),
            },
        client: { name: "homebrew-mlflow-web", version: "0.1.0" },
      };
      const operation = await request<{
        events_url: string;
        operation_id: string;
      }>(
        `/api/v1/projects/${projectId}/publication-operations`,
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify(body),
        },
        publicationToken,
      );
      setPublicationLog([`Queued ${operation.operation_id}`]);
      await watchPublication(
        operation.events_url,
        publicationToken,
        setPublicationLog,
      );
    } catch (value) {
      showError(value);
    }
  }

  if (!sessionChecked)
    return (
      <main className="login">
        <p className="eyebrow">Opening archive…</p>
      </main>
    );
  if (!token)
    return (
      <main className="login">
        <p className="eyebrow">SELF-HOSTED RESEARCH ARCHIVE</p>
        <h1>Homebrew MLflow</h1>
        <p className="lede">
          Sign in through the deployment’s GitLab identity. A short-lived access
          token remains only in memory; the rotating session stays in an
          HttpOnly cookie.
        </p>
        <a className="primary" href="/api/v1/auth/web/start">
          Continue with GitLab
        </a>
      </main>
    );
  if (setupClaimed === null)
    return (
      <main className="login">
        <p className="eyebrow">Loading platform state…</p>
      </main>
    );
  if (!setupClaimed)
    return (
      <main className="login">
        <p className="eyebrow">FIRST-RUN SETUP</p>
        <h1>Claim this installation</h1>
        <p className="lede">
          Create the first organization and make your GitLab identity its
          administrator. The bootstrap token is submitted once and is never
          stored in the browser.
        </p>
        {error && <div className="error">{error}</div>}
        <form className="claimForm" onSubmit={claimInstallation}>
          <input
            name="organization"
            placeholder="Organization name"
            required
            maxLength={200}
          />
          <input
            name="bootstrap_token"
            type="password"
            autoComplete="off"
            placeholder="Bootstrap token"
            required
          />
          <button disabled={projectBusy}>Claim installation</button>
        </form>
      </main>
    );
  if (!organization)
    return (
      <main className="login">
        <p className="eyebrow">ACCESS PENDING</p>
        <h1>Organization membership required</h1>
        <p className="lede">
          The installation is already claimed. Ask an organization
          administrator to enroll this GitLab account.
        </p>
      </main>
    );

  const projectForm = (
    <section className="createPanel">
      <p className="eyebrow">{organization.name}</p>
      <h2>Create a research project</h2>
      <p className="muted">
        The platform will create a private GitLab repository and commit the
        managed research template automatically.
      </p>
      <form className="stackForm" onSubmit={createProject}>
        <input
          name="name"
          placeholder="Project name"
          value={newProjectName}
          onChange={(event) => {
            setNewProjectName(event.target.value);
            if (!slugEdited) setNewProjectSlug(suggestSlug(event.target.value));
          }}
          required
          maxLength={200}
        />
        <input
          name="slug"
          placeholder="project-slug"
          value={newProjectSlug}
          onChange={(event) => {
            setSlugEdited(true);
            setNewProjectSlug(event.target.value.toLowerCase());
          }}
          pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
          required
          maxLength={100}
        />
        <button disabled={projectBusy}>Create and provision</button>
      </form>
      {projects.length > 0 && (
        <button className="linkButton" onClick={() => setShowProjectForm(false)}>
          cancel
        </button>
      )}
    </section>
  );

  return (
    <div className="shell">
      <aside>
        <p className="brand">
          Homebrew
          <br />
          MLflow
        </p>
        <p className="label">Research projects</p>
        {projects.map((project) => (
          <button
            className={project.id === projectId ? "selected" : "nav"}
            key={project.id}
            onClick={() => {
              setProjectId(project.id);
              setArtifactId("");
              setVersion(null);
              window.history.replaceState(null, "", `/?project=${encodeURIComponent(project.id)}`);
            }}
          >
            {project.name}
            <small>{project.state}</small>
          </button>
        ))}
        {canCreateProject && (
          <button className="newProject" onClick={() => setShowProjectForm(true)}>
            + New project
          </button>
        )}
        <a className="docs" href="/docs">
          API reference
        </a>
        <button
          className="logout"
          onClick={async () => {
            await fetch("/api/v1/auth/web/logout", {
              method: "POST",
              credentials: "same-origin",
              headers: { "X-CSRF-Token": csrfToken() },
            });
            setToken("");
          }}
        >
          Sign out
        </button>
      </aside>
      <main className="workspace">
        {(showProjectForm || (!selected && projects.length === 0 && canCreateProject)) &&
          projectForm}
        {!selected ? (
          <div className="empty">
            <h2>{projects.length ? "Choose a research project" : "No research projects yet"}</h2>
            <p>
              Runs and immutable outputs stay grouped by their canonical
              project.
            </p>
          </div>
        ) : (
          <>
            <header>
              <div>
                <p className="eyebrow">{selected.slug}</p>
                <h2>{selected.name}</h2>
              </div>
              <div>
                <code>{selected.id}</code>
                {selected.state === "active" && (
                  <button className="linkButton" onClick={openMlflow}>
                    Open in MLflow
                  </button>
                )}
                <button className="linkButton" onClick={toggleProjectLifecycle}>
                  {selected.state === "archived" ? "restore" : "archive"}
                </button>
              </div>
            </header>
            <nav className="tabs">
              {(["overview", "runs", "artifacts", "access"] as Tab[]).map(
                (value) => (
                  <button
                    key={value}
                    className={tab === value ? "active" : ""}
                    onClick={() => setTab(value)}
                  >
                    {value}
                  </button>
                ),
              )}
            </nav>
            {error && <div className="error">{error}</div>}
            {tab === "overview" && (
              <div className="overview">
                <section>
                  <Title
                    title="Hosted repositories"
                    count={repositories.length}
                  />
                  <div className="repositoryList" role="list">
                    {repositories.map((repo) => (
                      <article className="repositoryItem" key={repo.id} role="listitem">
                        <div className="repositoryIdentity">
                          <div>
                            <strong>{repo.name}</strong>
                            <span className={`state ${repo.state}`}>
                              {repo.state}
                            </span>
                          </div>
                          <code>{repo.id}</code>
                        </div>
                        <div className="repositoryLocation">
                          {repo.web_url && (
                            <a href={repo.web_url}>Open in GitLab</a>
                          )}
                          {repo.ssh_clone_url && <code>{repo.ssh_clone_url}</code>}
                          {repo.failure_code && <small>{repo.failure_code}</small>}
                        </div>
                        <div className="repositoryActions">
                          {repo.state === "failed" &&
                            memberships.some(
                              (membership) =>
                                membership.principal_id === me?.principal_id &&
                                membership.role === "maintainer",
                            ) && (
                              <button
                                className="linkButton"
                                onClick={() => retryProvisioning(repo.id)}
                              >
                                retry provisioning
                              </button>
                            )}
                          {(repo.state === "active" || repo.state === "failed") && (
                            <button
                              className="linkButton"
                              onClick={() => archiveRepository(repo.id)}
                            >
                              archive
                            </button>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
                <section className="overviewInfo" aria-label="Research metadata">
                  <div className="overviewInfoGroup">
                    <Title title="Experiments" count={experiments.length} />
                    <div className="overviewInfoBody">
                      <div className="metadataList">
                        {experiments.map((experiment) => (
                          <div className="metadataItem" key={experiment.id}>
                            <strong>{experiment.name}</strong>
                            <code>{experiment.id}</code>
                            <small>
                              {new Date(experiment.created_at).toLocaleString()}
                            </small>
                            <button
                              className="linkButton"
                              onClick={() => archiveExperiment(experiment.id)}
                            >
                              archive
                            </button>
                          </div>
                        ))}
                      </div>
                      {experiments.length === 0 && (
                        <p className="hint compactHint">
                          Experiments appear when the first Run is created.
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="overviewInfoGroup">
                    <Title
                      title="Pipeline definitions"
                      count={pipelines.length}
                    />
                    <div className="overviewInfoBody">
                      <div className="metadataList">
                        {pipelines.map((pipeline) => (
                          <button
                            className="metadataItem metadataButton"
                            key={pipeline.id}
                            onClick={async () =>
                              setPipelineVersions(
                                await request(
                                  `/api/v1/pipeline-definitions/${pipeline.id}/versions`,
                                ),
                              )
                            }
                          >
                            <strong>{pipeline.name}</strong>
                            <code>{pipeline.id}</code>
                            <small>view immutable versions</small>
                          </button>
                        ))}
                      </div>
                      <p className="hint compactHint">
                        Discovered from committed <code>dvc.yaml</code> files when
                        Runs finalize.
                      </p>
                      {pipelineVersions.map((item) => (
                        <div className="versionSummary" key={item.id}>
                          <code>{item.id}</code>
                          <span>
                            {item.pipeline_path} @ {item.git_commit_sha.slice(0, 10)}
                          </span>
                          <small>sha256:{item.content_sha256.slice(0, 12)}</small>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="overviewInfoGroup">
                    <Title
                      title="Environment specifications"
                      count={environments.length}
                    />
                    <div className="overviewInfoBody">
                      <div className="metadataList">
                        {environments.map((item) => (
                          <div className="metadataItem" key={item.id}>
                            <strong>{item.name}</strong>
                            <span className="state compactState">{item.kind}</span>
                            <code>{item.id}</code>
                            <small>sha256:{item.sha256.slice(0, 12)}</small>
                          </div>
                        ))}
                      </div>
                      <p className="hint compactHint">
                        Captured from the runtime by <code>homebrew-mlflow run</code>
                        {" "}and named in <code>homebrew-mlflow.toml</code>.
                      </p>
                    </div>
                  </div>
                </section>
                <section className="command">
                  <p className="label">Native workflow</p>
                  <pre>
                    homebrew-mlflow doctor{"\n"}homebrew-mlflow run --experiment
                    &lt;name&gt; -- &lt;command&gt;{"\n"}uv run --frozen dvc push -r platform
                    {"\n"}./scripts/dvc-publish.sh …
                  </pre>
                </section>
              </div>
            )}
            {tab === "runs" && (
              <>
                <section>
                  <Title
                    title="Runs"
                    count={runs.length}
                  />
                  <p className="hint">
                    Start a Run from a repository with{" "}
                    <code>homebrew-mlflow run --experiment &lt;name&gt; -- &lt;command&gt;</code>.
                    The CLI performs runtime capture before it creates the record.
                  </p>
                  <div className="split">
                    <div className="runList">
                      {runs.map((run) => (
                        <button
                          key={run.id}
                          className="artifact"
                          onClick={() => chooseRun(run)}
                        >
                          <strong>
                            {experimentNames.get(run.experiment_id) ??
                              run.experiment_id}
                          </strong>
                          <span className={`state ${run.state}`}>
                            {run.state}
                          </span>
                          <code>{run.id}</code>
                        </button>
                      ))}
                    </div>
                    {runDetail ? (
                      <RunInspector detail={runDetail} />
                    ) : (
                      <p className="muted">
                        Select a Run to inspect metrics and provenance.
                      </p>
                    )}
                  </div>
                </section>
              </>
            )}
            {tab === "artifacts" && (
              <>
                <section>
                  <Title title="Artifact catalog" count={artifacts.length} />
                  <form className="inlineForm" onSubmit={createArtifact}>
                    <input name="name" placeholder="Artifact family name" required />
                    <select name="kind" defaultValue="generic">
                      {(["dataset", "model", "checkpoint", "report", "generic"] as ArtifactKind[]).map((kind) => (
                        <option key={kind}>{kind}</option>
                      ))}
                    </select>
                    <input name="description" placeholder="Description (optional)" />
                    <button>Create artifact family</button>
                  </form>
                  <label className="field">
                    Kind
                    <select
                      value={artifactKind}
                      onChange={(event) => setArtifactKind(event.target.value as ArtifactKind | "all")}
                    >
                      <option value="all">all</option>
                      {(["dataset", "model", "checkpoint", "report", "generic"] as ArtifactKind[]).map((kind) => (
                        <option key={kind}>{kind}</option>
                      ))}
                    </select>
                  </label>
                  <div className="artifactLayout">
                    <div className="artifactList">
                      {artifacts.filter((artifact) => artifactKind === "all" || artifact.kind === artifactKind).map((artifact) => (
                        <button
                          key={artifact.id}
                          className={
                            artifact.id === artifactId
                              ? "artifact active"
                              : "artifact"
                          }
                          onClick={() => {
                            setArtifactId(artifact.id);
                            setVersion(null);
                            window.history.replaceState(
                              null,
                              "",
                              `/?project=${encodeURIComponent(projectId)}&artifact=${encodeURIComponent(artifact.id)}`,
                            );
                          }}
                        >
                          <strong>{artifact.name}</strong>
                          <span className="state">{artifact.kind}</span>
                          {artifact.description && <small>{artifact.description}</small>}
                          <code>{artifact.id}</code>
                        </button>
                      ))}
                    </div>
                    <div className="versions">
                      {versions.map((value) => (
                        <article
                          className={version?.id === value.id ? "chosen" : ""}
                          key={value.id}
                          onClick={() => chooseVersion(value)}
                        >
                          <div>
                            <strong>Version {value.sequence}</strong>
                            <span className="verified">{value.integrity}</span>
                          </div>
                          <code>
                            {value.algorithm}:{value.digest}
                          </code>
                          <p>
                            {value.output_kind} ·{" "}
                            {value.file_count.toLocaleString()} files ·{" "}
                            {formatBytes(value.size)}
                          </p>
                        </article>
                      ))}
                    </div>
                  </div>
                  {artifactId && canManageArtifacts && (
                    <div className="artifactControls">
                      <form className="inlineForm" onSubmit={updateArtifactMetadata}>
                        <select name="kind" defaultValue={artifacts.find((item) => item.id === artifactId)?.kind}>
                          {(["dataset", "model", "checkpoint", "report", "generic"] as ArtifactKind[]).map((kind) => (
                            <option key={kind}>{kind}</option>
                          ))}
                        </select>
                        <input
                          name="description"
                          defaultValue={artifacts.find((item) => item.id === artifactId)?.description ?? ""}
                          placeholder="Description"
                        />
                        <button>Update metadata</button>
                      </form>
                      <form className="inlineForm" onSubmit={setArtifactAlias}>
                        <input name="alias" placeholder="Alias, e.g. champion" required />
                        <select name="version" required defaultValue="">
                          <option value="" disabled>Target version</option>
                          {versions.map((value) => (
                            <option key={value.id} value={value.id}>Version {value.sequence}</option>
                          ))}
                        </select>
                        <button>Set alias</button>
                      </form>
                      <div className="aliasList">
                        {artifactAliases.map((value) => (
                          <span key={value.alias}>
                            <strong>{value.alias}</strong> → <code>{value.artifact_version_id}</code>{" "}
                            <button className="linkButton" onClick={() => deleteArtifactAlias(value.alias)}>delete</button>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {version && (
                    <VersionInspector
                      version={version}
                      files={files}
                      lineage={lineage}
                      grants={grants}
                      projects={projects}
                      consumption={consumption}
                      retention={retention}
                      onGrant={createGrant}
                      onRevoke={revokeGrant}
                    />
                  )}
                </section>
                <section>
                  <Title
                    title="Shared references and derivations"
                    count={sharedReferences.length}
                  />
                  <div className="table">
                    {sharedReferences.map((reference) => (
                      <div key={reference.id}>
                        <span>
                          <strong>{reference.artifact_version_id}</strong>
                          <small>{reference.run_id ?? "catalog reference"}</small>
                        </span>
                        <code>{reference.grant_id}</code>
                      </div>
                    ))}
                  </div>
                  <p className="hint">
                    References and derivations are derived from Run inputs and
                    published outputs instead of being entered as raw IDs.
                  </p>
                </section>
              </>
            )}
            {tab === "access" && (
              <>
                <section>
                  <Title
                    title="Project membership"
                    count={memberships.length}
                  />
                  <div className="table">
                    {memberships.map((member) => (
                      <div key={member.principal_id}>
                        <span>
                          <strong>{member.display_name}</strong>
                          <small>
                            {member.gitlab_username ?? member.principal_kind}
                          </small>
                        </span>
                        <span>
                          <code>{member.role}</code>
                          <button
                            className="linkButton"
                            onClick={() => recoverMaintainer(member.principal_id)}
                          >
                            recover Maintainer
                          </button>
                          <button
                            className="linkButton"
                            onClick={() => removeMembership(member.principal_id)}
                          >
                            remove
                          </button>
                        </span>
                      </div>
                    ))}
                  </div>
                  <form className="inlineForm" onSubmit={setMembership}>
                    <select name="principal" required>
                      {organizationPrincipals.map((principal) => (
                        <option key={principal.principal_id} value={principal.principal_id}>
                          {principal.display_name} ({principal.gitlab_username ?? principal.principal_kind})
                        </option>
                      ))}
                    </select>
                    <select name="role">
                      <option>viewer</option>
                      <option>contributor</option>
                      <option>maintainer</option>
                    </select>
                    <button>Add or update</button>
                  </form>
                </section>
                {organizationPrincipals.length > 0 && (
                  <section>
                    <Title
                      title="Organization directory"
                      count={organizationPrincipals.length}
                    />
                    <div className="table">
                      {organizationPrincipals.map((principal) => (
                        <div key={principal.principal_id}>
                          <span>
                            <strong>{principal.display_name}</strong>
                            <small>
                              {principal.gitlab_username ??
                                principal.principal_kind}
                            </small>
                          </span>
                          <code>
                            {principal.organization_role ?? "not enrolled"}
                          </code>
                        </div>
                      ))}
                    </div>
                    <form
                      className="inlineForm"
                      onSubmit={setOrganizationMembership}
                    >
                      <select name="principal" required>
                        {organizationPrincipals.map((principal) => (
                          <option
                            key={principal.principal_id}
                            value={principal.principal_id}
                          >
                            {principal.display_name} ({principal.principal_id})
                          </option>
                        ))}
                      </select>
                      <select name="role">
                        <option>member</option>
                        <option>admin</option>
                      </select>
                      <button>Enroll or update</button>
                    </form>
                  </section>
                )}
                <section>
                  <Title title="Machine principals" count={machines.length} />
                  {machineSecret && (
                    <div className="error">
                      <strong>
                        Copy this secret now; it will not be shown again.
                      </strong>
                      <pre>{machineSecret}</pre>
                    </div>
                  )}
                  <div className="table">
                    {machines.map((machine) => (
                      <div key={machine.credential_id}>
                        <span>
                          <strong>{machine.principal_id}</strong>
                            <small>
                              {machine.scopes.join(", ")} · expires {new Date(machine.expires_at).toLocaleDateString()}
                            </small>
                        </span>
                        {machine.revoked ? (
                          <code>revoked</code>
                        ) : (
                          <button
                            className="linkButton"
                            onClick={() => revokeMachine(machine.credential_id)}
                          >
                            revoke
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  <form className="inlineForm" onSubmit={createMachine}>
                    <input name="name" placeholder="Automation name" required />
                    <select name="role">
                      <option>viewer</option>
                      <option>contributor</option>
                    </select>
                    <button>Create credential</button>
                  </form>
                </section>
                <section>
                  <Title
                    title="Infisical routing"
                    count={secretContext ? 1 : 0}
                  />
                  {secretContext && (
                    <p className="statusline">
                      <span
                        className={`state ${secretContext.reconciliation_state}`}
                      >
                        {secretContext.reconciliation_state}
                      </span>{" "}
                      {secretContext.infisical_project_id} /{" "}
                      {secretContext.environment_slug} /{" "}
                      {secretContext.secret_path}
                    </p>
                  )}
                  <p className="hint">
                    The platform provisions this Infisical project automatically.
                    Secret values stay in Infisical and are injected only when a
                    Run explicitly enables <code>--secrets</code>.
                  </p>
                </section>
                <section>
                  <Title title="Audit trail" count={audit.length} />
                  <div className="table audit">
                    {audit.map((event) => (
                      <div key={event.sequence}>
                        <span>
                          <strong>{event.action}</strong>
                          <small>
                            {new Date(event.occurred_at).toLocaleString()} ·{" "}
                            {event.outcome}
                          </small>
                        </span>
                        <code>{event.resource_id ?? "system"}</code>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function Title({ title, count }: { title: string; count: number }) {
  return (
    <div className="sectionTitle">
      <h3>{title}</h3>
      <span>{count}</span>
    </div>
  );
}

function RunInspector({ detail }: { detail: RunDetail }) {
  const metricNames = [...new Set(detail.metrics.map((item) => item.key))];
  return (
    <article className="inspector">
      <div>
        <span className={`state ${detail.run.state}`}>{detail.run.state}</span>
        <code>{detail.run.id}</code>
      </div>
      <p>
        <strong>Command</strong> {detail.run.command.join(" ")}
      </p>
      <p>
        <strong>Source commit</strong>{" "}
        <code>{detail.git_commit_sha ?? "not finalized"}</code>
      </p>
      <p>
        <strong>Provenance</strong> {detail.provenance_status}
      </p>
      {detail.dvc_experiment_revision && (
        <p>
          <strong>DVC experiment</strong>{" "}
          <code>{detail.dvc_experiment_revision}</code>
        </p>
      )}
      <p>
        <strong>Inputs</strong>{" "}
        {detail.input_artifact_version_ids.join(", ") || "none"}
      </p>
      <p>
        <strong>Outputs</strong>{" "}
        {detail.output_artifact_version_ids.join(", ") || "none"}
      </p>
      {metricNames.map((name) => (
        <MetricChart
          key={name}
          name={name}
          values={detail.metrics.filter((item) => item.key === name)}
        />
      ))}
    </article>
  );
}

function MetricChart({ name, values }: { name: string; values: Metric[] }) {
  if (!values.length) return null;
  const sorted = [...values].sort(
    (a, b) => a.step - b.step || a.timestamp_ms - b.timestamp_ms,
  );
  const ys = sorted.map((item) => item.value);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const points = sorted
    .map(
      (item, index) =>
        `${(index / Math.max(1, sorted.length - 1)) * 300},${70 - ((item.value - min) / Math.max(1e-12, max - min)) * 60}`,
    )
    .join(" ");
  return (
    <div className="chart">
      <span>{name}</span>
      <svg
        viewBox="0 0 300 80"
        role="img"
        aria-label={`${name} metric history`}
      >
        <polyline points={points} />
      </svg>
      <small>{ys.at(-1)?.toPrecision(5)}</small>
    </div>
  );
}

function VersionInspector({
  version,
  files,
  lineage,
  grants,
  projects,
  consumption,
  retention,
  onGrant,
  onRevoke,
}: {
  version: Version;
  files: ArtifactFile[];
  lineage: Lineage[];
  grants: Grant[];
  projects: Project[];
  consumption: Consumption | null;
  retention: RetentionDependencies | null;
  onGrant: (event: FormEvent<HTMLFormElement>) => void;
  onRevoke: (id: string) => void;
}) {
  return (
    <article className="inspector versionInspector">
      <h3>{version.id}</h3>
      {retention && (
        <p className="statusline">
          <strong>Retention blockers:</strong>{" "}
          {retention.blockers.join(", ") || "none"}
        </p>
      )}
      <div className="columns">
        <div>
          <p className="label">File index</p>
          {files.slice(0, 100).map((file) => (
            <p className="file" key={file.path}>
              <code>{file.path}</code>
              <span>{formatBytes(file.size)}</span>
            </p>
          ))}
        </div>
        <div>
          <p className="label">Lineage</p>
          {lineage.length ? (
            lineage.map((edge) => (
              <p key={edge.id}>
                <code>{edge.source_artifact_version_id}</code> →{" "}
                <code>{edge.derived_artifact_version_id}</code>
              </p>
            ))
          ) : (
            <p className="muted">No derivation edges.</p>
          )}
          <p className="label">Exact-version sharing</p>
          {grants.map((grant) => (
            <p key={grant.id}>
              {grant.consuming_project_id} ·{" "}
              {grant.revoked_at ? (
                "revoked"
              ) : (
                <button
                  className="linkButton"
                  onClick={() => onRevoke(grant.id)}
                >
                  revoke
                </button>
              )}
            </p>
          ))}
          <form className="inlineForm compact" onSubmit={onGrant}>
            <select name="project" required>
              {projects
                .filter((item) => item.id !== version.owning_project_id)
                .map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
            <button>Grant</button>
          </form>
        </div>
      </div>
      {consumption && (
        <div className="command">
          <p className="label">Bash consumption</p>
          <pre>{consumption.bash_commands.join("\n")}</pre>
          <p className="label">PowerShell</p>
          <pre>{consumption.powershell_commands.join("\n")}</pre>
        </div>
      )}
    </article>
  );
}

async function watchPublication(
  url: string,
  token: string,
  update: React.Dispatch<React.SetStateAction<string[]>>,
) {
  let cursor = "";
  for (;;) {
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        ...(cursor ? { "Last-Event-ID": cursor } : {}),
      },
    });
    if (!response.ok || !response.body)
      throw new Error(`publication stream ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const message = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = "message";
        let data = "";
        for (const line of message.split("\n")) {
          if (line.startsWith("id:")) cursor = line.slice(3).trim();
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (event !== "heartbeat")
          update((items) => [...items, `${cursor} ${event} ${data}`]);
        if (event === "operation.published" || event === "operation.failed")
          return;
      }
    }
  }
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(1)} ${units[index]}`;
}

const root = document.getElementById("root");
if (root)
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
