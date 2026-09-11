# Regression checks

Use `tests/acceptance.py` as the offline entry point. `--list` shows supported checks;
optional names run a subset. Each check uses a fresh subprocess and temporary data.
See [the testing guide](../docs/testing.md) for the feature-to-test map.

Other bench files include historical or explicitly live model experiments; do not
blindly execute every Python file. Service probes and training tools are under
`scripts/`. Never upload private recordings or modify real calendar events in offline
checks. `korone_candidates.json` is a public music metadata fixture.
