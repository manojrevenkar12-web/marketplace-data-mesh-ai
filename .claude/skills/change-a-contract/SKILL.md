---
name: change-a-contract
description: Change an existing data contract or transform without breaking consumers; use for renames, removals, type or classification changes, SLA or quality changes.
---

# Change a contract safely

1. Before editing, call `lineage` with the product and the column to see who reads it, and `describe_dataset`
   for the current contract and recent runs.
2. Make the change, then call `review_change` with the changed files. Its verdict is the platform's:
   - **block**: do not merge. For a rename or removal, add the new column next to the old one, set
     `deprecated_on: <today>` on the old one, bump `version`, tell the consumers listed in the findings, and
     remove the old column only after the deprecation window (`config/platform.yaml`).
   - **needs_approval**: merge after the approvers named in the findings agree (a reclassification that takes
     access away needs the owner; lowering a classification needs data governance).
   - **approve**: merge.
3. A changed transform is dry-run on recent partitions; read the row-count comparison in the findings and
   explain any change above the threshold in the pull request.
4. Never edit `config/access.yaml` or a classification to make a query work. Ask for access instead.
