# Hardware specification format

New Design Lead proposals use `openrtl.hardware-specification.v1`. The format is
block-neutral: it does not embed FIFO, bus, register, CPU, or accelerator rules.
It makes the engineering topics that must be reviewed visible and leaves
block-specific meaning in the proposal.

The structure adapts established practices from OpenTitan IP documentation and
comportability guidance and OpenHW requirement traceability. Those projects are
useful precedents, not a universal mandatory hardware-specification standard.
IP-XACT and similar integration metadata can complement this format but do not
replace its behavioral, timing, exceptional-case, and acceptance content.

## Authoritative fields

The existing specification fields remain authoritative:

- `ports` defines every signal name, direction, and concrete reviewed width;
- `requirements[].acceptance` defines the observable acceptance criteria;
- `questions` and `assumptions` preserve unresolved decisions and reviewable
  defaults, including assumption rationale; and
- `readiness` anchors decisions to those requirements and ports.

The `hardware_specification` block adds only material that was previously
missing. Its parameter inventory records each compile-time parameter's name,
type, textual default, legal values, and description. Textual defaults avoid
silently coercing provider JSON values; later RTL generation consumes the exact
reviewed representation.

Its eight fixed, ordered sections are:

1. purpose, scope, and exclusions;
2. parameters;
3. signal and protocol interfaces;
4. clocks, resets, and clock-domain crossings;
5. functional operation and state;
6. timing, latency, throughput, and backpressure;
7. exceptional and boundary behavior; and
8. integration, registers, and software-visible considerations.

Each section is `specified`, `unresolved`, or `not_applicable`, and always has a
non-empty explanation. Parameters and sections do not duplicate the authoritative
port, requirement, acceptance, question, assumption, or readiness inventories.

## Compatibility and approval

Saved legacy specifications remain valid and retain their historic content
digests. The CLI and web review identify them as legacy rather than rewriting
them. New discovery and feature proposals must include the v1 format. DV-only
and optimization proposals preserve an exact legacy specification because those
intents cannot change requirements. A provider-proposed feature change to a
legacy design therefore becomes an explicit, reviewable migration; it is never
applied merely by reopening a project.

The deterministic validator checks the schema, parameter types and uniqueness,
the exact section set and order, and consistency between the parameter inventory
and parameter-section status. Approval additionally rejects unresolved sections,
unresolved readiness decisions, open questions, or missing ports. These checks
establish structural completeness, not correctness, test adequacy, synthesis,
formal proof, or live-provider reliability.

The browser and CLI render the complete parameter inventory, all fixed sections,
ports, requirement acceptance criteria, questions, assumption rationale, and
readiness anchors. All browser content is inserted as text.

## Reference practices

- OpenTitan, *Comportability Specification*:
  <https://opentitan.org/book/doc/contributing/hw/comportability/index.html>
- OpenTitan UART interface and theory-of-operation organization:
  <https://opentitan.org/book/hw/ip/uart/doc/interfaces.html> and
  <https://opentitan.org/book/hw/ip/uart/doc/theory_of_operation.html>
- OpenHW CVA6 requirements and verification traceability:
  <https://docs.openhwgroup.org/projects/cva6-user-manual/02_cva6_requirements/cva6_requirements_specification.html>
