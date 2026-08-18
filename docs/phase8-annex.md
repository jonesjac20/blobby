# Blobby — Deployment Pipeline Annex (Phase 8+)

Appends to [`GUIDEBOOK.md`](GUIDEBOOK.md) once its Phase 7 exit criterion is checked off: someone off your LAN has loaded the game URL and played a round, by hand. Nothing in this annex touches game logic, protocol, or client code. The client already opens `${location.protocol}//${location.host}/ws`, so HTTPS on a domain becomes `wss` without a protocol change.

**What this annex is.** Phase 7's home VirtualBox VM was the POC. Phases 8–9 automate packaging (CI, the container, GHCR) — that is the Phase 7 "rsync a folder" work, done properly. Phases 10–13 are an explicitly-asked move of **production** onto one cloud EC2: `main` deploys there, a domain can point at it, the home box stops being the live game. That is not automating the Spectrum port forward; if you treat it as scope creep against Phase 7, you will aim every checkbox at the wrong host. Phase 14 is an optional Fargate **PR preview**. It is not a second production.

Two productions are forbidden. Once the EC2 serves `main`, the home VM must not keep receiving `latest`. It can stay as a Phase 7 artifact.

**How to use this doc.** Same rules as the source Guidebook: boxes go `- [ ]` to `- [x]` as you clear them. Work Phases 8–9, then 10–13 top to bottom on the EC2 path. Phase 10's Agent boxes (workflow, `/healthz`, runner install script) are host-agnostic and can land before AWS exists; do **not** register a production runner on the home VM. Phase 10's Human/Both boxes and exit criterion wait until Phase 11 has an EC2. Phase 14 is optional: it can start after Phase 9 and does not replace 10–13. If the code and this doc disagree once something is built, the doc is the bug — fix it here, and log the mismatch under [Divergence](#divergence-from-this-annex) rather than silently editing around it.

## Legend

Same as `GUIDEBOOK.md`:

- **[Agent]** — code, configs, workflow files, scripts. Done end-to-end from chat.
- **[Human]** — your hands: GitHub UI tokens, AWS console / CLI, DNS, watching a browser tab on the live URL.
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
- [x] **[Agent]** Write `../docker-compose.yml` for the game host: one `game` service from the built image, `8000:8000`, `restart: unless-stopped`.
- [x] **[Agent]** Extend the workflow (or add `.github/workflows/build.yml`) so that on push to `main`, after tests pass, it builds the image and pushes to GHCR tagged with the git SHA and `latest`.
- [x] **[Human]** Set GHCR package visibility, or give the game host a token scoped to pull it. After the first successful `build` job on `main`: repo → Packages → `blobby` → Package settings → Change visibility. Public is the simple POC path. Private needs a `read:packages` PAT on the host in a `.env` that stays out of git.
- [x] **[Both]** Verify: `docker build`, `docker run -p 8000:8000 <image>` locally, confirm a browser tab can join exactly as it did in Phase 3/4 bare-metal.

**Exit criteria:** the container serves the same game the bare-metal process did. Parity check, not a new feature.

---

## Phase 10 — Deploy: self-hosted runner, CD

Goal: `main` lands on the production EC2 without an SSH session.

Why a self-hosted runner: compose runs on the game host. A GitHub-hosted job would have to SSH or SSM in to run `docker compose`; putting the runner on the box keeps `deploy.yml` as pull / up / curl localhost. That is the same shape this phase always had. The CGNAT and router-forward story in `GUIDEBOOK.md` Phase 7 is why the *home* VM needed an outbound-polling runner — it is not why the runner exists on the EC2. GitHub-hosted + SSM is an alternative, not this path.

Do not register a production runner on the home VirtualBox VM. That box is not the live game.

`/healthz` is specified in Phase 13. Implement **only that route** here so the deploy job can fail closed (200 if the last successful tick is recent, 503 otherwise). JSON logs, `/metrics`, Prometheus stay Phase 13.

- [x] **[Agent]** Add `.github/workflows/deploy.yml`: `runs-on: [self-hosted, linux, blobby-prod]`, `needs: build`, triggered on push to `main`. Steps: `docker compose pull` of the git SHA (`BLOBBY_IMAGE_TAG`), `docker compose up -d --force-recreate`, curl `http://127.0.0.1:8000/healthz` with a per-request timeout, fail the job if it doesn't return 200 within a few seconds.
- [x] **[Agent]** Add `/healthz` to [`server/main.py`](../server/main.py): register it **before** `/{name}`, or the whitelist 404s it. 200 if the tick loop's last successful tick was recent, 503 otherwise. Tests for 503 before any tick, 200 after a tick, 503 when the stamp is aged out.
- [x] **[Agent]** Install-helper script for the runner as a systemd service (the runner installer ships `svc.sh` for this).
- [ ] **[Human]** After Phase 11 has an EC2: GitHub → repo Settings → Actions → Runners → add a self-hosted runner, copy the registration command and token, run it **on the EC2** with `--labels blobby-prod` (in addition to the defaults). Without that label `deploy.yml` will not pick the runner. This is a one-time interactive token step tied to your GitHub session, can't be done from chat.
- [ ] **[Human]** Run the systemd helper on the EC2 so the runner survives logout/reboot. Confirm it shows **Idle**.
- [ ] **[Both]** Verify: push a trivial change to `main`, watch the Action run on that runner, confirm the live game at the Elastic IP (later the domain) updates without SSH.

**Exit criteria:** push to `main` from a laptop, no SSH session, and the deployed game on the cloud URL reflects the change within a minute or two.

---

## Phase 11 — Infra as code: production EC2

Goal: the production host is a Terraform-managed EC2 with a public IP. `terraform destroy` then `apply` brings the game — pipeline included — back. [`scripts/vm_bootstrap.sh`](../scripts/vm_bootstrap.sh) remains how Phase 7's home box was stood up; it is not the production rebuild path.

Sketch: default VPC (or one public subnet), security group (`8000/tcp` now; `443` if Caddy/nginx is in compose), Ubuntu `t3.micro`-class, Elastic IP, `user_data` that installs Docker + the compose plugin. No autoscaling, no ALB, no API Gateway. Same [`docker-compose.yml`](../docker-compose.yml) as Phase 9: `ghcr.io/jonesjac20/blobby:latest`.

Public URL: Elastic IP first, then a domain A record (Route 53 or Cloudflare). TLS is Caddy/nginx on the instance or Cloudflare in front — not ACM+ALB, not TLS inside `server/main.py`. Mixed content is why TLS belongs with the domain: an `https://` page will open `wss://`.

- [ ] **[Human]** AWS account. IAM (or keys) so Terraform can create EC2, EIP, security groups, and an instance profile if you want SSM later.
- [x] **[Agent]** Add `infra/prod/` Terraform for the sketch above. Output the public IP. `user_data` must not register a GitHub runner — that token is Human, Phase 10.
- [ ] **[Human]** `terraform apply`, confirm the Elastic IP answers. First bring-up may be a manual `docker compose up -d` on the box so you can play a round off-LAN before CD exists.
- [ ] **[Human]** Point a domain A record at the Elastic IP. Terminate TLS with Caddy/nginx on the instance or Cloudflare in front. Confirm a browser at `https://<domain>` loads the game and the WebSocket connects (`wss`).
- [ ] **[Both]** Verify: `terraform destroy`, then `apply`, re-register the Phase 10 runner with `--labels blobby-prod` if the instance was replaced, push to `main`, confirm deploy succeeds on the new box.

**Exit criteria:** destroy the host with Terraform, get the game back online — pipeline included — in the time it takes to apply and paste one runner token.

---

## Phase 12 — Rollback

Goal: a bad deploy is a one-command problem, not an incident. Host is the production EC2.

- [ ] **[Agent]** Add `scripts/rollback.sh` (or a `make rollback` target) that re-runs compose against the previous successful SHA tag instead of `latest`.
- [ ] **[Agent]** Have `deploy.yml` record the previous tag somewhere the rollback script can read it, or have the script query the last two tags from GHCR directly.
- [ ] **[Human]** Deliberately deploy something broken, confirm the Phase 10 healthcheck catches it, then run the rollback script and confirm recovery.

**Exit criteria:** a broken deploy is fixed by running one script, not by remembering the last good commit and re-triggering CI by hand.

---

## Phase 13 — Observability (optional)

Goal: you find out the server is unhealthy before a player does. Ship Phases 8–12 first; this phase is not required. Host is the production EC2.

- [x] **[Agent]** `/healthz` on [`server/main.py`](../server/main.py): 200 if the tick loop's last tick was recent, 503 otherwise. Pulled forward to Phase 10 so `deploy.yml` can curl it — check that box off here if it already landed. This reuses the failed-tick catch already noted in `GUIDEBOOK.md`'s divergence section rather than adding new failure handling.
- [ ] **[Agent]** Switch logging to structured JSON: one line per tick failure, join, disconnect.
- [ ] **[Agent, stretch]** Expose `/metrics` in Prometheus text format: connected sockets, tick duration, tick failure count.
- [ ] **[Human, stretch]** Add `prometheus` and `grafana` services to `docker-compose.yml`, point Prometheus at `/metrics`, build one dashboard panel.
- [ ] **[Both]** Verify: force a tick failure deliberately, confirm `/healthz` flips before a player would notice on their own.

**Exit criteria:** none required — optional phase.

---

## Phase 14 — PR preview: Fargate (optional)

Goal: a pull request gets a disposable Fargate task running that PR's image; `main` still deploys only to the production EC2.

Starts after Phase 9 (the Dockerfile and GHCR exist). Does not replace Phases 10–13 and does not wait on them. This is not a second live host. Fargate is the wrong production: always-on Fargate plus an ALB is a more expensive, less stable way to run one process whose IP you want to keep.

Why this does not collide with Phase 10: three couplings would actually share a host, a tag, or a URL, and a preview must break all three.

- Preview jobs are `runs-on: ubuntu-latest` and talk to AWS APIs. They never `runs-on: self-hosted`. That runner is production; a self-hosted preview job would `docker compose` on the live EC2.
- Preview images are tagged `pr-<number>` (and maybe the PR SHA). They never overwrite `latest`. Phase 9's `build` job already refuses to publish `latest` from a PR; production compose is `latest` (Human bring-up) or the main git SHA (`BLOBBY_IMAGE_TAG` from deploy.yml), never a `pr-*` tag.
- The preview URL is `http://<task-public-ip>:8000`, commented on the PR. The production URL is the Elastic IP, then the domain.

On PR close: `terraform destroy` for that PR's workspace (or desired-count 0). Merge to `main` still only deploys via Phase 10. Closing a PR must not touch the EC2, and destroy never SSHs to it.

Native `/ws` still works on the task — one container, port 8000, same origin. No API Gateway (that would rewrite the socket model). No ALB for v1: idle cost is real, and its idle timeout is a WebSocket footgun. Task `assign_public_ip = ENABLED` in a public subnet, security group `8000/tcp`.

GHCR pull: Phase 9's public package path is what makes Fargate simple (ECS cannot use `GITHUB_TOKEN` the way Actions can). If the package is private, push the `pr-N` tag to ECR instead, or store a `read:packages` PAT in Secrets Manager. Do not reuse the production host's GHCR token as a Fargate secret.

The preview smoke curl wants `/healthz`. If Phase 10 has not landed that route yet, curl `/` and swap once it exists.

- [ ] **[Agent]** Add `infra/preview/` Terraform: default VPC, ECS cluster, Fargate task + service (0.25 vCPU / 0.5 GB is enough), security group `8000/tcp`, CloudWatch logs. Variable: `image` tag. No autoscaling. One workspace or state key per PR so two open PRs do not clobber each other. Separate from `infra/prod/` — different state, different lifecycle.
- [ ] **[Agent]** Add `.github/workflows/preview.yml` on `pull_request` (`opened` / `synchronize` / `reopened`): after `test` is green, build and push `pr-${{ github.event.number }}` **without** tagging `latest`, `terraform apply -var=image=...`, wait for the task `RUNNING`, curl `/healthz` (or `/`), comment the URL on the PR. `runs-on: ubuntu-latest`. AWS auth via OIDC or an access-key secret — never the production runner, never `self-hosted`.
- [ ] **[Agent]** Same workflow on `pull_request` `closed`: `terraform destroy` for that PR's workspace/state. Does not SSH to the EC2 and does not run `deploy.yml`.
- [ ] **[Human]** IAM for the GitHub OIDC role used by preview (ecs, `iam:PassRole`, logs, ec2 security groups). Confirm GHCR is public, or set up ECR for the `pr-N` tag.
- [ ] **[Both]** Verify: open a PR that changes a visible string, play a round on the preview URL, confirm the production URL still serves `main`. Close the PR, confirm the task is gone and the EC2 game is untouched.

**Exit criteria:** open a PR, play a round on the preview URL, merge or close, confirm the Fargate task is gone and the production EC2 is untouched.

---

## Divergence from this annex

- **Image is `python:3.13-slim`, not 3.12.** The annex originally specified `python:3.12-slim`. CI, README, and the laptop are already 3.13, so the image matches the version tests already run. aiohttp does not need 3.12. The Phase 9 Dockerfile box above was updated to 3.13 to match.
- **Production is a cloud EC2, not the home VirtualBox VM.** Phase 7 proved the game off-LAN on the home box. Phases 10–13 were rewritten to put `main` on Terraform-managed EC2; the home VM is not a `latest` target. The CGNAT/self-hosted rationale in the original Phase 10 is Phase 7 history.
- **`/healthz` ships with Phase 10.** Phase 13 still owns JSON logs and `/metrics`. The deploy job curls `/healthz`, so the route is pulled forward rather than leaving CD with nothing to fail closed on.
- **`deploy.yml` is `workflow_call`; `needs: build` lives on the `ci.yml` caller.** GitHub `needs:` is intra-workflow only. A standalone `on: push` deploy file cannot wait for Phase 9's `build` job. PRs still never deploy: `build` is skipped off `main`, and a skipped `needs` skips `deploy`.
- **Deploy pins the git SHA, not only `latest`.** Parallel `build` jobs on GitHub-hosted runners can retag `latest` out of order. `BLOBBY_IMAGE_TAG=${{ github.sha }}` in `deploy.yml` (Compose default remains `latest` for a Human first bring-up) plus `concurrency` on `ci.yml` close that window. `runs-on` also requires `blobby-prod` so a leftover home-VM runner cannot take the job.
- **`infra/prod/` creates its own public VPC.** The annex sketch allows default VPC *or* one public subnet. This AWS account has no default VPC in `us-east-1`, so Terraform owns a `/16` + public subnet + IGW instead of looking up `default = true`.

---

## Deferred — do not build unless explicitly asked

- Kubernetes, or Fargate / ECS / any scheduler, as the **production** host. One authoritative server does not need a replica set. Compose on one EC2 is the production shape. ECS Fargate is an exception only for the disposable PR tasks in Phase 14.
- AWS API Gateway (WebSocket or HTTP) in front of `/ws`. It would terminate the socket and speak connection IDs; the client is a native WebSocket to the same origin.
- An ALB as a required front door. Idle cost is real, and the default idle timeout is a WebSocket footgun. Domain + TLS on the instance (or Cloudflare) is the v1 public URL.
- Blue/green or canary deploys. Meaningless with a single instance.
- A secrets manager (Vault, etc.). Tokens live in GitHub / AWS IAM and a `.env` kept out of git until that is actually insufficient.
- TLS termination inside `server/main.py`. Phase 11 terminates at Caddy/nginx or Cloudflare.
- Auto-scaling anything. One authoritative server owns all state; it cannot be horizontally scaled without redesigning that model, which is out of scope here.

---

## Annex exit criteria

- [ ] Push to `main` with no SSH session open, and the deployed game updates live on the cloud URL (Elastic IP, then the domain).
- [ ] `terraform destroy` then `apply` for `infra/prod/`, re-paste one runner token, and the game — pipeline included — is back.
- [ ] A deliberately broken deploy is caught by the healthcheck and fixed by one rollback command.
- [ ] **[Optional]** A PR preview comes up, a round plays on that URL, the PR closes, the task is gone, and production is untouched.
