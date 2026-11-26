# Cursor Form Builder Knowledge Pack

This pack gives Cursor the business-level knowledge extracted from the active Digi Punjab form schemas.

## Read order

1. `form-builder.mdc` — mandatory implementation rules.
2. `business_patterns.json` — observed business modules, section patterns, datasource patterns, autofill patterns and cross-field rules.
3. `validation-profiles.json` — canonical semantic validation profiles.
4. `datasource-registry.json` — approved API/datasource patterns.
5. `examples/` — representative schemas for common journeys.

Do not paste all raw schemas into every Cursor prompt. Keep the raw JSON corpus available for targeted lookup and use the generated catalogue for normal reasoning.

## How to install in the application repository

Copy this directory into the repository as:

```text
docs/form-builder/
```

Copy `form-builder.mdc` into:

```text
.cursor/rules/form-builder.mdc
```

Keep the raw schemas outside the always-loaded rule context. Add a search/index command so Cursor can inspect only relevant forms when necessary.

## Recommended commands

```bash
pnpm form:lint
pnpm form:validate --file path/to/form.json
pnpm form:search --query "residence certificate address eKYC"
pnpm form:pattern-report
```

## Important distinction

Observed patterns are evidence from the corpus. Recommended canonical rules are design decisions for the new Form Builder. Cursor must label the difference and must not silently change an existing backend contract.
