# Local provider controls

OpenRTL's design CLI and loopback web workspace share a reviewed model catalog
and a project-level estimated provider spend ceiling. List exact supported
model IDs and standard text prices without a provider call:

```sh
./openrtl models
```

The current catalog includes the existing onboarding snapshot
`gpt-5.4-nano-2026-03-17`, `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`,
and `gpt-5.6-luna`. Each is documented by OpenAI as supporting the Responses
API and structured outputs. Other names, including unreviewed aliases, fail
before credential resolution. The catalog is a local compatibility decision;
it cannot establish account access or live acceptance of OpenRTL's particular
schemas. Review current model documentation and prices before changing it.

For a CLI design turn, select the model and ceiling explicitly:

```sh
./openrtl chat --project /absolute/private/project --allow-provider \
  --model gpt-5.6-terra --max-spend-usd 20.00 --api-key-stdin
```

`--api-key-stdin` prompts without echo on a terminal or reads one bounded line
from redirected standard input. It keeps the value out of process arguments,
shell history, project files and provider reports. The existing
`--credential-env OPENAI_API_KEY` route remains available without the stdin
flag. The key is held only in the running process. Never paste it into a design
message or a `--model`/`--max-spend-usd` argument.

The web workspace exposes the same catalog and ceiling in **Provider settings**.
The password field replaces the running server's key; blank leaves the current
source unchanged. The server never returns the key, and a restart requires
re-entry unless the named environment source is available. Selecting a model
and key does not itself start a provider call. The **Enable provider calls**
checkbox explicitly controls whether subsequent chat actions may invoke it.
The server still binds only to loopback and checks Host and Origin. A browser
serving this UI should be treated as a trusted local session because the key
passes through that browser to the loopback server.

The ceiling is stored in the project session without the key. Before a call,
OpenRTL reserves a conservative amount using the selected model's published
standard text rates, context window, any applicable high-context surcharge, and configured
output-token maximum. A completed, identity-matched call with token usage
replaces that reservation with an estimate. A failed or interrupted call keeps
the reservation and blocks further provider work while cost is uncertain. The
estimate is not an exact provider bill or an account-side spending limit;
pricing, charges and account availability must be independently checked.
Changing the model or ceiling while idle preserves previously estimated spend.
Earlier calls from a migrated session are visibly marked unpriced.

Provider settings are project-level. M46's six-project live qualification also
requires a separately approved aggregate call/token/time/repair and monetary
ceiling, an exact model and a freshly verified owned runtime. These controls do
not qualify M46, authorize M47 or make a provider/runtime call during setup.
