# Practice app: AddisVerify KYC demo

A small app with planted bugs, for learning Mirmera on something real
without touching your own systems. No dependencies besides Node.

```bash
node practice-app/server.mjs        # http://localhost:4000
```

- `spec.md`: the requirements, to paste into Mirmera as an artifact.
- It has nine planted bugs. `PLANTED=classic node practice-app/server.mjs` runs it with
  the first four only, as the agent evals do (PowerShell: `$env:PLANTED="classic"; node practice-app/server.mjs`,
  cmd: `set "PLANTED=classic" && node practice-app/server.mjs`).
- `ANSWERS.md`: the planted bugs. Read it after your sessions, not before.

Data is kept in memory and resets when you restart the server.

## A second app: AddisMart shop

```bash
node practice-app/shop.mjs          # http://localhost:4200
```

- `shop-spec.md`: its requirements.
- `SHOP-ANSWERS.md`: its six planted bugs, for afterwards.

Measure the exploration agent on it with
`python -m app.evals.explore_eval --provider local --app shop` (from `backend/`).
