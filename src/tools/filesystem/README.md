# src/tools/filesystem

Agent-facing filesystem tool group.

This folder defines the **function tools** that let an agent inspect and mutate files inside the project root or the app Sandbox.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schemas + implementations for discovery, editing, structural file operations, and transaction undo.
- `prompt.md` — system prompt chapter for correct filesystem usage.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Explicit scope**: tools operate in `scope=project` or `scope=sandbox`; Sandbox means the real app-data Sandbox, not a repo folder.
- **Turn economy**: agents should work in short waves: inspect, mutate, verify. Independent calls should be batched, with a practical cap of 5 tool calls per batch.
- **Context efficiency**: many read-only tools accept `survive=false` so agents can keep the UI receipt without carrying bulky raw output into future context.
- **Transaction safety**: structural mutations are transaction-aware, so history can be inspected with `fs_list_transactions` and reverted with `fs_undo_transaction` when appropriate.

## Tool categories
- Discovery / inspection: `read_folder` (paths[]), `read_file`, `images_get`, `fs_search`, `path_stat`
- Content mutation: `write_file`, `replace_text`, `delete_lines`, `transfer_lines`
- Structural mutation: `create_folder`, `delete_paths`, `copy_paths`, `rename_path`, `move_paths`
- Audit / recovery: `fs_list_transactions`, `fs_undo_transaction`

## When to use which tool
- Unknown file or broad repo lookup: `fs_search`
- Known file, find the exact spot: `fs_search` with `mode="content"` and `start_path` set to that file (returns `results[0].matches` with line+content)
- Read specific slices (batch via `requests[]`, supports multiple files and multiple slices of the same file): `read_file`
- Need only metadata: `path_stat`
- Known-snippet edit: `replace_text`
- Replace or create a whole file: `write_file`
- Move or copy blocks between files: `transfer_lines`
- Rename / move / copy files and folders: `rename_path`, `move_paths`, `copy_paths`
- Inspect or revert recent filesystem changes: `fs_list_transactions`, `fs_undo_transaction`