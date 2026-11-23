# `infra` folder

This folder contains **infrastructure configurations** for the monorepo (for example: Dockerfiles, Terraform scripts, deployment manifests, Nginx configs, etc.).

- **Main purpose**: to centralize the definition of the infrastructure required to run the company's applications and services.
- **Recommendation**: document how to provision and deploy the infrastructure.

> _Spanish version: [README.es.md](./README.es.md)._

## Hardening

`infra/hardening/` holds the server baseline (non-root deploy user, no root SSH, folder permissions, firewall) as scripts and tests; see [docs/security/server-hardening.md](../docs/security/server-hardening.md).
