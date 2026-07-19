# Public repository release checklist

Use this checklist when creating a separate public repository for RivalTrackAgent.

## 1. Decide the publication scope

The default public scope is application source, tests, configuration templates, selected technical documentation, and reproducible demo data.

Keep coursework, presentation exports, scratch scripts, runtime history, temporary output, personal assistant configuration, and machine-specific files out of the public snapshot. Do not add personal paths to the shared `.gitignore`.

For paths that only exist on your machine, add patterns to `.git/info/exclude`. That file uses the same pattern syntax as `.gitignore`, but it is not committed:

```text
# Examples only; replace them with your local paths.
local-notes/
private-report.pdf
scratch/
```

## 2. Choose a license

Add a `LICENSE` file before publication. Publishing code without a license does not grant others permission to reuse, modify, or redistribute it. Choose the license deliberately; MIT is permissive, while Apache-2.0 also includes an explicit patent grant.

## 3. Verify secrets and personal data

```bash
git ls-files .env
git grep -n -E "(API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)"
git status --short
```

`git ls-files .env` must produce no output. Review matches from the second command: configuration variable names are expected, real credential values are not.

Also review documentation, screenshots, PDFs, and example datasets for names, local filesystem paths, account identifiers, private URLs, and copyrighted material.

## 4. Run the verification suite

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python src/tools/run_real_pipeline.py --preset ai-coding
```

The real-pipeline replay requires configured model credentials. Unit tests must remain runnable without external API calls.

## 5. Create a clean snapshot

Do not push the existing development history directly when it contains internal planning artifacts. After committing the intended release state, create an archive that respects `.gitattributes` `export-ignore` rules:

```bash
git archive --format=zip --output ../RivalTrackAgent-public.zip HEAD
```

Extract the archive into a new directory, add the selected `LICENSE`, initialize a new Git repository, inspect the staged file list, and only then connect it to an empty public remote.

```bash
git init
git add .
git status --short
git commit -m "Initial public release"
```

Create the remote repository through your hosting provider, then add its URL and push. This keeps private development history out of the public repository.
