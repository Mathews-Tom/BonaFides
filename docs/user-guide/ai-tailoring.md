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

Before an installation can use a model, that exact model has to pass a qualification test on that installation. The test checks that the model reliably produces documents in the right format and that its statements pass the checker. It doesn't judge writing quality.

Results so far, on a Mac with Apple silicon using [Ollama](https://ollama.com) 0.40.13:

| Model | Writing a tailored pair | Result |
|---|---|---|
| `qwen2.5:7b-instruct` | Correct format on all 20 test cases; 98% of its factual statements passed the checker | **Passed** |
| `gemma4:12b-mlx` | Correct format on none of the 20 test cases | Failed |

Rewriting a single section hasn't yet been tested with a real model. Neither has any model's writing been reviewed by people for accuracy.

## Setting it up

Setting up AI is a one-off technical task, best done by someone comfortable with the command line. The install scripts don't have an option for it yet. In outline:

1. Install [Ollama](https://ollama.com) on your computer and download the model you want to use.
2. Write a small settings file that names that exact model, where it runs, and limits on how much can be sent and received.
3. Run BonaFides's qualification test against the model. Each kind of task (writing a pair, rewriting a section, and so on) is tested separately.
4. Connect the settings file to your installation and restart it.

The exact steps and commands are in the developer guide under [Optional operator-controlled local model route](../development/README.md#optional-operator-controlled-local-model-route).

Once it's set up, the Documents page shows **Generation route:** with the model's status. Tailoring becomes available when it shows the route as qualified.
