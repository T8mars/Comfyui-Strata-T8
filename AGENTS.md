# Project context

Read meta.json, features.json, pyproject.toml, related source and tests before changing code or documentation.
This repository owns the ComfyUI nodes only. Strata runtime, model preparation and portable updates live in https://github.com/T8mars/Strata-T8.
Keep node IDs and Strata protocol 1 compatibility. Profiles and credentials stay outside the repository and workflows.
Run tests with STRATA_SOURCE_DIR pointing to a compatible Strata-T8 checkout; never download a model for unit tests.
Registry publisher: t8star. Node ID: strata-t8. Increment the independent semantic version before publishing an immutable Registry version.
