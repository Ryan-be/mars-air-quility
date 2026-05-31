# CI Runner Runbook

## Runner inventory

Two runners, split by label so Phase 4's privileged-Docker workload
gets its own machine without complicating the lightweight stuff.

Addresses below are placeholders — the real LAN IPs live in your
local Proxmox config (not in source control, per the
`test_no_private_ips_committed.py` privacy guard). Replace
`192.0.2.x` (RFC 5737 documentation range) with your actual values
when configuring.

| Name | Type | Address (placeholder) | User | Labels | Purpose |
|------|------|-----------------------|------|--------|---------|
| gittea-ci | Proxmox CT | `192.0.2.99` | root | ubuntu-latest, hil | All workflows except Pi image build |
| ci-mlss-pi | Proxmox VM | `192.0.2.100` (provision at Phase 4 Task 10a) | root | pi-image-builder | Pi image build only — privileged Docker for pi-gen chroot |

The actual address of `gittea-ci` was set in the Phase 0 CT
provisioning. To recover it: `pct list` on the Proxmox host shows
the CTID; `pct config <CTID> \| grep ^net0` shows the assigned IP.

## IP change recovery

If Gitea's IP changes (it has changed three times during initial
setup — treat as volatile):

1. SSH to each runner (CT first, then VM once provisioned).
2. Edit `/etc/act_runner/config.yaml` and update the
   `gitea_instance_url` field.
3. `sudo systemctl restart act_runner`.
4. Verify in Gitea UI → Settings → Actions → Runners that both
   runners show "online" within ~30 seconds.

## Smoke test

The `.gitea/workflows/runner-smoke.yml` workflow is the canary —
it fires on every push (and via `workflow_dispatch`). After any
runner change, look at the latest run on the
`feature/gitea-actions-pipeline` branch and verify the labels in
the log output match what's declared in this runbook.
</content>
</invoke>