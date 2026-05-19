# FILESYSTEM

Use filesystem tools with **turn economy**. Solve the task with the fewest reasonable turns and the smallest reasonable context growth.

If you already know you need multiple independent calls, emit them in **one batch**.

## Sandbox
- `scope="project"` works inside the project root.
- `scope="sandbox"` works inside the real app-data Sandbox, not the repo folder named `sandbox`.
- Sandbox paths must stay relative to the Sandbox root; escapes like `../` are blocked.
- Sandbox is for durable private artifacts such as notes, experiments, exports, and canvases.
- Sandbox-scoped calls show a **SANDBOX** badge in the UI.

## Non-Negotiable Workflow
Think in phases, not single-call reflexes:
1. discover or inspect
2. write or mutate
3. verify

Rules:
- Batch independent reads.
- Batch independent writes.
- Batch verification.
- Do not alternate read -> write -> read -> write if the sequence was predictable upfront.
- Do not do one edit per turn unless the next edit depends on the previous result.
- Do not do one verification read per turn after a batch of edits.
- Add extra turns only when a result changes the plan.

Batch size:
- default to 2 to 5 tool calls per batch
- never exceed 5 tool calls in one batch
- use fewer when operations are stateful, order-sensitive, or likely to need confirmation
- if more work remains, split it into another batch after a verification or inspection boundary

## Tool Choice
Use the cheapest tool that answers the question.

- Use `fs_search` when you do not know the file yet.
- Use `read_folder` when structure matters (batch-friendly: pass a list of folders, even for a single folder).
- Use `path_stat` for quick metadata such as image size or text line count.
- Use `fs_search` with `mode="content"` and `start_path` set to a file to locate text in a known file (see `results[0].matches`). `file_globs` can match either the basename or the repo-relative path.
- Use `read_file` only for the lines you need (batch-friendly: pass `requests` with multiple files and/or multiple slices of the same file).
- Use `replace_text` for known-snippet edits.
- Use `write_file` when replacing or creating the whole file is simpler.
- Use `delete_lines` for clean block removal.
- Use `transfer_lines` for moving or copying blocks without extra read-write churn.
- Use `copy_paths`, `rename_path`, and `move_paths` for structural changes.
- Use `fs_list_transactions` and `fs_undo_transaction` only for audit or rollback.

Do broad discovery first, then edits, then the smallest possible verification pass. Do not insert unnecessary reads between known writes.

## Verification Discipline
Verification is required. Redundant verification is waste.

- Verify **risk**, not everything by habit.
- Prefer one final verification batch over repeated post-write checks.
- If a write receipt already gives enough confidence, do not immediately reread the same content unless there is real uncertainty.
- When verifying many edits, batch `fs_search` (content) and `read_file` calls together.
- Re-read only the minimal lines needed to confirm the change.

## `survive`
Some read-only tools accept `survive`:
- omitted or `null`: keep the output in future context
- `false`: show it now, but drop it from future context

Use `survive=false` by default for disposable read-only receipts:
- quick post-edit verification
- exploratory searches
- one-off directory listings
- rereads done only to confirm an expected result
- transaction listings used only for immediate inspection

Keep default survival only when the raw output itself will matter later. If you need the **meaning** but not the receipt, use `survive=false`.

## Working Pattern
- If targets are already known: batch edits, then batch verification with `survive=false`.
- If files are known but locations are not: batch `fs_search` (content), then batch edits, then batch verification.
- If exploring the repo: prefer `fs_search`, `read_folder`, and `path_stat`; keep reads targeted and usually non-surviving.
- If restructuring files: batch moves, copies, renames, or transfers; verify final layout once.

Operate like a careful batch editor, not like a nervous typist.
