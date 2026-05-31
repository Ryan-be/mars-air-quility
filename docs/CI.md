# CI Runner Runbook

## Runner inventory

Two runners, split by label so Phase 4's privileged-Docker workload
gets its own machine without complicating the lightweight stuff.

| Name | Type | Address | User | Labels | Purpose |
|------|------|---------|------|--------|---------|
| gittea-ci | Proxmox CT | 192.168.0.99 | root | ubuntu-latest, hil | All workflows except Pi image build |
| ci-mlss-pi | Proxmox VM | (added at Phase 4 Task 10a) | (added at Phase 4 Task 10a) | pi-image-builder | Pi image build only — privileged Docker for pi-gen chroot |

See §IP change recovery below for what to do when Gitea's IP moves
(it has changed twice during initial setup — treat as volatile).

## IP change recovery

If Gitea's IP changes:

1. SSH to each runner (CT first, then VM once provisioned).
2. Edit `/etc/act_runner/config.yaml` and update the
   `gitea_instance_url` field.
3. `sudo systemctl restart act_runner`.
4. Verify in Gitea UI → Settings → Actions → Runners that both
   runners show "online" within ~30 seconds.

## Smoke test

The `.gitea/workflows/_runner_smoke.yml` workflow is the canary —
`workflow_dispatch` it after any runner change and verify the labels
in the log output match what's declared in this runbook.
