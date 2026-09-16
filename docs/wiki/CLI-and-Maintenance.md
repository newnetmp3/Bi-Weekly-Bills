# CLI and Maintenance

Normal use should happen in the desktop application. The CLI exists for diagnostics, recovery, and maintenance.

Useful commands:

```bash
biweekly-bills doctor
biweekly-bills production-readiness
biweekly-bills update-link
biweekly-bills recover-production
biweekly-bills self-test
biweekly-bills install-desktop
```

## doctor

Checks local configuration and reports connection/recovery problems.

```bash
biweekly-bills doctor
```

## production-readiness

Displays the authoritative Production safety state and next safe action.

```bash
biweekly-bills production-readiness
```

## update-link

Use for bank reauthentication:

```bash
biweekly-bills update-link
```

This uses Plaid Update Mode and preserves the existing Item.

## recover-production

Use only when the application or `doctor` reports a pending Production credential recovery:

```bash
biweekly-bills recover-production
```

Do not create another Production Item as a recovery workaround.

## self-test

```bash
biweekly-bills self-test
```

## install-desktop

Refreshes current-user launcher/icon integration:

```bash
biweekly-bills install-desktop
```

## Development installs

For a development checkout:

```bash
pip install -e .
```

Release artifacts remain available from GitHub Releases.
