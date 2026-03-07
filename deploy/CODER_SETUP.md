# Coder + CloudBroker + Cloudburst Setup

This document describes the full setup for the Coder + vcluster + Cloudburst use case.

## Architecture

- **Host cluster**: Coder, CloudBroker (shared), Tailscale operator
- **Per workspace**: vcluster with Cloudburst; connects to shared CloudBroker
- **Credentials**: Per-user (each user provides their own cloud provider credentials)

## 1. Install Prerequisites on Host Cluster

```bash
# CloudNative-PG operator (for CloudBroker PostgreSQL)
kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.28/releases/cnpg-1.28.1.yaml

# Tailscale operator (for VM join)
# See: https://tailscale.com/docs/kubernetes/operator/install
```

## 2. Deploy CloudBroker

```bash
cd cloudbroker
# Create secrets (see deploy/README.md)
kubectl apply -k deploy/
```

## 3. Deploy Coder

Coder uses CloudNative-PG for PostgreSQL. See [CODER_CNPG_DEPLOY.md](CODER_CNPG_DEPLOY.md) for full steps:

```bash
# Create coder-db-app secret, deploy coder-postgres-cnpg.yaml, create coder-db-url secret, then:
helm repo add coder-v2 https://helm.coder.com/v2
helm install coder coder-v2/coder -n coder -f deploy/coder-values.yaml
```

### OIDC (Google sign-in)

To enable Google sign-in, create an OAuth 2.0 client in [Google Cloud Console](https://console.cloud.google.com/apis/credentials):

1. Add authorized redirect URI: `https://<your-coder-url>/api/v2/users/oidc/callback` (or `http://localhost:30180/api/v2/users/oidc/callback` for local dev)
2. Set env vars and run deploy:

```bash
export CODER_OIDC_CLIENT_ID="your-client-id"
export CODER_OIDC_CLIENT_SECRET="your-client-secret"
export CODER_OIDC_EMAIL_DOMAIN="example.com"   # optional: restrict to domain
export CODER_ACCESS_URL="http://localhost:30180"  # must match redirect URI
make deploy-coder
```

Or pass inline: `CODER_OIDC_CLIENT_ID=xxx CODER_OIDC_CLIENT_SECRET=yyy make deploy-coder`

## 4. Configure Coder Terraform Runner

The Coder template runs Terraform. The Terraform execution environment must have:

- `vcluster` CLI
- `kubectl`
- `kustomize` (or `kubectl kustomize`)

Options:

- Use a Coder-provided image with these tools, or
- Build a custom image and configure Coder to use it for template execution

## 5. Push the Template

```bash
cd cloudburst-coder-template
coder templates create cloudburst-vcluster --directory .
```

## 6. Expose vcluster via Tailscale

For VMs to join the vcluster, the vcluster API must be reachable. With Tailscale operator:

1. Annotate the vcluster service for Tailscale exposure
2. Users set `vcluster_api_url` to the Tailscale hostname when creating a workspace
3. NodeClass `join.hostApiServer` uses this URL

## 7. Create NodeClass and NodePool (per workspace)

After the workspace is provisioned, users create NodeClass and NodePool in their vcluster:

```bash
vcluster connect <vcluster-name> -n <namespace> -- kubectl apply -f nodeclass.yaml
vcluster connect <vcluster-name> -n <namespace> -- kubectl apply -f nodepool.yaml
```

Example NodeClass (GCP) with Tailscale:

```yaml
apiVersion: cloudburst.io/v1alpha1
kind: NodeClass
metadata:
  name: gcp-nodeclass
  namespace: default
spec:
  gcp:
    project: "your-project"
    zone: "us-central1-a"
  join:
    hostApiServer: "https://your-vcluster.tailnet.ts.net:443"  # From vcluster_api_url param
    kindClusterName: "cloudburst"
    tokenTtlMinutes: 60
  tailscale:
    authKeySecretRef:
      name: tailscale-auth
      key: authkey
  bootstrap:
    kubernetesVersion: "1.34.3"
```
