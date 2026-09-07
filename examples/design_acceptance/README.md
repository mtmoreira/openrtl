# Design-agent acceptance inputs

These are specifications, not generated RTL, golden implementations or passing
results. No generator dispatches on these names. Each specification still needs
user review (or exact explicit batch delegation) before engineering work.

- `alu8.json`: combinational arithmetic, logic, shifts and flags.
- `counter4.json`: sequential reset, enable and saturation.
- `arbiter4.json`: combinational priority and one-hot properties.

The counter and arbiter broaden evaluation beyond the requested ALU. They are
public acceptance scenarios, not secret held-out benchmarks. Do not tune the
agent or its tests to a generated solution for these inputs. Record failures
and revisions as well as successes. For genuinely held-out evaluation, choose
additional designs after freezing the candidate and record their spec hashes.

No real generation or simulation is established by shipping these files.
See `docs/design-agent-acceptance.md` in the source checkout for the evidence
tiers, installed smoke test and live qualification procedure.
