# Local Data Directory

Place private CSV or database files here for local development.

This directory is intentionally ignored by git. Configure table mappings with
`TEXT2SQL_TABLES_JSON`, for example:

```bash
export TEXT2SQL_TABLES_JSON='{"resources":"data/resources.csv","sea_cable_faults":"data/sea_cable_faults.csv"}'
```

Do not commit real customer, production, or private datasets.
