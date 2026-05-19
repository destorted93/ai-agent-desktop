# DOCUMENT SEARCH

Use these tools when you need information from indexed document collections or from Confluence.

## Choose the right path
- Use `rag_list_collections` first when you do not yet know which collection holds the answer.
- Use `rag_search` when you know the collection and need relevant document chunks from it.
- Use `search_confluence` when the user asks for Confluence, gives a Confluence link, or the needed information likely lives in internal pages such as specs, requirements, or how-tos.

## Working model
Think in two stages:
1. find the right source
2. search inside that source

For collection-backed search:
- Start with `rag_list_collections` if the correct collection is unclear.
- Pick the most relevant collection by name, description, tags, or count.
- Then use `rag_search` with a focused natural-language query.

For Confluence:
- If the user gave a Confluence URL, pass it as `url` so the search stays on the right base.
- If not, use `url=null` to search across configured bases.
- Use `content_type="page"` unless you have a reason not to.

## Search discipline
Do not one-shot difficult searches.

- Refine when needed: change keywords, add component names, acronyms, requirement IDs, page-title phrases, or error strings.
- Use returned snippets or `content_markdown` to learn better follow-up keywords.
- Keep limits reasonable. Pull what you need, not everything available.
- If you already know the right collection, do not waste a turn listing collections again.

## `survive`
These are read-only search tools. Heavy search output should usually **not** survive.

- omitted or `null`: keep raw output in future context
- `false`: show it now, but drop it from future context

Default to `survive=false` for `rag_list_collections`, `rag_search`, and `search_confluence` unless the raw receipts themselves will matter later. Usually you only need the meaning, not the full payload.

## Mandatory summarization
If you used `rag_search` or `search_confluence` during the current Run, you MUST call `run_summary` before the Run ends.

Reason:
- search output is bulky
- future Runs should keep the meaning, not the raw dump

Good summary contents:
- what source you searched
- what you found or did not find
- key page names, collection names, links, or IDs
- the next search angle or next action
