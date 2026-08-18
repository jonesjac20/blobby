# Blobby — Deployment Pipeline Annex (Phase 8+)

Appends to [`GUIDEBOOK.md`](GUIDEBOOK.md) once its Phase 7 exit criterion is checked off: someone off your LAN has loaded the game URL and played a round, by hand. Nothing in this annex touches game logic, protocol, or client code — it automates the parts of Phase 7 you did manually. If a step below doesn't correspond to something you already did by hand in Phase 7, that's scope creep, not a gap Phase 7 left open.

**How to use this doc.** Same rules as the source Guidebook: work top to bottom, each phase must work before the next starts, boxes go `- [ ]` to `- [x]` as you clear them. If the code and this doc disagree once something is built, the doc is the bug — fix it here, and log the mismatch under [Divergence](#divergence-from-this-annex) rather than silently editing around it.

## Legend

Same as `GUIDEBOOK.md`:

- **[Agent]** — code, configs, workflow files, scripts. Done end-to-end from chat.
- **[Human]** — your hands: pulling a token from the GitHub UI, running a command on the actual VM, watching something happen.
- **[Both]** — the agent stages it, you confirm it's correct.

## Prerequisite

- [x] **[Human]** `GUIDEBOOK.md` Phase 7 exit criterion is checked off before starting Phase 8.

---

## Phase 8 — CI: build and test

Goal: every push is tested and linted before anything gets packaged.

- [x] **[Agent]** Add `.github/workflows/ci.yml`: on push and pull_request, checkout, set up Python, `pip install -r requirements-dev.txt`, run `pytest`, run `ruff check`.
- [x] **[Agent]** Add `ruff` to [`requirements-dev.txt`](../requirements-dev.txt) if it isn't already there.
- [x] **[Human]** Push a branch, open a PR, confirm the checks show up and run.
- [x] **[Both]** Verify: break a test on purpose, push it, confirm CI goes red before it's merged back.
- [x] **[Human, optional]** Turn on branch protection requiring the check to pass before merge. Not required for a one-person POC repo, but cheap and worth mentioning if asked.

**Exit criteria:** you would not merge a PR with a failing check, whether or not that's technically enforced.

---

## Phase 9 — Package: containerize

Goal: the server becomes a deployable image, not a folder you rsync.

- [x] **[Agent]** Write `../Dockerfile`: `python:3.13-slim` base, copy and `pip install -r requirements.txt` only (runtime pin, not the dev requirements — this mirrors the split `GUIDEBOOK.md` already documents), copy `server/` and the whitelisted `client/` files, `EXPOSE 8000`, `CMD ["python", "-m", "server.main"]`.
- [x] **[Agent]** Write `../docker-compose.yml` for the VM: one `game` service from the built image, `8000:8000`, `restart: unless-stopped`.
- [x] **[Agent]** Extend the workflow (or add `.github/workflows/build.yml`) so that on push to `main`, after tests pass, it builds the image and pushes to GHCR tagged with the git SHA and `latest`.
- [ ] **[Human]** Set GHCR package visibility, or give the VM a token scoped to pull it. After the first successful `build` job on `main`: repo → Packages → `blobby` → Package settings → Change visibility. Public is the simple POC path. Private needs a `read:packages` PAT on the VM in a `.env` that stays out of git.
- [x] **[Both]** Verify: `docker build`, `docker run -p 8000:8000 <image>` locally, confirm a browser tab can join exactly as it did in Phase 3/4 bare-metal.

**Exit criteria:** the container serves the same game the bare-metal process did. Parity check, not a new feature.

---

## Phase 10 — Deploy: self-hosted runner, CD

Goal: `main` lands on the VM without an SSH session.

Why a self-hosted runner and not a webhook: `GUIDEBOOK.md` and the source plan already establish that this VM may sit behind CGNAT, and the SSH forward (`2222 → VM:22`) only works because it's outbound-initiated on the VM's side for anything that isn't the game port itself. A webhook-triggered deploy would need GitHub to reach *in* to the VM, recreating the exact problem already solved for SSH. A self-hosted runner polls GitHub outbound instead, so it needs nothing new opened on the router.

- [ ] **[Human]** GitHub → repo Settings → Actions → Runners → add a self-hosted runner, copy the registration command and token.
- [ ] **[Human]** Run the registration command on the VM. This is a one-time interactive token step tied to your GitHub session, can't be done from chat.
- [ ] **[Agent]** Install the runner as a systemd service (the runner installer ships `svc.sh` for this).
- [ ] **[Agent]** Add `.github/workflows/deploy.yml`: `runs-on: self-hosted`, `needs: build`, triggered on push to `main`. Steps: `docker compose pull`, `docker compose up -d`, curl `/healthz` (Phase 13), fail the job if it doesn't return 200 within a few seconds.
- [ ] **[Human]** Confirm ufw and the router forward from Phase 7 are unchanged — the runner adds no new inbound requirement.
- [ ] **[Both]** Verify: push a trivial change to `main`, watch the Action run on the self-hosted runner, confirm the live game updates without touching the VM by hand.

**Exit criteria:** push to `main` from a laptop, no SSH session, no VPN, and the deployed game reflects the change within a minute or two.

---

## Phase 11 — Infra as code: full host bootstrap

Goal: the VM can be rebuilt from nothing but `git clone` and one script.

- [ ] **[Agent]** Rewrite [`scripts/vm_bootstrap.sh`](../scripts/vm_bootstrap.sh): install Docker + the compose plugin, `ufw allow 8000/tcp`, install and register the runner as a systemd service, accepting a runner token as an argument or env var so it runs non-interactively.
- [ ] **[Agent]** Document the "from nothing" path in the script header or repo README: fresh Ubuntu, `git clone`, `sudo scripts/vm_bootstrap.sh <runner-token>`.
- [ ] **[Human]** Actually run this against a throwaway VM (a VirtualBox snapshot revert is the cheap way to get one). This is the one item on this whole annex that can't be faked — it's the actual claim IaC is making.
- [ ] **[Both]** Verify: after the rebuild, the runner shows online in GitHub, and a push to `main` deploys successfully to the rebuilt VM.

**Exit criteria:** destroy the VM, get the game back online — pipeline included — in the time it takes to run one script and paste one token.

---

## Phase 12 — Rollback

Goal: a bad deploy is a one-command problem, not an incident.

- [ ] **[Agent]** Add `scripts/rollback.sh` (or a `make rollback` target) that re-runs compose against the previous successful SHA tag instead of `latest`.
- [ ] **[Agent]** Have `deploy.yml` record the previous tag somewhere the rollback script can read it, or have the script query the last two tags from GHCR directly.
- [ ] **[Human]** Deliberately deploy something broken, confirm the Phase 10 healthcheck catches it, then run the rollback script and confirm recovery.

**Exit criteria:** a broken deploy is fixed by running one script, not by remembering the last good commit and re-triggering CI by hand.

---

## Phase 13 — Observability (optional)

Goal: you find out the server is unhealthy before a player does. Ship Phases 8–12 first; this phase is not required.

- [ ] **[Agent]** Add a `/healthz` route to [`server/main.py`](../server/main.py): 200 if the tick loop's last tick was recent, 503 otherwise. This reuses the failed-tick catch already noted in `GUIDEBOOK.md`'s divergence section rather than adding new failure handling.
- [ ] **[Agent]** Switch logging to structured JSON: one line per tick failure, join, disconnect.
- [ ] **[Agent, stretch]** Expose `/metrics` in Prometheus text format: connected sockets, tick duration, tick failure count.
- [ ] **[Human, stretch]** Add `prometheus` and `grafana` services to `docker-compose.yml`, point Prometheus at `/metrics`, build one dashboard panel.
- [ ] **[Both]** Verify: force a tick failure deliberately, confirm `/healthz` flips before a player would notice on their own.

**Exit criteria:** none required — optional phase.

---

## Divergence from this annex

- **Image is `python:3.13-slim`, not 3.12.** The annex originally specified `python:3.12-slim`. CI, README, and the laptop are already 3.13, so the image matches the version tests already run. aiohttp does not need 3.12. The Phase 9 Dockerfile box above was updated to 3.13 to match.

---

## Deferred — do not build unless explicitly asked

- Kubernetes or any orchestrator beyond `docker compose`. One VM does not need a scheduler.
- Migrating off the home VirtualBox VM to a cloud host. Legitimate if CGNAT ever forces the ngrok/Cloudflare Tunnel fallback already flagged in `GUIDEBOOK.md` Phase 7, not worth doing just to look more cloud-native.
- Terraform or any cloud IaC tool. Only relevant if the above happens; a bash bootstrap script is the honest tool for one non-cloud host.
- Blue/green or canary deploys. Meaningless with a single instance.
- A secrets manager (Vault, etc.). The only secrets are a GHCR pull token and a runner registration token; a `.env` file kept out of git is sufficient until that changes.
- TLS termination inside `server/main.py`. Cloudflare Tunnel, already the CGNAT fallback, gets this for free if it's ever needed.
- Auto-scaling anything. One authoritative server owns all state; it cannot be horizontally scaled without redesigning that model, which is out of scope here.

---

## Annex exit criteria

- [ ] Push to `main` with no SSH session open, and the deployed game updates live.
- [ ] Destroy the VM, rerun the bootstrap script plus one pasted token, and the game — pipeline included — is back.
- [ ] A deliberately broken deploy is caught by the healthcheck and fixed by one rollback command.
