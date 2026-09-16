# UI Responsiveness QA — v0.23.0

This pass validates desktop interactions with an emphasis on keeping the Qt
event loop responsive and avoiding repeated database/disk work.

## Validated interaction paths

- Application launch with an established bank connection and with first-run setup.
- Sidebar navigation between every page, including repeated back-and-forth navigation.
- Overview → Pay Periods checklist navigation.
- Pay Period month/year/work-list changes and inline editing.
- Bills selection, account choices, save/deactivate workflow.
- Transactions month/account/state/reconciliation filters, free-text search,
  row selection, context menus, reconciliation actions, and funding validation.
- Reconciliation audit loading, search/state filters, manual refresh, and
  Reconcile history.
- Reports month/year preview and background export.
- Settings connection state, account roles, backup history, backup creation,
  restore, and bank-sync completion callbacks.

## Performance changes

### Lazy page loading and dirty-page reuse

The main window constructs pages without loading their data. Only the page
selected at startup is loaded. A page that has already been rendered is reused
when revisited until a write or bank-data change marks that page dirty.
Dependent hidden pages are invalidated instead of being rebuilt immediately.

### Transactions

- Transaction months are discovered with a compact distinct-period query.
- The default/current-month view loads only that month from SQLite.
- Other selected months are also queried by month rather than loading thousands
  of rows and filtering them in Python.
- Search, account, posted/pending, and reconciliation-state filters run against
  already-loaded rows.
- Free-text search is debounced to avoid rebuilding the table on every keypress.
- Review-candidate analysis is cached and computed in bulk. Bill instances are
  loaded once per transaction month and merchant aliases once per analysis pass.
- The All dates selection is preserved correctly across reloads.

### Reconciliation

- Full historical audit and integrity diagnostics run in a worker thread.
- Diagnostics reuse the audit already produced by that worker instead of
  rebuilding the historical audit a second time.
- Search and state filters operate entirely on cached audit results.
- The safety backup for Reconcile history is created inside the worker before
  reconciliation begins, so the GUI does not freeze before the busy indicator.
- Bill-instance and merchant-alias data are cached during automatic/history
  matching, with bill state deliberately reloaded after internal-transfer
  matches so correctness is preserved.

### Settings and backups

- Backup-history display no longer performs PRAGMA integrity_check against every
  retained snapshot whenever Settings opens.
- Newly created backups are still integrity-checked before acceptance.
- A selected backup is still fully integrity-checked before restore, and the
  restored database is checked again afterward.

### Reports

Report previews and exports query bank transactions only for the selected month
instead of loading a broad transaction history and discarding unrelated rows.

### Pay Periods

Missing active bill instances for current/future periods are materialized in a
single SQLite transaction rather than opening repeated connections/transactions
for every bill and cycle.

## Safety/correctness invariants

- SQLite remains the financial source of truth.
- No optimization initiates a payment or transfer.
- Plaid Item/link safeguards are unchanged.
- Historical bill instances are not synthesized or rewritten by the batching
  optimization.
- Reconciliation ambiguity rules remain conservative.
- Internal-transfer pairing refreshes bill state before the normal matching
  phase, preventing a newly verified bill from receiving a second match.
- Manual Refresh actions remain available when the user explicitly wants to
  reload local state.
