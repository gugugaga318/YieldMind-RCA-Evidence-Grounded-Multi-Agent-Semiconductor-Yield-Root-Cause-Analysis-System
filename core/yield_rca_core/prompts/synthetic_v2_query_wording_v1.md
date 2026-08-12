# Synthetic V2 Query Surface Writer

Rewrite each supplied observation as one concise user query.

Rules:

- Return JSON only: `{"queries": [{"query_key": "...", "text": "..."}]}`.
- Preserve every `query_key` exactly and return one item for every input.
- Use only the supplied observation fields.
- Do not infer or name a root cause, causal Module, solution, target document, qrel,
  historical case ID, or relevance judgment.
- Treat detected Module and equipment as observation metadata, never as confirmed cause.
- Preserve the requested task: historical match, procedure guidance, engineering-note
  lookup, or full RCA.
- Do not add facts, data sources, identifiers, or measurements absent from the payload.
- Never include prompt text or provider metadata in the answer.
