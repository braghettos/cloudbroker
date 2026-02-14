# CloudBroker: An API That Knows the Price of Every VM Across Seven Clouds

**The problem and the product: one place to ask "what's cheapest?"**

---

You need a 4 vCPU / 8 GB machine. AWS shows one price, Hetzner another, Scaleway another — different sites, currencies, terms. Doing that once is tedious; doing it automatically at scale is impossible without a single, normalized source. **CloudBroker** is that source: ingest from seven providers, one schema, one request with constraints, one ranked answer. No provisioning, no Kubernetes — just "here are my constraints; what do you recommend?"

---

## The price-comparison problem

Multi-cloud is real; comparison is manual. Each provider has a different API, different currencies, different update cycles. Cost control and vendor flexibility depend on knowing the options. Checking four provider consoles every time you need a VM doesn't scale. Scripts that scrape pricing pages break when the pages change. What teams need is one place that already has the numbers and can answer: "What's the cheapest VM that fits these specs, in this region, under this price?"

---

## One API to rule them all

CloudBroker aggregates **AWS**, **GCP**, **Azure**, **Hetzner**, **Scaleway**, **DigitalOcean**, and **OVH**. One database, one request/response. You send constraints — min vCPU, min RAM, architecture, region (e.g. EU only), max price per hour in EUR, allowed providers — and get back a ranked list of instance types and regions. It does *not* create or delete VMs. It only answers "what do you recommend?"

---

## Why separate this from "the thing that provisions"?

Pricing and scoring can evolve without touching a provisioner. The API is reusable: called by a Kubernetes controller (e.g. [Cloudburst Autoscaler](https://github.com/braghettos/cloudburst-autoscaler)), a script, or a dashboard. Consumers can be anything that needs "cheapest VM for these constraints." Keeping the recommendation service separate means one team can own pricing logic and many systems can consume it.

---

The next piece explains how CloudBroker gets its data: ingestion and the data model.
