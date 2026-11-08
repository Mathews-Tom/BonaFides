# Optional AI tailoring

BonaFides works fully without AI. You can build your profile, import a résumé, review jobs, create a base résumé, export PDFs and track applications without it.

AI is an optional extra. Once it's set up, it can:

- write a **résumé and cover letter tailored to one job** you're tracking,
- **rewrite one section** of a tailored document,
- **suggest the requirements** in a job posting, and
- **suggest how your profile covers** those requirements.

Every suggestion is shown to you before it's used, and every factual sentence it writes is checked against your profile.

## What AI never does in BonaFides

- **It never uses a cloud service.** The model must run on your own computer or private network, under your control. BonaFides refuses public internet addresses and never falls back to a hosted service.
- **It never runs without your say-so.** For each task, you see exactly what will be sent and where, and you tick a box to allow it. Opening or reviewing a document doesn't grant access.
- **It never downloads models or lets the model take actions.** BonaFides only asks the model for text and reads the answer, with a small, fixed limit on attempts per task.
- **It can't add facts.** It may only use your profile entries. Anything else it writes is flagged as a problem and blocks export.

## What the model receives

Before each task, preview the exact content on the Documents page: **Preview reviewed generation context** for a tailored pair, or **Preview selected-section model projection** for a single-section rewrite. In general:

**Sent:** the posting's title, employer and text; its reviewed requirements; your profile entries and their details; the text of their sources (except as noted below); the tone and language you chose.

**Never sent:** your name and contact details, your private notes, contacts, interview details and attachments, your password, and internal settings.

**Sources marked Confidential or employer-owned are withheld.** Entries that rely on them are still sent, as their own wording only. The preview lists withheld sources under **Evidence withheld from the model**.

Even a model on your own computer can see what you send it, and wording you wrote may still contain personal details. Read the preview.

The context prepared for a task is cleared by a daily cleanup once 24 hours have passed since the task finished.

## How BonaFides keeps the AI honest

Every factual statement in a generated document must cite one of your entries, and its wording must be that entry's wording (or the entry cut short at a natural break). The entry's wording must appear in its source. Statements that fail are listed as problems, and **the document can't be approved or exported until they're fixed**. Approving can't override this. See [Check every statement](documents.md#check-every-statement).

In tests on made-up examples, this checker caught every kind of exaggeration it was built to catch, and wrongly blocked 2 out of 1,175 honest statements. **One kind of change gets through:** a sentence cut short so that it loses a limiting phrase at the end. For example, "Reduced support costs by 20% in the pilot team" shortened to "Reduced support costs by 20%" passes, though it claims more. Read each statement before approving it.

The checker compares wording. It can't tell whether a sentence is well written, or whether your entries are true.

## Which models have been tested

Before an installation can use a model, that exact model has to pass a qualification test on that installation. Each task is qualified separately: at least 95% of its test cases must produce the right format after at most one correction. Factual statements are checked and reported separately; qualification doesn't judge writing quality or prove accuracy.

Results on a Mac with Apple silicon using [Ollama](https://ollama.com) 0.40.13. Qwen was tested through the installed commands on 8 November 2026; Gemma's result is from 2 November:

| Model | Task | Measured result | Qualification |
|---|---|---|---|
| `qwen2.5:7b-instruct` | Tailored pair | Correct format on 20/20 cases; 98% of factual statements passed the wording checks | **Passed** |
| `qwen2.5:7b-instruct` | Single-section rewrite | Correct format on 20/20 cases; 100% of factual statements passed the wording checks | **Passed** |
| `gemma4:12b-mlx` | Tailored pair | No checked output in 20 cases | Failed |

Neither model's writing has been reviewed by people for accuracy. Your installation must produce its own genuine receipts; these results don't qualify a different installation.

## Setting it up

Use the installed management scripts in your installation folder (normally `~/bonafides`). Docker and an already-running local [Ollama](https://ollama.com) with `qwen2.5:7b-instruct` are prerequisites. Check `ollama list` first. If the model is missing, install it yourself after reviewing its license; BonaFides never downloads it. No host Python or uv is needed.

Before attaching, disable Ollama cloud with `OLLAMA_NO_CLOUD=1` or `{"disable_ollama_cloud": true}` in `~/.ollama/server.json`, restart Ollama and confirm its startup log reports `Ollama cloud disabled: true` ([Ollama instructions](https://github.com/ollama/ollama/blob/main/docs/faq.mdx)). Keep cloud disabled and model tags unchanged throughout qualification and use. BonaFides does not attest these host settings or contain a runtime that ignores them.

On native Linux, Ollama's default `127.0.0.1:11434` listener is not reachable through Docker's host gateway. Arrange a Docker-reachable private listener or forwarder and restrict its firewall to the installation's Docker traffic before attaching. Do not expose port 11434 publicly. BonaFides does not reconfigure the host listener or firewall.

```sh
cd ~/bonafides
sh models.sh attach --model qwen2.5:7b-instruct --model-license Apache-2.0 --authorize-local-model
sh models.sh qualify --stage generate_documents --reviewed-by 'Local owner'
sh models.sh qualify --stage regenerate_section --reviewed-by 'Local owner'
```

Replace `Local owner` with the name to record as the reviewer. Attaching authorizes metadata reads and unencrypted HTTP on this computer's private Docker network, using the named model under your control with cloud use disabled. It records the installed model and Ollama version but does not send inference or claim that a test passed. Each qualification command sends only the bundled made-up examples, never your profile, and can take several minutes. Keep Ollama running and do not change its model while a test runs.

Each completed test saves its real result and restarts this installation to apply it. Exit status 0 means passed and applied; 1 means failed and applied, with its failed receipt saved; 2 means a setup, connection or application problem. Failures before publication leave earlier receipts unchanged. If configuration was saved but restarting failed, the error says so: resolve the reported problem and run `sh start.sh`. A failed test is not permission to use that stage. Only one model command can run at a time.

If interrupted attachment or removal leaves a disabled managed route, run `sh models.sh remove` again before attaching another model. A lock left by an interrupted command requires [safe manual recovery](../development/README.md#optional-operator-controlled-local-model-route); do not force-unlock a running command.

Open **Documents** for an application. **Generation route:** and the qualification receipts show each stage's real status. Writing a pair and rewriting a section qualify separately; a passed receipt cannot override a block caused by a malformed live answer. No model task runs until you preview its input and explicitly approve that task.

To disable AI and remove its managed route:

```sh
sh models.sh remove
```

Removal restarts this installation and returns the route to disabled. Your profile, documents, credentials and Ollama model weights are retained. Already transmitted input cannot be recalled. For a nondefault installation, add `--dir /absolute/path/to/installation` to each command. See the [developer contract](../development/README.md#optional-operator-controlled-local-model-route) for the pinned identity, limits and advanced source-checkout setup.

If the gateway's Docker address changes after network/container recreation, remove the route, attach it again and rerun both qualifications. Existing address pins deliberately fail closed; restarting retained containers does not require requalification.

## If an AI task fails

If the model reaches its output limit while writing a tailored pair or rewriting a section, only that task fails. No partial documents or section proposal are saved, and saved wording is unchanged. This failure does not block the model route: preview the task again and explicitly request another attempt. BonaFides does not make a corrective retry for an output limit.

An answer in the wrong format gets one corrective retry. If that answer is still malformed, the task fails and that kind of task is blocked for the route. A block on section regeneration does not by itself block pair generation. Another qualification receipt does not clear an existing live block.

On **Documents**, **Valid claim JSON rate for this route** shows separate **Pair generation** and **Section regeneration** counts. Output-limited tasks count against the rate: one output-limited task followed by one valid task gives 50% valid. Unreachable, interrupted and other failures before an answer can be evaluated are shown separately and excluded. This rate measures output format, not factual accuracy or writing quality.
