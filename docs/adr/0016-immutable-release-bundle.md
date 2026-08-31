# ADR 0016: Bind releases to library and examples artifacts

## Status

Accepted.

## Decision

OpenRTL releases bind one semantic version and one source commit to three
artifacts: a library-only wheel, a source distribution containing the complete
examples, and a deterministic companion examples archive. A checked JSON
manifest records each filename, byte size, media type, and SHA-256 digest.

The companion archive is the supported runnable-example surface. It contains
the FIFO model, synthesizable RTL, DV, fault fixtures, repair inputs, and the
provider-free example tools. Validation installs the wheel and its exact
AgentRig dependency into a clean environment, extracts the companion archive,
and runs the model, fault diagnosis, and Verilator repair walkthrough without
resolving source from the repository checkout.

The release-candidate manifest records the planned matching tag and explicitly
records that the tag has not been created. Tag creation, remote publication,
registry upload, and release hosting remain separate owner actions.

## Consequences

- Installed library users do not receive repository tests or examples in the
  wheel.
- Example users receive a content-addressed archive that is independently
  inspectable and executable with the installed wheel.
- A version declaration or editable installation alone is not a release.
- Rebuilding or replacing artifacts under an existing version is forbidden;
  corrections require a new version.
