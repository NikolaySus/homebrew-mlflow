import React, { useEffect, useMemo, useRef, useState } from "react";

export type Shell = "powershell" | "bash";
export type ShellCommands = { powershell: string; bash: string };

export type WorkflowRepository = {
  name: string;
  ssh_clone_url: string | null;
  http_clone_url: string | null;
};

export type WorkflowRun = { id: string; state: string };
export type WorkflowArtifact = {
  id: string;
  name: string;
  kind: "dataset" | "model" | "checkpoint" | "report" | "generic";
};

const SHELL_KEY = "homebrew-mlflow-shell";
const SHELL_EVENT = "homebrew-mlflow-shell-change";
const exactArtifactVersion = /^av_[0-9A-Z]{26}$/;
const exactRun = /^run_[0-9A-Z]{26}$/;
const safeName = /^[A-Za-z0-9_.-]+$/;

export function quotePowerShell(value: string) {
  return `'${value.replaceAll("'", "''")}'`;
}

export function quoteBash(value: string) {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function validRelativePath(value: string) {
  return (
    value.length > 0 &&
    !value.startsWith("-") &&
    !value.startsWith("/") &&
    !value.includes("\\") &&
    !value.includes("\n") &&
    !value.split("/").some((part) => part === "" || part === "." || part === "..")
  );
}

function useShellPreference(): [Shell, (value: Shell) => void] {
  const initial = window.localStorage.getItem(SHELL_KEY);
  const [shell, setShell] = useState<Shell>(initial === "bash" ? "bash" : "powershell");
  useEffect(() => {
    const update = (event: Event) => setShell((event as CustomEvent<Shell>).detail);
    window.addEventListener(SHELL_EVENT, update);
    return () => window.removeEventListener(SHELL_EVENT, update);
  }, []);
  return [
    shell,
    (value) => {
      window.localStorage.setItem(SHELL_KEY, value);
      window.dispatchEvent(new CustomEvent<Shell>(SHELL_EVENT, { detail: value }));
    },
  ];
}

export function CopyButton({ value, label = "Copy", disabled = false }: {
  value: string;
  label?: string;
  disabled?: boolean;
}) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setStatus("idle"), 1800);
  }
  const visible = status === "copied" ? "Copied" : status === "failed" ? "Copy failed" : label;
  return (
    <button className="copyButton" type="button" onClick={copy} disabled={disabled || !value}>
      {visible}
      <span className="srOnly" aria-live="polite">{status === "idle" ? "" : visible}</span>
    </button>
  );
}

export function CopyField({ label, value, secret = false, onClear }: {
  label: string;
  value: string;
  secret?: boolean;
  onClear?: () => void;
}) {
  return (
    <div className={`copyField${secret ? " secretField" : ""}`}>
      <span>{label}</span>
      <code>{value}</code>
      <CopyButton value={value} />
      {onClear && <button type="button" className="copyButton secondary" onClick={onClear}>Clear</button>}
    </div>
  );
}

export function CommandCard({ title, description, commands, disabledReason, compact = false }: {
  title: string;
  description?: React.ReactNode;
  commands: ShellCommands;
  disabledReason?: string;
  compact?: boolean;
}) {
  const [shell, setShell] = useShellPreference();
  const sharedCommand = commands.powershell === commands.bash;
  const command = sharedCommand ? commands.powershell : commands[shell];
  const rows = Math.max(2, Math.min(12, command.split("\n").length + 1));
  return (
    <article className={`commandCard${compact ? " compactCommand" : ""}`}>
      <div className="commandHeader">
        <div>
          <h4>{title}</h4>
          {description && <p>{description}</p>}
        </div>
        <div className={`shellTabs${sharedCommand ? " sharedShell" : ""}`} role={sharedCommand ? undefined : "group"} aria-label={`${title} shell`}>
          {sharedCommand ? <span>PowerShell &amp; Bash</span> : <>
            <button type="button" className={shell === "powershell" ? "active" : ""} onClick={() => setShell("powershell")}>PowerShell</button>
            <button type="button" className={shell === "bash" ? "active" : ""} onClick={() => setShell("bash")}>Bash</button>
          </>}
        </div>
      </div>
      <div className="commandField">
        <textarea readOnly rows={rows} value={command} aria-label={`${title} command`} />
        <CopyButton value={command} disabled={Boolean(disabledReason)} />
      </div>
      {disabledReason && <p className="commandValidation">{disabledReason}</p>}
    </article>
  );
}

function cloneDirectory(repository: WorkflowRepository) {
  const source = repository.ssh_clone_url ?? repository.http_clone_url ?? repository.name;
  return source.split(/[/:]/).at(-1)?.replace(/\.git$/, "") || "repository";
}

export function repositorySetupCommands(repository: WorkflowRepository): ShellCommands {
  const clone = repository.ssh_clone_url ?? repository.http_clone_url ?? "<clone-url>";
  const directory = cloneDirectory(repository);
  const make = (quote: (value: string) => string) => [
    `git clone ${quote(clone)}`,
    `cd ${quote(directory)}`,
    "homebrew-mlflow repository configure",
    "git diff",
    "uv sync --frozen",
    "uv run --frozen python -c \"import importlib.metadata as m; print(m.version('homebrew-mlflow-plugins'))\"",
    "git status --short",
  ].join("\n");
  return { powershell: make(quotePowerShell), bash: make(quoteBash) };
}

export function RepositorySetup({ repository }: { repository: WorkflowRepository }) {
  const clone = repository.ssh_clone_url ?? repository.http_clone_url;
  if (!clone) return null;
  return (
    <div className="contextCommands">
      <CopyField label="Clone URL" value={clone} />
      <details>
        <summary>Configure a new clone</summary>
        <CommandCard
          compact
          title="Clone and configure"
          description="Review generated changes before committing them. Credentials remain outside the repository."
          commands={repositorySetupCommands(repository)}
        />
      </details>
    </div>
  );
}

function Input({ label, value, onChange, placeholder, invalid = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  invalid?: boolean;
}) {
  return (
    <label className="recipeField">
      <span>{label}</span>
      <input value={value} placeholder={placeholder} aria-invalid={invalid} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function RunCommandCard({ initialInputVersion = "" }: { initialInputVersion?: string }) {
  const [experiment, setExperiment] = useState("");
  const [dvcExperiment, setDvcExperiment] = useState("");
  const [stage, setStage] = useState("");
  const [inputs, setInputs] = useState(initialInputVersion);
  useEffect(() => setInputs(initialInputVersion), [initialInputVersion]);
  const versions = inputs.split(/[\s,]+/).filter(Boolean);
  const invalid = !safeName.test(experiment) || !safeName.test(dvcExperiment) || !safeName.test(stage) || versions.some((value) => !exactArtifactVersion.test(value));
  const make = (quote: (value: string) => string) => [
    "homebrew-mlflow run",
    `--experiment ${quote(experiment || "experiment-name")}`,
    ...versions.map((value) => `--input-version ${quote(value)}`),
    `-- dvc exp run -n ${quote(dvcExperiment || "dvc-experiment-name")} ${quote(stage || "training-stage")}`,
  ].join(" ");
  return (
    <div className="interactiveRecipe">
      <div className="recipeFields">
        <Input label="Platform experiment" value={experiment} onChange={setExperiment} placeholder="experiment-name" invalid={Boolean(experiment) && !safeName.test(experiment)} />
        <Input label="DVC experiment" value={dvcExperiment} onChange={setDvcExperiment} placeholder="named-experiment" invalid={Boolean(dvcExperiment) && !safeName.test(dvcExperiment)} />
        <Input label="DVC stage" value={stage} onChange={setStage} placeholder="train" invalid={Boolean(stage) && !safeName.test(stage)} />
        <Input label="Input av_ IDs" value={inputs} onChange={setInputs} placeholder="av_... (space separated)" invalid={versions.some((value) => !exactArtifactVersion.test(value))} />
      </div>
      <CommandCard
        title="Record a managed DVC Run"
        description="Every input must be an exact published Artifact Version. Repeatable inputs are generated in the entered order."
        commands={{ powershell: make(quotePowerShell), bash: make(quoteBash) }}
        disabledReason={invalid ? "Enter valid experiment names, a DVC stage, and exact av_... input IDs before copying." : undefined}
      />
    </div>
  );
}

export function PublicationCommandCard({ artifact, runs }: { artifact: WorkflowArtifact; runs: WorkflowRun[] }) {
  const [selector, setSelector] = useState<"pipeline" | "standalone">("pipeline");
  const [stage, setStage] = useState("");
  const [output, setOutput] = useState("");
  const [dvcFile, setDvcFile] = useState("");
  const [runId, setRunId] = useState("");
  const [signature, setSignature] = useState("model-signature.json");
  const [manifest, setManifest] = useState("");
  const invalidRun = Boolean(runId) && !exactRun.test(runId);
  const selectorInvalid = selector === "pipeline" ? !safeName.test(stage) || !validRelativePath(output) : !validRelativePath(dvcFile) || !validRelativePath(output);
  const invalid = selectorInvalid || invalidRun || (artifact.kind === "model" && (!validRelativePath(signature) || !validRelativePath(manifest)));
  const make = (shell: Shell) => {
    const quote = shell === "powershell" ? quotePowerShell : quoteBash;
    const script = shell === "powershell" ? "scripts/dvc-publish.ps1" : "./scripts/dvc-publish.sh";
    const selectorArgs = selector === "pipeline"
      ? `--pipeline dvc.yaml --stage ${quote(stage || "stage")} --out ${quote(output || "output")}`
      : `--dvc-file ${quote(dvcFile || "dataset.dvc")} --out ${quote(output || "dataset")}`;
    return [
      script,
      selectorArgs,
      `--artifact ${quote(artifact.name)}`,
      runId ? `--run-id ${quote(runId)}` : "",
      artifact.kind === "model" ? `--signature ${quote(signature || "model-signature.json")}` : "",
    ].filter(Boolean).join(" ");
  };
  return (
    <div className="interactiveRecipe">
      <div className="recipeFields">
        <label className="recipeField"><span>Selector</span><select value={selector} onChange={(event) => setSelector(event.target.value as "pipeline" | "standalone")}><option value="pipeline">Pipeline output</option><option value="standalone">Standalone .dvc</option></select></label>
        {selector === "pipeline" ? <Input label="DVC stage" value={stage} onChange={setStage} placeholder="train" invalid={Boolean(stage) && !safeName.test(stage)} /> : <Input label=".dvc file" value={dvcFile} onChange={setDvcFile} placeholder="dataset.dvc" invalid={Boolean(dvcFile) && !validRelativePath(dvcFile)} />}
        <Input label="Output path" value={output} onChange={setOutput} placeholder={selector === "pipeline" ? "models/model" : "dataset.parquet"} invalid={Boolean(output) && !validRelativePath(output)} />
        <label className="recipeField"><span>Producing Run (optional)</span><input list={`runs-${artifact.id}`} value={runId} placeholder="run_..." aria-invalid={invalidRun} onChange={(event) => setRunId(event.target.value)} /></label>
        <datalist id={`runs-${artifact.id}`}>{runs.map((run) => <option key={run.id} value={run.id}>{run.state}</option>)}</datalist>
        {artifact.kind === "model" && <Input label="Committed signature" value={signature} onChange={setSignature} placeholder="model-signature.json" invalid={Boolean(signature) && !validRelativePath(signature)} />}
        {artifact.kind === "model" && <Input label="Model manifest" value={manifest} onChange={setManifest} placeholder="models/model/manifest.json" invalid={Boolean(manifest) && !validRelativePath(manifest)} />}
      </div>
      {artifact.kind === "model" && (
        <CommandCard
          title="Generate and commit the model signature"
          description="The sidecar contains schema only. Review it before committing and publish from that exact pushed commit."
          commands={{
            powershell: `uv run --frozen python scripts/generate-model-signature.py --manifest ${quotePowerShell(manifest || "model-manifest")} --output ${quotePowerShell(signature)}\ngit add ${quotePowerShell(signature)}\ngit commit -m 'Add model signature'\ngit push`,
            bash: `uv run --frozen python scripts/generate-model-signature.py --manifest ${quoteBash(manifest || "model-manifest")} --output ${quoteBash(signature)}\ngit add ${quoteBash(signature)}\ngit commit -m 'Add model signature'\ngit push`,
          }}
          disabledReason={!validRelativePath(manifest) || !validRelativePath(signature) ? "Enter valid relative manifest and signature paths before copying." : undefined}
        />
      )}
      <CommandCard
        title={`Publish ${artifact.name}`}
        description="Push Git and DVC state first. Registration is complete only when the event stream reaches operation.published."
        commands={{ powershell: make("powershell"), bash: make("bash") }}
        disabledReason={invalid ? "Complete the selector with valid relative paths and an exact run_... ID before copying." : undefined}
      />
    </div>
  );
}

function BranchCommandCard() {
  const [description, setDescription] = useState("");
  const slug = description.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const date = new Date().toLocaleDateString("sv-SE").replaceAll("-", "");
  const branch = `experiment/${date}-${slug || "short-description"}`;
  const command = `git switch -c ${branch}\ngit status --short --branch`;
  return <><Input label="Short branch description" value={description} onChange={setDescription} placeholder="temporal-features" invalid={Boolean(description) && !slug} /><CommandCard title="Start an isolated experiment branch" commands={{ powershell: command, bash: command }} disabledReason={!slug ? "Enter a short branch description before copying." : undefined} /></>;
}

function PreprocessingCommandCard() {
  const [stage, setStage] = useState("");
  const [output, setOutput] = useState("");
  const [training, setTraining] = useState("");
  const invalid = !safeName.test(stage) || !safeName.test(training) || !validRelativePath(output);
  const make = (quote: (value: string) => string) => `uv run --frozen dvc repro ${quote(stage || "preprocessing-stage")}\nuv run --frozen dvc status ${quote(stage || "preprocessing-stage")}\nuv run --frozen dvc push -r platform ${quote(output || "preprocessing-output")}\ngit add dvc.lock\ngit commit -m 'Record cached preprocessing features'\ngit push\ngit rev-list --count '@{u}..HEAD'\nuv run --frozen dvc repro --dry ${quote(training || "training-stage")}`;
  return <><div className="recipeFields"><Input label="Preprocessing stage" value={stage} onChange={setStage} placeholder="prepare_features" invalid={Boolean(stage) && !safeName.test(stage)} /><Input label="Preprocessing output" value={output} onChange={setOutput} placeholder="data/features" invalid={Boolean(output) && !validRelativePath(output)} /><Input label="Training stage" value={training} onChange={setTraining} placeholder="train" invalid={Boolean(training) && !safeName.test(training)} /></div><CommandCard title="Materialize reusable preprocessing" description="The final dry run must report that preprocessing did not change. Commit only its DVC lock identity, never cache objects." commands={{ powershell: make(quotePowerShell), bash: make(quoteBash) }} disabledReason={invalid ? "Enter valid DVC stages and a relative output path before copying." : undefined} /></>;
}

function DvcExperimentTransferCard() {
  const [experiment, setExperiment] = useState("");
  const invalid = !safeName.test(experiment);
  const make = (quote: (value: string) => string) => `uv run --frozen dvc exp list --all\nuv run --frozen dvc exp list origin --all\nuv run --frozen dvc exp push origin ${quote(experiment || "experiment-name")} -r platform -j 2\nuv run --frozen dvc status -c -r platform`;
  return <><Input label="Named DVC experiment" value={experiment} onChange={setExperiment} placeholder="temporal-anchor-bagging" invalid={Boolean(experiment) && invalid} /><CommandCard title="Share a named DVC experiment" description="This transfers the experiment ref and cached outputs to the Git and platform remotes." commands={{ powershell: make(quotePowerShell), bash: make(quoteBash) }} disabledReason={invalid ? "Enter a valid DVC experiment name before copying." : undefined} /></>;
}

function ShareBranchCommandCard() {
  const [paths, setPaths] = useState("");
  const [message, setMessage] = useState("");
  const [branch, setBranch] = useState("");
  const reviewedPaths = paths.split(/[\s,]+/).filter(Boolean);
  const validBranch = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(branch) && !branch.includes("..");
  const invalid = !reviewedPaths.length || reviewedPaths.some((path) => !validRelativePath(path)) || !message.trim() || message.includes("\n") || !validBranch;
  const make = (quote: (value: string) => string) => `uv run --frozen python -m unittest discover -s tests\nuv run --frozen dvc status\ngit diff --check\ngit add ${reviewedPaths.map(quote).join(" ") || quote("reviewed-path")}\ngit commit -m ${quote(message || "Describe the coherent change")}\ngit push -u origin ${quote(branch || "experiment/YYYYMMDD-description")}\ngit rev-list --count '@{u}..HEAD'`;
  return <><div className="recipeFields"><Input label="Reviewed paths" value={paths} onChange={setPaths} placeholder="src/model.py tests/test_model.py" invalid={Boolean(paths) && reviewedPaths.some((path) => !validRelativePath(path))} /><Input label="Commit message" value={message} onChange={setMessage} placeholder="Add temporal feature experiment" invalid={message.includes("\n")} /><Input label="Branch name" value={branch} onChange={setBranch} placeholder="experiment/20260823-temporal-features" invalid={Boolean(branch) && !validBranch} /></div><CommandCard title="Validate and share a branch" description="Only the reviewed relative paths are staged. The final count must be 0 before the branch is treated as shared." commands={{ powershell: make(quotePowerShell), bash: make(quoteBash) }} disabledReason={invalid ? "Enter reviewed relative paths, a commit message, and a valid branch name before copying." : undefined} /></>;
}

function RetryPreflightCommandCard() {
  const [stages, setStages] = useState("");
  const stageNames = stages.split(/[\s,]+/).filter(Boolean);
  const invalid = !stageNames.length || stageNames.some((stage) => !safeName.test(stage));
  const make = (quote: (value: string) => string) => `uv run --frozen dvc status ${stageNames.map(quote).join(" ") || quote("stage")}\ngit status --short\ngit rev-list --count '@{u}..HEAD'`;
  return <><Input label="Failed publication stages" value={stages} onChange={setStages} placeholder="submission evaluation_report" invalid={Boolean(stages) && invalid} /><CommandCard title="Preflight a registration-only retry" description="Use only when the failed operation created no Artifact Version and Git/DVC state has not changed." commands={{ powershell: make(quotePowerShell), bash: make(quoteBash) }} disabledReason={invalid ? "Enter one or more valid DVC stage names before copying." : undefined} /></>;
}

export function WorkflowGuide({ install, installAvailable, repository, artifacts, runs }: {
  install: ShellCommands;
  installAvailable: boolean;
  repository?: WorkflowRepository;
  artifacts: WorkflowArtifact[];
  runs: WorkflowRun[];
}) {
  const [artifactId, setArtifactId] = useState(artifacts[0]?.id ?? "");
  useEffect(() => {
    if (!artifacts.some((item) => item.id === artifactId)) setArtifactId(artifacts[0]?.id ?? "");
  }, [artifacts, artifactId]);
  const artifact = artifacts.find((item) => item.id === artifactId);
  const server = window.location.origin;
  const setup = useMemo<ShellCommands>(() => ({
    powershell: `${install.powershell}\nhomebrew-mlflow version\nhomebrew-mlflow login --server ${quotePowerShell(server)}`,
    bash: `${install.bash}\nhomebrew-mlflow version\nhomebrew-mlflow login --server ${quoteBash(server)}`,
  }), [install, server]);
  const staticCommands: { title: string; description: string; commands: ShellCommands }[] = [
    { title: "Materialize environment and data", description: "Use locked dependency resolution and the repository's credential helper.", commands: { powershell: "uv sync --frozen\nuv run --frozen dvc pull -r platform\nuv run --frozen dvc status", bash: "uv sync --frozen\nuv run --frozen dvc pull -r platform\nuv run --frozen dvc status" } },
    { title: "Check readiness", description: "Expected Git results are no status entries and an unpushed count of 0.", commands: { powershell: "git status --short\ngit rev-list --count '@{u}..HEAD'\nhomebrew-mlflow doctor", bash: "git status --short\ngit rev-list --count '@{u}..HEAD'\nhomebrew-mlflow doctor" } },
    { title: "Inspect and synchronize DVC", description: "A DVC push transfers cache objects; it does not publish an Artifact Version.", commands: { powershell: "uv run --frozen dvc exp show --no-pager\nuv run --frozen dvc metrics show\nuv run --frozen dvc exp list --all\nuv run --frozen dvc checkout\nuv run --frozen dvc status\nuv run --frozen dvc push -r platform\nuv run --frozen dvc status -c -r platform", bash: "uv run --frozen dvc exp show --no-pager\nuv run --frozen dvc metrics show\nuv run --frozen dvc exp list --all\nuv run --frozen dvc checkout\nuv run --frozen dvc status\nuv run --frozen dvc push -r platform\nuv run --frozen dvc status -c -r platform" } },
  ];
  return (
    <section className="workflowGuide" aria-label="Tested workflows">
      <div className="workflowIntro"><div><h4>Work locally, archive exact results here</h4></div><p>These recipes never execute in the browser. Fill required values, review the generated command, then copy it into your repository terminal.</p></div>
      <details open><summary>1. Install and configure</summary><div className="workflowBody"><CommandCard title="Install or update the CLI" description="The CLI is isolated from experiment environments and installed from this service." commands={setup} disabledReason={installAvailable ? undefined : "Recommended release metadata is unavailable. Refresh before copying an installation command."} />{repository && <CommandCard title="Clone and configure this repository" commands={repositorySetupCommands(repository)} />}</div></details>
      <details><summary>2. Prepare and verify the repository</summary><div className="workflowBody">{staticCommands.slice(0, 2).map((item) => <CommandCard key={item.title} {...item} />)}<div className="interactiveRecipe"><BranchCommandCard /></div></div></details>
      <details><summary>3. Run an experiment with immutable inputs</summary><div className="workflowBody"><RunCommandCard /></div></details>
      <details><summary>4. Cache reusable preprocessing</summary><div className="workflowBody"><div className="interactiveRecipe"><PreprocessingCommandCard /></div></div></details>
      <details><summary>5. Inspect, validate, and share results</summary><div className="workflowBody">{staticCommands.slice(2).map((item) => <CommandCard key={item.title} {...item} />)}<div className="interactiveRecipe"><ShareBranchCommandCard /></div><div className="interactiveRecipe"><DvcExperimentTransferCard /></div></div></details>
      <details><summary>6. Publish an exact DVC output</summary><div className="workflowBody">{artifacts.length ? <><label className="recipeField artifactChoice"><span>Artifact family</span><select value={artifactId} onChange={(event) => setArtifactId(event.target.value)}>{artifacts.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.kind})</option>)}</select></label>{artifact && <PublicationCommandCard artifact={artifact} runs={runs} />}</> : <p className="muted">Create an Artifact family before generating a publication command.</p>}</div></details>
      <details><summary>7. Retry registration without recomputation</summary><div className="workflowBody"><div className="interactiveRecipe"><RetryPreflightCommandCard /></div></div></details>
    </section>
  );
}
