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

& homebrew-mlflow publication submit @PublicationArguments
exit $LASTEXITCODE
