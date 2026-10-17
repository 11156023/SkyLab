---
layout: home
title: SkyLab
titleTemplate: A self-hosted cloud for schools

hero:
  name: SkyLab
  text: A self-hosted cloud for schools
  tagline: Students request machines, teachers build whole classrooms, administrators keep Proxmox VE under control.
  image:
    src: /favicon.svg
    alt: SkyLab
  actions:
    - theme: brand
      text: Get started
      link: /getting-started
    - theme: alt
      text: Concepts
      link: /concepts
    - theme: alt
      text: GitHub
      link: https://github.com/ntubclass/SkyLab

features:
  - icon: 🖥️
    title: Virtual machines and containers
    details: VM and LXC lifecycle on Proxmox VE with VNC and terminal consoles in the browser, snapshots, backups, resizing and scheduled start and stop.
  - icon: 🎓
    title: Teaching at scale
    details: Platform templates, classes, rosters and timetables. Provision a whole class in one batch or publish multi-machine teaching environments.
  - icon: ✅
    title: Request workflow
    details: Students submit a request, reviewers approve it, SkyLab checks capacity for the time window and provisions the machine on schedule.
  - icon: 🌐
    title: Network control
    details: Firewall topology, NAT and domain rules, IP management, and a gateway VM reached through WireGuard from the SkyLab Connect desktop client.
  - icon: 🛡️
    title: Governance
    details: TTL reclamation, idle detection, quotas, resource alerts and mining detection keep a shared cluster usable for everyone.
  - icon: 🤖
    title: AI built in
    details: An OpenAI-compatible AI API through LiteLLM and self-hosted vLLM models, plus assistants for administrators and teachers.
---

## Where to start

- **Running SkyLab for the first time:** [Getting started](/getting-started) walks from `docker compose up` to the setup wizard and the first login.
- **Understanding the moving parts:** [Concepts and architecture](/concepts) explains the components, the roles and the vocabulary used across the UI and the guides.
- **Putting it on a server:** [Deployment](/guides/deployment) covers secrets, the platform entry through the gateway, HTTPS, LDAP and CI deployment.
- **Something is wrong:** [Troubleshooting](/troubleshooting) lists the failures people actually hit, with their causes and fixes.
- **Calling the API:** [REST API](/reference/api), [WebSocket endpoints](/reference/websocket) and the [AI API user manual](/guides/ai-api-user-manual).

SkyLab is free software released under the [GNU AGPL-3.0](https://github.com/ntubclass/SkyLab/blob/main/LICENSE). The guides on this site are built from the [`docs/`](https://github.com/ntubclass/SkyLab/tree/main/docs) directory of the repository; the "Edit this page" link at the bottom of each guide opens the source file.
