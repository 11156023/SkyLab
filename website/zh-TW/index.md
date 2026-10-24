---
layout: home
title: SkyLab
titleTemplate: 校園自架雲端平台

hero:
  name: SkyLab
  text: 校園自架雲端平台
  tagline: 學生申請機器、老師一次建好整班環境、管理員牢牢掌握 Proxmox VE。
  image:
    src: /favicon.svg
    alt: SkyLab
  actions:
    - theme: brand
      text: 快速開始
      link: /zh-TW/getting-started
    - theme: alt
      text: 概念與架構
      link: /zh-TW/concepts
    - theme: alt
      text: GitHub
      link: https://github.com/SkyLab-tw/SkyLab

features:
  - icon: 🖥️
    title: 虛擬機與容器
    details: 在 Proxmox VE 上管理 VM 與 LXC 的完整生命週期：瀏覽器內的 VNC 與終端機主控台、快照、備份、規格調整與排程開關機。
  - icon: 🎓
    title: 規模化教學
    details: 平台範本、班級、名單與課表；一次批次建置整班機器，或發布多機教學環境。
  - icon: ✅
    title: 申請審核流程
    details: 學生送出申請、審核者核准，SkyLab 檢查該時段的容量後，在時段開始時自動建置。
  - icon: 🌐
    title: 網路控制
    details: 防火牆拓樸、NAT 與網域規則、IP 管理，以及可用 SkyLab Connect 桌面端透過 WireGuard 連入的閘道 VM。
  - icon: 🛡️
    title: 治理
    details: TTL 回收、閒置偵測、配額、資源告警與挖礦偵測，讓共用叢集維持人人可用。
  - icon: 🤖
    title: 內建 AI
    details: 經 LiteLLM 與自架 vLLM 模型提供的 OpenAI 相容 AI API，以及給管理員與老師的 AI 助手。
---

<div class="home-screenshot">
  <img src="/screenshots/home-admin.webp" alt="管理員首頁：節點、VM 與 LXC 數量、AI 維運助手，以及「現在就處理」與「排進今天」清單。" loading="lazy" />
</div>

## 從哪裡開始

- **第一次架 SkyLab：**[快速開始](/zh-TW/getting-started)從 `docker compose up` 一路走到初始化精靈與第一次登入。
- **想弄懂各個元件：**[概念與架構](/zh-TW/concepts)說明元件、角色，以及介面與指南中共用的詞彙。
- **要放上正式伺服器：**[部署指南](/zh-TW/guides/deployment)涵蓋機密值、經閘道的平台入口、HTTPS、LDAP 與 CI 部署。
- **出問題了：**[疑難排解](/zh-TW/troubleshooting)列出實際遇過的故障、原因與解法。
- **要呼叫 API：**[REST API](/zh-TW/reference/api)、[WebSocket 端點](/zh-TW/reference/websocket)與 [AI API 使用與部署手冊](/zh-TW/guides/ai-api-user-manual)。
- **找特定頁面：**[使用手冊](/zh-TW/manual/)依角色涵蓋每個功能，[文件索引](/zh-TW/guides/)依檔案列出所有頁面。

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://github.com/SkyLab-tw/SkyLab/blob/main/LICENSE)
[![Backend Tests](https://github.com/SkyLab-tw/SkyLab/actions/workflows/backend-tests.yml/badge.svg)](https://github.com/SkyLab-tw/SkyLab/actions/workflows/backend-tests.yml)
[![Frontend Tests](https://github.com/SkyLab-tw/SkyLab/actions/workflows/frontend-tests.yml/badge.svg)](https://github.com/SkyLab-tw/SkyLab/actions/workflows/frontend-tests.yml)

SkyLab 是以 GNU AGPL-3.0 釋出的自由軟體，見[授權](/zh-TW/project/license)。本站就是 repo 的文件，放在 [`website/`](https://github.com/SkyLab-tw/SkyLab/tree/main/website) 目錄；每頁底部的「在 GitHub 上編輯此頁」會開啟對應的原始檔。
