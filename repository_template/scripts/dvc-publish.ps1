[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PublicationArguments
)

$ErrorActionPreference = "Stop"

& git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("error: publication must run inside a Git repository")
    exit 2
}

$dirtyMetadata = & git status --porcelain -- dvc.yaml dvc.lock '*.dvc'
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if ($dirtyMetadata) {
    [Console]::Error.WriteLine("error: commit all relevant DVC metadata before publication")
    $dirtyMetadata | ForEach-Object { [Console]::Error.WriteLine($_) }
    exit 3
}

$commitSha = (& git rev-parse HEAD).Trim()
$upstreamSha = & git rev-parse '@{upstream}' 2> $null
if ($LASTEXITCODE -ne 0 -or !$upstreamSha -or $upstreamSha.Trim() -ne $commitSha) {
    [Console]::Error.WriteLine("error: the current commit must be pushed to its configured upstream")
    exit 4
}

& homebrew-mlflow publication submit --commit-sha $commitSha @PublicationArguments
exit $LASTEXITCODE
