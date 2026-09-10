# Priors

**The buyer agent that turns every outcome into a prior that changes its next decision.**

Priors hires other agents to get jobs done (generate a meme, design a logo, write
copy) and pays them on-chain. It gets measurably better every session because it
remembers who delivered, who stiffed it and what they charged. It refuses to repeat
a mistake it has already paid for. Its memory is not a feature bolted onto a chatbot.
Its memory **is** the product.

> Delete the memory layer and Priors stops working. It forgets every counterparty,
> loses every learned pattern and transacts blind, the way a stateless agent does.
> That collapse is asserted as a passing test: [`tests/test_gate.py`](tests/test_gate.py).

Live reputation dashboard: **https://priors.zkasuran.dev**
Built on [Sibyl Memory](https://github.com/Sibyl-Labs/Sibyl-Memory). Runs on **Base**. Packaged as a **Virtuals** marketplace buyer.

## The loop

1. A job comes in and a counterparty is proposed.
2. Priors consults memory: has it hired this counterparty before, how did it score,
   what did it charge and whether any learned category rule applies.
3. It returns a verdict (transact / caution / avoid) with the exact priors that drove
   it and its suggested terms. If the verdict is avoid, it names who to hire instead.
4. The job runs and Priors grades the result.
5. The graded outcome is written back to the ledger and, on Base, attested via the
   Ethereum Attestation Service so the reputation is public and verifiable.
6. Reflection folds the new outcome into the counterparty's record and, when a
   pattern crosses a threshold, into a category-level guardrail. Next session that
   guardrail changes who Priors hires.

## Where the memory is load-bearing

The verdict is computed **deterministically from memory**. No LLM sits in the decision
path, so the same memory always produces the same verdict and a test can assert exactly
how memory changes the call. Two independent mechanisms change decisions and both
vanish when the store is deleted:

**1. Individual history** (the counterparty book plus the outcome ledger).
A counterparty that stiffed Priors twice flips a cold `transact` into `avoid` and the
verdict names a better vendor from the roster. See `_score_history` and the `stiffed`
branch in [`priors/engine.py`](priors/engine.py).

**2. A learned guardrail** (reflection over the ledger).
After grading enough jobs, Priors detects that "image offers at or below 1.0 miss the
brief 100% of the time" and writes that as a guardrail. A counterparty Priors has
**never met**, offering at that tier, is then flipped from a blind `transact` to
`caution`, with a sample requested first. See `synthesize_guardrails` in
[`priors/learn.py`](priors/learn.py) and `_eval_guardrail` in `engine.py`.

### The four stores, on Sibyl's tiers

| Store | What it holds | Sibyl tier | API |
| --- | --- | --- | --- |
| Counterparty book | one record per counterparty: record, scores, prices, categories | WARM entities | `set_entity` / `get_entity` / `list_entities` |
| Outcome ledger | every graded job, append-only, time-ordered | COLD journal | `write_event` / `read_events` |
| Learned guardrails | category-level rules synthesized from the ledger | WARM entities | `set_entity` / `list_entities` |
| Vetting policy | the thresholds the engine starts from | REFERENCE | `set_reference` / `get_reference` |
| Working focus | the agent's current task and last snapshot | HOT state | `set_state` / `get_state` |

Cross-tier `search()` (Sibyl's FTS5) answers "have I dealt with anything like this".
The domain wrapper is [`priors/memory.py`](priors/memory.py), so the engine and the
learner never touch Sibyl directly.

### Memory primitives used (labeled honestly)

- **recall**: exact-key reads of a counterparty's record. Real, core.
- **entities**: WARM roster and guardrails, schema-unique per key. Real, core.
- **temporal**: outcomes carry timestamps, scores are recency-weighted (exponential
  half-life) and the ledger is read by time window. Real.
- **semantic search**: Sibyl's `search()` is FTS5 lexical plus trigram, not vector
  embeddings. We use it for "seen anything like this" and we do not call it
  vector-semantic.
- **reflection / consolidation**: our own reflection loop over the journal and entities
  tiers synthesizes guardrails and folds each outcome into the card. We built this on
  the free tiers rather than the paid-tier Learner, so the decision-changing brain is
  ours and runs offline.

## Try it

```bash
pip install -e .              # Python 3.10+
priors seed --reset           # load the disclosed demo roster (synthetic data)
priors roster                 # the remembered counterparties and their verdicts
priors vet 0xbad0000000000000000000000000000000000002   # PixelBot: AVOID, it stiffed us twice
priors vet 0x00000000000000000000000000000000deadbeef --price 0.5   # never met, cheap: CAUTION from a learned prior
priors why 0xbad0000000000000000000000000000000000002    # the priors behind a verdict
priors vet 0x4200000000000000000000000000000000000021 --onchain     # read Base Sepolia state as a prior
priors snapshot               # regenerate the dashboard data
```

### The delete-the-memory test, by hand

```bash
priors demo 0xbad0000000000000000000000000000000000002
#   fresh session, empty memory  -> TRANSACT (blind, confidence 0.2)
#   same agent, with its memory  -> AVOID and it names a better vendor
priors forget                   # delete the memory layer
priors vet 0xbad0000000000000000000000000000000000002   # back to TRANSACT (blind)
```

## Partner stacks

**Base.** Priors reads and writes Base Sepolia in service of its actual function.
- *Read prior (live, no gas):* `onchain_profile` reads a counterparty's live state on
  Base Sepolia (balance, transaction count, contract or EOA). A brand-new address with
  no on-chain footprint becomes a caution prior, folded into the verdict. Run
  `priors vet <addr> --onchain`.
- *Attestation (EAS):* `attest_outcome` writes a graded outcome to the Ethereum
  Attestation Service on Base (`0x4200000000000000000000000000000000000021`, verified
  live via `getSchemaRegistry()`), turning the local record into a public, verifiable
  trail. It needs a funded key. Without one it returns a fully prepared, unsent
  attestation plus the exact funding step, so finishing is one command.

**Virtuals.** Priors is a marketplace **buyer**: its whole job is choosing which
seller agent to hire for a subtask, from a remembered, graded roster. The memory is
what makes the buyer smart. A stateless buyer re-discovers the market every session,
ours accumulates a reputation ledger, so sourcing gets cheaper and better with use.

## Tests

```bash
pip install -e ".[dev]"
pytest -q            # 23 passing, including the eligibility-gate tests
```

## Prior Work declaration

The product concept, the Sibyl Memory research and the Python environment were prepared
during registration (August 16 to 17, 2026). **Every line of source code in this
repository was written on September 10, 2026, inside the build window,** and the commit
history reflects that. Sibyl Memory itself is third-party software by Sibyl Labs, used
under its MIT license and not modified.

## AI disclosure

AI assistance (Claude, Anthropic) was used in developing this project. The design, the
review and the verification were done by the author. Verified locally before
submitting: the full test suite (`pytest -q`, 23 passing) including the eligibility-gate
tests, the CLI end to end against a real Sibyl Memory database and the Base Sepolia
reads against the live chain.

## License

MIT. See [LICENSE](LICENSE).
